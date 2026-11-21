"""Pick the chat model from environment variables (or a .env file). No keys live in the code.

    LLM_MODEL         model name, e.g. "qwen2.5:7b" or "gemini-flash-lite-latest". Empty or missing
                      means NO LLM: the assistant then answers with the offline writer (still cited,
                      still grounded).
    LLM_BASE_URL      an OpenAI-compatible endpoint. Local Ollama is http://localhost:11434/v1; Google
                      Gemini is https://generativelanguage.googleapis.com/v1beta/openai
    LLM_API_KEY       the key for a hosted endpoint. Ollama needs none.
    LLM_MIN_INTERVAL  optional, seconds to wait between model calls. Free hosted tiers allow only a few
                      requests per minute (HTTP 429 above that), so set about 4.5 for Gemini's free tier.
    LLM_MAX_RETRIES   optional, retries after a transient error such as 429 (default 3, with backoff).

One client (LangChain's ChatOpenAI) covers all of them because they speak the same protocol,
so switching models is a change to .env, not to the code.

Every model the app uses is wrapped in `Throttled`, which spaces calls out and LOGS each one (model, seconds,
success or the provider's error). See src/common/log.py.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from src.common.log import event, get_logger

log = get_logger("llm")
_lock = threading.Lock()
_last_call = 0.0          # shared by every model object, so the evaluation judge and the writer share one budget


def describe_error(e: Exception, limit: int = 500) -> str:
    """A model error with the provider's explanation, not just "Error code: 400".

    HTTP errors from the OpenAI client carry the reason in `.body` (or the response text); str(e) alone often
    drops it. The API key is removed from the text before it is returned.
    """
    detail = str(e)
    body = getattr(e, "body", None)
    if body is None:
        response = getattr(e, "response", None)
        body = getattr(response, "text", None) if response is not None else None
    if body:
        text = body if isinstance(body, str) else json.dumps(body, default=str, ensure_ascii=False)
        if text not in detail:
            detail = f"{detail} | {text}"
    out = f"{type(e).__name__}: {detail}"
    key = os.getenv("LLM_API_KEY", "")
    if len(key) > 8:
        out = out.replace(key, "<key>")
    return out[:limit]


class Throttled:
    """Wraps a chat model: spaces calls at least `interval` seconds apart (0 = no wait) and logs every call.

    The log line has the model name, the seconds the call took, how long it waited for the throttle, and how many
    tool calls the reply asked for. Failures are logged with the provider's explanation. Message text is never logged.
    """

    def __init__(self, model, interval: float = 0.0):
        self._model, self._interval = model, interval

    @property
    def _name(self) -> str:
        return str(getattr(self._model, "model_name", None) or getattr(self._model, "model", None) or "unknown")

    def invoke(self, *args, **kwargs):
        global _last_call
        waited = 0.0
        with _lock:
            wait = self._interval - (time.monotonic() - _last_call)
            if wait > 0:
                time.sleep(wait)
                waited = wait
            _last_call = time.monotonic()
        started = time.perf_counter()
        try:
            result = self._model.invoke(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - logged here, then re-raised for the caller's fallback
            event(log, logging.WARNING, "model_call_failed", model=self._name,
                  seconds=round(time.perf_counter() - started, 2), waited=round(waited, 2), error=describe_error(e))
            raise
        event(log, logging.INFO, "model_call", model=self._name, seconds=round(time.perf_counter() - started, 2),
              waited=round(waited, 2), tool_calls=len(getattr(result, "tool_calls", None) or []))
        return result

    def bind_tools(self, *args, **kwargs):
        """A model with tools attached must stay throttled and logged too (agent mode)."""
        return Throttled(self._model.bind_tools(*args, **kwargs), self._interval)

    def __getattr__(self, name):          # everything else (model_name, ...) is the wrapped model's
        return getattr(self._model, name)


PROVIDERS = ("openai", "google")


def reply_text(message) -> str:
    """The text of a model reply. Gemini can reply with a list of content blocks (text, thinking, signatures);
    str() of that list would leak the private thinking into the answer, so only the text blocks are kept."""
    text = getattr(message, "text", None)
    if callable(text) and not isinstance(text, str):   # older langchain-core: text() is a method; newer: a str property
        text = text()
    return (text if isinstance(text, str) else str(getattr(message, "content", message))).strip()


def provider() -> str:
    """LLM_PROVIDER: "openai" (default; any OpenAI-compatible endpoint, including Ollama) or "google"
    (Gemini's native API, which keeps the thought signatures that Gemini 3 needs for tool calls)."""
    value = (os.getenv("LLM_PROVIDER") or "openai").strip().lower()
    if value not in PROVIDERS:
        raise ValueError(f"LLM_PROVIDER must be one of {PROVIDERS}, not {value!r}")
    return value


def build_chat(model: str, max_retries: int | None = None):
    """A bare LangChain chat model for `model` from the configured provider (not throttled, not logged)."""
    retries = int(os.getenv("LLM_MAX_RETRIES", "3") or 3) if max_retries is None else max_retries
    temperature = float(os.getenv("LLM_TEMPERATURE", "0") or 0)     # 0: same question, same answer
    if provider() == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        key = os.getenv("LLM_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
        if not key:
            raise ValueError("LLM_PROVIDER=google needs LLM_API_KEY (a Google AI Studio key)")
        return ChatGoogleGenerativeAI(model=model, google_api_key=key, temperature=temperature,
                                      max_retries=retries, timeout=90)
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        base_url=os.getenv("LLM_BASE_URL") or None,
        api_key=os.getenv("LLM_API_KEY") or "not-needed",   # local servers ignore it, but the client wants a value
        temperature=temperature,
        timeout=90,
        max_retries=retries,
    )


def get_llm():
    """Return a LangChain chat model, or None when no model is configured."""
    try:
        from dotenv import load_dotenv

        # override=True: the project's .env wins over variables left exported in the shell, which
        # otherwise silently point the app at a different model (a stale LLM_MODEL from an old session).
        load_dotenv(override=True)
    except ImportError:
        pass  # python-dotenv is optional; real environment variables still work

    model = os.getenv("LLM_MODEL", "").strip()
    if not model:
        return None
    chat = build_chat(model)
    interval = float(os.getenv("LLM_MIN_INTERVAL", "0") or 0)
    host = ("generativelanguage.googleapis.com (native)" if provider() == "google"
            else (os.getenv("LLM_BASE_URL") or "default").split("//")[-1].split("/")[0])
    event(log, logging.DEBUG, "model_configured", provider=provider(), model=model, min_interval=interval, host=host)
    return Throttled(chat, interval)       # always wrapped, so every model call is logged
