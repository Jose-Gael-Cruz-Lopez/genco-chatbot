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
    out = memory.get_recent_messages("sess", limit=3)

    # ordered by created_at DESC so limit takes the most recent, not the first ever
    args, kwargs = order_mock.call_args
    assert args[0] == "created_at"
    assert kwargs.get("desc") is True
    # ...and the returned list is reversed back to chronological (oldest -> newest)
    assert [m["content"] for m in out] == ["oldest", "middle", "newest"]
