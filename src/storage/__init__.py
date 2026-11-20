"""Storage: where the scraped data lives so the assistant can query it.

    sql_store.py     SQLite (data/comparison.db)  exact facts: products, snippets, evidence
    vector_store.py  Chroma (data/chroma/)        meaning-based search over the snippets

Both stores use the same snippet ids, so a vector hit can be joined back to SQL
for the product, the URL and the evidence. They are filled by
src/pipeline/build_stores.py and only READ by services, app and mcp_server.
"""
