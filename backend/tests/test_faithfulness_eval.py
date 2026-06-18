"""Grounded-faithfulness CI gate (DeepEval).

This goes beyond run_eval.py's keyword routing check: it uses an LLM judge to score whether an
answer is faithful to (i.e. doesn't contradict / hallucinate beyond) the retrieved KB context.

It is OPTIONAL and self-skipping:
  * `pytest.importorskip("deepeval")` skips the whole module if DeepEval isn't installed.
  * `skipif` skips if no judge LLM key is configured.
So the default offline suite stays green; when DeepEval + a judge key are present (e.g. in CI),
these cases run and FAIL the build if faithfulness drops below threshold.

Enable with:
    pip install -r backend/requirements-ml.txt
    export OPENAI_API_KEY=...            # judge model
    export DEEPEVAL_JUDGE_MODEL=gpt-4.1-mini   # optional override
    pytest backend/tests/test_faithfulness_eval.py -v

The cases pair a representative grounded answer with the KB snippet that supports it. They assert
the metric/wiring works and catch regressions where an answer drifts from the KB.
"""
import os
import pytest

pytest.importorskip("deepeval")

from deepeval import assert_test
from deepeval.metrics import FaithfulnessMetric
from deepeval.test_case import LLMTestCase

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="DeepEval faithfulness needs a judge LLM (set OPENAI_API_KEY).",
)

_JUDGE_MODEL = os.getenv("DEEPEVAL_JUDGE_MODEL", "gpt-4.1-mini")
_THRESHOLD = 0.7

CASES = [
    LLMTestCase(
        input="How do I buy laundry sheets for home delivery?",
        actual_output=(
            "You can buy them on the product page at "
            "https://generationconscious.co/product/laundry-detergent-sheets/ — choose your sheet "
            "count, scent, and one-time or subscription, then add to cart."
        ),
        retrieval_context=[
            "Home delivery: guide the user to the detergent-sheets product page "
            "https://generationconscious.co/product/laundry-detergent-sheets/ to choose options and buy."
        ],
    ),
    LLMTestCase(
        input="How fast does the team respond?",
        actual_output="The team responds within 24 hours, usually around 15 minutes.",
        retrieval_context=[
            "Submit a question: capture question + contact; team responds within 24h (avg ~15 min)."
        ],
    ),
    LLMTestCase(
        input="Do you charge sales tax, and how is shipping calculated?",
        actual_output=(
            "Sales tax applies to New York orders only, and shipping is calculated at checkout "
            "using live USPS rates — the exact amounts show at checkout."
        ),
        retrieval_context=[
            "Shipping is calculated at checkout using live USPS rates; sales tax applies to New "
            "York orders only. Never quote specific dollar figures."
        ],
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.input[:40])
def test_answers_are_faithful_to_kb(case):
    metric = FaithfulnessMetric(threshold=_THRESHOLD, model=_JUDGE_MODEL, include_reason=True)
    assert_test(case, [metric])
