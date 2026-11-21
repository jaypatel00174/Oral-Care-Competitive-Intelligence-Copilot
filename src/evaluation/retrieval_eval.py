"""Retrieval quality on its own, with no LLM and no answer writing.

For each query in retrieval_questions.json the vector search returns the top k snippets. We measure:
    product_recall@k   an expected product appears in the top k
    snippet_recall@k   a top-k snippet contains the expected key phrase (the evidence that answers it)
    mrr                1 / rank of the first snippet from an expected product (0 if none)

This answers "does the right evidence reach the writer?", separately from "did the writer use it well?".
"""
from __future__ import annotations

import json
from pathlib import Path

from src.storage import vector_store

QUESTIONS = Path(__file__).with_name("retrieval_questions.json")
K = 5
GATES = {"product_recall": 0.85, "snippet_recall": 0.75}


def run(path: Path = QUESTIONS, k: int = K, search=vector_store.search) -> dict:
    cases = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for c in cases:
        hits = search(c["query"], k=k)
        expected = set(c["expect_products"])
        ranks = [i for i, h in enumerate(hits, 1) if h.product_id in expected]
        phrase = c.get("snippet_contains", "").lower()
        rows.append({
            "id": c["id"], "query": c["query"],
            "product_hit": bool(ranks),
            "snippet_hit": (not phrase) or any(phrase in h.text.lower() for h in hits),
            "rank": ranks[0] if ranks else None,
            "top": [h.snippet_id for h in hits],
        })
    n = len(rows)
    metrics = {
        "product_recall": sum(r["product_hit"] for r in rows) / n,
        "snippet_recall": sum(r["snippet_hit"] for r in rows) / n,
        "mrr": sum(1 / r["rank"] for r in rows if r["rank"]) / n,
    }
    gates = {name: {"value": metrics[name], "minimum": floor, "passed": metrics[name] >= floor}
             for name, floor in GATES.items()}
    return {"k": k, "total": n, "metrics": metrics, "gates": gates,
            "all_gates_passed": all(g["passed"] for g in gates.values()), "cases": rows}
