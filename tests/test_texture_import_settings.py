from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from texture_import_settings import (
    APPROVAL_PREVIEW_SCHEMA,
    RESULT_SCHEMA,
    SETTINGS_DIGEST_SCHEMA,
    TOOL_NAME,
    TextureImportSettingsError,
    bind_authoritative_preview,
    build_preview_arguments,
    build_wrapper_arguments,
    compute_settings_digest,
    normalize_requested_settings,
)


PROJECT_PATH = str(Path("D:/DisposableUnityProject").resolve())


def requested_arguments() -> dict:
    return {
        "textureAssetPath": "Assets/Avatar/Textures/Body.png",
        "platform": "standalone",
        "maxTextureSize": 2048,
        "format": "dxt5_crunched",
        "compression": "high",
        "crunch": True,
        "quality": 82,
    }


def before_settings() -> dict:
    return {
        "platform": "standalone",
        "platformName": "Standalone",
        "overridden": True,
        "maxTextureSize": 4096,
        "format": "automatic",
        "compression": "normal",
        "crunch": False,
        "quality": 50,
        "ignorePlatformSupport": False,
    }


def target_settings() -> dict:
    return {
        "platform": "standalone",
        "platformName": "Standalone",
        "overridden": True,
        "maxTextureSize": 2048,
        "format": "dxt5_crunched",
        "compression": "high",
        "crunch": True,
        "quality": 82,
        "ignorePlatformSupport": False,
    }


def wrapper_arguments() -> dict:
    return {
        "projectPath": PROJECT_PATH,
        "toolName": TOOL_NAME,
        "arguments": requested_arguments(),
    }


def preview_payload(*, would_change: bool = True) -> dict:
    before = before_settings()
    target = target_settings() if would_change else deepcopy(before)
    if not would_change:
        target.update(
            {
                "maxTextureSize": 4096,
                "format": "automatic",
                "compression": "normal",
                "crunch": False,
                "quality": 50,
            }
        )
    before_digest = compute_settings_digest("Default", before)
    target_digest = compute_settings_digest("Default", target)
    return {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "preview": True,
        "verified": True,
        "changed": False,
        "wouldChange": would_change,
        "saved": False,
        "reimported": False,
        "projectPath": PROJECT_PATH,
        "textureAssetPath": "Assets/Avatar/Textures/Body.png",
        "textureAssetGuid": "a" * 32,
        "sourceFileDigestBefore": "b" * 64,
        "sourceFileDigestAfter": "b" * 64,
        "sourceFileIdentityDigest": "d" * 64,
        "sourceFileLinkCount": 1,
        "metaFileDigestBefore": "c" * 64,
        "metaFileDigestAfter": "c" * 64,
        "metaFileIdentityDigest": "e" * 64,
        "metaFileLinkCount": 1,
        "importerType": "Default",
        "beforeSettings": before,
        "targetSettings": target,
        "importerSettingsDigestBefore": before_digest,
        "importerSettingsDigestAfter": before_digest,
        "targetSettingsDigest": target_digest,
        "importerDirtyBefore": False,
        "importerDirtyAfter": False,
    }


def test_request_normalization_is_explicit_and_canonical() -> None:
    normalized = normalize_requested_settings(
        {
            "platform": " Standalone ",
            "maxTextureSize": 2048,
            "format": " DXT5_CRUNCHED ",
            "compression": " HIGH ",
            "crunch": True,
            "quality": 82,
        }
    )

    assert normalized == {
        "platform": "standalone",
        "maxTextureSize": 2048,
        "format": "dxt5_crunched",
        "compression": "high",
        "crunch": True,
        "quality": 82,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"platform": "web"},
        {"maxTextureSize": True},
        {"maxTextureSize": 3000},
        {"format": "bc7", "platform": "android"},
        {"format": "dxt5_crunched", "crunch": False},
        {"format": "dxt5", "crunch": True},
        {"format": "rgba32", "compression": "normal"},
        {"format": "dxt5", "compression": "uncompressed"},
        {"platform": "ios", "format": "automatic", "crunch": True},
        {"compression": "lossless"},
        {"quality": 101},
        {"quality": False},
    ],
)
def test_request_normalization_rejects_unknown_or_incompatible_combinations(change: dict) -> None:
    raw = requested_arguments()
    raw.update(change)

    with pytest.raises(TextureImportSettingsError):
        normalize_requested_settings(raw)


