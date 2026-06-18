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
