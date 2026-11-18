"""Run inside the extracted archive, optionally with the original CSV path."""

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

from calculation import AnalysisError, calculate, dataset_warnings, parse_csv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path)
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["sha256"].items():
        if hashlib.sha256((base / name).read_bytes()).hexdigest() != digest:
            parser.error(f"SHA-256 mismatch for {name}")
    if args.source:
        data = args.source.read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest["dataset_hash"]:
            parser.error("The original CSV does not match the experiment.")
    elif manifest["data_included"]:
        with (base / "data.csv").open(encoding="utf-8", newline="") as source:
            output = io.StringIO(newline="")
            csv.writer(output).writerows(
                [cell[1:] if cell.startswith("'") else cell for cell in row]
                for row in csv.reader(source, strict=True)
            )
        data = output.getvalue().encode("utf-8-sig")
    else:
        parser.error("Supply the original CSV as an argument.")

    plan = json.loads((base / "plan.json").read_text(encoding="utf-8"))
    expected = json.loads((base / "results.json").read_text(encoding="utf-8"))
    columns, records = parse_csv(data)
    try:
        estimates, warnings = calculate(columns, records, plan, manifest["synthetic"])
        error = None
    except AnalysisError as exc:
        estimates, warnings, error = [], dataset_warnings(manifest["synthetic"]), exc.payload()
    actual = {"estimates": estimates, "warnings": warnings, "error": error}
    if any(expected[key] != value for key, value in actual.items()):
        parser.error("Recalculated results differ from results.json.")
    print(json.dumps(actual, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
