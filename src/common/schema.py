"""The shared data model: what a "product" and a "snippet" look like.

The two ideas that matter
-------------------------
1. EVIDENCE: every value we store (price, modes, timer, ...) is linked to the
   exact source text it came from. That link is what lets the assistant cite
   its sources and lets the evaluation check that citations are real.

2. None MEANS "NOT FOUND", NEVER "NO": if a page does not mention a feature, the
   field stays None. We only store a value when the page actually says it. This
   is what allows the assistant to answer "not found in sources" honestly
   instead of guessing.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Snippet(BaseModel):
    """A piece of text copied word-for-word from a web page.

    A snippet is the unit of citation: answers point at snippet ids such as
    "OB-io9-genius#022", and the id can be resolved back to the text + URL.
    """

    id: str          # unique id, "<product_id>#<number>", e.g. "PH-HX9903_15#012"
    product_id: str  # which product this text describes
    brand: str       # "Philips Sonicare" or "Oral-B"
    section: str     # where on the page it came from, e.g. "Ease of use > Timer"
    text: str        # the source text (whitespace tidied, otherwise unchanged)
    source_url: str  # page it was copied from (the citation link)


class Product(BaseModel):
    """One electric toothbrush, described with the same fields for both brands.

    Using one common set of fields is what makes an apples-to-apples
    comparison between Sonicare and Oral-B possible.
    """

    # ---- identity ----------------------------------------------------------
    product_id: str       # our id, e.g. "PH-HX9903_15" or "OB-io9-genius"
    brand: str            # "Philips Sonicare" | "Oral-B"
    name: str             # product name as shown on the website
    series: str | None = None   # product family, e.g. "DiamondClean Smart", "iO Series 9"
    sku: str | None = None      # manufacturer / retailer code
    source_url: str             # main product page (default citation link)
    captured_at: str            # date we downloaded the page (prices change!)

    # ---- normalized features (None = not found in sources) -------------------
    price_usd: float | None = None          # listed price in US dollars
    modes: list[str] | None = None          # brushing mode names, e.g. ["Clean", "White+"]
    mode_count: int | None = None           # how many modes
    intensity_levels: str | None = None     # e.g. "3 (High, Medium, Low)" (Philips)
    pressure_sensor: bool | None = None     # True only if the page mentions one
    pressure_feedback: str | None = None    # how it warns you (light, vibration...)
    app_connectivity: bool | None = None    # True only if Bluetooth/app is mentioned
    app_details: str | None = None          # app / Bluetooth wording from the page
    timer: str | None = None                # brushing timer description
    battery_life: str | None = None         # how long one charge lasts
    battery_type: str | None = None         # e.g. "Lithium ION"
    charging: str | None = None             # charger / charging details
    brushing_motion: str | None = None      # cleaning technology, e.g. "62,000 brush movements / min"
    included_items: list[str] | None = None # what is in the box
    warranty: str | None = None             # e.g. "2-year limited warranty"
    claims: list[str] = Field(default_factory=list)  # marketing claims, quoted from the page
    notes: str | None = None                # caveats, e.g. "Twin pack - price is for two brushes"

    # attribute name -> ids of the snippets that prove it, e.g.
    # {"timer": ["PH-HX9903_15#007"], "price_usd": ["PH-HX9903_15#000"]}
    evidence: dict[str, list[str]] = Field(default_factory=dict)

    def set(self, attr: str, value, snippet_id: str | list[str]) -> None:
        """Store a value AND remember which snippet(s) prove it.

        Always use this instead of plain assignment, so no value is ever stored
        without its evidence. Calling it twice for the same attribute adds more
        evidence ids (without duplicates).
        """
        setattr(self, attr, value)
        ids = [snippet_id] if isinstance(snippet_id, str) else list(snippet_id)
        self.evidence.setdefault(attr, [])
        for i in ids:
            if i not in self.evidence[attr]:
                self.evidence[attr].append(i)


# Human-friendly names for the attributes above. Used when printing comparison
# tables or building answers, so users see "Battery life" and not "battery_life".
ATTRIBUTE_LABELS = {
    "price_usd": "Price (USD)",
    "modes": "Brushing modes",
    "mode_count": "Number of modes",
    "intensity_levels": "Intensity levels",
    "pressure_sensor": "Pressure sensor",
    "pressure_feedback": "Pressure feedback",
    "app_connectivity": "App / Bluetooth connectivity",
    "app_details": "App details",
    "timer": "Timer",
    "battery_life": "Battery life",
    "battery_type": "Battery type",
    "charging": "Charging",
    "brushing_motion": "Brushing motion / technology",
    "included_items": "Included items",
    "warranty": "Warranty",
}


class Citation(BaseModel):
    """One source the assistant relied on: a snippet plus where it came from."""

    id: str           # snippet id, e.g. "PH-HX9903_15#019"
    product_id: str
    product_name: str
    section: str
    text: str         # the verbatim source text
    source_url: str   # the page to open to check it


class Answer(BaseModel):
    """What the assistant returns for one question. The API, CLI and evaluation all use this."""

    question: str
    # "answered"     facts were found and cited
    # "not_found"    nothing in the collected sources answers it (we say so, we never guess)
    # "refused"      blocked by a guardrail (see `reason`)
    # "out_of_scope" not about these toothbrushes
    status: str
    text: str
    citations: list[Citation] = Field(default_factory=list)
    reason: str | None = None            # why it was refused, e.g. "prompt_injection"
    used_llm: bool = False               # True if an LLM wrote the final text
    fell_back: bool = False              # True if the LLM draft failed the checks and we used the safe writer
    retrieved_ids: list[str] = Field(default_factory=list)  # every snippet id the assistant could see
