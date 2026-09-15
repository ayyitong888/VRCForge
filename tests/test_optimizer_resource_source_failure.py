"""Registered resource audit handlers must propagate failed evidence sources."""
from dataclasses import replace
import pytest
import dashboard_server as dashboard

@pytest.mark.parametrize('suffix,source', [
 ('texture_vram_audit','materials'), ('lac_profile_plan','materials'),
 ('mesh_triangle_audit','avatar_items'), ('mesh_triangle_audit','performance_pc'),
 ('meshia_simplify_plan','avatar_items'),
 ('physbone_audit','avatar_items'), ('physbone_audit','performance_pc'),
 ('physbone_audit','performance_quest'), ('physbone_reduce_plan','avatar_items'),
])
def test_resource_audits_do_not_treat_failed_scans_as_empty(monkeypatch, suffix, source):
    validation={'sources':{source:{'ok':False,'error':'Avatar descriptor not found'}}}
    monkeypatch.setattr(dashboard.OPTIMIZATION,'_ports',replace(dashboard.OPTIMIZATION._ports,build_validation_report=lambda _:validation))
    result=dashboard.AGENT_GATEWAY._tools['vrcforge_optimization_'+suffix].handler({})
    assert result['ok'] is False
    assert result['error']['source']==source
    assert 'summary' not in result.get('result',{})
