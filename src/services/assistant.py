"""The one function the rest of the project calls: ask(question) -> Answer.

The API, the command line, the evaluation suite and the MCP tools all go through here, so they
all get the same guardrails and the same citations.
"""
from __future__ import annotations

from src.common.schema import Answer
from src.services.graph import build_graph
from src.services.llm import get_llm


def ask(question: str, llm="auto") -> Answer:
    """Answer one question.

    llm="auto"  use the model configured in .env (LLM_MODEL...), or the offline writer if none
    llm=None    force the offline writer (no model call)
    llm=<model> a LangChain chat model, useful in tests
    """
    model = get_llm() if llm == "auto" else llm
    return build_graph(model).invoke({"question": question})["answer"]
