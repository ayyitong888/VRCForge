from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CRUD = (ROOT / "Assets" / "VRCForge" / "Editor" / "Generic" / "UnityAssetPrefabCrud.cs").read_text(encoding="utf-8")
SCANNER = (ROOT / "Assets" / "VRCForge" / "Editor" / "WardrobeScanner.cs").read_text(encoding="utf-8")
WRITER = (ROOT / "Assets" / "VRCForge" / "Editor" / "WardrobeOutfitWriter.cs").read_text(encoding="utf-8")
SETUP = (ROOT / "Assets" / "VRCForge" / "Editor" / "SetupOutfitTool.cs").read_text(encoding="utf-8")
CONTINUATION = (ROOT / "Assets" / "VRCForge" / "Core" / "MCP" / "VRCForgeApprovedObjectReceipt.cs").read_text(encoding="utf-8")


def test_prefab_tools_support_exact_approval_receipts_without_renaming_tools():
    assert 'toolId: "vrc_instantiate_prefab"' in CRUD
    assert 'toolId: "vrc_unpack_prefab"' in CRUD
    for field in ("expectedPrefabGuid", "expectedScenePath", "expectedGlobalObjectId", "globalObjectId", "prefabGuid"):
        assert field in CRUD
    for field in ("expectedAssetDependencyHash", "expectedParentGlobalObjectId", "expectedResultPath", "unpacked"):
        assert field in CRUD
    for field in ("approvedObjectReceiptNonce", "approvedContinuationTools", "continuationRegistered", "continuationConsumed"):
        assert field in CRUD
    for field in ("committed = true", 'commitState = "unknown"', "checkpointRecoveryRequired = true"):
        assert CRUD.count(field) >= 2


def test_prefab_instantiation_rejects_duplicate_hierarchy_path_before_mutation():
    assert "if (AssetPrefabCore.CountHierarchyPath(expectedResultPath, scene.handle) != 0)" in CRUD
    preflight = CRUD.split("var instance = PrefabUtility.InstantiatePrefab(asset)", 1)[0]
    assert "refusing to create an ambiguous duplicate" in preflight


def test_wardrobe_readback_is_stable_and_writer_echoes_assigned_value():
    assert "ComputeStableFingerprint" in SCANNER
    assert "fingerprint" in SCANNER
    assert "assignedValue = newValue" in WRITER
    assert "expectedAssignedValue" in WRITER
    assert "expectedWardrobeFingerprint" in WRITER
    assert "approvedObjectReceiptNonce" in WRITER
    assert "continuationConsumed" in WRITER
    assert "approvedObjectReceiptNonce" in SETUP
    assert "outfitGlobalObjectId" in SETUP


def test_add_outfit_continuation_receipt_is_short_lived_ordered_and_identity_bound():
    assert "TimeSpan.FromMinutes(10)" in CONTINUATION
    assert "receipt.Tools[receipt.NextIndex]" in CONTINUATION
    assert "GlobalObjectId.GetGlobalObjectIdSlow(target)" in CONTINUATION
    assert "Receipts.Remove(nonce)" in CONTINUATION
    for tool in ("vrc_unpack_prefab", "vrc_setup_outfit", "vrc_add_wardrobe_outfit"):
        assert tool in CONTINUATION


def test_setup_outfit_persists_domain_reload_terminal_truth_without_nonce_material():
    assert "SessionState.SetString(JobSessionPrefix + job.jobId" in SETUP
    assert "LoadPersistedJob(jobId)" in SETUP
    assert "editor_reloaded_after_setup_job_started" in SETUP
    assert "job.mutationStarted || job.continuationConsumed" in SETUP
    persisted_block = SETUP.split("private static void PersistJob", 1)[1].split(
        "private static JObject LoadPersistedJob", 1
    )[0]
    assert "approvedObjectReceiptNonce" not in persisted_block
    assert "JobSessionIndexKey" in SETUP
    assert ".Where(jobId => LoadPersistedJob(jobId) != null)" in SETUP
