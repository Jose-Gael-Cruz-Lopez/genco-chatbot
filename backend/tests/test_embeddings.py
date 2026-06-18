from unittest.mock import patch, MagicMock
from app.rag import embeddings


def test_embed_text_returns_1536_vector():
    fake = MagicMock()
    fake.data = [MagicMock(embedding=[0.1] * 1536)]
    with patch.object(embeddings, "_client") as c:
        c.embeddings.create.return_value = fake
        vec = embeddings.embed_text("hello")
    assert len(vec) == 1536
    c.embeddings.create.assert_called_once()
