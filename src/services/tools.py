"""Read-only tools over the product data. One tool layer, three front ends:

    src/mcp_server/server.py   exposes these functions to any MCP client
    src/services/agent.py      lets an LLM choose and call them (optional agent mode)
    src/app/api.py            GET /products, POST /compare ...

Every function takes plain arguments and returns a JSON-serializable dict. Rules that hold for all of them:

* Read-only. Nothing here writes, runs raw SQL, scrapes, or touches the LLM settings.
* Citations survive. Anything that states a fact also returns the snippet ids that prove it, and the
  snippets themselves (verbatim text and source URL), so an answer built from a tool can cite them.
* A value of null means "not found in sources", never "no". Tools say so in `not_found`.
* Bad input gives {"error": "..."} with a hint, never an exception, so an agent can correct itself.
* Output is bounded (few products, few snippets), and scraped text that looks like an instruction is
  withheld, because page text is untrusted data.
"""
from __future__ import annotations

import json
import re

from src.guardrails import grounding
from src.guardrails.input_checks import looks_like_injection
from src.guardrails.refusals import NOT_FOUND
from src.services import retrieval
from src.storage import sql_store, vector_store

ATTRIBUTES = tuple(retrieval.LABELS)                       # price_usd, modes, timer, battery_life, ...
RANKABLE = {"price_usd": "price_usd", "mode_count": "mode_count"}
FLAGS = tuple(retrieval.BOOLEAN_ATTRIBUTES)                # pressure_sensor, app_connectivity
BRANDS = {"philips": retrieval.PHILIPS, "sonicare": retrieval.PHILIPS, "philips sonicare": retrieval.PHILIPS,
          "oral-b": retrieval.ORALB, "oralb": retrieval.ORALB, "oral b": retrieval.ORALB}
MAX_PRODUCTS = 6          # products per comparison
MAX_SEARCH_K = 8          # snippets per search
WITHHELD = "[withheld: this page text contains instruction-like wording]"


# ------------------------------------------------------------------ helpers
def _products(db) -> list[dict]:
    return [dict(r) for r in sql_store.list_products(db)]


def _brand(value: str | None) -> tuple[str | None, str | None]:
    """(canonical brand, error). None means no filter."""
    if value is None or not str(value).strip():
        return None, None
    brand = BRANDS.get(str(value).strip().lower())
    if brand is None:
        return None, f"Unknown brand {value!r}. Use 'Philips Sonicare' or 'Oral-B'."
    return brand, None


def _snippet_dict(c, score: float | None = None) -> dict:
    """A citation as plain data. Instruction-like page text is withheld, not passed on."""
    out = {"id": c.id, "product_id": c.product_id, "product": c.product_name, "section": c.section,
           "text": WITHHELD if looks_like_injection(c.text) else c.text, "source_url": c.source_url}
    if score is not None:
        out["score"] = round(score, 3)
    return out


def _fact_dict(f: retrieval.Fact) -> dict:
    return {"product_id": f.product_id, "product": f.product_name, "attribute": f.attribute, "label": f.label,
            "value": f.value, "snippet_ids": f.snippet_ids}


def _unknown_ids(ids: list[str], products: list[dict]) -> list[str]:
    known = {p["product_id"] for p in products}
    return [i for i in ids if i not in known]


def _coerce_ids(values: list[str], products: list[dict]) -> tuple[list[str], dict[str, list[str]], list[str]]:
    """Product ids from a mixed list of ids and names. A model (or a person) often writes "Sonicare 9300" or "iO9"
    where an id is expected, and the product is unambiguous, so it is understood rather than rejected.

    Returns (ids, {what was written: ids it was understood as}, values that match nothing).
    """
    known = {p["product_id"] for p in products}
    ids: list[str] = []
    understood: dict[str, list[str]] = {}
    unmatched: list[str] = []
    for value in values:
        value = str(value)
        if value in known:
            ids.append(value)
            continue
        matches = [p["product_id"] for p in retrieval.resolve_products(value, products)]
        if matches:
            understood[value] = matches
            ids += matches
        else:
            unmatched.append(value)
    return list(dict.fromkeys(ids)), understood, unmatched


def _one_id(value: str, products: list[dict]) -> str | None:
    """A single product id from an id or an unambiguous name, else None."""
    ids, _, _ = _coerce_ids([value], products)
    return ids[0] if len(ids) == 1 else None


# ------------------------------------------------------------------ tools
def list_products(brand: str | None = None) -> dict:
    """List the products in the dataset (id, name, SKU, series, price), optionally for one brand."""
    brand, err = _brand(brand)
    if err:
        return {"error": err}
    with sql_store.connect() as db:
        products = _products(db)
    names = retrieval.product_labels(products)
    rows = [{"product_id": p["product_id"], "name": names[p["product_id"]], "brand": p["brand"], "sku": p["sku"],
             "series": p["series"], "price_usd": p["price_usd"]} for p in products if brand in (None, p["brand"])]
    return {"count": len(rows), "products": rows,
            "note": "price_usd null means the price is not found in sources."}


