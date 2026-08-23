from __future__ import annotations

import importlib
import os
import socket
import subprocess
import sys
from pathlib import Path

from agent_mcp_2026 import PROTOCOL_VERSION
from avatar_composition_workflow_skills import AVATAR_COMPOSITION_WORKFLOW_SKILLS


def test_stdio_bridge_forces_utf8_over_a_legacy_windows_code_page() -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from tools.vrcforge_agent_mcp_stdio import configure_utf8_stdio;"
                "configure_utf8_stdio();"
                "print('衣服/表情')"
            ),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert completed.stdout.decode("utf-8").strip() == "衣服/表情"


def test_find_vrcforge_executable_prefers_current_packaged_root(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")
    current_root = tmp_path / "Current VRCForge"
    backend = current_root / "backend" / "vrcforge_backend.exe"
    desktop = current_root / "VRCForge.exe"
    old_program_files = tmp_path / "Program Files"
    old_desktop = old_program_files / "VRCForge" / "VRCForge.exe"
    backend.parent.mkdir(parents=True)
    old_desktop.parent.mkdir(parents=True)
    backend.write_text("backend", encoding="utf-8")
    desktop.write_text("desktop", encoding="utf-8")
    old_desktop.write_text("old desktop", encoding="utf-8")

    monkeypatch.delenv("VRCFORGE_EXE", raising=False)
    monkeypatch.setenv("ProgramFiles", str(old_program_files))
    monkeypatch.setenv("ProgramFiles(x86)", "")
    monkeypatch.setattr(module.sys, "executable", str(backend))
    monkeypatch.setattr(module.sys, "frozen", True, raising=False)

    assert module.find_vrcforge_executable() == desktop.resolve()


def test_preflight_does_not_launch_runtime_when_start_disabled(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")
    config = tmp_path / "agent_gateway.json"
    config.write_text('{"token":"test-token","enabled":true,"allow_write_requests":true}', encoding="utf-8")
    bridge = module.VRCForgeBridge(
        base_url="http://127.0.0.1:8757",
        config_path=config,
        timeout_seconds=0.1,
        start_runtime=False,
    )

    monkeypatch.setattr(bridge, "runtime_port_open", lambda: False)

    def fail_launch() -> dict[str, object]:
        raise AssertionError("try_launch_runtime should not be called when start_runtime is false")

    def offline_request(*args, **kwargs) -> dict[str, object]:
        raise RuntimeError("runtime offline")

    monkeypatch.setattr(bridge, "try_launch_runtime", fail_launch)
    monkeypatch.setattr(bridge, "request_json", offline_request)

    report = bridge.preflight()

    assert report["ok"] is False
    assert "launch" not in report
    assert report["error"] == "runtime offline"
    assert report["errorDetails"]["schema"] == "vrcforge.external_tool_error.v1"
    assert report["errorDetails"]["failureLayer"] == "external_stdio_http_transport"
    assert report["errorDetails"]["toolRoutingStarted"] is False
    assert report["errorDetails"]["mutationStarted"] is False
    assert report["errorDetails"]["commitState"] == "not_started"


def test_preflight_missing_token_uses_canonical_external_error(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")
    config = tmp_path / "agent_gateway.json"
    config.write_text('{"enabled":true,"allow_write_requests":true}', encoding="utf-8")
    monkeypatch.delenv("VRCFORGE_AGENT_TOKEN", raising=False)
    bridge = module.VRCForgeBridge(
        base_url="http://127.0.0.1:8757",
        config_path=config,
        timeout_seconds=0.1,
        start_runtime=False,
    )

    report = bridge.preflight()

    assert report["status"] == "gateway_token_missing"
    assert report["errorDetails"]["schema"] == "vrcforge.external_tool_error.v1"
    assert report["errorDetails"]["failureLayer"] == "external_stdio_authentication"
    assert report["errorDetails"]["mutationStarted"] is False
    assert report["errorDetails"]["committed"] is False
    assert report["errorDetails"]["commitState"] == "not_started"


def test_preflight_http_rejection_preserves_gateway_layer_and_raw_result(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")
    config = tmp_path / "agent_gateway.json"
    config.write_text('{"token":"test-token","enabled":false,"allow_write_requests":true}', encoding="utf-8")
    bridge = module.VRCForgeBridge(
        base_url="http://127.0.0.1:8757",
        config_path=config,
        timeout_seconds=0.1,
        start_runtime=False,
    )

    def reject_request(*args, **kwargs) -> dict[str, object]:
        raise module.ExternalHttpBridgeError(
            status_code=403,
            path="/mcp",
            body='{"ok":false,"error":"Agent Gateway is disabled."}',
        )

    monkeypatch.setattr(bridge, "_mcp_request", reject_request)

    report = bridge.preflight()

    assert report["status"] == "gateway_http_rejection"
    assert report["errorCode"] == "http_403"
    assert report["failureLayer"] == "external_gateway_http"
    assert report["error"] == "Agent Gateway is disabled."
    assert report["errorDetails"]["details"] == {"httpStatus": 403, "path": "/mcp", "baseUrl": "http://127.0.0.1:8757"}
    assert report["errorDetails"]["rawResult"] == {"ok": False, "error": "Agent Gateway is disabled."}
    assert report["errorDetails"]["toolRoutingStarted"] is False
    assert report["errorDetails"]["mutationStarted"] is False
    assert report["errorDetails"]["commitState"] == "not_started"


def test_runtime_launch_rejections_use_canonical_external_error(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")
    monkeypatch.setattr(module, "find_vrcforge_executable", lambda: None)
    bridge = module.VRCForgeBridge(
        base_url="http://127.0.0.1:8757",
        config_path=tmp_path / "missing.json",
        timeout_seconds=0.1,
        start_runtime=True,
    )

    result = bridge.try_launch_runtime()

    assert result["status"] == "runtime_executable_not_found"
    assert result["errorDetails"]["schema"] == "vrcforge.external_tool_error.v1"
    assert result["errorDetails"]["failureLayer"] == "external_runtime_bootstrap"
    assert result["errorDetails"]["mutationStarted"] is False
    assert result["errorDetails"]["commitState"] == "not_started"


def test_stdio_bridge_start_runtime_is_explicit_opt_in() -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")

    assert module.parse_args([]).start_runtime is False
    assert module.parse_args(["--start-runtime"]).start_runtime is True
    parsed = module.parse_args(["--start-runtime", "--no-start"])
    assert parsed.start_runtime is True
    assert parsed.no_start is True


def test_stdio_bridge_protocol_profile_and_exposure_are_explicit() -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")

    defaults = module.parse_args([])
    assert defaults.protocol_profile == "auto"
    assert defaults.exposure_layer == "planning"
    assert defaults.timeout == 30.0
    assert defaults.tool_timeout == 360.0

    standard = module.parse_args(["--protocol-profile", "mcp-1x", "--exposure-layer", "planning"])
    assert standard.protocol_profile == "mcp-1x"
    assert standard.exposure_layer == "planning"


def test_stdio_bridge_exposes_writes_only_in_execution_layer(monkeypatch) -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")

    class Bridge:
        calls = []

        def preflight(self):
            return {"runtimeOnline": True}

        def manifest(self, exposure_layer="planning", tool_blocks=None):
            del tool_blocks
            read_tool = {
                "name": "vrcforge_read_status",
                "description": "Read status",
                "inputSchema": {"type": "object"},
                "_meta": {"toolBlock": "core"},
            }
            tools = [read_tool]
            if exposure_layer == "execution":
                tools.append(
                    {
                        "name": "vrcforge_create_gameobject",
                        "description": "Create a scene GameObject",
                        "inputSchema": {"type": "object"},
                        "write": True,
                        "_meta": {
                            "permission": "Write",
                            "confirmationPolicy": "risk_based",
                            "toolBlock": "avatar",
                        },
                    }
                )
            return {"tools": tools}

        def call_tool(self, tool_name, arguments, **_kwargs):
            self.calls.append((tool_name, arguments))
            return {"ok": True, "queued": tool_name}

    captured = {}
    monkeypatch.setattr(module, "run_stdio_loop", lambda router: captured.setdefault("router", router))
    module.run_stdio_server(Bridge(), protocol_profile="vrcforge-2026")
    router = captured["router"]
    meta = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "stdio-test", "version": "1.4.0"},
    }
    loaded, loaded_status = router.handle(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "tools/call",
            "params": {
                "_meta": meta,
                "name": "vrcforge_load_tool_block",
                "arguments": {"block": "avatar"},
            },
        }
    )
    assert loaded_status == 200
    assert loaded["result"]["structuredContent"]["ok"] is True

    planning, planning_status = router.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": meta}}
    )
    execution, execution_status = router.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {"_meta": meta, "exposureLayer": "execution"},
        }
    )

    assert planning_status == 200
    assert execution_status == 200
    planning_names = {tool["name"] for tool in planning["result"]["tools"]}
    execution_names = {tool["name"] for tool in execution["result"]["tools"]}
    assert "vrcforge_request_apply" not in planning_names
    assert "vrcforge_request_apply" not in execution_names
    assert "vrcforge_create_gameobject" not in planning_names
    assert "vrcforge_create_gameobject" in execution_names
    for tool in execution["result"]["tools"]:
        assert "When to use:" in tool["description"]
        assert "When NOT to use:" in tool["description"]
        assert "Negative example:" in tool["description"]

    called, called_status = router.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "_meta": meta,
                "name": "vrcforge_create_gameobject",
                "arguments": {"projectRoot": "D:/Avatar", "name": "External"},
            },
        }
    )
    assert called_status == 200
    assert called["result"]["structuredContent"]["ok"] is True
    assert Bridge.calls == [
        ("vrcforge_create_gameobject", {"projectRoot": "D:/Avatar", "name": "External"})
    ]


