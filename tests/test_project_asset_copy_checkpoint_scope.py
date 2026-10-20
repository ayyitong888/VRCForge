"""Exact create-new checkpoint scope for authoritative project-asset copies."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from project_asset_copy import (
    GENERATED_ROOT,
    RESULT_SCHEMA,
    OPERATION,
    _batch_digest,
    bind_authoritative_preview,
    build_wrapper_arguments,
    compute_preview_digest,
)
from test_project_asset_copy import missing_folder_preview, preview_payload
from unity_asset_copy_checkpoint import prepare_asset_copy_checkpoint


def _project(tmp_path: Path, *, generated_root: bool = True) -> Path:
    root = tmp_path / "Project"
    (root / "Assets").mkdir(parents=True)
    if generated_root:
        (root / GENERATED_ROOT).mkdir()
    return root


def _single(
    project: Path,
    *,
    source: str = "Assets/Avatar/Body.mat",
    destination: str = f"{GENERATED_ROOT}/Body_Copy.mat",
    payload: dict | None = None,
) -> tuple[dict, dict]:
    preview = deepcopy(payload) if payload is not None else preview_payload()
    preview["source"].update(assetPath=source, mainAssetType="UnityEngine.Material")
    preview["target"]["assetPath"] = destination
    preview["target"]["parentFolderPath"] = str(Path(destination).parent).replace("\\", "/")
    preview["previewDigest"] = compute_preview_digest(preview)
    wrapper = build_wrapper_arguments({
        "projectPath": str(project),
        "sourceAssetPath": source,
        "destinationAssetPath": destination,
    })
    canonical, _approval = bind_authoritative_preview(wrapper, preview)
    return canonical, preview


def _run(project: Path, arguments: dict, preview: dict, *, refreshed=None, prepared=None):
    calls = []

    def revalidate(request, supplied_preview):
        calls.append(("revalidate", deepcopy(request), supplied_preview))
        if refreshed is not None:
            return deepcopy(refreshed), preview
        return bind_authoritative_preview(request, deepcopy(preview))

    def prepare(root, checkpoint_asset_paths=None, *, checkpoint_files):
        calls.append(("prepare", root, list(checkpoint_files)))
        return deepcopy(prepared) if prepared is not None else {"ok": True, "saved": False}

    result = prepare_asset_copy_checkpoint(
        project,
        arguments,
        revalidate=revalidate,
        prepare=prepare,
    )
    return result, calls


def test_single_copy_checkpoints_only_new_destination_and_meta(tmp_path: Path) -> None:
    project = _project(tmp_path)
    canonical, preview = _single(project)
    canonical.update({
        "archiveFiles": ["Assets/User.mat"],
        "assetFootprint": {"archiveAbsentFiles": ["Assets/Forged.mat"]},
    })
    before = deepcopy(canonical)
    result, calls = _run(project, canonical, preview)

    assert result["ok"] is True, result
    assert result["assetFootprint"] == {
        "schema": "vrcforge.asset_checkpoint_footprint.v1",
        "archiveFiles": [],
        "archiveAbsentFiles": [
            f"{GENERATED_ROOT}/Body_Copy.mat",
            f"{GENERATED_ROOT}/Body_Copy.mat.meta",
        ],
        "archiveAbsentDirectories": [],
    }
    assert calls[-1] == (
        "prepare",
        project,
        [f"{GENERATED_ROOT}/Body_Copy.mat", f"{GENERATED_ROOT}/Body_Copy.mat.meta"],
    )
    assert not any("Avatar/Body.mat" in str(value) for value in calls[-1])
    assert canonical == before


def test_missing_parents_include_only_new_directory_metadata_and_directories(tmp_path: Path) -> None:
    project = _project(tmp_path)
    preview = missing_folder_preview()
    destination = preview["target"]["assetPath"]
    canonical, preview = _single(
        project,
        source="Assets/Avatar/Original.controller",
        destination=destination,
        payload=preview,
    )
    result, calls = _run(project, canonical, preview)

    assert result["ok"] is True, result
    footprint = result["assetFootprint"]
    assert footprint["archiveAbsentDirectories"] == [
        f"{GENERATED_ROOT}/Avatar",
        f"{GENERATED_ROOT}/Avatar/Materials",
    ]
    assert footprint["archiveAbsentFiles"] == [
        f"{GENERATED_ROOT}/Avatar.meta",
        f"{GENERATED_ROOT}/Avatar/Materials.meta",
        destination,
        destination + ".meta",
    ]
    assert calls[-1][2] == footprint["archiveAbsentFiles"]


def test_absent_generated_root_is_included_as_directory_and_meta(tmp_path: Path) -> None:
    project = _project(tmp_path, generated_root=False)
    canonical, preview = _single(project, payload=preview_payload(generated_root_exists=False))
    result, calls = _run(project, canonical, preview)
    assert result["ok"] is True, result
    assert result["assetFootprint"]["archiveAbsentDirectories"] == [GENERATED_ROOT]
    assert result["assetFootprint"]["archiveAbsentFiles"] == [
        GENERATED_ROOT + ".meta",
        f"{GENERATED_ROOT}/Body_Copy.mat",
        f"{GENERATED_ROOT}/Body_Copy.mat.meta",
    ]
    assert calls[-1][2] == result["assetFootprint"]["archiveAbsentFiles"]


def test_batch_collects_every_authoritative_new_destination(tmp_path: Path) -> None:
    project = _project(tmp_path)
    previews = []
    rows = []
    for index in range(2):
        source = f"Assets/Avatar/Source{index}.mat"
        destination = f"{GENERATED_ROOT}/Copy{index}.mat"
        one = preview_payload()
        one["source"].update(assetPath=source, mainAssetType="UnityEngine.Material")
        one["target"]["assetPath"] = destination
        one["previewDigest"] = compute_preview_digest(one)
        previews.append(one)
        rows.append({"sourceAssetPath": source, "destinationAssetPath": destination})
    payload = {
        "schema": RESULT_SCHEMA,
        "operation": OPERATION,
        "batch": True,
        "ok": True,
        "preview": True,
        "verified": True,
        "changed": False,
        "saved": False,
        "cleanupRequired": False,
        "mutationCount": 0,
        "copies": previews,
        "previewDigest": _batch_digest(previews),
    }
    wrapper = build_wrapper_arguments({"projectPath": str(project), "copies": rows})
    canonical, _approval = bind_authoritative_preview(wrapper, payload)
    result, calls = _run(project, canonical, payload)
    assert result["ok"] is True, result
    assert result["assetFootprint"]["archiveAbsentFiles"] == [
        f"{GENERATED_ROOT}/Copy0.mat",
        f"{GENERATED_ROOT}/Copy0.mat.meta",
        f"{GENERATED_ROOT}/Copy1.mat",
        f"{GENERATED_ROOT}/Copy1.mat.meta",
    ]
    assert result["assetFootprint"]["archiveAbsentDirectories"] == []
    assert calls[-1][2] == result["assetFootprint"]["archiveAbsentFiles"]


def test_authoritative_preview_drift_fails_before_checkpoint(tmp_path: Path) -> None:
    project = _project(tmp_path)
    canonical, preview = _single(project)
    preview["source"]["fileDigest"] = "f" * 64
    preview["previewDigest"] = compute_preview_digest(preview)
    result, calls = _run(project, canonical, preview)
    assert result["ok"] is False and result["code"] == "checkpoint_scope_unresolved"
    assert [call[0] for call in calls] == ["revalidate"]


def test_fresh_canonical_arguments_must_equal_approved_arguments(tmp_path: Path) -> None:
    project = _project(tmp_path)
    canonical, preview = _single(project)
    changed = deepcopy(canonical)
    changed["arguments"]["expectedPreviewDigest"] = "f" * 64
    result, calls = _run(project, canonical, preview, refreshed=changed)
    assert result["ok"] is False and "identity changed" in result["error"]
    assert [call[0] for call in calls] == ["revalidate"]


def test_destination_appearing_after_preview_fails_before_checkpoint(tmp_path: Path) -> None:
    project = _project(tmp_path)
    canonical, preview = _single(project)
    destination = project / canonical["arguments"]["destinationAssetPath"]
    destination.write_text("foreign", encoding="utf-8")
    result, calls = _run(project, canonical, preview)
    assert result["ok"] is False and "appeared" in result["error"]
    assert [call[0] for call in calls] == ["revalidate"]


@pytest.mark.parametrize("mutation", ["tool", "project", "missing_project", "absent", "type"])
def test_noncanonical_or_unsupported_approved_copy_fails_closed(tmp_path: Path, mutation: str) -> None:
    project = _project(tmp_path)
    canonical, preview = _single(project)
    refreshed = deepcopy(canonical)
    if mutation == "tool":
        canonical["toolName"] = "vrc_other"
        refreshed = canonical
    elif mutation == "project":
        canonical["projectPath"] = str(project / "Other")
        refreshed = canonical
    elif mutation == "missing_project":
        canonical.pop("projectPath")
        refreshed = canonical
    elif mutation == "absent":
        canonical["arguments"]["expectedDestinationAbsent"] = False
        refreshed = canonical
    else:
        canonical["arguments"]["destinationAssetPath"] = f"{GENERATED_ROOT}/Body_Copy.cs"
        refreshed = canonical
    result, calls = _run(project, canonical, preview, refreshed=refreshed)
    assert result["ok"] is False and result["code"] == "checkpoint_scope_unresolved"
    assert not any(call[0] == "prepare" for call in calls)


def test_prepare_failure_is_preserved_without_footprint_offer(tmp_path: Path) -> None:
    project = _project(tmp_path)
    canonical, preview = _single(project)
    result, calls = _run(
        project,
        canonical,
        preview,
        prepared={"ok": False, "code": "core_busy", "error": "Core is compiling."},
    )
    assert result == {"ok": False, "code": "core_busy", "error": "Core is compiling."}
    assert [call[0] for call in calls] == ["revalidate", "prepare"]
