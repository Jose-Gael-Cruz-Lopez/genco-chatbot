from unittest.mock import MagicMock, patch

from app import faq_misses


def test_records_an_answered_event():
    sb = MagicMock()
    with patch("app.faq_misses.get_supabase", return_value=sb):
        faq_misses.record_feedback("do you ship to NY", 0.42, answered=True)
    sb.table.assert_called_once_with("faq_misses")
    sb.table.return_value.insert.assert_called_once_with(
        {"question": "do you ship to NY", "top_rank": 0.42, "answered": True})


def test_records_a_miss_event():
    sb = MagicMock()
    with patch("app.faq_misses.get_supabase", return_value=sb):
        faq_misses.record_feedback("do you sell dog food", 0.0, answered=False)
    sb.table.return_value.insert.assert_called_once_with(
        {"question": "do you sell dog food", "top_rank": 0.0, "answered": False})


def test_stores_no_pii_fields():
    sb = MagicMock()
    with patch("app.faq_misses.get_supabase", return_value=sb):
        faq_misses.record_feedback("q", 0.1, answered=False)
    row = sb.table.return_value.insert.call_args[0][0]
    assert set(row) == {"question", "top_rank", "answered"}


def test_never_raises_when_the_insert_fails():
    sb = MagicMock()
    sb.table.return_value.insert.side_effect = RuntimeError("supabase down")
    with patch("app.faq_misses.get_supabase", return_value=sb):
        faq_misses.record_feedback("q", 0.1, answered=False)  # must not raise
