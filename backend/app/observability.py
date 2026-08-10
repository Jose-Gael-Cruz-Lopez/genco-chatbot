"""LangFuse tracing (langfuse 2.x) for the chat pipeline.

Every chat turn emits one trace spanning retrieve -> generate -> respond:
a `retrieve` span around retrieval, a `generate` generation observation
carrying model + usage (so the LangFuse cost dashboard picks it up), and a
`respond` event with the final reply. Escalated turns are tagged `escalation`.

Everything degrades to a silent no-op when LangFuse keys are absent —
observability must never break a chat turn.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.config import get_settings

_settings = get_settings()
_langfuse = None


def init_langfuse() -> Any:
    """Return the shared Langfuse client, or None when keys are absent."""
    global _langfuse
    if _langfuse is None and _settings.LANGFUSE_SECRET_KEY:
        from langfuse import Langfuse
        _langfuse = Langfuse(
            public_key=_settings.LANGFUSE_PUBLIC_KEY,
            secret_key=_settings.LANGFUSE_SECRET_KEY,
            host=_settings.LANGFUSE_HOST,
        )
    return _langfuse


def _to_langfuse_usage(usage: dict | None) -> dict | None:
    """Map OpenRouter/OpenAI-style token counts to the langfuse 2.x usage
    shape ({input, output, total, unit}) so cost tracking works."""
    if not usage:
        return None
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    return {
        "input": prompt,
        "output": completion,
        "total": usage.get("total_tokens", prompt + completion),
        "unit": "TOKENS",
    }


class _Observation:
    """Handle yielded inside a span/generation context: collects the kwargs to
    pass to the observation's end() call. No-op-safe (works with no trace)."""

    def __init__(self) -> None:
        self.end_kwargs: dict[str, Any] = {}

    def set_output(self, output: Any) -> None:
        self.end_kwargs["output"] = output


class _Generation(_Observation):
    def set_result(self, *, model: str | None = None, usage: dict | None = None,
                   output: Any = None) -> None:
        if model is not None:
            self.end_kwargs["model"] = model
        lf_usage = _to_langfuse_usage(usage)
        if lf_usage is not None:
            self.end_kwargs["usage"] = lf_usage
        if output is not None:
            self.end_kwargs["output"] = output


class TurnTrace:
    """Handle yielded by trace_turn. Every method is a silent no-op when
    LangFuse keys are absent (self._trace is None)."""
@contextmanager
def trace_turn(name: str, **metadata):
    lf = init_langfuse()
    trace = lf.trace(name=name, metadata=metadata) if lf else None

    class _Span:
        def update(self, **kw):
            if trace:
                trace.update(metadata={**metadata, **kw})

    span = _Span()
    try:
        yield span
    finally:
        if lf:
            lf.flush()
