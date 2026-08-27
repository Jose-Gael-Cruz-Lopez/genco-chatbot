from unittest.mock import MagicMock, patch

from app.rag import fts


def _mock_sb(rows):
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value = MagicMock(data=rows)
    return sb


def test_retrieve_fts_calls_the_fts_rpc_and_returns_rows():
    rows = [{"id": "1", "content": "We ship via USPS.", "metadata": {}, "similarity": 0.4}]
    with patch("app.rag.fts.get_supabase", return_value=_mock_sb(rows)) as sb:
        out = fts.retrieve_fts("do you ship", k=3)
    assert out == rows
    sb.return_value.rpc.assert_called_once_with(
        "match_documents_fts", {"query_text": "do you ship", "match_count": 3})


def test_retrieve_fts_returns_empty_list_when_no_rows():
    with patch("app.rag.fts.get_supabase", return_value=_mock_sb(None)):
        assert fts.retrieve_fts("zzz") == []


def test_best_match_returns_top_hit_above_threshold():
    rows = [{"content": "strong", "metadata": {}, "similarity": 0.9},
            {"content": "weak", "metadata": {}, "similarity": 0.01}]
    with patch("app.rag.fts.get_supabase", return_value=_mock_sb(rows)):
        hit, scores = fts.best_match("q")
    assert hit["content"] == "strong"
    assert scores == [0.9, 0.01]


def test_best_match_returns_none_below_threshold():
    rows = [{"content": "weak", "metadata": {}, "similarity": 0.001}]
    with patch("app.rag.fts.get_supabase", return_value=_mock_sb(rows)):
        hit, scores = fts.best_match("q")
    assert hit is None
    assert scores == [0.001]


def test_best_match_returns_none_on_no_rows():
    with patch("app.rag.fts.get_supabase", return_value=_mock_sb([])):
        hit, scores = fts.best_match("q")
    assert hit is None
    assert scores == []


def test_best_match_answers_are_verbatim():
    content = "Shipping is calculated at checkout using live USPS rates."
    rows = [{"content": content, "metadata": {}, "similarity": 0.5}]
    with patch("app.rag.fts.get_supabase", return_value=_mock_sb(rows)):
        hit, _ = fts.best_match("shipping")
    assert hit["content"] == content


def test_fts_module_makes_no_ai_calls():
    src = (__import__("pathlib").Path(fts.__file__)).read_text()
    for banned in ("embeddings", "llm", "openrouter", "openai"):
        assert banned not in src.lower()
