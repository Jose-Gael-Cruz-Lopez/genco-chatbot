import hashlib
import re
from pathlib import Path
from app.db import get_supabase
from app.rag.embeddings import embed_batch

KB_DIR = Path(__file__).resolve().parent.parent.parent / "knowledge_base"
MAX_CHARS = 3200   # ~800 tokens
OVERLAP_CHARS = 400  # ~100 tokens


def chunk_markdown(text: str, source: str) -> list[dict]:
    blocks = re.split(r"\n(?=#{1,6}\s)", text.strip())
    chunks: list[dict] = []
    current_title = source
    for block in blocks:
        heading = re.match(r"#{1,6}\s+(.*)", block)
        if heading:
            current_title = heading.group(1).strip()
        for piece in _window(block):
            if piece.strip():
                chunks.append({
                    "content": piece.strip(),
                    "metadata": {"source": source, "title": current_title},
                })
    return chunks


def _window(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    out, start = [], 0
    while start < len(text):
        out.append(text[start:start + MAX_CHARS])
        start += MAX_CHARS - OVERLAP_CHARS
    return out


def ingest_all() -> int:
    """Non-destructive re-ingest: embed first, upsert, then drop stale rows.

    The database is never touched before embedding succeeds, so a failed embed
    (network/key/quota) leaves the old KB serving, and the upsert-before-delete
    ordering means live queries never see an empty-KB window.
    """
    all_chunks: list[dict] = []
    for md_file in sorted(KB_DIR.glob("*.md")):
        all_chunks.extend(chunk_markdown(md_file.read_text(), md_file.name))
    if not all_chunks:
        # An empty knowledge_base/ is almost certainly an operator error;
        # refuse to wipe the production KB over it.
        return 0
    # Embed BEFORE any database write — if this raises, the old KB keeps serving.
    vectors = embed_batch([c["content"] for c in all_chunks])
    # Dedupe by content_hash: duplicate hashes in one upsert statement fail with
    # Postgres "ON CONFLICT DO UPDATE command cannot affect row a second time".
    rows_by_hash: dict[str, dict] = {}
    for chunk, vector in zip(all_chunks, vectors):
        content_hash = hashlib.sha256(chunk["content"].encode()).hexdigest()
        rows_by_hash.setdefault(content_hash, {
            "content": chunk["content"],
            "content_hash": content_hash,
            "embedding": vector,
            "metadata": chunk["metadata"],
        })
    rows = list(rows_by_hash.values())
    sb = get_supabase()
    sb.table("kb_documents").upsert(rows, on_conflict="content_hash").execute()
    # Only now remove rows that are no longer part of the KB.
    sb.table("kb_documents").delete().not_.in_(
        "content_hash", list(rows_by_hash)
    ).execute()
    return len(rows)


if __name__ == "__main__":
    print(f"Ingested {ingest_all()} chunks.")
