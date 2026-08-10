from unittest.mock import patch, MagicMock, Mock, call

import pytest

from app import escalation
from app.pipedrive import create_lead_in_pipedrive


def _row(**kw):
    base = {"id": "lead-1", "intent": "wholesale", "name": "A",
            "email": "a@b.com", "phone": "1", "organization": "Org",
            "extra": {"estimated_sheets": 500}, "emailed": False,
            "pushed_to_pipedrive": False}
    base.update(kw); return base


def _valid_wholesale_fields() -> dict:
    return {"name": "A", "email": "a@b.com", "phone": "1",
            "organization": "Org", "estimated_sheets": 500}


def _supabase_table(sb) -> MagicMock:
    table = MagicMock()
    table.insert.return_value.execute.return_value = MagicMock(data=[_row()])
    sb.return_value.table.return_value = table
    return table


@patch("app.escalation.create_lead_in_pipedrive", return_value=True)
@patch("app.escalation.send_lead_notification", return_value=True)
@patch("app.escalation.get_supabase")
def test_capture_stores_before_notifying(sb, email, pipe):
    table = _supabase_table(sb)

    # Attach mocks to parent BEFORE the call so all calls are recorded in order.
    parent = Mock()
    parent.attach_mock(sb, "sb")
    parent.attach_mock(email, "email")
    parent.attach_mock(pipe, "pipe")

    lead = escalation.capture_lead("sess", "wholesale", _valid_wholesale_fields())
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
    table = _supabase_table(sb)
    lead = escalation.capture_lead("sess", "wholesale", _valid_wholesale_fields())
    assert lead["id"] == "lead-1"   # lead persisted despite both notifications failing
    # Both notifications failed, so neither flag may flip — the row must stay
    # emailed=false / pushed_to_pipedrive=false so the operator can retry from Supabase.
    table.update.assert_not_called()


# --- #16: real-code-path validation — invalid leads must raise BEFORE any Supabase call ---

@patch("app.escalation.create_lead_in_pipedrive")
@patch("app.escalation.send_lead_notification")
@patch("app.escalation.get_supabase")
def test_missing_required_field_raises_without_storing(sb, email, pipe):
    fields = _valid_wholesale_fields()
    del fields["estimated_sheets"]
    with pytest.raises(ValueError):
        escalation.capture_lead("sess", "wholesale", fields)
    sb.assert_not_called()      # no Supabase insert (client never even fetched)
    email.assert_not_called()
    pipe.assert_not_called()


@patch("app.escalation.create_lead_in_pipedrive")
@patch("app.escalation.send_lead_notification")
@patch("app.escalation.get_supabase")
def test_invalid_email_raises_without_storing(sb, email, pipe):
    with pytest.raises(ValueError):
        escalation.capture_lead(
            "sess", "wholesale", _valid_wholesale_fields() | {"email": "not-an-email"})
    sb.assert_not_called()
    email.assert_not_called()
    pipe.assert_not_called()


@patch("app.escalation.create_lead_in_pipedrive")
@patch("app.escalation.send_lead_notification")
@patch("app.escalation.get_supabase")
def test_validation_error_reprompt_is_humanized(sb, email, pipe):
    # The ValueError message is surfaced verbatim to the end user by the router's
    # re-prompt, so internal snake_case field names must not leak into it.
    with pytest.raises(ValueError) as exc:
        escalation.capture_lead("sess", "refill_station",
            {"name": "A", "email": "a@b.com", "phone": "1", "organization": "Org"})
    msg = str(exc.value)
    assert "num_laundry_rooms" not in msg
    assert "num_students" not in msg
    assert "laundry rooms" in msg
    assert "students" in msg


# --- #16: flag semantics — emailed / pushed_to_pipedrive must mirror notify outcomes ---

@patch("app.escalation.create_lead_in_pipedrive", return_value=True)
@patch("app.escalation.send_lead_notification", return_value=True)
@patch("app.escalation.get_supabase")
def test_notify_success_updates_both_flags(sb, email, pipe):
    table = _supabase_table(sb)
    escalation.capture_lead("sess", "wholesale", _valid_wholesale_fields())
    assert call({"emailed": True}) in table.update.call_args_list
    assert call({"pushed_to_pipedrive": True}) in table.update.call_args_list
    # Every flag update targets the stored row.
    assert table.update.return_value.eq.call_args_list == [call("id", "lead-1")] * 2


@patch("app.escalation.create_lead_in_pipedrive", side_effect=Exception("pipedrive down"))
@patch("app.escalation.send_lead_notification", return_value=True)
@patch("app.escalation.get_supabase")
def test_email_ok_pipedrive_down_updates_only_emailed(sb, email, pipe):
    table = _supabase_table(sb)
    lead = escalation.capture_lead("sess", "wholesale", _valid_wholesale_fields())
    assert lead["id"] == "lead-1"   # partial notify failure still never raises
    assert table.update.call_args_list == [call({"emailed": True})]


# --- #22: pushed_to_pipedrive means person+deal exist; the note is best-effort ---

def _pd_response(json_data: dict | None = None,
                 raise_exc: Exception | None = None) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = json_data or {}
    if raise_exc is not None:
        resp.raise_for_status.side_effect = raise_exc
    return resp


@patch("app.pipedrive.httpx.Client")
def test_pipedrive_note_failure_still_counts_as_pushed(client_cls):
    # Person + deal created, note POST fails: must still return True — a retry
    # driven by pushed_to_pipedrive=false would duplicate the person and deal.
    client = client_cls.return_value.__enter__.return_value
    client.post.side_effect = [
        _pd_response({"data": {"id": 7}}),                      # person ok
        _pd_response({"data": {"id": 9}}),                      # deal ok
        _pd_response(raise_exc=RuntimeError("note POST 500")),  # note fails
    ]
    assert create_lead_in_pipedrive(_row()) is True
    assert client.post.call_count == 3


@patch("app.pipedrive.httpx.Client")
def test_pipedrive_person_failure_propagates(client_cls):
    # If the person POST fails nothing exists in the CRM yet, so the error must
    # propagate and leave pushed_to_pipedrive=false for a safe retry.
    client = client_cls.return_value.__enter__.return_value
    client.post.return_value = _pd_response(raise_exc=RuntimeError("persons POST 500"))
    with pytest.raises(RuntimeError):
        create_lead_in_pipedrive(_row())
