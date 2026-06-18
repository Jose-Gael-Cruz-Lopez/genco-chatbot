import logging
import httpx
from app.config import get_settings

log = logging.getLogger(__name__)
_settings = get_settings()


def send_email(to: str, subject: str, body: str) -> bool:
    with httpx.Client(timeout=15.0) as client:
        resp = client.post("https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {_settings.RESEND_API_KEY}"},
            json={"from": _settings.FROM_EMAIL, "to": [to],
                  "subject": subject, "text": body})
        resp.raise_for_status()
    return True


def send_lead_notification(lead: dict) -> bool:
    extra = "\n".join(f"  {k}: {v}" for k, v in (lead.get("extra") or {}).items())
    body = (f"New {lead['intent']} lead\n\n"
            f"Name: {lead.get('name')}\nEmail: {lead.get('email')}\n"
            f"Phone: {lead.get('phone')}\nOrg: {lead.get('organization')}\n"
            f"{extra}\n\nMessage: {lead.get('message','')}")
    return send_email(_settings.ESCALATION_EMAIL, f"[Chatbot] {lead['intent']} lead", body)
