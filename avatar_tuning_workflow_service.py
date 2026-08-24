from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable, MutableMapping, Protocol

from prepared_blendshape_writes import (
    canonical_sha256 as blendshape_evidence_sha256,
    require_exact_evidence as require_exact_blendshape_evidence,
)
from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    install_prepared_calls,
    prepared_call,
    prepared_evidence,
)


class AvatarTuningError(RuntimeError):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.status_code = status_code


class RequestSupervisedUnityWritePort(Protocol):
    def __call__(
        self,
        target_tool: str,
        request: Any,
        *,
        reason: str,
        preview_callback: Callable[[], dict[str, Any]] | None = None,
        allow_mock_execute: bool = False,
        extra_arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class PreparedWritePort(Protocol):
    def __call__(
        self,
        arguments: dict[str, Any],
        preview: Any,
    ) -> tuple[dict[str, Any], Any]: ...


@dataclass(frozen=True, slots=True)
class AvatarTuningWorkflowPorts:
    """Frozen read, persistence and supervised-write capabilities for tuning."""

    scan_scene_avatars: Callable[[Any], dict[str, Any]]
    read_avatars: Callable[[Any], dict[str, Any]]
    read_avatar_blendshapes: Callable[[Any], dict[str, Any]]
    run_face_tuning: Callable[[Any, bool], dict[str, Any]]
    preview_manual_blendshapes: Callable[[Any], dict[str, Any]]
    preview_agent_blendshape_apply: Callable[[dict[str, Any]], dict[str, Any]]
    request_supervised_write: RequestSupervisedUnityWritePort

    load_history: Callable[[], dict[str, Any]]
    load_presets: Callable[[], dict[str, Any]]
    load_locked_blendshapes: Callable[[str], list[dict[str, Any]]]
    current_avatar_path: Callable[[], str]
    create_preset: Callable[[Any], dict[str, Any]]
    rename_preset: Callable[[str, Any], dict[str, Any]]
    duplicate_preset: Callable[[str, Any], dict[str, Any]]
    delete_preset: Callable[[str], dict[str, Any]]
    update_locks: Callable[[Any], dict[str, Any]]
    ai_select_locks: Callable[[Any], dict[str, Any]]
    preview_saved_history: Callable[[str, Any], dict[str, Any]]
    preview_saved_preset: Callable[[str, Any], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class AvatarTuningApprovedWriteHandlers:
    """Prepared Unity writers held only by the supervised approval registry."""

    prepare_manual_apply: PreparedWritePort
    execute_manual_apply: Callable[[dict[str, Any]], dict[str, Any]]
    prepare_manual_undo: PreparedWritePort
    execute_manual_undo: Callable[[dict[str, Any]], dict[str, Any]]
    prepare_face_tuning: PreparedWritePort
    execute_face_tuning: Callable[[dict[str, Any]], dict[str, Any]]
    prepare_reapply_history: PreparedWritePort
    execute_reapply_history: Callable[[dict[str, Any]], dict[str, Any]]
    prepare_apply_preset: PreparedWritePort
    execute_apply_preset: Callable[[dict[str, Any]], dict[str, Any]]


class AvatarTuningWorkflowService:
    """Own face/avatar tuning read, local-state and approval-request orchestration.

    The service can read tuning stores and create approval requests, but it owns
    neither a Unity writer nor an approval/checkpoint store. Persisted preset and
    lock mutations remain explicit supplied capabilities because they are local
    app state, not Unity project writes.
    """

    def __init__(self, ports: AvatarTuningWorkflowPorts) -> None:
        self._ports = ports

    def scan_scene_avatars(self, request: Any) -> dict[str, Any]:
        return self._ports.scan_scene_avatars(request)

    def read_avatars(self, request: Any) -> dict[str, Any]:
        return self._ports.read_avatars(request)

    def read_avatar_blendshapes(self, request: Any) -> dict[str, Any]:
        return self._ports.read_avatar_blendshapes(request)

    def plan_face_tuning(self, request: Any) -> dict[str, Any]:
        return self._ports.run_face_tuning(request, False)

    def preview_agent_blendshape_apply(
        self,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._ports.preview_agent_blendshape_apply(params or {})

    def request_face_tuning(self, request: Any) -> dict[str, Any]:
        return self._ports.request_supervised_write(
            "vrcforge_run_face_tuning",
            request,
            reason="Apply the generated face-tuning plan to the selected Unity avatar.",
            preview_callback=lambda: self._ports.run_face_tuning(request, True),
            allow_mock_execute=True,
        )

    def request_manual_blendshape_apply(self, request: Any) -> dict[str, Any]:
        return self._ports.request_supervised_write(
            "vrcforge_apply_blendshapes",
            request,
            reason="Apply the selected Blendshape adjustments to the Unity avatar.",
            preview_callback=lambda: self._ports.preview_manual_blendshapes(request),
            allow_mock_execute=True,
        )

    def request_manual_blendshape_undo(self, request: Any) -> dict[str, Any]:
        return self._ports.request_supervised_write(
            "vrcforge_undo_blendshapes",
            request,
            reason="Restore the previous approved Blendshape values for the selected avatar.",
        )

    def list_tuning_history(self, avatar_path: str | None = None) -> dict[str, Any]:
        store = self._ports.load_history()
        records = list(store.get("records") or [])
        if avatar_path:
            records = [
                record
                for record in records
                if (
                    record.get("avatar_path") == avatar_path
                    or record.get("avatar_name") == avatar_path
                )
            ]
        return {"ok": True, "records": records, "count": len(records)}

    def request_reapply_tuning_history(self, history_id: str, request: Any) -> dict[str, Any]:
        return self._ports.request_supervised_write(
            "vrcforge_reapply_tuning_history",
            request,
            reason="Reapply the selected saved face-tuning history record to Unity.",
            preview_callback=lambda: self._ports.preview_saved_history(history_id, request),
            allow_mock_execute=True,
            extra_arguments={"historyId": history_id},
        )

    def list_tuning_presets(self, avatar_path: str | None = None) -> dict[str, Any]:
        store = self._ports.load_presets()
        presets = list(store.get("presets") or [])
        if avatar_path:
            presets = [
                preset
                for preset in presets
                if (
                    preset.get("avatar_path") == avatar_path
                    or preset.get("avatar_name") == avatar_path
                )
            ]
        return {"ok": True, "presets": presets, "count": len(presets)}

    def create_tuning_preset(self, request: Any) -> dict[str, Any]:
        return self._ports.create_preset(request)

    def request_apply_tuning_preset(self, preset_id: str, request: Any) -> dict[str, Any]:
        return self._ports.request_supervised_write(
            "vrcforge_apply_tuning_preset",
            request,
            reason="Apply the selected saved face-tuning preset to Unity.",
            preview_callback=lambda: self._ports.preview_saved_preset(preset_id, request),
            allow_mock_execute=True,
            extra_arguments={"presetId": preset_id},
        )

    def rename_tuning_preset(self, preset_id: str, request: Any) -> dict[str, Any]:
        return self._ports.rename_preset(preset_id, request)

    def duplicate_tuning_preset(self, preset_id: str, request: Any) -> dict[str, Any]:
        return self._ports.duplicate_preset(preset_id, request)

    def delete_tuning_preset(self, preset_id: str) -> dict[str, Any]:
        return self._ports.delete_preset(preset_id)

    def read_tuning_locks(self, avatar_path: str | None = None) -> dict[str, Any]:
        resolved_avatar = avatar_path or self._ports.current_avatar_path()
        locked = self._ports.load_locked_blendshapes(resolved_avatar)
        return {
            "ok": True,
            "avatarPath": resolved_avatar,
            "lockedBlendshapes": locked,
            "count": len(locked),
        }

    def update_tuning_locks(self, request: Any) -> dict[str, Any]:
        return self._ports.update_locks(request)

    def ai_select_tuning_locks(self, request: Any) -> dict[str, Any]:
        return self._ports.ai_select_locks(request)


@dataclass(frozen=True, slots=True)
class AvatarTuningStorePaths:
    history: Path
    presets: Path
    locks: Path


@dataclass(frozen=True, slots=True)
class AvatarTuningStorePorts:
    paths: Callable[[], AvatarTuningStorePaths]
    lock: Any
    current_avatar_path: Callable[[], str]
    now_utc: Callable[[], datetime]
    emit_log: Callable[[str, str, str, dict[str, Any]], None]


def _request_value(request: Any, name: str, default: Any = None) -> Any:
    if isinstance(request, dict):
        return request.get(name, default)
    return getattr(request, name, default)


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


class AvatarTuningStoreService:
    """Own tuning history, preset and lock JSON stores.

    Each save owns one temporary file under the configured store directory and
    atomically replaces exactly one destination while holding the supplied
    app-lifetime store lock. No Unity, provider, approval or transport authority
    is held by this owner.
    """

    HISTORY_DEFAULT = {
        "type": "blendshape_tuning_history",
        "version": "0.1",
        "records": [],
    }
    PRESETS_DEFAULT = {
        "type": "blendshape_tuning_presets",
        "version": "0.1",
        "presets": [],
    }
    LOCKS_DEFAULT = {
        "type": "blendshape_tuning_locks",
        "version": "0.1",
        "avatars": {},
    }

    def __init__(self, ports: AvatarTuningStorePorts) -> None:
        self._ports = ports

    def _load(self, path: Path, default_payload: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return _json_clone(default_payload)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Tuning store is not valid JSON: {path}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Tuning store must be a JSON object: {path}")
        merged = _json_clone(default_payload)
        merged.update(payload)
        return merged

    def _save(self, path: Path, payload: dict[str, Any]) -> None:
        with self._ports.lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_suffix(path.suffix + ".tmp")
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temp_path.replace(path)

    def timestamp(self) -> str:
        return self._ports.now_utc().isoformat(timespec="seconds")

    def make_id(self, prefix: str) -> str:
        return f"{prefix}_{self._ports.now_utc().strftime('%Y%m%d_%H%M%S_%f')}"

    def load_history(self) -> dict[str, Any]:
        return self._load(self._ports.paths().history, self.HISTORY_DEFAULT)

    def load_presets(self) -> dict[str, Any]:
        return self._load(self._ports.paths().presets, self.PRESETS_DEFAULT)

    def load_locks(self) -> dict[str, Any]:
        return self._load(self._ports.paths().locks, self.LOCKS_DEFAULT)

    @staticmethod
    def normalize_locked_item(item: Any) -> dict[str, str] | None:
        if not isinstance(item, dict):
            return None
        renderer_path = str(
            item.get("rendererPath") or item.get("renderer_path") or ""
        ).strip()
        blendshape_name = str(
            item.get("blendshapeName")
            or item.get("blendshape_name")
            or item.get("blendshape")
            or ""
        ).strip()
        if not blendshape_name:
            return None
        return {
            "rendererPath": renderer_path,
            "blendshapeName": blendshape_name,
        }

    @classmethod
    def normalize_locked_list(cls, items: list[Any]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in items or []:
            normalized_item = cls.normalize_locked_item(item)
            if normalized_item is None:
                continue
            key = (
                normalized_item["rendererPath"],
                normalized_item["blendshapeName"],
            )
            if key in seen:
                continue
            seen.add(key)
            normalized.append(normalized_item)
        return normalized

    def load_locked_blendshapes(
        self,
        avatar_path: str | None,
    ) -> list[dict[str, str]]:
        if not avatar_path:
            return []
        store = self.load_locks()
        avatars = store.get("avatars") if isinstance(store.get("avatars"), dict) else {}
        return self.normalize_locked_list(avatars.get(avatar_path) or [])

    def update_locks(self, request: Any) -> dict[str, Any]:
        avatar_path = str(
            _request_value(request, "avatar_path")
            or self._ports.current_avatar_path()
            or ""
        ).strip()
        if not avatar_path:
            raise AvatarTuningError(
                "avatar_path is required before updating locked Blendshapes."
            )
        locked = self.normalize_locked_list(
            list(_request_value(request, "locked_blendshapes", []) or [])
        )
        store = self.load_locks()
        avatars = store.get("avatars") if isinstance(store.get("avatars"), dict) else {}
        avatars[avatar_path] = locked
        store["avatars"] = avatars
        self._save(self._ports.paths().locks, store)
        self._ports.emit_log(
            "info",
            "blendshape",
            "Locked Blendshape list updated.",
            {"avatarPath": avatar_path, "count": len(locked)},
        )
        return {
            "ok": True,
            "avatarPath": avatar_path,
            "lockedBlendshapes": locked,
            "count": len(locked),
        }

    def save_history_record(self, record: dict[str, Any]) -> dict[str, Any]:
        store = self.load_history()
        records = list(store.get("records") or [])
        records.append(record)
        store["records"] = records[-200:]
        self._save(self._ports.paths().history, store)
        return record

    def find_history(self, history_id: str) -> dict[str, Any]:
        for record in self.load_history().get("records") or []:
            if record.get("id") == history_id:
                return record
        raise RuntimeError(f"Tuning history record was not found: {history_id}")

    def find_preset(self, preset_id: str) -> dict[str, Any]:
        for preset in self.load_presets().get("presets") or []:
            if preset.get("id") == preset_id:
                return preset
        raise RuntimeError(f"Tuning preset was not found: {preset_id}")

    @staticmethod
    def trim_presets_for_avatar(
        presets: list[dict[str, Any]],
        max_presets: int,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(max_presets or 10), 100))
        grouped: dict[str, list[dict[str, Any]]] = {}
        ordered_keys: list[str] = []
        for preset in presets:
            avatar_key = str(
                preset.get("avatar_path")
                or preset.get("avatar_name")
                or "__global__"
            )
            if avatar_key not in grouped:
                grouped[avatar_key] = []
                ordered_keys.append(avatar_key)
            grouped[avatar_key].append(preset)
        trimmed: list[dict[str, Any]] = []
        for avatar_key in ordered_keys:
            avatar_presets = grouped[avatar_key]
            if len(avatar_presets) > safe_limit:
                avatar_presets = avatar_presets[-safe_limit:]
            trimmed.extend(avatar_presets)
        return trimmed

    def create_preset(self, request: Any) -> dict[str, Any]:
        history = self.find_history(str(_request_value(request, "history_id") or ""))
        name = str(_request_value(request, "name") or "").strip()
        if not name:
            raise RuntimeError("Preset name is required.")
        preset = {
            "id": self.make_id("preset"),
            "name": name,
            "created_at": self.timestamp(),
            "avatar_name": history.get("avatar_name", ""),
            "avatar_path": history.get("avatar_path", ""),
            "source_history_id": history.get("id", ""),
            "user_prompt": history.get("user_prompt", ""),
            "provider": history.get("provider", ""),
            "provider_id": history.get("provider_id", ""),
            "model": history.get("model", ""),
            "tags": [
                str(tag).strip()
                for tag in (_request_value(request, "tags", []) or [])
                if str(tag).strip()
            ],
            "description": str(
                _request_value(request, "description") or ""
            ).strip(),
            "apply_mode": "after_values",
            "changes": list(history.get("changes") or []),
        }
        store = self.load_presets()
        presets = list(store.get("presets") or [])
        presets.append(preset)
        presets = self.trim_presets_for_avatar(
            presets,
            int(_request_value(request, "max_presets", 10) or 10),
        )
        store["presets"] = presets
        self._save(self._ports.paths().presets, store)
        self._ports.emit_log(
            "success",
            "preset",
            "Tuning preset saved.",
            {"presetId": preset["id"], "name": preset["name"]},
        )
        return {"ok": True, "preset": preset, "presets": presets}

    def rename_preset(self, preset_id: str, request: Any) -> dict[str, Any]:
        name = str(_request_value(request, "name") or "").strip()
        if not name:
            raise RuntimeError("Preset name is required.")
        store = self.load_presets()
        presets = list(store.get("presets") or [])
        for preset in presets:
            if preset.get("id") == preset_id:
                preset["name"] = name
                preset["updated_at"] = self.timestamp()
                self._save(
                    self._ports.paths().presets,
                    {**store, "presets": presets},
                )
                return {"ok": True, "preset": preset, "presets": presets}
        raise RuntimeError(f"Tuning preset was not found: {preset_id}")

    def duplicate_preset(self, preset_id: str, request: Any) -> dict[str, Any]:
        source = self.find_preset(preset_id)
        duplicate = _json_clone(source)
        duplicate["id"] = self.make_id("preset")
        duplicate["name"] = str(
            _request_value(request, "name")
            or f"{source.get('name', 'preset')}_copy"
        ).strip()
        duplicate["created_at"] = self.timestamp()
        duplicate["source_preset_id"] = source.get("id", "")
        store = self.load_presets()
        presets = list(store.get("presets") or [])
        presets.append(duplicate)
        presets = self.trim_presets_for_avatar(
            presets,
            int(_request_value(request, "max_presets", 10) or 10),
        )
        store["presets"] = presets
        self._save(self._ports.paths().presets, store)
        return {"ok": True, "preset": duplicate, "presets": presets}

    def delete_preset(self, preset_id: str) -> dict[str, Any]:
        store = self.load_presets()
        presets = list(store.get("presets") or [])
        remaining = [preset for preset in presets if preset.get("id") != preset_id]
        if len(remaining) == len(presets):
            raise AvatarTuningError(f"Tuning preset was not found: {preset_id}")
        store["presets"] = remaining
        self._save(self._ports.paths().presets, store)
        return {
            "ok": True,
            "deletedPresetId": preset_id,
            "presets": remaining,
        }

    def mark_history_applied(self, history_id: str) -> None:
        store = self.load_history()
        records = list(store.get("records") or [])
        for record in records:
            if record.get("id") == history_id:
                record["applied"] = True
                record["last_applied_at"] = self.timestamp()
                break
        store["records"] = records
        self._save(self._ports.paths().history, store)

    def mark_preset_applied(self, preset_id: str) -> None:
        store = self.load_presets()
        presets = list(store.get("presets") or [])
        for preset in presets:
            if preset.get("id") == preset_id:
                preset["last_applied_at"] = self.timestamp()
                preset["apply_count"] = int(preset.get("apply_count") or 0) + 1
                break
        store["presets"] = presets
        self._save(self._ports.paths().presets, store)


@dataclass(frozen=True, slots=True)
class AvatarTuningLiveContext:
    settings: Any
    avatar_name: str
    avatar_path: str
    allowed_targets: dict[tuple[str, str], dict[str, Any]]
    locked_blendshapes: list[dict[str, Any]]
    using_mock_execute: bool = False
    selected_avatar: Any = None


@dataclass(frozen=True, slots=True)
class PreparedFaceTuningState:
    context: AvatarTuningLiveContext
    plan: dict[str, Any]
    direct_adjustments: list[dict[str, Any]]
    change_preview: list[dict[str, Any]]
    undo_items: list[dict[str, Any]]
    preview: dict[str, Any]
    apply_payload: str
    export_source: str
    reference_context: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AvatarTuningPreparedPorts:
    parse_manual_arguments: Callable[[dict[str, Any]], dict[str, Any]]
    parse_mock_execute: Callable[[dict[str, Any]], bool]
    make_prepare_error: Callable[[str, int], Exception]
    resolve_write_settings: Callable[[dict[str, Any]], Any]
    resolve_live_context: Callable[
        [dict[str, Any], str | None],
        AvatarTuningLiveContext,
    ]
    invoke_unity: Callable[[Any, str, dict[str, Any]], Any]
    serialize_result: Callable[[Any], Any]
    serialize_avatar: Callable[[AvatarTuningLiveContext], dict[str, Any]]
    verify_live_changes: Callable[
        [AvatarTuningLiveContext, list[dict[str, Any]]],
        list[dict[str, Any]],
    ]
    remember_avatar: Callable[[str, str], None]
    prepare_face_state: Callable[[dict[str, Any]], PreparedFaceTuningState]
    face_adjustments_from_plan: Callable[
        [dict[str, Any]],
        tuple[dict[str, Any], list[dict[str, Any]]],
    ]
    render_face_summary: Callable[
        [
            dict[str, Any],
            AvatarTuningLiveContext,
            dict[str, Any],
            Any,
            Any,
        ],
        Any,
    ]
    save_face_artifacts: Callable[
        [
            dict[str, Any],
            AvatarTuningLiveContext,
            dict[str, Any],
            Any,
            Any,
            Any,
        ],
        Any,
    ]
    save_face_history: Callable[
        [
            dict[str, Any],
            AvatarTuningLiveContext,
            dict[str, Any],
            Any,
            Any,
            Any,
            Any,
        ],
        Any,
    ]


class AvatarTuningUndoStore:
    """Own the bounded per-avatar undo stack and its approval-time CAS."""

    def __init__(
        self,
        stacks: MutableMapping[str, list[list[dict[str, Any]]]],
        lock: Any,
    ) -> None:
        self._stacks = stacks
        self._lock = lock

    def push(self, avatar_path: str, adjustments: list[dict[str, Any]]) -> int:
        with self._lock:
            stack = self._stacks.setdefault(avatar_path, [])
            stack.append(copy.deepcopy(adjustments))
            if len(stack) > 12:
                del stack[0]
            return len(stack)

    def depth(self, avatar_path: str) -> int:
        with self._lock:
            return len(self._stacks.get(avatar_path) or [])

    def capture(self, avatar_path: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        with self._lock:
            stack = self._stacks.get(avatar_path) or []
            if not stack:
                raise RuntimeError(
                    "There is no manual blendshape action to undo for the selected "
                    "avatar."
                )
            undo_items = copy.deepcopy(stack[-1])
            return undo_items, {
                "avatarPath": avatar_path,
                "undoDepth": len(stack),
                "undoSha256": blendshape_evidence_sha256(undo_items),
            }

    def consume_exact(
        self,
        avatar_path: str,
        expected_depth: Any,
        expected_sha256: Any,
        apply: Callable[[list[dict[str, Any]]], Any],
    ) -> tuple[Any, list[dict[str, Any]], int]:
        with self._lock:
            stack = self._stacks.get(avatar_path) or []
            if not stack:
                raise RuntimeError(
                    "There is no manual blendshape action to undo for the selected "
                    "avatar."
                )
            if expected_depth != len(stack):
                raise RuntimeError(
                    "Prepared Blendshape undo stack depth drifted after approval."
                )
            undo_items = copy.deepcopy(stack[-1])
            if expected_sha256 != blendshape_evidence_sha256(undo_items):
                raise RuntimeError(
                    "Prepared Blendshape undo stack drifted after approval."
                )
            result = apply(undo_items)
            stack.pop()
            return result, undo_items, len(stack)


def _clamp_blendshape_weight(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(number, 100.0))


def _manual_adjustment(item: Any) -> tuple[str, str, float]:
    if not isinstance(item, dict):
        raise RuntimeError("Blendshape adjustment is invalid.")
    renderer_path = item.get("renderer_path")
    blendshape_name = item.get("blendshape_name")
    if not isinstance(renderer_path, str) or not isinstance(blendshape_name, str):
        raise RuntimeError("Blendshape adjustment target is invalid.")
    if "target_weight" not in item:
        raise RuntimeError("Blendshape adjustment target_weight is required.")
    try:
        target_weight = float(item.get("target_weight"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Blendshape adjustment target_weight is invalid.") from exc
    if not math.isfinite(target_weight) or not 0.0 <= target_weight <= 100.0:
        raise RuntimeError("Blendshape adjustment target_weight is invalid.")
    previous_weight = item.get("previous_weight")
    if previous_weight is not None:
        try:
            previous = float(previous_weight)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Blendshape adjustment previous_weight is invalid.") from exc
        if not math.isfinite(previous) or not 0.0 <= previous <= 100.0:
            raise RuntimeError("Blendshape adjustment previous_weight is invalid.")
    return renderer_path, blendshape_name, target_weight


def _locked_target_set(
    locked_blendshapes: list[dict[str, Any]],
) -> set[tuple[str, str]]:
    return {
        (
            str(item.get("rendererPath") or item.get("renderer_path") or ""),
            str(
                item.get("blendshapeName")
                or item.get("blendshape_name")
                or item.get("blendshape")
                or ""
            ),
        )
        for item in locked_blendshapes
        if isinstance(item, dict)
    }


def _unity_result_failure(result: Any, serialized: Any) -> dict[str, Any] | None:
    """Project a failed Core envelope without turning it into a write success."""

    exit_code = getattr(result, "exit_code", None)
    envelope = serialized.get("payload") if isinstance(serialized, dict) else None
    envelope = envelope if isinstance(envelope, dict) else {}
    structured = envelope.get("structuredContent")
    structured = structured if isinstance(structured, dict) else {}
    data = structured.get("data")
    data = data if isinstance(data, dict) else {}
    direct_failure = serialized if isinstance(serialized, dict) else {}
    failed = bool(
        (isinstance(exit_code, int) and exit_code != 0)
        or envelope.get("isError") is True
        or structured.get("success") is False
        or direct_failure.get("ok") is False
        or direct_failure.get("success") is False
    )
    if not failed:
        return None
    fields: dict[str, Any] = {}
    for key in (
        "schema", "operation", "failureLayer", "failurePhase", "mutationStarted",
        "committed", "commitState", "requestMayHaveCommitted",
        "checkpointRecoveryRequired",
    ):
        value = data.get(key, structured.get(key, direct_failure.get(key)))
        if value is not None:
            fields[key] = value
    code = str(
        data.get("errorCode")
        or data.get("code")
        or structured.get("errorCode")
        or structured.get("code")
        or direct_failure.get("errorCode")
        or direct_failure.get("code")
        or "unity_tool_failed"
    ).strip()
    error = str(
        data.get("message")
        or data.get("error")
        or structured.get("message")
        or structured.get("error")
        or direct_failure.get("message")
        or direct_failure.get("error")
        or "The approved Unity Blendshape apply failed."
    ).strip()
    return {**fields, "errorCode": code, "error": error}


class AvatarTuningPreparedService:
    """Own prepared tuning seals, drift checks and approved Unity calls.

    Unity access is available only through one explicit invoke port. Planning,
    live reads and post-commit face metadata remain typed capabilities. The
    service owns no provider, transport, approval store or filesystem path.
    """

    def __init__(
        self,
        *,
        stores: AvatarTuningStoreService,
        undo: AvatarTuningUndoStore,
        ports: AvatarTuningPreparedPorts,
    ) -> None:
        self._stores = stores
        self._undo = undo
        self._ports = ports

    def _manual_state(self, arguments: dict[str, Any]) -> dict[str, Any]:
        parsed_arguments = self._ports.parse_manual_arguments(dict(arguments))
        raw_adjustments = parsed_arguments.get("adjustments")
        if not raw_adjustments:
            raise self._ports.make_prepare_error(
                "No blendshape adjustments were provided.",
                400,
            )
        if not isinstance(raw_adjustments, list):
            raise RuntimeError("Blendshape adjustments must be a list.")
        parsed_adjustments = [
            _manual_adjustment(item)
            for item in raw_adjustments
        ]
        context = self._ports.resolve_live_context(
            parsed_arguments,
            str(parsed_arguments.get("avatar") or "") or None,
        )
        self._ports.remember_avatar(context.avatar_name, context.avatar_path)
        locked = sorted(
            self._stores.normalize_locked_list(context.locked_blendshapes),
            key=lambda item: (
                item.get("rendererPath", ""),
                item.get("blendshapeName", ""),
            ),
        )
        locked_targets = _locked_target_set(locked)
        validated: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        undo_items: list[dict[str, Any]] = []
        target_facts: list[dict[str, Any]] = []
        for renderer_path, blendshape_name, target_weight in parsed_adjustments:
            key = (renderer_path, blendshape_name)
            if key not in context.allowed_targets:
                skipped.append(
                    {
                        "rendererPath": renderer_path,
                        "blendshapeName": blendshape_name,
                        "reason": "missing_blendshape",
                    }
                )
                continue
            if key in locked_targets:
                skipped.append(
                    {
                        "rendererPath": renderer_path,
                        "blendshapeName": blendshape_name,
                        "reason": "locked",
                    }
                )
                continue
            current_weight = float(
                context.allowed_targets[key]["currentWeight"]
            )
            validated.append(
                {
                    "rendererPath": renderer_path,
                    "blendshapeName": blendshape_name,
                    "targetWeight": target_weight,
                }
            )
            undo_items.append(
                {
                    "rendererPath": renderer_path,
                    "blendshapeName": blendshape_name,
                    "targetWeight": current_weight,
                }
            )
            target_facts.append(
                {
                    "rendererPath": renderer_path,
                    "blendshapeName": blendshape_name,
                    "currentWeight": current_weight,
                }
            )
        return {
            "context": context,
            "validatedAdjustments": validated,
            "skippedAdjustments": skipped,
            "undoItems": undo_items,
            "evidence": {
                "avatarPath": context.avatar_path,
                "targetFacts": target_facts,
                "locksSha256": blendshape_evidence_sha256(locked),
                # Seal the read-side avatar snapshot before approval. The
                # approved call must not perform a second Unity export.
                "selectedAvatar": self._ports.serialize_avatar(context),
                "skippedAdjustments": skipped,
            },
        }

    def prepare_manual_apply(
        self,
        arguments: dict[str, Any],
        preview: Any,
    ) -> tuple[dict[str, Any], Any]:
        del preview
        self._reject_reserved(arguments)
        state = self._manual_state(arguments)
        context = state["context"]
        if context.using_mock_execute:
            raise self._ports.make_prepare_error(
                "Mock Blendshape execution is preview-only and cannot be approved.",
                409,
            )
        adjustments = state["validatedAdjustments"]
        if not adjustments:
            raise self._ports.make_prepare_error(
                "No valid Blendshape adjustments remain after target/lock validation.",
                409,
            )
        prepared = install_prepared_calls(
            arguments,
            [
                (
                    "vrc_apply_blendshapes",
                    {
                        "avatarPath": context.avatar_path,
                        "adjustments": adjustments,
                        "saveAssets": True,
                    },
                )
            ],
            {**state["evidence"], "undoItems": state["undoItems"]},
        )
        return prepared, {
            "ok": True,
            "targetTool": "vrcforge_apply_blendshapes",
            "avatarPath": context.avatar_path,
            "adjustmentCount": len(adjustments),
            "skippedAdjustments": state["skippedAdjustments"],
        }

    def execute_manual_apply(self, arguments: dict[str, Any]) -> dict[str, Any]:
        evidence = prepared_evidence(arguments)
        if not isinstance(evidence, dict):
            raise RuntimeError("Prepared Blendshape evidence is invalid.")
        for key in ("avatarPath", "targetFacts", "locksSha256", "selectedAvatar"):
            if key not in evidence:
                raise RuntimeError("Prepared Blendshape evidence is incomplete.")
        tool_name, tool_arguments = prepared_call(arguments)
        expected = {
            "avatarPath": evidence["avatarPath"],
            "adjustments": tool_arguments.get("adjustments"),
            "saveAssets": True,
        }
        if tool_name != "vrc_apply_blendshapes":
            raise RuntimeError("Prepared Blendshape Core call is invalid.")
        require_exact_blendshape_evidence(
            tool_arguments,
            expected,
            "Core arguments",
        )
        target_facts = evidence["targetFacts"]
        if not isinstance(target_facts, list):
            raise RuntimeError("Prepared Blendshape target facts are invalid.")
        allowed_targets: dict[tuple[str, str], dict[str, Any]] = {}
        for item in target_facts:
            if not isinstance(item, dict):
                raise RuntimeError("Prepared Blendshape target facts are invalid.")
            renderer_path = item.get("rendererPath")
            blendshape_name = item.get("blendshapeName")
            if not isinstance(renderer_path, str) or not isinstance(blendshape_name, str):
                raise RuntimeError("Prepared Blendshape target facts are invalid.")
            try:
                current_weight = float(item["currentWeight"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("Prepared Blendshape target facts are invalid.") from exc
            if not math.isfinite(current_weight) or not 0.0 <= current_weight <= 100.0:
                raise RuntimeError("Prepared Blendshape target facts are invalid.")
            allowed_targets[(renderer_path, blendshape_name)] = {"currentWeight": current_weight}
        selected_avatar = evidence["selectedAvatar"]
        if not isinstance(selected_avatar, dict):
            raise RuntimeError("Prepared Blendshape selected avatar evidence is invalid.")
        avatar_path = evidence["avatarPath"]
        if not isinstance(avatar_path, str) or not avatar_path.strip():
            raise RuntimeError("Prepared Blendshape avatar evidence is invalid.")
        if str(selected_avatar.get("avatarPath") or "") != avatar_path:
            raise RuntimeError("Prepared Blendshape selected avatar evidence drifted.")
        context = AvatarTuningLiveContext(
            settings=self._ports.resolve_write_settings(
                {**arguments, "avatar_path": avatar_path}
            ),
            avatar_name=str(selected_avatar.get("avatarName") or "<unknown>"),
            avatar_path=avatar_path,
            allowed_targets=allowed_targets,
            locked_blendshapes=[],
            using_mock_execute=False,
            selected_avatar=selected_avatar,
        )
        result = self._ports.invoke_unity(
            context.settings,
            tool_name,
            tool_arguments,
        )
        serialized_result = self._ports.serialize_result(result)
        failure = _unity_result_failure(result, serialized_result)
        if failure is not None:
            return {
                "ok": False,
                "selectedAvatar": selected_avatar,
                "executionMode": "live-unity",
                "result": serialized_result,
                **failure,
            }
        change_preview = [
            {
                "rendererPath": item["rendererPath"],
                "blendshapeName": item["blendshapeName"],
                "previousWeight": item["currentWeight"],
                "targetWeight": adjustment["targetWeight"],
            }
            for adjustment in tool_arguments["adjustments"]
            for item in target_facts
            if item["rendererPath"] == adjustment["rendererPath"]
            and item["blendshapeName"] == adjustment["blendshapeName"]
        ]
        verified_changes = self._ports.verify_live_changes(context, change_preview)
        undo_items = evidence.get("undoItems")
        if not isinstance(undo_items, list):
            raise RuntimeError("Prepared Blendshape undo evidence is invalid.")
        undo_depth = self._undo.push(context.avatar_path, undo_items)
        self._ports.remember_avatar(context.avatar_name, context.avatar_path)
        return {
            "ok": True,
            "selectedAvatar": selected_avatar,
            "executionMode": (
                "mock" if context.using_mock_execute else "live-unity"
            ),
            "result": serialized_result,
            "appliedAdjustments": tool_arguments["adjustments"],
            "skippedAdjustments": list(evidence.get("skippedAdjustments") or []),
            "verifiedChanges": verified_changes,
            "undoDepth": undo_depth,
        }

    @staticmethod
    def _undo_avatar_path(arguments: dict[str, Any]) -> str:
        raw_avatar_path = arguments.get("avatar_path")
        if not isinstance(raw_avatar_path, str):
            raise RuntimeError("avatar_path is required for undo.")
        avatar_path = raw_avatar_path.strip()
        if not avatar_path:
            raise RuntimeError("avatar_path is required for undo.")
        return avatar_path

    def prepare_manual_undo(
        self,
        arguments: dict[str, Any],
        preview: Any,
    ) -> tuple[dict[str, Any], Any]:
        del preview
        self._reject_reserved(arguments)
        avatar_path = self._undo_avatar_path(arguments)
        undo_items, evidence = self._undo.capture(avatar_path)
        prepared = install_prepared_calls(
            arguments,
            [
                (
                    "vrc_apply_blendshapes",
                    {
                        "avatarPath": avatar_path,
                        "adjustments": undo_items,
                        "saveAssets": True,
                    },
                )
            ],
            evidence,
        )
        return prepared, {
            "ok": True,
            "targetTool": "vrcforge_undo_blendshapes",
            "avatarPath": avatar_path,
            "restoreCount": len(undo_items),
        }

    def execute_manual_undo(self, arguments: dict[str, Any]) -> dict[str, Any]:
        evidence = prepared_evidence(arguments)
        if not isinstance(evidence, dict):
            raise RuntimeError("Prepared Blendshape undo evidence is invalid.")
        avatar_path = self._undo_avatar_path(arguments)
        if avatar_path != evidence.get("avatarPath"):
            raise RuntimeError(
                "Prepared Blendshape undo avatar drifted after approval."
            )
        tool_name, tool_arguments = prepared_call(arguments)
        if tool_name != "vrc_apply_blendshapes":
            raise RuntimeError("Prepared Blendshape undo Core call is invalid.")

        def apply(undo_items: list[dict[str, Any]]) -> Any:
            expected = {
                "avatarPath": avatar_path,
                "adjustments": undo_items,
                "saveAssets": True,
            }
            require_exact_blendshape_evidence(
                tool_arguments,
                expected,
                "undo Core arguments",
            )
            return self._ports.invoke_unity(
                self._ports.resolve_write_settings(arguments),
                tool_name,
                tool_arguments,
            )

        result, undo_items, undo_depth = self._undo.consume_exact(
            avatar_path,
            evidence.get("undoDepth"),
            evidence.get("undoSha256"),
            apply,
        )
        return {
            "ok": True,
            "avatarPath": avatar_path,
            "result": self._ports.serialize_result(result),
            "undoDepth": undo_depth,
            "restoredAdjustments": undo_items,
        }

    @staticmethod
    def _saved_target_id(arguments: dict[str, Any], source_type: str) -> str:
        key = "historyId" if source_type == "history" else "presetId"
        alternate = "history_id" if source_type == "history" else "preset_id"
        value = str(arguments.get(key, arguments.get(alternate, "")) or "").strip()
        if not value:
            raise RuntimeError(
                f"{key} is required for saved tuning {source_type} reapply."
            )
        return value

    def _saved_item(self, item_id: str, source_type: str) -> dict[str, Any]:
        return (
            self._stores.find_history(item_id)
            if source_type == "history"
            else self._stores.find_preset(item_id)
        )

    def _saved_state(
        self,
        saved: dict[str, Any],
        arguments: dict[str, Any],
        source_type: str,
    ) -> dict[str, Any]:
        avatar_hint = str(
            arguments.get("avatar")
            or saved.get("avatar_path")
            or saved.get("avatar_name")
            or ""
        )
        context = self._ports.resolve_live_context(
            arguments,
            avatar_hint or None,
        )
        locked = self._stores.normalize_locked_list(context.locked_blendshapes)
        locked_targets = _locked_target_set(locked)
        adjustments: list[dict[str, Any]] = []
        undo_items: list[dict[str, Any]] = []
        target_facts: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for change in saved.get("changes") or []:
            if not isinstance(change, dict):
                raise RuntimeError(f"Saved {source_type} contains an invalid change.")
            renderer_path = str(
                change.get("renderer_path") or change.get("rendererPath") or ""
            )
            blendshape_name = str(
                change.get("blendshape")
                or change.get("blendshapeName")
                or change.get("blendshape_name")
                or ""
            )
            key = (renderer_path, blendshape_name)
            if key not in context.allowed_targets:
                skipped.append(
                    {
                        "rendererPath": renderer_path,
                        "blendshapeName": blendshape_name,
                        "reason": "missing_blendshape",
                    }
                )
                continue
            if key in locked_targets:
                skipped.append(
                    {
                        "rendererPath": renderer_path,
                        "blendshapeName": blendshape_name,
                        "reason": "locked",
                    }
                )
                continue
            current_weight = _clamp_blendshape_weight(
                context.allowed_targets[key].get("currentWeight", 0.0)
            )
            target_weight = _clamp_blendshape_weight(
                change.get("after", change.get("targetWeight", current_weight))
            )
            adjustments.append(
                {
                    "rendererPath": renderer_path,
                    "blendshapeName": blendshape_name,
                    "targetWeight": target_weight,
                }
            )
            undo_items.append(
                {
                    "rendererPath": renderer_path,
                    "blendshapeName": blendshape_name,
                    "targetWeight": current_weight,
                }
            )
            target_facts.append(
                {
                    "rendererPath": renderer_path,
                    "blendshapeName": blendshape_name,
                    "currentWeight": current_weight,
                }
            )
        if not adjustments:
            raise RuntimeError(
                f"No valid saved {source_type} changes remain after target/lock "
                "validation."
            )
        return {
            "context": context,
            "adjustments": adjustments,
            "undoItems": undo_items,
            "skipped": skipped,
            "evidence": {
                "avatarPath": context.avatar_path,
                "targetFacts": target_facts,
                "locksSha256": blendshape_evidence_sha256(locked),
            },
        }

    def _prepare_saved(
        self,
        arguments: dict[str, Any],
        source_type: str,
    ) -> tuple[dict[str, Any], Any]:
        self._reject_reserved(arguments)
        if self._ports.parse_mock_execute(dict(arguments)):
            raise RuntimeError(
                "Mock saved tuning is preview-only and cannot be approved for "
                "execution."
            )
        item_id = self._saved_target_id(arguments, source_type)
        saved = self._saved_item(item_id, source_type)
        state = self._saved_state(saved, arguments, source_type)
        context = state["context"]
        if context.using_mock_execute:
            raise RuntimeError(
                "Mock saved tuning is preview-only and cannot be approved for "
                "execution."
            )
        id_key = "historyId" if source_type == "history" else "presetId"
        prepared = install_prepared_calls(
            arguments,
            [
                (
                    "vrc_apply_blendshapes",
                    {
                        "avatarPath": context.avatar_path,
                        "adjustments": state["adjustments"],
                        "saveAssets": True,
                    },
                )
            ],
            {
                **state["evidence"],
                "sourceType": source_type,
                id_key: item_id,
                "savedSha256": blendshape_evidence_sha256(saved),
                "undoItems": state["undoItems"],
                "skipped": state["skipped"],
            },
        )
        return prepared, {
            "ok": True,
            "targetTool": (
                "vrcforge_reapply_tuning_history"
                if source_type == "history"
                else "vrcforge_apply_tuning_preset"
            ),
            "avatarPath": context.avatar_path,
            "adjustmentCount": len(state["adjustments"]),
            "skippedAdjustments": state["skipped"],
        }

    def prepare_reapply_history(
        self,
        arguments: dict[str, Any],
        preview: Any,
    ) -> tuple[dict[str, Any], Any]:
        del preview
        return self._prepare_saved(arguments, "history")

    def prepare_apply_preset(
        self,
        arguments: dict[str, Any],
        preview: Any,
    ) -> tuple[dict[str, Any], Any]:
        del preview
        return self._prepare_saved(arguments, "preset")

    def _execute_saved(
        self,
        arguments: dict[str, Any],
        source_type: str,
    ) -> dict[str, Any]:
        evidence = prepared_evidence(arguments)
        if not isinstance(evidence, dict) or evidence.get("sourceType") != source_type:
            raise RuntimeError("Prepared saved tuning evidence is invalid.")
        item_id = self._saved_target_id(arguments, source_type)
        id_key = "historyId" if source_type == "history" else "presetId"
        if evidence.get(id_key) != item_id:
            raise RuntimeError(
                "Prepared saved tuning identity drifted after approval."
            )
        saved = self._saved_item(item_id, source_type)
        if evidence.get("savedSha256") != blendshape_evidence_sha256(saved):
            raise RuntimeError(
                "Prepared saved tuning record drifted after approval."
            )
        state = self._saved_state(saved, arguments, source_type)
        context = state["context"]
        if context.using_mock_execute:
            raise RuntimeError(
                "Mock saved tuning cannot execute an approved Unity write."
            )
        for key in ("avatarPath", "targetFacts", "locksSha256"):
            require_exact_blendshape_evidence(
                evidence.get(key),
                state["evidence"].get(key),
                f"saved tuning {key}",
            )
        tool_name, tool_arguments = prepared_call(arguments)
        expected = {
            "avatarPath": context.avatar_path,
            "adjustments": state["adjustments"],
            "saveAssets": True,
        }
        if tool_name != "vrc_apply_blendshapes":
            raise RuntimeError("Prepared saved tuning Core call is invalid.")
        require_exact_blendshape_evidence(
            tool_arguments,
            expected,
            "saved tuning Core arguments",
        )
        result = self._ports.invoke_unity(
            context.settings,
            tool_name,
            tool_arguments,
        )
        undo_items = evidence.get("undoItems")
        if not isinstance(undo_items, list):
            raise RuntimeError("Prepared saved tuning undo evidence is invalid.")
        undo_depth = self._undo.push(context.avatar_path, undo_items)
        change_preview = [
            {
                "avatarPath": context.avatar_path,
                "rendererPath": adjustment["rendererPath"],
                "blendshapeName": adjustment["blendshapeName"],
                "previousWeight": fact["currentWeight"],
                "targetWeight": adjustment["targetWeight"],
            }
            for adjustment, fact in zip(
                state["adjustments"],
                state["evidence"]["targetFacts"],
                strict=True,
            )
        ]
        verified_changes = self._ports.verify_live_changes(
            context,
            change_preview,
        )
        readback_verified = bool(verified_changes) and all(
            bool(item.get("verified")) for item in verified_changes
        )
        warnings: list[str] = []
        if not readback_verified:
            warnings.append(
                "Unity changes committed, but exact Blendshape readback was not "
                "fully verified."
            )
        metadata = None
        try:
            if source_type == "history":
                self._stores.mark_history_applied(item_id)
                metadata = self._stores.find_history(item_id)
            else:
                self._stores.mark_preset_applied(item_id)
                metadata = self._stores.find_preset(item_id)
        except Exception as exc:  # The Unity mutation already committed.
            warnings.append(f"Post-apply metadata was not saved: {exc}")
        response = {
            "ok": True,
            "sourceType": source_type,
            "selectedAvatar": self._ports.serialize_avatar(context),
            "executionMode": "live-unity",
            "result": self._ports.serialize_result(result),
            "appliedAdjustments": state["adjustments"],
            "skippedAdjustments": evidence.get("skipped") or [],
            "verifiedChanges": verified_changes,
            "readbackVerified": readback_verified,
            "undoDepth": undo_depth,
            "warnings": warnings,
            (
                "historyRecord" if source_type == "history" else "preset"
            ): metadata,
        }
        if warnings:
            response.update({"committed": True, "committedWithWarning": True})
        return response

    def execute_reapply_history(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._execute_saved(arguments, "history")

    def execute_apply_preset(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._execute_saved(arguments, "preset")

    def prepare_face_tuning(
        self,
        arguments: dict[str, Any],
        preview: Any,
    ) -> tuple[dict[str, Any], Any]:
        del preview
        self._reject_reserved(arguments)
        if self._ports.parse_mock_execute(dict(arguments)):
            raise RuntimeError(
                "Mock face tuning is preview-only and cannot be approved for "
                "execution."
            )
        state = self._ports.prepare_face_state(arguments)
        if state.context.using_mock_execute:
            raise RuntimeError(
                "Mock face tuning is preview-only and cannot be approved for "
                "execution."
            )
        target_facts = [
            {
                "rendererPath": item["rendererPath"],
                "blendshapeName": item["blendshapeName"],
                "currentWeight": item["previousWeight"],
            }
            for item in state.change_preview
        ]
        locked = self._stores.normalize_locked_list(
            state.context.locked_blendshapes
        )
        prepared = install_prepared_calls(
            arguments,
            [
                (
                    "vrc_apply_blendshapes",
                    {
                        "avatarPath": state.context.avatar_path,
                        "adjustments": state.direct_adjustments,
                        "saveAssets": True,
                    },
                )
            ],
            {
                "avatarPath": state.context.avatar_path,
                "targetFacts": target_facts,
                "locksSha256": blendshape_evidence_sha256(locked),
                "planSha256": blendshape_evidence_sha256(state.plan),
                "undoItems": state.undo_items,
                "plan": state.plan,
                "changePreview": state.change_preview,
                "preview": state.preview,
                "applyPayload": state.apply_payload,
                "exportSource": state.export_source,
                "lockedBlendshapes": locked,
                "referenceContext": state.reference_context,
            },
        )
        return prepared, {
            "ok": True,
            "targetTool": "vrcforge_run_face_tuning",
            "avatarPath": state.context.avatar_path,
            "adjustmentCount": len(state.direct_adjustments),
            "preview": state.preview,
        }

    def execute_face_tuning(self, arguments: dict[str, Any]) -> dict[str, Any]:
        evidence = prepared_evidence(arguments)
        if not isinstance(evidence, dict) or not isinstance(
            evidence.get("plan"),
            dict,
        ):
            raise RuntimeError("Prepared face-tuning evidence is invalid.")
        context = self._ports.resolve_live_context(
            arguments,
            str(arguments.get("avatar") or evidence.get("avatarPath") or "") or None,
        )
        if context.using_mock_execute:
            raise RuntimeError(
                "Mock face tuning cannot execute an approved Unity write."
            )
        require_exact_blendshape_evidence(
            evidence.get("avatarPath"),
            context.avatar_path,
            "face-tuning avatar selection",
        )
        locked = self._stores.normalize_locked_list(context.locked_blendshapes)
        require_exact_blendshape_evidence(
            evidence.get("locksSha256"),
            blendshape_evidence_sha256(locked),
            "face-tuning locks",
        )
        fresh_facts: list[dict[str, Any]] = []
        for expected in evidence.get("targetFacts") or []:
            if not isinstance(expected, dict):
                raise RuntimeError(
                    "Prepared face-tuning target evidence is invalid."
                )
            key = (
                str(expected.get("rendererPath") or ""),
                str(expected.get("blendshapeName") or ""),
            )
            live = context.allowed_targets.get(key)
            if live is None:
                raise RuntimeError(
                    "Prepared face-tuning target disappeared after approval."
                )
            fresh_facts.append(
                {
                    "rendererPath": key[0],
                    "blendshapeName": key[1],
                    "currentWeight": float(live.get("currentWeight", 0.0)),
                }
            )
        require_exact_blendshape_evidence(
            evidence.get("targetFacts"),
            fresh_facts,
            "face-tuning target values",
        )
        normalized_plan, adjustments = self._ports.face_adjustments_from_plan(
            evidence["plan"]
        )
        if evidence.get("planSha256") != blendshape_evidence_sha256(
            normalized_plan
        ):
            raise RuntimeError(
                "Prepared face-tuning plan evidence drifted after approval."
            )
        tool_name, tool_arguments = prepared_call(arguments)
        expected_arguments = {
            "avatarPath": context.avatar_path,
            "adjustments": adjustments,
            "saveAssets": True,
        }
        if tool_name != "vrc_apply_blendshapes" or not isinstance(
            tool_arguments.get("adjustments"),
            list,
        ):
            raise RuntimeError("Prepared face-tuning Core call is invalid.")
        require_exact_blendshape_evidence(
            tool_arguments,
            expected_arguments,
            "face-tuning Core arguments",
        )
        result = self._ports.invoke_unity(
            context.settings,
            tool_name,
            tool_arguments,
        )
        undo_items = evidence.get("undoItems")
        if not isinstance(undo_items, list):
            raise RuntimeError("Prepared face-tuning undo evidence is invalid.")
        undo_depth = self._undo.push(context.avatar_path, undo_items)
        verified_changes = self._ports.verify_live_changes(
            context,
            list(evidence.get("changePreview") or []),
        )
        verification_ok = bool(verified_changes) and all(
            bool(item.get("verified")) for item in verified_changes
        )
        warnings: list[str] = []
        if not verification_ok:
            warnings.append(
                "Unity changes committed, but exact Blendshape readback was not "
                "fully verified."
            )
        summary = self._ports.render_face_summary(
            arguments,
            context,
            evidence,
            result,
            normalized_plan,
        )
        artifacts = None
        history_record = None
        try:
            artifacts = self._ports.save_face_artifacts(
                arguments,
                context,
                evidence,
                result,
                normalized_plan,
                summary,
            )
            history_record = self._ports.save_face_history(
                arguments,
                context,
                evidence,
                result,
                normalized_plan,
                summary,
                artifacts,
            )
        except Exception as exc:  # The Unity mutation already committed.
            warnings.append(f"Post-apply metadata was not saved: {exc}")
        return {
            "ok": True,
            "executionMode": "live-unity",
            "selectedAvatar": self._ports.serialize_avatar(context),
            "plan": evidence["plan"],
            "changePreview": evidence.get("changePreview") or [],
            "verifiedChanges": verified_changes,
            "visualProof": {
                "status": "unavailable",
                "reason": (
                    "Capture is deferred to a separately approved screenshot write."
                ),
            },
            "preview": evidence.get("preview") or {},
            "applyPayload": evidence.get("applyPayload") or "",
            "result": self._ports.serialize_result(result),
            "summary": summary,
            "artifacts": artifacts,
            "historyRecord": history_record,
            "lockedBlendshapes": evidence.get("lockedBlendshapes") or [],
            "undoDepth": undo_depth,
            "readbackVerified": verification_ok,
            "warnings": warnings,
        }

    @staticmethod
    def _reject_reserved(arguments: dict[str, Any]) -> None:
        if PREPARED_UNITY_EXECUTION_ARGUMENT_KEY in arguments:
            raise RuntimeError(
                "Caller may not provide the reserved prepared Unity execution key."
            )
