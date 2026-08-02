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
    assert "VerifyPairedReleasePayload(processPath, parentProcessPath)" in source
    assert 'Path.Combine(root, "backend", "vrcforge_backend.exe")' in source
    assert 'Path.Combine(root, "payload-integrity.json")' in source
    assert '"vrcforge.payload-integrity.v1"' in source
    assert "VRCForgeMcpTrustedRelease.DesktopSha256" in source
    assert "VRCForgeMcpTrustedRelease.BackendSha256" in source
    assert 'VerifyIntegrityEntry(files, "desktop", "VRCForge.exe", expectedParent, expectedDesktopDigest)' in source
    assert 'VerifyIntegrityEntry(files, "backend", "backend/vrcforge_backend.exe", expectedBackend, expectedBackendDigest)' in source
    assert "ConstantTimeTextEquals(manifestDigest, releaseDigest)" in source
    assert "ConstantTimeTextEquals(releaseDigest, ComputeSha256(actualPath))" in source
    assert "SHA256.Create()" in source
    assert "ConstantTimeTextEquals" in source
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


def test_source_tree_keeps_managed_peer_lane_unbound_until_release_pairing() -> None:
    trusted_release = (SOURCE.parent / "VRCForgeMcpTrustedRelease.cs").read_text(encoding="utf-8")

    assert 'internal const string DesktopSha256 = "";' in trusted_release
    assert 'internal const string BackendSha256 = "";' in trusted_release
