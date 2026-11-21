"""The evaluation must not spend a hosted free tier's quota without being asked."""
import io

import pytest

from src.evaluation import run_eval

HOSTED = {"LLM_MODEL": "gemini-flash-lite-latest", "LLM_BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai",
          "LLM_MIN_INTERVAL": "4.5"}


class Tty(io.StringIO):
    def isatty(self):
        return True


def env(monkeypatch, **values):
    for k in ("LLM_MODEL", "LLM_BASE_URL", "LLM_MIN_INTERVAL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in values.items():
        monkeypatch.setenv(k, v)


def test_a_hosted_model_is_not_called_without_confirmation_when_there_is_no_terminal(monkeypatch, capsys):
    env(monkeypatch, **HOSTED)
    assert run_eval.confirm_hosted_calls(100, stream=io.StringIO(), load_env=False) is False
    out = capsys.readouterr().out
    assert "up to ~100 model calls" in out and "generativelanguage.googleapis.com" in out and "--yes" in out


def test_yes_allows_it(monkeypatch):
    env(monkeypatch, **HOSTED)
    assert run_eval.confirm_hosted_calls(100, assume_yes=True, load_env=False) is True


@pytest.mark.parametrize("answer, expected", [("y", True), ("yes", True), ("", False), ("n", False), ("maybe", False)])
def test_in_a_terminal_only_an_explicit_yes_continues(monkeypatch, answer, expected):
    env(monkeypatch, **HOSTED)
    monkeypatch.setattr("builtins.input", lambda _prompt="": answer)
    assert run_eval.confirm_hosted_calls(100, stream=Tty(), load_env=False) is expected


def test_no_question_when_nothing_is_spent(monkeypatch):
    env(monkeypatch, LLM_MODEL="qwen2.5:7b", LLM_BASE_URL="http://localhost:11434/v1")      # local: free
    assert run_eval.confirm_hosted_calls(500, stream=io.StringIO(), load_env=False) is True
    env(monkeypatch)                                                                         # no model configured
    assert run_eval.confirm_hosted_calls(500, stream=io.StringIO(), load_env=False) is True
    env(monkeypatch, **HOSTED)
    assert run_eval.confirm_hosted_calls(0, stream=io.StringIO(), load_env=False) is True    # a run with no model calls


def test_the_estimate_is_an_upper_bound_and_agent_mode_costs_more():
    llm_only = run_eval.estimate_model_calls(["llm"], 41, 1, judge_on=False)
    both = run_eval.estimate_model_calls(["llm", "fallback"], 41, 1, judge_on=True)
    agent = run_eval.estimate_model_calls(["agent"], 41, 1, judge_on=False)
    offline = run_eval.estimate_model_calls(["fallback"], 41, 1, judge_on=False)
    assert llm_only == 41 and offline == 0 and agent == 41 * run_eval.AGENT_CALLS_PER_QUESTION
    assert both == 3 + 41 + 41 + 41                       # calibration + writer + judge for each mode
    assert run_eval.estimate_model_calls(["llm"], 41, 3, judge_on=False) == 123      # repeats multiply the LLM mode


def test_the_default_run_asks_before_calling_a_hosted_model(monkeypatch, tmp_path):
    env(monkeypatch, **HOSTED)
    monkeypatch.setattr("sys.argv", ["run_eval", "--out", str(tmp_path)])
    monkeypatch.setattr(run_eval, "confirm_hosted_calls", lambda *_a, **_k: False)
    called = []
    monkeypatch.setattr(run_eval, "run", lambda *a, **k: called.append(1))
    assert run_eval.main() == 2 and not called                # refused: nothing ran, nothing was written
    assert not list(tmp_path.iterdir())
