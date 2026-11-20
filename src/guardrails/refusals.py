"""The fixed messages the assistant uses when it declines or cannot answer.

They are plain text on purpose: the same wording every time, easy to review,
and never written by an LLM.
"""

# The phrase used whenever a fact is missing. The evaluation looks for it.
NOT_FOUND = "not found in sources"

REFUSALS = {
    "prompt_injection": (
        "I can't follow instructions that try to change how I work, reveal my setup, or skip the "
        "sources. I answer questions about Philips Sonicare and Oral-B electric toothbrushes using "
        "the public product pages I collected. Ask me about a feature, price or comparison and "
        "I'll answer with sources."
    ),
    "medical_advice": (
        "I can't give dental or medical advice. I can only share what the manufacturers publish "
        "about their products, with sources. For anything about your own teeth or gums, please "
        "talk to a dentist. You can ask me what a product page claims, for example the modes, "
        "sensors or published claims of a specific model."
    ),
    "too_long": "That question is too long for me to handle safely. Please shorten it and try again.",
    "empty": "Please type a question about Philips Sonicare or Oral-B electric toothbrushes.",
}

OUT_OF_SCOPE = (
    "I only answer questions about Philips Sonicare and Oral-B electric toothbrushes, based on the "
    "public product pages I collected. Try asking about a model's price, modes, pressure sensor, "
    "app, timer, battery, charging or what is included."
)

HEALTH_NOTE = (
    "Note: this is information published on the manufacturers' pages, not dental or medical advice."
)
