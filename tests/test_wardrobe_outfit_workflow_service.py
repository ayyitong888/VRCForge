from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from wardrobe_outfit_workflow_service import (
    AddModularAvatarComponentApprovedWritePorts,
    AddModularAvatarComponentApprovedWriteService,
    AddModularAvatarComponentPreviewPorts,
    AddModularAvatarComponentPreviewService,
    AddOutfitPartApprovedWritePorts,
    AddOutfitPartApprovedWriteService,
    AddOutfitPartPreviewPorts,
    AddOutfitPartPreviewService,
    AddWardrobeOutfitApprovedWritePorts,
    AddWardrobeOutfitApprovedWriteService,
    AddWardrobeOutfitPreviewPorts,
    AddWardrobeOutfitPreviewService,
    ClothingFxApprovedWritePorts,
    ClothingFxApprovedWriteService,
    ClothingFxReadPorts,
    ClothingFxReadService,
    CreateWardrobeApprovedWritePorts,
    CreateWardrobeApprovedWriteService,
    CreateWardrobePreviewPorts,
    CreateWardrobePreviewService,
    ManageWardrobeApprovedWritePorts,
    ManageWardrobeApprovedWriteService,
    ManageWardrobePreviewPorts,
    ManageWardrobePreviewService,
    SetupOutfitApprovedWritePorts,
    SetupOutfitApprovedWriteService,
    SetupOutfitPreviewPorts,
    SetupOutfitPreviewService,
    WardrobeArtifactReadPorts,
    WardrobeArtifactReadService,
    WardrobeOutfitApprovedWriteHandlers,
    WardrobeOutfitWorkflowError,
    WardrobeOutfitWorkflowPorts,
    WardrobeOutfitWorkflowService,
    build_add_wardrobe_outfit_request,
    build_add_outfit_part_request,
    build_add_modular_avatar_component_request,
    build_create_wardrobe_core_calls,
    build_create_wardrobe_request,
    build_manage_wardrobe_request,
    coerce_setup_outfit_float_param,
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


class _MappedClothingFxError(RuntimeError):
    pass


def _clothing_approved(
    calls: list[tuple[Any, ...]],
    *,
    preview_error: Exception | None = None,
    apply_error: Exception | None = None,
    parse_error: Exception | None = None,
) -> ClothingFxApprovedWriteService:
    settings = object()

    def parse_request(arguments: dict[str, Any]) -> Any:
        calls.append(("parse", dict(arguments)))
        if parse_error is not None:
            raise parse_error
        return SimpleNamespace(
            dry_run=bool(arguments.get("dry_run", True)),
            avatar_path=arguments.get("avatarPath") or arguments.get("avatar_path"),
            items=arguments.get("items") or [],
        )

    def preview(request: Any) -> dict[str, Any]:
        calls.append(("preview", request))
        if preview_error is not None:
            raise preview_error
        return {"ok": True, "dryRun": True, "itemCount": len(request.items)}

    def load_settings(request: Any) -> Any:
        calls.append(("settings", request))
        return settings

    def current_avatar_path() -> str:
        calls.append(("current-avatar",))
        return "Scene/CurrentAvatar"

    def build_preview(avatar_path: str | None, items: list[dict[str, Any]]) -> str:
        calls.append(("build-preview", avatar_path, items))
        return "frozen-preview"

    def apply(
        actual_settings: Any,
        avatar_path: str | None,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        calls.append(("apply", actual_settings, avatar_path, items))
        if apply_error is not None:
            raise apply_error
        return {"createdCount": len(items)}

    def log(
        level: str,
        scope: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        calls.append(("log", level, scope, message, data))

    def map_error(exc: Exception) -> Exception:
        calls.append(("map-error", exc))
        return _MappedClothingFxError(str(exc))

    return ClothingFxApprovedWriteService(
        ClothingFxApprovedWritePorts(
            parse_request=parse_request,
            preview=preview,
            load_settings=load_settings,
            current_avatar_path=current_avatar_path,
            build_apply_preview=build_preview,
            apply_approved=apply,
            log=log,
            map_error=map_error,
            handled_errors=(RuntimeError,),
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


def test_clothing_fx_approved_owner_preserves_live_order_shape_and_log() -> None:
    calls: list[tuple[Any, ...]] = []
    service = _clothing_approved(calls)
    items = [{"displayName": "Jacket"}]

    payload = service.execute({"items": items, "dryRun": False})

    assert payload == {
        "ok": True,
        "avatarPath": "Scene/CurrentAvatar",
        "dryRun": False,
        "applyPayload": "frozen-preview",
        "result": {"createdCount": 1},
        "itemCount": 1,
    }
    assert [call[0] for call in calls] == [
        "parse",
        "settings",
        "current-avatar",
        "build-preview",
        "apply",
        "log",
    ]
    assert calls[0][1]["dry_run"] is False
    assert calls[4][2:] == ("Scene/CurrentAvatar", items)
    assert calls[5] == (
        "log",
        "success",
        "fx",
        "Clothing FX assets authored in Unity.",
        {"avatarPath": "Scene/CurrentAvatar", "itemCount": 1},
    )


@pytest.mark.parametrize(
    ("arguments", "expect_preview"),
    [
        ({"items": [{"displayName": "Jacket"}], "dryRun": True}, True),
        (
            {
                "items": [{"displayName": "Jacket"}],
                "dry_run": True,
                "dryRun": False,
            },
            True,
        ),
        (
            {
                "items": [{"displayName": "Jacket"}],
                "dry_run": False,
                "dryRun": True,
            },
            False,
        ),
    ],
)
def test_clothing_fx_approved_owner_preserves_camel_projection_and_snake_precedence(
    arguments: dict[str, Any],
    expect_preview: bool,
) -> None:
    calls: list[tuple[Any, ...]] = []
    payload = _clothing_approved(calls).execute(arguments)

    assert payload["dryRun"] is expect_preview
    assert ("preview" in [call[0] for call in calls]) is expect_preview
    assert ("apply" in [call[0] for call in calls]) is (not expect_preview)


def test_clothing_fx_approved_owner_parses_before_any_side_effect() -> None:
    calls: list[tuple[Any, ...]] = []
    parse_error = ValueError("invalid request")

    with pytest.raises(ValueError, match="invalid request") as exc_info:
        _clothing_approved(calls, parse_error=parse_error).execute({"dryRun": False})

    assert exc_info.value is parse_error
    assert [call[0] for call in calls] == ["parse"]


@pytest.mark.parametrize("phase", ["preview", "apply"])
def test_clothing_fx_approved_owner_maps_handled_errors_without_extra_writes(
    phase: str,
) -> None:
    calls: list[tuple[Any, ...]] = []
    error = RuntimeError("Cannot connect to Unity MCP server")
    service = _clothing_approved(
        calls,
        preview_error=error if phase == "preview" else None,
        apply_error=error if phase == "apply" else None,
    )

    with pytest.raises(_MappedClothingFxError, match="Cannot connect"):
        service.execute(
            {
                "avatarPath": "Scene/Avatar",
                "items": [{"displayName": "Jacket"}],
                "dryRun": phase == "preview",
            }
        )

    names = [call[0] for call in calls]
    assert names.count("map-error") == 1
    if phase == "preview":
        assert "settings" not in names
        assert "apply" not in names
        assert "log" not in names
    else:
        assert names.count("apply") == 1
        assert calls[-2][:4] == (
            "log",
            "error",
            "fx",
            "Failed to apply clothing FX.",
        )


def test_clothing_fx_approved_owner_has_only_frozen_fixed_ports() -> None:
    assert set(ClothingFxApprovedWritePorts.__dataclass_fields__) == {
        "parse_request",
        "preview",
        "load_settings",
        "current_avatar_path",
        "build_apply_preview",
        "apply_approved",
        "log",
        "map_error",
        "handled_errors",
    }


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


def test_dashboard_has_no_outfit_read_import_or_dead_wrapper_facades() -> None:
    source = (REPO_ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    bindings = {
        item.name
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    imported_names = {
        alias.asname or alias.name
        for item in tree.body
        if isinstance(item, ast.ImportFrom)
        for alias in item.names
    }

    assert {
        "inspect_outfit_package_sync",
        "plan_outfit_import_sync",
    }.isdisjoint(bindings)
    assert {
        "inspect_outfit_package",
        "build_outfit_import_plan",
    }.isdisjoint(imported_names)
    assert "outfit_package_inspector_domain.inspect_outfit_package" in source
    assert "outfit_import_planner_domain.build_outfit_import_plan" in source
    assert "preview_setup_outfit_sync" not in bindings
    assert "setup_outfit_sync" not in bindings
    assert "preview_add_wardrobe_outfit_sync" not in bindings
    assert "add_wardrobe_outfit_sync" not in bindings
    assert "build_add_wardrobe_outfit_request" not in bindings
    assert "_validate_add_wardrobe_outfit_request" not in bindings
    assert "preview_add_outfit_part_sync" not in bindings
    assert "add_outfit_part_sync" not in bindings
    assert "build_add_outfit_part_request" not in bindings
    assert "_validate_add_outfit_part_request" not in bindings
    assert "preview_add_modular_avatar_component_sync" not in bindings
    assert "add_modular_avatar_component_sync" not in bindings
    assert "build_add_modular_avatar_component_request" not in bindings
    assert "_validate_add_modular_avatar_component_request" not in bindings
    assert "preview_manage_wardrobe_sync" not in bindings
    assert "manage_wardrobe_sync" not in bindings
    assert "build_manage_wardrobe_request" not in bindings
    assert "_validate_manage_wardrobe_request" not in bindings
    assert "_coerce_int_list" not in bindings
    assert "build_create_wardrobe_request" not in bindings
    assert "_validate_create_wardrobe_request" not in bindings
    assert "_create_wardrobe_primitive_args" not in bindings
    assert "preview_create_wardrobe_sync" not in bindings
    assert "create_wardrobe_sync" not in bindings
    assert "_prepare_add_outfit_state" not in bindings
    assert "preview_add_outfit_workflow_sync" not in bindings
    assert "prepare_add_outfit_request" not in bindings
    assert "add_outfit_workflow_approved_sync" not in bindings
    assert "_coerce_path_list" not in bindings
    assert "scan_avatar_items_sync" not in bindings
    assert "scan_avatar_controls_sync" not in bindings
    assert "scan_wardrobe_sync" not in bindings
    assert "scan_clothes=CLOTHING_FX_READ.scan_clothes" in source
    assert "generate_clothing_fx=CLOTHING_FX_READ.generate_clothing_fx" in source
    assert (
        "preview_apply_clothing_fx=CLOTHING_FX_READ.preview_apply_clothing_fx"
        in source
    )
    assert "apply_clothing_fx_approved_sync" not in bindings
    assert "CLOTHING_FX_APPROVED_WRITE = ClothingFxApprovedWriteService(" in source
    assert "preview=CLOTHING_FX_READ.preview_apply_clothing_fx" in source
    assert "apply_clothing_fx=CLOTHING_FX_APPROVED_WRITE.execute" in source
    assert (
        "WARDROBE_OUTFIT_APPROVED_WRITES.apply_clothing_fx" in source
    )
    assert "preview_setup_outfit=SETUP_OUTFIT_PREVIEW.preview" in source
    assert "setup_outfit=SETUP_OUTFIT_APPROVED_WRITE.execute" in source
    assert (
        "preview_add_wardrobe_outfit=ADD_WARDROBE_OUTFIT_PREVIEW.preview"
        in source
    )
    assert (
        "add_wardrobe_outfit=ADD_WARDROBE_OUTFIT_APPROVED_WRITE.execute"
        in source
    )
    assert source.count("build_request=build_owned_add_wardrobe_outfit_request") == 2
    assert "preview_add_outfit_part=ADD_OUTFIT_PART_PREVIEW.preview" in source
    assert "add_outfit_part=ADD_OUTFIT_PART_APPROVED_WRITE.execute" in source
    assert source.count("build_request=build_owned_add_outfit_part_request") == 2
    assert (
        "preview_add_modular_avatar_component="
        "ADD_MODULAR_AVATAR_COMPONENT_PREVIEW.preview"
    ) in source
    assert (
        "add_modular_avatar_component="
        "ADD_MODULAR_AVATAR_COMPONENT_APPROVED_WRITE.execute"
    ) in source
    assert (
        source.count(
            "build_request=build_owned_add_modular_avatar_component_request"
        )
        == 2
    )
    assert "preview_manage_wardrobe=MANAGE_WARDROBE_PREVIEW.preview" in source
    assert "manage_wardrobe=MANAGE_WARDROBE_APPROVED_WRITE.execute" in source
    assert source.count("build_request=build_owned_manage_wardrobe_request") == 2
    assert "preview_create_wardrobe=CREATE_WARDROBE_PREVIEW.preview" in source
    assert "create_wardrobe=CREATE_WARDROBE_APPROVED_WRITE.execute" in source
    assert source.count("build_calls=build_owned_create_wardrobe_core_calls") == 2
    assert source.count("build_request=build_owned_create_wardrobe_request") == 2
    assert (
        "preview_add_outfit=PREPARED_ADD_OUTFIT_PREVIEW.preview" in source
    )
    assert (
        "prepare_add_outfit=PREPARED_ADD_OUTFIT_PREPARER.prepare" in source
    )
    assert (
        "add_outfit=PREPARED_ADD_OUTFIT_APPROVED_WRITE.execute" in source
    )

    assignments = {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    for owner_name, expected_preview in (
        ("CREATE_WARDROBE_PREVIEW", True),
        ("CREATE_WARDROBE_APPROVED_WRITE", False),
    ):
        owner_call = assignments[owner_name]
        assert isinstance(owner_call, ast.Call)
        ports_call = owner_call.args[0]
        assert isinstance(ports_call, ast.Call)
        step_bindings = {
            keyword.arg: keyword.value
            for keyword in ports_call.keywords
            if keyword.arg in {"ensure_parameter", "ensure_animator", "ensure_menu"}
        }
        assert set(step_bindings) == {
            "ensure_parameter",
            "ensure_animator",
            "ensure_menu",
        }
        for binding in step_bindings.values():
            assert isinstance(binding, ast.Lambda)
            assert len(binding.args.args) == 1
            assert isinstance(binding.body, ast.Call)
            assert len(binding.body.args) == 1
            assert [keyword.arg for keyword in binding.body.keywords] == ["preview"]
            assert isinstance(binding.body.keywords[0].value, ast.Constant)
            assert binding.body.keywords[0].value.value is expected_preview

    flattened_source = " ".join(source.split())
    assert "primitive_live_guard_fields=lambda params:" in flattened_source
    assert "_primitive_live_guard_fields(" in flattened_source
    assert not any(
        isinstance(node, ast.ImportFrom)
        and any(
            alias.name == "build_add_wardrobe_outfit_request"
            and alias.asname is None
            for alias in node.names
        )
        for node in tree.body
    )
    assert not any(
        isinstance(node, ast.ImportFrom)
        and any(
            alias.name == "build_manage_wardrobe_request"
            and alias.asname is None
            for alias in node.names
        )
        for node in tree.body
    )
    assert not any(
        isinstance(node, ast.ImportFrom)
        and any(
            alias.name == "build_create_wardrobe_request"
            and alias.asname is None
            for alias in node.names
        )
        for node in tree.body
    )
    assert not any(
        isinstance(node, ast.ImportFrom)
        and any(
            alias.name == "build_add_modular_avatar_component_request"
            and alias.asname is None
            for alias in node.names
        )
        for node in tree.body
    )
    assert not any(
        isinstance(node, ast.ImportFrom)
        and any(
            alias.name == "build_add_outfit_part_request"
            and alias.asname is None
            for alias in node.names
        )
        for node in tree.body
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


def test_setup_outfit_owners_separate_preview_from_single_approved_poll_owner() -> None:
    calls: list[tuple[Any, ...]] = []
    settings = object()

    def load_settings(params: dict[str, Any]) -> Any:
        calls.append(("settings", params))
        return settings

    def preview(actual_settings: Any, request: dict[str, Any]) -> dict[str, Any]:
        calls.append(("preview", actual_settings, request))
        return {"ready": True}

    def approved(actual_settings: Any, request: dict[str, Any]) -> dict[str, Any]:
        calls.append(("approved", actual_settings, request))
        return {
            "pending": True,
            "status": "pending",
            "jobId": "job-1",
            "outfitGlobalObjectId": "global-running",
            "mutationStarted": True,
        }

    def poll(actual_settings: Any, job_id: str) -> dict[str, Any]:
        calls.append(("poll", actual_settings, job_id))
        return {
            "ok": False,
            "pending": False,
            "status": "unavailable",
            "jobId": job_id,
        }

    preview_service = SetupOutfitPreviewService(
        SetupOutfitPreviewPorts(
            load_settings=load_settings,
            invoke_preview=preview,
        )
    )
    approved_service = SetupOutfitApprovedWriteService(
        SetupOutfitApprovedWritePorts(
            load_settings=load_settings,
            start_approved=approved,
            poll_existing_job=poll,
            retryable_poll_error=RuntimeError,
            monotonic=lambda: 0.0,
            sleep=lambda _seconds: None,
            log=lambda level, scope, message, data=None: calls.append(
                ("log", level, scope, message, data)
            ),
        )
    )
    params = {
        "avatarPath": "Avatar",
        "outfitPath": "Avatar/Outfit",
        "setupOutfitPollIntervalSeconds": 0,
        "setupOutfitPollTimeoutSeconds": 1,
    }

    preview_payload = preview_service.preview(params)
    approved_payload = approved_service.execute(params)

    assert preview_payload == {"ready": True, "ok": True}
    assert approved_payload["status"] == "unavailable"
    assert approved_payload["commitState"] == "unknown"
    assert sum(call[0] == "preview" for call in calls) == 1
    assert sum(call[0] == "approved" for call in calls) == 1
    assert sum(call[0] == "poll" for call in calls) == 1
    assert next(call for call in calls if call[0] == "preview")[2]["confirmSetup"] is False
    assert next(call for call in calls if call[0] == "approved")[2]["confirmSetup"] is True


def test_setup_outfit_owners_missing_target_have_no_settings_or_unity_side_effects() -> None:
    calls: list[str] = []
    preview_service = SetupOutfitPreviewService(
        SetupOutfitPreviewPorts(
            load_settings=lambda _params: calls.append("settings"),
            invoke_preview=lambda _settings, _request: calls.append("preview"),
        )
    )
    approved_service = SetupOutfitApprovedWriteService(
        SetupOutfitApprovedWritePorts(
            load_settings=lambda _params: calls.append("settings"),
            start_approved=lambda _settings, _request: calls.append("approved"),
            poll_existing_job=lambda _settings, _job_id: calls.append("poll"),
            retryable_poll_error=RuntimeError,
            monotonic=lambda: 0.0,
            sleep=lambda _seconds: None,
            log=lambda *_args, **_kwargs: calls.append("log"),
        )
    )

    assert preview_service.preview({}) == {
        "ok": False,
        "error": "outfitPath is required.",
    }
    assert approved_service.execute({}) == {
        "ok": False,
        "error": "outfitPath is required.",
    }
    assert calls == []


def test_setup_outfit_nan_poll_values_preserve_safe_minimum_clamp() -> None:
    assert coerce_setup_outfit_float_param(
        {"timeout": "nan"},
        ("timeout",),
        180.0,
        0.0,
        3600.0,
    ) == 0.0


def test_add_wardrobe_outfit_owners_separate_preview_and_approved_capabilities() -> None:
    calls: list[tuple[Any, ...]] = []
    settings = object()

    def build_request(
        params: dict[str, Any], preview: bool
    ) -> dict[str, Any]:
        calls.append(("build", params, preview))
        return {
            "avatarPath": params.get("avatarPath", ""),
            "parameterName": params.get("parameterName", ""),
            "outfitName": params.get("outfitName", ""),
            "objectPaths": params.get("objectPaths", []),
            "preview": preview,
        }

    def load_settings(params: dict[str, Any]) -> Any:
        calls.append(("settings", params))
        return settings

    preview_service = AddWardrobeOutfitPreviewService(
        AddWardrobeOutfitPreviewPorts(
            build_request=build_request,
            load_settings=load_settings,
            invoke_preview=lambda actual_settings, request: (
                calls.append(("preview", actual_settings, request))
                or {"plan": {"value": 3}}
            ),
        )
    )
    approved_service = AddWardrobeOutfitApprovedWriteService(
        AddWardrobeOutfitApprovedWritePorts(
            build_request=build_request,
            load_settings=load_settings,
            invoke_approved=lambda actual_settings, request: (
                calls.append(("approved", actual_settings, request))
                or {"assignedValue": 3}
            ),
            log=lambda level, scope, message, data=None: calls.append(
                ("log", level, scope, message, data)
            ),
        )
    )
    params = {
        "avatarPath": "Scene/Avatar",
        "parameterName": "Clothes",
        "outfitName": "Hoodie",
        "objectPaths": ["Outfits/Hoodie"],
    }

    assert preview_service.preview(params) == {
        "plan": {"value": 3},
        "ok": True,
    }
    assert approved_service.execute(params) == {
        "assignedValue": 3,
        "ok": True,
    }
    assert set(AddWardrobeOutfitPreviewPorts.__dataclass_fields__) == {
        "build_request",
        "load_settings",
        "invoke_preview",
    }
    assert [call[2] for call in calls if call[0] == "build"] == [True, False]
    assert sum(call[0] == "preview" for call in calls) == 1
    assert sum(call[0] == "approved" for call in calls) == 1
    assert (
        "log",
        "info",
        "wardrobe",
        "Wardrobe outfit added.",
        {"parameterName": "Clothes", "outfitName": "Hoodie"},
    ) in calls


def test_add_wardrobe_outfit_paths_preserve_legacy_scalar_and_tuple_coercion() -> None:
    request = build_add_wardrobe_outfit_request(
        {
            "parameterName": "Clothes",
            "outfitName": "Hoodie",
            "object_paths": "Avatar/A;Avatar/B\nAvatar/C",
            "objectPaths": ("Avatar/Tuple", "Avatar/Tuple"),
            "onObjectPaths": 42,
        },
        True,
    )

    assert request["objectPaths"] == [
        "Avatar/A;Avatar/B\nAvatar/C",
        "Avatar/Tuple",
        "42",
    ]


@pytest.mark.parametrize(
    ("params", "error"),
    [
        (
            {"outfitName": "Hoodie", "objectPaths": ["Outfits/Hoodie"]},
            "parameterName is required (the existing int wardrobe parameter).",
        ),
        (
            {"parameterName": "Clothes", "objectPaths": ["Outfits/Hoodie"]},
            "outfitName is required (display name for the new outfit).",
        ),
        (
            {"parameterName": "Clothes", "outfitName": "Hoodie"},
            "objectPaths is required (the new outfit's scene objects to turn on).",
        ),
    ],
)
def test_add_wardrobe_outfit_validation_has_no_settings_or_unity_side_effects(
    params: dict[str, Any], error: str
) -> None:
    calls: list[str] = []

    def build_request(
        arguments: dict[str, Any], preview: bool
    ) -> dict[str, Any]:
        return {
            "parameterName": arguments.get("parameterName", ""),
            "outfitName": arguments.get("outfitName", ""),
            "objectPaths": arguments.get("objectPaths", []),
            "preview": preview,
        }

    preview_service = AddWardrobeOutfitPreviewService(
        AddWardrobeOutfitPreviewPorts(
            build_request=build_request,
            load_settings=lambda _params: calls.append("settings"),
            invoke_preview=lambda _settings, _request: calls.append("preview"),
        )
    )
    approved_service = AddWardrobeOutfitApprovedWriteService(
        AddWardrobeOutfitApprovedWritePorts(
            build_request=build_request,
            load_settings=lambda _params: calls.append("settings"),
            invoke_approved=lambda _settings, _request: calls.append("approved"),
            log=lambda *_args, **_kwargs: calls.append("log"),
        )
    )

    expected = {"ok": False, "error": error}
    assert preview_service.preview(params) == expected
    assert approved_service.execute(params) == expected
    assert calls == []


def test_add_outfit_part_owners_separate_preview_and_approved_capabilities() -> None:
    calls: list[tuple[Any, ...]] = []
    settings = object()

    def load_settings(params: dict[str, Any]) -> Any:
        calls.append(("settings", params))
        return settings

    preview_service = AddOutfitPartPreviewService(
        AddOutfitPartPreviewPorts(
            build_request=build_add_outfit_part_request,
            load_settings=load_settings,
            invoke_preview=lambda actual_settings, request: (
                calls.append(("preview", actual_settings, request))
                or {"plan": {"partParameterName": "Hat"}}
            ),
        )
    )
    approved_service = AddOutfitPartApprovedWriteService(
        AddOutfitPartApprovedWritePorts(
            build_request=build_add_outfit_part_request,
            load_settings=load_settings,
            invoke_approved=lambda actual_settings, request: (
                calls.append(("approved", actual_settings, request))
                or {"assignedPartParameter": "Hat"}
            ),
            log=lambda level, scope, message, data=None: calls.append(
                ("log", level, scope, message, data)
            ),
        )
    )
    params = {
        "avatarPath": "Scene/Avatar",
        "parameterName": "Clothes",
        "partName": "Hat",
        "outfitValue": 2,
        "objectPaths": ["Outfits/Hoodie/Hat"],
    }

    assert preview_service.preview(params) == {
        "plan": {"partParameterName": "Hat"},
        "ok": True,
    }
    assert approved_service.execute(params) == {
        "assignedPartParameter": "Hat",
        "ok": True,
    }
    assert set(AddOutfitPartPreviewPorts.__dataclass_fields__) == {
        "build_request",
        "load_settings",
        "invoke_preview",
    }
    assert next(call for call in calls if call[0] == "preview")[2]["preview"] is True
    assert next(call for call in calls if call[0] == "approved")[2]["preview"] is False
    assert sum(call[0] == "preview" for call in calls) == 1
    assert sum(call[0] == "approved" for call in calls) == 1
    assert (
        "log",
        "info",
        "wardrobe",
        "Outfit part added.",
        {"parameterName": "Clothes", "partName": "Hat", "value": 2},
    ) in calls


@pytest.mark.parametrize(
    ("params", "error"),
    [
        (
            {
                "partName": "Hat",
                "value": 2,
                "objectPaths": ["Outfits/Hat"],
            },
            (
                "parameterName is required (the existing int wardrobe parameter "
                "the part is gated on)."
            ),
        ),
        (
            {
                "parameterName": "Clothes",
                "value": 2,
                "objectPaths": ["Outfits/Hat"],
            },
            "partName is required (display name for the new part toggle).",
        ),
        (
            {
                "parameterName": "Clothes",
                "partName": "Hat",
                "objectPaths": ["Outfits/Hat"],
            },
            "value is required (the wardrobe int value N this part belongs to).",
        ),
        (
            {"parameterName": "Clothes", "partName": "Hat", "value": 2},
            "objectPaths is required (the part's scene objects to toggle on/off).",
        ),
    ],
)
def test_add_outfit_part_validation_has_no_settings_or_unity_side_effects(
    params: dict[str, Any],
    error: str,
) -> None:
    calls: list[str] = []
    preview_service = AddOutfitPartPreviewService(
        AddOutfitPartPreviewPorts(
            build_request=build_add_outfit_part_request,
            load_settings=lambda _params: calls.append("settings"),
            invoke_preview=lambda _settings, _request: calls.append("preview"),
        )
    )
    approved_service = AddOutfitPartApprovedWriteService(
        AddOutfitPartApprovedWritePorts(
            build_request=build_add_outfit_part_request,
            load_settings=lambda _params: calls.append("settings"),
            invoke_approved=lambda _settings, _request: calls.append("approved"),
            log=lambda *_args, **_kwargs: calls.append("log"),
        )
    )

    expected = {"ok": False, "error": error}
    assert preview_service.preview(params) == expected
    assert approved_service.execute(params) == expected
    assert calls == []


def test_modular_avatar_component_owners_separate_preview_and_approved_capabilities() -> None:
    calls: list[tuple[Any, ...]] = []
    settings = object()

    def load_settings(params: dict[str, Any]) -> Any:
        calls.append(("settings", params))
        return settings

    preview_service = AddModularAvatarComponentPreviewService(
        AddModularAvatarComponentPreviewPorts(
            build_request=build_add_modular_avatar_component_request,
            load_settings=load_settings,
            invoke_preview=lambda actual_settings, request: (
                calls.append(("preview", actual_settings, request))
                or {"componentType": "MergeArmature"}
            ),
        )
    )
    approved_service = AddModularAvatarComponentApprovedWriteService(
        AddModularAvatarComponentApprovedWritePorts(
            primitive_live_connection=lambda: None,
            primitive_live_guard_fields=lambda _params: {},
            build_request=build_add_modular_avatar_component_request,
            load_settings=load_settings,
            invoke_approved=lambda actual_settings, request: (
                calls.append(("approved", actual_settings, request))
                or {"addedComponent": True}
            ),
            log=lambda level, scope, message, data=None: calls.append(
                ("log", level, scope, message, data)
            ),
        )
    )
    params = {
        "avatarPath": "Scene/Avatar",
        "gameObjectPath": "Scene/Avatar/Outfit",
        "componentType": "MergeArmature",
        "saveScene": "yes",
        "references": {"mergeTarget": "Armature"},
        "fields": {"prefix": "", "nested": {"enabled": True}},
    }

    assert preview_service.preview(params) == {
        "componentType": "MergeArmature",
        "ok": True,
    }
    assert approved_service.execute(params) == {
        "addedComponent": True,
        "ok": True,
    }
    assert set(AddModularAvatarComponentPreviewPorts.__dataclass_fields__) == {
        "build_request",
        "load_settings",
        "invoke_preview",
    }
    preview_request = next(call for call in calls if call[0] == "preview")[2]
    approved_request = next(call for call in calls if call[0] == "approved")[2]
    assert preview_request["preview"] is True
    assert approved_request["preview"] is False
    assert preview_request["saveScene"] is True
    assert approved_request["references"] == {"mergeTarget": "Armature"}
    assert approved_request["references"] is params["references"]
    assert approved_request["fields"] == {
        "prefix": "",
        "nested": {"enabled": True},
    }
    assert approved_request["fields"] is params["fields"]
    assert (
        "log",
        "info",
        "modular_avatar",
        "Modular Avatar component added.",
        {
            "gameObjectPath": "Scene/Avatar/Outfit",
            "componentType": "MergeArmature",
        },
    ) in calls


def test_modular_avatar_component_approved_live_shortcut_has_no_generic_unity_side_effects() -> None:
    calls: list[str] = []

    class LiveConnection:
        def apply_component(self, _params: dict[str, Any]) -> dict[str, Any]:
            calls.append("primitive-live")
            return {"ok": True, "live": True}

    service = AddModularAvatarComponentApprovedWriteService(
        AddModularAvatarComponentApprovedWritePorts(
            primitive_live_connection=LiveConnection,
            primitive_live_guard_fields=lambda _params: {"sessionId": "live-1"},
            build_request=lambda _params, _preview: (
                calls.append("build") or {}
            ),
            load_settings=lambda _params: calls.append("settings"),
            invoke_approved=lambda _settings, _request: calls.append("approved"),
            log=lambda *_args, **_kwargs: calls.append("log"),
        )
    )

    assert service.execute({"primitiveLive": {"sessionId": "live-1"}}) == {
        "ok": True,
        "live": True,
    }
    assert calls == ["primitive-live"]


@pytest.mark.parametrize(
    ("params", "error"),
    [
        (
            {"componentType": "MergeArmature"},
            (
                "gameObjectPath is required (the scene object to add the Modular "
                "Avatar component to)."
            ),
        ),
        (
            {"gameObjectPath": "Avatar/Outfit"},
            (
                "componentType is required (e.g. MergeArmature, BoneProxy, "
                "MenuInstaller, MergeAnimator, Parameters)."
            ),
        ),
    ],
)
def test_modular_avatar_component_validation_has_no_unity_side_effects(
    params: dict[str, Any],
    error: str,
) -> None:
    calls: list[str] = []
    preview_service = AddModularAvatarComponentPreviewService(
        AddModularAvatarComponentPreviewPorts(
            build_request=build_add_modular_avatar_component_request,
            load_settings=lambda _params: calls.append("settings"),
            invoke_preview=lambda _settings, _request: calls.append("preview"),
        )
    )
    approved_service = AddModularAvatarComponentApprovedWriteService(
        AddModularAvatarComponentApprovedWritePorts(
            primitive_live_connection=lambda: None,
            primitive_live_guard_fields=lambda _params: (
                calls.append("live-check") or {}
            ),
            build_request=build_add_modular_avatar_component_request,
            load_settings=lambda _params: calls.append("settings"),
            invoke_approved=lambda _settings, _request: calls.append("approved"),
            log=lambda *_args, **_kwargs: calls.append("log"),
        )
    )

    expected = {"ok": False, "error": error}
    assert preview_service.preview(params) == expected
    assert approved_service.execute(params) == expected
    assert calls == []


def test_manage_wardrobe_owners_separate_preview_and_approved_capabilities() -> None:
    calls: list[tuple[Any, ...]] = []
    settings = object()

    def load_settings(params: dict[str, Any]) -> Any:
        calls.append(("settings", params))
        return settings

    preview_service = ManageWardrobePreviewService(
        ManageWardrobePreviewPorts(
            build_request=build_manage_wardrobe_request,
            load_settings=load_settings,
            invoke_preview=lambda actual_settings, request: (
                calls.append(("preview", actual_settings, request))
                or {"plan": {"action": "rename_outfit"}}
            ),
        )
    )
    approved_service = ManageWardrobeApprovedWriteService(
        ManageWardrobeApprovedWritePorts(
            build_request=build_manage_wardrobe_request,
            load_settings=load_settings,
            invoke_approved=lambda actual_settings, request: (
                calls.append(("approved", actual_settings, request))
                or {"action": "rename_outfit"}
            ),
            log=lambda level, scope, message, data=None: calls.append(
                ("log", level, scope, message, data)
            ),
        )
    )
    params = {
        "avatarPath": "Scene/Avatar",
        "parameterName": "Clothes",
        "action": "rename_outfit",
        "value": 3,
        "newName": "Coat",
    }

    assert preview_service.preview(params) == {
        "plan": {"action": "rename_outfit"},
        "ok": True,
    }
    assert approved_service.execute(params) == {
        "action": "rename_outfit",
        "ok": True,
    }
    assert set(ManageWardrobePreviewPorts.__dataclass_fields__) == {
        "build_request",
        "load_settings",
        "invoke_preview",
    }
    assert next(call for call in calls if call[0] == "preview")[2]["preview"] is True
    assert next(call for call in calls if call[0] == "approved")[2]["preview"] is False
    assert (
        "log",
        "info",
        "wardrobe",
        "Wardrobe management action executed.",
        {"parameterName": "Clothes", "action": "rename_outfit"},
    ) in calls


@pytest.mark.parametrize(
    ("params", "error"),
    [
        (
            {"parameterName": "Clothes"},
            "action is required for wardrobe management.",
        ),
        (
            {"action": "remove_outfit", "targetValue": 3},
            "parameterName is required for wardrobe management.",
        ),
    ],
)
def test_manage_wardrobe_validation_has_no_settings_or_unity_side_effects(
    params: dict[str, Any],
    error: str,
) -> None:
    calls: list[str] = []
    preview_service = ManageWardrobePreviewService(
        ManageWardrobePreviewPorts(
            build_request=build_manage_wardrobe_request,
            load_settings=lambda _params: calls.append("settings"),
            invoke_preview=lambda _settings, _request: calls.append("preview"),
        )
    )
    approved_service = ManageWardrobeApprovedWriteService(
        ManageWardrobeApprovedWritePorts(
            build_request=build_manage_wardrobe_request,
            load_settings=lambda _params: calls.append("settings"),
            invoke_approved=lambda _settings, _request: calls.append("approved"),
            log=lambda *_args, **_kwargs: calls.append("log"),
        )
    )

    expected = {"ok": False, "error": error}
    assert preview_service.preview(params) == expected
    assert approved_service.execute(params) == expected
    assert calls == []


def test_create_wardrobe_preview_runs_all_fixed_steps_and_approved_is_fail_fast() -> None:
    preview_calls: list[tuple[str, dict[str, Any]]] = []

    def preview_step(name: str, ok: bool, error: str | None = None):
        def invoke(arguments: dict[str, Any]) -> dict[str, Any]:
            preview_calls.append((name, arguments))
            return {"ok": ok, "error": error} if error else {"ok": ok}

        return invoke

    preview = CreateWardrobePreviewService(
        CreateWardrobePreviewPorts(
            build_request=build_create_wardrobe_request,
            build_calls=build_create_wardrobe_core_calls,
            ensure_parameter=preview_step("parameter", False, "parameter failed"),
            ensure_animator=preview_step("animator", True),
            ensure_menu=preview_step("menu", False, "menu failed"),
        )
    )
    result = preview.preview({"parameterName": "Clothes"})

    assert result["ok"] is False
    assert result["preview"] is True
    assert result["error"] == "parameter failed"
    assert [name for name, _arguments in preview_calls] == [
        "parameter",
        "animator",
        "menu",
    ]
    assert all(arguments["preview"] is True for _name, arguments in preview_calls)

    approved_calls: list[str] = []
    approved = CreateWardrobeApprovedWriteService(
        CreateWardrobeApprovedWritePorts(
            build_request=build_create_wardrobe_request,
            build_calls=build_create_wardrobe_core_calls,
            ensure_parameter=lambda _arguments: (
                approved_calls.append("parameter") or {"ok": False, "error": "stop"}
            ),
            ensure_animator=lambda _arguments: (
                approved_calls.append("animator") or {"ok": True}
            ),
            ensure_menu=lambda _arguments: (
                approved_calls.append("menu") or {"ok": True}
            ),
            log=lambda *_args, **_kwargs: approved_calls.append("log"),
        )
    )

    assert approved.execute({"parameterName": "Clothes"}) == {
        "ok": False,
        "action": "create_wardrobe",
        "parameterName": "Clothes",
        "steps": [
            {
                "tool": "vrc_ensure_expression_parameter",
                "result": {"ok": False, "error": "stop"},
            }
        ],
        "error": "stop",
    }
    assert approved_calls == ["parameter"]


def test_create_wardrobe_approved_success_logs_after_three_fixed_steps() -> None:
    calls: list[tuple[Any, ...]] = []

    def step(name: str):
        def invoke(arguments: dict[str, Any]) -> dict[str, Any]:
            calls.append((name, arguments))
            return {"ok": True, "name": name}

        return invoke

    service = CreateWardrobeApprovedWriteService(
        CreateWardrobeApprovedWritePorts(
            build_request=build_create_wardrobe_request,
            build_calls=build_create_wardrobe_core_calls,
            ensure_parameter=step("parameter"),
            ensure_animator=step("animator"),
            ensure_menu=step("menu"),
            log=lambda level, scope, message, data=None: calls.append(
                ("log", level, scope, message, data)
            ),
        )
    )

    result = service.execute({"parameterName": "Clothes", "menuName": False})
    assert result["ok"] is True
    assert result["preview"] is False
    assert [call[0] for call in calls[:3]] == ["parameter", "animator", "menu"]
    assert all(call[1]["preview"] is False for call in calls[:3])
    assert calls[2][1]["menuPath"] == "Wardrobe"
    assert calls[3] == (
        "log",
        "info",
        "wardrobe",
        "Wardrobe skeleton created.",
        {"parameterName": "Clothes"},
    )
    assert set(CreateWardrobePreviewPorts.__dataclass_fields__) == {
        "build_request",
        "build_calls",
        "ensure_parameter",
        "ensure_animator",
        "ensure_menu",
    }
    assert set(CreateWardrobeApprovedWritePorts.__dataclass_fields__) == {
        "build_request",
        "build_calls",
        "ensure_parameter",
        "ensure_animator",
        "ensure_menu",
        "log",
    }


def test_create_wardrobe_validation_precedes_all_step_capabilities() -> None:
    calls: list[str] = []
    service = CreateWardrobeApprovedWriteService(
        CreateWardrobeApprovedWritePorts(
            build_request=build_create_wardrobe_request,
            build_calls=lambda _params, _preview: calls.append("build-calls") or [],
            ensure_parameter=lambda _arguments: calls.append("parameter") or {},
            ensure_animator=lambda _arguments: calls.append("animator") or {},
            ensure_menu=lambda _arguments: calls.append("menu") or {},
            log=lambda *_args, **_kwargs: calls.append("log"),
        )
    )

    assert service.execute({"parameter_name": " "}) == {
        "ok": False,
        "error": "parameterName is required for wardrobe creation.",
    }
    assert calls == []


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
