import logging
import httpx
from app.config import get_settings

log = logging.getLogger(__name__)
_settings = get_settings()


def create_lead_in_pipedrive(lead: dict) -> bool:
    """Create a person + deal for the lead; returns True once BOTH exist.

    Person+deal is the spec's definition of pushed_to_pipedrive=true ("Attempt Pipedrive
    person+deal → set pushed_to_pipedrive=true on success"). The detail note is
    best-effort: once person and deal exist, a note failure must NOT leave the flag
    false, because a flag-driven retry would re-run this function and duplicate the
    person and deal in the CRM. The note content is logged on failure so an operator
    can attach it by hand. Person or deal failures still propagate, leaving the flag
    false for a safe retry (nothing, or only a person, exists yet — Pipedrive dedupes
    persons far more gracefully than deals).
    """
    base = f"https://{_settings.PIPEDRIVE_DOMAIN}.pipedrive.com/api/v1"
    params = {"api_token": _settings.PIPEDRIVE_API_TOKEN}
    with httpx.Client(timeout=15.0) as client:
        person = client.post(f"{base}/persons", params=params, json={
            "name": lead.get("name"),
            "email": [lead.get("email")] if lead.get("email") else [],
            "phone": [lead.get("phone")] if lead.get("phone") else [],
        })
        person.raise_for_status()
        person_id = person.json()["data"]["id"]
        note = f"intent={lead['intent']} extra={lead.get('extra')} msg={lead.get('message','')}"
        deal = client.post(f"{base}/deals", params=params, json={
            "title": f"{lead.get('organization') or lead.get('name')} — {lead['intent']}",
            "person_id": person_id})
        deal.raise_for_status()
        deal_id = deal.json()["data"]["id"]
        try:
            notes = client.post(f"{base}/notes", params=params,
                                json={"content": note, "deal_id": deal_id})
            notes.raise_for_status()
        except Exception:
            log.exception(
                "pipedrive note failed for deal %s (person %s); person+deal exist so the "
                "lead still counts as pushed. Note content for manual attach: %s",
                deal_id, person_id, note)
    return True