def get_product(product_id: str) -> dict:
    """All normalized fields of one product. null fields are 'not found in sources'.
    product_id is an id such as 'PH-HX9903_15' (from resolve_models or list_products); an unambiguous name works too."""
    with sql_store.connect() as db:
        products = _products(db)
        product_id = _one_id(product_id, products) or product_id
        p = next((x for x in products if x["product_id"] == product_id), None)
        if p is None:
            return {"error": f"Unknown product_id {product_id!r}.", "valid_ids": [x["product_id"] for x in products]}
        names = retrieval.product_labels(products)
    out = {k: p.get(k) for k in ("product_id", "brand", "series", "sku", "price_usd", "mode_count",
                                  "intensity_levels", "pressure_feedback", "app_details", "timer", "battery_life",
                                  "battery_type", "charging", "brushing_motion", "warranty", "notes",
                                  "source_url", "captured_at")}
    out["name"] = names[product_id]
    out["pressure_sensor"] = True if p["pressure_sensor"] else None
    out["app_connectivity"] = True if p["app_connectivity"] else None
    out["modes"] = json.loads(p["modes_json"]) if p["modes_json"] else None
    out["included_items"] = json.loads(p["included_items_json"]) if p["included_items_json"] else None
    claims = json.loads(p["claims_json"]) if p["claims_json"] else None
    out["claims"] = claims[:5] if claims else None
    out["note"] = "null means 'not found in sources', never 'no'. Use compare_products to get citations."
    return out


def compare_products(product_ids: list[str], attributes: list[str] | None = None) -> dict:
    """Values of the chosen attributes for up to 6 products, each with its proving snippet ids and snippets.
    product_ids are ids such as 'PH-HX9903_15' (get them from resolve_models or list_products); a name such as
    'Sonicare 9300' or 'iO9' is also understood. attributes: price_usd, modes, timer, battery_life, charging,
    warranty, pressure_sensor, app_connectivity, included_items, claims (default: a standard set)."""
    attrs = list(attributes) if attributes else list(retrieval.DEFAULT_ATTRIBUTES)
    bad = [a for a in attrs if a not in ATTRIBUTES]
    if bad:
        return {"error": f"Unknown attribute(s) {bad}.", "valid_attributes": list(ATTRIBUTES)}
    if not product_ids:
        return {"error": "Give at least one product_id (see list_products)."}
    with sql_store.connect() as db:
        products = _products(db)
        ids, understood, missing = _coerce_ids(list(product_ids), products)
        if missing:
            return {"error": f"Unknown product_id(s) {missing}.", "valid_ids": [p["product_id"] for p in products]}
        ids = ids[:MAX_PRODUCTS]
        names = retrieval.product_labels(products)
        chosen = [p for i in ids for p in products if p["product_id"] == i]
        snippets: dict = {}
        facts = retrieval.build_facts(db, [(p, a) for p in chosen for a in attrs], names, snippets)
    result = {"facts": [_fact_dict(f) for f in facts],
              "snippets": [_snippet_dict(c) for c in snippets.values()],
              "not_found": [f"{f.product_name} / {f.label}" for f in facts if f.value is None],
              "note": f"A null value is '{NOT_FOUND}', which is not the same as 'no'. Cite snippets as [id]."}
    if understood:
        result["understood_as"] = understood         # names that were accepted in place of ids
    return result


def rank_products(attribute: str = "price_usd", order: str = "asc", brand: str | None = None,
                  requires: list[str] | None = None, limit: int = 5) -> dict:
    """Rank products by price_usd or mode_count. `requires` keeps only products whose pages state
    pressure_sensor and/or app_connectivity. Twin packs are left out (their price covers two brushes)."""
    if attribute not in RANKABLE:
        return {"error": f"Cannot rank by {attribute!r}.", "valid_attributes": list(RANKABLE)}
    if order not in ("asc", "desc"):
        return {"error": "order must be 'asc' or 'desc'."}
    need = list(requires or [])
    if [r for r in need if r not in FLAGS]:
        return {"error": f"requires may only contain {list(FLAGS)}."}
    brand, err = _brand(brand)
    if err:
        return {"error": err}
    limit = max(1, min(int(limit), 10))
    with sql_store.connect() as db:
        products = _products(db)
        names = retrieval.product_labels(products)
        pool = [p for p in products if brand in (None, p["brand"]) and p[attribute] is not None
                and not (p["notes"] or "").startswith("Twin pack") and all(p[r] == 1 for r in need)]
        pool.sort(key=lambda p: p[attribute], reverse=(order == "desc"))
        top = pool[:limit]
        ev_attr = "price_usd" if attribute == "price_usd" else "modes"
        snippets: dict = {}
        facts = retrieval.build_facts(db, [(p, ev_attr) for p in top], names, snippets)
    ranking = [{"rank": i + 1, "product_id": p["product_id"], "product": names[p["product_id"]],
                attribute: p[attribute], "snippet_ids": f.snippet_ids} for i, (p, f) in enumerate(zip(top, facts))]
    return {"ranking": ranking, "considered": len(pool), "snippets": [_snippet_dict(c) for c in snippets.values()],
            "note": "Twin packs are excluded. Prices are a snapshot. Products whose pages do not state the "
                    f"attribute are not ranked ('{NOT_FOUND}')."}


