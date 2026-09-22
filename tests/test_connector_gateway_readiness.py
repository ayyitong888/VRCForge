from pathlib import Path
from unittest.mock import patch
from external_agent_connector_installer import install_connector, StdioBridgeSpec
from connector_action_status import ConnectorActionStatusStore


def test_disabled_gateway_is_reported_without_opaque_install_failure(tmp_path: Path):
    config = tmp_path / 'client.toml'
    config.write_text('fixture', encoding='utf-8')
    bridge = StdioBridgeSpec('fixture', ['fixture.py'], str(tmp_path), False, 'test')
    with patch('external_agent_connector_installer.resolve_stdio_bridge', return_value=bridge), patch('external_agent_connector_installer._install_codex', return_value={'configPath':str(config),'changed':False}), patch('external_agent_connector_installer.run_stdio_mcp_handshake', return_value={'ok':False,'connected':True,'preflightGatewayEnabled':False}):
        result = install_connector('codexApp', root_dir=tmp_path)
    assert result['stage'] == 'gateway_disabled'
    assert result['changed'] is False
    assert 'Gateway' in result['error'] and 'enable' in result['error'].lower()
    saved = ConnectorActionStatusStore().record(str(tmp_path/'gateway.json'), '', result)
    assert saved['error'] == result['error']
    assert saved['handshake']['preflightGatewayEnabled'] is False

