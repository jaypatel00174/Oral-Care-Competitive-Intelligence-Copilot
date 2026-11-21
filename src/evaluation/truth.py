"""Ground-truth checks: is the answer CORRECT, not just grounded?

The grounding guard only proves that numbers appear somewhere in the retrieved text. These checks
compare the answer with `outputs/comparison_table.csv`, which the guard and the writer never read,
so a wrong value, a value taken from the wrong product, or a false "not found" is caught here.

A case lists what to verify:

    "truth": [{"product_id": "PH-HX9903_15", "field": "price_usd"}]
    "strict_attribution": true      # also: no other product's different value may appear

What is expected depends on the CSV cell:
    blank cell                   -> the answer must say "not found in sources" (None means not found, never no)
    price_usd / mode_count /
    battery_life (short value)   -> the value must appear in the answer
    True (yes/no features)       -> the answer must not say "not found" and must cite that product
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from src.guardrails.refusals import NOT_FOUND

CSV_PATH = Path("outputs/comparison_table.csv")
VALUE_FIELDS = {"price_usd", "mode_count", "battery_life"}


def load_table(path: Path = CSV_PATH) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return {row["product_id"]: row for row in csv.DictReader(f)}


_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}


def _stated(field: str, raw: str, low: str) -> bool:
    """Does the (lower-cased) answer state this value? Accepts the usual ways of writing it."""
    if field == "price_usd":
        v = float(raw)
        return f"{v:.2f}" in low or f"{v:,.2f}" in low
    if field == "mode_count":
        n = int(float(raw))
        forms = "|".join([str(n)] + ([_NUMBER_WORDS[n]] if n in _NUMBER_WORDS else []))
        # "5 modes", "5 brushing modes", "five smart cleaning modes"
        return bool(re.search(rf"\b(?:{forms})\s+(?:[\w-]+\s+){{0,3}}modes?\b", low))
    if re.search(rf"(?<![\w.]){re.escape(raw.lower())}(?!\w)", low):      # not inside a longer number: "114 days"
        return True
    m = re.match(r"^(?:up to\s+)?(\d+)\s*(hour|day|week|month)s?\b", raw.lower())
    if m:   # "14 days" is also written "14-day" or "14 day"
        return bool(re.search(rf"\b{m.group(1)}[\s-]*{m.group(2)}s?\b", low))
    return False


def check(case: dict, text: str, cited_products: set[str], table: dict[str, dict[str, str]]) -> list[str]:
    """Return a list of problems (empty = every expected fact is right)."""
    problems: list[str] = []
    low = text.lower()
    specs = case.get("truth", [])
    for spec in specs:
        pid, field = spec["product_id"], spec["field"]
        row = table.get(pid)
        if row is None or field not in row:
            problems.append(f"truth spec points at unknown {pid}.{field}")
            continue
        raw = (row[field] or "").strip()
        if raw == "":
            if NOT_FOUND not in low:
                problems.append(f"{pid}.{field} is blank in the table, so the answer should say '{NOT_FOUND}'")
        elif field in VALUE_FIELDS:
            if not _stated(field, raw, low):
                problems.append(f"{pid}.{field} should be {raw!r} but the answer does not state it")
            if case.get("strict_attribution"):
                for other_id, other in table.items():
                    ov = (other.get(field) or "").strip()
                    if other_id == pid or not ov or ov == raw:
                        continue
                    if _stated(field, ov, low):
                        problems.append(f"answer contains {other_id}.{field}={ov!r}, which is a different product's value")
                        break
        elif raw == "True" and len(specs) == 1:
            if NOT_FOUND in low:
                problems.append(f"{pid}.{field} is True in the table but the answer says '{NOT_FOUND}'")
            if pid not in cited_products:
                problems.append(f"answer does not cite {pid}")
    return problems
