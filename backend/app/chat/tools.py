import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

REQUIRED_FIELDS: dict[str, list[str]] = {
    "wholesale": ["name", "email", "phone", "organization", "estimated_sheets"],
    "refill_station": ["name", "email", "phone", "organization",
                       "num_laundry_rooms", "num_students"],
    "question": ["name", "email", "question"],
}

# Human labels for validation re-prompts: the ValueError raised by capture_lead is shown
# verbatim to the end user, so internal snake_case names must never leak into chat replies.
FIELD_LABELS: dict[str, str] = {
    "name": "your name",
    "email": "your email address",
    "phone": "your phone number",
    "organization": "your organization's name",
    "estimated_sheets": "your estimated total sheet purchase",
    "num_laundry_rooms": "the number of laundry rooms",
    "num_students": "the number of students or tenants",
    "question": "your question for the team",
}


def _tool_description() -> str:
    per_intent = "; ".join(
        f"{intent}: {', '.join(fields)}" for intent, fields in REQUIRED_FIELDS.items())
    return ("Record a lead once ALL required fields for the intent are collected. "
            f"Required fields per intent — {per_intent}. "
            "Keep asking the user for the missing fields; do not call this tool early.")


CAPTURE_LEAD_TOOL = {
    "type": "function",
    "function": {
        "name": "capture_lead",
        "description": _tool_description(),
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
            # Per-intent required fields (single source of truth: REQUIRED_FIELDS), so a
            # schema-compliant model is steered to collect everything BEFORE emitting the
            # call. Server-side validate_lead remains the backstop.
            "allOf": [
                {"if": {"properties": {"intent": {"const": intent}},
                        "required": ["intent"]},
                 "then": {"required": fields}}
                for intent, fields in REQUIRED_FIELDS.items()
            ],
        },
    },
}


def validate_lead(intent: str, fields: dict) -> list[str]:
    """Machine-readable validation errors (stable strings, used in logs and tests).

    Use humanize_lead_errors() before surfacing these to an end user.
    """
    errors: list[str] = []
    for f in REQUIRED_FIELDS.get(intent, []):
        if fields.get(f) in (None, ""):
            errors.append(f"missing required field: {f}")
    email = fields.get("email")
    if email and not _EMAIL_RE.match(str(email)):
        errors.append("invalid email format")
    return errors


_MISSING_PREFIX = "missing required field: "


def humanize_lead_errors(errors: list[str]) -> str:
    """Turn validate_lead errors into a friendly fragment for the user-facing re-prompt.

    The router surfaces it as "I still need a bit more info before I can submit
    this: <fragment>", so the fragment is a natural-language list of what's needed.
    """
    needs: list[str] = []
    for err in errors:
        if err.startswith(_MISSING_PREFIX):
            field = err.removeprefix(_MISSING_PREFIX)
            needs.append(FIELD_LABELS.get(field, field.replace("_", " ")))
        elif err == "invalid email format":
            needs.append("a valid email address (the one provided doesn't look right)")
        else:
            needs.append(err)
    if len(needs) == 1:
        return needs[0]
    return ", ".join(needs[:-1]) + " and " + needs[-1]
