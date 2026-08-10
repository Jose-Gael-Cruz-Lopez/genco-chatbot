"""Tests for the LangFuse trace structure (#4).

Every chat turn must emit a trace spanning retrieve -> generate -> respond:
a `retrieve` span, a `generate` generation observation carrying model + usage
(so LangFuse cost tracking works), and a `respond` event. Escalated turns are
tagged `escalation`. All of it must degrade to a silent no-op when LangFuse
keys are absent — observability must never break a chat turn.
"""

from unittest.mock import MagicMock, patch

import pytest

from app import observability


def test_trace_turn_is_noop_without_langfuse_keys():
    # With no LangFuse client, every operation must silently succeed.
    with patch("app.observability.init_langfuse", return_value=None):
        with observability.trace_turn("chat", message="hi") as span:
            with span.span("retrieve", query="hi") as retrieval:
                retrieval.set_output({"scores": [0.5]})
            with span.generation("generate", messages=[]) as gen:
                gen.set_result(model="m", usage={"prompt_tokens": 1}, output="x")
            span.update(reply="x")
            span.tag("escalation")
            span.event("respond", output="x")


def test_trace_turn_creates_trace_with_metadata_and_flushes():
    lf = MagicMock()
    with patch("app.observability.init_langfuse", return_value=lf):
        with observability.trace_turn("chat", message="hi"):
            pass
    lf.trace.assert_called_once_with(name="chat", metadata={"message": "hi"})
    lf.flush.assert_called_once()


def test_span_opens_and_ends_with_output():
    lf = MagicMock()
    trace = lf.trace.return_value
    with patch("app.observability.init_langfuse", return_value=lf):
        with observability.trace_turn("chat") as span:
            with span.span("retrieve", query="q") as retrieval:
                retrieval.set_output({"scores": [0.9]})
    trace.span.assert_called_once_with(name="retrieve", input={"query": "q"})
    trace.span.return_value.end.assert_called_once_with(output={"scores": [0.9]})


def test_generation_records_model_usage_and_output():
    lf = MagicMock()
    trace = lf.trace.return_value
    with patch("app.observability.init_langfuse", return_value=lf):
        with observability.trace_turn("chat") as span:
            with span.generation("generate", messages=[{"role": "user", "content": "hi"}]) as gen:
                gen.set_result(
                    model="anthropic/claude-3.5-sonnet",
                    usage={"prompt_tokens": 10, "completion_tokens": 5},
                    output="hello",
                )
    trace.generation.assert_called_once_with(
