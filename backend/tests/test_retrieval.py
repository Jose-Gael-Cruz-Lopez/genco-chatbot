import os
import pytest
from app.rag import ingest, retrieve

requires_keys = pytest.mark.skipif(
    not os.getenv("SUPABASE_URL") or not os.getenv("EMBEDDING_API_KEY"),
    reason="needs live Supabase + embedding keys",
)


@requires_keys
def test_ingest_then_query():
    assert ingest.ingest_all() > 0
    for q in ["how do I buy sheets",
              "can you bring refill stations to my building",
              "I want to buy wholesale"]:
        hits = retrieve.retrieve(q, k=3)
        assert hits and hits[0]["similarity"] > 0.2
        print(q, "->", hits[0]["metadata"]["title"], round(hits[0]["similarity"], 3))
