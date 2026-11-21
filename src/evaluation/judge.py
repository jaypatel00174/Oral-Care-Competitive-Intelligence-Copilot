"""LLM-as-judge: does the answer make any claim the cited evidence does not support?

The judge sees only the evidence (the cited snippets) and the answer. It returns
    {"faithful": true/false, "unsupported_claims": [...]}
which complements the rule-based checks: rules catch numbers and links, the judge catches invented features, swapped products and exaggerations in plain wording.

Note: A judge can be wrong, so it is calibrated before use on three fixed examples (one faithful answer, one with a wrong number, one with an invented feature). 
If it misjudges any of them, the report says the judge is unreliable and its score is not used as a quality gate.

JUDGE_MODEL (optional env var) picks a different model than the assistant's, which avoids a model
grading its own writing. It uses the same LLM_BASE_URL and LLM_API_KEY.
"""
from __future__ import annotations

import json
import os
import re

from src.common.schema import Answer

PROMPT = """You are a strict fact checker. Compare the ANSWER with the EVIDENCE.

Rules:
- A claim is supported only if the EVIDENCE says it. Ignore the [ID] citation markers.
- "not found in sources" is an honest statement, never an unsupported claim.
- Flag any product feature, number, price or comparison that the EVIDENCE does not support, including
  a value that belongs to a different product than the one named.
- Do not judge style or completeness.

EVIDENCE:
{evidence}

ANSWER:
{answer}

Reply with JSON only, no other text:
{{"faithful": true or false, "unsupported_claims": ["short description", ...]}}"""

# Calebration done as small LLM models tends to fails
CALIBRATION = [
    # (evidence, answer, expected faithful)
    ("[X#1] Oral-B iO Series 9: A built-in 2-minute timer with quadrant pacing. Price: $329.99.",
     "The Oral-B iO Series 9 has a built-in 2-minute timer with quadrant pacing [X#1] and costs $329.99 [X#1].", True),
    ("[X#1] Oral-B iO Series 9: A built-in 2-minute timer with quadrant pacing. Price: $329.99.",
     "The Oral-B iO Series 9 has a 3-minute timer and costs $199.99 [X#1].", False),
    ("[X#1] Sonicare 9300: Pressure sensor: ring lights up purple. Battery: 14 days.",
     "The Sonicare 9300 has a UV sanitizer and a wireless charging pad [X#1].", False),
]


def get_judge():
    """The judge model, or None when no LLM is configured."""
    from src.services.llm import Throttled, build_chat, get_llm

    model = os.getenv("JUDGE_MODEL", "").strip()
    base = get_llm()
    if base is None or not model:
        return base
    # Same provider as the writer (LLM_PROVIDER), throttled and logged like it.
    return Throttled(build_chat(model, max_retries=1), float(os.getenv("LLM_MIN_INTERVAL", "0") or 0))


def _parse(raw: str) -> dict:
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    m = re.search(r"\{.*\}", raw, flags=re.S)
    if not m:
        raise ValueError(f"judge did not return JSON: {raw[:120]!r}")
    data = json.loads(m.group(0))
    if not isinstance(data.get("faithful"), bool):
        raise ValueError("judge JSON has no boolean 'faithful'")
    claims = data.get("unsupported_claims") or []
    return {"faithful": data["faithful"], "unsupported_claims": [str(c) for c in claims][:5]}


def judge_text(llm, evidence: str, answer: str) -> dict:
    out = llm.invoke(PROMPT.format(evidence=evidence, answer=answer))
    from src.services.llm import reply_text

    return _parse(reply_text(out) if hasattr(out, "content") else str(out))


def judge_answer(llm, a: Answer) -> dict | None:
    """Judge one Answer against its own citations. None when there is nothing to judge."""
    if not a.citations:
        return None
    evidence = "\n".join(f"[{c.id}] {c.product_name} ({c.section}): {c.text}" for c in a.citations)
    return judge_text(llm, evidence, a.text_with_ids or a.text)      # the judge's evidence is keyed by snippet id


def calibrate(llm) -> dict:
    """Run the fixed examples. reliable=True only if the judge gets all of them right."""
    results = []
    for evidence, answer, expected in CALIBRATION:
        try:
            got = judge_text(llm, evidence, answer)["faithful"]
        except Exception as e:  # noqa: BLE001 - an unreachable or garbled judge simply is not reliable
            return {"reliable": False, "error": str(e)[:200], "results": results}
        results.append({"expected": expected, "got": got})
    return {"reliable": all(r["expected"] == r["got"] for r in results), "error": None, "results": results}
