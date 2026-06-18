import logging
from app.db import get_supabase
from app.email_service import send_lead_notification
from app.pipedrive import create_lead_in_pipedrive
from app.chat.tools import REQUIRED_FIELDS, validate_lead

log = logging.getLogger(__name__)
LOW_SIMILARITY = 0.25
HIGH_RISK_KEYWORDS = ("refund", "complaint", "lawyer", "press", "urgent")


def should_escalate(retrieval_scores: list[float], model_signal: bool = False,
                    text: str = "") -> bool:
    top = max(retrieval_scores) if retrieval_scores else 0.0
    if top < LOW_SIMILARITY or model_signal:
        return True
    return any(k in text.lower() for k in HIGH_RISK_KEYWORDS)


def capture_lead(session_id: str, intent: str, fields: dict) -> dict:
    errors = validate_lead(intent, fields)
    if errors:
        raise ValueError("; ".join(errors))
    core = {k: fields.get(k) for k in ("name", "email", "phone", "organization")}
    extra = {k: v for k, v in fields.items()
             if k in REQUIRED_FIELDS.get(intent, []) and k not in core}
    row = {"session_id": session_id, "intent": intent, **core,
           "extra": extra, "message": fields.get("question", "")}
    # 1) store first — the lead must never be lost
    stored = get_supabase().table("leads").insert(row).execute().data[0]
    # 2) notify (best-effort; failures flagged, never raised)
    try:
        if send_lead_notification(stored):
            get_supabase().table("leads").update({"emailed": True}).eq("id", stored["id"]).execute()
    except Exception:
        log.exception("lead %s email failed", stored["id"])
    try:
        if create_lead_in_pipedrive(stored):
            get_supabase().table("leads").update({"pushed_to_pipedrive": True}).eq("id", stored["id"]).execute()
    except Exception:
        log.exception("lead %s pipedrive failed", stored["id"])
    return stored
