"""Run API integration tests over HTTP against a fresh loopback server."""

import http.client
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

from backend.calculation import MAX_BYTES, AnalysisError
from backend.explanation import explain

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "contracts" / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class Client:
    def __init__(self, port):
        self.port = port
        self.cookie = ""

    def request(self, method, path, data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Cookie": self.cookie, **(headers or {})}
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, "/api/v1" + path, data, headers)
            response = connection.getresponse()
            cookie = response.getheader("Set-Cookie")
            if cookie:
                assert "HttpOnly" in cookie
                self.cookie = cookie.split(";")[0]
            body = response.read()
            if response.getheader("Content-Type", "").startswith("application/json"):
                body = json.loads(body)
            return response.status, body
        finally:
            connection.close()

    def upload(self, contents):
        boundary = "researchlens-test-boundary"
        prefix = (f"--{boundary}\r\n"
                  'Content-Disposition: form-data; name="file"; filename="test.csv"\r\n'
                  "Content-Type: text/csv\r\n\r\n").encode()
        return self.request("POST", "/datasets", prefix + contents + f"\r\n--{boundary}--\r\n".encode(),
                            {"Content-Type": f"multipart/form-data; boundary={boundary}"})


class APITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        cls.port = listener.getsockname()[1]
        cls.server_log = tempfile.TemporaryFile()
        cls.addClassCleanup(cls.server_log.close)
        cls.server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:api", "--fd", str(listener.fileno()),
             "--log-level", "warning", "--no-access-log"],
            cwd=ROOT, pass_fds=(listener.fileno(),), stderr=cls.server_log,
            env={**os.environ, "RESEARCHLENS_MODEL_URL": "", "RESEARCHLENS_SECURE_COOKIE": "0"},
        )
        listener.close()
        cls.addClassCleanup(cls.stop_server)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and cls.server.poll() is None:
            try:
                if Client(cls.port).request("GET", "/health")[0] == 200:
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("Server failed to start.")

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.server.kill()
            cls.server.wait()
        cls.server_log.seek(0)
        if b"Exception in ASGI application" in cls.server_log.read():
            raise AssertionError("Unexpected ASGI application exception during HTTP checks.")

    def setUp(self):
        self.client = Client(self.port)

    def ok(self, result):
        status, body = result
        self.assertEqual(status, 200, body)
        return body

    def plan(self, dataset, prefix="", **overrides):
        body = fixture(prefix + "plan")
        body.pop("id")
        body.pop("confirmed")
        return self.ok(self.client.request("POST", "/plans", {
            **body, "dataset_id": dataset["id"], **overrides,
        }))

    def run_plan(self, plan, **headers):
        return self.ok(self.client.request("POST", "/runs", {"plan_id": plan["id"]}, headers))

    def demo(self):
        return self.ok(self.client.request("POST", "/datasets", {"example_id": "simpson-search"}))

    def test_fixtures_through_api(self):
        self.assertEqual(self.ok(self.client.request("GET", "/health")), {"status": "ok", "api_version": "1"})
        self.assertEqual(self.ok(self.client.request("GET", "/examples")), fixture("examples"))
        for name, prefix in [("simpson-search", ""), ("stable-search", "stable-")]:
            with self.subTest(example=name):
                dataset = self.ok(self.client.request("POST", "/datasets", {"example_id": name}))
                expected_dataset = fixture(prefix + "dataset")
                self.assertEqual(len(dataset["preview"]), 20)
                self.assertEqual({**dataset, "id": expected_dataset["id"],
                                  "preview": dataset["preview"][:1]}, expected_dataset)
                self.assertEqual(self.ok(self.client.request("GET", "/datasets/" + dataset["id"])), dataset)
                plan = self.plan(dataset, prefix)
                expected_plan = fixture(prefix + "plan")
                self.assertEqual({**plan, "id": expected_plan["id"],
                                  "dataset_id": expected_plan["dataset_id"]}, expected_plan)
                actual = self.run_plan(plan)
                expected = fixture(prefix + "run")
                self.assertEqual({**actual, "id": expected["id"], "plan_id": expected["plan_id"]}, expected)

    def test_upload_order_and_validation(self):
        original = (FIXTURES / "simpson-search.csv").read_bytes()
        header, *rows = original.splitlines()
        ordered = self.ok(self.client.upload(original))
        reversed_rows = self.ok(self.client.upload(b"\n".join([header, *rows[::-1]])))
        self.assertFalse(ordered["synthetic"])
        self.assertEqual(self.run_plan(self.plan(ordered))["estimates"],
                         self.run_plan(self.plan(reversed_rows))["estimates"])
        for data, status, code in [
            (b"x,x\n1,2\n", 400, "INVALID_CSV"),
            (b"x,y\n1\n", 400, "INVALID_CSV"),
            (b"x\n\xff\n", 400, "INVALID_CSV"),
            (b"x\n" + b"1\n" * 50_001, 413, "LIMIT_EXCEEDED"),
            (b"x" * (MAX_BYTES + 1), 413, "LIMIT_EXCEEDED"),
        ]:
            actual, body = self.client.upload(data)
            self.assertEqual((actual, body["error"]["code"]), (status, code))

    def test_missing_category_and_outcome(self):
        dataset = self.ok(self.client.upload(b"system,difficulty,success\nA,easy,1\nB,easy,0\nA,hard,0\n"))
        run = self.run_plan(self.plan(dataset))
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["estimates"], [])
        self.assertEqual(run["error"]["details"], {"group": "B", "stratum": "hard"})
        dataset = self.ok(self.client.upload(b"system,difficulty,success\nA,easy,\nB,easy,0\n"))
        self.assertEqual(dataset["columns"][-1]["missing_count"], 1)
        status, body = self.client.request("POST", "/plans", {
            **{key: value for key, value in fixture("plan").items() if key not in {"id", "confirmed"}},
            "dataset_id": dataset["id"],
        })
        self.assertEqual(status, 422)
        self.assertEqual(body["error"]["details"], {"missing": {"success": 1}})

    def test_retry_and_session_isolation(self):
        dataset = self.demo()
        plan = self.plan(dataset)
        clients = [Client(self.port), Client(self.port)]
        for client in clients:
            client.cookie = self.client.cookie
        with ThreadPoolExecutor(max_workers=2) as executor:
            requests = executor.map(lambda client: client.request(
                "POST", "/runs", {"plan_id": plan["id"]}, {"Idempotency-Key": "retry"}), clients)
            first, retry = map(self.ok, requests)
        self.assertEqual(first, retry)
        new = self.run_plan(plan)
        self.assertNotEqual(new["id"], first["id"])
        self.assertEqual(new["estimates"], first["estimates"])
        conflict = self.client.request("POST", "/runs", {"plan_id": self.plan(dataset)["id"]},
                                       {"Idempotency-Key": "retry"})
        self.assertEqual(conflict[0], 409)
        path = "/runs/" + first["id"]
        self.assertEqual(self.ok(self.client.request("GET", path)), first)
        stranger = Client(self.port)
        self.ok(stranger.request("POST", "/datasets", {"example_id": "stable-search"}))
        for method, resource in [
            ("GET", "/datasets/" + dataset["id"]), ("GET", "/plans/" + plan["id"]),
            ("GET", path), ("GET", path + "/export?include_data=true"), ("POST", path + "/explanation"),
        ]:
            self.assertEqual(stranger.request(method, resource)[0], 404)
        status, _ = self.client.request("POST", "/runs", {"plan_id": plan["id"]},
                                        {"Origin": "https://foreign.invalid"})
        self.assertEqual(status, 403)

    def test_origin_rejected_before_upload_body(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as client:
            client.sendall(
                b"POST /api/v1/datasets HTTP/1.1\r\nHost: localhost\r\n"
                b"Origin: https://foreign.invalid\r\nContent-Length: 1000000\r\n\r\n"
            )
            self.assertIn(b"403 Forbidden", client.recv(4096))

    def test_parallel_upload_limit_before_body(self):
        headers = (b"POST /api/v1/datasets HTTP/1.1\r\nHost: localhost\r\n"
                   b"Content-Length: 1000000\r\nExpect: 100-continue\r\n\r\n")
        with ExitStack() as stack:
            for _ in range(4):
                client = stack.enter_context(socket.create_connection(("127.0.0.1", self.port), timeout=2))
                client.sendall(headers)
                self.assertIn(b"100 Continue", client.recv(4096))
            extra = stack.enter_context(socket.create_connection(("127.0.0.1", self.port), timeout=2))
            extra.sendall(headers)
            self.assertIn(b"429 Too Many Requests", extra.recv(4096))
        deadline = time.monotonic() + 2
        while True:
            response = self.client.request("POST", "/datasets", {"example_id": "simpson-search"})
            if response[0] != 429 or time.monotonic() >= deadline:
                self.ok(response)
                break
            time.sleep(0.01)

    def test_export_in_clean_directory_and_unavailable_model(self):
        source = (b"\xef\xbb\xbf\xef\xbb\xbfsystem,difficulty,success,notes\nA,easy,1,=1+2\nB,easy,0,'literal\n"
                  b"A,hard,0,normal\nB,hard,1,  +1\n")
        dataset = self.ok(self.client.upload(source))
        plan = self.plan(dataset, group_column="\ufeffsystem", claim="<script>alert(1)</script>")
        run = self.run_plan(plan)
        path = "/runs/" + run["id"]
        status, error = self.client.request("POST", path + "/explanation")
        self.assertEqual(status, 503)
        self.assertEqual(error["error"], fixture("model-error")["error"])
        updated = self.ok(self.client.request("GET", path))
        self.assertEqual(updated["explanation_status"], "failed")
        self.assertEqual(updated["estimates"], run["estimates"])
        for include in (False, True):
            blob = self.ok(self.client.request("GET", path + f"/export?include_data={str(include).lower()}"))
            with ZipFile(io.BytesIO(blob)) as archive, tempfile.TemporaryDirectory() as tmp:
                self.assertNotIn(b"<script>", archive.read("report.html"))
                self.assertEqual("data.csv" in archive.namelist(), include)
                if include:
                    self.assertIn(b"'=1+2", archive.read("data.csv"))
                    self.assertIn(b"''literal", archive.read("data.csv"))
                archive.extractall(tmp)
                (Path(tmp) / "original.csv").write_bytes(source)
                command = [sys.executable, "-E", "-S", "reproduce.py"]
                if not include:
                    command.append("original.csv")
                completed = subprocess.run(command, cwd=tmp, capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(completed.stdout)["estimates"], run["estimates"])


class ExplanationTest(unittest.TestCase):
    @patch.dict(os.environ, {"RESEARCHLENS_MODEL_URL": "https://example.invalid/chat",
                            "RESEARCHLENS_MODEL": "test-model", "RESEARCHLENS_MODEL_KEY": "test-key"})
    @patch("backend.explanation.build_opener")
    def test_structured_explanation_and_invalid_references(self, opener):
        response = MagicMock()
        opener.return_value.open.return_value.__enter__.return_value = response
        result = fixture("explanation")
        result.pop("run_id")
        result["statements"][0]["estimate_ids"] = ["e3", "e4", "e5", "e6"]

        def model_message():
            return json.dumps({"choices": [{"message": {"content": json.dumps(result)}}]}).encode()

        response.read.return_value = model_message()
        run = fixture("run")
        self.assertEqual(explain(run), fixture("explanation"))
        sent = opener.return_value.open.call_args.args[0].data
        self.assertNotIn(b"observed_A_all", sent)
        result["statements"][0]["estimate_ids"] = ["invented"]
        response.read.return_value = model_message()
        with self.assertRaises(AnalysisError) as error:
            explain(run)
        self.assertEqual(error.exception.code, "MODEL_UNAVAILABLE")
        response.read.side_effect = http.client.IncompleteRead(b"partial")
        with self.assertRaises(AnalysisError) as error:
            explain(run)
        self.assertEqual(error.exception.code, "MODEL_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
