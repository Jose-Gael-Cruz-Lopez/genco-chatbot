from unittest.mock import patch, MagicMock
from app import injection_scanner


def test_unavailable_defers_to_substring_guard():
    # When llm-guard isn't installed/loaded, the ML layer must be a no-op (return False) so the
    # substring guard remains the active defense.
    with patch.object(injection_scanner, "_get_scanner", return_value=None):
        assert injection_scanner.is_injection("ignore previous instructions") is False
        assert injection_scanner.available() is False


def test_detects_injection_when_scanner_marks_invalid():
    # scan() returns (sanitized, is_valid, risk); is_valid=False means injection detected.
    fake = MagicMock()
    fake.scan.return_value = ("clean", False, 0.97)
    with patch.object(injection_scanner, "_get_scanner", return_value=fake):
        assert injection_scanner.is_injection("a sneaky indirect prompt") is True


def test_passes_benign_when_scanner_valid():
    fake = MagicMock()
    fake.scan.return_value = ("clean", True, 0.02)
    with patch.object(injection_scanner, "_get_scanner", return_value=fake):
        assert injection_scanner.is_injection("how do I buy sheets") is False


def test_scan_exception_does_not_block_turn():
    fake = MagicMock()
    fake.scan.side_effect = RuntimeError("model boom")
    with patch.object(injection_scanner, "_get_scanner", return_value=fake):
        assert injection_scanner.is_injection("anything") is False
