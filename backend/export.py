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


def text(value):
    return escape(str(value))


def percent(value):
    return f"{value * 100:.12g}%"


def estimate_table(estimates, kind, title, description):
    rows = []
    for item in estimates:
        group = text(item["group"])
        formula = text(item["formula"])
        rate = percent(item["value"])
        if kind == "standardized":
            cells = f"<td>{rate}</td><td><code>{formula}</code></td>"
        else:
            cells = (f"<td>{rate}</td><td>{item['numerator']:,}</td>"
                     f"<td>{item['denominator']:,}</td><td><code>{formula}</code></td>")
        stratum = f"<td>{text(item['stratum'])}</td>" if kind == "stratum" else ""
        rows.append(f'<tr><th scope="row">{group}</th>{stratum}{cells}</tr>')
    headers = ["Group"]
    if kind == "stratum":
        headers.append("Stratum")
    headers.append("Success rate")
    if kind != "standardized":
        headers.extend(["Positive outcomes", "Observations"])
    headers.append("Formula")
    headings = "".join(f'<th scope="col">{label}</th>' for label in headers)
    return (
        f'<section><h2 id="{kind}">{title}</h2><p>{description}</p>'
        f'<div class="table" role="region" aria-labelledby="{kind}" tabindex="0">'
        f'<table aria-labelledby="{kind}"><thead><tr>{headings}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div></section>'
    )


def render_report(dataset, plan, run, include_data):
    sections = []
    for kind, title, description in (
        ("observed", "Observed overall", "Each group keeps the stratum mix in its own sample."),
        ("standardized", "Standardized to the target mix",
         "Both groups use the same target weights. The rates are weighted averages with "
         "no single numerator or denominator."),
        ("stratum", "Within each stratum", "Compare groups within the same stratum."),
    ):
        estimates = [item for item in run["estimates"] if item["estimand"] == kind]
        if estimates:
            sections.append(estimate_table(estimates, kind, title, description))
    if run["error"]:
        error = run["error"]
        details = "".join(f"<dt>{text(key)}</dt><dd>{text(value)}</dd>"
                          for key, value in error["details"].items())
        sections.append(f'<section class="failure"><h2>Calculation error</h2>'
                        f'<p><code>{text(error["code"])}</code> · {text(error["message"])}</p>'
                        f'<dl>{details}</dl></section>')
    elif not sections:
        sections.append("<section><h2>Results pending</h2><p>The run has no estimates yet.</p></section>")
    weights = "".join(f"<dt>{text(name)}</dt><dd>{percent(weight)}</dd>"
                      for name, weight in plan["target_weights"].items())
    warnings = "".join(f"<li>{text(warning)}</li>" for warning in run["warnings"]
                       if warning != "Synthetic demonstration data")
    source = "Synthetic example" if dataset["synthetic"] else "Imported CSV"
    command = "python3 reproduce.py" if include_data else "python3 reproduce.py /path/to/original.csv"
    instruction = ("The CSV is included in this archive." if include_data
                   else "Supply the original CSV used for this run.")
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>ResearchLens experiment report</title>
<style>
  :root {{ font: 100%/1.55 system-ui, sans-serif; color: #152537; background: #edf2f7; }}
  * {{ box-sizing: border-box; }}
  body {{ max-width: 70rem; margin: auto; padding: clamp(1rem, 4vw, 3rem); }}
  h1, h2, p {{ margin-block: 0 .7rem; overflow-wrap: anywhere; }}
  h1 {{ font-size: clamp(1.8rem, 4vw, 2.7rem); line-height: 1.15; }}
  h2 {{ font-size: 1.2rem; }}
  header, section {{ background: #fff; border: 1px solid #c5d2df; border-radius: .7rem;
    margin-block-end: 1rem; padding: clamp(1rem, 3vw, 1.6rem); break-inside: avoid; }}
  header {{ border-inline-start: .3rem solid #146055; }}
  .label {{ color: #146055; font-weight: 700; }}
  .failure {{ border-inline-start: .3rem solid #943a2e; }}
  dl {{ margin: 0; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); gap: .3rem 1rem; }}
  dt {{ font-weight: 600; }}
  dd {{ margin: 0; overflow-wrap: anywhere; }}
  .table {{ overflow: auto; }}
  table {{ width: 100%; border-collapse: collapse; text-align: left; font-variant-numeric: tabular-nums; }}
  th, td {{ vertical-align: top; padding: .55rem .7rem; border-block-end: 1px solid #d2dce5; }}
  thead {{ background: #e5eef5; }}
  code {{ font-size: .9em; overflow-wrap: anywhere; }}
  td code {{ display: inline-block; min-width: 6rem; }}
  pre {{ background: #e5eef5; padding: .8rem; white-space: pre-wrap; }}
  :focus-visible {{ outline: 2px solid #146055; outline-offset: 2px; }}
  @media print {{ :root {{ background: white; }} body {{ padding: 0; }} }}
</style>
<header>
  <p class="label">ResearchLens · {source}</p>
  <h1>Experiment report</h1>
  <p>{text(plan["claim"])}</p>
  <p>Status <strong>{text(run["status"])}</strong> · {dataset["row_count"]:,} dataset observations</p>
</header>
<main>
  <section>
    <h2>Confirmed analysis plan</h2>
    <dl>
      <dt>Groups</dt><dd>{text(plan["group_column"])} · {text(" / ".join(plan["groups"]))}</dd>
      <dt>Positive outcome</dt><dd>{text(plan["outcome_column"])} = {text(plan["positive_value"])}</dd>
      <dt>Stratification</dt><dd>{text(plan["stratum_column"])}</dd>
      <dt>Run ID</dt><dd><code>{text(run["id"])}</code></dd>
    </dl>
    <h3>Entered target weights</h3><dl>{weights}</dl>
  </section>
  {"".join(sections)}
  <section>
    <h2>Interpretation</h2>
    <p>Observed and standardized rates describe different population mixes.
      These comparisons are descriptive and do not establish causality.</p>
    {f"<ul>{warnings}</ul>" if warnings else ""}
  </section>
  <section>
    <h2>Reproduce the calculation</h2>
    <p>Extract the ZIP and open a terminal in the extracted directory. {instruction} Use Python 3.11 or later.</p>
    <pre><code>{command}</code></pre>
    <p>The script checks the archive files against their recorded SHA-256 hashes and checks the
      recalculated results against <code>results.json</code>.</p>
  </section>
</main>
</html>
"""


def export_run(dataset, plan, run, include_data):
    files = {
        "plan.json": json.dumps(plan, ensure_ascii=False, indent=2),
        "results.json": json.dumps(run, ensure_ascii=False, indent=2),
        "requirements.txt": "# Python 3.11+. No third-party packages required.\n",
        "reproduce.py": (ROOT / "reproduce.py").read_bytes(),
        "calculation.py": (ROOT / "calculation.py").read_bytes(),
        "report.html": render_report(dataset["public"], plan, run, include_data),
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
