from pathlib import Path
from unittest.mock import patch
import pytest
import external_agent_connector_installer as c


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path/'client'))
    root=tmp_path/'app'; (root/'tools').mkdir(parents=True)
    (root/'tools/vrcforge_agent_mcp_stdio.py').write_text('# fixture')
    a=tmp_path/'installed/gateway.json'; b=tmp_path/'test/gateway.json'
    c.install_connector('codexApp',root_dir=root,gateway_config_path=a,run_self_test=False)
    return root,a,b,c.codex_config_path()


def test_other_profile_is_visible_but_not_current(tmp_path,monkeypatch):
    root,a,b,path=setup(tmp_path,monkeypatch)
    with patch.object(c,'_probe_windows_app',return_value={}):
        status=c._codex_status('codexApp',c.resolve_stdio_bridge(root,gateway_config_path=b).as_dict())
    assert status['installed'] is True
    assert status['bindingConflict'] is True
    assert status['bindingMatchesCurrent'] is False
    assert status['installable'] is False


@pytest.mark.parametrize('action',['install','remove'])
def test_other_profile_cannot_overwrite_or_remove(tmp_path,monkeypatch,action):
    root,a,b,path=setup(tmp_path,monkeypatch); before=path.read_bytes()
    with pytest.raises(c.ConnectorInstallError) as error:
        if action=='install':
            c.install_connector('codexApp',root_dir=root,gateway_config_path=b,run_self_test=False)
        else:
            c.uninstall_connector('codexApp',root_dir=root,gateway_config_path=b)
    assert error.value.stage=='binding_conflict'
    assert path.read_bytes()==before


def test_owner_can_remove_then_other_profile_can_install(tmp_path,monkeypatch):
    root,a,b,path=setup(tmp_path,monkeypatch)
    c.uninstall_connector('codexApp',root_dir=root,gateway_config_path=a)
    c.install_connector('codexApp',root_dir=root,gateway_config_path=b,run_self_test=False)
    with patch.object(c,'_probe_windows_app',return_value={}):
        status=c._codex_status('codexApp',c.resolve_stdio_bridge(root,gateway_config_path=b).as_dict())
    assert status['bindingMatchesCurrent'] is True
    assert status['bindingConflict'] is False
