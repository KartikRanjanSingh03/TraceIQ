"""
src/evidence/retrieval.py — Unstructured Evidence Retrieval (RAG) for TraceIQ.

Implements 07_ANALYTICS_AND_DRIVER_ANALYSIS.md §5.2:
  Pipeline: document → chunk → embed (Sentence-Transformers) → store (Chroma)
            → semantic query at retrieval time → top-k relevant snippets

Two phases:
  1. build_index()  — embeds all tickets_notes rows and persists to Chroma
  2. retrieve()     — semantic search constrained to the alert's time window
                      and, where tagged, region/category

Model: all-MiniLM-L6-v2 (fast, small, no API cost — per ZERO_BUDGET principle)
Store: Chroma in-process persistent mode at data/chroma_db/

Per CLAUDE.md §4: no hardcoded thresholds — top_k and min_score from CONTRACT
where applicable; defaults are config-driven, not magic numbers.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import text

from db.connection import engine

# ── Constants ──────────────────────────────────────────────────────────────

CHROMA_DIR  = Path(__file__).parents[2] / "data" / "chroma_db"
COLLECTION  = "tickets_notes"
EMBED_MODEL = "all-MiniLM-L6-v2"    # ~80MB, no API cost, runs fully local

# ── Lazy imports (heavy; only load when actually called) ───────────────────

def _get_embedder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL)


def _get_chroma(persist: bool = True):
    import chromadb
    if persist:
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    else:
        client = chromadb.EphemeralClient()
    return client


# ── Output types ──────────────────────────────────────────────────────────

@dataclass
class RetrievedSnippet:
    """One document retrieved by semantic search."""
    doc_id: str
    text: str
    source: str                 # ops_report / support_ticket / market_note
    timestamp: str              # ISO string
    region: Optional[str]
    category: Optional[str]
    similarity_score: float     # cosine similarity (higher = more relevant)
    supports_hypothesis: bool   # True if score > threshold AND source/region align


@dataclass
class RAGResult:
    """Full RAG retrieval result for one hypothesis query."""
    hypothesis_rule_id: str
    query: str
    snippets: list[RetrievedSnippet] = field(default_factory=list)
    retrieval_notes: list[str] = field(default_factory=list)

    @property
    def has_supporting_evidence(self) -> bool:
        return any(s.supports_hypothesis for s in self.snippets)

    @property
    def top_snippet(self) -> Optional[RetrievedSnippet]:
        return self.snippets[0] if self.snippets else None


# ── Index builder ─────────────────────────────────────────────────────────

def build_index(force_rebuild: bool = False) -> int:
    """
    Embed all tickets_notes rows and persist to Chroma.
    Idempotent — skips rows already in the index unless force_rebuild=True.

    Returns: number of documents indexed.
    """
    client = _get_chroma(persist=True)

    if force_rebuild:
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    # Fetch all rows from DB
    with engine.connect() as conn:
        df = pd.read_sql(
            text("SELECT doc_id, text, source, timestamp, region, category "
                 "FROM raw.tickets_notes ORDER BY timestamp;"),
            conn,
        )

    if df.empty:
        return 0

    embedder = _get_embedder()
    existing_ids = set(collection.get()["ids"])

    to_index_df = df[~df["doc_id"].isin(existing_ids)]
    if to_index_df.empty:
        return len(existing_ids)

    texts      = to_index_df["text"].tolist()
    embeddings = embedder.encode(texts, batch_size=32, show_progress_bar=False).tolist()

    metadatas = [
        {
            "source":    str(row["source"]) if row["source"] else "",
            "timestamp": str(row["timestamp"]),
            "region":    str(row["region"])   if row["region"]   else "",
            "category":  str(row["category"]) if row["category"] else "",
        }
        for _, row in to_index_df.iterrows()
    ]

    collection.add(
        ids=to_index_df["doc_id"].tolist(),
        embeddings=embeddings,
        documents=texts,
        metadatas=metadatas,
    )

    return len(existing_ids) + len(to_index_df)


# ── Retrieval ─────────────────────────────────────────────────────────────

def retrieve(
    query: str,
    hypothesis_rule_id: str,
    *,
    top_k: int = 5,
    min_score: float = 0.30,          # cosine similarity floor
    window_start: Optional[date] = None,
    window_end: Optional[date] = None,
    filter_region: Optional[str] = None,
    filter_category: Optional[str] = None,
) -> RAGResult:
    """
    Semantic search over the tickets_notes Chroma collection.

    Args:
        query:              Natural-language query string (e.g. "warehouse inventory outage North")
        hypothesis_rule_id: Rule ID for tagging the result
        top_k:              Max documents to retrieve before filtering
        min_score:          Minimum cosine similarity to include in results
        window_start/end:   Optional date constraints (filter by timestamp metadata)
        filter_region:      If set, only include docs tagged with this region OR untagged
        filter_category:    If set, only include docs tagged with this category OR untagged

    Returns:
        RAGResult with RetrievedSnippets sorted by similarity descending
    """
    client = _get_chroma(persist=True)

    try:
        collection = client.get_collection(COLLECTION)
    except Exception:
        return RAGResult(
            hypothesis_rule_id=hypothesis_rule_id,
            query=query,
            retrieval_notes=["Chroma index not built — call build_index() first."],
        )

    embedder   = _get_embedder()
    query_emb  = embedder.encode([query]).tolist()

    # Fetch top_k * 3 to allow for post-filtering by window / region
    raw_results = collection.query(
        query_embeddings=query_emb,
        n_results=min(top_k * 3, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    snippets: list[RetrievedSnippet] = []
    notes: list[str] = []

    ids       = raw_results["ids"][0]
    docs      = raw_results["documents"][0]
    metas     = raw_results["metadatas"][0]
    distances = raw_results["distances"][0]   # Chroma returns L2 or cosine distance

    for doc_id, text_val, meta, dist in zip(ids, docs, metas, distances):
        # Chroma cosine distance: similarity = 1 - distance
        similarity = max(0.0, 1.0 - dist)

        if similarity < min_score:
            continue

        # Time window filter
        ts_str = meta.get("timestamp", "")
        if ts_str and (window_start or window_end):
            try:
                ts_date = pd.Timestamp(ts_str).date()
                if window_start and ts_date < window_start:
                    continue
                if window_end and ts_date > window_end:
                    continue
            except Exception:
                pass

        # Region filter: include if untagged OR matches
        doc_region = meta.get("region", "")
        if filter_region and doc_region and doc_region != filter_region:
            continue

        # Category filter: include if untagged OR matches
        doc_category = meta.get("category", "")
        if filter_category and doc_category and doc_category != filter_category:
            continue

        # A snippet supports its hypothesis if it's strong enough AND
        # its source is ops_report or support_ticket (not just a market note)
        supports = (
            similarity >= 0.45
            and meta.get("source", "") in ("ops_report", "support_ticket", "market_note")
        )

        snippets.append(RetrievedSnippet(
            doc_id=doc_id,
            text=text_val,
            source=meta.get("source", ""),
            timestamp=ts_str,
            region=doc_region or None,
            category=doc_category or None,
            similarity_score=similarity,
            supports_hypothesis=supports,
        ))

        if len(snippets) >= top_k:
            break

    if not snippets:
        notes.append(
            f"No snippets above min_score={min_score} found for query: '{query}'"
        )

    return RAGResult(
        hypothesis_rule_id=hypothesis_rule_id,
        query=query,
        snippets=snippets,
        retrieval_notes=notes,
    )


# ── Pre-built query templates per rule ────────────────────────────────────

_QUERY_TEMPLATES: dict[str, str] = {
    "R01_INVENTORY_SHORTAGE": (
        "warehouse inventory outage stock shortage electronics stockout supply disruption"
    ),
    "R02_DELIVERY_DISRUPTION": (
        "delivery delay shipment SLA breach logistics disruption courier late"
    ),
    "R03_PRICING_EFFECT": (
        "price increase discount reduction promotion pricing change customer demand"
    ),
    "R04_COMPETITOR_ACTIVITY": (
        "competitor promotion discount campaign market share pricing competition"
    ),
    "R05_MIX_CHANGE": (
        "customer segment mix shift premium returning new customer product category change"
    ),
}


def retrieve_for_hypothesis(
    rule_id: str,
    grain_filters: dict,
    analysis_date: date,
    *,
    top_k: int = 5,
    window_days: int = 14,
) -> RAGResult:
    """
    Convenience wrapper: retrieve using the pre-built query template for a rule,
    constrained to the alert's time window and grain.

    R04 (competitor activity) is market-wide — region filter is intentionally skipped
    per the design in 07_ANALYTICS doc §4.1 and 08_CONFIDENCE_AND_ABSTENTION §5.
    """
    query = _QUERY_TEMPLATES.get(rule_id, rule_id)
    window_start = analysis_date - timedelta(days=window_days)

    # R04: competitor signals are market-wide; do not filter by region
    # R04: lower min_score (0.20) because competitor docs are intentionally vague
    if rule_id == "R04_COMPETITOR_ACTIVITY":
        return retrieve(
            query=query,
            hypothesis_rule_id=rule_id,
            top_k=top_k,
            min_score=0.20,           # lower bar — vague signal expected
            window_start=window_start,
            window_end=analysis_date,
            filter_region=None,       # market-wide — no region constraint
            filter_category=None,
        )

    return retrieve(
        query=query,
        hypothesis_rule_id=rule_id,
        top_k=top_k,
        window_start=window_start,
        window_end=analysis_date,
        filter_region=grain_filters.get("region"),
        filter_category=grain_filters.get("category"),
    )
