"""A supplied rollback drift cannot be hidden by an improved optimizer step."""
import pytest
import dashboard_server as dashboard
from test_optimization_validation_delta import report


@pytest.mark.parametrize('rollback_warnings', [None, 1, 2, 3])
def test_registered_delta_keeps_improvement_metrics_but_fails_rollback_drift(rollback_warnings):
    arguments = {'beforeValidation': report(warnings=2), 'afterValidation': report(warnings=1)}
    if rollback_warnings is not None:
        arguments['rollbackValidation'] = report(warnings=rollback_warnings)
    result = dashboard.AGENT_GATEWAY._tools['vrcforge_optimization_validation_delta'].handler(arguments)
    drift = rollback_warnings not in (None, 2)
    assert result['status'] == ('rollback-drift' if drift else 'improved')
    assert result['ok'] is not drift
    assert result['severityDelta']['Warning'] == -1
    assert result['before']['severityCounts']['Warning'] == 2
    assert result['after']['severityCounts']['Warning'] == 1
    assert result['rollbackProof']['matchesBeforeSeverityAndGate'] is (rollback_warnings == 2)
