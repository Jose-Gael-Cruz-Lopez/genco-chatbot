"""Guardrails: prompt-injection screening, PII consent note, rate limiting, cost cap.

Durability note (IMPORTANT for operators): RateLimiter and CostTracker state is
plain process memory. Both reset to zero on every deploy/restart/crash, and each
uvicorn worker or extra instance keeps its own independent copy — so the "daily"
cost cap is per-process, not global, and effectively multiplies by the
worker/instance count. Pin the deploy to a single worker, or swap in a shared
store (Redis, or a Supabase row keyed by UTC date) before scaling out.

On-topic enforcement is deliberately NOT implemented here: it is delegated to
the system prompt's "answer only from context" rule plus the retrieval-score
escalation threshold (see app/escalation.py). A former always-True
check_on_topic() stub was removed because it was dead code — never called from
any request path — and tightening it would have silently done nothing.
"""

import re
import time
from collections import defaultdict, deque

_INJECTION = ("ignore previous", "ignore all previous", "system prompt",
              "reveal your", "disregard", "you are now", "act as",
              "ignore all", "override", "forget everything", "new instructions")
# Match phrases only at word boundaries: "act as a pirate" trips the guard, but
# "impact assessment" (which contains "act as" as a raw substring) does not.
_INJECTION_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in _INJECTION) + r")\b")

# USD per 1K tokens, split prompt/completion — OpenRouter list prices for the
# configured models (high side, so the cap trips early rather than late).
# Add an entry whenever OPENROUTER_MODEL or OPENROUTER_MODEL_FALLBACK changes.
# Unknown models fall back to "default", which assumes sonnet-class pricing
# (the most expensive configured model) so an unlisted model never undercounts.
_RATES: dict[str, dict[str, float]] = {
    "anthropic/claude-3.5-sonnet": {"prompt": 0.003, "completion": 0.015},
    "openai/gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "default": {"prompt": 0.003, "completion": 0.015},
}


def is_injection_attempt(text: str) -> bool:
    return _INJECTION_RE.search(text.lower()) is not None


def consent_note() -> str:
    """One-line PII disclosure shown before the bot collects name/email/phone."""
    return ("Before we continue — I'll only use the contact details you share "
            "to connect you with our team.")


# NOTE: in-memory, single-instance only — swap in Redis (or similar shared
# store) for multi-instance deployments. See module docstring.
class RateLimiter:
        self.per_minute = per_minute
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        q = self._hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


class CostTracker:
    def __init__(self, daily_cap_usd: float):
        self.cap = daily_cap_usd
        self._spent = 0.0
        self._day = time.gmtime().tm_yday

    def _roll(self):
        today = time.gmtime().tm_yday
        if today != self._day:
            self._day, self._spent = today, 0.0

    def record(self, usage: dict, model: str) -> None:
        self._roll()
        tokens = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        self._spent += (tokens / 1000.0) * _RATES.get(model, _RATES["default"])

    def exceeded(self) -> bool:
        self._roll()
        return self._spent >= self.cap
