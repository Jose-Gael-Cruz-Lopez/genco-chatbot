from unittest.mock import patch, MagicMock

import pytest

from app.rag import embeddings


def test_embed_text_returns_1536_vector():
    fake = MagicMock()
    fake.data = [MagicMock(embedding=[0.1] * 1536)]
    with patch.object(embeddings, "_client") as c:
        c.embeddings.create.return_value = fake
        vec = embeddings.embed_text("hello")
    assert len(vec) == 1536
    c.embeddings.create.assert_called_once()


def test_embedding_dim_constant_matches_schema():
    assert embeddings.EMBEDDING_DIM == 1536


def test_embed_batch_rejects_wrong_dimension():
    """The kb_documents schema is fixed at vector(1536); a model returning any
    other size must fail immediately with a named error, not a confusing
