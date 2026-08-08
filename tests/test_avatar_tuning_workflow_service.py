from __future__ import annotations

from typing import Any

from avatar_tuning_workflow_service import (
    AvatarTuningApprovedWriteHandlers,
    AvatarTuningWorkflowPorts,
    AvatarTuningWorkflowService,
)


def _service(calls: list[tuple[Any, ...]]) -> AvatarTuningWorkflowService:
    def unary(name: str):
        def call(value: Any) -> dict[str, Any]:
            calls.append((name, value))
            return {"ok": True, "name": name, "value": value}

        return call

    def binary(name: str):
        def call(first: Any, second: Any) -> dict[str, Any]:
            calls.append((name, first, second))
            return {"ok": True, "name": name, "first": first, "second": second}

        return call

    def face(request: Any, execute: bool) -> dict[str, Any]:
        calls.append(("face", request, execute))
        return {"ok": True, "execute": execute}

    def request_write(
        target_tool: str,
        request: Any,
        *,
        reason: str,
        preview_callback=None,
        allow_mock_execute: bool = False,
        extra_arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        preview = preview_callback() if preview_callback is not None else None
        calls.append(
            (
                "request-write",
                target_tool,
                request,
                reason,
                preview,
                allow_mock_execute,
                extra_arguments,
            )
        )
        return {"ok": True, "targetTool": target_tool, "preview": preview}

    return AvatarTuningWorkflowService(
        AvatarTuningWorkflowPorts(
            scan_scene_avatars=unary("scene-avatars"),
            read_avatars=unary("avatars"),
            read_avatar_blendshapes=unary("blendshapes"),
            run_face_tuning=face,
            preview_manual_blendshapes=unary("manual-preview"),
            preview_agent_blendshape_apply=unary("agent-preview"),
            request_supervised_write=request_write,
            load_history=lambda: {
                "records": [
                    {"id": "a", "avatar_path": "Avatar/A", "avatar_name": "Alice"},
                    {"id": "b", "avatar_path": "Avatar/B", "avatar_name": "Bob"},
                ]
            },
            load_presets=lambda: {
                "presets": [
                    {"id": "p1", "avatar_path": "Avatar/A", "avatar_name": "Alice"},
                    {"id": "p2", "avatar_path": "Avatar/B", "avatar_name": "Bob"},
                ]
            },
            load_locked_blendshapes=lambda avatar: [{"avatar": avatar, "name": "Smile"}],
            current_avatar_path=lambda: "Avatar/Current",
            create_preset=unary("create-preset"),
            rename_preset=binary("rename-preset"),
            duplicate_preset=binary("duplicate-preset"),
            delete_preset=unary("delete-preset"),
            update_locks=unary("update-locks"),
            ai_select_locks=unary("ai-locks"),
            preview_saved_history=binary("history-preview"),
            preview_saved_preset=binary("preset-preview"),
        )
    )


def test_read_models_and_local_store_projection_preserve_existing_shapes() -> None:
    calls: list[tuple[Any, ...]] = []
    service = _service(calls)
    request = {"projectPath": "E:/avatar"}

    assert service.scan_scene_avatars(request)["name"] == "scene-avatars"
    assert service.read_avatars(request)["name"] == "avatars"
    assert service.read_avatar_blendshapes(request)["name"] == "blendshapes"
    assert service.plan_face_tuning(request) == {"ok": True, "execute": False}
    assert service.preview_agent_blendshape_apply(request)["name"] == "agent-preview"

    history = service.list_tuning_history("Alice")
    presets = service.list_tuning_presets("Avatar/B")
    locks = service.read_tuning_locks()
    assert [item["id"] for item in history["records"]] == ["a"]
    assert [item["id"] for item in presets["presets"]] == ["p2"]
    assert locks == {
        "ok": True,
        "avatarPath": "Avatar/Current",
        "lockedBlendshapes": [{"avatar": "Avatar/Current", "name": "Smile"}],
        "count": 1,
    }


def test_supervised_tuning_requests_keep_tool_reason_preview_and_ids() -> None:
    calls: list[tuple[Any, ...]] = []
    service = _service(calls)
    request = {"avatarPath": "Avatar/A"}

    assert service.request_face_tuning(request)["targetTool"] == "vrcforge_run_face_tuning"
    assert service.request_manual_blendshape_apply(request)["targetTool"] == "vrcforge_apply_blendshapes"
    assert service.request_manual_blendshape_undo(request)["targetTool"] == "vrcforge_undo_blendshapes"
    assert service.request_reapply_tuning_history("history-1", request)["targetTool"] == "vrcforge_reapply_tuning_history"
    assert service.request_apply_tuning_preset("preset-1", request)["targetTool"] == "vrcforge_apply_tuning_preset"

    writes = [call for call in calls if call[0] == "request-write"]
    assert writes[0][4] == {"ok": True, "execute": True}
    assert writes[0][5] is True
    assert writes[1][4]["name"] == "manual-preview"
    assert writes[1][5] is True
    assert writes[2][4] is None and writes[2][5] is False and writes[2][6] is None
    assert writes[3][4]["name"] == "history-preview"
    assert writes[3][6] == {"historyId": "history-1"}
    assert writes[4][4]["name"] == "preset-preview"
    assert writes[4][6] == {"presetId": "preset-1"}


def test_local_mutation_and_prepared_write_ports_remain_explicit() -> None:
    calls: list[tuple[Any, ...]] = []
    service = _service(calls)
    request = {"avatarPath": "Avatar/A"}

    assert service.create_tuning_preset(request)["name"] == "create-preset"
    assert service.rename_tuning_preset("p1", request)["name"] == "rename-preset"
    assert service.duplicate_tuning_preset("p1", request)["name"] == "duplicate-preset"
    assert service.delete_tuning_preset("p1")["name"] == "delete-preset"
    assert service.update_tuning_locks(request)["name"] == "update-locks"
    assert service.ai_select_tuning_locks(request)["name"] == "ai-locks"

    def unary(name: str):
        def call(value: Any) -> dict[str, Any]:
            calls.append((name, value))
            return {"ok": True, "name": name, "value": value}

        return call

    def prepare(name: str):
        def call(arguments: dict[str, Any], preview: Any) -> tuple[dict[str, Any], Any]:
            calls.append((name, arguments, preview))
            return {**arguments, "preparedBy": name}, preview

        return call

    handlers = AvatarTuningApprovedWriteHandlers(
        prepare_manual_apply=prepare("prepare-manual-apply"),
        execute_manual_apply=unary("execute-manual-apply"),
        prepare_manual_undo=prepare("prepare-manual-undo"),
        execute_manual_undo=unary("execute-manual-undo"),
        prepare_face_tuning=prepare("prepare-face"),
        execute_face_tuning=unary("execute-face"),
        prepare_reapply_history=prepare("prepare-history"),
        execute_reapply_history=unary("execute-history"),
        prepare_apply_preset=prepare("prepare-preset"),
        execute_apply_preset=unary("execute-preset"),
    )

    prepared_operations = [
        (handlers.prepare_manual_apply, "prepare-manual-apply"),
        (handlers.prepare_manual_undo, "prepare-manual-undo"),
        (handlers.prepare_face_tuning, "prepare-face"),
        (handlers.prepare_reapply_history, "prepare-history"),
        (handlers.prepare_apply_preset, "prepare-preset"),
    ]
    for operation, owner in prepared_operations:
        assert operation(request, {"preview": owner}) == (
            {**request, "preparedBy": owner},
            {"preview": owner},
        )

    execute_operations = [
        (handlers.execute_manual_apply, "execute-manual-apply"),
        (handlers.execute_manual_undo, "execute-manual-undo"),
        (handlers.execute_face_tuning, "execute-face"),
        (handlers.execute_reapply_history, "execute-history"),
        (handlers.execute_apply_preset, "execute-preset"),
    ]
    for operation, owner in execute_operations:
        assert operation(request)["name"] == owner
