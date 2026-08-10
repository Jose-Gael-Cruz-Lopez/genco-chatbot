from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from app.config import get_settings

_settings = get_settings()
_client = OpenAI(api_key=_settings.EMBEDDING_API_KEY)

# Must match `embedding vector(1536)` in app/rag/schema.sql. Switching embedding
# models requires a schema migration + full re-ingest (see README).
EMBEDDING_DIM = 1536


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def _request_embeddings(texts: list[str]) -> list[list[float]]:
    resp = _client.embeddings.create(model=_settings.EMBEDDING_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def embed_batch(texts: list[str]) -> list[list[float]]:
    # Validate outside the retry: a dimension mismatch is deterministic, so it
    # should fail immediately with a named error instead of burning retries and
    # surfacing later as a confusing Postgres/PostgREST error.
    vectors = _request_embeddings(texts)
    for vector in vectors:
        if len(vector) != EMBEDDING_DIM:
            raise ValueError(
def embed_text(text: str) -> list[float]:
    return embed_batch([text])[0]
