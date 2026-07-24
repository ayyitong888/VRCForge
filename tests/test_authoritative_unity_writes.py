from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

import authoritative_unity_writes as writes
from material_shader_assignment import TOOL_NAME as MATERIAL_TOOL
from scene_object_copy import DUPLICATE_TOOL_NAME, PREFAB_TOOL_NAME
from texture_import_settings import TOOL_NAME as TEXTURE_TOOL


def test_registry_contains_only_the_guarded_write_protocols() -> None:
    assert writes.AUTHORITATIVE_UNITY_WRITE_TOOLS == {
        MATERIAL_TOOL,
        DUPLICATE_TOOL_NAME,
        PREFAB_TOOL_NAME,
        TEXTURE_TOOL,
    }


def test_unknown_write_preserves_existing_request_and_preview_semantics() -> None:
    request = {"toolName": "vrc_apply_blendshapes", "arguments": {"preview": False}}
    preview = {"summary": "existing"}

    prepared_request, prepared_preview = writes.prepare_authoritative_unity_write(
        deepcopy(request),
        deepcopy(preview),
        lambda _tool, _arguments: pytest.fail("unknown writes must not invoke a preview"),
    )

    assert prepared_request == request
    assert prepared_preview == preview


def test_authoritative_mapping_canonicalizes_project_and_replaces_caller_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    observed: dict[str, object] = {}

    class DomainError(ValueError):
        pass

    def build_preview(arguments: dict) -> dict:
        observed["builderArguments"] = deepcopy(arguments)
        return {"selector": arguments["selector"], "preview": True}

    def bind_preview(request: dict, payload: object) -> tuple[dict, dict]:
        observed["boundRequest"] = deepcopy(request)
        observed["payload"] = payload
        canonical = deepcopy(request)
        canonical["arguments"] = {
            "selector": request["arguments"]["selector"],
            "expectedProjectPath": request["arguments"]["expectedProjectPath"],
            "preview": False,
        }
        return canonical, {"schema": "approval.v1"}

    tool_name = "vrc_test_authoritative_write"
    monkeypatch.setitem(
        writes._SPECS,
        tool_name,
        writes.AuthoritativeUnityWriteSpec(
            tool_name=tool_name,
            request_error="request invalid",
            bridge_error="preview unavailable",
            receipt_error="receipt invalid",
            domain_error=DomainError,
            build_preview=build_preview,
            bind_preview=bind_preview,
        ),
    )
    invocations: list[tuple[str, dict]] = []

    prepared, preview = writes.prepare_authoritative_unity_write(
        {
            "projectPath": str(project / "."),
            "toolName": tool_name,
            "arguments": {
                "selector": "Body",
                "expectedProjectPath": "D:/spoofed",
                "expectedReceipt": "spoofed",
            },
        },
        {"spoofed": True},
        lambda actual_tool, arguments: (
            invocations.append((actual_tool, deepcopy(arguments)))
            or {"schema": "live.v1"}
        ),
    )

    canonical_project = str(project.resolve())
    assert invocations == [
        (
            tool_name,
            {
                "selector": "Body",
                "preview": True,
                "expectedProjectPath": canonical_project,
            },
        )
    ]
    assert observed["builderArguments"] == {
        "selector": "Body",
        "expectedProjectPath": "D:/spoofed",
        "expectedReceipt": "spoofed",
    }
    assert observed["payload"] == {"schema": "live.v1"}
    assert prepared["projectPath"] == canonical_project
    assert prepared["arguments"] == {
        "selector": "Body",
        "expectedProjectPath": canonical_project,
        "preview": False,
    }
    assert preview == {"schema": "approval.v1"}


def test_transport_failure_returns_only_the_spec_safe_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    spec = writes._SPECS[MATERIAL_TOOL]

    with pytest.raises(writes.AuthoritativeUnityWriteError) as captured:
        writes.prepare_authoritative_unity_write(
            {
                "projectPath": str(project),
                "toolName": MATERIAL_TOOL,
                "arguments": {"shaderName": "Project/Toon", "materialAssetPath": "Assets/A.mat"},
            },
            None,
            lambda _tool, _arguments: (_ for _ in ()).throw(
                RuntimeError("credential at C:/private/project")
            ),
        )

    assert captured.value.status_code == 409
    assert str(captured.value) == spec.bridge_error
    assert "private" not in str(captured.value)


def test_unexpected_receipt_parser_failure_returns_only_the_spec_safe_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    original = writes._SPECS[MATERIAL_TOOL]
    monkeypatch.setitem(
        writes._SPECS,
        MATERIAL_TOOL,
        writes.AuthoritativeUnityWriteSpec(
            tool_name=original.tool_name,
            request_error=original.request_error,
            bridge_error=original.bridge_error,
            receipt_error=original.receipt_error,
            domain_error=original.domain_error,
            build_preview=original.build_preview,
            bind_preview=lambda _request, _payload: (_ for _ in ()).throw(
                RuntimeError("parser path C:/private/project")
            ),
        ),
    )

    with pytest.raises(writes.AuthoritativeUnityWriteError) as captured:
        writes.prepare_authoritative_unity_write(
            {
                "projectPath": str(project),
                "toolName": MATERIAL_TOOL,
                "arguments": {"shaderName": "Project/Toon", "materialAssetPath": "Assets/A.mat"},
            },
            None,
            lambda _tool, _arguments: {"schema": "live.v1"},
        )

    assert captured.value.status_code == 409
    assert str(captured.value) == original.receipt_error
    assert "private" not in str(captured.value)


@pytest.mark.parametrize("project_path", ["", "relative/project"])
def test_authoritative_mapping_rejects_non_absolute_projects(project_path: str) -> None:
    with pytest.raises(writes.AuthoritativeUnityWriteError, match="absolute") as captured:
        writes.prepare_authoritative_unity_write(
            {
                "projectPath": project_path,
                "toolName": MATERIAL_TOOL,
                "arguments": {},
            },
            None,
            lambda _tool, _arguments: {},
        )

    assert captured.value.status_code == 400
