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
"""
from __future__ import annotations

import os
import threading
import time

_lock = threading.Lock()
_last_call = 0.0          # shared by every model object, so the evaluation judge and the writer share one budget


class Throttled:
    """Wraps a chat model so consecutive calls are at least `interval` seconds apart."""

    def __init__(self, model, interval: float):
        self._model, self._interval = model, interval

    def invoke(self, *args, **kwargs):
        global _last_call
        with _lock:
            wait = self._interval - (time.monotonic() - _last_call)
            if wait > 0:
                time.sleep(wait)
            _last_call = time.monotonic()
        return self._model.invoke(*args, **kwargs)

    def __getattr__(self, name):          # everything else (model_name, ...) is the wrapped model's
        return getattr(self._model, name)


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

    from langchain_openai import ChatOpenAI

    chat = ChatOpenAI(
        model=model,
        base_url=os.getenv("LLM_BASE_URL") or None,
        api_key=os.getenv("LLM_API_KEY") or "not-needed",   # local servers ignore it, but the client wants a value
        temperature=0,                                      # same question, same answer
        timeout=90,
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "3") or 3),
    )
    interval = float(os.getenv("LLM_MIN_INTERVAL", "0") or 0)
    return Throttled(chat, interval) if interval > 0 else chat
