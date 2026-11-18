"""CSV parsing and binary-outcome estimates shared by API and export."""

import csv
import io
from collections import Counter
from decimal import Decimal
from fractions import Fraction
from urllib.parse import quote

MAX_BYTES = 10_000_000
MAX_ROWS = 50_000
MAX_COLUMNS = 200
MAX_CELLS = 1_000_000
MAX_STRATA = 100
WEIGHT_WARNING = "Observed and standardized estimates answer different population questions"


class AnalysisError(ValueError):
    def __init__(self, message, code="INVALID_PLAN", status=422, **details):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details

    def payload(self):
        return {"code": self.code, "message": str(self), "details": self.details}


def parse_csv(data):
    try:
        reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        names = next(reader, [])
        if not names or any(not name.strip() for name in names) or len(set(names)) != len(names):
            raise AnalysisError("CSV needs unique, non-empty column names.", "INVALID_CSV", 400)
        if len(names) > MAX_COLUMNS:
            raise AnalysisError("CSV exceeds 200 columns.", "LIMIT_EXCEEDED", 413)
        rows = []
        for row in reader:
            if len(row) != len(names):
                raise AnalysisError("CSV row has the wrong number of fields.", "INVALID_CSV", 400,
                                    line=reader.line_num)
            if len(rows) == MAX_ROWS:
                raise AnalysisError("CSV exceeds 50,000 rows.", "LIMIT_EXCEEDED", 413)
            if (len(rows) + 1) * len(names) > MAX_CELLS:
                raise AnalysisError("CSV exceeds 1,000,000 cells.", "LIMIT_EXCEEDED", 413)
            rows.append(row)
    except (UnicodeError, csv.Error) as exc:
        raise AnalysisError("Use a valid UTF-8 comma-separated CSV.", "INVALID_CSV", 400) from exc
    if not rows:
        raise AnalysisError("CSV has no observations.", "UNSUPPORTED_SCHEMA")

    columns = []
    for index, name in enumerate(names):
        values = [row[index] for row in rows if row[index].strip()]
        integer = all(
            len(value) <= 16 and value.lstrip("-").isascii() and value.lstrip("-").isdigit()
            and value.count("-") <= 1 and str(int(value)) == value
            and abs(int(value)) <= 2**53 - 1
            for value in values
        )
        missing = len(rows) - len(values)
        kind = "integer" if values and integer else "string"
        columns.append({"name": name, "type": kind, "missing_count": missing})
    records = [
        {col["name"]: (None if not value.strip() else
                       int(value) if col["type"] == "integer" else value)
         for col, value in zip(columns, row)}
        for row in rows
    ]
    return columns, records


def validate_plan(columns, plan):
    keys = [plan[name] for name in ("group_column", "stratum_column", "outcome_column")]
    selected = {col["name"]: col for col in columns}
    if len(set(keys)) != 3 or any(key not in selected for key in keys):
        raise AnalysisError("Select three distinct existing columns.")
    missing = {key: selected[key]["missing_count"] for key in keys if selected[key]["missing_count"]}
    if missing:
        raise AnalysisError("Selected columns contain missing values.", missing=missing)
    groups = plan["groups"]
    if len(groups) != 2 or len(set(groups)) != 2 or any(not group.strip() for group in groups):
        raise AnalysisError("Select two different groups.")
    weights = plan["target_weights"]
    if not weights or len(weights) > MAX_STRATA or any(
        not key.strip() or not 0 <= value <= 1 for key, value in weights.items()
    ):
        raise AnalysisError("Specify up to 100 strata with weights between 0 and 1.")
    if abs(sum(Decimal(str(weight)) for weight in weights.values()) - 1) > Decimal("1e-12"):
        raise AnalysisError("Target weights must sum to 1.")


def estimate(kind, group, stratum, numerator, denominator, value, formula):
    group_id = quote(group, safe="").replace("_", "%5F")
    stratum_id = quote(stratum, safe="").replace("_", "%5F") if stratum is not None else "all"
    return dict(id=f"{kind}_{group_id}_{stratum_id}", estimand=kind, group=group, stratum=stratum,
                numerator=numerator, denominator=denominator, value=float(value),
                unit="proportion", formula=formula)


def dataset_warnings(synthetic):
    return ["Synthetic demonstration data"] if synthetic else []


def calculate(columns, records, plan, synthetic=False):
    validate_plan(columns, plan)
    group_key, stratum_key, outcome_key = (
        plan[key] for key in ("group_column", "stratum_column", "outcome_column")
    )
    groups = plan["groups"]
    weights = plan["target_weights"]
    total, positive = Counter(), Counter()
    outcomes, observed_strata = set(), set()
    for row in records:
        group = str(row[group_key])
        if group not in groups:
            continue
        stratum = str(row[stratum_key])
        outcome = str(row[outcome_key])
        outcomes.add(outcome)
        observed_strata.add(stratum)
        for cell in [(group, None), (group, stratum)]:
            total[cell] += 1
            positive[cell] += outcome == str(plan["positive_value"])

    if len(outcomes) != 2 or str(plan["positive_value"]) not in outcomes:
        raise AnalysisError("Select a binary outcome and a positive value present in the selected groups.")
    unexpected = observed_strata.difference(weights)
    if unexpected:
        raise AnalysisError("Specify weights for every observed stratum.", strata=sorted(unexpected)[:20])
    estimates = []
    for stratum in [None, *sorted(weights)]:
        for group in groups:
            cell = (group, stratum)
            n, d = positive[cell], total[cell]
            if not d:
                raise AnalysisError("A target stratum has no observations.", group=group, stratum=stratum)
            kind = "observed" if stratum is None else "stratum"
            estimates.append(estimate(kind, group, stratum, n, d, Fraction(n, d), f"{n}/{d}"))

    normalized = {key: Fraction(str(value)) for key, value in weights.items()}
    weight_sum = sum(normalized.values())
    for group in groups:
        terms = []
        value = Fraction()
        for stratum, weight in sorted(normalized.items()):
            n, d = positive[group, stratum], total[group, stratum]
            value += weight / weight_sum * Fraction(n, d)
            terms.append(f"{weights[stratum]}*({n}/{d})" if weight_sum == 1
                         else f"({weight}/{weight_sum})*({n}/{d})")
        estimates.append(estimate("standardized", group, None, None, None, value, "+".join(terms)))

    warnings = dataset_warnings(synthetic)
    if any(Fraction(total[group, stratum], total[group, None]) != weight / weight_sum
           for group in groups for stratum, weight in normalized.items()):
        warnings.append(WEIGHT_WARNING)
    return estimates, warnings
