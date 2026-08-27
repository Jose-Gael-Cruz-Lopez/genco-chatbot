import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.rag import ingest


def test_chunk_markdown_splits_and_tags_title():
    md = "# Buying Sheets\n\nGo to the product page.\n\n## Wholesale\n\nEmail us."
    chunks = ingest.chunk_markdown(md, source="products.md")
    assert len(chunks) >= 1
    assert all(c["metadata"]["source"] == "products.md" for c in chunks)
    assert any("Buying Sheets" in c["metadata"]["title"] for c in chunks)
    assert all(c["content"].strip() for c in chunks)


def _fake_vectors(texts: list[str]) -> list[list[float]]:
    return [[0.1] * 1536 for _ in texts]


def _write_kb(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return tmp_path


def _call_names(sb: MagicMock) -> list[str]:
    return [str(c) for c in sb.mock_calls]


def test_ingest_upserts_before_deleting_stale(tmp_path, monkeypatch):
    """The new KB must be upserted before old rows are removed — no empty-KB window."""
    monkeypatch.setattr(ingest, "KB_DIR", _write_kb(tmp_path, {
        "a.md": "# Sheets\n\nBuy sheets on the product page.",
    }))
    sb = MagicMock()
    with patch.object(ingest, "get_supabase", return_value=sb), \
         patch.object(ingest, "embed_batch", side_effect=_fake_vectors):
        count = ingest.ingest_all()
    assert count == 1
    calls = _call_names(sb)
    upsert_idx = next(i for i, c in enumerate(calls) if "upsert" in c)
    delete_idx = next(i for i, c in enumerate(calls) if "delete" in c)
    assert upsert_idx < delete_idx


def test_ingest_deletes_only_rows_outside_new_hash_set(tmp_path, monkeypatch):
    content = "# Sheets\n\nBuy sheets on the product page."
    monkeypatch.setattr(ingest, "KB_DIR", _write_kb(tmp_path, {"a.md": content}))
    expected_hash = hashlib.sha256(content.strip().encode()).hexdigest()
    sb = MagicMock()
    with patch.object(ingest, "get_supabase", return_value=sb), \
         patch.object(ingest, "embed_batch", side_effect=_fake_vectors):
        ingest.ingest_all()
    sb.table.return_value.delete.return_value.not_.in_.assert_called_once_with(
        "content_hash", [expected_hash]
    )


def test_ingest_failed_embed_leaves_db_untouched(tmp_path, monkeypatch):
    """A failed embed must leave the old KB serving: no delete, no upsert."""
    # Only generative mode embeds at all; FAQ mode has no embed step to fail.
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("BOT_MODE", "generative")
    monkeypatch.setattr(ingest, "KB_DIR", _write_kb(tmp_path, {
        "a.md": "# Sheets\n\nBuy sheets on the product page.",
    }))
    sb = MagicMock()
    with patch.object(ingest, "get_supabase", return_value=sb), \
         patch.object(ingest, "embed_batch", side_effect=RuntimeError("quota")):
        with pytest.raises(RuntimeError):
            ingest.ingest_all()
    calls = _call_names(sb)
    assert not any("delete" in c for c in calls)
    assert not any("upsert" in c for c in calls)
    get_settings.cache_clear()


def test_ingest_dedupes_rows_by_content_hash(tmp_path, monkeypatch):
    """Identical chunks in one batch must collapse to a single row, or the
    single upsert statement fails with Postgres 'ON CONFLICT DO UPDATE
    command cannot affect row a second time'."""
    same = "# Contact\n\nEmail Info@GenerationConscious.co."
    monkeypatch.setattr(ingest, "KB_DIR", _write_kb(tmp_path, {
        "a.md": same,
        "b.md": same,
    }))
    sb = MagicMock()
    with patch.object(ingest, "get_supabase", return_value=sb), \
         patch.object(ingest, "embed_batch", side_effect=_fake_vectors):
        count = ingest.ingest_all()
    assert count == 1
    rows = sb.table.return_value.upsert.call_args.args[0]
    hashes = [r["content_hash"] for r in rows]
    assert len(hashes) == len(set(hashes)) == 1


def test_ingest_empty_kb_returns_zero_without_touching_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "KB_DIR", tmp_path)
    sb = MagicMock()
    with patch.object(ingest, "get_supabase", return_value=sb), \
         patch.object(ingest, "embed_batch", side_effect=_fake_vectors):
        assert ingest.ingest_all() == 0
    assert not sb.mock_calls


def test_faq_mode_ingest_makes_no_embedding_calls(monkeypatch, tmp_path):
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("BOT_MODE", "faq")
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "a.md").write_text("# Shipping\nWe ship via USPS.")
    monkeypatch.setattr("app.rag.ingest.KB_DIR", kb)
    sb = MagicMock()
    with patch("app.rag.ingest.get_supabase", return_value=sb), \
         patch("app.rag.ingest.embed_batch") as embed:
        count = ingest.ingest_all()
    embed.assert_not_called()
    assert count == 1
    rows = sb.table.return_value.upsert.call_args[0][0]
    assert all(r["embedding"] is None for r in rows)
    get_settings.cache_clear()


def test_generative_mode_ingest_still_embeds(monkeypatch, tmp_path):
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("BOT_MODE", "generative")
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "a.md").write_text("# Shipping\nWe ship via USPS.")
    monkeypatch.setattr("app.rag.ingest.KB_DIR", kb)
    with patch("app.rag.ingest.get_supabase"), \
         patch("app.rag.ingest.embed_batch", return_value=[[0.1] * 1536]) as embed:
        ingest.ingest_all()
    embed.assert_called_once()
    get_settings.cache_clear()
