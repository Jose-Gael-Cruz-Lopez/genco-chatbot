import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

REQUIRED_FIELDS: dict[str, list[str]] = {
    "wholesale": ["name", "email", "phone", "organization", "estimated_sheets"],
    "refill_station": ["name", "email", "phone", "organization",
                       "num_laundry_rooms", "num_students"],
    "question": ["name", "email", "question"],
}

CAPTURE_LEAD_TOOL = {
    "type": "function",
    "function": {
        "name": "capture_lead",
        "description": "Record a lead once ALL required fields for the intent are collected.",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {"type": "string",
                           "enum": ["wholesale", "refill_station", "question"]},
                "name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "organization": {"type": "string"},
                "estimated_sheets": {"type": "integer"},
                "num_laundry_rooms": {"type": "integer"},
                "num_students": {"type": "integer"},
                "question": {"type": "string"},
            },
            "required": ["intent", "name", "email"],
        },
    },
}


def validate_lead(intent: str, fields: dict) -> list[str]:
    errors: list[str] = []
    for f in REQUIRED_FIELDS.get(intent, []):
        if fields.get(f) in (None, ""):
            errors.append(f"missing required field: {f}")
    email = fields.get("email")
    if email and not _EMAIL_RE.match(str(email)):
        errors.append("invalid email format")
    return errors
