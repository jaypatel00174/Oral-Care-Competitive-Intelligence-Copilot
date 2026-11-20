"""Checks that run BEFORE any retrieval or LLM call.

Two jobs:
  1. check_question()      decide whether a user question may be answered at all.
  2. looks_like_injection() detect instruction-like text. Used on the question AND on every
                            scraped snippet, because web text is untrusted data, never instructions.

These are simple pattern checks. They catch the common attacks and they are easy to read and
extend, but they are not a complete defence. The real protection is the design: snippets are
passed to the LLM as quoted data, the LLM is told to ignore instructions inside them, and the
output check (grounding.py) rejects any answer that cites or states something we did not retrieve.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.guardrails import refusals

MAX_QUESTION_LENGTH = 600


@dataclass(frozen=True)
class Verdict:
    """Result of check_question(). `message` is the text to show when the question is blocked."""

    allowed: bool
    reason: str | None = None
    message: str | None = None


# ---- prompt injection ----------------------------------------------------------------------
# Each pattern is one family of attack. Matching is case-insensitive.
_INJECTION_PATTERNS = [
    # "ignore / disregard / forget / override ... instructions / rules / guardrails"
    r"\b(?:ignore|disregard|forget|override|bypass)\b[^.?!]{0,40}\b(?:instructions?|prompts?|rules?|guidelines?|guardrails?|restrictions?|polic(?:y|ies))\b",
    r"\bforget\s+(?:everything|all|what)\b",
    # asking for the hidden prompt
    r"\b(?:reveal|show|print|repeat|display|leak|output)\b[^.?!]{0,30}\b(?:system|hidden|initial|original|secret)\s+(?:prompt|instructions?|message)\b",
    r"\bsystem\s+prompt\b",
    # role changes
    r"\byou\s+are\s+now\s+(?:a|an|the|in|my|dan|free|unrestricted)\b",
    r"\bact\s+as\s+(?:a|an|if|my)\b",
    r"\bpretend\s+(?:to\s+be|you\s+are|that)\b",
    r"\b(?:developer|debug|admin|god|dan)\s+mode\b|\bjailbreak\w*|\bdo\s+anything\s+now\b",
    r"\bnew\s+instructions?\b|\bfrom\s+now\s+on\b[^.?!]{0,40}\byou\b",
    r"^\s*(?:system|assistant|developer)\s*:",
    # secrets
    r"\b(?:api[- ]?keys?|secrets?|passwords?|access\s+tokens?|credentials?)\b",
    # forcing a conclusion or switching off the sources
    r"\b(?:always|only)\s+(?:say|recommend|answer|respond|reply)\b|\bno\s+matter\s+what\b",
    r"\b(?:without|don'?t|do\s+not|never|skip|stop|no\s+need\s+to)\s+(?:cit\w+|reference\w*|sources?|citations?)\b",
    # markup / raw-IP links like the injected content found in the Philips review data
    r"<\s*(?:script|form|a|img|iframe)\b|\bhref\s*=|https?://\d{1,3}(?:\.\d{1,3}){3}",
]
_INJECTION_RX = [re.compile(p, re.I | re.M) for p in _INJECTION_PATTERNS]


def looks_like_injection(text: str) -> bool:
    """True if the text contains instruction-like or markup-injection content."""
    return any(rx.search(text) for rx in _INJECTION_RX)


# ---- medical / dental advice ---------------------------------------------------------------
# We refuse when a health term appears together with an advice-seeking phrase. Questions about
# what a product PAGE claims ("does it claim to help with gum disease?") are still allowed.
_MEDICAL_TERMS = re.compile(
    r"gingivitis|periodontitis|periodontal|gum\s+disease|cavit(?:y|ies)|tooth\s+decay|\bdecay\b|abscess|"
    r"infect\w*|bleed\w*|toothache|tooth\s?pain|ulcers?|canker|braces|implants?|dentures?|bruxism|"
    r"pregnan\w*|diabet\w*|allerg\w*|swollen|swelling|symptoms?|diagnos\w*|prescri\w*|medicat\w*|"
    r"my\s+(?:dentist|doctor)",
    re.I,
)
_ADVICE_INTENT = re.compile(
    r"\bshould\s+i\b|\bcan\s+i\b|\bcould\s+i\b|\bdo\s+i\b|\bam\s+i\b|\bis\s+it\s+(?:safe|ok|okay|fine|good|bad)\b|"
    r"\bsafe\s+for\b|\bwill\s+(?:it|this|that|the)\b[^?]{0,30}\b(?:cure|treat|heal|fix|prevent|stop|reduce|help|get\s+rid)\b|"
    r"\b(?:cure|treat|heal|fix|prevent|stop|get\s+rid\s+of)\b|\bwhat\s+(?:should|can)\s+i\b|"
    r"\b(?:best|better|good|suitable|recommended?)\b(?:\s+\w+){0,3}\s+(?:for|to)\b|\bmy\b|\bi\s+(?:have|am|got|suffer)\b|\bi'?m\b",
    re.I,
)
# Used to decide when to add the "not medical advice" note to an answer.
_HEALTH_TOPIC = re.compile(
    r"gums?\b|plaque|enamel|sensitiv\w*|cavit\w*|healthier|clinical\w*|dentist\w*|children|child|kids?\b",
    re.I,
)


def touches_health_topic(question: str) -> bool:
    return bool(_HEALTH_TOPIC.search(question))


def check_question(question: str) -> Verdict:
    """Decide whether the question may go on to retrieval. Order: empty, length, injection, medical."""
    q = (question or "").strip()
    if not q:
        return Verdict(False, "empty", refusals.REFUSALS["empty"])
    if len(q) > MAX_QUESTION_LENGTH:
        return Verdict(False, "too_long", refusals.REFUSALS["too_long"])
    if looks_like_injection(q):
        return Verdict(False, "prompt_injection", refusals.REFUSALS["prompt_injection"])
    if _MEDICAL_TERMS.search(q) and _ADVICE_INTENT.search(q):
        return Verdict(False, "medical_advice", refusals.REFUSALS["medical_advice"])
    return Verdict(True)
