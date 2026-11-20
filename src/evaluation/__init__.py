"""Evaluation (planned): automated quality checks for the assistant.
Run it after any change; it fails if a quality gate is missed.

    questions.yaml     15+ test questions: comparisons, unknowns, injection, medical advice
    checks.py          citations present and valid, unsupported claims, retrieval hit rate, refusals
    run_eval.py        runs every question, prints a report, writes outputs/eval_report.md
"""
