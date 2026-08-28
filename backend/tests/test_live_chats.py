from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from app.live import chats

NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC)
SID = "11111111-1111-1111-1111-111111111111"


def _chat(status=chats.WAITING, started=0, visitor=0, **kw):
    base = {
        "id": "chat-1", "session_id": SID, "lead_id": "lead-1", "status": status,
        "question": "do you ship", "ended_reason": None,
        "started_at": (NOW - timedelta(seconds=started)).isoformat(),
        "visitor_last_seen_at": (NOW - timedelta(seconds=visitor)).isoformat(),
    }
    base.update(kw)
    return base


# ── sweep: the four end reasons ───────────────────────────────────────────
def test_waiting_too_long_ends_as_not_accepted():
    with patch("app.live.chats.end_chat", side_effect=lambda c, r: {**c, "status": chats.ENDED, "ended_reason": r}):
        out = chats.sweep(_chat(started=120), now=NOW)
    assert out["ended_reason"] == chats.END_NOT_ACCEPTED


def test_waiting_inside_the_window_stays_waiting():
    with patch("app.live.chats.is_agent_available", return_value=True):
        out = chats.sweep(_chat(started=10), now=NOW)
    assert out["status"] == chats.WAITING


def test_active_chat_ends_when_the_agent_heartbeat_goes_stale():
    with patch("app.live.chats.is_agent_available", return_value=False), \
         patch("app.live.chats.end_chat", side_effect=lambda c, r: {**c, "ended_reason": r}):
        out = chats.sweep(_chat(status=chats.ACTIVE), now=NOW)
    assert out["ended_reason"] == chats.END_AGENT_DROPPED


def test_active_chat_ends_when_the_visitor_stops_polling():
    with patch("app.live.chats.is_agent_available", return_value=True), \
         patch("app.live.chats.end_chat", side_effect=lambda c, r: {**c, "ended_reason": r}):
        out = chats.sweep(_chat(status=chats.ACTIVE, visitor=300), now=NOW)
    assert out["ended_reason"] == chats.END_VISITOR_LEFT


def test_a_healthy_active_chat_is_left_alone():
    with patch("app.live.chats.is_agent_available", return_value=True):
        out = chats.sweep(_chat(status=chats.ACTIVE, visitor=3), now=NOW)
    assert out["status"] == chats.ACTIVE


def test_an_already_ended_chat_is_never_re_ended():
    with patch("app.live.chats.end_chat") as end:
        chats.sweep(_chat(status=chats.ENDED), now=NOW)
    end.assert_not_called()


# ── end_chat: the lead is always notified, exactly once ───────────────────
def test_end_chat_attaches_the_transcript_and_notifies_once():
    sb = MagicMock()
    transcript = [{"role": "user", "content": "hi"},
                  {"role": "agent", "content": "hello"}]
    with patch("app.live.chats.get_supabase", return_value=sb), \
         patch("app.live.chats.get_recent_messages", return_value=transcript), \
         patch("app.live.chats.notify_lead") as notify:
        chats.end_chat(_chat(status=chats.ACTIVE), chats.END_AGENT_ENDED)
    notify.assert_called_once()
    # the transcript is written onto the lead so the email carries it
    updates = [c[0][0] for c in sb.table.return_value.update.call_args_list]
    assert any("hello" in str(u.get("message", "")) for u in updates)


def test_end_chat_still_ends_when_notification_fails():
    sb = MagicMock()
    with patch("app.live.chats.get_supabase", return_value=sb), \
         patch("app.live.chats.get_recent_messages", return_value=[]), \
         patch("app.live.chats.notify_lead", side_effect=RuntimeError("down")):
        out = chats.end_chat(_chat(status=chats.ACTIVE), chats.END_AGENT_ENDED)
    assert out["status"] == chats.ENDED


def test_end_chat_without_a_lead_does_not_notify():
    sb = MagicMock()
    with patch("app.live.chats.get_supabase", return_value=sb), \
         patch("app.live.chats.get_recent_messages", return_value=[]), \
         patch("app.live.chats.notify_lead") as notify:
        chats.end_chat(_chat(status=chats.ACTIVE, lead_id=None), chats.END_AGENT_ENDED)
    notify.assert_not_called()


# ── plumbing ──────────────────────────────────────────────────────────────
def test_start_chat_inserts_a_waiting_row():
    sb = MagicMock()
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock(
        data=[_chat()])
    with patch("app.live.chats.get_supabase", return_value=sb):
        chats.start_chat(SID, "do you ship", "lead-1")
    row = sb.table.return_value.insert.call_args[0][0]
    assert row["status"] == chats.WAITING
    assert row["session_id"] == SID
    assert row["question"] == "do you ship"
    assert row["lead_id"] == "lead-1"


def test_add_agent_message_saves_with_the_agent_role():
    with patch("app.live.chats.save_message") as save:
        chats.add_agent_message(SID, "hello there")
    save.assert_called_once_with(SID, "agent", "hello there")


def test_accept_marks_the_chat_active():
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.neq.return_value \
        .order.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[_chat()])
    with patch("app.live.chats.get_supabase", return_value=sb):
        chats.accept(SID)
    update = sb.table.return_value.update.call_args[0][0]
    assert update["status"] == chats.ACTIVE
    assert "accepted_at" in update


def test_current_chat_returns_none_when_there_is_none():
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.neq.return_value \
        .order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    with patch("app.live.chats.get_supabase", return_value=sb):
        assert chats.current_chat(SID) is None


def test_current_chat_returns_none_on_db_failure():
    sb = MagicMock()
    sb.table.side_effect = RuntimeError("supabase down")
    with patch("app.live.chats.get_supabase", return_value=sb):
        assert chats.current_chat(SID) is None


def test_end_chat_notifies_even_when_the_transcript_write_fails():
    """A failed leads-table write must not swallow the notification.

    Hard rule: there is no exit from a live chat that leaves the visitor
    un-followed-up. The chat is marked ended regardless, so if the transcript
    write takes the notify call down with it the lead is stranded forever.
    """
    sb = MagicMock()
    sb.table.return_value.update.return_value.eq.return_value.execute.side_effect = (
        RuntimeError("supabase down"))
    with patch("app.live.chats.get_supabase", return_value=sb), \
         patch("app.live.chats.get_recent_messages", return_value=[]), \
         patch("app.live.chats.notify_lead") as notify:
        out = chats.end_chat(_chat(status=chats.ACTIVE), chats.END_AGENT_ENDED)
    assert out["status"] == chats.ENDED
    notify.assert_called_once()
    assert notify.call_args[0][0]["id"] == "lead-1"
