"""Is anyone from the team actually here?

Live chat is only ever offered while this says yes, so a visitor is never
promised a human who isn't there. Availability expires on its own: the portal
heartbeats every 15s and the TTL is three missed beats, so closing the tab takes
the team offline without anyone remembering to flip a switch.

Every failure path returns False. Failing closed sends the visitor down the email
route, which always works.
"""
import logging
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.db import get_supabase

log = logging.getLogger(__name__)

AGENT_ID = "greg"


def parse_ts(value: str | None) -> datetime | None:
    """Parse a Postgres timestamptz as returned by PostgREST, or None.

    Shared with live.chats so every timeout in the system reads timestamps the
    same way. Trailing 'Z' is normalised because fromisoformat rejects it before
    Python 3.11 and PostgREST is not consistent about which form it sends.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def heartbeat(available: bool) -> None:
    """Record that the portal is open and whether the team is taking chats.

    Best-effort: a failed heartbeat simply ages out into 'unavailable', which is
    the safe direction, so this must never raise into the portal.
    """
    try:
        get_supabase().table("agent_presence").upsert({
            "id": AGENT_ID,
            "available": available,
            "last_seen_at": datetime.now(UTC).isoformat(),
        }).execute()
    except Exception:
        log.exception("agent presence heartbeat failed (available=%s)", available)


def is_agent_available(now: datetime | None = None) -> bool:
    """True only when the team is toggled on AND the heartbeat is fresh."""
    try:
        resp = (get_supabase().table("agent_presence")
                .select("available,last_seen_at").eq("id", AGENT_ID).execute())
        rows = resp.data or []
        if not rows or not rows[0].get("available"):
            return False
        seen = parse_ts(rows[0].get("last_seen_at"))
        if seen is None:
            return False
        ttl = get_settings().AGENT_HEARTBEAT_TTL_SECONDS
        return (now or datetime.now(UTC)) - seen <= timedelta(seconds=ttl)
    except Exception:
        log.exception("presence lookup failed; treating the team as unavailable")
        return False
