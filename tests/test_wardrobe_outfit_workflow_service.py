from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from wardrobe_outfit_workflow_service import (
    ClothingFxReadPorts,
    ClothingFxReadService,
    WardrobeArtifactReadPorts,
    WardrobeArtifactReadService,
    WardrobeOutfitApprovedWriteHandlers,
    WardrobeOutfitWorkflowError,
    WardrobeOutfitWorkflowPorts,
    WardrobeOutfitWorkflowService,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _clothing_reads(calls: list[tuple[Any, ...]]) -> ClothingFxReadService:
    settings = object()

    def load_settings(request: Any) -> Any:
        calls.append(("settings", request))
        return settings

    def scan_controls(actual_settings: Any, avatar_path: str | None) -> dict[str, Any]:
        calls.append(("scan", actual_settings, avatar_path))
        return {
            "items": [{"displayName": "Jacket", "parameterName": "Cloth_Jacket"}],
            "jsonPath": "artifacts/avatar_controls.json",
        }

    def build_blueprint(actual_settings: Any, avatar_path: str | None) -> dict[str, Any]:
        calls.append(("blueprint", actual_settings, avatar_path))
        return {"items": [{"displayName": "Jacket"}], "itemCount": 1}

    def build_apply_preview(avatar_path: str | None, items: list[dict[str, Any]]) -> str:
        calls.append(("apply-preview", avatar_path, items))
        return '{"tool":"vrc_apply_clothing_fx"}'

    def ensure_list(payload: Any, scope: str) -> list[Any]:
        calls.append(("ensure-list", payload, scope))
        if not isinstance(payload, list):
            raise RuntimeError("not a list")
        return payload

    def log(
        level: str,
        scope: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        calls.append(("log", level, scope, message, data))

    return ClothingFxReadService(
        ClothingFxReadPorts(
            load_settings=load_settings,
            current_avatar_path=lambda: "Scene/CurrentAvatar",
            scan_controls=scan_controls,
            build_blueprint=build_blueprint,
            build_apply_preview=build_apply_preview,
            ensure_list=ensure_list,
            log=log,
        )
    )


def _service(calls: list[tuple[Any, ...]], *, ready: bool = True) -> WardrobeOutfitWorkflowService:
    def unary(name: str):
        def call(params: dict[str, Any]) -> dict[str, Any]:
            calls.append((name, params))
            return {"ok": True, "name": name, "params": params}

        return call

    def inspect(package_path: str, *, max_entries: int = 5000) -> dict[str, Any]:
        calls.append(("inspect", package_path, max_entries))
        return {"ok": True, "source": package_path, "maxEntries": max_entries}

    def plan(**kwargs: Any) -> dict[str, Any]:
        calls.append(("plan", kwargs))
        return {"ok": True, "plan": {"readyToApply": ready}, "kwargs": kwargs}

    def create_apply_request(
        params: dict[str, Any],
        *,
        internal_wrapper: bool = False,
    ) -> dict[str, Any]:
        calls.append(("approval", params, internal_wrapper))
        return {"ok": True, "approval": params, "internalWrapper": internal_wrapper}

    def request_supervised_write(
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

    return WardrobeOutfitWorkflowService(
        WardrobeOutfitWorkflowPorts(
            selected_project_path=lambda: "E:/selected-project",
            inspect_package=inspect,
            build_import_plan=plan,
            create_apply_request=create_apply_request,
            request_supervised_write=request_supervised_write,
            scan_avatar_items=unary("scan-items"),
            scan_avatar_controls=unary("scan-controls"),
            scan_wardrobe=unary("scan-wardrobe"),
            scan_clothes=unary("scan-clothes"),
            generate_clothing_fx=unary("generate-clothing-fx"),
            preview_apply_clothing_fx=unary("preview-clothing-fx"),
            preview_setup_outfit=unary("preview-setup"),
            preview_add_wardrobe_outfit=unary("preview-add-wardrobe"),
            preview_add_outfit_part=unary("preview-add-part"),
            preview_add_modular_avatar_component=unary("preview-add-ma"),
            preview_manage_wardrobe=unary("preview-manage"),
            preview_create_wardrobe=unary("preview-create"),
            preview_add_outfit=unary("preview-add-outfit"),
        )
    )


def test_inspection_and_plan_normalize_existing_aliases_and_selected_project() -> None:
    calls: list[tuple[Any, ...]] = []
    service = _service(calls)

    assert service.inspect_outfit_package({"package_path": " E:/booth.zip ", "max_entries": 42})["ok"]
    planned = service.plan_outfit_import(
        {
            "package_path": " E:/booth.zip ",
            "target_folder": " Assets/Outfit ",
            "selected_unitypackage": " Base.unitypackage ",
            "selected_prefab": " Assets/Outfit.prefab ",
            "base_avatar_name": " Manuka ",
            "max_entries": 77,
        }
    )

    assert planned["kwargs"] == {
        "package_path": "E:/booth.zip",
        "project_path": "E:/selected-project",
        "target_folder": "Assets/Outfit",
        "selected_unitypackage": "Base.unitypackage",
        "selected_prefab": "Assets/Outfit.prefab",
        "base_avatar_name": "Manuka",
        "max_entries": 77,
    }


def test_missing_package_path_fails_before_any_port_call() -> None:
    calls: list[tuple[Any, ...]] = []
    service = _service(calls)

    with pytest.raises(WardrobeOutfitWorkflowError) as exc_info:
        service.plan_outfit_import({})

    assert exc_info.value.status_code == 400
    assert str(exc_info.value) == "packagePath is required."
    assert calls == []


def test_import_request_requires_ready_plan_and_preserves_approval_envelope() -> None:
    blocked_calls: list[tuple[Any, ...]] = []
    blocked = _service(blocked_calls, ready=False)
    with pytest.raises(WardrobeOutfitWorkflowError):
        blocked.request_outfit_import({"packagePath": "E:/outfit.unitypackage"})
    assert not any(call[0] == "approval" for call in blocked_calls)

    calls: list[tuple[Any, ...]] = []
    service = _service(calls)
    params = {"packagePath": "E:/outfit.unitypackage", "projectPath": "E:/avatar"}
    result = service.request_outfit_import(params, agent_name="desktop-agent")
    approval = result["approval"]

    assert approval["target_tool"] == "vrcforge_import_outfit_package"
    assert approval["arguments"] == params
    assert approval["agent_name"] == "desktop-agent"
    assert approval["preview"]["plan"]["readyToApply"] is True
    assert result["internalWrapper"] is False


def test_typed_read_and_preview_ports_are_direct_and_explicit() -> None:
    calls: list[tuple[Any, ...]] = []
    service = _service(calls)
    payload = {"avatarPath": "Avatar"}

    operations = [
        (service.scan_avatar_items, "scan-items"),
        (service.scan_avatar_controls, "scan-controls"),
        (service.scan_wardrobe, "scan-wardrobe"),
        (service.scan_clothes, "scan-clothes"),
        (service.generate_clothing_fx, "generate-clothing-fx"),
        (service.preview_setup_outfit, "preview-setup"),
        (service.preview_add_wardrobe_outfit, "preview-add-wardrobe"),
        (service.preview_add_outfit_part, "preview-add-part"),
        (service.preview_add_modular_avatar_component, "preview-add-ma"),
        (service.preview_manage_wardrobe, "preview-manage"),
        (service.preview_create_wardrobe, "preview-create"),
        (service.preview_add_outfit, "preview-add-outfit"),
    ]
    for operation, expected in operations:
        assert operation(payload)["name"] == expected


def test_clothing_fx_read_owner_preserves_scan_and_blueprint_shapes_and_logs() -> None:
    calls: list[tuple[Any, ...]] = []
    service = _clothing_reads(calls)

    scan_request = SimpleNamespace(avatar_path="Scene/OverrideAvatar")
    scanned = service.scan_clothes(scan_request)
    generated = service.generate_clothing_fx(SimpleNamespace(avatar_path=None))
    previewed = service.preview_apply_clothing_fx(
        SimpleNamespace(
            avatar_path=None,
            items=[{"displayName": "Jacket"}],
        )
    )

    assert scanned == {
        "ok": True,
        "avatarPath": "Scene/OverrideAvatar",
        "clothes": [{"displayName": "Jacket", "parameterName": "Cloth_Jacket"}],
        "count": 1,
        "jsonPath": "artifacts/avatar_controls.json",
    }
    assert generated == {
        "ok": True,
        "avatarPath": "Scene/CurrentAvatar",
        "fxBlueprint": {"items": [{"displayName": "Jacket"}], "itemCount": 1},
    }
    assert previewed == {
        "ok": True,
        "avatarPath": "Scene/CurrentAvatar",
        "dryRun": True,
        "applyPayload": '{"tool":"vrc_apply_clothing_fx"}',
        "itemCount": 1,
    }
    assert any(call[:3] == ("log", "info", "fx") for call in calls)
    assert any(call[:3] == ("log", "success", "fx") for call in calls)
    assert any(
        call[:4]
        == (
            "log",
            "info",
            "fx",
            "Clothing FX apply payload generated (dry-run).",
        )
        for call in calls
    )
    assert any(call[0] == "scan" and call[2] == "Scene/OverrideAvatar" for call in calls)
    assert any(call[0] == "blueprint" and call[2] == "Scene/CurrentAvatar" for call in calls)


def test_clothing_fx_read_owner_logs_and_preserves_runtime_errors() -> None:
    calls: list[tuple[Any, ...]] = []

    def fail_scan(_settings: Any, _avatar_path: str | None) -> dict[str, Any]:
        raise RuntimeError("Cannot connect to Unity MCP server")

    service = ClothingFxReadService(
        ClothingFxReadPorts(
            load_settings=lambda _request: object(),
            current_avatar_path=lambda: "Scene/Avatar",
            scan_controls=fail_scan,
            build_blueprint=lambda _settings, _avatar: {},
            build_apply_preview=lambda _avatar, _items: "{}",
            ensure_list=lambda payload, _scope: payload,
            log=lambda level, scope, message, data=None: calls.append(
                (level, scope, message, data)
            ),
        )
    )

    with pytest.raises(RuntimeError, match="Cannot connect to Unity MCP server"):
        service.scan_clothes(SimpleNamespace(avatar_path=None))

    assert calls == [
        (
            "error",
            "fx",
            "Failed to scan clothing objects.",
            {"error": "Cannot connect to Unity MCP server"},
        )
    ]


def test_dashboard_composes_clothing_fx_reads_without_legacy_facades() -> None:
    tree = ast.parse((REPO_ROOT / "dashboard_server.py").read_text(encoding="utf-8"))
    bindings = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    source = (REPO_ROOT / "dashboard_server.py").read_text(encoding="utf-8")

    assert "scan_clothes_sync" not in bindings
    assert "generate_clothing_fx_sync" not in bindings
    assert "apply_clothing_fx_sync" not in bindings
    assert "scan_avatar_items_sync" not in bindings
    assert "scan_avatar_controls_sync" not in bindings
    assert "scan_wardrobe_sync" not in bindings
    assert "scan_clothes=CLOTHING_FX_READ.scan_clothes" in source
    assert "generate_clothing_fx=CLOTHING_FX_READ.generate_clothing_fx" in source
    assert (
        "preview_apply_clothing_fx=CLOTHING_FX_READ.preview_apply_clothing_fx"
        in source
    )
    assert source.count("apply_clothing_fx_approved_sync") == 2
    assert (
        "WARDROBE_OUTFIT_APPROVED_WRITES.apply_clothing_fx" in source
    )
    assert "scan_avatar_items=WARDROBE_ARTIFACT_READ.scan_avatar_items" in source
    assert "scan_avatar_controls=WARDROBE_ARTIFACT_READ.scan_avatar_controls" in source
    assert "scan_wardrobe=WARDROBE_ARTIFACT_READ.scan_wardrobe" in source
    artifact_root = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "WARDROBE_ARTIFACT_READ"
            for target in node.targets
        )
    )
    artifact_composition = ast.unparse(artifact_root.value)
    assert artifact_composition.count("run_unity_artifact_scan_sync") == 2
    for fixed_value in (
        "vrc_scan_avatar_items",
        "avatar_items",
        "avatar item scan",
        "vrc_scan_wardrobe",
        "wardrobe",
        "wardrobe scan",
        "scan_avatar_controls_direct",
    ):
        assert fixed_value in artifact_composition


def test_wardrobe_artifact_read_owner_preserves_read_parameters_and_shapes() -> None:
    calls: list[tuple[Any, ...]] = []

    def scan_items(params: dict[str, Any]) -> dict[str, Any]:
        calls.append(("items", params))
        return {"source": "vrc_scan_avatar_items"}

    def scan_controls(params: dict[str, Any]) -> dict[str, Any]:
        calls.append(("controls", params))
        return {"items": [{"parameterName": "Clothes"}]}

    def scan_wardrobe(params: dict[str, Any]) -> dict[str, Any]:
        calls.append(("wardrobe", params))
        return {"source": "vrc_scan_wardrobe"}

    service = WardrobeArtifactReadService(
        WardrobeArtifactReadPorts(
            scan_avatar_items=scan_items,
            scan_avatar_controls=scan_controls,
            scan_wardrobe=scan_wardrobe,
        )
    )

    assert set(WardrobeArtifactReadPorts.__dataclass_fields__) == {
        "scan_avatar_items",
        "scan_avatar_controls",
        "scan_wardrobe",
    }

    items = service.scan_avatar_items({"max_items": 42, "avatarPath": "Scene/Avatar"})
    controls = service.scan_avatar_controls({"avatar_path": " Scene/Avatar "})
    wardrobe = service.scan_wardrobe(None)

    assert items == {"source": "vrc_scan_avatar_items"}
    assert controls == {"items": [{"parameterName": "Clothes"}], "ok": True}
    assert wardrobe == {"source": "vrc_scan_wardrobe"}
    assert ("items", {"max_items": 42, "avatarPath": "Scene/Avatar"}) in calls
    assert ("controls", {"avatar_path": " Scene/Avatar "}) in calls
    assert ("wardrobe", {}) in calls


def test_approved_writes_are_a_separate_least_authority_binding() -> None:
    calls: list[tuple[Any, ...]] = []

    def unary(name: str):
        def call(arguments: dict[str, Any]) -> dict[str, Any]:
            calls.append((name, arguments))
            return {"ok": True, "name": name}

        return call

    def prepare(name: str):
        def call(arguments: dict[str, Any], preview: Any) -> tuple[dict[str, Any], Any]:
            calls.append((name, arguments, preview))
            return {**arguments, "preparedBy": name}, preview

        return call

    handlers = WardrobeOutfitApprovedWriteHandlers(
        apply_clothing_fx=unary("apply-clothing-fx"),
        setup_outfit=unary("setup"),
        add_wardrobe_outfit=unary("add-wardrobe"),
        add_outfit_part=unary("add-part"),
        add_modular_avatar_component=unary("add-ma"),
        manage_wardrobe=unary("manage"),
        create_wardrobe=unary("create"),
        prepare_add_outfit=prepare("prepare-add-outfit"),
        add_outfit=unary("add-outfit"),
        prepare_import_package=prepare("prepare-import"),
        import_package=unary("import"),
    )
    payload = {"avatarPath": "Avatar"}

    assert handlers.setup_outfit(payload)["name"] == "setup"
    assert handlers.apply_clothing_fx(payload)["name"] == "apply-clothing-fx"
    assert handlers.add_outfit(payload)["name"] == "add-outfit"
    assert handlers.import_package(payload)["name"] == "import"
    assert handlers.prepare_add_outfit(payload, {"preview": 1})[0]["preparedBy"] == "prepare-add-outfit"
    assert handlers.prepare_import_package(payload, {"preview": 2})[0]["preparedBy"] == "prepare-import"


def test_clothing_write_routes_keep_the_supervised_controller_boundary() -> None:
    calls: list[tuple[Any, ...]] = []
    service = _service(calls)
    request = {"avatarPath": "Avatar", "objectPath": "Avatar/Coat"}

    assert service.request_toggle_clothing(request)["targetTool"] == "vrcforge_toggle_scene_object"
    fx = service.request_apply_clothing_fx(request)
    assert fx["targetTool"] == "vrcforge_apply_clothing_fx"
    assert fx["preview"]["name"] == "preview-clothing-fx"

    writes = [call for call in calls if call[0] == "request-write"]
    assert writes[0][4] is None and writes[0][5] is False
    assert writes[1][4]["name"] == "preview-clothing-fx"
    assert writes[1][5] is False
