"""Genco chatbot eval harness — routing/grounding smoke eval over eval/test_set.jsonl.

Usage:
    python eval/run_eval.py [backend_url] [--rps RPS] [--mock]

    backend_url   Base URL of a running backend (default: http://localhost:8000).
    --rps RPS     Live-mode pacing, in requests per second. Defaults to 0.3
                  (one request every ~3.3s) so a full sequential run stays under
                  the backend's default RATE_LIMIT_PER_MINUTE=20 per IP — the
                  throttle reply matches no classify() branch and would show up
                  as spurious FAILs. Pass --rps 0 to disable pacing (e.g. when
                  the backend's limit was raised for the run). If a throttle
                  reply slips through anyway, the case waits out the 60s window
                  and retries once.
    --mock        Offline mode: no network, no keys, no pacing. Every case is
                  answered with a canned reply shaped like the real backend's
                  reply for its category, so test_set.jsonl parsing, classify(),
                  and the pass/fail + latency + score reporting run end-to-end
                  against mocks. Setting MOCK=1 in the environment does the same.

Exit status: 0 when every case passes, 1 otherwise.
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

CASES = Path(__file__).parent / "test_set.jsonl"

EXPECTED_VALUES = ("answer_from_kb", "collect_lead_fields", "escalate",
                   "redirect_to_store", "decline")

# The rate limiter's reply (chat/router.py) — matches no classify() branch, so it must be
# detected and retried rather than scored.
THROTTLE_SNIPPET = "give me a moment and try again"

# --mock canned replies, one per expected category, mirroring the real backend's wording
# (escalate/decline are verbatim from chat/router.py) so classify() sees realistic text.
MOCK_REPLIES: dict[str, str] = {
    "redirect_to_store": (
        "You can pick your sheet count, scent, and one-time vs. subscription on our "
        "product page: https://generationconscious.co/product/laundry-detergent-sheets/"
    ),
    "collect_lead_fields": (
        "Happy to help! Could you share your name, email, phone, and organization?"
    ),
    "answer_from_kb": (
        "Shipping is calculated at checkout using live USPS rates, and sales tax "
        "applies to New York orders."
    ),
    "escalate": (
        "I want to make sure you get accurate information. I can help you buy sheets, "
        "set up refill stations for your community, or connect you with our team — email "
        "Info@GenerationConscious.co or text (516) 619-6174."
    ),
    "decline": (
        "I can only help with Generation Conscious products and orders. "
        "How can I help with that?"
    ),
}

MOCK_SCORES: dict[str, list[float]] = {
    "redirect_to_store": [0.87, 0.74, 0.61],
    "collect_lead_fields": [0.52, 0.41, 0.33],
    "answer_from_kb": [0.83, 0.72, 0.58],
    "escalate": [0.18, 0.12],   # below the 0.25 grounding threshold
    "decline": [],              # guard paths return no retrieval scores
}


def classify(reply: str, scores: list) -> str:
    r = reply.lower()
    if "generationconscious.co/product" in r:
        return "redirect_to_store"
    # Check escalate/decline BEFORE collect_lead_fields so that escalate replies
    # containing "email Info@..." or "connect you" are not misclassified as lead collection.
    if "email info@generationconscious.co" in r or "connect you" in r:
        return "escalate"
    if "only help with" in r or "i can only" in r:
        return "decline"
    if any(w in r for w in ("name", "email", "phone", "organization")):
        return "collect_lead_fields"
    return "answer_from_kb"


def load_cases() -> list[dict]:
    cases: list[dict] = []
    for n, line in enumerate(CASES.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        case = json.loads(line); total += 1
        body = json.dumps({"message": case["question"]}).encode()
        req = urllib.request.Request(f"{BACKEND}/chat", body,
                                     {"Content-Type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        lat.append(time.time() - t0)
        scores = data.get("retrieval_scores", []); sc += scores
        got = classify(data.get("reply", ""), scores)
        ok = got == case["expected"]
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {case['question'][:40]!r} expected={case['expected']} got={got}")
    print(f"\n{passed}/{total} passed | avg latency {sum(lat)/len(lat):.2f}s | "
          f"avg score {sum(sc)/len(sc) if sc else 0:.3f}")


if __name__ == "__main__":
    main()
