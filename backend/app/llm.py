import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from app.config import get_settings

_settings = get_settings()
_URL = "https://openrouter.ai/api/v1/chat/completions"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def chat_completion(messages: list[dict], tools: list[dict] | None = None,
                    use_fallback: bool = False) -> dict:
    model = _settings.OPENROUTER_MODEL_FALLBACK if use_fallback else _settings.OPENROUTER_MODEL
    payload: dict = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {_settings.OPENROUTER_API_KEY}"}
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    msg = data["choices"][0]["message"]
    return {
        "content": msg.get("content"),
        "tool_calls": msg.get("tool_calls"),
        "model": data.get("model", model),
        "usage": data.get("usage", {}),
    }
