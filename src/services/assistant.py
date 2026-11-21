"""The functions the rest of the project calls to get an answer.

    ask(question)          one answer (an `Answer`)
    ask_events(question)   the same run as a stream of progress events, ending with the `Answer`

The API, the command line, the evaluation suite and the MCP tools all go through here, so they
all get the same guardrails and the same citations. Conversations (follow-up questions) are in
src/services/conversation.py and call `ask` after resolving what a follow-up refers to.
"""
from __future__ import annotations

from collections.abc import Iterator

from src.common.schema import Answer
from src.services.graph import build_graph
from src.services.llm import get_llm


def _model(llm):
    return get_llm() if llm == "auto" else llm


def _state(question: str, focus) -> dict:
    return {"question": question, **({"focus": focus} if focus else {})}


def ask(question: str, llm="auto", agent: bool = False, focus=None) -> Answer:
    """Answer one question.

    llm="auto"  use the model configured in .env (LLM_MODEL...), or the offline writer if none
    llm=None    force the offline writer (no model call)
    llm=<model> a LangChain chat model, useful in tests
    agent=True  let the model choose and call tools (see services/agent.py); falls back to the pipeline
    focus       products and attributes a follow-up question refers back to (see conversation.py)
    """
    model = _model(llm)
    if agent:
        from src.services.agent import run_agent

        return run_agent(question if not focus else f"{question} ({focus.note})", model)
    return build_graph(model).invoke(_state(question, focus))["answer"]


def ask_events(question: str, llm="auto", agent: bool = False, focus=None) -> Iterator[dict]:
    """Yield {"event": "stage"|"tool"|"answer", ...} while the question is processed.

    Stages are real: each one is emitted when that step has finished (input_guard, retrieve, write,
    output_guard). The "answer" event comes last and holds the final, verified `Answer`.
    """
    model = _model(llm)
    if agent:
        from src.services.agent import agent_events

        yield from agent_events(question if not focus else f"{question} ({focus.note})", model)
        return
    answer = None
    for update in build_graph(model).stream(_state(question, focus), stream_mode="updates"):
        for node, delta in update.items():
            yield {"event": "stage", "stage": node}
            if delta and "answer" in delta:
                answer = delta["answer"]
    if answer is not None:
        yield {"event": "answer", "answer": answer}
