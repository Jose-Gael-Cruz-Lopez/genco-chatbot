"""Optional ML-based prompt-injection scanner (LLM Guard).

This sits in front of the substring guardrail in `chat/router.py` as an additional layer. It is
OPTIONAL: `llm-guard` pulls in transformers/torch, so it is NOT a core dependency. When the
package is not installed (the default), `is_injection()` returns False and the existing substring
guard (`guardrails.is_injection_attempt`) remains the active defense — the app runs unchanged.

Enable it by installing the optional ML extra and deploying with it:
    pip install -r backend/requirements-ml.txt

The model and threshold are loaded lazily on first use and cached for the process lifetime.
"""
import logging

logger = logging.getLogger(__name__)

# Risk threshold for the PromptInjection model; higher = fewer (but more confident) flags.
_THRESHOLD = 0.5

_scanner = None          # cached scanner instance once loaded
_unavailable = False     # set once if llm-guard can't be imported / the model can't load


def _get_scanner():
    """Lazily construct the PromptInjection scanner. Returns None if llm-guard is unavailable."""
    global _scanner, _unavailable
    if _scanner is not None or _unavailable:
        return _scanner
    try:
        from llm_guard.input_scanners import PromptInjection
        from llm_guard.input_scanners.prompt_injection import MatchType

        _scanner = PromptInjection(threshold=_THRESHOLD, match_type=MatchType.FULL)
        logger.info("llm-guard PromptInjection scanner loaded.")
    except Exception:
        # Not installed, or the model failed to load — degrade gracefully to the substring guard.
        _unavailable = True
        logger.info("llm-guard not available; using substring injection guard only.")
    return _scanner


def is_injection(text: str) -> bool:
    """True if the ML scanner flags `text` as a prompt-injection attempt.

    Returns False when the scanner is unavailable (so the caller's substring guard still applies)
    or if scanning raises — a scanner failure must never block a legitimate chat turn.
    """
    scanner = _get_scanner()
    if scanner is None:
        return False
    try:
        _sanitized, is_valid, _risk = scanner.scan(text)
        return not is_valid
    except Exception:
        logger.exception("llm-guard scan failed; deferring to substring guard.")
        return False


def available() -> bool:
    """Whether the ML scanner is loaded and active (vs. the substring fallback)."""
    return _get_scanner() is not None
