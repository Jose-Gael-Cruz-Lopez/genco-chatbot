import hashlib
from unittest.mock import MagicMock, patch

import pytest

from app.agent import faq_gaps


def _sb_rows(rows):
    sb = MagicMock()
    chain = sb.table.return_value.select.return_value.eq.return_value
    chain.order.return_value.limit.return_value.execute.return_value = MagicMock(data=rows)
    return sb


def test_gaps_group_the_same_question_asked_different_ways():
    rows = [
        {"id": "1", "question": "Do you ship to NY?", "created_at": "2026-08-01"},
        {"id": "2", "question": "do you ship to ny", "created_at": "2026-08-02"},
        {"id": "3", "question": "What is a refill station?", "created_at": "2026-08-03"},
    ]
    with patch("app.agent.faq_gaps.get_supabase", return_value=_sb_rows(rows)):
        gaps = faq_gaps.list_gaps()
    assert gaps[0]["count"] == 2
    assert set(gaps[0]["ids"]) == {"1", "2"}
    assert gaps[1]["count"] == 1


def test_gaps_are_ordered_most_asked_first():
    rows = [
        {"id": "1", "question": "rare question", "created_at": "2026-08-01"},
        {"id": "2", "question": "common", "created_at": "2026-08-02"},
        {"id": "3", "question": "common", "created_at": "2026-08-03"},
    ]
    with patch("app.agent.faq_gaps.get_supabase", return_value=_sb_rows(rows)):
        gaps = faq_gaps.list_gaps()
    assert gaps[0]["count"] == 2
    assert gaps[0]["question"] == "common"


def test_gaps_show_the_most_recent_wording_and_last_asked():
    rows = [
        {"id": "1", "question": "do you ship", "created_at": "2026-08-01"},
        {"id": "2", "question": "Do you ship?", "created_at": "2026-08-09"},
    ]
    with patch("app.agent.faq_gaps.get_supabase", return_value=_sb_rows(rows)):
        gaps = faq_gaps.list_gaps()
    assert gaps[0]["question"] == "Do you ship?"
    assert gaps[0]["last_asked"] == "2026-08-09"


def test_gaps_returns_empty_on_db_failure():
    sb = MagicMock()
    sb.table.side_effect = RuntimeError("supabase down")
    with patch("app.agent.faq_gaps.get_supabase", return_value=sb):
        assert faq_gaps.list_gaps() == []


def test_publish_writes_a_portal_owned_row():
    sb = MagicMock()
    with patch("app.agent.faq_gaps.get_supabase", return_value=sb):
        faq_gaps.publish_entry("Shipping", "We ship via USPS.", [])
    row = sb.table.return_value.upsert.call_args[0][0]
    assert row["managed_by"] == "portal"
    assert row["content"] == "We ship via USPS."
    assert row["embedding"] is None
    assert row["metadata"] == {"source": "portal", "title": "Shipping"}
    # Namespaced with a "portal:" prefix so it can never collide with the hash
    # ingest computes for a markdown chunk of the same text.
    assert row["content_hash"] == hashlib.sha256(b"portal:We ship via USPS.").hexdigest()


def test_publish_marks_the_gap_resolved():
    sb = MagicMock()
    with patch("app.agent.faq_gaps.get_supabase", return_value=sb):
        faq_gaps.publish_entry("T", "Body", ["1", "2"])
    sb.table.return_value.update.assert_called_once_with({"resolved": True})
    sb.table.return_value.update.return_value.in_.assert_called_once_with(
        "id", ["1", "2"])


def test_publish_rejects_blank_input():
    with pytest.raises(ValueError):
        faq_gaps.publish_entry("", "Body", [])
    with pytest.raises(ValueError):
        faq_gaps.publish_entry("Title", "   ", [])


def test_publish_upserts_so_a_corrected_answer_replaces_the_old_one():
    sb = MagicMock()
    with patch("app.agent.faq_gaps.get_supabase", return_value=sb):
        faq_gaps.publish_entry("T", "Body", [])
    assert sb.table.return_value.upsert.call_args.kwargs["on_conflict"] == "content_hash"


def test_portal_hash_cannot_collide_with_a_file_chunk():
    """A portal answer whose text happens to match a markdown chunk must still be
    its own row. Sharing a content_hash would let the next re-ingest's upsert flip
    the row to managed_by='file', after which the prune could delete Greg's work.
    """
    content = "We ship via USPS."
    sb = MagicMock()
    with patch("app.agent.faq_gaps.get_supabase", return_value=sb):
        faq_gaps.publish_entry("Shipping", content, [])
    row = sb.table.return_value.upsert.call_args[0][0]
    assert row["content_hash"] != hashlib.sha256(content.encode()).hexdigest()


def test_republishing_the_same_answer_reuses_one_row():
    sb = MagicMock()
    with patch("app.agent.faq_gaps.get_supabase", return_value=sb):
        faq_gaps.publish_entry("Title", "Body", [])
        first = sb.table.return_value.upsert.call_args[0][0]["content_hash"]
        faq_gaps.publish_entry("Corrected title", "Body", [])
        second = sb.table.return_value.upsert.call_args[0][0]["content_hash"]
    assert first == second
