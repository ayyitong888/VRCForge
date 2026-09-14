from types import SimpleNamespace

import pytest
import dashboard_server as dashboard


@pytest.mark.parametrize('preview', [False, True])
@pytest.mark.parametrize('source,ordered', [('None', False), ('SourceThenDestination', True)])
def test_registered_fx_handler_and_approved_plan_preserve_interruption(monkeypatch, preview, source, ordered):
    arguments = {'projectPath': '/fixture', 'action': 'ensure_transition', 'layerName': 'FX',
                 'sourceStateName': 'Old', 'destinationStateName': 'New',
                 'interruptionSource': source, 'orderedInterruption': ordered}
    registered = dashboard.AGENT_GATEWAY._write_handlers['vrcforge_manage_fx_animator']
    assert registered.requires_approved_execution_context
    plan = registered.approved_execution_plan_builder(arguments)
    calls = []
    monkeypatch.setattr(dashboard, 'load_dashboard_settings', lambda _: SimpleNamespace())
    monkeypatch.setattr(dashboard, 'invoke_unity_mcp', lambda _settings, tool, request, **kwargs:
                        (calls.append((tool, request)) or SimpleNamespace(payload={'ok': True})))
    if preview:
        dashboard.manage_fx_animator_sync(arguments, preview=True)
    else:
        registered.handler(arguments)
    assert calls[0][1].get('interruptionSource') == source
    assert calls[0][1].get('orderedInterruption') is ordered
    assert calls[0] == (plan[0][0], {**plan[0][1], 'preview': preview})
