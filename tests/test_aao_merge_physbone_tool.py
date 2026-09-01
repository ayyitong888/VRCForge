from pathlib import Path


SOURCE_PATH = Path("Assets/VRCForge/Editor/AaoMergePhysBoneTool.cs")


def source() -> str:
    return SOURCE_PATH.read_text(encoding="utf-8")


def test_aao_merge_physbone_is_a_fixed_public_api_primitive() -> None:
    text = source()
    for fragment in (
        'toolId: "vrc_configure_aao_merge_physbone"',
        "When to use:",
        "When NOT to use:",
        "Negative example:",
        'MergeTypeName = "Anatawa12.AvatarOptimizer.MergePhysBone"',
        'PhysBoneBaseTypeName = "VRC.Dynamics.VRCPhysBoneBase"',
        'AaoPackageName = "com.anatawa12.avatar-optimizer"',
        'RequirePublicMethod(mergeType, "Initialize", typeof(void), typeof(int))',
        'RequirePublicProperty(mergeType, "MakeParent", typeof(bool), true)',
        'mergeType.GetProperty("PhysBones", BindingFlags.Instance | BindingFlags.Public)',
        'RequirePublicMethod(physBones.PropertyType, "Add", typeof(void), physBoneType)',
        "Undo.AddComponent",
    ):
        assert fragment in text

    for forbidden in (
        'GetField("componentsSet"',
        'FindProperty("componentsSet"',
        "SerializedProperty",
        "TraceAndOptimize",
        "System.Reflection.Emit",
        "Microsoft.CodeAnalysis",
    ):
        assert forbidden not in text


def test_aao_merge_physbone_preview_is_zero_write_and_apply_is_receipt_bound() -> None:
    text = source()
    preview_branch = text.index("if (preview)")
    first_mutation = text.index("Undo.AddComponent")
    assert preview_branch < first_mutation

    for fragment in (
        "sourcePhysBonePaths exceeds its fixed bound",
        "sourcePhysBonePaths must be unique",
        "must contain exactly one VRCPhysBone",
        "expectedProjectPath",
        "expectedSceneFileDigest",
        "expectedHostObjectId",
        "expectedAaoPackageVersion",
        "expectedSourceDigest",
        "expectedBeforeStateDigest",
        "expectedTargetStateDigest",
        "expectedPreviewDigest",
        "BuildPreview(snapshot.Request)",
        "SnapshotsMatch(snapshot, immediate)",
    ):
        assert fragment in text


def test_aao_merge_physbone_save_readback_and_failure_restore_are_explicit() -> None:
    text = source()
    for fragment in (
        "EditorSceneManager.SaveScene",
        "Independent post-write readback",
        "ResolveUniqueGameObject",
        "ReadConfiguredState",
        "VerifyReadback",
        "Undo.RevertAllDownToGroup",
        "TryRestore",
        "checkpointRestoreRequired",
        '"checkpoint_restore_required"',
        '"not_started_or_restored"',
    ):
        assert fragment in text


def test_aao_merge_physbone_is_create_only_and_version_bounded() -> None:
    text = source()
    assert "CreateNew target already exists" in text
    assert 'parts[0] == "1" && parts[1] == "9"' in text
    assert "MinimumSources = 2" in text
    assert "MaximumSources = 16" in text
    assert "host.GetComponents(compatibility.MergeType)" in text
    assert "existing.Length != 0" in text


def test_aao_merge_physbone_uses_unambiguous_unity_apis_and_valid_result_factory() -> None:
    text = source()
    assert "UnityEditor.PackageManager.PackageInfo.FindForAssembly" in text
    assert "VRCForgeToolResult.Completed(" in text
    assert "VRCForgeToolResult.Success(" not in text
