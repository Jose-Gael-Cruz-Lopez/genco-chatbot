"""The FAQ backlog, and turning it into knowledge.

This is the loop that makes the portal worth having: every question the FAQ could
not answer is surfaced here, and publishing an answer puts it in the knowledge base
immediately — content_tsv is a generated column, so the next visitor to ask gets it
without a re-ingest or a redeploy.
"""
import hashlib
import logging

from app.chat.flows import _norm
from app.db import get_supabase

log = logging.getLogger(__name__)


def list_gaps(limit: int = 50) -> list[dict]:
    """Unanswered questions, grouped by wording and ordered most-asked first.

    Grouping happens here rather than in SQL so it reuses the one normalisation
    rule the conversation code already uses; a generated SQL column would split
    groups on inconsistent spacing.
    """
    try:
        resp = (get_supabase().table("faq_misses")
                .select("id,question,created_at")
                .eq("resolved", False)
                .order("created_at", desc=True)
                .limit(1000).execute())
        rows = resp.data or []
    except Exception:
        log.exception("faq_misses read failed; returning no gaps")
        return []

    # Re-sort newest-first here rather than trusting the query to have done it:
    # the wording and last_asked a group reports come from whichever row is seen
    # first, and that must be the latest one however the rows arrive.
    rows = sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True)

    grouped: dict[str, dict] = {}
    for row in rows:
        question = row.get("question") or ""
        key = _norm(question)
        if not key:
            continue
        gap = grouped.get(key)
        if gap is None:
            grouped[key] = {"question": question, "count": 1,
                            "ids": [row["id"]], "last_asked": row.get("created_at")}
        else:
            gap["count"] += 1
            gap["ids"].append(row["id"])
    ordered = sorted(grouped.values(),
                     key=lambda g: (-g["count"], g["last_asked"] or ""))
    return ordered[:limit]


def publish_entry(title: str, content: str, resolve_ids: list[str]) -> dict:
    """Publish an answer to the knowledge base and clear the gap it fills.

    The row is managed_by='portal', so ingest will never delete it (see
    rag/ingest.py). embedding stays NULL: FAQ mode matches on full text.
    """
    title = (title or "").strip()
    content = (content or "").strip()
    if not title:
        raise ValueError("A title is required.")
    if not content:
        raise ValueError("An answer is required.")

    row = {
        "content": content,
        # Namespaced so a portal answer can never share a row with a markdown
        # chunk of identical text. On a collision the next re-ingest's upsert
        # would rewrite the row as managed_by='file', and the prune could then
        # delete it the moment that chunk changed — losing the team's work to
        # the exact bug managed_by exists to prevent.
        "content_hash": hashlib.sha256(b"portal:" + content.encode()).hexdigest(),
        "embedding": None,
        "metadata": {"source": "portal", "title": title},
        "managed_by": "portal",
    }
    sb = get_supabase()
    # Upsert, so republishing a corrected answer replaces it instead of erroring.
    sb.table("kb_documents").upsert(row, on_conflict="content_hash").execute()
    if resolve_ids:
        try:
            sb.table("faq_misses").update({"resolved": True}).in_(
                "id", resolve_ids).execute()
        except Exception:
            # The answer is published, which is the part that matters; a gap that
            # fails to clear reappears in the list and can be dismissed again.
            log.exception("failed to mark faq_misses resolved: %s", resolve_ids)
    return row
