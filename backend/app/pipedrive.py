import logging
import httpx
from app.config import get_settings

log = logging.getLogger(__name__)
_settings = get_settings()


def create_lead_in_pipedrive(lead: dict) -> bool:
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
        notes = client.post(f"{base}/notes", params=params,
                            json={"content": note, "deal_id": deal_id})
        notes.raise_for_status()
    return True
