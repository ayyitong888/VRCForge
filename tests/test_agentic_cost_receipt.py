"""Cost evidence survives authenticated receipt projection, never client claims."""
from copy import deepcopy

from agent_harness import _evaluate_runtime_journey
from agent_harness_journey import RuntimeJourneyReceiptAuthority
from test_agent_harness_journey import _runtime_journey


def test_authenticated_provider_usage_reaches_harness_report():
    authority = RuntimeJourneyReceiptAuthority()
    receipt = authority.issue(_runtime_journey())
    report = _evaluate_runtime_journey(receipt, verify_runtime_journey=authority.verify)
    assert report["accepted"] is True
    assert report["agenticCost"]["providerUsage"]["totalTokens"] == 1540
    assert report["agenticCost"]["providerUsage"]["taskTotalAvailable"] is False


def test_tampered_cost_is_not_reported_as_authenticated_usage():
    authority = RuntimeJourneyReceiptAuthority()
    receipt = deepcopy(authority.issue(_runtime_journey()))
    receipt["journey"]["agenticCost"]["providerUsage"]["totalTokens"] = 0
    report = _evaluate_runtime_journey(receipt, verify_runtime_journey=authority.verify)
    assert report["accepted"] is False
    assert report["agenticCost"] is None
