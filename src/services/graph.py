"""The assistant's workflow, built with LangGraph.

    question
       |
       v
   input_guard   refuse prompt injection, medical advice, empty or oversized questions
       |
       v
   retrieve      facts from SQLite + snippets from Chroma (or: out of scope)
       |
       v
     write       an LLM drafts the answer if one is configured, otherwise the offline writer does
       |
       v
   output_guard  every citation real? every number in the evidence? If the LLM draft fails,
       |         replace it with the offline writer's answer (always grounded)
       v
     answer

Each box is a small function below. A node that produces the final `answer` ends the run early,
so a refused question never reaches retrieval or the LLM.
"""
from __future__ import annotations

import logging
from typing import TypedDict
from langgraph.graph import END, START, StateGraph

from src.common.schema import Answer
from src.guardrails import grounding, refusals
from src.guardrails.input_checks import check_question, touches_health_topic
from src.common.log import event, get_logger, preview
from src.services import answer_writer, retrieval
from src.services.citations import present
from src.services.llm import describe_error

log = get_logger("pipeline")


class State(TypedDict, total=False):
    """The data passed from one step to the next."""

    question: str
    focus: retrieval.Focus
    context: retrieval.Context
    draft: str
    used_llm: bool
    fell_back: bool
    llm_error: str
    answer: Answer


def _finish(ctx: retrieval.Context, text: str, used_llm: bool, fell_back: bool, repaired: bool = False) -> Answer:
    """Build the final Answer: resolve citation ids to sources and set the status."""
    citations = [ctx.snippets[i] for i in grounding.cited_ids(text) if i in ctx.snippets]
    status = "answered" if citations else "not_found"
    if status == "answered" and touches_health_topic(ctx.question):
        text += "\n\n" + refusals.HEALTH_NOTE
    answer = Answer(question=ctx.question, status=status, text=text, citations=citations,
                    used_llm=used_llm, fell_back=fell_back, repaired=repaired, retrieved_ids=list(ctx.snippets))
    return present(answer)      # verified with snippet ids above; now shown as numbered sources [1], [2] ...


