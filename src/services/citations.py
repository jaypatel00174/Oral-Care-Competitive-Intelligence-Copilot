"""Show citations as numbered sources: one number per source page, like a reference list.

Inside the system nothing changes: the writer cites exact snippet ids ([PH-HX9903_15#019]) and every guard checks
them. Only AFTER an answer has passed the checks is it rewritten for people:

    "BrushPacer and SmartTimer [PH-HX9903_15#019] ... 14 days [PH-HX9903_15#006][OB-io9-genius#046]"
 -> "BrushPacer and SmartTimer [1] ... 14 days [1, 2]"

    sources: [1] https://www.usa.philips.com/c-p/HX9903_15/...   snippet ids: PH-HX9903_15#019, PH-HX9903_15#006
             [2] https://oralb.com/en-us/products/compare/...   snippet ids: OB-io9-genius#046

Numbers follow the order in which pages are first cited. The id-cited text is kept in Answer.text_with_ids, so
an auditor (and the evaluation) can still check every claim against its exact snippet.
"""
from __future__ import annotations

import re

from src.common.schema import Citation, Source
from src.guardrails import grounding

_ID = re.compile(r"[A-Z]{2}-[A-Za-z0-9_\-]+#\d{3}")
# One or more citation brackets in a row, optionally separated by spaces: "[A][B]", "[A, B] [C]".
_RUN = re.compile(rf"(?:{grounding.CITATION_RE.pattern})(?:\s*(?:{grounding.CITATION_RE.pattern}))*")


def number_by_source(text: str, citations: list[Citation]) -> tuple[str, list[Source]]:
    """(text with [n] markers, numbered sources). Ids without a known citation are left as they were."""
    by_id = {c.id: c for c in citations}
    numbers: dict[str, int] = {}                 # source url -> number
    sources: dict[str, Source] = {}

    def number_for(snippet_id: str) -> int | None:
        c = by_id.get(snippet_id)
        if c is None:
            return None
        if c.source_url not in numbers:
            numbers[c.source_url] = len(numbers) + 1
            sources[c.source_url] = Source(number=numbers[c.source_url], url=c.source_url, title="", snippet_ids=[],
                                           sections=[])
        s = sources[c.source_url]
        if snippet_id not in s.snippet_ids:
            s.snippet_ids.append(snippet_id)
            if c.section not in s.sections:
                s.sections.append(c.section)
            if c.product_name not in s.title.split("; "):
                s.title = c.product_name if not s.title else f"{s.title}; {c.product_name}"
        return numbers[c.source_url]

    def replace(match: re.Match) -> str:
        ids = _ID.findall(match.group(0))
        nums = [number_for(i) for i in ids]
        if any(n is None for n in nums):        # an unknown id: keep the original marker, do not hide it
            return match.group(0)
        unique = list(dict.fromkeys(nums))
        return "[" + ", ".join(str(n) for n in unique) + "]"

    numbered = _RUN.sub(replace, text)
    return numbered, sorted(sources.values(), key=lambda s: s.number)


def present(answer):
    """Rewrite a verified Answer for people: numbered markers in `text`, the reference list in `sources`, and the
    original id-cited text in `text_with_ids`. Answers without citations are returned unchanged."""
    if not answer.citations or answer.text_with_ids is not None:
        return answer
    numbered, sources = number_by_source(answer.text, answer.citations)
    answer.text_with_ids, answer.text, answer.sources = answer.text, numbered, sources
    return answer
