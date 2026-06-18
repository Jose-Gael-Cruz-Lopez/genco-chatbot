from unittest.mock import patch, MagicMock
from app.chat import memory


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