def build_graph(llm=None):
    """Compile the workflow. `llm` is a LangChain chat model, or None for the offline writer."""

    def input_guard(state: State) -> dict:
        verdict = check_question(state["question"])
        if verdict.allowed:
            event(log, logging.DEBUG, "input_guard_ok", chars=len(state["question"]), question=preview(state["question"]))
            return {}
        event(log, logging.INFO, "input_refused", reason=verdict.reason, chars=len(state["question"]),
              question=preview(state["question"]))
        return {"answer": Answer(question=state["question"], status="refused",
                                 text=verdict.message, reason=verdict.reason)}

    def retrieve(state: State) -> dict:
        ctx = retrieval.retrieve(state["question"], focus=state.get("focus"))
        event(log, logging.DEBUG, "retrieved", facts=len(ctx.facts), missing=sum(f.value is None for f in ctx.facts),
              snippets=len(ctx.snippets), page_hits=len(ctx.search_ids), unknown_models=ctx.unknown_models,
              out_of_scope=ctx.out_of_scope, follow_up=bool(state.get("focus")))
        if ctx.out_of_scope:
            event(log, logging.INFO, "out_of_scope", chars=len(state["question"]))
            return {"context": ctx, "answer": Answer(question=state["question"], status="out_of_scope",
                                                     text=refusals.OUT_OF_SCOPE, reason="out_of_scope")}
        return {"context": ctx}

    def write(state: State) -> dict:
        ctx = state["context"]
        # With nothing to write about, the offline writer already says "not found in sources".
        # Calling a model would only invite it to invent something, so skip it.
        if llm is not None and ctx.has_evidence:
            try:
                return {"draft": answer_writer.write_with_llm(ctx, llm), "used_llm": True, "fell_back": False}
            except Exception as e:  # model offline, rate limited, bad key ... never break the answer
                event(log, logging.WARNING, "safe_writer_used", why="model call failed", error=describe_error(e))
                return {"draft": answer_writer.write_offline(ctx), "used_llm": False, "fell_back": True,
                        "llm_error": describe_error(e)}
        return {"draft": answer_writer.write_offline(ctx), "used_llm": False, "fell_back": False}

    def output_guard(state: State) -> dict:
        ctx, draft = state["context"], state["draft"]
        used_llm, fell_back = state["used_llm"], state["fell_back"]

        # How many "not found in sources" statements are justified: one per missing fact or unknown
        # model, plus any the notes themselves contain. With no facts (a text search) we cannot tell.
        missing = (sum(1 for f in ctx.facts if f.value is None) + len(ctx.unknown_models)
                   + sum(n.lower().count(refusals.NOT_FOUND) for n in ctx.notes))
        # Every product AND every fact that has evidence must be cited at least once, so an answer cannot
        # drop a side of a comparison or quietly skip a value (the price, say) that the question asked for.
        needed: dict[str, set[str]] = {}
        facts_with_value = [f for f in ctx.facts if f.value is not None and f.snippet_ids]
        for f in ctx.facts:
            if f.value is not None:
                needed.setdefault(f.product_name, set()).update(f.snippet_ids)
        required = list(needed.items()) + [(f"{f.product_name} / {f.label}", set(f.snippet_ids)) for f in facts_with_value]
        sku_names = list(ctx.must_name)

        def check(text: str) -> grounding.GroundingReport:
            return grounding.verify(text, set(ctx.snippets), answer_writer.allowed_text(ctx),
                                    max_not_found=missing if ctx.facts else None,
                                    required_any=required, required_text=sku_names,
                                    min_not_found=1 if missing else None)

        repaired = False
        if used_llm:                                         # caveat notes are always shown, in the writer's words
            absent = [n for n in ctx.notes if n.lower() not in draft.lower()]
            if absent:
                draft, repaired = answer_writer.add_notes(draft, absent), True
        report = check(draft)
        if not report.ok:
            event(log, logging.WARNING if used_llm else logging.ERROR, "draft_rejected", from_llm=used_llm,
                  problems=" | ".join(report.problems)[:400])
        if not report.ok and used_llm:
            # Only omissions ("does not mention ...", "does not name ...") can be repaired: the facts left out
            # are appended from the evidence and the draft is re-checked. Any false statement (invented number,
            # unretrieved citation, foreign link) still sends the whole answer to the safe writer.
            if all(p.startswith("does not") for p in report.problems):
                cited = set(grounding.cited_ids(draft))
                left_out = [f for f in facts_with_value if not set(f.snippet_ids) & cited]
                unnamed = [ctx.must_name[s] for s in sku_names if s.lower() not in draft.lower()]
                gaps = [] if refusals.NOT_FOUND in draft.lower() else answer_writer.gap_lines(ctx)
                fixed = answer_writer.complete_draft(draft, left_out, unnamed, gaps)
                if check(fixed).ok:
                    draft, report, repaired = fixed, check(fixed), True
                    event(log, logging.INFO, "draft_repaired", facts_added=len(left_out), names_added=len(unnamed),
                          gaps_added=len(gaps))
            if not report.ok:                                # bad LLM draft -> safe writer
                event(log, logging.WARNING, "safe_writer_used", why="draft failed the checks")
                draft, used_llm, fell_back = answer_writer.write_offline(ctx), False, True
                report = check(draft)
        if not report.ok:                                    # should never happen; say so honestly
            event(log, logging.ERROR, "grounding_failed", problems=" | ".join(report.problems)[:400])
            return {"answer": Answer(question=ctx.question, status="not_found", text=refusals.NOT_FOUND.capitalize() + ".",
                                     reason="grounding_failed: " + "; ".join(report.problems),
                                     retrieved_ids=list(ctx.snippets))}
        answer = _finish(ctx, draft, used_llm, fell_back, repaired)
        answer.llm_error = state.get("llm_error")
        event(log, logging.INFO, "answer", status=answer.status, citations=len(answer.citations), used_llm=used_llm,
              fell_back=fell_back, repaired=repaired, chars=len(answer.text))
        return {"answer": answer}

    graph = StateGraph(State)
    graph.add_node("input_guard", input_guard)
    graph.add_node("retrieve", retrieve)
    graph.add_node("write", write)
    graph.add_node("output_guard", output_guard)

    graph.add_edge(START, "input_guard")
    # A node that already produced `answer` (a refusal) ends the run; otherwise carry on.
    graph.add_conditional_edges("input_guard", lambda s: END if "answer" in s else "retrieve")
    graph.add_conditional_edges("retrieve", lambda s: END if "answer" in s else "write")
    graph.add_edge("write", "output_guard")
    graph.add_edge("output_guard", END)
    return graph.compile()
