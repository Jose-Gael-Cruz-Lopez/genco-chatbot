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
        name="generate", input={"messages": [{"role": "user", "content": "hi"}]})
    trace.generation.return_value.end.assert_called_once_with(
        model="anthropic/claude-3.5-sonnet",
        usage={"input": 10, "output": 5, "total": 15, "unit": "TOKENS"},
        output="hello",
    )


def test_generation_ends_even_when_wrapped_call_raises():
    # The observation must be closed (latency recorded) and the trace flushed
    # even when the LLM call inside it blows up.
    lf = MagicMock()
    trace = lf.trace.return_value
    with patch("app.observability.init_langfuse", return_value=lf):
        with pytest.raises(RuntimeError):
            with observability.trace_turn("chat") as span:
                with span.generation("generate"):
                    raise RuntimeError("openrouter down")
    trace.generation.return_value.end.assert_called_once_with()
    lf.flush.assert_called_once()


def test_tag_sets_trace_tags():
    lf = MagicMock()
    trace = lf.trace.return_value
    with patch("app.observability.init_langfuse", return_value=lf):
        with observability.trace_turn("chat") as span:
            span.tag("escalation")
    trace.update.assert_called_with(tags=["escalation"])


def test_event_emitted_on_trace():
    lf = MagicMock()
    trace = lf.trace.return_value
    with patch("app.observability.init_langfuse", return_value=lf):
        with observability.trace_turn("chat") as span:
            span.event("respond", output="bye")
    trace.event.assert_called_once_with(name="respond", output="bye")


def test_update_merges_metadata_onto_trace():
    lf = MagicMock()
    trace = lf.trace.return_value
    with patch("app.observability.init_langfuse", return_value=lf):
        with observability.trace_turn("chat", message="hi") as span:
            span.update(reply="ok", model="m")
    trace.update.assert_called_once_with(
        metadata={"message": "hi", "reply": "ok", "model": "m"})


def test_usage_mapping_openrouter_to_langfuse():
    # OpenRouter/OpenAI-style token counts map to the langfuse 2.x usage shape
    # so the cost dashboard picks them up; empty/absent usage maps to None.
    assert observability._to_langfuse_usage(
        {"prompt_tokens": 10, "completion_tokens": 5}) == {
        "input": 10, "output": 5, "total": 15, "unit": "TOKENS"}
    assert observability._to_langfuse_usage(
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 20}) == {
        "input": 10, "output": 5, "total": 20, "unit": "TOKENS"}
    assert observability._to_langfuse_usage({}) is None
    assert observability._to_langfuse_usage(None) is None
