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
    sb = get_supabase()
    sb.table("kb_documents").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
    all_chunks: list[dict] = []
    for md_file in sorted(KB_DIR.glob("*.md")):
        all_chunks.extend(chunk_markdown(md_file.read_text(), md_file.name))
    if not all_chunks:
        return 0
    vectors = embed_batch([c["content"] for c in all_chunks])
    rows = [{
        "content": c["content"],
        "content_hash": hashlib.sha256(c["content"].encode()).hexdigest(),
        "embedding": v,
        "metadata": c["metadata"],
    } for c, v in zip(all_chunks, vectors)]
    sb.table("kb_documents").upsert(rows, on_conflict="content_hash").execute()
    return len(rows)


if __name__ == "__main__":
    print(f"Ingested {ingest_all()} chunks.")
