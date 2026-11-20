"""Pick the chat model from environment variables (or a .env file). No keys live in the code.

    LLM_MODEL      model name, e.g. "qwen2.5:7b". Empty or missing means NO LLM: the assistant
                   then answers with the offline writer (still cited, still grounded).
    LLM_BASE_URL   an OpenAI-compatible endpoint. Local Ollama is http://localhost:11434/v1;
                   hosted services with a free tier (Groq, OpenRouter, ...) each have their own.
    LLM_API_KEY    the key for a hosted endpoint. Ollama needs none.

One client (LangChain's ChatOpenAI) covers all of them because they speak the same protocol,
so switching models is a change to .env, not to the code.
"""
from __future__ import annotations

import os


def get_llm():
    """Return a LangChain chat model, or None when no model is configured."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass  # python-dotenv is optional; real environment variables still work

    model = os.getenv("LLM_MODEL", "").strip()
    if not model:
        return None

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        base_url=os.getenv("LLM_BASE_URL") or None,
        api_key=os.getenv("LLM_API_KEY") or "not-needed",   # local servers ignore it, but the client wants a value
        temperature=0,                                      # same question, same answer
        timeout=90,
        max_retries=1,
    )
