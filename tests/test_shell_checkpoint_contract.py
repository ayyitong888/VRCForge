"""Shell checkpoint dispatch must use the installed callback contract."""
from agent_gateway import AgentGateway


def test_registered_checkpoint_callback_keeps_default_and_exact_scope(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import dashboard_server as server

    class Core:
        def __init__(self, *_args, **_kwargs): pass
        def core_info(self): return {'checkpointPrepareSavesDirtyAssets': False}

    seen = []
    monkeypatch.setattr(server, 'UnityMcpCoreClient', Core)
    monkeypatch.setattr(server, 'PRIMITIVE_BASIS_LIVE_CONNECTION', None)
    monkeypatch.setattr(server, 'build_agent_connection_request', lambda _args: {})
    monkeypatch.setattr(server, 'load_dashboard_settings', lambda _args: SimpleNamespace(unity_mcp_timeout_seconds=30))
    monkeypatch.setattr(server, 'invoke_unity_mcp', lambda _settings, _tool, args, **_kwargs: seen.append(args) or {'ok': True})
    monkeypatch.setattr(server, 'normalize_unity_checkpoint_result', lambda value, _root: value)
    callback = server.AGENT_GATEWAY.approval_transactions.checkpoint_prepare_handler
    assert callback(tmp_path)['ok'] is True
    assert seen[-1] == {'projectPath': str(tmp_path)}
    assert callback(tmp_path, checkpoint_files=['Assets/Owned.mat'])['ok'] is True
    assert seen[-1]['checkpointFiles'] == ['Assets/Owned.mat']
    count = len(seen)
    assert callback(tmp_path, checkpoint_files=[])['ok'] is False
    assert len(seen) == count


def test_native_shell_keeps_current_user_checkpoint_choice(tmp_path, monkeypatch):
    from tests.test_native_runtime_gateway import call, setup_gateway
    from tests.test_runtime_planner_service import FakeCatalog
    from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool

    quote = 'Skip the checkpoint for this write'
    message = quote + '. Create the requested copy.'
    gateway, _model, _ = setup_gateway(tmp_path, [
        call('phase', 'vrcforge_runtime_action', {'action': 'enter_execution'}),
        call('shell', 'call_tool', {
            'tool': 'vrcforge_execute_shell', 'arguments': {'command': 'Write-Output probe'},
            'checkpoint_choice': {'mode': 'skip', 'quote': quote},
        }),
    ])
    tool = PlannerTool('vrcforge_execute_shell', 'Run a command.', 'write', write=True,
                       input_schema={'type': 'object', 'properties': {'command': {'type': 'string'}},
                                     'required': ['command'], 'additionalProperties': False})
    gateway.runtime_planner._catalog = FakeCatalog(
        execution=PlannerCatalogSnapshot(visible_tools=(tool,), routable_tools=(tool,)))
    captured = []

    def execute(params, agent_name='desktop-agent', **kwargs):
        captured.append(kwargs['task_context'])
        return {'ok': True, 'status': 'running', 'sessionId': 'controlled-shell',
                'session': {'sessionId': 'controlled-shell', 'status': 'running'}}

    monkeypatch.setattr(gateway.shell, 'execute', execute)
    gateway.runtime_message({
        'message': message, 'sessionId': 'shell-skip-session',
        'clientTurnId': 'shell-skip-client', 'projectRoot': str(tmp_path),
        'maxAgenticTurns': 4,
        '_humanUserMessageSource': {
            'source': 'app_user_message', 'message': message,
            'sessionId': 'shell-skip-session', 'clientTurnId': 'shell-skip-client',
            'projectRoot': str(tmp_path),
        },
    })
    assert len(captured) == 1
    policy = captured[0].get('checkpointUserPolicy')
    assert policy is not None, captured[0].keys()
    assert policy['sourceQuote'] == quote
    assert policy['tool'] == captured[0]['requestedTool']


def test_loaded_shell_write_alias_keeps_checkpoint_choice():
    import json
    from runtime_planner_service import EXPOSURE_LAYER_EXECUTION, PlannerCatalogSnapshot, PlannerTool
    from tests.native_planner_fixture import NativePlannerFixture
    from tests.test_runtime_planner_service import FakeCatalog, NativeModel, native_call, service

    quote = 'Skip the checkpoint for this write'
    tool = PlannerTool('unity_shell', 'Run an approved command.', 'write',
                       runtime_name='vrcforge_execute_shell', write=True,
                       input_schema={'type': 'object', 'properties': {'command': {'type': 'string'}},
                                     'required': ['command'], 'additionalProperties': False})
    catalog = FakeCatalog(execution=PlannerCatalogSnapshot(visible_tools=(tool,), routable_tools=(tool,)))
    model = NativeModel(native_call('call_tool', json.dumps({
        'tool': 'unity_shell', 'arguments': {'command': 'Write-Output probe'},
        'checkpoint_choice': {'mode': 'skip', 'quote': quote},
    })))
    plan = service(catalog=catalog, model=model).plan_agent_turn(
        quote, {}, {}, exposure_layer=EXPOSURE_LAYER_EXECUTION,
        native_turn=NativePlannerFixture([{'role': 'user', 'content': quote}]),
    )
    assert plan['shellNeeded'] is True
    assert plan.get('checkpointChoice') == {'mode': 'skip', 'quote': quote}
