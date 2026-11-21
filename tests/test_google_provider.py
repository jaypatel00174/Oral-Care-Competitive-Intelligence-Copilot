"""LLM_PROVIDER=google: Gemini's native API through langchain-google-genai.

The real ChatGoogleGenerativeAI class is used; only its final network call is replaced by a fake Google response,
so these tests prove the thought signature round-trip through OUR agent with no network and no quota.
"""
import pytest
from google.genai import types
from langchain_core.messages import AIMessage

from src.services import llm as llm_module
from src.services.agent import run_agent


@pytest.fixture()
def env(monkeypatch):
    """A clean model environment that never reads the real .env."""
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    for key in ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "GOOGLE_API_KEY", "LLM_BASE_URL", "LLM_MIN_INTERVAL",
                "LLM_TEMPERATURE"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def gemini(model="gemini-3-flash-preview"):
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=model, google_api_key="test-key-not-real", temperature=0, max_retries=0)


def response(*parts):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=list(parts)), finish_reason="STOP")])


class FakeGoogle:
    """Stands in for client.models.generate_content: returns scripted responses and records every request."""

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, **request):
        self.requests.append(request)
        return self.responses[len(self.requests) - 1]


def sent_signatures(request) -> list:
    """The thought signatures attached to function calls in one outgoing request."""
    return [part.thought_signature for content in request["contents"] for part in (content.parts or [])
            if part.function_call]


# ---- choosing the provider ---------------------------------------------------------------------------------------
def test_the_default_provider_is_the_openai_compatible_client(env):
    env.setenv("LLM_MODEL", "qwen2.5:7b")
    env.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    from langchain_openai import ChatOpenAI

    assert isinstance(llm_module.get_llm()._model, ChatOpenAI)


def test_llm_provider_google_builds_the_native_gemini_client_throttled_and_logged(env):
    from langchain_google_genai import ChatGoogleGenerativeAI

    env.setenv("LLM_PROVIDER", "google")
    env.setenv("LLM_MODEL", "gemini-flash-lite-latest")
    env.setenv("LLM_API_KEY", "test-key-not-real")
    env.setenv("LLM_MIN_INTERVAL", "4.5")
    model = llm_module.get_llm()
    assert isinstance(model, llm_module.Throttled) and isinstance(model._model, ChatGoogleGenerativeAI)
    assert model._interval == 4.5 and model._model.temperature == 0


def test_bad_provider_settings_fail_loudly_not_silently(env):
    env.setenv("LLM_MODEL", "gemini-flash-lite-latest")
    env.setenv("LLM_PROVIDER", "gooogle")
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        llm_module.get_llm()
    env.setenv("LLM_PROVIDER", "google")
    with pytest.raises(ValueError, match="LLM_API_KEY"):          # no key: say so instead of a confusing 401
        llm_module.get_llm()


def test_the_judge_uses_the_same_provider(env, monkeypatch):
    from langchain_google_genai import ChatGoogleGenerativeAI

    from src.evaluation import judge

    monkeypatch.undo()          # conftest replaces get_judge; this test needs the real one
    env.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    env.setenv("LLM_PROVIDER", "google")
    env.setenv("LLM_MODEL", "gemini-flash-lite-latest")
    env.setenv("JUDGE_MODEL", "gemini-other")
    env.setenv("LLM_API_KEY", "test-key-not-real")
    assert isinstance(judge.get_judge()._model, ChatGoogleGenerativeAI)


# ---- reading replies -----------------------------------------------------------------------------------------------
def test_reply_text_keeps_only_the_text_blocks_never_the_thinking():
    reply = AIMessage(content=[{"type": "thinking", "thinking": "private reasoning"},
                               {"type": "text", "text": "BrushPacer [PH-HX9903_15#019]"}])
    assert llm_module.reply_text(reply) == "BrushPacer [PH-HX9903_15#019]"
    assert llm_module.reply_text(AIMessage(content="  plain  ")) == "plain"


# ---- the thought signature round-trip through the agent -----------------------------------------------------------
def test_the_agent_sends_geminis_thought_signature_back_with_its_tool_call():
    chat = gemini()
    fake = FakeGoogle(
        response(types.Part(function_call=types.FunctionCall(
            name="compare_products", args={"product_ids": ["PH-HX9903_15"], "attributes": ["timer"]}),
            thought_signature=b"SIGNATURE-FROM-GEMINI")),
        response(types.Part(text="The 9300 has BrushPacer and SmartTimer [PH-HX9903_15#019].")))
    chat.client.models.generate_content = fake

    a = run_agent("What is the timer on the Sonicare 9300?", llm_module.Throttled(chat, 0))

    assert len(fake.requests) == 2
    assert sent_signatures(fake.requests[1]) == [b"SIGNATURE-FROM-GEMINI"]          # sent back unchanged
    assert a.used_llm and not a.fell_back and [c.id for c in a.citations] == ["PH-HX9903_15#019"]


def test_signatures_survive_several_tool_turns_and_a_revision():
    chat = gemini()
    fake = FakeGoogle(
        response(types.Part(function_call=types.FunctionCall(name="resolve_models", args={"text": "Sonicare 9300"}),
                            thought_signature=b"SIG-1")),
        response(types.Part(text="It has a timer.")),                                  # uncited -> revision
        response(types.Part(function_call=types.FunctionCall(
            name="compare_products", args={"product_ids": ["PH-HX9903_15"], "attributes": ["timer"]}),
            thought_signature=b"SIG-2")),
        response(types.Part(text="BrushPacer and SmartTimer [PH-HX9903_15#019].")))
    chat.client.models.generate_content = fake

    a = run_agent("What is the timer on the Sonicare 9300?", llm_module.Throttled(chat, 0))

    assert a.used_llm and not a.fell_back and len(fake.requests) == 4
    assert b"SIG-1" in sent_signatures(fake.requests[1])
    assert set(sent_signatures(fake.requests[3])) >= {b"SIG-2"}


def test_the_pipeline_works_with_the_native_client_too():
    from src.services.assistant import ask

    chat = gemini("gemini-flash-lite-latest")
    fake = FakeGoogle(response(types.Part(text="BrushPacer and SmartTimer [PH-HX9903_15#019].")))
    chat.client.models.generate_content = fake
    a = ask("What is the timer on the Sonicare 9300?", llm=llm_module.Throttled(chat, 0))
    assert a.used_llm and not a.fell_back and len(fake.requests) == 1
