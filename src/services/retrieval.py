"""Retrieval: understand the question and collect the evidence to answer it.

Nothing here writes an answer and nothing here calls an LLM. The job is to turn a question into
a Context: a small, checkable bundle of FACTS (exact values from SQLite, each with the snippet ids
that prove it) and SNIPPETS (verbatim page text, from SQLite or from Chroma vector search).

How a question is handled
-------------------------
1. Work out WHICH products it is about ("iO9", "the 9300", "Sonicare", "Oral-B"),
   WHICH attributes it asks for ("timer", "price", "pressure sensor") and what KIND of question it
   is ("cheapest ...", "which have ...", "what does ... do").
2. FACTS path   (named products and/or attributes): read the exact values from SQLite. A value that
                is empty becomes a Fact with value None, i.e. "not found in sources". It is never
                guessed and never turned into "no".
3. SEARCH path  (descriptive questions or no attribute): vector search over the snippets, using the
                brand or product as a filter when we know it.
4. OUT OF SCOPE: a question that mentions nothing about toothbrushes and matches nothing well.

"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field

from src.common.schema import ATTRIBUTE_LABELS, Citation
from src.guardrails.input_checks import looks_like_injection
from src.storage import sql_store, vector_store

PHILIPS = "Philips Sonicare"
ORALB = "Oral-B"

MIN_SEARCH_SCORE = 0.50     # vector hits below this are too weak to use
OUT_OF_SCOPE_SCORE = 0.60   # an unrelated question has no brand/attribute AND a best score below this
MAX_FACTS = 30              # keeps answers (and LLM prompts) a readable size
TOP_K = 5                   # vector search results

LABELS = {**ATTRIBUTE_LABELS, "claims": "Marketing claims"}

# Why plain keywords and not an LLM - Deterministic and free & works offline without LLM
# attribute column -> words that mean the user is asking about it
ATTRIBUTE_KEYWORDS = {
    "price_usd": r"\bpric\w*|\bcost\w*|how much|\bcheap\w*|expensive|affordab\w*|budget|priciest",
    "modes": r"\bmodes?\b|\bsettings?\b",
    "intensity_levels": r"intensit\w*",
    "pressure_sensor": r"pressure|too hard|brush(?:ing)? hard",
    "app_connectivity": r"\bapps?\b|bluetooth|connect\w*|smartphone|phone",
    "timer": r"timer|pacer|pacing|2[- ]?min\w*|two[- ]minute",
    "battery_life": r"batter\w*|run ?time|how long (?:does|will|can)[^?]*(?:last|run)|charge lasts",
    "battery_type": r"battery type|lithium|li-?ion",
    "charging": r"charg\w*|\bdock\b",
    "included_items": r"in the box|included|includes?\b|comes? with|what(?:'s| is) (?:in|inside)|accessor\w*|travel case|brush heads?",
    "warranty": r"warrant\w*|guarantee",
    "brushing_motion": r"\bsonic\b|oscillat\w*|vibrat\w*|movements|how does it clean|cleaning (?:action|motion)",
    "claims": r"\bclaims?\b|plaque|clinical\w*|whiten\w*|healthier gums|stain",
}
# Shown when someone asks about a product without naming a feature.
DEFAULT_ATTRIBUTES = ["price_usd", "modes", "pressure_sensor", "app_connectivity", "timer",
                      "battery_life", "charging", "warranty"]
# Yes/not-found features: they can be filtered with "which models have ...".
BOOLEAN_ATTRIBUTES = ["pressure_sensor", "app_connectivity"]

_CHEAPEST = re.compile(r"\bcheap\w*|lowest[- ]price|least expensive|most affordable|inexpensive|\bbudget\b", re.I)
_PRICIEST = re.compile(r"most expensive|priciest|highest[- ]price|\bpremium\b", re.I)
_LIST_INTENT = re.compile(r"^\s*(?:which|what|list|show)\b.*\b(?:have|has|with|support\w*|include\w*|offer\w*)\b", re.I)
_DESCRIPTIVE = re.compile(r"\b(?:what does|what do|how does|how do|explain|describe|tell me about|why)\b", re.I)
_DOMAIN_WORDS = re.compile(
    r"tooth|teeth|brush|oral|sonicare|philips|oral-?b|electric|gum|plaque|bristle|enamel|dental|dentist|"
    r"charger|rechargeable|\bi\s?o\s?-?\s?\d", re.I)


@dataclass
class Fact:
    """One value for one attribute of one product. value None means 'not found in sources'."""

    product_id: str
    product_name: str
    attribute: str
    label: str
    value: str | None
    snippet_ids: list[str] = field(default_factory=list)


@dataclass
class Context:
    """Everything the writer is allowed to use. Nothing outside this reaches an answer."""

    question: str
    facts: list[Fact] = field(default_factory=list)
    snippets: dict[str, Citation] = field(default_factory=dict)   # id -> source (all ids that may be cited)
    search_ids: list[str] = field(default_factory=list)           # ids found by vector search
    notes: list[str] = field(default_factory=list)                # caveats to show with the answer
    unknown_models: list[str] = field(default_factory=list)       # e.g. "iO10": asked about, not in our data
    out_of_scope: bool = False

    @property
    def has_evidence(self) -> bool:
        return any(f.value is not None for f in self.facts) or bool(self.search_ids)


# ------------------------------------------------------------------ understanding the question
def _product_aliases(p: dict) -> list[str]:
    """Regexes that mean 'the user is talking about this product'."""
    name = p["name"].lower()
    if p["product_id"].startswith("OB-"):
        n = re.match(r"OB-io(\d+)", p["product_id"]).group(1)
        # "iO9", "io 9", "iO-9", "iO Series 9", "Series 9"
        return [rf"\bi\s?o\s?-?\s?{n}\b", rf"\bio\s+series\s+{n}\b", rf"\bseries\s+{n}(?!\d)\b"]
    aliases = [rf"\b{num}\b" for num in re.findall(r"\b\d{4}\b", name)]
    aliases += [w for w in ("diamondclean", "prestige", "kids") if w in name]
    if "philips one" in name or "one by sonicare" in name:
        aliases.append(r"philips one|one by sonicare")
    return aliases


def resolve_products(question: str, products: list[dict]) -> list[dict]:
    """Products the question names. Some names match two products (e.g. '6500'): both are returned."""
    q = question.lower()
    return [p for p in products if any(re.search(a, q) for a in _product_aliases(p))]


def detect_brands(question: str) -> list[str]:
    q = question.lower()
    brands = []
    if re.search(r"sonicare|philips", q):
        brands.append(PHILIPS)
    if re.search(r"oral-?\s?b\b|oralb", q):
        brands.append(ORALB)
    return brands


def detect_attributes(question: str) -> list[str]:
    return [a for a, rx in ATTRIBUTE_KEYWORDS.items() if re.search(rx, question, re.I)]


def find_unknown_models(question: str, products: list[dict]) -> list[str]:
    """Models the user names that are not in our data (e.g. 'iO10', 'Sonicare 9000')."""
    known_io = {re.match(r"OB-io(\d+)", p["product_id"]).group(1) for p in products if p["product_id"].startswith("OB-")}
    known_ph = {n for p in products if p["brand"] == PHILIPS for n in re.findall(r"\b\d{4}\b", p["name"])}
    q = question.lower()
    unknown = [f"iO{n}" for n in re.findall(r"\bi\s?o\s?-?\s?(\d{1,2})\b", q) if n not in known_io]
    if re.search(r"sonicare|philips", q):
        unknown += [f"Sonicare {n}" for n in re.findall(r"\b(\d{4})\b", q)
                    if n not in known_ph and not 1990 <= int(n) <= 2035]
    return list(dict.fromkeys(unknown))


# ------------------------------------------------------------------ reading values from SQLite
def _fact_value(p: dict, attr: str) -> str | None:
    """Human-readable value of one attribute, or None when the page did not state it."""
    if attr == "price_usd":
        return f"${p['price_usd']:.2f}" if p["price_usd"] is not None else None
    if attr == "modes":
        modes = json.loads(p["modes_json"]) if p["modes_json"] else None
        if modes:
            return f"{len(modes)} modes: {', '.join(modes)}"
        if p["mode_count"]:
            return f"{p['mode_count']} modes (names not listed on the page)"
        return None
    if attr == "pressure_sensor":
        return (p["pressure_feedback"] or "Yes") if p["pressure_sensor"] else None
    if attr == "app_connectivity":
        return (p["app_details"] or "Yes") if p["app_connectivity"] else None
    if attr == "included_items":
        items = json.loads(p["included_items_json"]) if p["included_items_json"] else None
        return "; ".join(items) if items else None
    if attr == "claims":
        claims = json.loads(p["claims_json"]) if p["claims_json"] else None
        return " | ".join(claims[:2]) if claims else None
    return p.get(attr) or None


def _evidence_rows(db, pid: str, attr: str) -> list[dict]:
    """The snippets that prove this attribute (modes also fall back to the mode-count evidence)."""
    rows = sql_store.get_evidence(db, pid, attr)
    if not rows and attr == "modes":
        rows = sql_store.get_evidence(db, pid, "mode_count")
    return [dict(r) for r in rows]


def _citation(row: dict, names: dict[str, str]) -> Citation:
    return Citation(id=row["snippet_id"], product_id=row["product_id"], product_name=names[row["product_id"]],
                    section=row["section"], text=row["text"], source_url=row["source_url"])


def _search_both_sides(search, question: str, k: int, named: list[dict], brands: list[str]):
    """Vector search that keeps a comparison fair.

    Named products: search inside each one. Otherwise search each brand separately, so a question
    that names no brand returns Philips AND Oral-B text instead of whichever brand scores higher.
    """
    if named:
        per = k if len(named) == 1 else 2
        hits = [h for p in named[:4] for h in search(question, k=per, product_id=p["product_id"])]
    else:
        targets = brands or [PHILIPS, ORALB]
        per = k if len(targets) == 1 else (k + 1) // 2
        hits = [h for b in targets for h in search(question, k=per, brand=b)]
    return sorted(hits, key=lambda h: -h.score)


# ------------------------------------------------------------------ the main entry point
def retrieve(question: str, search=vector_store.search) -> Context:
    """Turn a question into a Context. `search` can be swapped out in tests."""
    db = sql_store.connect()
    try:
        return _retrieve(question, db, search)
    finally:
        db.close()


def _retrieve(question: str, db, search) -> Context:
    products = [dict(r) for r in sql_store.list_products(db)]
    # Two products can share a name (the two Series 6500 variants): add the SKU to tell them apart.
    name_count = Counter(p["name"] for p in products)
    names = {p["product_id"]: p["name"] if name_count[p["name"]] == 1 else f"{p['name']} ({p['sku']})"
             for p in products}
    named = resolve_products(question, products)
    brands = detect_brands(question)
    attrs = detect_attributes(question)
    ctx = Context(question=question, unknown_models=find_unknown_models(question, products))
    cheapest, priciest = bool(_CHEAPEST.search(question)), bool(_PRICIEST.search(question))
    descriptive = bool(_DESCRIPTIVE.search(question))

    # A model we do not have (e.g. iO10) and no known model beside it: say so, do not answer about others.
    if ctx.unknown_models and not named:
        return ctx

    # 4. OUT OF SCOPE: nothing about toothbrushes and nothing in our data matches well.
    if not (named or brands or attrs or ctx.unknown_models or _DOMAIN_WORDS.search(question)):
        hits = search(question, k=1)
        if not hits or hits[0].score < OUT_OF_SCOPE_SCORE:
            ctx.out_of_scope = True
            return ctx

    # ---- 2. FACTS path -----------------------------------------------------------------
    scope = named or [p for p in products if p["brand"] in brands] or products
    use_attrs = list(attrs)
    use_facts = bool(named or attrs)
    if descriptive and not named and not (cheapest or priciest):
        use_facts = False       # "what does the app do?" is answered from page text, not a per-product table

    if cheapest or priciest:                      # "cheapest Oral-B with a pressure sensor"
        use_facts = True
        needed = [a for a in attrs if a in BOOLEAN_ATTRIBUTES]
        pool = [p for p in scope if p["price_usd"] is not None and not (p["notes"] or "").startswith("Twin pack")]
        pool = [p for p in pool if all(p[a] == 1 for a in needed)]
        pool.sort(key=lambda p: p["price_usd"], reverse=priciest and not cheapest)
        ctx.notes.append(f"Ranked {len(pool)} single-brush product(s) by listed price; twin packs are left out "
                         "because their price covers two brushes. Prices are a snapshot.")
        scope, use_attrs = pool[:1], ["price_usd"] + needed
    elif not named and _LIST_INTENT.search(question) and any(a in BOOLEAN_ATTRIBUTES for a in attrs):
        wanted = [a for a in attrs if a in BOOLEAN_ATTRIBUTES]          # "which models have a pressure sensor"
        have = [p for p in scope if all(p[a] == 1 for a in wanted)]
        ctx.notes.append(f"{len(have)} of {len(scope)} products state this on their pages. For the others it is "
                         "not found in sources, which is not the same as 'no'.")
        scope, use_attrs = have, wanted
    elif named and not attrs:
        use_attrs = DEFAULT_ATTRIBUTES                                    # "tell me about the iO9"

    if use_facts and use_attrs:
        pairs = [(p, a) for p in scope for a in use_attrs]
        if len(pairs) > MAX_FACTS:
            ctx.notes.append(f"Showing the first {MAX_FACTS} values only; ask about fewer products for the rest.")
        for p, attr in pairs[:MAX_FACTS]:
            value = _fact_value(p, attr)
            rows = _evidence_rows(db, p["product_id"], attr) if value is not None else []
            for row in rows:
                ctx.snippets[row["snippet_id"]] = _citation(row, names)
            ctx.facts.append(Fact(p["product_id"], names[p["product_id"]], attr, LABELS.get(attr, attr),
                                  value, [r["snippet_id"] for r in rows]))

    # ---- 3. SEARCH path ----------------------------------------------------------------
    if not ctx.facts or (descriptive and attrs):
        withheld = 0
        for h in _search_both_sides(search, question, TOP_K if not ctx.facts else 3, named, brands):
            if h.score < MIN_SEARCH_SCORE:
                continue
            if looks_like_injection(h.text):          # scraped text is data, never instructions
                withheld += 1
                continue
            ctx.snippets[h.snippet_id] = Citation(id=h.snippet_id, product_id=h.product_id,
                                                  product_name=names[h.product_id], section=h.section,
                                                  text=h.text, source_url=h.source_url)
            ctx.search_ids.append(h.snippet_id)
        if withheld:
            ctx.notes.append(f"{withheld} page snippet(s) were withheld because they contained instruction-like text.")
    return ctx
