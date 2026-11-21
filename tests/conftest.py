"""Test-wide safety: no test may call a real language model.

The assistant's default (llm="auto") reads .env and would call the configured hosted model, which costs
free-tier quota. This fixture makes "auto" mean "no model" in every test. Tests that need a model pass an
explicit fake one, which is not affected.
"""
import pytest


@pytest.fixture(autouse=True)
def never_call_a_real_llm(monkeypatch):
    monkeypatch.setattr("src.services.assistant.get_llm", lambda: None)
    monkeypatch.setattr("src.evaluation.judge.get_judge", lambda: None, raising=False)


@pytest.fixture(autouse=True)
def fresh_agent_support():
    """Agent mode remembers models that cannot do it; no test may inherit another test's verdict."""
    from src.services.agent import reset_agent_support

    reset_agent_support()
    yield
    reset_agent_support()
