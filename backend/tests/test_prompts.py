from app import guardrails
from app.chat import prompts


def test_system_prompt_has_grounding_and_greeting_rules():
    p = prompts.SYSTEM_PROMPT
    assert "only" in p.lower()
    assert "How can we support your sustainability journey?" in p
    assert "never invent" in p.lower()


def test_system_prompt_pins_home_delivery_store_url():
    # The spec's "Store URLs (must not be confused)" section: home-delivery buyers go to the
    # product page (variant picker), never to /checkout/ (empty cart) or the gated
    # location-subscription page. Pin the exact URL so a prompt edit can't silently swap it.
    p = prompts.SYSTEM_PROMPT
    assert "https://generationconscious.co/product/laundry-detergent-sheets/" in p
    assert "/checkout/" not in p
    assert "location-subscription" not in p


def test_system_prompt_pins_privacy_consent_rule():
    # The PRIVACY rule must survive prompt edits: the first request for personal contact
    # details has to carry the exact consent disclosure from guardrails.consent_note().
    p = prompts.SYSTEM_PROMPT
    assert "PRIVACY" in p
    assert guardrails.consent_note() in p


def test_system_prompt_lists_every_lead_intent_and_required_field():
    # The LEAD FLOWS section is generated from REQUIRED_FIELDS/FIELD_LABELS; pin that every
    # intent and every human field label actually lands in the prompt the model sees.
    from app.chat.tools import FIELD_LABELS, REQUIRED_FIELDS
    p = prompts.SYSTEM_PROMPT
    assert "LEAD FLOWS" in p
    for intent, fields in REQUIRED_FIELDS.items():
        assert intent.replace("_", " ") in p
        for f in fields:
            assert FIELD_LABELS[f] in p


def test_build_messages_orders_system_context_history_user():
    msgs = prompts.build_messages("SYS", "CONTEXT", [{"role": "user", "content": "hi"}], "now")
    assert msgs[0]["role"] == "system"
    assert "CONTEXT" in msgs[0]["content"] or any("CONTEXT" in m["content"] for m in msgs)
    assert msgs[-1] == {"role": "user", "content": "now"}
