from app.chat import prompts


def test_system_prompt_has_grounding_and_greeting_rules():
    p = prompts.SYSTEM_PROMPT
    assert "only" in p.lower()
    assert "How can we support your sustainability journey?" in p
    assert "never invent" in p.lower()


def test_build_messages_orders_system_context_history_user():
    msgs = prompts.build_messages("SYS", "CONTEXT", [{"role": "user", "content": "hi"}], "now")
    assert msgs[0]["role"] == "system"
    assert "CONTEXT" in msgs[0]["content"] or any("CONTEXT" in m["content"] for m in msgs)
    assert msgs[-1] == {"role": "user", "content": "now"}
