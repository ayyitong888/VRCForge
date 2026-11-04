from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]


def test_deep_links_are_statically_registered_and_main_window_read_only():
    config = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    assert config["plugins"]["deep-link"]["desktop"]["schemes"] == ["vrcforge"]
    permissions = json.loads((ROOT / "src-tauri/capabilities/main-window.json").read_text(encoding="utf-8"))["permissions"]
    assert [item for item in permissions if item.startswith("deep-link:")] == ["deep-link:allow-get-current"]


def test_both_custom_installers_register_and_remove_the_protocol():
    for name in ("Web", "Offline"):
        source = (ROOT / f"installer/VRCForge_{name}_Installer_x64.nsi").read_text(encoding="utf-8-sig")
        assert '!include "VRCForge_DeepLink.nsh"' in source
        assert "Call un.VrcForgeRemoveDeepLink" in source
    finalization = (ROOT / "installer/VRCForge_InstallFinalization.nsh").read_text(encoding="utf-8-sig")
    assert 'Call VrcForgeRegisterDeepLink\n  !endif\n  !insertmacro VrcForgeRequireInstallStep' in finalization


def test_protocol_command_is_quoted_and_uninstall_checks_ownership():
    source = (ROOT / "installer/VRCForge_DeepLink.nsh").read_text(encoding="utf-8")
    assert '!define VRCFORGE_DEEP_LINK_COMMAND "$\\"$INSTDIR\\VRCForge.exe$\\" $\\"%1$\\""' in source
    install, uninstall = source.split("Function un.VrcForgeRemoveDeepLink")
    assert '"URL Protocol" ""' in install
    assert 'StrCmp $0 "${VRCFORGE_DEEP_LINK_COMMAND}" deep_link_register deep_link_done' in install
    assert uninstall.index('StrCmp $0 "${VRCFORGE_DEEP_LINK_COMMAND}" 0 deep_link_uninstall_done') < uninstall.index("DeleteRegKey")
    assert "!ifndef VRCFORGE_SMOKE_BUILD" in install and "!ifndef VRCFORGE_SMOKE_BUILD" in uninstall
