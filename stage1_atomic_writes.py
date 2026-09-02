"""Approval binding for the four Stage 1 Unity atomic Tools.

The Unity Core owns mutation and fresh readback.  This module only binds an
App approval to the exact Core preview receipt and rejects any drift before a
managed write is dispatched.
"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any


SCENE_SAVE_TOOL = "vrc_scene_save"
SCENE_TRANSITION_TOOL = "vrc_scene_transition"
TEXTURE_PATCH_TOOL = "vrc_texture_patch"
USER_ADJUSTMENT_HANDOFF_TOOL = "vrc_user_adjustment_handoff"

STAGE1_TOOL_SCHEMAS = {
    SCENE_SAVE_TOOL: "vrcforge.scene_save.v1",
    SCENE_TRANSITION_TOOL: "vrcforge.scene_transition.v1",
    TEXTURE_PATCH_TOOL: "vrcforge.texture_patch.v1",
    USER_ADJUSTMENT_HANDOFF_TOOL: "vrcforge.user_adjustment_handoff.v1",
}

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class Stage1AtomicWriteError(ValueError):
    pass


def build_wrapper_arguments(params: dict[str, Any], tool_name: str) -> dict[str, Any]:
    if tool_name not in STAGE1_TOOL_SCHEMAS:
        raise Stage1AtomicWriteError("Unknown Stage 1 atomic Unity tool.")
    wrapper = deepcopy(params or {})
    nested = wrapper.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper.get("params")
    if not isinstance(nested, dict):
        nested = {
            key: deepcopy(value)
            for key, value in wrapper.items()
            if key not in {"projectPath", "projectRoot", "executionTarget", "confirmation"}
        }
    for key in tuple(nested):
        wrapper.pop(key, None)
    wrapper.pop("params", None)
    wrapper.pop("tool_name", None)
    wrapper["toolName"] = tool_name
    wrapper["arguments"] = deepcopy(nested)
    return wrapper


def build_preview_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    request = {
        key: deepcopy(value)
        for key, value in dict(arguments or {}).items()
        if not key.startswith("expected") and key not in {"preview", "operationId"}
    }
    request["preview"] = True
    return request


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tool_name = str(wrapper_arguments.get("toolName") or "").strip()
    schema = STAGE1_TOOL_SCHEMAS.get(tool_name)
    if schema is None:
        raise Stage1AtomicWriteError("Unknown Stage 1 atomic Unity tool.")
    arguments = wrapper_arguments.get("arguments")
    if not isinstance(arguments, dict):
        raise Stage1AtomicWriteError("Stage 1 atomic tool arguments are required.")
    result = _payload(payload)
    for key, expected in (
        ("schema", schema),
        ("ok", True),
        ("preview", True),
        ("verified", True),
        ("mutationStarted", False),
        ("commitState", "not_started"),
    ):
        if result.get(key) != expected:
            raise Stage1AtomicWriteError(f"Stage 1 preview {key} is invalid.")
    project_path = _project_path(wrapper_arguments.get("projectPath"))
    if _project_path(result.get("projectPath")) != project_path:
        raise Stage1AtomicWriteError("Stage 1 preview changed the Unity project.")
    preview_digest = str(result.get("previewDigest") or "")
    if _DIGEST.fullmatch(preview_digest) is None:
        raise Stage1AtomicWriteError("Stage 1 preview digest is invalid.")

    # Core returns the exact fields it requires at apply time.  Copy only the
    # explicitly namespaced expectation object, never arbitrary response data.
    binding = result.get("applyBinding")
    if not isinstance(binding, dict) or not binding:
        raise Stage1AtomicWriteError("Stage 1 preview apply binding is missing.")
    if any(not str(key).startswith("expected") for key in binding):
        raise Stage1AtomicWriteError("Stage 1 preview apply binding is invalid.")

    prepared = deepcopy(wrapper_arguments)
    prepared["projectPath"] = str(project_path)
    prepared["toolName"] = tool_name
    prepared["arguments"] = {
        **deepcopy(arguments),
        **deepcopy(binding),
        "preview": False,
        "expectedProjectPath": str(project_path).replace("\\", "/"),
        "expectedPreviewDigest": preview_digest,
    }
    approval = {
        "schema": schema.removesuffix(".v1") + "_approval.v1",
        "operation": result.get("operation"),
        "projectPath": str(project_path),
        "previewDigest": preview_digest,
        "target": deepcopy(result.get("target")),
        "effect": deepcopy(result.get("effect")),
        "requiresExplicitUserApproval": True,
    }
    return prepared, approval


def validate_apply_result(arguments: dict[str, Any], payload: Any, tool_name: str) -> dict[str, Any]:
    schema = STAGE1_TOOL_SCHEMAS.get(tool_name)
    if schema is None:
        raise Stage1AtomicWriteError("Unknown Stage 1 atomic Unity tool.")
    result = _payload(payload)
    if result.get("schema") != schema or result.get("ok") is not True:
        raise Stage1AtomicWriteError("Stage 1 apply result schema is invalid.")
    if result.get("preview") is not False or result.get("verified") is not True:
        raise Stage1AtomicWriteError("Stage 1 apply did not return fresh verification.")
    if result.get("previewDigest") != arguments.get("expectedPreviewDigest"):
        raise Stage1AtomicWriteError("Stage 1 apply preview binding changed.")
    if result.get("commitState") not in {"committed", "not_started", "no_change"}:
        raise Stage1AtomicWriteError("Stage 1 apply commit state is ambiguous.")
    if result.get("commitState") == "committed" and result.get("mutationStarted") is not True:
        raise Stage1AtomicWriteError("Stage 1 apply mutation receipt is invalid.")
    if result.get("commitState") in {"not_started", "no_change"} and result.get("changed") is not False:
        raise Stage1AtomicWriteError("Stage 1 no-change receipt is invalid.")
    if not isinstance(result.get("readback"), dict):
        raise Stage1AtomicWriteError("Stage 1 apply fresh readback is missing.")
    return result


def _payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Stage1AtomicWriteError("Stage 1 Unity result is invalid.")
    data = value.get("data") if isinstance(value.get("data"), dict) else value
    if not isinstance(data, dict):
        raise Stage1AtomicWriteError("Stage 1 Unity result is invalid.")
    return data


def _project_path(value: Any) -> Path:
    text = str(value or "").strip()
    path = Path(text)
    if not text or not path.is_absolute():
        raise Stage1AtomicWriteError("projectPath must be an absolute Unity project path.")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise Stage1AtomicWriteError("projectPath is unavailable.") from exc
    if not (resolved / "Assets").is_dir():
        raise Stage1AtomicWriteError("projectPath is not a Unity project.")
    return resolved
