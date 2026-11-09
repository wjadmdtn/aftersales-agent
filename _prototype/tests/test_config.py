import json

from app.config import HandoffRules
from app.handoff import triggers
from app.handoff.triggers import TurnSignals


def test_threshold_from_env(monkeypatch):
    monkeypatch.setenv("HANDOFF_RISK_AMOUNT_THRESHOLD", "100")
    rules = HandoffRules()
    assert rules.risk_amount_threshold == 100
    r = triggers.evaluate(TurnSignals(user_message="x", refund_amount=150), rules)
    assert r.triggered


def test_keywords_from_env(monkeypatch):
    monkeypatch.setenv("HANDOFF_USER_REQUEST_KEYWORDS", json.dumps(["叫经理"], ensure_ascii=False))
    rules = HandoffRules()
    assert rules.user_request_keywords == ("叫经理",)
    assert triggers.evaluate(TurnSignals(user_message="叫经理来"), rules).triggered


def test_bad_keyword_json_is_rejected(monkeypatch):
    monkeypatch.setenv("HANDOFF_USER_REQUEST_KEYWORDS", "not json")
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        HandoffRules()
