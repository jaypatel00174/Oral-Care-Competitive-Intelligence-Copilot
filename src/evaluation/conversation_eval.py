"""Scripted multi-turn conversations: does the assistant keep track of what a follow-up refers to?

    python -m src.evaluation.conversation_eval

Offline and deterministic: every turn is answered by the code-only writer, so it makes NO model calls and uses
no API quota. (The memory logic is plain rules in src/services/conversation.py, so this tests it fully.)

Each turn checks the expected status, required and forbidden text, and which products were cited.
Quality gate: every turn must pass, and follow-up turns (marked "followup") are reported separately.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from src.services.conversation import Conversation  # noqa: E402

CONVERSATIONS = Path(__file__).with_name("conversations.json")


def check_turn(turn: dict, answer) -> list[str]:
    problems = []
    if answer.status != turn["expect_status"]:
        problems.append(f"status {answer.status}, expected {turn['expect_status']}")
    low = answer.text.lower()
    problems += [f"missing {p!r}" for p in turn.get("must_contain", []) if p.lower() not in low]
    problems += [f"contains forbidden {p!r}" for p in turn.get("forbid", []) if p.lower() in low]
    cited = {c.product_id for c in answer.citations}
    problems += [f"did not cite {p}" for p in turn.get("expect_products", []) if p not in cited]
    if turn.get("followup") and not answer.resolved_question and turn["expect_status"] == "answered":
        problems.append("was not recognised as a follow-up")
    return problems


def run(path: Path = CONVERSATIONS) -> dict:
    results = []
    for conv_case in json.loads(path.read_text(encoding="utf-8")):
        conv = Conversation()
        for i, turn in enumerate(conv_case["turns"], 1):
            problems = check_turn(turn, conv.ask(turn["q"], llm=None))
            results.append({"conversation": conv_case["id"], "title": conv_case["title"], "turn": i, "question": turn["q"],
                            "followup": bool(turn.get("followup")), "passed": not problems, "problems": problems})
    follow = [r for r in results if r["followup"]]
    return {
        "turns": len(results), "passed": sum(r["passed"] for r in results),
        "followups": len(follow), "followups_passed": sum(r["passed"] for r in follow),
        "all_passed": all(r["passed"] for r in results), "results": results,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    out = run()
    for r in out["results"]:
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['conversation']} turn {r['turn']}: {r['question']}")
        for p in r["problems"]:
            print(f"        - {p}")
    print(f"\n{out['passed']}/{out['turns']} turns passed; follow-ups {out['followups_passed']}/{out['followups']}")
    return 0 if out["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
