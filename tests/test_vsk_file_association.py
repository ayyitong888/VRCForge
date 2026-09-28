from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = (ROOT / "installer" / "VRCForge_VskAssociation.nsh").read_text(encoding="utf-8")
INSTALLERS = (
    ROOT / "installer" / "VRCForge_Web_Installer_x64.nsi",
    ROOT / "installer" / "VRCForge_Offline_Installer_x64.nsi",
)


def function_body(name: str) -> str:
    match = re.search(
        rf"(?ms)^Function {re.escape(name)}\s*(.*?)^FunctionEnd\s*$",
        INTEGRATION,
    )
    assert match, f"missing NSIS function {name}"
    return match.group(1)


def test_vsk_progid_open_command_quotes_install_path_and_argument():
    assert '!define VRCFORGE_VSK_PROGID "VRCForge.SkillPackage"' in INTEGRATION
    assert '!define VRCFORGE_VSK_OPEN_COMMAND "$\\"$INSTDIR\\VRCForge.exe$\\" $\\"%1$\\""' in INTEGRATION
    assert '"Software\\Classes\\${VRCFORGE_VSK_PROGID}\\shell\\open\\command"' in INTEGRATION


def test_install_registers_openwith_and_preserves_existing_default():
    install = function_body("VrcForgeRegisterVskAssociation")

    assert '"Software\\Classes\\.vsk\\OpenWithProgids"' in install
    assert '"${VRCFORGE_VSK_PROGID}" ""' in install
    assert 'StrCmp $0 "" vsk_register_default' in install
    assert 'StrCmp $0 "${VRCFORGE_VSK_PROGID}" vsk_register_default vsk_register_notify' in install
    assert 'IfErrors vsk_register_default' in install
    assert 'WriteRegStr HKLM "Software\\Classes\\.vsk" "" "${VRCFORGE_VSK_PROGID}"' in install
    assert 'DefaultIcon" "" "$\\"$INSTDIR\\VRCForge.exe$\\",0"' in install
    assert "UserChoice" not in INTEGRATION


def test_association_changes_notify_shell_and_smoke_build_is_guarded():
    for function in ("VrcForgeRegisterVskAssociation", "un.VrcForgeRemoveVskAssociation"):
        body = function_body(function)
        assert "!ifndef VRCFORGE_SMOKE_BUILD" in body
        assert "SHChangeNotify(i 0x08000000, i 0, p 0, p 0)" in body

    for path in INSTALLERS:
        source = path.read_text(encoding="utf-8")
        assert '!include "VRCForge_VskAssociation.nsh"' in source
        assert source.index("SetCompressor /SOLID lzma") < source.index('!include "VRCForge_VskAssociation.nsh"')
        assert re.search(r"!ifndef VRCFORGE_SMOKE_BUILD\s+Call VrcForgeRegisterVskAssociation\s+!endif", source)
        assert re.search(r"!ifndef VRCFORGE_SMOKE_BUILD\s+Call un\.VrcForgeRemoveVskAssociation\s+!endif", source)


def test_uninstall_removes_only_matching_install_command_and_keeps_default():
    uninstall = function_body("un.VrcForgeRemoveVskAssociation")

    assert 'StrCmp $0 "${VRCFORGE_VSK_OPEN_COMMAND}" 0 vsk_uninstall_done' in uninstall
    assert uninstall.index('StrCmp $0 "${VRCFORGE_VSK_OPEN_COMMAND}"') < uninstall.index("DeleteRegValue")
    assert uninstall.index('StrCmp $0 "${VRCFORGE_VSK_OPEN_COMMAND}"') < uninstall.index("DeleteRegKey")
    assert 'DeleteRegValue HKLM "Software\\Classes\\.vsk\\OpenWithProgids" "${VRCFORGE_VSK_PROGID}"' in uninstall
    assert 'DeleteRegKey HKLM "Software\\Classes\\${VRCFORGE_VSK_PROGID}"' in uninstall
    assert 'DeleteRegValue HKLM "Software\\Classes\\.vsk" ""' not in uninstall
    assert 'DeleteRegKey HKLM "Software\\Classes\\.vsk"' not in uninstall
