"""Automated evaluation of the assistant.

    python -m src.evaluation.run_eval                    # default: BOTH writer modes + retrieval + judge
    python -m src.evaluation.run_eval --mode llm         # LLM from .env, with safe-writer fallback
    python -m src.evaluation.run_eval --mode fallback    # safe writer only, no LLM (deterministic)
    python -m src.evaluation.run_eval --repeats 3        # repeat the LLM mode 3 times (it varies run to run)
    python -m src.evaluation.run_eval --no-judge --no-retrieval
    python -m src.evaluation.run_eval --questions my.json --out outputs

What is measured
  Per answer (questions.json)
    status        expected status (answered / not_found / refused / out_of_scope) and refusal reason
    citations     every citation resolves to a stored snippet with identical text
    grounding     the guard finds no unsupported numbers, links or unretrieved citations
    correctness   values match outputs/comparison_table.csv; blanks are reported as "not found"
                  (see truth.py). This is independent of the assistant's own checks.
    contains      required phrases, brands and products are present
    judge         an LLM fact-checker flags claims the cited evidence does not support (see judge.py)
  Retrieval (retrieval_questions.json): product and snippet recall@5 and MRR of the vector search alone.
  Repeats and speed: pass rate per repeat (min / mean / max), flaky cases, latency p50 / p95.

Two writer modes run by default: "llm" (the model in .env writes; the safe writer replaces drafts that
fail the checks) and "fallback" (safe writer only). If no LLM is reachable the llm section says so.
Quality gates set the exit code (0 = pass, 1 = a gate failed), so this can run in CI.
Writes outputs/eval_results.json and outputs/eval_report.md.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from src.common.schema import Answer  # noqa: E402
from src.evaluation import judge as judge_mod  # noqa: E402
from src.evaluation import retrieval_eval, truth  # noqa: E402
from src.guardrails import grounding  # noqa: E402
from src.services.assistant import ask  # noqa: E402
from src.storage import sql_store  # noqa: E402

QUESTIONS = Path(__file__).with_name("questions.json")

# Quality gates: name -> minimum value between 0 and 1.
GATES = {
    "pass_rate": 0.90,                # share of questions where every check passed
    "grounding_rate": 1.00,           # answered questions with no unsupported claim
    "citation_validity": 1.00,        # citations that match a stored snippet exactly
    "correctness_rate": 0.95,         # values and "not found" statements that match the CSV
    "refusal_accuracy": 1.00,         # injection / medical / out-of-scope handled correctly
    "not_found_accuracy": 1.00,       # unknowns reported as not found, with no citations
    "retrieval_hit_rate": 0.90,       # expected products found among the citations
    "llm_call_success": 0.95,         # model calls that did not error; a throttled run must not pass as an LLM result
}
JUDGE_GATE = 0.85                      # only applied when the judge passed its calibration

# The two ways the assistant can write an answer.
MODES = {
    "llm": ("LLM with safe-writer fallback", "auto"),
    "fallback": ("Safe writer only (no LLM)", None),
    "agent": ("Agent: the LLM chooses tools, pipeline fallback", "agent"),
}
AGENT_CALLS_PER_QUESTION = 4      # typical: resolve_models, compare_products, then the final answer


def estimate_model_calls(keys: list[str], n_cases: int, repeats: int, judge_on: bool) -> int:
    """An UPPER bound on model calls for a run (refused and out-of-scope questions never reach the model)."""
    calls = 3 if judge_on else 0                      # the judge's calibration examples
    for k in keys:
        reps = repeats if k in ("llm", "agent") else 1
        writer = {"llm": n_cases * reps, "agent": n_cases * reps * AGENT_CALLS_PER_QUESTION}.get(k, 0)
        calls += writer + (n_cases * reps if judge_on else 0)
    return calls


def confirm_hosted_calls(calls: int, assume_yes: bool = False, stream=None, load_env: bool = True) -> bool:
    """Ask before a run that would call a hosted model: free tiers have a request limit, and a run can use it up.

    No question is asked when no model is configured, when the model is local (Ollama), or with --yes.
    Without a terminal (CI, a script) an unconfirmed run is refused instead of silently spending the quota.
    """
    from urllib.parse import urlparse

    if load_env:
        try:
            from dotenv import load_dotenv

            load_dotenv(override=True)
        except ImportError:
            pass
    if calls == 0 or not os.getenv("LLM_MODEL", "").strip():
        return True
    host = urlparse(os.getenv("LLM_BASE_URL", "") or "").hostname or "the model provider"
    if host in ("localhost", "127.0.0.1", "::1") or assume_yes:
        return True
    interval = float(os.getenv("LLM_MIN_INTERVAL", "0") or 0)
    minutes = f", about {calls * interval / 60:.0f} min at LLM_MIN_INTERVAL={interval:g}" if interval else ""
    print(f"This run may make up to ~{calls} model calls to {host}{minutes}.\n"
          "A hosted free tier has a request limit, and this can use it up.")
    if not (stream or sys.stdin).isatty():
        print("Not confirmed (no terminal). Re-run with --yes to allow it, or use "
              "`--mode fallback --no-judge` for a run that makes no model calls.")
        return False
    return input("Continue? [y/N] ").strip().lower() in ("y", "yes")


@dataclass
class CaseResult:
    id: str
    category: str
    question: str
    status: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    checks: dict[str, bool | None] = field(default_factory=dict)   # None = not applicable
    seconds: float = 0.0
    used_llm: bool = False
    fell_back: bool = False
    repaired: bool = False
    cited: list[str] = field(default_factory=list)
    judge: dict | None = None            # {"faithful": bool, "unsupported_claims": [...]} or {"error": ...}
    repeat: int = 0
    xfail: str | None = None             # known gap in the assistant: reported, but not counted in the gates
    llm_error: str | None = None         # the model call failed (rate limit, offline ...); the safe writer answered


def _evidence(a: Answer, db) -> tuple[str, set[str]]:
    """All text the assistant was allowed to rely on: retrieved snippets, product names and SKUs."""
    parts = [a.question]
    for sid in a.retrieved_ids:
        row = sql_store.get_snippet(db, sid)
        if row:
            parts.append(row["text"])
    for p in db.execute("SELECT name, sku FROM products").fetchall():
        parts += [p["name"], p["sku"] or ""]
    return "\n".join(parts), set(a.retrieved_ids)


def evaluate_case(case: dict, a: Answer, db, table: dict | None = None) -> CaseResult:
    checks: dict[str, bool | None] = {}
    failures: list[str] = []

    def record(name: str, ok: bool | None, msg: str = "") -> None:
        checks[name] = ok
        if ok is False:
            failures.append(f"{name}: {msg}" if msg else name)

    record("status", a.status == case["expect_status"], f"expected {case['expect_status']}, got {a.status}")
    if "expect_reason" in case:
        record("reason", a.reason == case["expect_reason"], f"expected {case['expect_reason']}, got {a.reason}")

    cited_products = {c.product_id for c in a.citations}
    if case["expect_status"] == "answered":
        record("has_citations", bool(a.citations), "answered without any citation")
        bad = []
        for c in a.citations:
            row = sql_store.get_snippet(db, c.id)
            if row is None or row["text"] != c.text or row["source_url"] != c.source_url:
                bad.append(c.id)
        record("citation_valid", not bad, "citations do not match stored snippets: " + ", ".join(bad))
        allowed_text, allowed_ids = _evidence(a, db)
        report = grounding.verify(a.text, allowed_ids, allowed_text)
        record("grounded", report.ok, "; ".join(report.problems))
    else:
        record("has_citations", None)
        record("citation_valid", None)
        record("grounded", None)

    if case.get("truth"):
        problems = truth.check(case, a.text, cited_products, table if table is not None else truth.load_table())
        record("correct", not problems, "; ".join(problems))

    if case.get("expect_no_citations"):
        record("no_citations", not a.citations, f"cited {[c.id for c in a.citations]}")
    if case.get("must_contain"):
        low = a.text.lower()
        missing = [p for p in case["must_contain"] if p.lower() not in low]
        record("contains", not missing, "missing phrases: " + ", ".join(missing))
    if case.get("forbid"):
        low = a.text.lower()
        present = [p for p in case["forbid"] if p.lower() in low]
        record("forbidden", not present, "forbidden phrases present: " + ", ".join(present))
    if case.get("expect_brands"):
        brands = {p.split("-")[0] for p in cited_products}
        missing = [b for b in case["expect_brands"] if b not in brands]
        record("brands", not missing, "no citation from brand(s): " + ", ".join(missing))
    if case.get("expect_products"):
        missing = [p for p in case["expect_products"] if p not in cited_products]
        record("retrieval", not missing, "expected products not cited: " + ", ".join(missing))

    return CaseResult(
        id=case["id"], category=case["category"], question=case["question"], status=a.status,
        passed=not failures, failures=failures, checks=checks, used_llm=a.used_llm,
        fell_back=a.fell_back, repaired=a.repaired, cited=[c.id for c in a.citations], xfail=case.get("known_gap"),
        llm_error=a.llm_error,
    )


def _rate(results: list[CaseResult], check: str, only: set[str] | None = None) -> float | None:
    vals = [r.checks.get(check) for r in results if (only is None or r.category in only)]
    vals = [v for v in vals if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def _percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def summarize(results: list[CaseResult], repeats: int = 1, judge_reliable: bool | None = None) -> dict:
    """Metrics over all results (pooled across repeats) and the gate verdicts.

    Cases marked "known_gap" are reported but left out of every metric and gate.
    """
    gaps = [r for r in results if r.xfail]
    results = [r for r in results if not r.xfail]
    refusal_cats = {"prompt_injection", "medical", "out_of_scope"}
    nf_cats = {"not_found", "unknown_product"}
    metrics = {
        "pass_rate": sum(r.passed for r in results) / len(results),
        "grounding_rate": _rate(results, "grounded"),
        "citation_validity": _rate(results, "citation_valid"),
        "correctness_rate": _rate(results, "correct"),
        "refusal_accuracy": _rate(results, "status", refusal_cats),
        "not_found_accuracy": _rate(results, "status", nf_cats),
        "retrieval_hit_rate": _rate(results, "retrieval"),
    }
    attempts = [r for r in results if r.used_llm or r.fell_back]      # answers where a model call was made
    errored = [r for r in attempts if r.llm_error]
    metrics["llm_call_success"] = (1 - len(errored) / len(attempts)) if attempts else None
    gates = {}
    for name, floor in GATES.items():
        v = metrics[name]
        gates[name] = {"value": v, "minimum": floor, "passed": v is None or v >= floor}

    judged = [r.judge for r in results if r.judge and "faithful" in r.judge]
    errors = sum(1 for r in results if r.judge and "error" in r.judge)
    faithfulness = (sum(j["faithful"] for j in judged) / len(judged)) if judged else None
    if faithfulness is not None:
        metrics["judge_faithfulness"] = faithfulness
        if judge_reliable:   # an unreliable judge is reported, but never fails the build
            gates["judge_faithfulness"] = {"value": faithfulness, "minimum": JUDGE_GATE,
                                           "passed": faithfulness >= JUDGE_GATE}

    per_repeat = []
    for i in range(repeats):
        rs = [r for r in results if r.repeat == i]
        per_repeat.append(sum(r.passed for r in rs) / len(rs))
    secs = [r.seconds for r in results]
    return {
        "total": len(results),
        "passed": sum(r.passed for r in results),
        "repeats": repeats,
        "pass_rate_per_repeat": per_repeat,
        "metrics": metrics,
        "gates": gates,
        "all_gates_passed": all(g["passed"] for g in gates.values()),
        "llm_used_in": sum(r.used_llm for r in results),
        "llm_fell_back_in": sum(r.fell_back for r in results),
        "llm_repaired_in": sum(r.repaired for r in results),
        "llm_errors": len(errored),
        "llm_error_sample": errored[0].llm_error if errored else None,
        "known_gaps": {"cases": sorted({r.id for r in gaps}),
                       "still_failing": sorted({r.id for r in gaps if not r.passed}),
                       "unexpectedly_passing": sorted({r.id for r in gaps if r.passed})},
        "judge": {"judged": len(judged), "errors": errors, "reliable": judge_reliable,
                  "error_sample": next((r.judge["error"] for r in results if r.judge and "error" in r.judge), None)},
        "avg_seconds": sum(secs) / len(secs),
        "p50_seconds": _percentile(secs, 50),
        "p95_seconds": _percentile(secs, 95),
    }


def llm_available(summary: dict) -> bool:
    """True if the LLM wrote at least one final answer (or tried and fell back)."""
    return summary["llm_used_in"] > 0 or summary["llm_fell_back_in"] > 0


def run(questions: Path, llm, repeats: int = 1, judge=None, judge_reliable: bool | None = None,
        table: dict | None = None) -> tuple[list[CaseResult], dict]:
    """Run every question `repeats` times with writer `llm` ("auto", None or a model).

    judge: a chat model used to fact-check answered questions (None = no judge).
    """
    cases = json.loads(questions.read_text(encoding="utf-8"))
    table = table if table is not None else truth.load_table()
    agent = llm == "agent"          # the model chooses tools ("auto" below supplies the configured model)
    results: list[CaseResult] = []
    with sql_store.connect() as db:
        for rep in range(repeats):
            for case in cases:
                t = time.perf_counter()
                a = ask(case["question"], llm="auto" if agent else llm, agent=agent)
                seconds = time.perf_counter() - t
                r = evaluate_case(case, a, db, table)
                r.seconds, r.repeat = seconds, rep
                if judge is not None and a.status == "answered":
                    try:
                        r.judge = judge_mod.judge_answer(judge, a)
                    except Exception as e:  # noqa: BLE001 - a judge failure must not break the run
                        r.judge = {"error": str(e)[:200]}
                results.append(r)
    return results, summarize(results, repeats, judge_reliable)


def _aggregate(results: list[CaseResult]) -> list[dict]:
    """One row per question across repeats: pass count, writer, unique failures."""
    by_id: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        by_id[r.id].append(r)
    rows = []
    for rs in by_id.values():
        n = len(rs)
        writers = Counter(("LLM + repair" if r.repaired else "LLM") if r.used_llm
                          else ("fallback" if r.fell_back else "safe writer") for r in rs)
        verdicts = [r.judge["faithful"] for r in rs if r.judge and "faithful" in r.judge]
        claims = sorted({c for r in rs if r.judge for c in r.judge.get("unsupported_claims", [])})
        rows.append({
            "id": rs[0].id, "category": rs[0].category, "status": Counter(r.status for r in rs).most_common(1)[0][0],
            "writer": ", ".join(f"{w} x{c}" if n > 1 else w for w, c in writers.most_common()),
            "passed": sum(r.passed for r in rs), "runs": n, "xfail": rs[0].xfail,
            "failures": sorted({f for r in rs for f in r.failures}),
            "judge": None if not verdicts else f"{sum(verdicts)}/{len(verdicts)} faithful",
            "judge_claims": claims,
        })
    return rows


def _warnings(summary: dict) -> list[str]:
    """Things that make a run less trustworthy than its pass rate suggests."""
    out = []
    if summary["llm_errors"]:
        out.append(f"{summary['llm_errors']} model call(s) failed ({summary['llm_error_sample']}). Those answers came from "
                   "the safe writer, so this run under-represents the LLM. If it is a rate limit (429), set "
                   "`LLM_MIN_INTERVAL` in `.env` (about 4.5 for a free Gemini key).")
    if summary["judge"]["errors"]:
        out.append(f"{summary['judge']['errors']} judge call(s) failed ({summary['judge']['error_sample']}), "
                   "so the judge score covers only part of the answers.")
    return out


def _passed_cell(row: dict) -> str:
    if not row["xfail"]:
        return f"{row['passed']}/{row['runs']}"
    return "known gap (now passes: remove the flag)" if row["passed"] == row["runs"] else "known gap"


def _pct(v):
    return "n/a" if v is None else f"{v:.0%}"


def write_report(runs: dict, retrieval: dict | None, judge_info: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "eval_results.json").write_text(json.dumps({
        "judge_calibration": judge_info,
        "retrieval": retrieval,
        "modes": {key: {"label": MODES[key][0], "summary": summary, "cases": [r.__dict__ for r in results]}
                  for key, (results, summary) in runs.items()},
    }, indent=2), encoding="utf-8")

    lines = ["# Evaluation report", ""]
    if retrieval:
        lines += [f"## Retrieval (vector search alone, top {retrieval['k']})", "",
                  f"Queries: **{retrieval['total']}**. Product recall: **{_pct(retrieval['metrics']['product_recall'])}**. "
                  f"Snippet recall: **{_pct(retrieval['metrics']['snippet_recall'])}**. "
                  f"MRR: **{retrieval['metrics']['mrr']:.2f}**.", "",
                  "| Gate | Value | Minimum | Result |", "|---|---|---|---|"]
        for name, g in retrieval["gates"].items():
            lines.append(f"| {name} | {_pct(g['value'])} | {_pct(g['minimum'])} | {'pass' if g['passed'] else 'FAIL'} |")
        misses = [c for c in retrieval["cases"] if not (c["product_hit"] and c["snippet_hit"])]
        if misses:
            lines += ["", "Misses:", ""] + [
                f"- {c['id']} {c['query']} (product hit: {c['product_hit']}, snippet hit: {c['snippet_hit']})" for c in misses]
        lines.append("")

    if judge_info.get("enabled"):
        state = "passed" if judge_info["reliable"] else "FAILED"
        who = ("a different model than the writer" if judge_info["independent"]
               else "the same model that writes the answers (set JUDGE_MODEL to use another)")
        gate = ("The judge score is a quality gate." if judge_info["gated"]
                else "The judge score is **advisory**: shown below, but it does not fail the run. "
                     "Small models are noisy judges and flag fragments of correct answers. "
                     "Use `JUDGE_MODEL` or `--judge-gate` to make it a gate.")
        lines += ["## LLM judge calibration", "",
                  f"Calibration on {len(judge_info['results'])} fixed examples: **{state}**. The judge is {who}. {gate}"]
        if judge_info.get("error"):
            lines.append(f"Error: {judge_info['error']}")
        lines.append("")
    else:
        lines += ["## LLM judge", "", f"Not run: {judge_info.get('why', 'disabled')}.", ""]

    for key, (results, summary) in runs.items():
        lines += [f"## {MODES[key][0]}", ""]
        if key == "llm" and not llm_available(summary):
            lines += ["No LLM is configured or reachable (set `LLM_MODEL`, `LLM_BASE_URL` in `.env`). "
                      "Every answer came from the safe writer, so this run is not an LLM result.", ""]
        reps = summary["repeats"]
        per = summary["pass_rate_per_repeat"]
        lines += [
            f"Questions: **{len(results) // reps}**, repeats: **{reps}**. Fully passed: **{summary['passed']}/{summary['total']}**.",
            f"Latency per question: mean {summary['avg_seconds']:.2f}s, p50 {summary['p50_seconds']:.2f}s, "
            f"p95 {summary['p95_seconds']:.2f}s.",
            f"LLM wrote the final answer: {summary['llm_used_in']}. LLM draft rejected, safe writer used: {summary['llm_fell_back_in']}. "
            f"LLM draft completed with missing facts: {summary['llm_repaired_in']}.",
        ]
        gaps = summary["known_gaps"]
        if gaps["cases"]:
            lines.append(f"Known gaps (not counted in the gates): {', '.join(gaps['cases'])}. "
                         f"Still failing: {', '.join(gaps['still_failing']) or 'none'}.")
        if reps > 1:
            lines.append(f"Pass rate per repeat: min {min(per):.0%}, mean {sum(per) / reps:.0%}, max {max(per):.0%}.")
        if summary["judge"]["judged"] or summary["judge"]["errors"]:
            lines.append(f"Judge: {summary['judge']['judged']} answers judged, {summary['judge']['errors']} judge errors.")
        for w in _warnings(summary):
            lines.append(f"**WARNING:** {w}")
        lines += ["", f"Overall: **{'PASS' if summary['all_gates_passed'] else 'FAIL'}**", "",
                  "### Quality gates", "", "| Gate | Value | Minimum | Result |", "|---|---|---|---|"]
        for name, g in summary["gates"].items():
            lines.append(f"| {name} | {_pct(g['value'])} | {_pct(g['minimum'])} | {'pass' if g['passed'] else 'FAIL'} |")
        if "judge_faithfulness" in summary["metrics"] and "judge_faithfulness" not in summary["gates"]:
            lines.append(f"| judge_faithfulness (not gated) | {_pct(summary['metrics']['judge_faithfulness'])} | - | - |")
        lines += ["", "### Cases", "", "| ID | Category | Status | Writer | Passed | Judge | Failures |", "|---|---|---|---|---|---|---|"]
        for row in _aggregate(results):
            fails = "; ".join(row["failures"] + [f"judge: {c}" for c in row["judge_claims"]])
            lines.append(f"| {row['id']} | {row['category']} | {row['status']} | {row['writer']} | "
                         f"{_passed_cell(row)} | {row['judge'] or '-'} | {fails.replace('|', '/') or '-'} |")
        lines.append("")
    (out_dir / "eval_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Evaluate the comparison assistant.")
    ap.add_argument("--questions", type=Path, default=QUESTIONS)
    ap.add_argument("--out", type=Path, default=Path("outputs"))
    ap.add_argument("--mode", choices=["both", "llm", "fallback", "agent"], default="both",
                    help="both (default): the LLM mode and the fallback-only mode. agent: the model chooses tools "
                         "(several model calls per question; never part of 'both')")
    ap.add_argument("--repeats", type=int, default=1,
                    help="repeat the LLM mode this many times to measure variation (fallback is deterministic, runs once)")
    ap.add_argument("--yes", action="store_true",
                    help="do not ask before making calls to a hosted model (a hosted free tier has a request limit)")
    ap.add_argument("--no-judge", action="store_true", help="skip the LLM-as-judge faithfulness check")
    ap.add_argument("--judge-gate", action="store_true",
                    help="make the judge score a quality gate even when the judge is the same model as the writer "
                         "(default: only gated when JUDGE_MODEL names a different model and calibration passed)")
    ap.add_argument("--no-retrieval", action="store_true", help="skip the retrieval recall evaluation")
    args = ap.parse_args()
    from src.common.log import setup_logging

    setup_logging("WARNING")        # model failures and fallbacks still show; set LOG_LEVEL=INFO to see every call

    # Ask BEFORE anything that could call a hosted model (including the judge's calibration).
    keys = ["llm", "fallback"] if args.mode == "both" else [args.mode]
    n_cases = len(json.loads(args.questions.read_text(encoding="utf-8")))
    calls = estimate_model_calls(keys, n_cases, max(1, args.repeats), judge_on=not args.no_judge)
    if not confirm_hosted_calls(calls, assume_yes=args.yes):
        return 2

    retrieval = None if args.no_retrieval else retrieval_eval.run()

    judge, judge_info = None, {"enabled": False, "why": "disabled with --no-judge"}
    if not args.no_judge:
        judge = judge_mod.get_judge()
        if judge is None:
            judge_info = {"enabled": False, "why": "no LLM configured (set LLM_MODEL in .env)"}
        else:
            cal = judge_mod.calibrate(judge)
            if cal["error"]:
                judge, judge_info = None, {"enabled": False, "why": f"judge model unreachable ({cal['error']})"}
            else:
                independent = bool(os.getenv("JUDGE_MODEL", "").strip())
                # A model grading its own writing is advisory: reported, never failing the build, unless forced.
                gated = cal["reliable"] and (independent or args.judge_gate)
                judge_info = {"enabled": True, "independent": independent, "gated": gated, **cal}

    runs = {}
    for k in keys:
        reps = max(1, args.repeats) if k in ("llm", "agent") else 1
        runs[k] = run(args.questions, MODES[k][1], repeats=reps, judge=judge,
                      judge_reliable=judge_info.get("gated"))
    write_report(runs, retrieval, judge_info, args.out)

    exit_code = 0
    if retrieval:
        m = retrieval["metrics"]
        print(f"=== Retrieval (top {retrieval['k']}) ===\n  product recall {m['product_recall']:.0%}, "
              f"snippet recall {m['snippet_recall']:.0%}, MRR {m['mrr']:.2f}")
        if not retrieval["all_gates_passed"]:
            exit_code = 1
            print("  retrieval gates FAILED")
    if judge_info.get("enabled"):
        print(f"=== Judge === calibration {'passed' if judge_info['reliable'] else 'FAILED'}; "
              f"{'gated' if judge_info['gated'] else 'advisory (not gated)'}")
    for key, (results, summary) in runs.items():
        print(f"\n=== {MODES[key][0]} ===")
        if key == "llm" and not llm_available(summary):
            print("NOTE: no LLM configured or reachable; this run used the safe writer only.")
        for row in _aggregate(results):
            ok = row["passed"] == row["runs"]
            label = ("XPASS" if ok else "XFAIL") if row["xfail"] else ("PASS" if ok else "FAIL")
            print(f"{label:5} {row['id']:4} {row['category']:18} {row['passed']}/{row['runs']}")
            for f in ([f"known gap: {row['xfail']}"] if row["xfail"] else row["failures"]):
                print(f"        - {f}")
        print(f"\n{summary['passed']}/{summary['total']} passed "
              f"(LLM wrote {summary['llm_used_in']}, repaired {summary['llm_repaired_in']}, "
              f"fell back {summary['llm_fell_back_in']}); "
              f"latency p50 {summary['p50_seconds']:.1f}s p95 {summary['p95_seconds']:.1f}s")
        for name, g in summary["gates"].items():
            print(f"  gate {name:20} {_pct(g['value']):>5} (min {g['minimum']:.0%}) {'ok' if g['passed'] else 'FAILED'}")
        for w in _warnings(summary):
            print(f"  WARNING: {w}")
        if not summary["all_gates_passed"]:
            exit_code = 1
    print(f"\nReport: {args.out / 'eval_report.md'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
