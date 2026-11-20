"""Vector store (Chroma): search the source text by MEANING.

Use it for fuzzy questions: "which brush is gentler on gums?", "what does the
app do?". For exact questions (prices, counts, filters) use sql_store.py.

How it works
------------
1. Every Snippet is turned into a vector (a list of 384 numbers) by a small
   open-source embedding model that runs locally on the CPU - no API key.
2. Vectors go into a Chroma collection stored in data/chroma/.
3. At question time the question is embedded the same way, and Chroma returns
   the snippets whose vectors are closest.

Every hit carries the same `snippet_id` used in SQLite, so a search result can
be joined back to the product, the URL and the evidence table.

"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.config import Settings
from fastembed import TextEmbedding

from src.common.schema import Snippet

# Open-source, 384-dimension, about 130 MB (downloaded once, then cached).
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
# bge models work best when a SEARCH QUERY (not the documents) starts with this hint.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

CHROMA_DIR = Path(__file__).resolve().parents[2] / "data" / "chroma"
# Where the downloaded embedding model is kept. By default fastembed uses the OS
# temp folder, which can be cleaned at any time and forces a re-download.
MODEL_DIR = Path(__file__).resolve().parents[2] / "data" / "models"
COLLECTION = "snippets"

_embedder: TextEmbedding | None = None


def _get_embedder() -> TextEmbedding:
    """Load the embedding model once and reuse it (loading takes a moment)."""
    global _embedder
    if _embedder is None:
        _embedder = TextEmbedding(model_name=EMBEDDING_MODEL, cache_dir=str(MODEL_DIR))
    return _embedder


def _embed(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in _get_embedder().embed(texts)]


def _client(path: Path = CHROMA_DIR) -> chromadb.ClientAPI:
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))


def _document_text(s: Snippet, product_name: str) -> str:
    """What we embed for a snippet.

    A bare line like "Timer: BrushPacer and SmartTimer" does not say which
    product it belongs to, so we prepend the product name and section. This
    makes "iO9 timer" and "9300 timer" land near the right snippets.
    The ORIGINAL text is stored unchanged as the document, so citations quote
    the source exactly.
    """
    return f"{product_name} | {s.section}: {s.text}"


def build_vectors(snippets: list[Snippet], product_names: dict[str, str], path: Path = CHROMA_DIR) -> int:
    """(Re)build the whole index from scratch. Returns the number of vectors."""
    client = _client(path)
    try:
        client.delete_collection(COLLECTION)  # start clean so rebuilds are repeatable
    except Exception:
        pass  # collection did not exist yet
    col = client.create_collection(
        COLLECTION,
        metadata={"embedding_model": EMBEDDING_MODEL, "hnsw:space": "cosine"},
    )
    batch = 64
    for i in range(0, len(snippets), batch):
        chunk = snippets[i : i + batch]
        col.add(
            ids=[s.id for s in chunk],
            embeddings=_embed([_document_text(s, product_names.get(s.product_id, s.product_id)) for s in chunk]),
            documents=[s.text for s in chunk],  # original, citable text
            metadatas=[
                {"product_id": s.product_id, "brand": s.brand, "section": s.section, "source_url": s.source_url}
                for s in chunk
            ],
        )
    return col.count()


@dataclass
class Hit:
    """One search result."""

    snippet_id: str
    text: str
    score: float  # similarity: 1.0 = identical meaning, lower = less related
    product_id: str
    brand: str
    section: str
    source_url: str


def search(query: str, k: int = 5, brand: str | None = None, product_id: str | None = None,
           path: Path = CHROMA_DIR) -> list[Hit]:
    """Return the k snippets closest in meaning to `query`.

    Optional filters: only one brand, or only one product.
    Raises RuntimeError if the index was built with a different embedding model.
    """
    col = _client(path).get_collection(COLLECTION)
    built_with = (col.metadata or {}).get("embedding_model")
    if built_with != EMBEDDING_MODEL:
        raise RuntimeError(f"Index was built with {built_with!r} but code uses {EMBEDDING_MODEL!r}. Rebuild it.")

    where = None
    filters = [{"brand": brand}] if brand else []
    if product_id:
        filters.append({"product_id": product_id})
    if filters:
        where = filters[0] if len(filters) == 1 else {"$and": filters}

    res = col.query(query_embeddings=_embed([QUERY_PREFIX + query]), n_results=k, where=where)
    hits = []
    for sid, doc, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
        hits.append(Hit(sid, doc, 1.0 - dist, meta["product_id"], meta["brand"], meta["section"], meta["source_url"]))
    return hits
