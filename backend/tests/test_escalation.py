from app.escalation import is_lead_flow_turn, should_escalate


def test_no_scores_escalates():
    assert should_escalate([]) is True


def test_low_similarity_escalates():
    assert should_escalate([0.1, 0.2]) is True


def test_high_similarity_no_escalate():
    assert should_escalate([0.9]) is False


def test_high_risk_keyword_escalates():
    assert should_escalate([0.9], text="I want a refund") is True


def test_normal_text_does_not_escalate():
    assert should_escalate([0.9], text="How do I buy sheets?") is False


# --- lead-flow suppression (#3): the safety net must not hijack mid-lead-collection turns ---

def test_lead_flow_suppresses_low_similarity():
    # Field answers ("John Smith, john@acme.com") naturally score low against the KB.
    assert should_escalate([0.05], lead_flow=True) is False


def test_lead_flow_suppresses_high_risk_keyword():
    # "City Urgent Care" as an organization name contains "urgent" — must not derail the flow.
    assert should_escalate([0.9], text="City Urgent Care", lead_flow=True) is False


def test_non_lead_flow_still_escalates_on_low_similarity():
    assert should_escalate([0.05], lead_flow=False) is True


def test_lead_flow_turn_from_quick_reply_in_history():
    history = [
        {"role": "user", "content": "Buy Refill Stations"},
        {"role": "assistant", "content": "Great — happy to help set up refill stations."},
    ]
    assert is_lead_flow_turn(history) is True


def test_lead_flow_turn_from_current_quick_reply():
    assert is_lead_flow_turn([], current_message="Question for the team") is True


def test_lead_flow_turn_from_previous_field_request():
    history = [
        {"role": "user", "content": "I want to buy wholesale"},
        {"role": "assistant",
         "content": "Could I get your name and email? This is only used to connect you "
                    "with the Generation Conscious team."},
    ]
    assert is_lead_flow_turn(history) is True


def test_plain_qa_history_is_not_lead_flow():
    history = [
        {"role": "user", "content": "Do you ship to New York?"},
        {"role": "assistant",
         "content": "Yes — shipping is calculated at checkout with live USPS rates."},
    ]
    assert is_lead_flow_turn(history, current_message="what about tax?") is False


def test_escalation_reply_is_not_a_field_request():
    history = [{"role": "assistant", "content": (
        "I want to make sure you get accurate information. I can help you buy sheets, set up "
        "refill stations for your community, or connect you with our team — email "
        "Info@GenerationConscious.co or text (516) 619-6174.")}]
    assert is_lead_flow_turn(history) is False


def test_greeting_is_not_a_field_request():
    history = [{"role": "assistant", "content": (
        "How can we support your sustainability journey? "
        "Buy Sheets / Buy Refill Stations / Question for the team")}]
    assert is_lead_flow_turn(history) is False
