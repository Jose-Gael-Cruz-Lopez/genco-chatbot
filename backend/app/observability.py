"""LangFuse tracing (langfuse 2.x) for the chat pipeline.

Every chat turn emits one trace spanning retrieve -> generate -> respond:
a `retrieve` span around retrieval, a `generate` generation observation
carrying model + usage (so the LangFuse cost dashboard picks it up), and a
`respond` event with the final reply. Escalated turns are tagged `escalation`.
from contextlib import contextmanager
from app.config import get_settings

_settings = get_settings()
_langfuse = None


def init_langfuse():
    global _langfuse
    if _langfuse is None and _settings.LANGFUSE_SECRET_KEY:
        from langfuse import Langfuse
        _langfuse = Langfuse(
            public_key=_settings.LANGFUSE_PUBLIC_KEY,
            secret_key=_settings.LANGFUSE_SECRET_KEY,
            host=_settings.LANGFUSE_HOST,
        )
    return _langfuse


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