def test_stdio_bridge_loads_external_unity_tool_blocks_on_demand(monkeypatch) -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")

    class Bridge:
        calls = []

        def preflight(self):
            return {"runtimeOnline": True}

        def manifest(self, exposure_layer="planning", tool_blocks=None):
            del tool_blocks
            tools = [
                {
                    "name": "vrcforge_get_compile_errors",
                    "description": "Read Unity compile errors",
                    "inputSchema": {"type": "object"},
                    "_meta": {"toolBlock": "core"},
                },
                {
                    "name": "vrcforge_scan_blendshapes",
                    "description": "Scan avatar blendshapes",
                    "inputSchema": {"type": "object"},
                    "_meta": {"toolBlock": "avatar"},
                },
                {
                    "name": "vrcforge_scan_vrcfury",
                    "description": "Scan VRCFury",
                    "inputSchema": {"type": "object"},
                    "_meta": {"toolBlock": "integrations/vrcfury"},
                },
                {
                    "name": "vrcforge_gesture_manager_status",
                    "description": "Read Gesture Manager runtime status",
                    "inputSchema": {"type": "object"},
                    "_meta": {"toolBlock": "integrations/gesture-manager"},
                },
                {
                    "name": "vrcforge_preflight_skill_package",
                    "description": "Preflight one local VSK package",
                    "inputSchema": {"type": "object"},
                    "_meta": {"toolBlock": "skills/vsk"},
                },
            ]
            if exposure_layer == "execution":
                tools.append(
                    {
                        "name": "vrcforge_apply_blendshapes",
                        "description": "Apply avatar blendshapes",
                        "inputSchema": {"type": "object"},
                        "write": True,
                        "_meta": {"permission": "Write", "toolBlock": "avatar"},
                    }
                )
                tools.append(
                    {
                        "name": "vrcforge_gesture_manager_enter_play_mode",
                        "description": "Enter Gesture Manager Play Mode for one avatar",
                        "inputSchema": {"type": "object"},
                        "write": True,
                        "_meta": {
                            "permission": "Write",
                            "toolBlock": "integrations/gesture-manager",
                        },
                    }
                )
                tools.append(
                    {
                        "name": "vrcforge_gesture_manager_set_parameter",
                        "description": "Set one Gesture Manager runtime parameter",
                        "inputSchema": {"type": "object"},
                        "write": True,
                        "_meta": {
                            "permission": "Write",
                            "toolBlock": "integrations/gesture-manager",
                        },
                    }
                )
                tools.extend(
                    [
                        {
                            "name": "vrcforge_export_skill_package",
                            "description": "Export one user Skill to VSK",
                            "inputSchema": {"type": "object"},
                            "write": True,
                            "_meta": {"permission": "Write", "toolBlock": "skills/vsk"},
                        },
                        {
                            "name": "vrcforge_import_skill_package",
                            "description": "Import one local VSK package",
                            "inputSchema": {"type": "object"},
                            "write": True,
                            "_meta": {"permission": "Write", "toolBlock": "skills/vsk"},
                        },
                    ]
                )
            return {"tools": tools}

        def call_tool(self, tool_name, arguments, **_kwargs):
            self.calls.append((tool_name, arguments))
            return {"ok": True, "tool": tool_name}

    captured = {}
    monkeypatch.setattr(module, "run_stdio_loop", lambda router: captured.setdefault("router", router))
    module.run_stdio_server(Bridge(), protocol_profile="vrcforge-2026", exposure_layer="execution")
    router = captured["router"]
    meta = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "block-test", "version": "1"},
    }

    before, before_status = router.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": meta}}
    )
    before_names = {tool["name"] for tool in before["result"]["tools"]}
    assert before_status == 200
    assert "vrcforge_get_compile_errors" in before_names
    assert "vrcforge_scan_blendshapes" not in before_names
    assert "vrcforge_apply_blendshapes" not in before_names
    assert {
        "vrcforge_list_tool_blocks",
        "vrcforge_load_tool_block",
        "vrcforge_unload_tool_block",
    }.issubset(before_names)

    root, root_status = router.handle(
        {
            "jsonrpc": "2.0",
            "id": 20,
            "method": "tools/call",
            "params": {
                "_meta": meta,
                "name": "vrcforge_list_tool_blocks",
                "arguments": {},
            },
        }
    )
    assert root_status == 200
    root_tree = root["result"]["structuredContent"]["tree"]
    assert root_tree["index"] == "0"
    assert root_tree["name"] == "unity"
    assert [(node["index"], node["name"]) for node in root_tree["children"]] == [
        ("1", "core"),
        ("2", "project"),
        ("3", "avatar"),
        ("4", "assets"),
        ("5", "materials"),
        ("6", "integrations"),
        ("7", "optimization"),
        ("8", "checkpoint"),
        ("9", "diagnostics"),
        ("10", "encryption"),
        ("11", "skills"),
    ]
    root_nodes = {node["name"]: node for node in root_tree["children"]}
    assert root_nodes["core"]["toolNames"] == ["vrcforge_get_compile_errors"]
    assert root_nodes["avatar"]["toolNames"] == [
        "vrcforge_apply_blendshapes",
        "vrcforge_scan_blendshapes",
    ]
    assert root_nodes["avatar"]["workflowSkillNames"] == [
        "avatar-head-swap",
        "face-tracking-four-piece-merge",
        "original-avatar-part-extraction",
        "avatar-head-swap-face-tracked",
        "avatar-head-swap-gesture-only",
        "source-avatar-part-transplant",
    ]
    assert "workflowSkills" not in root_nodes["avatar"]
    integration_children = {
        node["name"]: node for node in root_nodes["integrations"]["children"]
    }
    assert integration_children["integrations/vrcfury"]["toolNames"] == [
        "vrcforge_scan_vrcfury"
    ]
    assert integration_children["integrations/modular-avatar"]["toolNames"] == []
    assert integration_children["integrations/gesture-manager"]["toolNames"] == [
        "vrcforge_gesture_manager_enter_play_mode",
        "vrcforge_gesture_manager_set_parameter",
        "vrcforge_gesture_manager_status",
    ]
    skills_children = {
        node["name"]: node for node in root_nodes["skills"]["children"]
    }
    assert skills_children["skills/vsk"]["toolNames"] == [
        "vrcforge_export_skill_package",
        "vrcforge_import_skill_package",
        "vrcforge_preflight_skill_package",
    ]
    assert root_nodes["project"]["toolNames"] == []
    assert "whenToUse" in root_nodes["avatar"]
    assert "whenNotToUse" in root_nodes["avatar"]
    assert "children" not in root_nodes["avatar"]
    compact_text = root["result"]["content"][0]["text"]
    assert "Full tool-name index is in structuredContent" in compact_text
    assert "vrcforge_apply_blendshapes" not in compact_text

    avatar_branch, avatar_branch_status = router.handle(
        {
            "jsonrpc": "2.0",
            "id": 22,
            "method": "tools/call",
            "params": {
                "_meta": meta,
                "name": "vrcforge_list_tool_blocks",
                "arguments": {"block": "3"},
            },
        }
    )
    assert avatar_branch_status == 200
    avatar_node = avatar_branch["result"]["structuredContent"]["tree"]["children"][0]
    workflow_skills = avatar_node["workflowSkills"]
    assert [skill["name"] for skill in workflow_skills] == avatar_node["workflowSkillNames"]
    assert all(skill["schema"] == "vrcforge.skill.v1" for skill in workflow_skills)
    assert all("checkpoint" in skill["backupRestore"].lower() for skill in workflow_skills)
    assert all("separately approved" in skill["backupRestore"].lower() for skill in workflow_skills)
    workflow_by_name = {skill["name"]: skill for skill in workflow_skills}
    for source in AVATAR_COMPOSITION_WORKFLOW_SKILLS:
        projected = workflow_by_name[source["name"]]
        for field in (
            "name",
            "title",
            "description",
            "whenToUse",
            "whenNotToUse",
            "backupRestore",
            "toolBlocks",
            "problemBreakdown",
            "acceptance",
            "pitfalls",
        ):
            assert projected[field] == source[field]
        assert [step["goal"] for step in projected["steps"]] == [
            step["goal"] for step in source["steps"]
        ]
        projected_tool_names = {
            ref["name"]
            for step in projected["steps"]
            for ref in step["toolRefs"]
        }
        assert projected_tool_names | set(projected["missingToolNames"]) == set(
            source["allowedTools"]
        )
    head_swap = workflow_skills[0]
    first_step_refs = {item["name"]: item for item in head_swap["steps"][0]["toolRefs"]}
    assert first_step_refs["vrcforge_scan_blendshapes"] == {
        "block": "avatar",
        "index": "3.2",
        "name": "vrcforge_scan_blendshapes",
        "mode": "read",
    }
    assert "vrcforge_list_avatars" in head_swap["missingToolNames"]
    assert not any(
        ref["name"].startswith("vrcforge_agent_")
        for skill in workflow_skills
        for step in skill["steps"]
        for ref in step["toolRefs"]
    )

    branch, branch_status = router.handle(
        {
            "jsonrpc": "2.0",
            "id": 21,
            "method": "tools/call",
            "params": {
                "_meta": meta,
                "name": "vrcforge_list_tool_blocks",
                "arguments": {"block": "6"},
            },
        }
    )
    assert branch_status == 200
    integration_node = branch["result"]["structuredContent"]["tree"]["children"][0]
    assert integration_node["index"] == "6"
    assert integration_node["name"] == "integrations"
    assert [child["index"] for child in integration_node["children"]] == [
        "6.1",
        "6.2",
        "6.3",
    ]
    vrcfury_node = next(
        child
        for child in integration_node["children"]
        if child["name"] == "integrations/vrcfury"
    )
    assert vrcfury_node["toolNames"] == ["vrcforge_scan_vrcfury"]
    gesture_manager_node = next(
        child
        for child in integration_node["children"]
        if child["name"] == "integrations/gesture-manager"
    )
    assert gesture_manager_node["toolNames"] == [
        "vrcforge_gesture_manager_enter_play_mode",
        "vrcforge_gesture_manager_set_parameter",
        "vrcforge_gesture_manager_status",
    ]

    loaded, loaded_status = router.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "_meta": meta,
                "name": "vrcforge_load_tool_block",
                "arguments": {"block": "3"},
            },
        }
    )
    assert loaded_status == 200
    assert loaded["result"]["structuredContent"]["loadedBlocks"] == ["avatar", "core"]

    after, after_status = router.handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"_meta": meta}}
    )
    after_names = {tool["name"] for tool in after["result"]["tools"]}
    assert after_status == 200
    assert "vrcforge_scan_blendshapes" in after_names
    assert "vrcforge_apply_blendshapes" in after_names
    assert Bridge.calls == []

    rejected, rejected_status = router.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "_meta": meta,
                "name": "vrcforge_unload_tool_block",
                "arguments": {"block": "core"},
            },
        }
    )
    assert rejected_status == 200
    rejection = rejected["result"]["structuredContent"]
    assert rejection["status"] == "core_block_required"
    assert rejection["errorDetails"]["schema"] == "vrcforge.external_tool_error.v1"
    assert rejection["errorDetails"]["failureLayer"] == "external_tool_discovery"
    assert rejection["errorDetails"]["mutationStarted"] is False

    skills_loaded, skills_loaded_status = router.handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "_meta": meta,
                "name": "vrcforge_load_tool_block",
                "arguments": {"block": "skills"},
            },
        }
    )
    assert skills_loaded_status == 200
    assert skills_loaded["result"]["structuredContent"]["loadedBlocks"] == [
        "avatar",
        "core",
        "skills/vsk",
    ]

    with_skills, with_skills_status = router.handle(
        {"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {"_meta": meta}}
    )
    with_skill_names = {tool["name"] for tool in with_skills["result"]["tools"]}
    assert with_skills_status == 200
    assert {
        "vrcforge_preflight_skill_package",
        "vrcforge_import_skill_package",
        "vrcforge_export_skill_package",
    }.issubset(with_skill_names)


def test_bridge_uses_http_mcp_for_manifest_and_tool_calls(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")
    config = tmp_path / "agent_gateway.json"
    config.write_text('{"token":"test-token","enabled":true,"allow_write_requests":true}', encoding="utf-8")
    bridge = module.VRCForgeBridge(
        base_url="http://127.0.0.1:8757",
        config_path=config,
        timeout_seconds=0.1,
        start_runtime=False,
    )
    calls: list[dict] = []

    def fake_request(method, path, **kwargs):
        calls.append({"method": method, "path": path, **kwargs})
        rpc_method = kwargs["payload"]["method"]
        if rpc_method == "tools/list":
            return {"result": {"tools": [{"name": "vrcforge_create_gameobject"}]}}
        return {"result": {"structuredContent": {"ok": True, "status": "executed"}}}

    monkeypatch.setattr(bridge, "request_json", fake_request)

    assert bridge.manifest("execution")["tools"][0]["name"] == "vrcforge_create_gameobject"
    assert bridge.call_tool("vrcforge_create_gameobject", {"name": "External"})["status"] == "executed"
    assert [call["path"] for call in calls] == ["/mcp", "/mcp"]
    assert calls[0]["extra_headers"]["Mcp-Method"] == "tools/list"
    assert calls[1]["extra_headers"]["Mcp-Method"] == "tools/call"
    assert calls[1]["extra_headers"]["Mcp-Name"] == "vrcforge_create_gameobject"
    assert calls[0]["timeout_seconds"] == 0.1
    assert calls[1]["timeout_seconds"] == 360.0


def test_tool_transport_timeout_is_structured_and_never_safe_to_blind_retry(
    monkeypatch, tmp_path: Path
) -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")
    config = tmp_path / "agent_gateway.json"
    config.write_text('{"token":"test-token"}', encoding="utf-8")
    bridge = module.VRCForgeBridge(
        base_url="http://127.0.0.1:8757",
        config_path=config,
        timeout_seconds=0.1,
        start_runtime=False,
    )

    def timeout(*_args, **_kwargs):
        raise socket.timeout("loopback response deadline exceeded")

    monkeypatch.setattr(bridge, "request_json", timeout)

    result = bridge.call_tool("vrcforge_import_outfit_package", {"projectPath": "D:/Avatar"})

    expected_compatibility = {
        "ok": False,
        "status": "transport_error",
        "failureLayer": "external_stdio_http_transport",
        "errorCode": "bridge_timeout",
        "error": "loopback response deadline exceeded",
        "mutationStarted": None,
        "committed": None,
        "commitState": "unknown",
        "requestMayHaveCommitted": True,
        "safeToRetry": False,
        "toolName": "vrcforge_import_outfit_package",
    }
    assert {key: result[key] for key in expected_compatibility} == expected_compatibility
    assert result["errorDetails"]["schema"] == "vrcforge.external_tool_error.v1"
    assert result["errorDetails"]["failureLayer"] == "external_stdio_http_transport"
    assert result["errorDetails"]["commitStateKnown"] is False