def search_pages(query: str, k: int = 5, brand: str | None = None, product_id: str | None = None) -> dict:
    """Meaning-based search over the product page text. Returns the closest snippets with their ids."""
    if not query or not query.strip():
        return {"error": "query is empty."}
    brand, err = _brand(brand)
    if err:
        return {"error": err}
    k = max(1, min(int(k), MAX_SEARCH_K))
    with sql_store.connect() as db:
        products = _products(db)
    if product_id and _unknown_ids([product_id], products):
        return {"error": f"Unknown product_id {product_id!r}.", "valid_ids": [p["product_id"] for p in products]}
    names = retrieval.product_labels(products)
    hits = [h for h in vector_store.search(query.strip()[:500], k=k, brand=brand, product_id=product_id)
            if h.score >= retrieval.MIN_SEARCH_SCORE]
    from src.common.schema import Citation

    results = [_snippet_dict(Citation(id=h.snippet_id, product_id=h.product_id, product_name=names[h.product_id],
                                      section=h.section, text=h.text, source_url=h.source_url), h.score) for h in hits]
    return {"count": len(results), "results": results,
            "note": "Weak matches are dropped; an empty list means nothing relevant was found in sources."}


def get_snippet(snippet_id: str) -> dict:
    """One stored page snippet in full (verbatim text and source URL)."""
    with sql_store.connect() as db:
        row = sql_store.get_snippet(db, snippet_id)
        names = retrieval.product_labels(_products(db)) if row else {}
    if row is None:
        return {"error": f"Unknown snippet id {snippet_id!r}."}
    from src.common.schema import Citation

    return _snippet_dict(Citation(id=row["snippet_id"], product_id=row["product_id"],
                                  product_name=names[row["product_id"]], section=row["section"],
                                  text=row["text"], source_url=row["source_url"]))


def get_evidence(product_id: str, attribute: str) -> dict:
    """The snippets that prove one attribute of one product."""
    if attribute not in ATTRIBUTES:
        return {"error": f"Unknown attribute {attribute!r}.", "valid_attributes": list(ATTRIBUTES)}
    with sql_store.connect() as db:
        products = _products(db)
        product_id = _one_id(product_id, products) or product_id
        p = next((x for x in products if x["product_id"] == product_id), None)
        if p is None:
            return {"error": f"Unknown product_id {product_id!r}.", "valid_ids": [x["product_id"] for x in products]}
        names = retrieval.product_labels(products)
        snippets: dict = {}
        fact = retrieval.build_facts(db, [(p, attribute)], names, snippets)[0]
    return {"fact": _fact_dict(fact), "snippets": [_snippet_dict(c) for c in snippets.values()],
            "not_found": fact.value is None}


def resolve_models(text: str) -> dict:
    """Which products, brands and attributes a piece of text mentions, and which model names are not in the data."""
    with sql_store.connect() as db:
        products = _products(db)
    names = retrieval.product_labels(products)
    named = retrieval.resolve_products(text, products)
    return {"products": [{"product_id": p["product_id"], "name": names[p["product_id"]]} for p in named],
            "brands": retrieval.detect_brands(text),
            "attributes": retrieval.detect_attributes(text),
            "unknown_models": retrieval.find_unknown_models(text, products),
            "note": "unknown_models are models the text names that are not in the collected data."}


def check_answer(text: str, allowed_ids: list[str]) -> dict:
    """Run the grounding guard on a draft answer: every [id] must be in allowed_ids, every number must appear
    in those snippets, no foreign links, and facts need a citation (or 'not found in sources')."""
    ids = list(dict.fromkeys(allowed_ids or []))
    with sql_store.connect() as db:
        rows = [sql_store.get_snippet(db, i) for i in ids]
        names = " ".join(p["name"] + " " + (p["sku"] or "") for p in _products(db))
    unknown = [i for i, r in zip(ids, rows) if r is None]
    evidence = " ".join(r["text"] for r in rows if r) + " " + names
    report = grounding.verify(text, {i for i, r in zip(ids, rows) if r}, evidence)
    return {"ok": report.ok and not unknown, "problems": report.problems + [f"unknown snippet id {i}" for i in unknown],
            "cited_ids": grounding.cited_ids(text)}


# The tools an agent or MCP client may call, by name. `ask` (the full pipeline) is added by the front ends.
TOOLS = {f.__name__: f for f in (list_products, get_product, compare_products, rank_products, search_pages,
                                 get_snippet, get_evidence, resolve_models, check_answer)}

_ID_RE = re.compile(r"[A-Z]{2}-[A-Za-z0-9_\-]+#\d{3}")


def snippet_ids_in(result: dict) -> set[str]:
    """Every snippet id a tool result makes citable (used to build the allow-list for an agent's answer)."""
    return set(_ID_RE.findall(json.dumps(result)))
