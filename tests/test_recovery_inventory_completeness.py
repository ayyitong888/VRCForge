"""Real recovery service and persisted log; no running App ledger is modified."""
import json

import pytest

from agent_gateway import AgentGateway, APPLY_RECOVERY_SCHEMA


@pytest.mark.parametrize('newer_count', [1, 2001])
def test_old_active_recovery_is_not_hidden_by_page_or_log_window(tmp_path, newer_count):
    gateway = AgentGateway(tmp_path / 'config.json', tmp_path / 'audit')
    service = gateway.checkpoint_recovery
    path = service._ports.apply_recovery_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    active = {'schema': APPLY_RECOVERY_SCHEMA, 'id': 'old-active',
              'status': 'needs_recovery', 'projectRoot': str(tmp_path),
              'updatedAt': '2020-01-01T00:00:00Z'}
    records = [active] + [dict(active, id=f'resolved-{i}', status='dismissed',
                               updatedAt='2021-01-01T00:00:00Z') for i in range(newer_count)]
    path.write_text(''.join(json.dumps(row) + '\n' for row in records), encoding='utf-8')
    assert any(row['id'] == 'old-active' for row in service._active_apply_recoveries())
    result = service.list_interrupted_apply_recoveries({'includeResolved': True, 'limit': 1})
    assert result['count'] == 1
    assert result['activeCount'] == 1
    assert result['blockingWrites'] is True


def test_latest_resolution_still_overrides_old_active_record(tmp_path):
    gateway = AgentGateway(tmp_path / 'config.json', tmp_path / 'audit')
    service = gateway.checkpoint_recovery
    service._append_apply_recovery_entry({'id': 'one', 'status': 'needs_recovery'})
    service._append_apply_recovery_entry({'id': 'one', 'status': 'dismissed'})
    result = service.list_interrupted_apply_recoveries({'includeResolved': True, 'limit': 1})
    assert result['activeCount'] == 0
    assert result['blockingWrites'] is False
