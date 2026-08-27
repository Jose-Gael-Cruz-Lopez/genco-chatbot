"""The FAQ backlog: one row per feedback event.

Escalated questions already become `question` leads, but a visitor who taps "No"
and then abandons before leaving contact details would otherwise be lost. This
table captures every hit and miss so the team can see what the FAQ is missing.
No PII is stored — the question text and match rank only.
"""
import logging

from app.db import get_supabase

log = logging.getLogger(__name__)


def record_feedback(question: str, top_rank: float, answered: bool) -> None:
    """Record one FAQ hit (answered=True) or miss (answered=False).

    Best-effort by design: this is analytics, and a logging failure must never
    break the visitor's turn.
    """
    try:
        get_supabase().table("faq_misses").insert({
            "question": question,
            "top_rank": top_rank,
            "answered": answered,
        }).execute()
    except Exception:
        log.exception("faq_misses insert failed (answered=%s, rank=%s)",
                      answered, top_rank)