def test_preview_arguments_strip_all_caller_preconditions_and_force_zero_write() -> None:
    raw = requested_arguments()
    raw.update(
        {
            "preview": False,
            "saveAndReimport": True,
            "expectedTextureAssetGuid": "1" * 32,
            "expectedSourceFileDigest": "2" * 64,
            "expectedSourceFileIdentityDigest": "6" * 64,
            "expectedMetaFileDigest": "3" * 64,
            "expectedMetaFileIdentityDigest": "7" * 64,
            "expectedImporterSettingsDigest": "4" * 64,
            "expectedTargetSettingsDigest": "5" * 64,
            "expectedProjectPath": "D:/OtherProject",
            "secretField": "must-not-cross-preview-boundary",
        }
    )

    prepared = build_preview_arguments(raw)

    assert prepared["preview"] is True
    assert prepared["saveAndReimport"] is False
    assert not any(key.startswith("expected") for key in prepared)
    assert "secretField" not in prepared
    assert raw["preview"] is False


def test_flat_inputs_are_normalized_into_the_supervised_wrapper() -> None:
    wrapper = build_wrapper_arguments({"projectPath": PROJECT_PATH, **requested_arguments()})

    assert wrapper == {
        "projectPath": PROJECT_PATH,
        "toolName": TOOL_NAME,
        "arguments": requested_arguments(),
    }


def test_authoritative_preview_binds_asset_project_and_expected_before_receipts() -> None:
    canonical, approval = bind_authoritative_preview(wrapper_arguments(), preview_payload())
    nested = canonical["arguments"]

    assert approval["schema"] == APPROVAL_PREVIEW_SCHEMA
    assert approval["settingsDigestSchema"] == SETTINGS_DIGEST_SCHEMA
    assert approval["projectPath"] == PROJECT_PATH
    assert approval["target"] == {
        "textureAssetPath": "Assets/Avatar/Textures/Body.png",
        "textureAssetGuid": "a" * 32,
        "sourceFileDigest": "b" * 64,
        "sourceFileIdentityDigest": "d" * 64,
        "metaFileDigest": "c" * 64,
        "metaFileIdentityDigest": "e" * 64,
        "importerType": "Default",
    }
    assert approval["change"]["before"] == before_settings()
    assert approval["change"]["after"] == target_settings()
    assert approval["change"]["wouldChange"] is True
    assert approval["rollbackRequired"] is True

    assert nested["preview"] is False
    assert nested["saveAndReimport"] is True
    assert nested["expectedProjectPath"] == PROJECT_PATH
    assert nested["expectedTextureAssetPath"] == "Assets/Avatar/Textures/Body.png"
    assert nested["expectedTextureAssetGuid"] == "a" * 32
    assert nested["expectedSourceFileDigest"] == "b" * 64
    assert nested["expectedSourceFileIdentityDigest"] == "d" * 64
    assert nested["expectedMetaFileDigest"] == "c" * 64
    assert nested["expectedMetaFileIdentityDigest"] == "e" * 64
    assert nested["expectedImporterType"] == "Default"
    assert nested["expectedImporterSettingsDigest"] == compute_settings_digest("Default", before_settings())
    assert nested["expectedTargetSettingsDigest"] == compute_settings_digest("Default", target_settings())


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update({"schema": "unexpected"}),
        lambda payload: payload.update({"preview": False}),
        lambda payload: payload.update({"verified": False}),
        lambda payload: payload.update({"changed": True}),
        lambda payload: payload.update({"saved": True}),
        lambda payload: payload.update({"reimported": True}),
        lambda payload: payload.update({"projectPath": str(Path("D:/OtherProject").resolve())}),
        lambda payload: payload.update({"textureAssetPath": "../Outside.png"}),
        lambda payload: payload.update({"textureAssetPath": "Packages/Body.png"}),
        lambda payload: payload.update({"textureAssetGuid": "a" * 31}),
        lambda payload: payload.update({"sourceFileDigestAfter": "d" * 64}),
        lambda payload: payload.update({"sourceFileLinkCount": 2}),
        lambda payload: payload.update({"sourceFileIdentityDigest": "z" * 64}),
        lambda payload: payload.update({"metaFileDigestAfter": "d" * 64}),
        lambda payload: payload.update({"metaFileLinkCount": 2}),
        lambda payload: payload.update({"metaFileIdentityDigest": "z" * 64}),
        lambda payload: payload.update({"importerSettingsDigestAfter": "d" * 64}),
        lambda payload: payload.update({"importerDirtyBefore": True}),
        lambda payload: payload.update({"importerDirtyAfter": True}),
        lambda payload: payload["targetSettings"].update({"quality": 81}),
        lambda payload: payload["beforeSettings"].update({"platformName": "Android"}),
        lambda payload: payload.update({"wouldChange": False}),
    ],
)
def test_authoritative_preview_rejects_writes_substitution_or_inconsistent_receipts(mutator) -> None:
    payload = preview_payload()
    mutator(payload)

    with pytest.raises(TextureImportSettingsError):
        bind_authoritative_preview(wrapper_arguments(), payload)


