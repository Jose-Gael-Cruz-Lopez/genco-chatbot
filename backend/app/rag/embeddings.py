from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from app.config import get_settings

_settings = get_settings()
_client = OpenAI(api_key=_settings.EMBEDDING_API_KEY)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def embed_batch(texts: list[str]) -> list[list[float]]:
    resp = _client.embeddings.create(model=_settings.EMBEDDING_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def embed_text(text: str) -> list[float]:
    return embed_batch([text])[0]
