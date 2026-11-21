"""Ask the assistant from the command line.

    python -m src.app.cli "What is the timer on the Oral-B iO9?"
    python -m src.app.cli                       # interactive chat with memory: "and its price?" works
    python -m src.app.cli "..." --json          # the full Answer as JSON
    python -m src.app.cli "..." --offline       # ignore .env and use the code-only writer (no model call)
    python -m src.app.cli "..." --agent         # let the model choose tools (needs a tool-calling model)

In interactive mode type /new to start a fresh conversation and an empty line to quit.
Build the data first (see the README): build_dataset, then build_stores.
"""
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from src.common.schema import Answer  # noqa: E402
from src.common.log import new_request_id, setup_logging  # noqa: E402
from src.services.conversation import Conversation  # noqa: E402


def render(a: Answer) -> str:
    """Answer text followed by its sources, as plain text."""
    out = [a.text]
    if a.citations:
        out.append("\nSources:")
        for c in a.citations:
            out.append(f"  [{c.id}] {c.product_name} - {c.section}\n      {c.source_url}")
    if a.resolved_question:
        out.append(f"\n[understood as: {a.resolved_question}]")
    if a.tool_calls:
        out.append("[tools: " + "; ".join(a.tool_calls) + "]")
    if a.llm_error:
        out.append(f"[model note: {a.llm_error}]")
    if a.fallback_reason:
        out.append(f"[agent note: {a.fallback_reason}]")
    mode = "agent" if a.mode == "agent" and a.used_llm else ("LLM" if a.used_llm else "offline writer")
    extra = " (the model's draft failed the checks or the model was unavailable; safe writer used)" if a.fell_back else ""
    extra += " (the model's draft was completed from the sources)" if a.repaired else ""
    out.append(f"\n[{a.status}{' / ' + a.reason if a.reason and a.status != 'answered' else ''} | {mode}{extra}]")
    return "\n".join(out)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # product text contains characters Windows consoles reject
    ap = argparse.ArgumentParser(description="Ask the toothbrush comparison assistant.")
    ap.add_argument("question", nargs="?", help="your question; omit for interactive mode")
    ap.add_argument("--json", action="store_true", help="print the full Answer as JSON")
    ap.add_argument("--offline", action="store_true", help="do not use an LLM even if one is configured")
    ap.add_argument("--agent", action="store_true", help="let the LLM choose and call tools (needs a tool-calling model)")
    ap.add_argument("-v", "--verbose", action="count", default=0,
                    help="show logs on stderr: -v for what happened (model calls, tools, fallbacks), -vv for every step")
    args = ap.parse_args()
    # Quiet by default; -v / -vv beat LOG_LEVEL, and LOG_LEVEL / LOG_FILE / LOG_FORMAT in .env also apply.
    setup_logging("WARNING", level=("DEBUG" if args.verbose > 1 else "INFO") if args.verbose else None)
    llm = None if args.offline else "auto"
    conv = Conversation()

    def run(q: str) -> None:
        new_request_id()
        a = conv.ask(q, llm=llm, agent=args.agent)
        print(a.model_dump_json(indent=2) if args.json else render(a))

    if args.question:
        run(args.question)
        return 0
    print("Ask about Philips Sonicare vs Oral-B electric toothbrushes. Follow-ups work (\"and its price?\"). "
          "/new = new conversation, empty line = quit.")
    while (q := input("\n> ").strip()):
        if q == "/new":
            conv.reset()
            print("Started a new conversation.")
            continue
        run(q)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
