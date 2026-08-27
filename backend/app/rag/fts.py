"""Zero-AI retrieval: Postgres full-text matching over the KB.

This is the FAQ-mode counterpart to `rag.retrieve` and deliberately imports no
vector or model code — BOT_MODE=faq must reach no AI service at all.
"""
import logging

from app.db import get_supabase

logger = logging.getLogger(__name__)

# Below this ts_rank a hit is too weak to show as an answer: the caller takes the
# no-match path (offer to send the question to the team) rather than risk showing
# a confidently wrong FAQ entry. Tuned against the seeded KB; raise it if weak
# matches start slipping through, lower it if good questions fall to escalation.
FTS_MIN_RANK = 0.05


def retrieve_fts(query: str, k: int = 5) -> list[dict]:
    """Full-text match against kb_documents.

    Mirrors `rag.retrieve.retrieve()`'s row shape — {id, content, metadata,
    similarity} — so the frozen retrieval_scores contract is unchanged. Rows come
    back best-match-first; an unmatchable query yields an empty list.
    """
    resp = get_supabase().rpc("match_documents_fts", {
        "query_text": query,
        "match_count": k,
    }).execute()
    return resp.data or []


def best_match(query: str, k: int = 5) -> tuple[dict | None, list[float]]:
    """Return (best hit at/above FTS_MIN_RANK else None, every rank retrieved)."""
    hits = retrieve_fts(query, k=k)
    scores = [float(h.get("similarity") or 0.0) for h in hits]
    if hits and scores[0] >= FTS_MIN_RANK:
        return hits[0], scores
    return None, scores
