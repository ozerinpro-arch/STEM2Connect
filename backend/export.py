"""Build a portable experiment archive with spreadsheet-safe CSV."""

import csv
import hashlib
import io
import json
from html import escape
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

VERSION = "1.0"
ROOT = Path(__file__).parent


def unsafe_cell(value):
    first_visible = next((char for char in value if not char.isspace() and char != "\ufeff"), "")
    return value.startswith(("'", "\t", "\r", "\n")) or first_visible in {
        "=", "+", "-", "@", "＝", "＋", "－", "＠",
    }


def export_run(dataset, plan, run, include_data):
    files = {
        "plan.json": json.dumps(plan, ensure_ascii=False, indent=2),
        "results.json": json.dumps(run, ensure_ascii=False, indent=2),
        "requirements.txt": "# Python 3.11+. No third-party packages required.\n",
        "reproduce.py": (ROOT / "reproduce.py").read_bytes(),
        "calculation.py": (ROOT / "calculation.py").read_bytes(),
        "report.html": (
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<title>ResearchLens experiment</title><h1>ResearchLens experiment</h1>'
            f'<p>{escape(plan["claim"])}</p><p>{escape(run["status"])}</p><pre>'
            + escape(json.dumps(run["estimates"] or run["error"], ensure_ascii=False, indent=2))
            + "</pre></html>"
        ),
    }
    if include_data:
        source = io.StringIO(dataset["raw"].decode("utf-8-sig"), newline="")
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerows(
            ["'" + cell if unsafe_cell(cell) else cell for cell in row]
            for row in csv.reader(source, strict=True)
        )
        files["data.csv"] = output.getvalue().encode("utf-8")

    files = {name: body.encode("utf-8") if isinstance(body, str) else body
             for name, body in files.items()}
    files["manifest.json"] = json.dumps({
        "api_version": "1",
        "analysis_version": VERSION,
        "python": ">=3.11",
        "dataset_hash": run["dataset_hash"],
        "synthetic": dataset["public"]["synthetic"],
        "data_included": include_data,
        "csv_encoding": "spreadsheet-safe-apostrophe-v1" if include_data else None,
        "sha256": {name: hashlib.sha256(body).hexdigest() for name, body in files.items()},
    }, indent=2).encode("utf-8")
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return output.getvalue()