def test_noop_preview_is_valid_but_still_requires_the_supervised_apply_contract() -> None:
    wrapper = wrapper_arguments()
    wrapper["arguments"].update(
        {
            "maxTextureSize": 4096,
            "format": "automatic",
            "compression": "normal",
            "crunch": False,
            "quality": 50,
        }
    )
    payload = preview_payload(would_change=False)

    canonical, approval = bind_authoritative_preview(wrapper, payload)

    assert approval["change"]["wouldChange"] is False
    assert canonical["arguments"]["preview"] is False
    assert canonical["arguments"]["saveAndReimport"] is True


def test_csharp_domain_module_keeps_preview_zero_write_and_apply_exact() -> None:
    source = Path("Assets/VRCForge/Editor/TextureImportSettingsTool.cs").read_text(encoding="utf-8-sig")

    assert 'name: "vrc_set_texture_import_settings"' in source
    assert "TextureImporter.GetAtPath" in source
    assert "TextureImporter.IsDefaultPlatformTextureFormatValid" in source
    assert "TextureImporter.IsPlatformTextureFormatValid" in source
    assert "GetFileInformationByHandle" in source
    assert "CreateFile(" in source
    assert "desiredAccess: 0" in source
    assert "VerifyPathMatchesLease" in source
    assert "FileAccess.Read, FileShare.Read" in source
    assert "LeaseFileObjectMatches(metaLease)" in source
    assert "numberOfLinks != 1" in source
    assert "expectedSourceFileIdentityDigest" in source
    assert "expectedMetaFileIdentityDigest" in source
    assert "checkpointRestoreRequired" in source
    assert "cleanupRequired" in source
    assert "TryRestoreBeforeSettings" in source
    apply_source, restore_source = source.split("private static bool TryRestoreBeforeSettings", 1)
    assert apply_source.count(".SaveAndReimport();") == 1
    assert restore_source.count(".SaveAndReimport();") == 1
    assert "AssetDatabase.SaveAssets" not in source
    assert "AssetDatabase.Refresh" not in source
    assert "AssetDatabase.ImportAsset" not in source
    preview_prefix, after_preview_guard = source.split("if (preview)", 1)
    preview_branch = after_preview_guard.split("if (!wouldChange)", 1)[0]
    assert "SetPlatformTextureSettings" not in preview_branch
    assert "SaveAndReimport" not in preview_branch
    assert "sourceFileDigestAfter = ComputeFileSha256" in preview_prefix
    assert "metaFileDigestAfter = ComputeFileSha256" in preview_prefix
    assert source.index("if (preview)") < source.index("SetPlatformTextureSettings")


def test_disposable_unity_fixture_covers_the_authoritative_lifecycle() -> None:
    fixture_path = Path(
        "tests/fixtures/primitive_basis/texture_import_settings/TextureImportSettingsFixtureProbe.cs"
    )
    assert fixture_path.is_file()
    source = fixture_path.read_text(encoding="utf-8-sig")

    for required_stage in (
        "VerifyStructuredMutationFailureSignals",
        "cleanupRequired",
        "checkpointRestoreRequired",
        "checkpoint_restore_required",
        "preview source bytes",
        "preview meta bytes",
        "VerifySourceWriteDeniedByApplyLease",
        "CreateHardLinkW",
        "metadata hardlink accepted",
        "apply source bytes",
        "VerifyImporterReadback",
        "no-op meta bytes",
        "stale precondition accepted",
        "VRCFORGE_TEXTURE_IMPORT_SETTINGS_PROBE_OK",
        "EditorApplication.Exit(0)",
        "EditorApplication.Exit(1)",
    ):
        assert required_stage in source
