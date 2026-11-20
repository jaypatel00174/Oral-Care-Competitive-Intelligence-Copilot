"""Guardrails: the rules that keep answers safe and grounded.

    input_checks.py   BEFORE retrieval: refuse prompt injection, medical/dental advice, empty or
                      oversized questions; also flags instruction-like text inside scraped snippets
    grounding.py      AFTER writing: every citation real, every number in the evidence, no foreign links
    refusals.py       the fixed messages (and the phrase "not found in sources")

Called by the LangGraph workflow in src.services.graph.
"""
