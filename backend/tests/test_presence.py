from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from app.live import presence


def _sb(row):
    sb = MagicMock()
    (sb.table.return_value.select.return_value.eq.return_value
     .execute.return_value) = MagicMock(data=[row] if row else [])
    return sb


NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC)


def _row(available: bool, seconds_ago: int) -> dict:
    return {"available": available,
            "last_seen_at": (NOW - timedelta(seconds=seconds_ago)).isoformat()}


def test_available_with_a_fresh_heartbeat():
    with patch("app.live.presence.get_supabase", return_value=_sb(_row(True, 5))):
        assert presence.is_agent_available(now=NOW) is True


def test_unavailable_when_the_heartbeat_is_stale():
    with patch("app.live.presence.get_supabase", return_value=_sb(_row(True, 90))):
        assert presence.is_agent_available(now=NOW) is False


def test_boundary_just_inside_the_ttl_is_available():
    with patch("app.live.presence.get_supabase", return_value=_sb(_row(True, 44))):
        assert presence.is_agent_available(now=NOW) is True


def test_boundary_just_outside_the_ttl_is_unavailable():
    with patch("app.live.presence.get_supabase", return_value=_sb(_row(True, 46))):
        assert presence.is_agent_available(now=NOW) is False


def test_unavailable_when_toggled_off_even_with_a_fresh_heartbeat():
    with patch("app.live.presence.get_supabase", return_value=_sb(_row(False, 1))):
        assert presence.is_agent_available(now=NOW) is False


def test_unavailable_when_there_is_no_presence_row():
    with patch("app.live.presence.get_supabase", return_value=_sb(None)):
        assert presence.is_agent_available(now=NOW) is False


def test_fails_closed_on_a_database_error():
    sb = MagicMock()
    sb.table.side_effect = RuntimeError("supabase down")
    with patch("app.live.presence.get_supabase", return_value=sb):
        assert presence.is_agent_available(now=NOW) is False


def test_fails_closed_on_an_unparseable_timestamp():
    row = {"available": True, "last_seen_at": "not-a-timestamp"}
    with patch("app.live.presence.get_supabase", return_value=_sb(row)):
        assert presence.is_agent_available(now=NOW) is False


def test_heartbeat_upserts_the_single_row():
    sb = MagicMock()
    with patch("app.live.presence.get_supabase", return_value=sb):
        presence.heartbeat(True)
    row = sb.table.return_value.upsert.call_args[0][0]
    assert row["id"] == presence.AGENT_ID
    assert row["available"] is True
    assert "last_seen_at" in row


def test_heartbeat_never_raises():
    sb = MagicMock()
    sb.table.side_effect = RuntimeError("supabase down")
    with patch("app.live.presence.get_supabase", return_value=sb):
        presence.heartbeat(False)  # must not raise
