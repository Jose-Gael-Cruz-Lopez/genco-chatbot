from app.escalation import should_escalate


def test_no_scores_escalates():
    assert should_escalate([]) is True


def test_low_similarity_escalates():
    assert should_escalate([0.1, 0.2]) is True


def test_high_similarity_no_escalate():
    assert should_escalate([0.9]) is False


def test_model_signal_overrides():
    assert should_escalate([0.9], model_signal=True) is True


def test_high_risk_keyword_escalates():
    assert should_escalate([0.9], text="I want a refund") is True


def test_normal_text_does_not_escalate():
    assert should_escalate([0.9], text="How do I buy sheets?") is False
