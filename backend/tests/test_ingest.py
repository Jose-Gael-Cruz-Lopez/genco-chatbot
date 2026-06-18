from app.rag import ingest


def test_chunk_markdown_splits_and_tags_title():
    md = "# Buying Sheets\n\nGo to the product page.\n\n## Wholesale\n\nEmail us."
    chunks = ingest.chunk_markdown(md, source="products.md")
    assert len(chunks) >= 1
    assert all(c["metadata"]["source"] == "products.md" for c in chunks)
    assert any("Buying Sheets" in c["metadata"]["title"] for c in chunks)
    assert all(c["content"].strip() for c in chunks)
