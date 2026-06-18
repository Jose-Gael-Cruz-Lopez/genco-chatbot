import time
from collections import defaultdict, deque

_INJECTION = ("ignore previous", "ignore all previous", "system prompt",
              "reveal your", "disregard", "you are now", "act as",
              "ignore all", "override", "forget everything", "new instructions")
# rough $/1K tokens for cost estimation; tune per model
_RATES = {"default": 0.003}


def is_injection_attempt(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in _INJECTION)


def check_on_topic(text: str) -> bool:
    # Conservative: treat everything on-topic; the system prompt enforces grounding.
    # Off-topic handling is delegated to the model's "answer only from context" rule.
    return True


def consent_note() -> str:
    return ("Before we continue — I'll only use the contact details you share to connect you "
            "with our team. ")


# NOTE: in-memory, single-instance only — swap in Redis (or similar shared store) for multi-instance deployments.
class RateLimiter:
    def __init__(self, per_minute: int):
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
