"""Services: the assistant's logic, built with LangChain + LangGraph.
Reads SQLite and Chroma through src.storage; never scrapes and knows nothing about HTTP.

    assistant.py      ask(question) -> Answer   (the one function everything else calls)
    graph.py          LangGraph workflow: input_guard -> retrieve -> write -> output_guard
    retrieval.py      understand the question; collect facts (SQLite) and snippets (Chroma)
    answer_writer.py  the offline writer (code only) and the LLM writer
    llm.py            the chat model, configured from .env (none = offline writer)

The guards come from src.guardrails.
"""
