from unittest.mock import patch, MagicMock
from app.chat import memory


@patch("app.chat.memory.get_supabase")
def test_non_uuid_session_id_mints_fresh_session(sb):
    # chat_sessions.id is a Postgres uuid column; filtering it with "verify-001" makes PostgREST
    # raise 22P02. The function must skip the lookup entirely and mint a fresh session instead.
    sb.return_value.table.return_value.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": "11111111-1111-1111-1111-111111111111"}])
    out = memory.get_or_create_session("verify-001")
    assert out == "11111111-1111-1111-1111-111111111111"
    # the uuid column was never queried with the invalid value
    sb.return_value.table.return_value.select.assert_not_called()


@patch("app.chat.memory.get_supabase")
def test_valid_uuid_existing_session_is_returned(sb):
    sid = "22222222-2222-2222-2222-222222222222"
    sb.return_value.table.return_value.select.return_value.eq.return_value.execute.return_value = (
        MagicMock(data=[{"id": sid}]))
    assert memory.get_or_create_session(sid) == sid
    sb.return_value.table.return_value.insert.assert_not_called()


@patch("app.chat.memory.get_supabase")
def test_valid_uuid_unknown_session_mints_new(sb):
    sb.return_value.table.return_value.select.return_value.eq.return_value.execute.return_value = (
        MagicMock(data=[]))
    sb.return_value.table.return_value.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": "33333333-3333-3333-3333-333333333333"}])
    out = memory.get_or_create_session("22222222-2222-2222-2222-222222222222")
    assert out == "33333333-3333-3333-3333-333333333333"


@patch("app.chat.memory.get_supabase")
def test_get_recent_messages_invalid_uuid_returns_empty(sb):
    # A non-UUID id can never match a session row, and filtering the uuid column with it
    # raises 22P02 — so it must short-circuit to empty history without touching Supabase.
    out = memory.get_recent_messages("verify-001", limit=10)
    assert out == []
    sb.return_value.table.assert_not_called()


@patch("app.chat.memory.get_supabase")
def test_get_recent_messages_returns_most_recent_in_chronological_order(sb):
    # Supabase returns the most-recent-first (descending) page...
    chain = MagicMock()
    sb.return_value.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[{"role": "assistant", "content": "newest"},
              {"role": "user", "content": "middle"},
              {"role": "assistant", "content": "oldest"}]
    )
    # capture the .order(...) call args
    order_mock = sb.return_value.table.return_value.select.return_value.eq.return_value.order
    out = memory.get_recent_messages("44444444-4444-4444-4444-444444444444", limit=3)

    # ordered by created_at DESC so limit takes the most recent, not the first ever
    args, kwargs = order_mock.call_args
    assert args[0] == "created_at"
    assert kwargs.get("desc") is True
    # ...and the returned list is reversed back to chronological (oldest -> newest)
    assert [m["content"] for m in out] == ["oldest", "middle", "newest"]


def _sb_with_select(rows):
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = (
        MagicMock(data=rows))
    return sb


def test_get_flow_state_returns_the_stored_dict():
    state = {"state": "awaiting_feedback", "question": "do you ship"}
    with patch("app.chat.memory.get_supabase",
               return_value=_sb_with_select([{"flow_state": state}])):
        assert memory.get_flow_state("11111111-1111-1111-1111-111111111111") == state


def test_get_flow_state_returns_none_for_idle_session():
    with patch("app.chat.memory.get_supabase",
               return_value=_sb_with_select([{"flow_state": None}])):
        assert memory.get_flow_state("11111111-1111-1111-1111-111111111111") is None


def test_get_flow_state_returns_none_for_unknown_session():
    with patch("app.chat.memory.get_supabase", return_value=_sb_with_select([])):
        assert memory.get_flow_state("11111111-1111-1111-1111-111111111111") is None


def test_get_flow_state_returns_none_for_non_uuid_without_touching_db():
    with patch("app.chat.memory.get_supabase") as sb:
        assert memory.get_flow_state("not-a-uuid") is None
    sb.assert_not_called()


def test_get_flow_state_returns_none_for_corrupt_non_dict_value():
    with patch("app.chat.memory.get_supabase",
               return_value=_sb_with_select([{"flow_state": "garbage"}])):
        assert memory.get_flow_state("11111111-1111-1111-1111-111111111111") is None


def test_set_flow_state_writes_the_state():
    sb = MagicMock()
    with patch("app.chat.memory.get_supabase", return_value=sb):
        memory.set_flow_state("11111111-1111-1111-1111-111111111111", {"state": "lead"})
    sb.table.return_value.update.assert_called_once_with({"flow_state": {"state": "lead"}})


def test_set_flow_state_clears_with_none():
    sb = MagicMock()
    with patch("app.chat.memory.get_supabase", return_value=sb):
        memory.set_flow_state("11111111-1111-1111-1111-111111111111", None)
    sb.table.return_value.update.assert_called_once_with({"flow_state": None})


def test_set_flow_state_ignores_non_uuid():
    with patch("app.chat.memory.get_supabase") as sb:
        memory.set_flow_state("not-a-uuid", {"state": "lead"})
    sb.assert_not_called()
