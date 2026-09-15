"""Registered optimization handlers must retain scanner failure semantics."""
from dataclasses import replace
import pytest
import dashboard_server as dashboard

CASES = [
    ('inventory', 'parameters'), ('menu_map', 'menu'),
    ('compressibility_plan', 'parameters'), ('compressibility_plan', 'menu'),
    ('compressibility_plan', 'fx'),
    ('budget_audit', 'parameters'), ('animator_usage', 'animation_bindings'),
    ('vrcfury_compressor_plan', 'menu'), ('behavior_regression', 'fx'),
    ('path_to_skill', 'parameters'),
]

@pytest.mark.parametrize('suffix,failed_source', CASES)
def test_parameter_read_does_not_turn_scanner_failure_into_zero_risk(monkeypatch, suffix, failed_source):
    validation = {'sources': {name: {'ok': True, 'payload': {}} for name in ('parameters','menu','fx','animation_bindings')}}
    validation['sources'][failed_source] = {'ok': False, 'error': 'Avatar descriptor not found'}
    monkeypatch.setattr(dashboard.OPTIMIZATION, '_ports', replace(
        dashboard.OPTIMIZATION._ports, build_validation_report=lambda _: validation))
    result = dashboard.AGENT_GATEWAY._tools['vrcforge_optimization_parameter_'+suffix].handler({'avatarPath': 'MissingAvatar'})
    assert result['ok'] is False
    assert result['error']['source'] == failed_source
    assert 'summary' not in result.get('result', {})


@pytest.mark.parametrize('failed_source', [None, 'menu'])
def test_successful_empty_inventory_does_not_require_unrelated_menu(monkeypatch, failed_source):
    validation = {'sources': {'parameters': {'ok': True, 'payload': {'parameters': []}}}}
    if failed_source:
        validation['sources'][failed_source] = {'ok': False, 'error': 'menu failed'}
    monkeypatch.setattr(dashboard.OPTIMIZATION, '_ports', replace(
        dashboard.OPTIMIZATION._ports, build_validation_report=lambda _: validation))
    result = dashboard.AGENT_GATEWAY._tools['vrcforge_optimization_parameter_inventory'].handler({})
    assert result['ok'] is True
    assert result['result']['summary']['totalCustomParameters'] == 0
