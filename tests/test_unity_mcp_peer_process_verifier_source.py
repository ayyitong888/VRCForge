from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpPeerProcessVerifier.cs"


def test_peer_verifier_is_windows_owner_pid_fail_closed_contract() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "internal static class VRCForgeMcpPeerProcessVerifier" in source
    assert "internal static bool TryScreenManagedBackendPeer(TcpClient client, out VRCForgeMcpPeerProcessEvidence evidence)" in source
    assert "GetExtendedTcpTable" in source
    assert "TcpTableOwnerPidAll" in source
    assert "Environment.OSVersion.Platform != PlatformID.Win32NT" in source
    assert "evidence = null;" in source
    assert "IPAddress.Loopback" in source
    assert 'ReadExpectedProcessPath(processId, "vrcforge_backend", out processStartTimeUtcTicks)' in source
    assert 'ReadExpectedProcessPath(parentProcessId, "VRCForge", out parentProcessStartTimeUtcTicks)' in source
    assert "VerifyManagedProcessLayout(processPath, parentProcessPath)" in source
    assert 'Path.Combine(root, "backend", "vrcforge_backend.exe")' in source
    assert 'Path.Combine(root, "payload-integrity.json")' not in source
    assert "VRCForgeMcpTrustedRelease.AssetPath" not in source
    assert 'manifest["desktopSha256"]' not in source
    assert 'manifest["backendSha256"]' not in source
    assert "SHA256.Create()" not in source
    assert "CreateToolhelp32Snapshot" in source
    assert "ProcessStartTimeUtcTicks" in source
    assert "ParentProcessStartTimeUtcTicks" in source
    assert "IsRegularFileWithoutReparse" in source
    assert "IsDirectoryWithoutReparse" in source
    assert "return false;" in source


def test_peer_verifier_never_uses_caller_claims_or_development_fallbacks() -> None:
    source = SOURCE.read_text(encoding="utf-8").lower()

    assert "claimedpid" not in source
    assert "request.pid" not in source
    assert "python" not in source
    assert "dev fallback" not in source
    assert "localhost" not in source


def test_source_tree_keeps_release_integrity_metadata_for_install_time_only() -> None:
    trusted_release = (SOURCE.parent / "VRCForgeMcpTrustedRelease.cs").read_text(encoding="utf-8")
    trusted_release_data = (SOURCE.parent / "VRCForgeMcpTrustedRelease.json").read_text(encoding="utf-8")

    assert 'internal const string AssetPath = "Assets/VRCForge/Editor/MCP/VRCForgeMcpTrustedRelease.json";' in trusted_release
    assert "DesktopSha256" not in trusted_release
    assert "BackendSha256" not in trusted_release
    assert '"schema": "vrcforge.trusted-release.v1"' in trusted_release_data
    assert '"desktopSha256": ""' in trusted_release_data
    assert '"backendSha256": ""' in trusted_release_data


def test_peer_verifier_does_not_use_release_pairing_as_runtime_authorization() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "VRCForgeMcpTrustedRelease.AssetPath" not in source
    assert '"vrcforge.trusted-release.v1"' not in source
    assert 'manifest["desktopSha256"]' not in source
    assert 'manifest["backendSha256"]' not in source
    assert "VRCForgeMcpTrustedRelease.DesktopSha256" not in source
    assert "VRCForgeMcpTrustedRelease.BackendSha256" not in source
