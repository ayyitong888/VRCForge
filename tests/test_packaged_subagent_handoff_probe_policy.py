from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "diagnose_packaged_subagent_handoff.mjs"


def test_subagent_handoff_probe_has_explicit_local_acceptance_boundary() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'const allowUnpushed = process.argv.includes("--allow-unpushed");' in source
    assert '"--self-test"' in source
    assert "isLocalAcceptanceBuildPolicy" in source
    assert "releaseEligible === false" in source
    assert "policy.allowDirty === false" in source
    assert "!worktreeClean || !localAcceptanceBuildPolicy" in source
    assert "strictReleaseBinding: false" in source
    assert "Unknown packaged sub-agent handoff probe option." in source
    assert "| Select-Object -First 1" not in source
    assert "if ($entry.Count -ne 1)" in source
    assert '!key.toUpperCase().startsWith("VRCFORGE_")' in source
    assert 'VRCFORGE_DESKTOP_EXECUTOR: "0"' in source
    assert "APPDATA: hostProfileRoot" in source
    assert "proveLoopbackPortReleased" in source
    assert "let providerPort = 0;" in source
    assert "if (report.provider)" in source
    assert "report.provider.portReleased = portReleased" in source
    assert "async function assertNoHostUnityProcesses()" in source
    assert "await assertNoHostUnityProcesses();" in source
    assert "environment-not-isolated" in source


def test_subagent_handoff_probe_self_test_is_side_effect_free() -> None:
    result = subprocess.run(
        ["node", str(SCRIPT), "--self-test"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "sub-agent handoff probe self-test passed" in result.stdout


def test_subagent_handoff_probe_rejects_unknown_cli_options_before_package_access() -> None:
    result = subprocess.run(
        ["node", str(SCRIPT), "--unexpected-option"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    assert "Unknown packaged sub-agent handoff probe option." in result.stderr
