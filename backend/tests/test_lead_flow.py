from unittest.mock import patch, MagicMock, Mock, call
from app import escalation


def _row(**kw):
    base = {"id": "lead-1", "intent": "wholesale", "name": "A",
            "email": "a@b.com", "phone": "1", "organization": "Org",
            "extra": {"estimated_sheets": 500}, "emailed": False,
            "pushed_to_pipedrive": False}
    base.update(kw); return base


@patch("app.escalation.create_lead_in_pipedrive", return_value=True)
@patch("app.escalation.send_lead_notification", return_value=True)
@patch("app.escalation.get_supabase")
def test_capture_stores_before_notifying(sb, email, pipe):
    table = MagicMock()
    table.insert.return_value.execute.return_value = MagicMock(data=[_row()])
    sb.return_value.table.return_value = table

    # Attach mocks to parent BEFORE the call so all calls are recorded in order.
    parent = Mock()
    parent.attach_mock(sb, "sb")
    parent.attach_mock(email, "email")
    parent.attach_mock(pipe, "pipe")

    lead = escalation.capture_lead("sess", "wholesale",
        {"name": "A", "email": "a@b.com", "phone": "1",
         "organization": "Org", "estimated_sheets": 500})
    assert lead["id"] == "lead-1"
    table.insert.assert_called_once()       # stored first
    email.assert_called_once()
    pipe.assert_called_once()

    # Assert ORDER: the insert call must precede both notification calls.
    # parent.mock_calls names are strings like "sb().table().insert" / "email" / "pipe".
    names = [c[0] for c in parent.mock_calls]
    insert_idx = next(i for i, n in enumerate(names) if "insert" in n)
    email_idx  = next(i for i, n in enumerate(names) if n == "email")
    pipe_idx   = next(i for i, n in enumerate(names) if n == "pipe")
    assert insert_idx < email_idx, "Supabase insert must happen before email notification"
    assert insert_idx < pipe_idx,  "Supabase insert must happen before pipedrive notification"


@patch("app.escalation.create_lead_in_pipedrive", side_effect=Exception("down"))
@patch("app.escalation.send_lead_notification", side_effect=Exception("down"))
@patch("app.escalation.get_supabase")
def test_notify_failures_do_not_raise(sb, email, pipe):
    table = MagicMock()
    table.insert.return_value.execute.return_value = MagicMock(data=[_row()])
    sb.return_value.table.return_value = table
    lead = escalation.capture_lead("sess", "wholesale",
        {"name": "A", "email": "a@b.com", "phone": "1",
         "organization": "Org", "estimated_sheets": 500})
    assert lead["id"] == "lead-1"   # lead persisted despite both notifications failing
