import respx, httpx
from app import llm


@respx.mock
def test_chat_completion_parses_content_and_usage():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "choices": [{"message": {"content": "hello", "tool_calls": None}}],
            "model": "test/model",
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }))
    out = llm.chat_completion([{"role": "user", "content": "hi"}])
    assert out["content"] == "hello"
    assert out["model"] == "test/model"
    assert out["usage"]["completion_tokens"] == 2
