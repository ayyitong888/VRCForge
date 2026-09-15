from dataclasses import replace
import pytest
import dashboard_server as dashboard

@pytest.mark.parametrize('source', ['parameters','materials','avatar_items','performance_pc','menu','fx'])
def test_report_does_not_recommend_optimization_after_scan_failure(monkeypatch, source):
    validation={'sources':{source:{'ok':False,'error':'Avatar descriptor not found'}}}
    monkeypatch.setattr(dashboard.OPTIMIZATION,'_ports',replace(dashboard.OPTIMIZATION._ports,build_validation_report=lambda _:validation))
    result=dashboard.AGENT_GATEWAY._tools['vrcforge_optimization_plan'].handler({})
    assert result['ok'] is False
    assert result['nextSafeAction'] is None
    assert result['recommendedOrder']==[]
    assert result['error']['sourceFailures'][0]['source']==source
