from app.chat import tools


def test_wholesale_requires_all_fields():
    errs = tools.validate_lead("wholesale",
        {"name": "A", "email": "a@b.com", "phone": "1", "organization": "Org"})
    assert any("estimated_sheets" in e for e in errs)


def test_invalid_email_rejected():
    errs = tools.validate_lead("question",
        {"name": "A", "email": "not-an-email", "question": "hi"})
    assert any("email" in e.lower() for e in errs)


def test_valid_refill_station_passes():
    errs = tools.validate_lead("refill_station", {
        "name": "A", "email": "a@b.com", "phone": "1", "organization": "Org",
        "num_laundry_rooms": 3, "num_students": 200})
    assert errs == []


# --- #22: the tool schema itself must steer the model to collect every per-intent field ---

def test_tool_schema_encodes_per_intent_required_fields():
    params = tools.CAPTURE_LEAD_TOOL["function"]["parameters"]
    branches = {b["if"]["properties"]["intent"]["const"]: set(b["then"]["required"])
                for b in params["allOf"]}
    for intent, fields in tools.REQUIRED_FIELDS.items():
        assert branches[intent] == set(fields), intent


def test_tool_description_lists_per_intent_fields():
    desc = tools.CAPTURE_LEAD_TOOL["function"]["description"]
    for intent, fields in tools.REQUIRED_FIELDS.items():
        assert intent in desc
        for f in fields:
            assert f in desc


# --- #22: validation errors surfaced to users must not leak internal field names ---

