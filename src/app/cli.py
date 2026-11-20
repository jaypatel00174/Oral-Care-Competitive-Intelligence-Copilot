"""Ask the assistant from the command line.

    python -m src.app.cli "What is the timer on the Oral-B iO9?"
    python -m src.app.cli                       # interactive: type questions, empty line to quit
    python -m src.app.cli "..." --json          # the full Answer as JSON
    python -m src.app.cli "..." --offline       # ignore .env and use the code-only writer

Build the data first (see the README): build_dataset, then build_stores.
"""
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from src.common.schema import Answer  # noqa: E402
from src.services.assistant import ask  # noqa: E402


def render(a: Answer) -> str:
    """Answer text followed by its sources, as plain text."""
    out = [a.text]
    if a.citations:
        out.append("\nSources:")
        for c in a.citations:
            out.append(f"  [{c.id}] {c.product_name} - {c.section}\n      {c.source_url}")
    mode = "LLM" if a.used_llm else "offline writer"
    extra = " (LLM unavailable or its draft failed the checks; safe writer used)" if a.fell_back else ""
    out.append(f"\n[{a.status}{' / ' + a.reason if a.reason and a.status != 'answered' else ''} | {mode}{extra}]")
    return "\n".join(out)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # product text contains characters Windows consoles reject
    ap = argparse.ArgumentParser(description="Ask the toothbrush comparison assistant.")
    ap.add_argument("question", nargs="?", help="your question; omit for interactive mode")
    ap.add_argument("--json", action="store_true", help="print the full Answer as JSON")
    ap.add_argument("--offline", action="store_true", help="do not use an LLM even if one is configured")
    args = ap.parse_args()
    llm = None if args.offline else "auto"

    def run(q: str) -> None:
        a = ask(q, llm=llm)
        print(a.model_dump_json(indent=2) if args.json else render(a))

    if args.question:
        run(args.question)
        return 0
    print("Ask about Philips Sonicare vs Oral-B electric toothbrushes. Empty line to quit.")
    while (q := input("\n> ").strip()):
        run(q)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
