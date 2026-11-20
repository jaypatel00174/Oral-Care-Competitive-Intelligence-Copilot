"""STEP 3 - Services (planned): the AI logic, built with LangChain + LangGraph.
Reads the files written by the pipeline; never scrapes and knows nothing about HTTP.

    retrieval.py   find relevant products and snippets for a question
    llm.py         LangChain chat model, configured from environment variables
    graph.py       LangGraph workflow: input guard -> retrieve -> answer -> output guard
                   (the guards come from src.guardrails)
"""
