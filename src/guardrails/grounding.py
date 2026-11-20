"""Checks that run AFTER an answer is written (by an LLM or by the offline writer).

An answer is "grounded" when:
  * every citation like [PH-HX9903_15#019] points at a snippet we actually retrieved,
  * it cites at least one source (unless it only says "not found in sources"),
  * every number in it appears in the retrieved evidence (so no invented figures), and
  * it contains no web address that did not come from our sources.

verify() returns the list of problems, so the caller can log them or fall back to the safe writer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.guardrails.refusals import NOT_FOUND

# A citation is an id in square brackets: [PH-HX9903_15#019] or [OB-io9-genius#003].
# Models often group several ids in one bracket: [OB-io2#000, OB-io2#007]. That is accepted, and
# every id inside is checked on its own.
_ID = r"[A-Z]{2}-[A-Za-z0-9_\-]+#\d{3}"
CITATION_RE = re.compile(rf"\[\s*{_ID}(?:\s*[,;]\s*{_ID})*\s*\]")
_ID_RE = re.compile(_ID)
_NOT_FOUND_THEN_CITE_RE = re.compile(rf"{re.escape(NOT_FOUND)}[\s.,:;)]*\[\s*{_ID}", re.I)
_URL_RE = re.compile(r"https?://[^\s)\]]+", re.I)
# A number, but not one glued to a word or a citation id (so "iO9" and "HX9903" are ignored).
_NUMBER_RE = re.compile(r"(?<![\w#.])\d[\d,]*(?:\.\d+)?")
_LIST_MARKER_RE = re.compile(r"(?m)^\s*\d+[.)]\s+")


@dataclass
class GroundingReport:
    ok: bool
    problems: list[str] = field(default_factory=list)


def cited_ids(text: str) -> list[str]:
    """Citation ids in the order they first appear, without duplicates."""
    seen: dict[str, None] = {}
    for m in CITATION_RE.finditer(text):
        for found in _ID_RE.findall(m.group(0)):
            seen[found] = None
    return list(seen)


def _numbers(text: str) -> set[str]:
    """Numbers in the text, normalised ("62,000" -> "62000", "20." -> "20")."""
    return {n.replace(",", "").rstrip(".") for n in _NUMBER_RE.findall(text)}


def verify(text: str, allowed_ids: set[str], allowed_text: str, allowed_urls: set[str] | None = None,
           max_not_found: int | None = None,
           required_any: list[tuple[str, set[str]]] | None = None) -> GroundingReport:
    """Check an answer against what was retrieved.

    allowed_ids    snippet ids the assistant was given
    allowed_text   all evidence text, product names and the question; numbers must appear in here
    allowed_urls   source URLs the answer may mention (normally none: citations carry the URLs)
    max_not_found  how many "not found in sources" statements are justified (None = do not check).
                   More than that means the answer claims something is missing when the evidence has it.
    required_any   [(name, ids)]: the answer must cite at least one id from each group, so it cannot
                   silently drop a product that has evidence (matters in comparisons)
    """
    problems: list[str] = []
    ids = cited_ids(text)

    for i in ids:
        if i not in allowed_ids:
            problems.append(f"cites {i}, which was not retrieved")
    if not ids and NOT_FOUND not in text.lower():
        problems.append("states facts without any citation")

    body = _LIST_MARKER_RE.sub("", _URL_RE.sub(" ", CITATION_RE.sub(" ", text)))
    unsupported = sorted(_numbers(body) - _numbers(allowed_text))
    if unsupported:
        problems.append("numbers not found in the evidence: " + ", ".join(unsupported))

    for url in _URL_RE.findall(text):
        if url.rstrip(".,") not in (allowed_urls or set()):
            problems.append(f"contains a web address that is not from the sources: {url}")

    # "not found in sources [ID]" contradicts itself: a citation means the fact exists.
    if _NOT_FOUND_THEN_CITE_RE.search(text):
        problems.append(f'says "{NOT_FOUND}" and then cites a source for the same statement')

    if max_not_found is not None:
        said = text.lower().count(NOT_FOUND)
        if said > max_not_found:
            problems.append(f'says "{NOT_FOUND}" {said} time(s) but only {max_not_found} item(s) are missing from the evidence')
    for name, group_ids in required_any or []:
        if not group_ids & set(ids):
            problems.append(f"does not mention {name}, which has evidence")

    return GroundingReport(ok=not problems, problems=problems)
