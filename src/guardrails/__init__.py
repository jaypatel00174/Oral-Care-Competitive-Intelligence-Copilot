"""Guardrails (planned): rules that keep answers safe and grounded.
Called by the LangGraph workflow in src.services, before and after the LLM.

    input_checks.py    detect prompt injection; block medical / dental advice requests
    grounding.py       every claim must cite a real snippet id, else say "not found in sources"
    refusals.py        the fixed refusal messages
"""
