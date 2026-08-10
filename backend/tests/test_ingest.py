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
