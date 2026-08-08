import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

import dashboard_server
import unity_status_service
from agent_command_safety import normalize_filesystem_path
from agent_gateway import (
    AgentGateway,
    AgentGatewayError,
    CHECKPOINT_ARCHIVE_DEFAULT_MAX_SIZE_MB,
    redact_background_goal_persistence,
    redact_sensitive,
    summarize_text,
)
from agent_shell_service import (
    SHELL_RUNNER_NATIVE,
    SHELL_RUNNER_POWERSHELL,
    ShellProcessPorts,
    kill_process_tree,
    native_shell_argv,
    resolve_powershell_executable,
)
from wardrobe_outfit_workflow_service import (
    build_create_wardrobe_request,
    build_manage_wardrobe_request,
)
from agent_question_service import (
    AgentQuestionPersistence,
    AgentQuestionPersistencePorts,
    AgentQuestionScopePorts,
    AgentQuestionService,
    GoalQuestionResolutionPort,
)
from agent_goal_service import AgentGoalServiceError
from optimization_workflow_service import (
    OptimizationWorkflowService,
    OptimizerProofStore,
    OptimizerProofStorePorts,
)
from package_install_workflow_service import (
    PackageInstallWorkflowPorts,
    PackageInstallWorkflowService,
)
from provider_configuration_service import (
    ProviderApiConfig,
    ProviderConfigurationPersistencePorts,
    ProviderConfigurationService,
)
from provider_model_catalog_service import (
    ProviderModelCatalogSdkPorts,
    ProviderModelCatalogService,
)
from provider_test_integration_service import (
    ProviderTestIntegrationService,
    ProviderTestServicePorts,
)
from runtime_planner_service import (
    EXPOSURE_LAYER_EXECUTION,
    PlannerCatalogSnapshot,
    PlannerModelResult,
    PlannerSkill,
    PlannerTool,
    PlannerTurnMetadata,
    RUNTIME_PLANNER_TOOL_OBSERVATION_MAX_CHARS,
    RuntimePlannerService,
)
from skill_packages import SkillPackageError, SkillPackageService
from sub_agent_collaboration_service import SubAgentCollaborationService
from sub_agent_tasks import SubAgentRole, SubAgentTaskRegistry
from vrchat_blendshape_agent import BlendshapeAdjustment, BlendshapePlan, LlmPlanResponse


class _TestRuntimePlannerCatalog:
    def __init__(self, gateway: AgentGateway) -> None:
        self._gateway = gateway

    @staticmethod
    def _tool(value) -> PlannerTool:
        return PlannerTool(
            name=str(value.name),
            description=str(value.description),
            category=str(value.category),
            write=bool(value.write),
            advanced=bool(value.advanced),
            requires_user_activation=bool(value.requires_user_activation),
        )

    def read(self, exposure_layer: str) -> PlannerCatalogSnapshot:
        config = self._gateway.ensure_config()
        visible_tools = tuple(
            self._tool(tool)
            for tool in self._gateway._tools.values()
            if self._gateway._tool_visible(tool, config, exposure_layer)
        )
        skills = tuple(
            PlannerSkill(
                name=str(skill.get("name") or ""),
                title=str(skill.get("title") or ""),
                source=str(skill.get("source") or ""),
                skill_type=str(skill.get("skillType") or ""),
                category=str(skill.get("category") or ""),
                description=str(skill.get("description") or ""),
                when_to_use=str(skill.get("whenToUse") or ""),
                enabled=bool(skill.get("enabled", True)),
                disable_model_invocation=bool(skill.get("disableModelInvocation")),
            )
            for skill in self._gateway.build_skill_registry(config, EXPOSURE_LAYER_EXECUTION).get("skills") or []
            if isinstance(skill, dict) and str(skill.get("name") or "").strip()
        )
        return PlannerCatalogSnapshot(
            visible_tools=visible_tools,
            routable_tools=tuple(self._tool(tool) for tool in self._gateway._tools.values()),
            skills=skills,
            computer_use_model_invocable=self._gateway.desktop.computer_use_model_invocable(config),
        )


class _TestRuntimePlannerDesktop:
    def __init__(self, gateway: AgentGateway) -> None:
        self._gateway = gateway

    def summarize_action_result(self, result: object) -> str:
        return self._gateway.desktop.desktop_action_observation(result)


def _model_payload_from_final_plan(plan: Mapping[str, object]) -> dict[str, object]:
    if plan.get("skillNeeded"):
        return {
            "action": "skill",
            "skill_tool": plan.get("skillTool"),
            "skill_params": plan.get("skillParams") or {},
            "summary": plan.get("summary") or "",
            "reply": plan.get("reply") or "",
        }
    if plan.get("shellNeeded"):
        return {
            "action": "shell",
            "shell_command": plan.get("shellCommand") or "",
            "summary": plan.get("summary") or "",
            "reply": plan.get("reply") or "",
        }
    return {
        "action": "reply",
        "summary": plan.get("summary") or "",
        "reply": plan.get("reply") or "",
    }


class _TestRuntimePlannerModel:
    def __init__(self, respond: Callable[[str], object]) -> None:
        self._respond = respond

    def plan(self, prompt: str) -> PlannerModelResult:
        response = self._respond(prompt)
        if isinstance(response, PlannerModelResult):
            return response
        if not isinstance(response, Mapping):
            return PlannerModelResult(text=str(response or ""), planner_label="test")
        if any(key in response for key in ("text", "content", "response", "message")):
            text = str(
                response.get("text")
                or response.get("content")
                or response.get("response")
                or response.get("message")
                or ""
            )
            usage = response.get("usage") or response.get("tokenUsage") or {}
            reasoning = response.get("reasoning") or {}
        else:
            text = json.dumps(_model_payload_from_final_plan(response))
            usage = {}
            reasoning = {}
        return PlannerModelResult(
            text=text,
            usage=usage if isinstance(usage, Mapping) else {},
            reasoning=reasoning if isinstance(reasoning, Mapping) else {},
            planner_label=str(response.get("plannerLabel") or response.get("planner") or "test"),
        )


class _TestRuntimePlannerCompactor:
    def __init__(self, compact: Callable[[list[dict[str, object]], dict[str, object]], Mapping[str, object]]) -> None:
        self._compact = compact

    def compact(
        self,
        history: tuple[Mapping[str, object], ...],
        request: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self._compact([dict(entry) for entry in history], dict(request))


class _TestRuntimePlannerTurn:
    def bind(self, request: Mapping[str, object]):
        requested_limit = request.get("_contextCompactionLimit") or request.get("_requestedContextLimit")
        verified_limit = int(requested_limit) if requested_limit is not None else None
        return nullcontext(
            PlannerTurnMetadata(
                verified_context_limit=verified_limit,
                planner_label="test",
            )
        )


def bind_test_runtime_planner(
    gateway: AgentGateway,
    respond: Callable[[str], object],
    *,
    compact: Callable[[list[dict[str, object]], dict[str, object]], Mapping[str, object]] | None = None,
) -> RuntimePlannerService:
    planner = RuntimePlannerService(
        catalog=_TestRuntimePlannerCatalog(gateway),
        desktop=_TestRuntimePlannerDesktop(gateway),
        model=_TestRuntimePlannerModel(respond),
        compactor=_TestRuntimePlannerCompactor(compact) if compact is not None else None,
        turn=_TestRuntimePlannerTurn(),
    )
    gateway.bind_runtime_planner(planner)
    return planner


def _test_runtime_provider_config(*, model: str = "test-model") -> ProviderApiConfig:
    return ProviderApiConfig(
        provider="ollama",
        api_key="",
        base_url="http://127.0.0.1:11434",
        model=model,
    )


def isolated_provider_configuration_service(
    config_path: Path,
    *,
    settings: SimpleNamespace | None = None,
) -> ProviderConfigurationService:
    runtime_settings = settings or SimpleNamespace(
        llm_provider="ollama",
        llm_api_key="",
        llm_model="qwen3",
        gemini_thinking_level="",
    )
    return ProviderConfigurationService(
        ProviderConfigurationPersistencePorts(
            config_path=config_path,
            load_runtime_settings=lambda: runtime_settings,
            atomic_write_json=dashboard_server.atomic_write_json,
            path_is_reparse_or_link=dashboard_server._path_is_reparse_or_link,
        ),
        dashboard_server.PROVIDER_CONFIGURATION._policy,
        threading.RLock(),
    )


def isolated_provider_model_catalog_service(
    models: list[dict[str, object]],
) -> ProviderModelCatalogService:
    client = SimpleNamespace(models=SimpleNamespace(list=lambda: list(models)))
    return ProviderModelCatalogService(
        dashboard_server.PROVIDER_MODEL_CATALOG._policy,
        ProviderModelCatalogSdkPorts(
            openai_client=lambda _key, _base_url, _timeout: client,
            google_ai_studio_client=lambda _key: client,
            google_vertex_client=lambda _project, _location: client,
            anthropic_client=lambda _key: client,
        ),
    )
def isolated_project_snapshot_service(*, cache_path: Path | None = None):
    original = dashboard_server._PROJECT_SNAPSHOT_SELECTION
    return dashboard_server.ProjectSnapshotSelectionService(
        original._ports,
        cache_path=cache_path or original.cache_path,
        selection_path=original.selection_path,
        selection_schema=original.selection_schema,
        cache_ttl_seconds=original.cache_ttl_seconds,
    )


def isolated_agent_question_service(
    gateway: AgentGateway,
    *,
    resolve_question=None,
) -> AgentQuestionService:
    resolver = resolve_question or (
        lambda question_id, continuation_prompt: gateway.goal.resolve_agent_goal_question(
            question_id,
            continuation_prompt=continuation_prompt,
        )
    )
    return AgentQuestionService(
        AgentQuestionPersistence(
            AgentQuestionPersistencePorts(
                log_path=lambda: gateway.audit_dir / "agent-questions.jsonl",
                shared_state_lock=gateway._lock,
                redact=redact_sensitive,
            )
        ),
        AgentQuestionScopePorts(
            normalize_path=normalize_filesystem_path,
            summarize=summarize_text,
            redact_goal_persistence=redact_background_goal_persistence,
        ),
        GoalQuestionResolutionPort(
            resolve=resolver
        ),
    )


def make_shader_inventory() -> dict:
    return {
        "type": "material_inventory_snapshot",
        "version": "0.2",
        "materials": [
            {
                "material_id": "mat_skin",
                "avatar_name": "HeroAvatar",
                "avatar_path": "Scene/HeroAvatar",
                "item_path": "Scene/HeroAvatar/Body",
                "renderer_id": "renderer_body",
                "renderer_name": "Body",
                "renderer_path": "Scene/HeroAvatar/Body",
                "mesh_name": "BodyMesh",
                "slot_index": 0,
                "material_name": "Face_Skin",
                "shader_name": "lilToon",
                "shader_family": "lilToon",
                "category": "skin",
                "supported_properties": {
                    "base_color": {"type": "color", "value": "#FFD6C8FF", "writable": True},
                    "smoothness": {"type": "float", "value": 0.2, "writable": True},
                    "outline_width": {"type": "float", "value": 0.01, "writable": True},
                },
            },
            {
                "material_id": "mat_unsupported",
                "avatar_name": "HeroAvatar",
                "avatar_path": "Scene/HeroAvatar",
                "material_name": "Legacy",
                "shader_family": "Unsupported",
                "category": "unknown",
                "supported_properties": {},
            },
        ],
        "summary": {"materialCount": 2},
    }


class FakeDashboardShellProcess:
    _next_pid = 12000

    def __init__(self, stdout: str) -> None:
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.returncode: int | None = None
        self.stdout = stdout
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def communicate(self, *, timeout: float | None = None) -> tuple[str, str]:
        if self.killed:
            self.returncode = -9
            return "", "terminated"
        self.returncode = 0
        return self.stdout, ""

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def fake_dashboard_shell_process_ports(
    *,
    stdout: str = "fixture shell output",
) -> tuple[ShellProcessPorts, list[FakeDashboardShellProcess]]:
    processes: list[FakeDashboardShellProcess] = []

    def spawn(*_args: object, **_kwargs: object) -> FakeDashboardShellProcess:
        process = FakeDashboardShellProcess(stdout)
        processes.append(process)
        return process

    return (
        ShellProcessPorts(
            spawn=spawn,
            terminate_tree=lambda process: process.kill(),
            environment=dict,
            monotonic=time.monotonic,
            utc_now=lambda: "2026-08-08T00:00:00+00:00",
            sleep=lambda _seconds: None,
        ),
        processes,
    )


class DashboardServerTests(unittest.TestCase):
    def setUp(self) -> None:
        dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack.clear()
        dashboard_server.DASHBOARD_RUNTIME.current_avatar_path = ""
        dashboard_server.DASHBOARD_RUNTIME.current_avatar_name = ""
        self.tuning_store_dir = tempfile.TemporaryDirectory()
        self.original_tuning_paths = (
            dashboard_server.TUNING_HISTORY_PATH,
            dashboard_server.TUNING_PRESETS_PATH,
            dashboard_server.TUNING_LOCKS_PATH,
            dashboard_server.SHADER_TUNING_HISTORY_PATH,
            dashboard_server.SHADER_TUNING_PRESETS_PATH,
            dashboard_server.SHADER_TUNING_LOCKS_PATH,
        )
        self.original_agent_paths = (
            dashboard_server.AGENT_GATEWAY.config_path,
            dashboard_server.AGENT_GATEWAY.audit_dir,
        )
        tuning_root = Path(self.tuning_store_dir.name)
        dashboard_server.TUNING_HISTORY_PATH = tuning_root / "tuning_history.json"
        dashboard_server.TUNING_PRESETS_PATH = tuning_root / "tuning_presets.json"
        dashboard_server.TUNING_LOCKS_PATH = tuning_root / "tuning_locks.json"
        dashboard_server.SHADER_TUNING_HISTORY_PATH = tuning_root / "shader_tuning_history.json"
        dashboard_server.SHADER_TUNING_PRESETS_PATH = tuning_root / "shader_tuning_presets.json"
        dashboard_server.SHADER_TUNING_LOCKS_PATH = tuning_root / "shader_tuning_locks.json"
        dashboard_server.AGENT_GATEWAY.configure_paths(
            tuning_root / "agent_gateway.json",
            tuning_root / "agent_gateway",
        )
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.developer_options_enabled = True
        config.developer_options_ever_enabled = True
        config.computer_use_enabled = True
        config.computer_use_ever_enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        dashboard_server.AGENT_GATEWAY.desktop._desktop_bridges.clear()  # noqa: SLF001
        self.status_snapshot_patcher = patch(
            "dashboard_server.build_unity_status_snapshot",
            return_value={
                "connected": False,
                "host": "127.0.0.1",
                "port": 8080,
                "instance": "",
                "projectPath": "",
                "output": "",
                "parsed": None,
                "error": "mocked in tests",
            },
        )
        self.status_snapshot_patcher.start()

    def tearDown(self) -> None:
        self.status_snapshot_patcher.stop()
        (
            dashboard_server.TUNING_HISTORY_PATH,
            dashboard_server.TUNING_PRESETS_PATH,
            dashboard_server.TUNING_LOCKS_PATH,
            dashboard_server.SHADER_TUNING_HISTORY_PATH,
            dashboard_server.SHADER_TUNING_PRESETS_PATH,
            dashboard_server.SHADER_TUNING_LOCKS_PATH,
        ) = self.original_tuning_paths
        dashboard_server.AGENT_GATEWAY.configure_paths(*self.original_agent_paths)
        self.tuning_store_dir.cleanup()

    def test_websocket_sends_bootstrap_payload(self) -> None:
        with TestClient(dashboard_server.app) as client:
            with client.websocket_connect("/ws") as websocket:
                message = websocket.receive_json()
                self.assertEqual(message["type"], "hello")
                self.assertIn("projects", message["payload"])
                self.assertIn("unityStatus", message["payload"])
                self.assertNotIn("api_key", json.dumps(message["payload"]).lower())

    def test_websocket_uses_header_or_cookie_instead_of_query_token(self) -> None:
        original_required = dashboard_server.APP_AUTH_REQUIRED
        original_token = dashboard_server.APP_SESSION_TOKEN
        dashboard_server.APP_AUTH_REQUIRED = True
        dashboard_server.APP_SESSION_TOKEN = "test-app-session-token"
        headers = {"Authorization": "Bearer test-app-session-token"}
        try:
            with TestClient(dashboard_server.app) as client:
                with self.assertRaises(dashboard_server.WebSocketDisconnect):
                    with client.websocket_connect("/ws?ws_ticket=test-app-session-token") as websocket:
                        websocket.receive_json()

                with self.assertRaises(dashboard_server.WebSocketDisconnect):
                    with client.websocket_connect("/ws?app_token=test-app-session-token") as websocket:
                        websocket.receive_json()

                with client.websocket_connect("/ws", headers=headers) as websocket:
                    message = websocket.receive_json()
                    self.assertEqual(message["type"], "hello")

                with client.websocket_connect(
                    "/ws",
                    headers={
                        "Authorization": "Bearer test-app-session-token",
                        "Origin": "http://127.0.0.1:8757",
                    },
                ) as websocket:
                    message = websocket.receive_json()
                    self.assertEqual(message["type"], "hello")

                dashboard_response = client.get("/")
                self.assertEqual(dashboard_response.status_code, 200)
                self.assertIn("httponly", dashboard_response.headers.get("set-cookie", "").lower())
                with client.websocket_connect("/ws") as websocket:
                    message = websocket.receive_json()
                    self.assertEqual(message["type"], "hello")

                with self.assertRaises(dashboard_server.WebSocketDisconnect):
                    with client.websocket_connect(
                        "/ws",
                        headers={"Authorization": "Bearer test-app-session-token", "Origin": "https://example.invalid"},
                    ) as websocket:
                        websocket.receive_json()
        finally:
            dashboard_server.APP_AUTH_REQUIRED = original_required
            dashboard_server.APP_SESSION_TOKEN = original_token

    def test_legacy_dashboard_websocket_avoids_query_tokens(self) -> None:
        app_js = (Path(__file__).resolve().parents[1] / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("/api/app/ws-ticket", app_js)
        self.assertNotIn("ws_ticket", app_js)
        self.assertIn("new WebSocket(`${scheme}://${window.location.host}/ws`)", app_js)

    def test_chat_transcripts_split_temporary_and_project_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            project.mkdir()
            chats = [
                {"id": "temp-chat", "projectPath": "", "items": [{"id": "u1", "type": "user", "text": "temporary"}]},
                {"id": "project-chat", "projectPath": str(project), "items": [{"id": "u2", "type": "user", "text": "project"}]},
            ]

            with TestClient(dashboard_server.app) as client:
                response = client.post("/api/app/chats", json={"chats": chats})
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["appCount"], 1)
                self.assertEqual(len(payload["projectPaths"]), 1)

                app_path = Path(payload["path"])
                project_path = project / ".vrcforge" / "chat-transcripts.json"
                index_path = app_path.parent / "chat-projects.json"
                self.assertTrue(app_path.is_file())
                self.assertTrue(project_path.is_file())
                self.assertTrue(index_path.is_file())
                self.assertEqual(json.loads(app_path.read_text(encoding="utf-8"))["chats"][0]["id"], "temp-chat")
                self.assertEqual(json.loads(project_path.read_text(encoding="utf-8"))["chats"][0]["id"], "project-chat")
                self.assertIn(str(project.resolve()), json.loads(index_path.read_text(encoding="utf-8"))["projectPaths"])

                read_response = client.get("/api/app/chats", params=[("projectPath", str(project))])
                self.assertEqual(read_response.status_code, 200)
                self.assertEqual({chat["id"] for chat in read_response.json()["chats"]}, {"temp-chat", "project-chat"})

                response = client.post(
                    "/api/app/chats",
                    json={"chats": [chats[0]], "sourceRevisions": read_response.json()["sources"]},
                )
                self.assertEqual(response.status_code, 200)
                self.assertFalse(project_path.exists())
                read_response = client.get("/api/app/chats", params=[("projectPath", str(project))])
                self.assertEqual(read_response.status_code, 200)
                self.assertEqual([chat["id"] for chat in read_response.json()["chats"]], ["temp-chat"])

    def test_chat_restore_isolates_corrupt_project_source_and_blocks_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            project.mkdir()
            chats = [
                {"id": "app-chat", "projectPath": "", "items": [{"id": "u1", "type": "user", "text": "healthy"}]},
                {"id": "project-chat", "projectPath": str(project), "items": [{"id": "u2", "type": "user", "text": "project"}]},
            ]
            with TestClient(dashboard_server.app) as client:
                created = client.post("/api/app/chats", json={"chats": chats})
                self.assertEqual(created.status_code, 200)
                project_path = project / ".vrcforge" / "chat-transcripts.json"
                damaged = b'{"version":1,"chats":['
                project_path.write_bytes(damaged)
                before_mtime = project_path.stat().st_mtime_ns

                restored = client.get("/api/app/chats", params=[("projectPath", str(project))])

                self.assertEqual(restored.status_code, 200)
                payload = restored.json()
                self.assertEqual([chat["id"] for chat in payload["chats"]], ["app-chat"])
                self.assertTrue(payload["writeBlocked"])
                self.assertEqual(payload["recoveries"][0]["scope"], "project")
                self.assertEqual(payload["recoveries"][0]["reason"], "invalid_json")
                self.assertNotIn(str(project), json.dumps(payload["recoveries"]))
                self.assertEqual(project_path.read_bytes(), damaged)
                self.assertEqual(project_path.stat().st_mtime_ns, before_mtime)

                blocked = client.post(
                    "/api/app/chats",
                    json={"chats": [chats[0]], "sourceRevisions": payload["sources"]},
                )
                self.assertEqual(blocked.status_code, 409)
                self.assertEqual(blocked.json()["detail"]["code"], "chat_store_recovery_required")
                self.assertEqual(project_path.read_bytes(), damaged)

    def test_chat_save_rejects_stale_source_revision(self) -> None:
        chat = {"id": "app-chat", "projectPath": "", "items": [{"id": "u1", "type": "user", "text": "one"}]}
        with TestClient(dashboard_server.app) as client:
            created = client.post("/api/app/chats", json={"chats": [chat]})
            self.assertEqual(created.status_code, 200)
            restored = client.get("/api/app/chats").json()
            app_path = Path(restored["path"])
            replacement = json.dumps(
                {"version": 1, "chats": [{**chat, "items": [{"id": "u2", "type": "user", "text": "newer"}]}]},
                ensure_ascii=False,
            )
            app_path.write_text(replacement, encoding="utf-8")

            response = client.post(
                "/api/app/chats",
                json={"chats": [chat], "sourceRevisions": restored["sources"]},
            )

            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["code"], "chat_store_snapshot_changed")
            self.assertEqual(app_path.read_text(encoding="utf-8"), replacement)

    def test_chat_save_rejects_missing_revision_for_existing_source(self) -> None:
        chat = {"id": "app-chat", "projectPath": "", "items": [{"id": "u1", "type": "user", "text": "one"}]}
        with TestClient(dashboard_server.app) as client:
            created = client.post("/api/app/chats", json={"chats": [chat]})
            self.assertEqual(created.status_code, 200)
            app_path = Path(created.json()["path"])
            original = app_path.read_bytes()

            response = client.post("/api/app/chats", json={"chats": [{**chat, "title": "unversioned"}]})

            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["code"], "chat_store_snapshot_changed")
            self.assertEqual(app_path.read_bytes(), original)

    def test_chat_get_includes_every_healthy_project_from_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            project.mkdir()
            chat = {
                "id": "indexed-project-chat",
                "projectPath": str(project),
                "items": [{"id": "u1", "type": "user", "text": "keep"}],
            }
            with TestClient(dashboard_server.app) as client:
                created = client.post("/api/app/chats", json={"chats": [chat]})
                self.assertEqual(created.status_code, 200)

                restored = client.get("/api/app/chats")

            self.assertEqual(restored.status_code, 200)
            self.assertEqual([item["id"] for item in restored.json()["chats"]], ["indexed-project-chat"])
            project_sources = [source for source in restored.json()["sources"] if source.get("scope") == "project"]
            self.assertEqual(len(project_sources), 1)
            self.assertTrue(project_sources[0]["exists"])

    def test_chat_save_deletes_last_chat_from_healthy_unindexed_project_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            store = project / ".vrcforge" / "chat-transcripts.json"
            store.parent.mkdir(parents=True)
            chat = {
                "id": "unindexed-project-chat",
                "projectPath": str(project),
                "items": [{"id": "u1", "type": "user", "text": "delete me"}],
            }
            store.write_text(
                json.dumps({"version": 1, "scope": "project", "chats": [chat]}),
                encoding="utf-8",
            )

            with TestClient(dashboard_server.app) as client:
                loaded = client.get("/api/app/chats", params=[("projectPath", str(project))])
                self.assertEqual([item["id"] for item in loaded.json()["chats"]], [chat["id"]])
                saved = client.post(
                    "/api/app/chats",
                    json={"chats": [], "sourceRevisions": loaded.json()["sources"]},
                )
                reloaded = client.get("/api/app/chats", params=[("projectPath", str(project))])

            self.assertEqual(saved.status_code, 200)
            self.assertFalse(store.exists())
            self.assertEqual(reloaded.status_code, 200)
            self.assertEqual(reloaded.json()["chats"], [])

    def test_chat_save_moves_last_chat_out_of_healthy_unindexed_project_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            store = project / ".vrcforge" / "chat-transcripts.json"
            store.parent.mkdir(parents=True)
            chat = {
                "id": "move-project-chat",
                "projectPath": str(project),
                "items": [{"id": "u1", "type": "user", "text": "move me"}],
            }
            store.write_text(
                json.dumps({"version": 1, "scope": "project", "chats": [chat]}),
                encoding="utf-8",
            )

            with TestClient(dashboard_server.app) as client:
                loaded = client.get("/api/app/chats", params=[("projectPath", str(project))])
                moved = {**loaded.json()["chats"][0], "projectPath": ""}
                saved = client.post(
                    "/api/app/chats",
                    json={"chats": [moved], "sourceRevisions": loaded.json()["sources"]},
                )
                reloaded = client.get("/api/app/chats", params=[("projectPath", str(project))])

            self.assertEqual(saved.status_code, 200)
            self.assertFalse(store.exists())
            self.assertEqual([item["id"] for item in reloaded.json()["chats"]], [chat["id"]])
            self.assertEqual(reloaded.json()["chats"][0]["projectPath"], "")

    def test_chat_save_rolls_back_all_sources_when_index_commit_fails_after_project_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            project.mkdir()
            chat = {
                "id": "transaction-chat",
                "projectPath": str(project),
                "items": [{"id": "u1", "type": "user", "text": "move atomically"}],
            }
            with TestClient(dashboard_server.app) as client:
                created = client.post("/api/app/chats", json={"chats": [chat]})
                self.assertEqual(created.status_code, 200)
                loaded = client.get("/api/app/chats", params=[("projectPath", str(project))]).json()
                app_path = Path(loaded["path"])
                project_path = project / ".vrcforge" / "chat-transcripts.json"
                index_path = app_path.parent / "chat-projects.json"
                before = {path: path.read_bytes() for path in (app_path, project_path, index_path)}
                moved = {**loaded["chats"][0], "projectPath": ""}
                real_atomic_write = dashboard_server.atomic_write_text

                def fail_index_write(path: Path, content: str) -> None:
                    if path == index_path:
                        raise OSError("injected index failure")
                    real_atomic_write(path, content)

                with patch("dashboard_server.atomic_write_text", side_effect=fail_index_write):
                    failed = client.post(
                        "/api/app/chats",
                        json={"chats": [moved], "sourceRevisions": loaded["sources"]},
                    )

            self.assertEqual(failed.status_code, 500)
            self.assertIn("restored", failed.json()["detail"])
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertFalse(any(path.name.endswith(".tmp") for path in app_path.parent.iterdir()))
            self.assertFalse(any(path.name.endswith(".tmp") for path in project_path.parent.iterdir()))

    def test_chat_project_root_rejects_parent_symlink_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            actual_parent = root / "actual"
            project = actual_parent / "AvatarProject"
            project.mkdir(parents=True)
            linked_parent = root / "linked"
            try:
                linked_parent.symlink_to(actual_parent, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlink creation is unavailable: {exc}")

            self.assertIsNone(dashboard_server.resolve_chat_project_root(str(linked_parent / project.name)))
            self.assertIsNone(dashboard_server.project_chat_transcripts_path(str(linked_parent / project.name)))
            self.assertIsNotNone(dashboard_server.resolve_chat_project_root(str(project)))

    def test_project_chat_source_overrides_embedded_project_path_and_never_touches_other_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_a = Path(temp_dir) / "ProjectA"
            project_b = Path(temp_dir) / "ProjectB"
            project_a.mkdir()
            project_b.mkdir()
            store_a = project_a / ".vrcforge" / "chat-transcripts.json"
            store_b = project_b / ".vrcforge" / "chat-transcripts.json"
            store_a.parent.mkdir()
            store_b.parent.mkdir()
            embedded = {
                "id": "bound-to-source",
                "projectPath": str(project_b),
                "items": [{"id": "u1", "type": "user", "text": "owned by A"}],
            }
            store_a.write_text(json.dumps({"version": 1, "scope": "project", "chats": [embedded]}), encoding="utf-8")
            store_b.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "scope": "project",
                        "chats": [
                            {
                                "id": "project-b-sentinel",
                                "projectPath": str(project_b),
                                "items": [{"id": "b1", "type": "user", "text": "do not touch"}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            before_b = store_b.read_bytes()

            with TestClient(dashboard_server.app) as client:
                loaded = client.get("/api/app/chats", params=[("projectPath", str(project_a))])
                self.assertEqual(loaded.status_code, 200)
                self.assertEqual(loaded.json()["chats"][0]["projectPath"], str(project_a.resolve()))
                saved = client.post(
                    "/api/app/chats",
                    json={"chats": loaded.json()["chats"], "sourceRevisions": loaded.json()["sources"]},
                )

            self.assertEqual(saved.status_code, 200)
            self.assertEqual(json.loads(store_a.read_text(encoding="utf-8"))["chats"][0]["projectPath"], str(project_a.resolve()))
            self.assertEqual(store_b.read_bytes(), before_b)

    def test_divergent_cross_source_chat_id_blocks_get_and_direct_post_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            project.mkdir()
            app_path = dashboard_server.chat_transcripts_path()
            index_path = dashboard_server.chat_project_index_path()
            project_path = project / ".vrcforge" / "chat-transcripts.json"
            app_path.parent.mkdir(parents=True, exist_ok=True)
            project_path.parent.mkdir()
            app_chat = {"id": "duplicate-id", "items": [{"id": "a1", "type": "user", "text": "app"}]}
            project_chat = {
                "id": "duplicate-id",
                "projectPath": str(project),
                "items": [{"id": "p1", "type": "user", "text": "project"}],
            }
            app_path.write_text(json.dumps({"version": 1, "chats": [app_chat]}), encoding="utf-8")
            project_path.write_text(
                json.dumps({"version": 1, "scope": "project", "chats": [project_chat]}),
                encoding="utf-8",
            )
            index_path.write_text(json.dumps({"version": 1, "projectPaths": [str(project)]}), encoding="utf-8")
            before = {path: path.read_bytes() for path in (app_path, project_path, index_path)}

            with TestClient(dashboard_server.app) as client:
                loaded = client.get("/api/app/chats")
                self.assertEqual(loaded.status_code, 200)
                self.assertTrue(loaded.json()["writeBlocked"])
                self.assertTrue(
                    any(item.get("reason") == "duplicate_chat_id_conflict" for item in loaded.json()["recoveries"])
                )
                blocked = client.post(
                    "/api/app/chats",
                    json={"chats": loaded.json()["chats"], "sourceRevisions": loaded.json()["sources"]},
                )

            self.assertEqual(blocked.status_code, 409)
            self.assertEqual(blocked.json()["detail"]["code"], "chat_store_duplicate_id_conflict")
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_unavailable_indexed_project_blocks_writes_but_ad_hoc_query_does_not(self) -> None:
        missing_project = str(Path(self.tuning_store_dir.name) / "missing-project")
        index_path = dashboard_server.chat_project_index_path()
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps({"version": 1, "projectPaths": [missing_project]}), encoding="utf-8")

        with TestClient(dashboard_server.app) as client:
            indexed = client.get("/api/app/chats")
            self.assertTrue(indexed.json()["writeBlocked"])
            self.assertTrue(any(item.get("reason") == "project_unavailable" for item in indexed.json()["recoveries"]))

            index_path.write_text(json.dumps({"version": 1, "projectPaths": []}), encoding="utf-8")
            ad_hoc = client.get("/api/app/chats", params=[("projectPath", missing_project)])

        self.assertEqual(ad_hoc.status_code, 200)
        self.assertFalse(ad_hoc.json()["writeBlocked"])
        unavailable = [item for item in ad_hoc.json()["sources"] if item.get("reason") == "project_unavailable"]
        self.assertEqual(len(unavailable), 1)
        self.assertFalse(unavailable[0]["indexed"])
        self.assertEqual(ad_hoc.json()["recoveries"], [])

    def test_chat_get_quarantines_exponent_overflow_instead_of_returning_500(self) -> None:
        app_path = dashboard_server.chat_transcripts_path()
        app_path.parent.mkdir(parents=True, exist_ok=True)
        app_path.write_text(
            '{"version":1,"chats":[{"id":"overflow","items":[],"future":1e999}]}',
            encoding="utf-8",
        )

        with TestClient(dashboard_server.app) as client:
            response = client.get("/api/app/chats")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["chats"], [])
        self.assertFalse(app_path.exists())
        self.assertTrue(any(item["reason"] == "invalid_json" for item in response.json()["recoveries"]))

    def test_chat_get_blocks_oversized_record_count_without_loading_unsavable_state(self) -> None:
        app_path = dashboard_server.chat_transcripts_path()
        app_path.parent.mkdir(parents=True, exist_ok=True)
        chats = [
            {
                "id": f"chat-{index}",
                "items": [{"id": f"u-{index}", "type": "user", "text": "keep"}],
            }
            for index in range(dashboard_server.CHAT_TRANSCRIPTS_MAX_CHATS + 1)
        ]
        app_path.write_text(json.dumps({"version": 1, "chats": chats}), encoding="utf-8")

        with TestClient(dashboard_server.app) as client:
            response = client.get("/api/app/chats")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["chats"], [])
        self.assertTrue(response.json()["writeBlocked"])
        self.assertEqual(response.json()["recoveries"][0]["reason"], "record_limit_exceeded")
        self.assertTrue(app_path.exists())

    def test_chat_transcripts_filter_unstarted_empty_chats_on_write_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            project.mkdir()
            chats = [
                {"id": "empty-temp", "projectPath": "", "title": "", "sessionId": "", "pinned": True, "items": []},
                {"id": "empty-project", "projectPath": str(project), "title": "", "sessionId": "", "items": []},
                {"id": "temp-chat", "projectPath": "", "items": [{"id": "u1", "type": "user", "text": "temporary"}]},
                {"id": "project-chat", "projectPath": str(project), "items": [{"id": "u2", "type": "user", "text": "project"}]},
            ]

            with TestClient(dashboard_server.app) as client:
                response = client.post("/api/app/chats", json={"chats": chats})
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["count"], 2)
                self.assertEqual(payload["appCount"], 1)
                self.assertEqual(len(payload["projectPaths"]), 1)

                app_path = Path(payload["path"])
                project_path = project / ".vrcforge" / "chat-transcripts.json"
                self.assertEqual([chat["id"] for chat in json.loads(app_path.read_text(encoding="utf-8"))["chats"]], ["temp-chat"])
                self.assertEqual([chat["id"] for chat in json.loads(project_path.read_text(encoding="utf-8"))["chats"]], ["project-chat"])

                app_path.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "chats": [
                                {"id": "old-empty-temp", "projectPath": "", "title": "", "sessionId": "", "items": []},
                                {"id": "temp-chat", "projectPath": "", "items": [{"id": "u1", "type": "user", "text": "temporary"}]},
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                project_path.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "scope": "project",
                            "chats": [
                                {"id": "old-empty-project", "projectPath": str(project), "title": "", "sessionId": "", "items": []},
                                {"id": "project-chat", "projectPath": str(project), "items": [{"id": "u2", "type": "user", "text": "project"}]},
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

                read_response = client.get("/api/app/chats", params=[("projectPath", str(project))])
                self.assertEqual(read_response.status_code, 200)
                self.assertEqual({chat["id"] for chat in read_response.json()["chats"]}, {"temp-chat", "project-chat"})
                self.assertEqual(read_response.json()["count"], 2)

    def test_chat_transcripts_never_persist_or_restore_streaming_placeholders(self) -> None:
        payload_hash = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        chats = [
            {
                "id": "chat-with-orphan",
                "sessionId": "session-orphan",
                "attachmentPayloads": {
                    payload_hash: {
                        "payloadHash": payload_hash,
                        "payloadKind": "text",
                        "text": "hello",
                    }
                },
                "compactedAttachmentRefs": [
                    {
                        "id": "a1",
                        "name": "hello.txt",
                        "size": 5,
                        "type": "text/plain",
                        "payloadHash": payload_hash,
                        "payloadKind": "text",
                    }
                ],
                "items": [
                    {
                        "id": "user-1",
                        "type": "user",
                        "text": "hello",
                        "attachments": [
                            {
                                "id": "a1",
                                "name": "hello.txt",
                                "size": 5,
                                "type": "text/plain",
                                "payloadHash": payload_hash,
                                "payloadKind": "text",
                            }
                        ],
                    },
                    {"id": "stream-old", "type": "streaming", "clientTurnId": "old-turn", "text": ""},
                    {
                        "id": "agent-1",
                        "type": "agent",
                        "response": {
                            "plan": {
                                "summary": "done",
                                "planner": "test",
                                "shellNeeded": False,
                            }
                        },
                    },
                ],
            }
        ]
        with TestClient(dashboard_server.app) as client:
            written = client.post("/api/app/chats", json={"chats": chats})
            restored = client.get("/api/app/chats")

        self.assertEqual(written.status_code, 200)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual([item["type"] for item in restored.json()["chats"][0]["items"]], ["user", "agent"])
        self.assertEqual(restored.json()["chats"][0]["attachmentPayloads"][payload_hash]["text"], "hello")
        compacted_ref = restored.json()["chats"][0]["compactedAttachmentRefs"][0]
        self.assertEqual(compacted_ref["payloadHash"], payload_hash)
        self.assertNotIn("text", compacted_ref)
        self.assertNotIn("dataUrl", compacted_ref)

    def test_extract_streaming_dialogue_text_reads_summary_fallback(self) -> None:
        field, text = dashboard_server.extract_streaming_dialogue_text('{"action":"reply","summary":"hel')

        self.assertEqual(field, "summary")
        self.assertEqual(text, "hel")

    def test_extract_streaming_dialogue_text_prefers_reply(self) -> None:
        field, text = dashboard_server.extract_streaming_dialogue_text(
            '{"summary":"draft","reply":"final line"}'
        )

        self.assertEqual(field, "reply")
        self.assertEqual(text, "final line")

    def test_extract_streaming_dialogue_text_skips_non_string_reply(self) -> None:
        field, text = dashboard_server.extract_streaming_dialogue_text(
            '{"reply":null,"summary":"visible"}'
        )

        self.assertEqual(field, "summary")
        self.assertEqual(text, "visible")

    def test_project_prefs_accepts_only_unity_project_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            valid = root / "ValidAvatarProject"
            (valid / "Assets").mkdir(parents=True)
            (valid / "Packages").mkdir()
            (valid / "ProjectSettings").mkdir()
            (valid / "ProjectSettings" / "ProjectVersion.txt").write_text(
                "m_EditorVersion: 2022.3.22f1",
                encoding="utf-8",
            )
            plain_dir = root / "Start Menu Shortcut Folder"
            plain_dir.mkdir()

            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/projects/prefs",
                    json={"customPaths": [str(valid), str(plain_dir)], "hiddenPaths": []},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["customPaths"], [str(valid).replace("\\", "/")])

    def test_project_prefs_rejects_parent_directory_without_project_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            project = parent / "ChildUnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
                "m_EditorVersion: 2022.3.22f1",
                encoding="utf-8",
            )

            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/projects/prefs",
                    json={"customPaths": [str(parent)], "hiddenPaths": []},
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["customPaths"], [])

    def test_app_bootstrap_does_not_wait_for_full_health_diagnostics(self) -> None:
        async def idle_status_monitor() -> None:
            await asyncio.sleep(60)

        with (
            patch("dashboard_server.build_full_health_payload", side_effect=AssertionError("bootstrap waited for full health")),
            patch("dashboard_server.build_unity_status_snapshot", side_effect=AssertionError("bootstrap waited for Unity diagnostics")),
            patch("dashboard_server.status_monitor_loop", side_effect=idle_status_monitor),
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.get("/api/app/bootstrap")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["health"]["deferredDiagnostics"])
        self.assertEqual(payload["health"]["components"]["backend"]["status"], "ok")
        self.assertIn(payload["health"]["components"]["unityMcpBridgeReachable"]["status"], {"unknown", "warning"})

    def test_app_unity_readiness_refresh_updates_cached_status_without_project_scan(self) -> None:
        async def idle_status_monitor() -> None:
            await asyncio.sleep(60)

        previous_status = dashboard_server.CURRENT_UNITY_STATUS
        previous_fingerprint = dashboard_server.LAST_STATUS_FINGERPRINT
        previous_connected = dashboard_server.LAST_STATUS_CONNECTED
        snapshot = {
            "connected": True,
            "mcpServerReachable": True,
            "unityInstanceRegistered": True,
            "selectedInstanceMatched": True,
            "activeInstanceCount": 1,
            "vrcForgeToolsRegistered": True,
            "missingRequiredVrcForgeTools": [],
            "tools": {"totalTools": 80, "vrcForgeToolsCount": 42},
            "error": "",
        }
        try:
            dashboard_server.CURRENT_UNITY_STATUS = {"connected": False}
            with (
                patch("dashboard_server.build_unity_status_snapshot", return_value=snapshot) as mock_status,
                patch("dashboard_server.status_monitor_loop", side_effect=idle_status_monitor),
                patch("dashboard_server.bootstrap_project_snapshot_payload", side_effect=AssertionError("Unity refresh triggered project scan")),
            ):
                with TestClient(dashboard_server.app) as client:
                    response = client.post("/api/app/unity/readiness/refresh")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["schema"], "vrcforge.unity_readiness_refresh.v1")
            self.assertEqual(payload["unityStatus"], snapshot)
            self.assertEqual(dashboard_server.CURRENT_UNITY_STATUS, snapshot)
            self.assertEqual(payload["health"]["components"]["unityMcpBridgeReachable"]["status"], "ok")
            self.assertEqual(payload["health"]["components"]["vrcForgeUnityTools"]["status"], "ok")
            mock_status.assert_called_once_with()
        finally:
            dashboard_server.CURRENT_UNITY_STATUS = previous_status
            dashboard_server.LAST_STATUS_FINGERPRINT = previous_fingerprint
            dashboard_server.LAST_STATUS_CONNECTED = previous_connected

    def test_app_bootstrap_degrades_when_agent_surfaces_fail(self) -> None:
        with (
            patch.object(dashboard_server.AGENT_GATEWAY, "build_manifest", side_effect=RuntimeError("manifest broken")),
            patch.object(dashboard_server.AGENT_GATEWAY, "build_health", side_effect=RuntimeError("health broken")),
            patch.object(dashboard_server.AGENT_GATEWAY, "permission_state", side_effect=RuntimeError("permission broken")),
            patch.object(dashboard_server.AGENT_GATEWAY, "list_approvals", side_effect=RuntimeError("approvals broken")),
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.get("/api/app/bootstrap")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["agentManifest"]["ok"])
        self.assertIn("manifest broken", payload["agentManifest"]["error"])
        self.assertFalse(payload["agentHealth"]["ok"])
        self.assertEqual(payload["permission"]["executionMode"], "approval")
        self.assertEqual(payload["approvals"], [])

    def test_mcp_startup_failure_does_not_block_app_bootstrap(self) -> None:
        with patch("dashboard_server.create_agent_mcp_app", side_effect=RuntimeError("mcp broken")):
            with TestClient(dashboard_server.app) as client:
                response = client.get("/api/app/bootstrap")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_startup_schedules_mcp_init_without_waiting_for_it(self) -> None:
        async def slow_mcp_init() -> None:
            await asyncio.sleep(60)

        async def slow_status_loop() -> None:
            await asyncio.sleep(60)

        async def exercise() -> None:
            original_mcp_task = dashboard_server.AGENT_MCP_INIT_TASK
            original_status_task = dashboard_server.STATUS_MONITOR_TASK
            dashboard_server.AGENT_MCP_INIT_TASK = None
            dashboard_server.STATUS_MONITOR_TASK = None
            reconcile_sub_agents = Mock(return_value=True)
            try:
                with (
                    patch("dashboard_server.initialize_agent_mcp_mount", side_effect=slow_mcp_init),
                    patch("dashboard_server.status_monitor_loop", side_effect=slow_status_loop),
                    patch("dashboard_server.BACKEND_OWNER_LEASE", SimpleNamespace(owned=True)),
                    patch.object(
                        dashboard_server,
                        "_SUB_AGENT_COLLABORATION",
                        SimpleNamespace(reconcile_startup=reconcile_sub_agents),
                    ),
                    patch.object(
                        dashboard_server.AGENT_GOALS,
                        "reconcile_stale_agent_goal_deliveries",
                        return_value={"ok": True, "deliveries": [], "count": 0},
                    ) as reconcile_goal_deliveries,
                ):
                    await dashboard_server.on_startup()
                    reconcile_sub_agents.assert_called_once_with(refresh_from_disk=True)
                    reconcile_goal_deliveries.assert_called_once_with()
                    self.assertIsNotNone(dashboard_server.AGENT_MCP_INIT_TASK)
                    self.assertFalse(dashboard_server.AGENT_MCP_INIT_TASK.done())
            finally:
                for task in (dashboard_server.AGENT_MCP_INIT_TASK, dashboard_server.STATUS_MONITOR_TASK):
                    if task is not None and not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                dashboard_server.AGENT_MCP_INIT_TASK = original_mcp_task
                dashboard_server.STATUS_MONITOR_TASK = original_status_task

        asyncio.run(exercise())

    def test_startup_without_backend_owner_does_not_reconcile_durable_work(self) -> None:
        async def slow_mcp_init() -> None:
            await asyncio.sleep(60)

        async def slow_status_loop() -> None:
            await asyncio.sleep(60)

        async def exercise() -> None:
            original_mcp_task = dashboard_server.AGENT_MCP_INIT_TASK
            original_status_task = dashboard_server.STATUS_MONITOR_TASK
            dashboard_server.AGENT_MCP_INIT_TASK = None
            dashboard_server.STATUS_MONITOR_TASK = None
            reconcile_sub_agents = Mock(side_effect=AssertionError("a non-owner must not reconcile sub-agent tasks"))
            try:
                with (
                    patch("dashboard_server.initialize_agent_mcp_mount", side_effect=slow_mcp_init),
                    patch("dashboard_server.status_monitor_loop", side_effect=slow_status_loop),
                    patch("dashboard_server.BACKEND_OWNER_LEASE", SimpleNamespace(owned=False)),
                    patch.object(
                        dashboard_server.AGENT_GATEWAY.desktop,
                        "embedded_worker_enabled",
                        return_value=False,
                    ),
                    patch("dashboard_server.load_project_snapshot_cache"),
                    patch.object(
                        dashboard_server,
                        "_SUB_AGENT_COLLABORATION",
                        SimpleNamespace(reconcile_startup=reconcile_sub_agents),
                    ),
                    patch.object(
                        dashboard_server.AGENT_GOALS,
                        "reconcile_stale_agent_goal_deliveries",
                        side_effect=AssertionError("a non-owner must not reconcile goal deliveries"),
                    ) as reconcile_goal_deliveries,
                ):
                    await dashboard_server.on_startup()
                    reconcile_sub_agents.assert_not_called()
                    reconcile_goal_deliveries.assert_not_called()
                    self.assertIsNotNone(dashboard_server.AGENT_MCP_INIT_TASK)
                    self.assertIsNotNone(dashboard_server.STATUS_MONITOR_TASK)
            finally:
                for task in (dashboard_server.AGENT_MCP_INIT_TASK, dashboard_server.STATUS_MONITOR_TASK):
                    if task is not None and not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                dashboard_server.AGENT_MCP_INIT_TASK = original_mcp_task
                dashboard_server.STATUS_MONITOR_TASK = original_status_task

        asyncio.run(exercise())

    def test_startup_reconcile_failures_do_not_block_background_tasks(self) -> None:
        async def slow_mcp_init() -> None:
            await asyncio.sleep(60)

        async def slow_status_loop() -> None:
            await asyncio.sleep(60)

        async def exercise(sub_agent_error: Exception | None, goal_error: Exception | None) -> None:
            original_mcp_task = dashboard_server.AGENT_MCP_INIT_TASK
            original_status_task = dashboard_server.STATUS_MONITOR_TASK
            dashboard_server.AGENT_MCP_INIT_TASK = None
            dashboard_server.STATUS_MONITOR_TASK = None
            reconcile_sub_agents = Mock(side_effect=sub_agent_error)
            try:
                with (
                    patch("dashboard_server.initialize_agent_mcp_mount", side_effect=slow_mcp_init),
                    patch("dashboard_server.status_monitor_loop", side_effect=slow_status_loop),
                    patch("dashboard_server.BACKEND_OWNER_LEASE", SimpleNamespace(owned=True)),
                    patch.object(
                        dashboard_server.AGENT_GATEWAY.desktop,
                        "embedded_worker_enabled",
                        return_value=False,
                    ),
                    patch("dashboard_server.load_project_snapshot_cache"),
                    patch.object(
                        dashboard_server,
                        "_SUB_AGENT_COLLABORATION",
                        SimpleNamespace(reconcile_startup=reconcile_sub_agents),
                    ),
                    patch.object(
                        dashboard_server.AGENT_GOALS,
                        "reconcile_stale_agent_goal_deliveries",
                        side_effect=goal_error,
                    ) as reconcile_goal_deliveries,
                    patch("dashboard_server.emit_log") as emit_log,
                ):
                    await dashboard_server.on_startup()
                    reconcile_sub_agents.assert_called_once_with(refresh_from_disk=True)
                    reconcile_goal_deliveries.assert_called_once_with()
                    self.assertIsNotNone(dashboard_server.AGENT_MCP_INIT_TASK)
                    self.assertFalse(dashboard_server.AGENT_MCP_INIT_TASK.done())
                    self.assertIsNotNone(dashboard_server.STATUS_MONITOR_TASK)
                    self.assertFalse(dashboard_server.STATUS_MONITOR_TASK.done())
                    warning_messages = [
                        call.args[2]
                        for call in emit_log.call_args_list
                        if len(call.args) >= 3 and call.args[0] == "warn"
                    ]
                    self.assertEqual(
                        "Sub-agent startup reconciliation had a warning." in warning_messages,
                        sub_agent_error is not None,
                    )
                    self.assertEqual(
                        "Goal delivery startup reconciliation had a warning." in warning_messages,
                        goal_error is not None,
                    )
                    if sub_agent_error is not None:
                        emit_log.assert_any_call(
                            "warn",
                            "subagent",
                            "Sub-agent startup reconciliation had a warning.",
                            {"error": str(sub_agent_error)},
                        )
                    if goal_error is not None:
                        emit_log.assert_any_call(
                            "warn",
                            "agent",
                            "Goal delivery startup reconciliation had a warning.",
                            {"error": str(goal_error)},
                        )
            finally:
                for task in (dashboard_server.AGENT_MCP_INIT_TASK, dashboard_server.STATUS_MONITOR_TASK):
                    if task is not None and not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                dashboard_server.AGENT_MCP_INIT_TASK = original_mcp_task
                dashboard_server.STATUS_MONITOR_TASK = original_status_task

        for sub_agent_error, goal_error in (
            (RuntimeError("sub-agent disk unavailable"), None),
            (None, OSError("goal append failed")),
            (RuntimeError("sub-agent append failed"), OSError("goal disk unavailable")),
        ):
            asyncio.run(exercise(sub_agent_error, goal_error))

    def test_shutdown_does_not_release_the_process_owner_lease(self) -> None:
        async def exercise() -> None:
            originals = (
                dashboard_server.AGENT_MCP_INIT_TASK,
                dashboard_server.STATUS_MONITOR_TASK,
                dashboard_server.AGENT_MCP_APP,
                dashboard_server.AGENT_MCP_CONTEXT,
                dashboard_server.AGENT_MCP_MOUNT.app,
            )
            dashboard_server.AGENT_MCP_INIT_TASK = None
            dashboard_server.STATUS_MONITOR_TASK = None
            dashboard_server.AGENT_MCP_APP = None
            dashboard_server.AGENT_MCP_CONTEXT = None
            dashboard_server.AGENT_MCP_MOUNT.app = None
            lease = Mock()
            drain_shutdown = AsyncMock(
                return_value=SimpleNamespace(
                    snapshot_count=0,
                    drained_count=0,
                    pending_count=0,
                )
            )
            try:
                with (
                    patch("dashboard_server.BACKEND_OWNER_LEASE", lease),
                    patch.object(
                        dashboard_server.BACKGROUND_GOAL_COORDINATOR,
                        "shutdown",
                        drain_shutdown,
                    ),
                    patch.object(
                        dashboard_server.AGENT_GATEWAY.desktop,
                        "stop_embedded_worker",
                    ),
                ):
                    await dashboard_server.on_shutdown()
                lease.release.assert_not_called()
                drain_shutdown.assert_awaited_once_with()
            finally:
                (
                    dashboard_server.AGENT_MCP_INIT_TASK,
                    dashboard_server.STATUS_MONITOR_TASK,
                    dashboard_server.AGENT_MCP_APP,
                    dashboard_server.AGENT_MCP_CONTEXT,
                    dashboard_server.AGENT_MCP_MOUNT.app,
                ) = originals

        asyncio.run(exercise())

    def test_app_bootstrap_uses_cached_project_snapshot_without_waiting(self) -> None:
        original_service = dashboard_server._PROJECT_SNAPSHOT_SELECTION
        service = isolated_project_snapshot_service()
        service._cache_loaded = True
        dashboard_server._PROJECT_SNAPSHOT_SELECTION = service
        try:
            with (
                patch.object(type(service), "schedule_project_snapshot_refresh", return_value=True) as schedule_refresh,
                patch("dashboard_server.build_project_snapshot_payload", side_effect=AssertionError("bootstrap waited for project discovery")),
                TestClient(dashboard_server.app) as client,
            ):
                normal_response = client.get("/api/app/bootstrap")
                response = client.get("/api/app/bootstrap", params={"refreshProjects": "true"})
                second_response = client.get("/api/app/bootstrap")

            self.assertEqual(normal_response.status_code, 200)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(second_response.status_code, 200)
            payload = response.json()
            projects = payload["health"]["projects"]
            self.assertEqual(projects["projects"], [])
            self.assertIn(projects["scan"]["status"], {"pending", "refreshing"})
            self.assertEqual(schedule_refresh.call_count, 1)
            schedule_refresh.assert_called_once_with(force=True)
        finally:
            dashboard_server._PROJECT_SNAPSHOT_SELECTION = original_service

    def test_projects_get_reads_cache_without_scheduling_refresh(self) -> None:
        original_service = dashboard_server._PROJECT_SNAPSHOT_SELECTION
        service = isolated_project_snapshot_service()
        service._cache = {
            "selectedProjectPath": "",
            "unityEditorPath": "",
            "projects": [{"name": "Cached Project", "path": "", "sources": ["test"]}],
        }
        service._cache_loaded = True
        dashboard_server._PROJECT_SNAPSHOT_SELECTION = service
        try:
            with (
                patch.object(type(service), "schedule_project_snapshot_refresh", return_value=True) as schedule_refresh,
                patch("dashboard_server.build_project_snapshot_payload", side_effect=AssertionError("GET /api/projects scanned project roots")),
                TestClient(dashboard_server.app) as client,
            ):
                response = client.get("/api/projects")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["projects"][0]["name"], "Cached Project")
            self.assertFalse(schedule_refresh.called)
        finally:
            dashboard_server._PROJECT_SNAPSHOT_SELECTION = original_service

    def test_full_health_reads_project_cache_without_scheduling_refresh(self) -> None:
        original_service = dashboard_server._PROJECT_SNAPSHOT_SELECTION
        service = isolated_project_snapshot_service()
        service._cache = {
            "selectedProjectPath": "",
            "unityEditorPath": "",
            "projects": [{"name": "Cached Project", "path": "", "sources": ["test"]}],
        }
        service._cache_loaded = True
        dashboard_server._PROJECT_SNAPSHOT_SELECTION = service
        try:
            with (
                patch.object(type(service), "schedule_project_snapshot_refresh", return_value=True) as schedule_refresh,
                patch("dashboard_server.build_project_snapshot_payload", side_effect=AssertionError("/api/health scanned project roots")),
                TestClient(dashboard_server.app) as client,
            ):
                response = client.get("/api/health")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["projects"]["projects"][0]["name"], "Cached Project")
            self.assertFalse(schedule_refresh.called)
        finally:
            dashboard_server._PROJECT_SNAPSHOT_SELECTION = original_service

    def test_root_serves_dashboard_page(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("VRCForge 控制台", response.text)
            self.assertIn("识图分析", response.text)
            self.assertIn("Gesture Manager Play Mode screenshots", response.text)
            self.assertIn("原图 / 当前脸", response.text)
            self.assertIn("目标参考图", response.text)
            self.assertIn("粘贴图片", response.text)
            self.assertIn("选择本地图片", response.text)

    def test_workspace_diff_summary_reads_git_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.name", "VRCForge Test"], cwd=root, check=True, capture_output=True, text=True)
            tracked = root / "tracked.txt"
            tracked.write_text("one\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True, text=True)

            tracked.write_text("one\ntwo\n", encoding="utf-8")
            (root / "new.txt").write_text("new\n", encoding="utf-8")

            with TestClient(dashboard_server.app) as client:
                response = client.get("/api/app/workspace/diff", params={"root": str(root)})
                patch_response = client.get("/api/app/workspace/diff", params={"root": str(root), "includePatch": "true"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema"], "vrcforge.workspace_diff.v1")
        self.assertEqual(payload["status"], "changed")
        self.assertGreaterEqual(payload["fileCount"], 2)
        self.assertGreaterEqual(payload["additions"], 1)
        self.assertTrue(any("tracked.txt" in item["path"] for item in payload["files"]))
        self.assertTrue(any("new.txt" in item["path"] for item in payload["files"]))
        self.assertEqual(payload.get("patch", ""), "")

        self.assertEqual(patch_response.status_code, 200)
        patch_payload = patch_response.json()
        self.assertIn("tracked.txt", patch_payload.get("patch", ""))
        self.assertFalse(patch_payload.get("patchTruncated", False))

    def test_workspace_diff_summary_does_not_fallback_for_non_git_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "UnityProject"
            project_root.mkdir()
            with TestClient(dashboard_server.app) as client:
                response = client.get("/api/app/workspace/diff", params={"root": str(project_root), "includePatch": "true"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["schema"], "vrcforge.workspace_diff.v1")
        self.assertEqual(payload["requestedRoot"], str(project_root))
        self.assertEqual(payload["status"], "not_git")
        self.assertEqual(payload["fileCount"], 0)
        self.assertEqual(payload["files"], [])
        self.assertNotIn("gitRoot", payload)
        self.assertNotIn("fallbackFromProjectRoot", payload)
        self.assertNotIn("src/App.tsx", json.dumps(payload, ensure_ascii=False))

    def test_runtime_snapshot_reports_non_git_project_without_app_diff_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "ProjectA"
            project_root.mkdir()
            with TestClient(dashboard_server.app) as client:
                response = client.get(
                    "/api/app/runtime/snapshot",
                    params={"sessionId": "session-a", "projectRoot": str(project_root), "globalOnly": "false"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema"], "vrcforge.desktop_runtime_snapshot.v1")
        workspace_diff = payload["workspaceDiff"]
        self.assertFalse(workspace_diff["ok"])
        self.assertEqual(workspace_diff["requestedRoot"], str(project_root))
        self.assertEqual(workspace_diff["status"], "not_git")
        self.assertEqual(workspace_diff["files"], [])
        self.assertNotIn("gitRoot", workspace_diff)
        self.assertNotIn("fallbackFromProjectRoot", workspace_diff)
        self.assertNotIn("src/App.tsx", json.dumps(workspace_diff, ensure_ascii=False))
        self.assertIn("approvals", payload)
        self.assertIn("runs", payload)
        self.assertIn("desktopActions", payload)
        self.assertIn("goals", payload)
        self.assertIn("memory", payload)

    def test_runtime_snapshot_without_scope_does_not_return_ledgers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            gateway.record_runtime_queue_event({"clientTurnId": "turn-a", "sessionId": "session-a", "message": "queued"})
            gateway.request_desktop_action({"action": "browser", "sessionId": "session-a", "message": "open"})
            gateway.goal.create_agent_goal({"title": "Scoped goal", "sessionId": "session-a"})
            gateway.create_agent_memory({"scope": "user", "text": "user memory", "kind": "preference"})
            original_gateway = dashboard_server.AGENT_GATEWAY
            try:
                dashboard_server.AGENT_GATEWAY = gateway
                with TestClient(dashboard_server.app) as client:
                    response = client.get("/api/app/runtime/snapshot")
            finally:
                dashboard_server.AGENT_GATEWAY = original_gateway

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["runs"]["count"], 0)
        self.assertEqual(payload["desktopActions"]["count"], 0)
        self.assertEqual(payload["goals"]["count"], 0)
        self.assertEqual(payload["memory"]["count"], 0)

    def test_agent_runtime_message_preserves_bounded_attachments(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/agent/message",
                json={
                    "message": "read the attached note",
                    "attachments": [
                        {
                            "id": "att-1",
                            "name": "note.txt",
                            "type": "text/plain",
                            "size": 11,
                            "text": "hello world",
                            "payloadKind": "text",
                        }
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["attachments"][0]["name"], "note.txt")
        self.assertEqual(payload["attachments"][0]["payloadKind"], "text")
        self.assertEqual(payload["attachments"][0]["text"], "hello world")
        self.assertTrue(payload["attachments"][0]["replayable"])
        self.assertTrue(payload["attachments"][0]["payloadHash"])

    def test_agent_runtime_message_keeps_vision_fallback_out_of_reply(self) -> None:
        previous_hook = dashboard_server.AGENT_GATEWAY.vision_analyze_fn

        def fake_vision(_message, images):
            return {
                "status": "unconfigured",
                "reason": "Main model is not vision-capable and no vision profile is configured.",
                "imageCount": len(images),
                "imageNames": [image.get("name") for image in images],
                "notice": "(vision fallback notice should stay out of assistant text)",
            }

        try:
            dashboard_server.AGENT_GATEWAY.vision_analyze_fn = fake_vision
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "describe the attached image",
                        "attachments": [
                            {
                                "id": "att-image",
                                "name": "probe.png",
                                "type": "image/png",
                                "size": 68,
                                "dataUrl": "data:image/png;base64,iVBORw0KGgo=",
                                "payloadKind": "data_url",
                            }
                        ],
                    },
                )
        finally:
            dashboard_server.AGENT_GATEWAY.vision_analyze_fn = previous_hook

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["vision"]["status"], "unconfigured")
        self.assertEqual(payload["vision"]["imageCount"], 1)
        self.assertEqual(payload["steps"][0]["kind"], "vision")
        self.assertEqual(payload["steps"][0]["imageCount"], 1)
        self.assertNotIn("vision fallback notice should stay out", payload["plan"].get("reply", ""))
        self.assertEqual(payload["plan"].get("visionStatus"), "unconfigured")

    def test_agent_runtime_message_runs_off_event_loop(self) -> None:
        with patch("dashboard_server.asyncio.to_thread", wraps=dashboard_server.asyncio.to_thread) as to_thread:
            with TestClient(dashboard_server.app) as client:
                response = client.post("/api/app/agent/message", json={"message": "check repository status"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            any(call.args and call.args[0] == dashboard_server.AGENT_GATEWAY.runtime_message for call in to_thread.call_args_list)
        )

    def test_advanced_settings_remember_only_ever_enabled_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            audit_path = root / "audit"
            gateway = AgentGateway(config_path, audit_path)

            self.assertEqual(
                gateway.advanced_settings_state(),
                {
                    "developerOptionsEnabled": False,
                    "developerOptionsEverEnabled": False,
                    "computerUseEnabled": False,
                    "computerUseEverEnabled": False,
                    "backgroundGoalNotificationsEnabled": True,
                    "roslynFullAutoEverEnabled": False,
                },
            )
            gateway.update_advanced_settings(
                developer_options_enabled=True,
                computer_use_enabled=True,
            )
            gateway.update_advanced_settings(
                developer_options_enabled=False,
                computer_use_enabled=False,
            )

            reloaded = AgentGateway(config_path, audit_path)
            state = reloaded.advanced_settings_state()

        self.assertFalse(state["developerOptionsEnabled"])
        self.assertFalse(state["computerUseEnabled"])
        self.assertTrue(state["developerOptionsEverEnabled"])
        self.assertTrue(state["computerUseEverEnabled"])
        self.assertNotIn("confirmedAt", state)
        self.assertNotIn("history", state)

    def test_computer_use_tool_is_visible_only_for_explicit_app_turn(self) -> None:
        captured: list[str] = []

        def plan_fn(_settings, prompt: str, *, stream_callback=None) -> LlmPlanResponse:
            captured.append(prompt)
            return LlmPlanResponse(
                text=json.dumps({"action": "reply", "reply": "done"}),
                usage={},
                reasoning={},
            )

        with (
            patch.object(
                dashboard_server.PROVIDER_CONFIGURATION,
                "current_api_config",
                return_value=_test_runtime_provider_config(),
            ),
            patch("dashboard_server.request_llm_plan_with_metadata", side_effect=plan_fn),
        ):
            with TestClient(dashboard_server.app) as client:
                ordinary = client.post(
                    "/api/app/agent/message",
                    json={"message": "ordinary turn", "session_id": "sess-ordinary-computer-use"},
                )
                grant = client.post(
                    "/api/app/agent/computer-use/grants",
                    json={"sessionId": "sess-explicit-computer-use", "clientTurnId": "turn-explicit"},
                )
                explicit = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "inspect the active window",
                        "session_id": "sess-explicit-computer-use",
                        "clientTurnId": "turn-explicit",
                        "computerUseRequested": True,
                        "computerUseGrantId": grant.json()["grantId"],
                    },
                )

        self.assertEqual(ordinary.status_code, 200)
        self.assertEqual(grant.status_code, 200)
        self.assertEqual(explicit.status_code, 200)
        self.assertEqual(len(captured), 2)
        self.assertNotIn("vrcforge_agent_desktop_action", captured[0])
        self.assertIn("vrcforge_agent_desktop_action", captured[1])
        self.assertFalse(dashboard_server.AGENT_GATEWAY.computer_use_turn_active())

    def test_computer_use_turn_grant_is_required_bound_and_single_use(self) -> None:
        response = LlmPlanResponse(
            text=json.dumps({"action": "reply", "reply": "done"}),
            usage={},
            reasoning={},
        )
        with (
            patch.object(
                dashboard_server.PROVIDER_CONFIGURATION,
                "current_api_config",
                return_value=_test_runtime_provider_config(),
            ),
            patch("dashboard_server.request_llm_plan_with_metadata", return_value=response),
        ):
            with TestClient(dashboard_server.app) as client:
                forged = client.post(
                    "/api/app/agent/message",
                    json={"message": "forged", "clientTurnId": "forged-turn", "computerUseRequested": True},
                )
                grant = client.post(
                    "/api/app/agent/computer-use/grants",
                    json={"sessionId": "grant-session", "clientTurnId": "granted-turn", "projectRoot": "ProjectA"},
                )
                granted = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "granted",
                        "session_id": "grant-session",
                        "clientTurnId": "granted-turn",
                        "projectRoot": "ProjectA",
                        "computerUseRequested": True,
                        "computerUseGrantId": grant.json()["grantId"],
                    },
                )
                replayed = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "replay",
                        "session_id": "grant-session",
                        "clientTurnId": "granted-turn",
                        "projectRoot": "ProjectA",
                        "computerUseRequested": True,
                        "computerUseGrantId": grant.json()["grantId"],
                    },
                )

        self.assertEqual(forged.status_code, 403)
        self.assertEqual(grant.status_code, 200)
        self.assertEqual(granted.status_code, 200)
        self.assertEqual(replayed.status_code, 403)

    def test_computer_use_direct_app_action_is_blocked_while_setting_is_off(self) -> None:
        try:
            dashboard_server.AGENT_GATEWAY.update_advanced_settings(
                developer_options_enabled=False,
                computer_use_enabled=False,
            )
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/desktop-actions",
                    json={"action": "computer_use", "params": {"operation": "screenshot"}},
                )
        finally:
            dashboard_server.AGENT_GATEWAY.update_advanced_settings(
                developer_options_enabled=True,
                computer_use_enabled=True,
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("Computer Use is disabled", response.json()["detail"])

    def test_desktop_capture_root_is_the_gateway_trusted_vision_root(self) -> None:
        self.assertEqual(
            dashboard_server.AGENT_GATEWAY.desktop.capture_dir.resolve(),
            (dashboard_server.AGENT_GATEWAY.audit_dir / "desktop-captures").resolve(),
        )

    def test_agent_desktop_action_is_explicit_and_audited(self) -> None:
        with patch("dashboard_server.asyncio.to_thread", wraps=dashboard_server.asyncio.to_thread) as to_thread:
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/desktop-actions",
                    json={
                        "action": "computer_use",
                        "prompt": "diagnose a desktop issue",
                        "sessionId": "sess-test",
                        "clientTurnId": "turn-test",
                        "projectRoot": "ProjectA",
                    },
                )
                listing = client.get("/api/app/agent/desktop-actions", params={"sessionId": "sess-test"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "unavailable")
        self.assertIn("Desktop control bridge", payload["error"])
        self.assertEqual(listing.status_code, 200)
        actions = listing.json()["actions"]
        self.assertEqual(actions[0]["action"], "computer_use")
        self.assertEqual(actions[0]["clientTurnId"], "turn-test")
        self.assertTrue(
            any(call.args and call.args[0] == dashboard_server.AGENT_GATEWAY.request_desktop_action for call in to_thread.call_args_list)
        )

    def test_desktop_bridge_lifecycle_claim_and_complete(self) -> None:
        with TestClient(dashboard_server.app) as client:
            registered = client.post(
                "/api/app/agent/desktop-bridge/register",
                json={"name": "mock-bridge", "provider": "mock-provider", "capabilities": ["computer_use"]},
            )
            self.assertEqual(registered.status_code, 200)
            registration = registered.json()
            bridge_id = registration["bridge"]["bridgeId"]
            bridge_credential = registration["bridgeCredential"]

            status = client.get("/api/app/agent/desktop-bridge")
            self.assertEqual(status.status_code, 200)
            status_payload = status.json()
            self.assertTrue(status_payload["connected"])
            self.assertEqual(status_payload["bridges"][0]["provider"], "mock-provider")
            self.assertIn("computer_use", status_payload["supportedActions"])
            self.assertIn("desktop_rescue", status_payload["supportedActions"])
            self.assertIn("embeddedExecutor", status_payload)
            self.assertIn("nativeOverlayInfo", status_payload["embeddedExecutor"])

            requested = client.post(
                "/api/app/agent/desktop-actions",
                json={
                    "action": "computer_use",
                    "prompt": "open settings window",
                    "sessionId": "sess-bridge",
                    "clientTurnId": "turn-bridge",
                    "projectRoot": "ProjectA",
                },
            )
            self.assertEqual(requested.status_code, 200)
            requested_payload = requested.json()
            self.assertEqual(requested_payload["status"], "requested")
            action_id = requested_payload["actionId"]
            self.assertTrue(action_id)

            heartbeat = client.post(
                "/api/app/agent/desktop-bridge/heartbeat",
                json={"bridgeId": bridge_id, "bridgeCredential": bridge_credential},
            )
            self.assertEqual(heartbeat.status_code, 200)
            self.assertEqual(heartbeat.json()["pendingActionCount"], 1)

            claimed = client.post(
                "/api/app/agent/desktop-actions/claim",
                json={"bridgeId": bridge_id, "bridgeCredential": bridge_credential},
            )
            self.assertEqual(claimed.status_code, 200)
            claimed_action = claimed.json()["action"]
            self.assertEqual(claimed_action["actionId"], action_id)
            self.assertEqual(claimed_action["status"], "claimed")
            self.assertEqual(claimed_action["bridgeId"], bridge_id)

            completed = client.post(
                "/api/app/agent/desktop-actions/complete",
                json={
                    "bridgeId": bridge_id,
                    "bridgeCredential": bridge_credential,
                    "actionId": action_id,
                    "status": "completed",
                    "result": {"summary": "settings window opened", "windowTitle": "Settings"},
                },
            )
            self.assertEqual(completed.status_code, 200)
            self.assertTrue(completed.json()["ok"])

            listing = client.get("/api/app/agent/desktop-actions", params={"sessionId": "sess-bridge"})

        actions = listing.json()["actions"]
        merged = [row for row in actions if row.get("actionId") == action_id]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["status"], "completed")
        self.assertEqual(merged[0]["action"], "computer_use")
        self.assertEqual(merged[0]["bridgeId"], bridge_id)

    def test_active_desktop_action_is_global_and_result_stays_transient(self) -> None:
        with TestClient(dashboard_server.app) as client:
            registration = client.post(
                "/api/app/agent/desktop-bridge/register",
                json={
                    "name": "embedded-proof",
                    "provider": "embedded-ctypes-win32",
                    "capabilities": ["computer_use"],
                    "operations": ["list_windows", "screenshot", "sequence"],
                },
            ).json()
            bridge_id = registration["bridge"]["bridgeId"]
            bridge_credential = registration["bridgeCredential"]
            requested = client.post(
                "/api/app/agent/desktop-actions",
                json={
                    "action": "computer_use",
                    "sessionId": "owner-session",
                    "projectRoot": "OwnerProject",
                    "params": {"operation": "sequence", "steps": [{"operation": "list_windows"}]},
                },
            ).json()
            action_id = requested["actionId"]
            client.post(
                "/api/app/agent/desktop-actions/claim",
                json={"bridgeId": bridge_id, "bridgeCredential": bridge_credential},
            )
            other_snapshot = client.get(
                "/api/app/runtime/snapshot",
                params={"sessionId": "different-session", "projectRoot": "DifferentProject"},
            ).json()
            completed = client.post(
                "/api/app/agent/desktop-actions/complete",
                json={
                    "bridgeId": bridge_id,
                    "bridgeCredential": bridge_credential,
                    "actionId": action_id,
                    "status": "completed",
                    "result": {
                        "operation": "sequence",
                        "stepCount": 1,
                        "steps": [
                            {
                                "index": 1,
                                "operation": "list_windows",
                                "result": {"operation": "list_windows", "count": 3, "windows": [{"title": "private"}]},
                            }
                        ],
                    },
                },
            )
            result = client.get(f"/api/app/agent/desktop-actions/{action_id}/result")
            listing = client.get("/api/app/agent/desktop-actions", params={"sessionId": "owner-session"}).json()
            after_snapshot = client.get(
                "/api/app/runtime/snapshot",
                params={"sessionId": "different-session", "projectRoot": "DifferentProject"},
            ).json()

        active = other_snapshot["activeDesktopActions"]["actions"]
        self.assertEqual(active[0]["actionId"], action_id)
        self.assertEqual(active[0]["status"], "claimed")
        self.assertEqual(completed.status_code, 200)
        self.assertTrue(result.json()["resultAvailable"])
        self.assertEqual(result.json()["result"]["steps"][0]["result"]["count"], 3)
        owner_row = next(item for item in listing["actions"] if item.get("actionId") == action_id)
        self.assertEqual(owner_row["resultSummary"]["steps"][0]["result"]["count"], 3)
        self.assertNotIn("windows", owner_row["resultSummary"]["steps"][0]["result"])
        self.assertEqual(after_snapshot["activeDesktopActions"]["actions"], [])

    def test_desktop_bridge_stale_heartbeat_blocks_claim_and_requests(self) -> None:
        with TestClient(dashboard_server.app) as client:
            registered = client.post(
                "/api/app/agent/desktop-bridge/register",
                json={"name": "stale-bridge", "provider": "mock-provider", "capabilities": ["computer_use"]},
            )
            registration = registered.json()
            bridge_id = registration["bridge"]["bridgeId"]
            bridge_credential = registration["bridgeCredential"]
            stale_at = (datetime.now(timezone.utc) - timedelta(seconds=999)).isoformat().replace("+00:00", "Z")
            dashboard_server.AGENT_GATEWAY.desktop._desktop_bridges[bridge_id]["lastHeartbeatAt"] = stale_at  # noqa: SLF001

            status = client.get("/api/app/agent/desktop-bridge")
            self.assertFalse(status.json()["connected"])

            requested = client.post(
                "/api/app/agent/desktop-actions",
                json={"action": "computer_use", "prompt": "should be unavailable", "sessionId": "sess-stale"},
            )
            self.assertEqual(requested.json()["status"], "unavailable")

            claimed = client.post(
                "/api/app/agent/desktop-actions/claim",
                json={"bridgeId": bridge_id, "bridgeCredential": bridge_credential},
            )
            self.assertEqual(claimed.status_code, 409)

    def test_desktop_bridge_capability_and_type_filtering(self) -> None:
        with TestClient(dashboard_server.app) as client:
            registered = client.post(
                "/api/app/agent/desktop-bridge/register",
                json={"name": "rescue-bridge", "provider": "mock-provider", "capabilities": ["desktop_rescue"]},
            )
            registration = registered.json()
            bridge_id = registration["bridge"]["bridgeId"]
            bridge_credential = registration["bridgeCredential"]

            computer_use = client.post(
                "/api/app/agent/desktop-actions",
                json={"action": "computer_use", "prompt": "no capable bridge", "sessionId": "sess-caps"},
            )
            self.assertEqual(computer_use.json()["status"], "unavailable")

            rescue = client.post(
                "/api/app/agent/desktop-actions",
                json={"action": "desktop_rescue", "prompt": "rescue the desktop", "sessionId": "sess-caps"},
            )
            rescue_payload = rescue.json()
            self.assertEqual(rescue_payload["status"], "requested")

            claimed = client.post(
                "/api/app/agent/desktop-actions/claim",
                json={"bridgeId": bridge_id, "bridgeCredential": bridge_credential},
            )
            claimed_action = claimed.json()["action"]
            self.assertEqual(claimed_action["actionId"], rescue_payload["actionId"])
            self.assertEqual(claimed_action["action"], "desktop_rescue")

    def test_desktop_bridge_unknown_and_failed_paths(self) -> None:
        with TestClient(dashboard_server.app) as client:
            missing = client.post(
                "/api/app/agent/desktop-bridge/heartbeat",
                json={"bridgeId": "bridge_missing", "bridgeCredential": "missing-credential"},
            )
            self.assertEqual(missing.status_code, 404)

            registered = client.post(
                "/api/app/agent/desktop-bridge/register",
                json={"name": "fail-bridge", "provider": "mock-provider", "capabilities": ["computer_use"]},
            )
            registration = registered.json()
            bridge_id = registration["bridge"]["bridgeId"]
            bridge_credential = registration["bridgeCredential"]
            requested = client.post(
                "/api/app/agent/desktop-actions",
                json={"action": "computer_use", "prompt": "will fail", "sessionId": "sess-fail"},
            )
            action_id = requested.json()["actionId"]

            failed = client.post(
                "/api/app/agent/desktop-actions/complete",
                json={
                    "bridgeId": bridge_id,
                    "bridgeCredential": bridge_credential,
                    "actionId": action_id,
                    "status": "failed",
                    "error": "window not found",
                },
            )
            self.assertEqual(failed.status_code, 409)

            claimed = client.post(
                "/api/app/agent/desktop-actions/claim",
                json={"bridgeId": bridge_id, "bridgeCredential": bridge_credential},
            )
            self.assertEqual(claimed.status_code, 200)
            self.assertEqual(claimed.json()["action"]["actionId"], action_id)

            failed = client.post(
                "/api/app/agent/desktop-actions/complete",
                json={
                    "bridgeId": bridge_id,
                    "bridgeCredential": bridge_credential,
                    "actionId": action_id,
                    "status": "failed",
                    "error": "window not found",
                },
            )
            self.assertEqual(failed.status_code, 200)
            self.assertFalse(failed.json()["ok"])

            reclaim = client.post(
                "/api/app/agent/desktop-actions/claim",
                json={"bridgeId": bridge_id, "bridgeCredential": bridge_credential},
            )
            self.assertEqual(reclaim.status_code, 200)
            self.assertIsNone(reclaim.json()["action"])

            listing = client.get("/api/app/agent/desktop-actions", params={"sessionId": "sess-fail"})

        actions = listing.json()["actions"]
        failed_rows = [row for row in actions if row.get("actionId") == action_id]
        self.assertEqual(len(failed_rows), 1)
        self.assertEqual(failed_rows[0]["status"], "failed")
        self.assertIn("window not found", failed_rows[0]["error"])

    def test_desktop_bridge_requires_explicit_capability_and_credential(self) -> None:
        with TestClient(dashboard_server.app) as client:
            missing_capability = client.post(
                "/api/app/agent/desktop-bridge/register",
                json={"name": "empty-bridge", "provider": "mock-provider", "capabilities": []},
            )
            unsupported_capability = client.post(
                "/api/app/agent/desktop-bridge/register",
                json={"name": "bad-bridge", "provider": "mock-provider", "capabilities": ["shell"]},
            )
            registered = client.post(
                "/api/app/agent/desktop-bridge/register",
                json={"name": "auth-bridge", "provider": "mock-provider", "capabilities": ["computer_use"]},
            )
            bridge_id = registered.json()["bridge"]["bridgeId"]
            wrong_credential = client.post(
                "/api/app/agent/desktop-bridge/heartbeat",
                json={"bridgeId": bridge_id, "bridgeCredential": "wrong"},
            )

        self.assertEqual(missing_capability.status_code, 400)
        self.assertEqual(unsupported_capability.status_code, 400)
        self.assertEqual(wrong_credential.status_code, 401)

    def test_desktop_interaction_requires_auto_or_full_permission(self) -> None:
        with TestClient(dashboard_server.app) as client:
            client.post(
                "/api/app/agent/desktop-bridge/register",
                json={"name": "permission-bridge", "provider": "mock-provider", "capabilities": ["computer_use"]},
            )
            click = client.post(
                "/api/app/agent/desktop-actions",
                json={"action": "computer_use", "params": {"operation": "click", "x": 10, "y": 10}},
            )
            screenshot = client.post(
                "/api/app/agent/desktop-actions",
                json={"action": "computer_use", "params": {"operation": "screenshot"}},
            )

        self.assertEqual(click.status_code, 403)
        self.assertEqual(screenshot.status_code, 200)
        self.assertEqual(screenshot.json()["status"], "requested")

    def test_desktop_bridge_preserves_params_and_fails_closed_after_interactive_claim_loss(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.execution_mode = "auto"
        dashboard_server.AGENT_GATEWAY.save_config(config)
        with TestClient(dashboard_server.app) as client:
            first_registration = client.post(
                "/api/app/agent/desktop-bridge/register",
                json={"name": "first-bridge", "provider": "mock-provider", "capabilities": ["computer_use"]},
            ).json()
            first_id = first_registration["bridge"]["bridgeId"]
            first_credential = first_registration["bridgeCredential"]
            requested = client.post(
                "/api/app/agent/desktop-actions",
                json={
                    "action": "computer_use",
                    "prompt": "focus and inspect",
                    "sessionId": "sess-requeue",
                    "params": {
                        "operation": "sequence",
                        "steps": [
                            {"operation": "focus_window", "titleContains": "Settings"},
                            {"operation": "screenshot"},
                        ],
                    },
                },
            ).json()
            action_id = requested["actionId"]
            claimed = client.post(
                "/api/app/agent/desktop-actions/claim",
                json={"bridgeId": first_id, "bridgeCredential": first_credential},
            ).json()["action"]
            self.assertEqual(claimed["params"]["operation"], "sequence")
            self.assertEqual(claimed["params"]["steps"][0]["titleContains"], "Settings")

            stale_at = (datetime.now(timezone.utc) - timedelta(seconds=999)).isoformat().replace("+00:00", "Z")
            dashboard_server.AGENT_GATEWAY.desktop._desktop_bridges[first_id]["lastHeartbeatAt"] = stale_at  # noqa: SLF001
            replacement_registration = client.post(
                "/api/app/agent/desktop-bridge/register",
                json={"name": "replacement-bridge", "provider": "mock-provider", "capabilities": ["computer_use"]},
            ).json()
            replacement_id = replacement_registration["bridge"]["bridgeId"]
            replacement_credential = replacement_registration["bridgeCredential"]
            replacement_claim = client.post(
                "/api/app/agent/desktop-actions/claim",
                json={"bridgeId": replacement_id, "bridgeCredential": replacement_credential},
            )
            listing = client.get("/api/app/agent/desktop-actions", params={"sessionId": "sess-requeue"})

        self.assertEqual(replacement_claim.status_code, 200)
        self.assertIsNone(replacement_claim.json()["action"])
        failed_action = next(item for item in listing.json()["actions"] if item.get("actionId") == action_id)
        self.assertEqual(failed_action["status"], "failed")
        self.assertIn("not replayed", failed_action["error"])

    def test_desktop_bridge_requeues_read_only_action_after_stale_claim(self) -> None:
        with TestClient(dashboard_server.app) as client:
            first_registration = client.post(
                "/api/app/agent/desktop-bridge/register",
                json={"name": "read-bridge", "provider": "mock-provider", "capabilities": ["computer_use"]},
            ).json()
            first_id = first_registration["bridge"]["bridgeId"]
            first_credential = first_registration["bridgeCredential"]
            requested = client.post(
                "/api/app/agent/desktop-actions",
                json={
                    "action": "computer_use",
                    "prompt": "capture after reconnect",
                    "params": {"operation": "screenshot", "region": {"left": 0, "top": 0, "width": 32, "height": 32}},
                },
            ).json()
            client.post(
                "/api/app/agent/desktop-actions/claim",
                json={"bridgeId": first_id, "bridgeCredential": first_credential},
            )
            stale_at = (datetime.now(timezone.utc) - timedelta(seconds=999)).isoformat().replace("+00:00", "Z")
            dashboard_server.AGENT_GATEWAY.desktop._desktop_bridges[first_id]["lastHeartbeatAt"] = stale_at  # noqa: SLF001
            replacement_registration = client.post(
                "/api/app/agent/desktop-bridge/register",
                json={"name": "read-replacement", "provider": "mock-provider", "capabilities": ["computer_use"]},
            ).json()
            replacement_claim = client.post(
                "/api/app/agent/desktop-actions/claim",
                json={
                    "bridgeId": replacement_registration["bridge"]["bridgeId"],
                    "bridgeCredential": replacement_registration["bridgeCredential"],
                },
            )

        self.assertEqual(replacement_claim.status_code, 200)
        self.assertEqual(replacement_claim.json()["action"]["actionId"], requested["actionId"])
        self.assertEqual(replacement_claim.json()["action"]["params"]["operation"], "screenshot")

    def test_desktop_bridge_claim_and_complete_are_idempotent(self) -> None:
        with TestClient(dashboard_server.app) as client:
            registration = client.post(
                "/api/app/agent/desktop-bridge/register",
                json={"name": "retry-bridge", "provider": "mock-provider", "capabilities": ["computer_use"]},
            ).json()
            bridge_id = registration["bridge"]["bridgeId"]
            bridge_credential = registration["bridgeCredential"]
            requested = client.post(
                "/api/app/agent/desktop-actions",
                json={"action": "computer_use", "prompt": "retry proof", "sessionId": "sess-retry"},
            ).json()
            claim_body = {
                "bridgeId": bridge_id,
                "bridgeCredential": bridge_credential,
                "claimRequestId": "claim-retry-1",
            }
            first_claim = client.post("/api/app/agent/desktop-actions/claim", json=claim_body).json()
            retry_claim = client.post("/api/app/agent/desktop-actions/claim", json=claim_body).json()
            completion_body = {
                "bridgeId": bridge_id,
                "bridgeCredential": bridge_credential,
                "actionId": requested["actionId"],
                "status": "completed",
                "result": {"summary": "done"},
            }
            first_complete = client.post("/api/app/agent/desktop-actions/complete", json=completion_body).json()
            retry_complete = client.post("/api/app/agent/desktop-actions/complete", json=completion_body).json()

        self.assertEqual(first_claim["action"]["actionId"], requested["actionId"])
        self.assertEqual(retry_claim["action"]["actionId"], requested["actionId"])
        self.assertTrue(retry_claim["idempotent"])
        self.assertFalse(first_complete["idempotent"])
        self.assertTrue(retry_complete["idempotent"])

    def test_desktop_action_cancel_lifecycle(self) -> None:
        with TestClient(dashboard_server.app) as client:
            registration = client.post(
                "/api/app/agent/desktop-bridge/register",
                json={"name": "cancel-bridge", "provider": "mock-provider", "capabilities": ["computer_use"]},
            ).json()
            bridge_id = registration["bridge"]["bridgeId"]
            bridge_credential = registration["bridgeCredential"]
            requested = client.post(
                "/api/app/agent/desktop-actions",
                json={"action": "computer_use", "prompt": "wait for cancellation", "sessionId": "sess-cancel"},
            ).json()
            action_id = requested["actionId"]
            client.post(
                "/api/app/agent/desktop-actions/claim",
                json={"bridgeId": bridge_id, "bridgeCredential": bridge_credential},
            )
            cancel = client.post(
                f"/api/app/agent/desktop-actions/{action_id}/cancel",
                json={"reason": "User clicked Stop"},
            )
            completed = client.post(
                "/api/app/agent/desktop-actions/complete",
                json={
                    "bridgeId": bridge_id,
                    "bridgeCredential": bridge_credential,
                    "actionId": action_id,
                    "status": "completed",
                    "result": {"summary": "late success must not win"},
                },
            )
            retry_cancel = client.post(
                f"/api/app/agent/desktop-actions/{action_id}/cancel",
                json={"reason": "retry"},
            )

        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(cancel.json()["status"], "cancel_requested")
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "cancelled")
        self.assertEqual(retry_cancel.status_code, 200)
        self.assertTrue(retry_cancel.json()["idempotent"])

    def test_runtime_stop_cascades_to_owned_desktop_actions(self) -> None:
        with TestClient(dashboard_server.app) as client:
            client.post(
                "/api/app/agent/desktop-bridge/register",
                json={"name": "runtime-cancel-bridge", "provider": "mock-provider", "capabilities": ["computer_use"]},
            )
            requested = client.post(
                "/api/app/agent/desktop-actions",
                json={
                    "action": "computer_use",
                    "prompt": "owned runtime action",
                    "sessionId": "sess-runtime-stop",
                    "clientTurnId": "client-runtime-stop",
                    "params": {"operation": "wait", "durationMs": 10000},
                },
            ).json()
            stopped = client.post(
                "/api/app/agent/runs/cancel",
                json={"clientTurnId": "client-runtime-stop", "reason": "user_stop"},
            )
            listing = client.get("/api/app/agent/desktop-actions", params={"limit": 20}).json()

        action = next(item for item in listing["actions"] if item.get("actionId") == requested["actionId"])
        self.assertEqual(stopped.status_code, 200)
        self.assertIn(requested["actionId"], stopped.json()["cancelledDesktopActionIds"])
        self.assertEqual(action["status"], "cancelled")

    def test_desktop_action_projection_orders_by_latest_lifecycle_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            registration = gateway.register_desktop_bridge(
                {"name": "projection-bridge", "provider": "mock-provider", "capabilities": ["computer_use"]}
            )
            bridge_id = registration["bridge"]["bridgeId"]
            bridge_credential = registration["bridgeCredential"]
            action_id = gateway.request_desktop_action({"action": "computer_use", "prompt": "old request"})["actionId"]
            gateway.claim_desktop_action({"bridgeId": bridge_id, "bridgeCredential": bridge_credential})
            for index in range(230):
                gateway.request_desktop_action({"action": "annotation", "prompt": f"newer legacy row {index}"})
            gateway.complete_desktop_action(
                {
                    "bridgeId": bridge_id,
                    "bridgeCredential": bridge_credential,
                    "actionId": action_id,
                    "status": "completed",
                }
            )
            listing = gateway.list_desktop_actions(limit=5)

        self.assertEqual(listing["actions"][0]["actionId"], action_id)
        self.assertEqual(listing["actions"][0]["status"], "completed")

    def test_agent_goals_are_durable_and_statused(self) -> None:
        with TestClient(dashboard_server.app) as client:
            created = client.post(
                "/api/app/agent/goals",
                json={"title": "Finish avatar QA", "summary": "Long task", "sessionId": "sess-goal", "projectRoot": "ProjectA"},
            )
            goal_id = created.json()["goal"]["goalId"]
            paused = client.post(f"/api/app/agent/goals/{goal_id}", json={"status": "paused", "summary": "Waiting on user"})
            completed = client.post(f"/api/app/agent/goals/{goal_id}", json={"status": "completed", "summary": "Done"})
            listing = client.get("/api/app/agent/goals", params={"sessionId": "sess-goal"})

        self.assertEqual(created.status_code, 200)
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(completed.status_code, 200)
        goals = listing.json()["goals"]
        self.assertEqual(goals[0]["goalId"], goal_id)
        self.assertEqual(goals[0]["status"], "completed")
        self.assertEqual(goals[0]["title"], "Finish avatar QA")

    def test_runtime_run_status_never_marks_explicitly_incomplete_plans_completed(self) -> None:
        for next_step, expected in (
            ("context_compaction_required", "blocked"),
            ("await_user_instruction", "blocked"),
            ("paused", "blocked"),
            ("loop_suppressed", "failed"),
            ("done", "completed"),
        ):
            with self.subTest(next_step=next_step):
                status = AgentGateway._runtime_turn_run_status(
                    top_plan={"nextStep": next_step},
                    shell_payload=None,
                    skill_payload=None,
                    write_payload=None,
                    approval_id="",
                )
                self.assertEqual(status, expected)
        failed_before_pause = AgentGateway._runtime_turn_run_status(
            top_plan={"nextStep": "paused"},
            shell_payload=None,
            skill_payload={"ok": False, "status": "failed"},
            write_payload=None,
            approval_id="",
        )
        self.assertEqual(failed_before_pause, "failed")

    def test_agent_goal_wake_lifecycle_and_cross_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config" / "agent_gateway.json"
            audit_dir = root / "audit"
            gateway = AgentGateway(config_path, audit_dir)
            past = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
            one_shot = gateway.goal.create_agent_goal({"title": "One shot", "summary": "Ship wake slice", "wakeAt": past, "chatId": "chat-one"})["goal"]
            recurring = gateway.goal.create_agent_goal({"title": "Recurring", "wakeAt": past, "wakeEveryMinutes": 30, "chatId": "chat-recurring"})["goal"]
            idle = gateway.goal.create_agent_goal({"title": "No schedule", "chatId": "chat-idle"})["goal"]

            with self.assertRaises(AgentGoalServiceError) as bound:
                gateway.goal.create_agent_goal({"title": "Too fast", "wakeEveryMinutes": 2, "chatId": "chat-fast"})
            self.assertEqual(bound.exception.status_code, 400)

            due = gateway.goal.list_due_agent_goals()
            self.assertEqual(
                {goal["goalId"] for goal in due["goals"]},
                {one_shot["goalId"], recurring["goalId"]},
            )

            woken = gateway.goal.wake_agent_goal(one_shot["goalId"])
            self.assertEqual(woken["resumePrompt"], "Resume goal: One shot\nContext: Ship wake slice")
            self.assertEqual(woken["goal"]["wakeAt"], past)
            self.assertEqual(woken["goal"]["wakeCount"], 0)
            duplicate = gateway.goal.wake_agent_goal(one_shot["goalId"])
            self.assertEqual(duplicate["delivery"]["deliveryId"], woken["delivery"]["deliveryId"])
            gateway.goal.begin_agent_goal_delivery(
                woken["delivery"]["deliveryId"],
                {"clientTurnId": woken["delivery"]["clientTurnId"]},
            )
            gateway.goal.complete_agent_goal_delivery(woken["delivery"]["deliveryId"], {"turnId": "one-shot-done"})
            completed_one_shot = gateway.goal.project_goals()[one_shot["goalId"]]
            self.assertEqual(completed_one_shot["wakeAt"], "")
            self.assertEqual(completed_one_shot["wakeCount"], 1)

            rewoken = gateway.goal.wake_agent_goal(recurring["goalId"])
            gateway.goal.begin_agent_goal_delivery(
                rewoken["delivery"]["deliveryId"],
                {"clientTurnId": rewoken["delivery"]["clientTurnId"]},
            )
            gateway.goal.complete_agent_goal_delivery(rewoken["delivery"]["deliveryId"], {"turnId": "recurring-done"})
            recurring_after_completion = gateway.goal.project_goals()[recurring["goalId"]]
            next_wake = datetime.fromisoformat(recurring_after_completion["wakeAt"])
            self.assertGreater(next_wake, datetime.now(timezone.utc))
            # Status-only update carries no wake keys, so the schedule must survive untouched.
            updated = gateway.goal.update_agent_goal(recurring["goalId"], {"status": "active", "summary": "still going"})["goal"]
            self.assertEqual(updated["wakeAt"], recurring_after_completion["wakeAt"])
            self.assertEqual(updated["wakeEveryMinutes"], 30)
            # Explicit empty values clear the schedule.
            cleared = gateway.goal.update_agent_goal(recurring["goalId"], {"status": "active", "wakeAt": "", "wakeEveryMinutes": 0})["goal"]
            self.assertEqual(cleared["wakeAt"], "")
            self.assertEqual(cleared["wakeEveryMinutes"], 0)

            gateway.goal.update_agent_goal(idle["goalId"], {"status": "active", "wakeAt": past})
            reopened = AgentGateway(config_path, audit_dir)
            due_after_restart = reopened.goal.list_due_agent_goals()
            self.assertEqual([goal["goalId"] for goal in due_after_restart["goals"]], [idle["goalId"]])
            resumed = reopened.goal.wake_agent_goal(idle["goalId"])
            self.assertEqual(resumed["resumePrompt"], "Resume goal: No schedule")
            reopened_goals = {goal["goalId"]: goal for goal in reopened.goal.list_agent_goals(limit=10)["goals"]}
            self.assertEqual(reopened_goals[one_shot["goalId"]]["wakeCount"], 1)
            self.assertEqual(reopened_goals[one_shot["goalId"]]["wakeAt"], "")

    def test_timed_out_wake_worker_rearms_after_its_thread_exits(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with TestClient(dashboard_server.app) as client:
            created = client.post(
                "/api/app/agent/goals",
                json={"title": "Slow wake", "chatId": "chat-slow-wake", "wakeAt": past},
            ).json()["goal"]
            original_wake = dashboard_server.AGENT_GOALS.wake_agent_goal

            def slow_wake(*args, **kwargs):
                time.sleep(0.05)
                return original_wake(*args, **kwargs)

            with (
                patch.object(dashboard_server.AGENT_GOALS, "wake_agent_goal", side_effect=slow_wake),
                patch("dashboard_server.PHASE_TIMEOUT_SECONDS", {"wake": 0.01}),
            ):
                response = client.post(
                    f"/api/app/agent/goals/{created['goalId']}/wake",
                    json={"chatId": "chat-slow-wake"},
                )
                self.assertEqual(response.status_code, 504)
                time.sleep(0.15)

            deliveries = dashboard_server.AGENT_GOALS.project_deliveries()
            delivery = next(
                item for item in deliveries.values() if item.get("chatId") == "chat-slow-wake"
            )
            self.assertEqual(delivery["status"], "interrupted")
            self.assertEqual(delivery["failureLabel"], "watchdog_wake_timeout")
            self.assertFalse(delivery["consumeRetry"])

    def test_claimed_goal_handoff_can_be_released_without_consuming_retry(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with TestClient(dashboard_server.app) as client:
            goal = client.post(
                "/api/app/agent/goals",
                json={"title": "Deferred handoff", "chatId": "chat-handoff", "wakeAt": past},
            ).json()["goal"]
            claimed = client.post(
                f"/api/app/agent/goals/{goal['goalId']}/wake",
                json={"chatId": "chat-handoff"},
            ).json()["delivery"]
            released = client.post(
                f"/api/app/agent/goals/deliveries/{claimed['deliveryId']}/defer",
                json={"expectedRevision": claimed["revision"]},
            )

        self.assertEqual(released.status_code, 200)
        delivery = released.json()["delivery"]
        self.assertEqual(delivery["status"], "interrupted")
        self.assertEqual(delivery["failureLabel"], "client_handoff_deferred")
        self.assertFalse(delivery["consumeRetry"])
        self.assertEqual(delivery["attempt"], 1)

    def test_linked_approval_terminal_state_is_gateway_authoritative(self) -> None:
        for outcome in ("rejected", "revision_requested", "applied", "failed"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                gateway = AgentGateway(root / "config.json", root / "audit")
                gateway.register_write_handler(
                    "vrcforge_test_linked_write",
                    "Test linked write.",
                    "high",
                    lambda _arguments, result=outcome: (
                        {"ok": False, "error": "bounded failure"}
                        if result == "failed"
                        else {"ok": True, "status": "applied"}
                    ),
                )
                past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
                goal = gateway.goal.create_agent_goal(
                    {"title": f"Linked {outcome}", "chatId": "chat-approval", "wakeAt": past}
                )["goal"]
                woken = gateway.goal.wake_agent_goal(goal["goalId"])
                delivery_id = woken["delivery"]["deliveryId"]
                gateway.goal.begin_agent_goal_delivery(
                    delivery_id,
                    {"clientTurnId": woken["delivery"]["clientTurnId"]},
                )
                request = gateway.create_apply_request(
                    {
                        "target_tool": "vrcforge_test_linked_write",
                        "arguments": {},
                        "never_auto_approve": True,
                        "goalDeliveryId": delivery_id,
                    }
                )
                approval_id = request["approval"]["id"]
                gateway.goal.block_agent_goal_delivery(
                    delivery_id,
                    kind="approval",
                    reference=approval_id,
                    response={"approvalId": approval_id},
                )
                if outcome == "rejected":
                    terminal = gateway.reject(approval_id)["goalDelivery"]
                    expected_status = "denied"
                elif outcome == "revision_requested":
                    terminal = gateway.request_approval_revision(
                        approval_id,
                        reason="change requested",
                    )["goalDelivery"]
                    expected_status = "denied"
                else:
                    gateway.approve(approval_id)
                    with patch.object(gateway, "_create_pre_write_checkpoint", return_value=None):
                        execution = gateway.apply_approved({"approvalId": approval_id})
                    terminal = execution["goalDelivery"]
                    expected_status = "completed" if outcome == "applied" else "failed"
                self.assertEqual(terminal["delivery"]["status"], expected_status)
                self.assertEqual(gateway.goal.reconcile_agent_goal_watchdogs()["deliveries"], [])

    def test_linked_approval_scope_error_restores_waiting_delivery_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as project_a, tempfile.TemporaryDirectory() as project_b:
            past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            with TestClient(dashboard_server.app) as client:
                goal = dashboard_server.AGENT_GOALS.create_agent_goal(
                    {
                        "title": "Approval scope retry",
                        "chatId": "chat-approval-scope",
                        "projectRoot": project_a,
                        "wakeAt": past,
                    }
                )["goal"]
                woken = dashboard_server.AGENT_GOALS.wake_agent_goal(goal["goalId"])
                delivery_id = woken["delivery"]["deliveryId"]
                dashboard_server.AGENT_GOALS.begin_agent_goal_delivery(
                    delivery_id,
                    {"clientTurnId": woken["delivery"]["clientTurnId"]},
                )
                approval = dashboard_server.AGENT_GATEWAY._new_approval(
                    "test-agent",
                    "vrcforge_test_linked_write",
                    {"projectRoot": project_a},
                    "bounded test",
                    {},
                    "high",
                    goal_delivery_id=delivery_id,
                )
                dashboard_server.AGENT_GOALS.block_agent_goal_delivery(
                    delivery_id,
                    kind="approval",
                    reference=approval["id"],
                    response={"approvalId": approval["id"]},
                )

                rejected = client.post(
                    f"/api/app/agent/approvals/{approval['id']}/approve",
                    json={"expectedProjectRoot": project_b, "globalOnly": False},
                )

            self.assertEqual(rejected.status_code, 409)
            current = dashboard_server.AGENT_GOALS.project_deliveries()[delivery_id]
            self.assertEqual(current["status"], "blocked")
            self.assertFalse(current["approvalPendingResolution"])
            self.assertEqual(dashboard_server.AGENT_GATEWAY._approvals[approval["id"]]["status"], "pending")
            self.assertEqual(dashboard_server.RUNTIME_LANE_BUDGET.snapshot()["total"], 0)

    def test_linked_approval_revision_broadcasts_terminal_background_state(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        goal = dashboard_server.AGENT_GOALS.create_agent_goal(
            {"title": "Approval revision", "chatId": "chat-approval-revision", "wakeAt": past}
        )["goal"]
        woken = dashboard_server.AGENT_GOALS.wake_agent_goal(goal["goalId"])
        delivery_id = woken["delivery"]["deliveryId"]
        dashboard_server.AGENT_GOALS.begin_agent_goal_delivery(
            delivery_id,
            {"clientTurnId": woken["delivery"]["clientTurnId"]},
        )
        approval = dashboard_server.AGENT_GATEWAY._new_approval(
            "test-agent",
            "vrcforge_test_linked_write",
            {},
            "bounded test",
            {},
            "high",
            goal_delivery_id=delivery_id,
        )
        dashboard_server.AGENT_GOALS.block_agent_goal_delivery(
            delivery_id,
            kind="approval",
            reference=approval["id"],
            response={"approvalId": approval["id"]},
        )

        broadcast = AsyncMock()
        with patch("dashboard_server.broadcast_background_goal_state", new=broadcast):
            payload = asyncio.run(
                dashboard_server.app_agent_request_approval_revision(
                    approval["id"],
                    dashboard_server.AgentApprovalRevisionRequest(reason="change requested"),
                )
            )

        self.assertEqual(payload["approval"]["status"], "revision_requested")
        self.assertEqual(payload["goalDelivery"]["delivery"]["status"], "denied")
        self.assertEqual(
            dashboard_server.AGENT_GOALS.project_deliveries()[delivery_id]["status"],
            "denied",
        )
        broadcast.assert_awaited_once_with({})

    def test_restart_closes_unrecoverable_linked_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config.json", root / "audit")
            past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            goal = gateway.goal.create_agent_goal(
                {
                    "title": "Restart approval",
                    "chatId": "chat-restart-approval",
                    "wakeAt": past,
                    "wakeEveryMinutes": 5,
                }
            )["goal"]
            woken = gateway.goal.wake_agent_goal(goal["goalId"])
            delivery_id = woken["delivery"]["deliveryId"]
            gateway.goal.begin_agent_goal_delivery(
                delivery_id,
                {"clientTurnId": woken["delivery"]["clientTurnId"]},
            )
            approval = gateway._new_approval(
                "test-agent",
                "vrcforge_test_linked_write",
                {},
                "bounded test",
                {},
                "high",
                goal_delivery_id=delivery_id,
            )
            gateway.goal.block_agent_goal_delivery(
                delivery_id,
                kind="approval",
                reference=approval["id"],
                response={"approvalId": approval["id"]},
            )

            reopened = AgentGateway(root / "config.json", root / "audit")
            recovery = reopened.goal.reconcile_agent_goal_watchdogs()

            self.assertEqual(recovery["deliveries"][-1]["status"], "failed")
            self.assertEqual(recovery["deliveries"][-1]["failureLabel"], "approval_recovery_required")
            self.assertEqual(reopened.goal.project_goals()[goal["goalId"]]["wakeCount"], 1)

    def test_agent_goal_projection_keeps_creation_fields_beyond_legacy_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            past = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
            created = gateway.goal.create_agent_goal(
                {
                    "title": "Long-lived recurring goal",
                    "summary": "Creation metadata must survive",
                    "wakeAt": past,
                    "wakeEveryMinutes": 30,
                    "chatId": "chat-long-lived",
                }
            )["goal"]

            with gateway.goal.log_path.open("a", encoding="utf-8") as log_file:
                for index in range(2001):
                    log_file.write(
                        json.dumps(
                            {
                                "schema": "vrcforge.agent_goal.v1",
                                "event": "goal_updated",
                                "goalId": created["goalId"],
                                "status": "active",
                                "summary": f"event {index}",
                                "createdAt": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                        + "\n"
                    )

            projected = gateway.goal.list_agent_goals(limit=1)["goals"][0]
            self.assertEqual(projected["title"], "Long-lived recurring goal")
            self.assertEqual(projected["wakeAt"], past)
            self.assertEqual(projected["wakeEveryMinutes"], 30)

    def test_agent_goal_due_filter_runs_before_display_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            past = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
            due_goal = gateway.goal.create_agent_goal({"title": "Old due goal", "wakeAt": past, "chatId": "chat-due"})["goal"]
            for index in range(60):
                gateway.goal.create_agent_goal({"title": f"New unscheduled goal {index}"})

            due = gateway.goal.list_due_agent_goals(limit=20)
            self.assertEqual([goal["goalId"] for goal in due["goals"]], [due_goal["goalId"]])

    def test_agent_goal_wake_is_atomic_within_gateway_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            past = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
            goal = gateway.goal.create_agent_goal({"title": "Wake once", "wakeAt": past, "chatId": "chat-atomic"})["goal"]
            start = threading.Barrier(4)
            results: list[str | int] = []
            results_lock = threading.Lock()
            original_append = gateway._append_jsonl

            def slow_append(*args, **kwargs):
                time.sleep(0.05)
                return original_append(*args, **kwargs)

            def wake() -> None:
                start.wait()
                try:
                    gateway.goal.wake_agent_goal(goal["goalId"])
                    result: str | int = "ok"
                except AgentGoalServiceError as exc:
                    result = exc.status_code
                with results_lock:
                    results.append(result)

            def update() -> None:
                start.wait()
                gateway.goal.update_agent_goal(goal["goalId"], {"status": "active", "summary": "concurrent update"})
                with results_lock:
                    results.append("updated")

            with patch.object(gateway, "_append_jsonl", side_effect=slow_append):
                threads = [threading.Thread(target=wake) for _ in range(2)] + [threading.Thread(target=update)]
                for thread in threads:
                    thread.start()
                start.wait()
                for thread in threads:
                    thread.join(timeout=5)

            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertCountEqual(results, ["ok", "ok", "updated"])
            projected = gateway.goal.list_agent_goals(limit=1)["goals"][0]
            self.assertEqual(projected["wakeCount"], 0)
            self.assertEqual(projected["summary"], "concurrent update")
            events = gateway._read_jsonl(gateway.goal.log_path, limit=0)
            self.assertEqual(sum(event.get("event") == "goal_delivery_pending" for event in events), 1)
            self.assertEqual(sum(event.get("event") == "goal_delivery_claimed" for event in events), 1)
            self.assertEqual(sum(event.get("event") == "goal_updated" for event in events), 1)

    def test_agent_goal_wake_routes_and_key_presence_semantics(self) -> None:
        with TestClient(dashboard_server.app) as client:
            past = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
            created = client.post(
                "/api/app/agent/goals",
                json={
                    "title": "Wake route goal",
                    "summary": "Route proof",
                    "sessionId": "sess-wake",
                    "chatId": "chat-wake",
                    "wakeAt": past,
                    "wakeEveryMinutes": 60,
                },
            )
            goal_id = created.json()["goal"]["goalId"]
            due = client.get("/api/app/agent/goals/due", params={"sessionId": "sess-wake"})
            woken = client.post(f"/api/app/agent/goals/{goal_id}/wake", json={"sessionId": "sess-wake"})
            second = client.post(f"/api/app/agent/goals/{goal_id}/wake", json={"sessionId": "sess-wake"})
            paused = client.post(f"/api/app/agent/goals/{goal_id}", json={"status": "paused", "sessionId": "sess-wake"})
            cleared = client.post(
                f"/api/app/agent/goals/{goal_id}",
                json={"status": "active", "sessionId": "sess-wake", "wakeAt": None, "wakeEveryMinutes": None},
            )
            overflow = client.post(
                "/api/app/agent/goals",
                json={"title": "Overflow timestamp", "chatId": "chat-overflow", "wakeAt": "9999-12-31T23:59:59-23:59"},
            )

        self.assertEqual(created.status_code, 200)
        self.assertEqual(due.status_code, 200)
        self.assertIn(goal_id, [goal["goalId"] for goal in due.json()["goals"]])
        self.assertEqual(woken.status_code, 200)
        woken_goal = woken.json()["goal"]
        self.assertEqual(woken_goal["wakeCount"], 0)
        self.assertEqual(woken_goal["chatId"], "chat-wake")
        self.assertTrue(woken_goal["wakeAt"])
        self.assertEqual(woken.json()["resumePrompt"], "Resume goal: Wake route goal\nContext: Route proof")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["delivery"]["deliveryId"], woken.json()["delivery"]["deliveryId"])
        self.assertEqual(paused.status_code, 200)
        paused_goal = paused.json()["goal"]
        # A status-only update sends no wake keys; presence-based semantics keep the schedule.
        self.assertEqual(paused_goal["wakeAt"], woken_goal["wakeAt"])
        self.assertEqual(paused_goal["wakeEveryMinutes"], 60)
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.json()["goal"]["wakeAt"], "")
        self.assertEqual(cleared.json()["goal"]["wakeEveryMinutes"], 0)
        self.assertEqual(overflow.status_code, 400)
        self.assertIn("ISO-8601", overflow.json()["detail"])

    def test_agent_goal_delivery_runtime_is_cached_until_chat_ack(self) -> None:
        with TestClient(dashboard_server.app) as client:
            past = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
            created = client.post(
                "/api/app/agent/goals",
                json={"title": "Durable runtime", "chatId": "chat-durable", "wakeAt": past},
            )
            goal_id = created.json()["goal"]["goalId"]
            woken = client.post(f"/api/app/agent/goals/{goal_id}/wake", json={"chatId": "chat-durable"})
            delivery = woken.json()["delivery"]
            runtime_payload = {
                "ok": True,
                "sessionId": "session-durable",
                "session_id": "session-durable",
                "turnId": "turn-durable",
                "turn_id": "turn-durable",
                "clientTurnId": delivery["clientTurnId"],
                "observe": {},
                "plan": {"summary": "done", "planner": "test", "shellNeeded": False, "skillNeeded": False},
                "contextCompaction": {
                    "schema": "vrcforge.runtime_context_compaction.v1",
                    "applied": True,
                    "summary": "transient replacement text",
                    "summaryDigest": "abc123",
                },
            }
            request = {
                "message": delivery["resumePrompt"],
                "clientTurnId": delivery["clientTurnId"],
                "goalDeliveryId": delivery["deliveryId"],
            }
            runtime = Mock(return_value=runtime_payload)
            runtime_port = dashboard_server.RuntimeExecutionPort(
                execute=runtime,
                request_cancel=dashboard_server.AGENT_GATEWAY.request_runtime_cancel,
            )
            with patch.object(dashboard_server.BACKGROUND_GOAL_COORDINATOR, "_runtime", runtime_port):
                first = client.post("/api/app/agent/message", json=request)
                second = client.post("/api/app/agent/message", json=request)

            recoverable = client.get("/api/app/agent/goals/deliveries/recoverable")
            acknowledged = client.post(
                f"/api/app/agent/goals/deliveries/{delivery['deliveryId']}/materialized",
                json={"chatId": "chat-durable"},
            )
            after_ack = client.get("/api/app/agent/goals/deliveries/recoverable")
            projected = client.get("/api/app/agent/goals").json()["goals"]

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(runtime.call_count, 1)
        self.assertEqual(first.json()["contextCompaction"]["summary"], "transient replacement text")
        self.assertEqual(second.json()["turnId"], "turn-durable")
        self.assertNotIn("contextCompaction", second.json())
        self.assertEqual(recoverable.json()["count"], 1)
        self.assertEqual(recoverable.json()["deliveries"][0]["response"]["turnId"], "turn-durable")
        self.assertNotIn("contextCompaction", recoverable.json()["deliveries"][0]["response"])
        self.assertEqual(acknowledged.status_code, 200)
        self.assertEqual(after_ack.json()["count"], 0)
        goal = next(item for item in projected if item["goalId"] == goal_id)
        self.assertEqual(goal["wakeAt"], "")
        self.assertEqual(goal["wakeCount"], 1)

    def test_background_goal_pending_approval_is_denied_not_green_and_acknowledged(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            for directory in ("Assets", "Packages", "ProjectSettings"):
                (workspace_path / directory).mkdir()
            target = workspace_path / "Assets" / "background-goal.txt"
            past = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
            with TestClient(dashboard_server.app) as client:
                created = client.post(
                    "/api/app/agent/goals",
                    json={"title": "Guarded write", "chatId": "chat-background-denied", "wakeAt": past},
                )
                goal_id = created.json()["goal"]["goalId"]
                woken = client.post(
                    f"/api/app/agent/goals/{goal_id}/wake",
                    json={"chatId": "chat-background-denied"},
                )
                delivery = woken.json()["delivery"]
                runtime = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": delivery["resumePrompt"],
                        "clientTurnId": delivery["clientTurnId"],
                        "goalDeliveryId": delivery["deliveryId"],
                        "shell_command": "Set-Content -Path Assets/background-goal.txt -Value guarded -Encoding utf8",
                        "workspace_root": workspace,
                        "cwd": workspace,
                    },
                )

                self.assertEqual(runtime.status_code, 200)
                approval_id = runtime.json()["shell"]["approval_id"]
                before_reject = client.get("/api/app/agent/goals/background").json()
                blocked = next(item for item in before_reject["recent"] if item["deliveryId"] == delivery["deliveryId"])
                self.assertEqual(blocked["status"], "blocked")
                self.assertEqual(blocked["blockedKind"], "approval")
                self.assertEqual(blocked["approvalId"], approval_id)
                self.assertNotEqual(blocked["status"], "completed")
                run_sidecar = (
                    dashboard_server.AGENT_GATEWAY.audit_dir
                    / "agent-goal-runs"
                    / f"{delivery['deliveryId']}.json"
                ).read_text(encoding="utf-8")
                self.assertNotIn(workspace_path.name, run_sidecar)

                rejected = client.post(f"/api/app/agent/approvals/{approval_id}/reject")
                denied_state = client.get("/api/app/agent/goals/background").json()
                acknowledged = client.post(
                    "/api/app/agent/goals/background/ack",
                    json={"chatId": "chat-background-denied", "deliveryIds": [delivery["deliveryId"]]},
                )
                projected = client.get("/api/app/agent/goals").json()["goals"]

        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.json()["goalDelivery"]["delivery"]["status"], "denied")
        denied = next(item for item in denied_state["recent"] if item["deliveryId"] == delivery["deliveryId"])
        self.assertEqual(denied["status"], "denied")
        self.assertTrue(denied["noticeUnread"])
        self.assertEqual(denied_state["unreadByChat"]["chat-background-denied"], 1)
        self.assertEqual(acknowledged.status_code, 200)
        self.assertEqual(acknowledged.json()["totalUnread"], 0)
        goal = next(item for item in projected if item["goalId"] == goal_id)
        self.assertEqual(goal["wakeAt"], "")
        self.assertEqual(goal["wakeCount"], 1)
        self.assertFalse(target.exists())

    def test_sub_agent_routes_persist_parent_handoff_and_merge_revision(self) -> None:
        original_service = dashboard_server._SUB_AGENT_COLLABORATION
        with tempfile.TemporaryDirectory() as tmp:
            registry = SubAgentTaskRegistry(
                Path(tmp),
                roles=[SubAgentRole("test_role", "Test", "Test role")],
                handlers={"test_role": lambda _payload, _cancel: {"ok": True, "summaryText": "ready"}},
            )
            dashboard_server._SUB_AGENT_COLLABORATION = SubAgentCollaborationService.from_registry_for_testing(registry)
            try:
                with TestClient(dashboard_server.app) as client:
                    ownerless = client.post(
                        "/api/app/sub-agents",
                        json={"role": "test_role", "task": "run"},
                    )
                    created = client.post(
                        "/api/app/sub-agents",
                        json={"role": "test_role", "task": "run", "parentChatId": "chat-a"},
                    )
                    task_id = created.json()["task"]["id"]
                    deadline = time.time() + 2
                    task = created.json()["task"]
                    while task["status"] not in {"completed", "failed"} and time.time() < deadline:
                        time.sleep(0.01)
                        task = client.get(f"/api/app/sub-agents/{task_id}").json()["task"]
                    acknowledged = client.post(
                        f"/api/app/sub-agents/{task_id}/handoff-ack",
                        json={"expectedRevision": task["revision"]},
                    )
                    acknowledged_task = acknowledged.json()["task"]
                    wrong_chat = client.post(
                        f"/api/app/sub-agents/{task_id}/merge",
                        json={
                            "decision": "adopted",
                            "chatId": "chat-b",
                            "expectedRevision": acknowledged_task["revision"],
                        },
                    )
                    adopted = client.post(
                        f"/api/app/sub-agents/{task_id}/merge",
                        json={
                            "decision": "adopted",
                            "chatId": "chat-a",
                            "expectedRevision": acknowledged_task["revision"],
                        },
                    )
            finally:
                dashboard_server._SUB_AGENT_COLLABORATION = original_service

        self.assertEqual(ownerless.status_code, 400)
        self.assertEqual(task["parentChatId"], "chat-a")
        self.assertEqual(task["handoffStatus"], "handoff_pending")
        self.assertEqual(acknowledged.status_code, 200)
        self.assertEqual(acknowledged_task["handoffStatus"], "materialized")
        self.assertEqual(wrong_chat.status_code, 409)
        self.assertEqual(adopted.status_code, 200)
        self.assertEqual(adopted.json()["task"]["mergeDecision"], "adopted")

    def test_agent_progress_replace_update_and_delete(self) -> None:
        with TestClient(dashboard_server.app) as client:
            replaced = client.post(
                "/api/app/agent/progress/replace",
                json={
                    "sessionId": "sess-progress",
                    "projectRoot": "ProjectA",
                    "items": [
                        {"id": "step-a", "title": "Read requirements", "status": "completed"},
                        {"id": "step-b", "title": "Run app proof", "status": "in_progress"},
                    ],
                },
            )
            updated = client.post(
                "/api/app/agent/progress/step-b",
                json={"status": "completed", "summary": "Actual app proof captured", "sessionId": "sess-progress", "projectRoot": "ProjectA"},
            )
            deleted = client.request("DELETE", "/api/app/agent/progress/step-a", json={"sessionId": "sess-progress", "projectRoot": "ProjectA"})
            listing = client.get("/api/app/agent/progress", params={"sessionId": "sess-progress", "projectRoot": "ProjectA"})

        self.assertEqual(replaced.status_code, 200)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(deleted.status_code, 200)
        items = listing.json()["items"]
        self.assertEqual([item["progressId"] for item in items], ["step-b"])
        self.assertEqual(items[0]["status"], "completed")

    def test_agent_progress_and_questions_are_strictly_scoped(self) -> None:
        with TestClient(dashboard_server.app) as client:
            client.post(
                "/api/app/agent/progress/replace",
                json={"sessionId": "scope-a", "projectRoot": "ProjectA", "items": [{"id": "step-1", "title": "A"}]},
            )
            client.post(
                "/api/app/agent/progress/replace",
                json={"sessionId": "scope-b", "projectRoot": "ProjectB", "items": [{"id": "step-1", "title": "B"}]},
            )
            dashboard_server.AGENT_GATEWAY.create_agent_progress({"title": "legacy unscoped"})
            dashboard_server.AGENT_QUESTIONS.create(
                {"question": "Unscoped?", "options": ["A", "B"]}
            )
            progress_a = client.get("/api/app/agent/progress", params={"sessionId": "scope-a", "projectRoot": "ProjectA"})
            progress_b = client.get("/api/app/agent/progress", params={"sessionId": "scope-b", "projectRoot": "ProjectB"})
            questions_a = client.get("/api/app/agent/questions", params={"sessionId": "scope-a", "projectRoot": "ProjectA"})

        self.assertEqual([item["title"] for item in progress_a.json()["items"]], ["A"])
        self.assertEqual([item["title"] for item in progress_b.json()["items"]], ["B"])
        self.assertEqual(questions_a.json()["questions"], [])

    def test_agent_questions_can_be_answered_and_snapshot(self) -> None:
        with TestClient(dashboard_server.app) as client:
            created = client.post(
                "/api/app/agent/questions",
                json={
                    "header": "Accept?",
                    "question": "Which proof should run?",
                    "options": [
                        {"id": "actual", "label": "Actual app", "value": "Run actual app proof"},
                        {"id": "browser", "label": "Browser precheck", "value": "Run browser precheck"},
                    ],
                    "sessionId": "sess-question",
                    "projectRoot": "ProjectA",
                },
            )
            question_id = created.json()["question"]["questionId"]
            before_answer = client.get("/api/app/runtime/snapshot", params={"sessionId": "sess-question", "projectRoot": "ProjectA"})
            answered = client.post(
                f"/api/app/agent/questions/{question_id}/answer",
                json={"selectedOptionId": "actual", "answer": "Run actual app proof", "sessionId": "sess-question", "projectRoot": "ProjectA"},
            )
            after_answer = client.get("/api/app/agent/questions", params={"sessionId": "sess-question", "projectRoot": "ProjectA"})

        self.assertEqual(created.status_code, 200)
        self.assertEqual(before_answer.json()["questions"]["count"], 1)
        self.assertEqual(answered.status_code, 200)
        self.assertEqual(answered.json()["question"]["selectedOptionId"], "actual")
        self.assertEqual(after_answer.json()["count"], 0)

    def test_answered_goal_question_recovers_after_interrupted_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config.json", root / "audit")
            past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            goal = gateway.goal.create_agent_goal(
                {"title": "Resume after answer", "chatId": "chat-question", "wakeAt": past}
            )["goal"]
            woken = gateway.goal.wake_agent_goal(goal["goalId"])
            delivery_id = woken["delivery"]["deliveryId"]
            gateway.goal.begin_agent_goal_delivery(
                delivery_id,
                {"clientTurnId": woken["delivery"]["clientTurnId"]},
            )
            questions = isolated_agent_question_service(gateway)
            question = questions.create(
                {
                    "question": "Which bounded option should continue?",
                    "options": ["First", "Second"],
                    "goalDeliveryId": delivery_id,
                }
            )["question"]
            gateway.goal.block_agent_goal_delivery(
                delivery_id,
                kind="question",
                reference=question["questionId"],
                response={"questionId": question["questionId"]},
            )
            marker = "sk-" + "1145141919810"
            local_path = "C:\\Users\\PrivateName\\secret.txt"

            def fail_goal_resolution(_question_id: str, _continuation_prompt: str):
                raise OSError("interrupted")

            interrupted_questions = isolated_agent_question_service(
                gateway,
                resolve_question=fail_goal_resolution,
            )
            with self.assertRaises(OSError):
                interrupted_questions.answer(
                    question["questionId"],
                    {"answer": f"Use {marker} from {local_path}"},
                )

            reopened = AgentGateway(root / "config.json", root / "audit")
            resumed = isolated_agent_question_service(reopened).answer(question["questionId"], {})
            self.assertTrue(resumed["idempotent"])
            self.assertEqual(resumed["goalDelivery"]["delivery"]["status"], "interrupted")
            durable_text = (root / "audit").read_text(encoding="utf-8") if (root / "audit").is_file() else ""
            durable_text += "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in (root / "audit").rglob("*")
                if path.is_file()
            )
            self.assertNotIn(marker, durable_text)
            self.assertNotIn("PrivateName", durable_text)

    def test_agent_questions_require_at_least_two_options(self) -> None:
        with TestClient(dashboard_server.app) as client:
            created = client.post(
                "/api/app/agent/questions",
                json={
                    "question": "Pick a path",
                    "options": [{"id": "only", "label": "Only one"}],
                    "sessionId": "sess-question-min",
                },
            )

        self.assertEqual(created.status_code, 400)
        self.assertIn("at least two options", created.json()["detail"])

    def test_agent_questions_keep_more_than_three_options(self) -> None:
        with TestClient(dashboard_server.app) as client:
            created = client.post(
                "/api/app/agent/questions",
                json={
                    "question": "Pick a path",
                    "options": [
                        {"id": "a", "label": "Recommended"},
                        {"id": "b", "label": "Conservative"},
                        {"id": "c", "label": "Broad"},
                        {"id": "d", "label": "Split work"},
                    ],
                    "sessionId": "sess-question-many",
                },
            )

        self.assertEqual(created.status_code, 200)
        self.assertEqual([option["id"] for option in created.json()["question"]["options"]], ["a", "b", "c", "d"])

    def test_agent_progress_and_question_tools_are_callable_but_computer_use_needs_app_turn(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            progress = client.post(
                "/api/agent/tool/vrcforge_progress_replace",
                headers=headers,
                json={
                    "agentName": "pytest-agent",
                    "params": {
                        "sessionId": "sess-tool-progress",
                        "projectRoot": "ProjectA",
                        "items": [{"id": "tool-step", "title": "Tool-visible progress", "status": "in_progress"}],
                    },
                },
            )
            question = client.post(
                "/api/agent/tool/vrcforge_ask_user",
                headers=headers,
                json={
                    "agentName": "pytest-agent",
                    "params": {
                        "sessionId": "sess-tool-progress",
                        "projectRoot": "ProjectA",
                        "question": "Pick a proof path",
                        "options": [
                            {"id": "actual", "label": "Actual app"},
                            {"id": "browser", "label": "Browser precheck"},
                        ],
                    },
                },
            )
            desktop_action = client.post(
                "/api/agent/tool/vrcforge_agent_desktop_action",
                headers=headers,
                json={
                    "agentName": "pytest-agent",
                    "params": {
                        "sessionId": "sess-tool-progress",
                        "projectRoot": "ProjectA",
                        "action": "computer_use",
                        "prompt": "Observe current desktop state only.",
                    },
                },
            )
            snapshot = client.get("/api/app/runtime/snapshot", params={"sessionId": "sess-tool-progress", "projectRoot": "ProjectA"})

        self.assertEqual(progress.status_code, 200)
        self.assertEqual(question.status_code, 200)
        self.assertEqual(desktop_action.status_code, 200)
        self.assertFalse(desktop_action.json()["ok"])
        self.assertIn("user-started", desktop_action.json()["error"])
        self.assertEqual(snapshot.json()["progress"]["items"][0]["progressId"], "tool-step")
        self.assertEqual(snapshot.json()["questions"]["questions"][0]["question"], "Pick a proof path")

    def test_agent_memory_can_be_inspected_deleted_and_cleared(self) -> None:
        with TestClient(dashboard_server.app) as client:
            first = client.post(
                "/api/app/agent/memory",
                json={"scope": "project", "kind": "style", "text": "Prefer soft lilToon outlines.", "projectRoot": "ProjectA"},
            )
            second = client.post(
                "/api/app/agent/memory",
                json={"scope": "user", "kind": "preference", "text": "Show approval summaries inline."},
            )
            memory_id = first.json()["memory"]["memoryId"]
            listed = client.get("/api/app/agent/memory", params={"projectRoot": "ProjectA"})
            deleted = client.request("DELETE", f"/api/app/agent/memory/{memory_id}", json={"reason": "test"})
            after_delete = client.get("/api/app/agent/memory", params={"projectRoot": "ProjectA"})
            cleared = client.post("/api/app/agent/memory/clear", json={"scope": "user", "reason": "test"})
            after_clear = client.get("/api/app/agent/memory")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(listed.json()["count"], 2)
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(all(item["memoryId"] != memory_id for item in after_delete.json()["memories"]))
        self.assertEqual(cleared.status_code, 200)
        self.assertGreaterEqual(cleared.json()["cleared"], 1)
        self.assertEqual(after_clear.json()["count"], 0)

    def test_agent_project_memory_requires_project_root(self) -> None:
        with TestClient(dashboard_server.app) as client:
            missing_project = client.post(
                "/api/app/agent/memory",
                json={"scope": "project", "kind": "style", "text": "Project-specific preference."},
            )
            user_memory = client.post(
                "/api/app/agent/memory",
                json={"scope": "user", "kind": "preference", "text": "User-wide preference."},
            )

        self.assertEqual(missing_project.status_code, 400)
        self.assertIn("projectRoot", missing_project.json()["detail"])
        self.assertEqual(user_memory.status_code, 200)
        self.assertEqual(user_memory.json()["memory"]["scope"], "user")

    def test_agent_memory_clear_requires_valid_scope_and_project_root(self) -> None:
        with TestClient(dashboard_server.app) as client:
            missing_scope = client.post("/api/app/agent/memory/clear", json={"reason": "test"})
            invalid_scope = client.post("/api/app/agent/memory/clear", json={"scope": "all", "reason": "test"})
            missing_project = client.post("/api/app/agent/memory/clear", json={"scope": "project", "reason": "test"})

        self.assertEqual(missing_scope.status_code, 422)
        self.assertEqual(invalid_scope.status_code, 422)
        self.assertEqual(missing_project.status_code, 400)
        self.assertIn("projectRoot", missing_project.json()["detail"])

    def test_agent_project_memory_clear_is_exactly_project_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            project_a = str(root / "ProjectA")
            project_b = str(root / "ProjectB")
            gateway.create_agent_memory({"scope": "project", "text": "Project A memory.", "projectRoot": project_a})
            gateway.create_agent_memory({"scope": "project", "text": "Project B memory.", "projectRoot": project_b})
            gateway.create_agent_memory({"scope": "user", "text": "User memory."})

            cleared = gateway.clear_agent_memory({"scope": "project", "projectRoot": project_a})
            project_a_after = gateway.list_agent_memory(project_root=project_a, limit=10)
            project_b_after = gateway.list_agent_memory(project_root=project_b, limit=10)

        self.assertEqual(cleared["cleared"], 1)
        self.assertEqual({item["text"] for item in project_a_after["memories"]}, {"User memory."})
        self.assertEqual(
            {item["text"] for item in project_b_after["memories"]},
            {"Project B memory.", "User memory."},
        )

    def test_agent_memory_no_project_list_only_returns_user_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            gateway.create_agent_memory(
                {"scope": "project", "kind": "note", "text": "Project A private memory.", "projectRoot": str(root / "ProjectA")}
            )
            gateway.create_agent_memory(
                {"scope": "project", "kind": "note", "text": "Project B private memory.", "projectRoot": str(root / "ProjectB")}
            )
            gateway.create_agent_memory({"scope": "user", "kind": "preference", "text": "User-wide memory."})

            no_project = gateway.list_agent_memory(limit=10)
            project_a = gateway.list_agent_memory(limit=10, project_root=str(root / "ProjectA"))

        self.assertEqual([item["text"] for item in no_project["memories"]], ["User-wide memory."])
        self.assertEqual(
            {item["text"] for item in project_a["memories"]},
            {"Project A private memory.", "User-wide memory."},
        )

    def test_agent_project_filters_normalize_windows_style_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            project = root / "AvatarProject"
            project.mkdir()
            stored_project = str(project).replace("/", "\\")
            query_project = str(project).replace("\\", "/")
            gateway.record_runtime_queue_event({"clientTurnId": "turn-path", "message": "queued", "projectRoot": stored_project})
            gateway.request_desktop_action({"action": "computer_use", "prompt": "inspect", "projectRoot": stored_project})
            gateway.goal.create_agent_goal({"title": "Path scoped goal", "projectRoot": stored_project})
            gateway.create_agent_memory({"scope": "project", "text": "Path scoped memory", "projectRoot": stored_project})

            runs = gateway.list_runtime_runs(project_root=query_project, limit=10)
            actions = gateway.list_desktop_actions(project_root=query_project, limit=10)
            goals = gateway.goal.list_agent_goals(project_root=query_project, limit=10)
            memories = gateway.list_agent_memory(project_root=query_project, limit=10)

        self.assertEqual(runs["count"], 1)
        self.assertEqual(actions["count"], 1)
        self.assertEqual(goals["count"], 1)
        self.assertEqual({item["text"] for item in memories["memories"]}, {"Path scoped memory"})

    def test_agent_memory_active_state_keeps_entries_before_last_4000_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            first_id = gateway.create_agent_memory({"scope": "user", "text": "Oldest active memory."})["memory"]["memoryId"]
            for index in range(4001):
                gateway.create_agent_memory({"scope": "user", "text": f"Active memory {index}"})

            active_memory_ids = set(gateway._project_agent_memory())  # noqa: SLF001

        self.assertIn(first_id, active_memory_ids)

    def test_agent_approval_list_filters_by_normalized_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            project_a = root / "ProjectA"
            project_b = root / "ProjectB"
            project_a.mkdir()
            project_b.mkdir()
            gateway.register_write_handler("tool_a", "Tool A", "medium", lambda _args: {"ok": True})
            gateway.register_write_handler("tool_b", "Tool B", "medium", lambda _args: {"ok": True})
            approval_a = gateway.create_apply_request(
                {"target_tool": "tool_a", "arguments": {"projectRoot": str(project_a).replace("/", "\\")}, "reason": "A"}
            )
            gateway.create_apply_request(
                {"target_tool": "tool_b", "arguments": {"projectRoot": str(project_b)}, "reason": "B"}
            )

            filtered = gateway.list_approvals(include_expired=False, project_root=str(project_a).replace("\\", "/"))

        self.assertEqual([item["id"] for item in filtered], [approval_a["approval"]["id"]])

    def test_runtime_snapshot_keeps_app_wide_approval_inbox_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            project_a = root / "ProjectA"
            project_b = root / "ProjectB"
            gateway.register_write_handler("tool_a", "Tool A", "medium", lambda _args: {"ok": True})
            gateway.register_write_handler("tool_b", "Tool B", "medium", lambda _args: {"ok": True})
            approval_a = gateway.create_apply_request(
                {"target_tool": "tool_a", "arguments": {"projectRoot": str(project_a)}, "reason": "A"}
            )["approval"]
            approval_b = gateway.create_apply_request(
                {"target_tool": "tool_b", "arguments": {"projectRoot": str(project_b)}, "reason": "B"}
            )["approval"]
            original_gateway = dashboard_server.AGENT_GATEWAY
            dashboard_server.AGENT_GATEWAY = gateway
            try:
                with patch("dashboard_server.build_workspace_diff_summary", return_value={"ok": True}):
                    snapshot = dashboard_server.read_app_runtime_snapshot(projectRoot=str(project_a))
                with (
                    patch("dashboard_server.build_bootstrap_app_health", return_value={}),
                    patch("dashboard_server.safe_agent_manifest", return_value={}),
                    patch("dashboard_server.safe_agent_health", return_value={}),
                    patch("dashboard_server.safe_permission_state", return_value={}),
                    patch.object(gateway, "advanced_settings_state", return_value={}),
                ):
                    bootstrap = dashboard_server.build_agentic_app_bootstrap_payload()
            finally:
                dashboard_server.AGENT_GATEWAY = original_gateway

        snapshot_approvals = snapshot["approvals"]
        self.assertEqual(snapshot_approvals["count"], 2)
        self.assertEqual(
            {item["id"] for item in snapshot_approvals["approvals"]},
            {approval_a["id"], approval_b["id"]},
        )
        self.assertEqual(
            {item["projectRoot"] for item in snapshot_approvals["approvals"]},
            {str(project_a), str(project_b)},
        )
        self.assertEqual(
            {item["id"] for item in bootstrap["approvals"]},
            {approval_a["id"], approval_b["id"]},
        )

    def test_external_mcp_pending_callback_broadcasts_approval_refresh(self) -> None:
        async def exercise() -> AsyncMock:
            broadcast = AsyncMock()
            with patch.object(dashboard_server.EVENT_BUS, "broadcast", broadcast):
                dashboard_server._notify_mcp_pending_approval({"id": "approval-id"})
                await asyncio.sleep(0)
            return broadcast

        broadcast = asyncio.run(exercise())

        broadcast.assert_awaited_once()
        event_name, payload = broadcast.await_args.args
        self.assertEqual(event_name, "agentApprovals")
        self.assertIn("approvals", payload)

    def test_agent_approval_scope_checks_project_aliases_on_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            project_a = root / "ProjectA"
            project_b = root / "ProjectB"
            project_a.mkdir()
            project_b.mkdir()
            gateway.register_write_handler("tool_a", "Tool A", "medium", lambda _args: {"ok": True})
            gateway.register_write_handler("tool_b", "Tool B", "medium", lambda _args: {"ok": True})
            approval_a = gateway.create_apply_request(
                {"target_tool": "tool_a", "arguments": {"projectPath": str(project_a)}, "reason": "A"}
            )["approval"]
            approval_b = gateway.create_apply_request(
                {"target_tool": "tool_b", "arguments": {"project_path": str(project_b)}, "reason": "B"}
            )["approval"]

            project_a_approvals = gateway.list_approvals(include_expired=False, project_root=str(project_a))
            global_approvals = gateway.list_approvals(include_expired=False, global_only=True)

            with self.assertRaises(AgentGatewayError):
                gateway.approve(approval_b["id"], expected_project_root=str(project_a))
            with self.assertRaises(AgentGatewayError):
                gateway.reject(approval_a["id"], global_only=True)

            approved = gateway.approve(approval_a["id"], expected_project_root=str(project_a))

        self.assertEqual([item["id"] for item in project_a_approvals], [approval_a["id"]])
        self.assertEqual(global_approvals, [])
        self.assertTrue(approved["ok"])

    def test_agent_runtime_observe_filters_goals_by_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            project_a = root / "ProjectA"
            project_b = root / "ProjectB"
            project_a.mkdir()
            project_b.mkdir()
            gateway.goal.create_agent_goal({"title": "Goal A", "projectRoot": str(project_a)})
            gateway.goal.create_agent_goal({"title": "Goal B private", "projectRoot": str(project_b)})

            observe = gateway.runtime_observe(project_root=str(project_a))
            titles = {item["title"] for item in observe["goals"]["items"]}

        self.assertEqual(titles, {"Goal A"})

    def test_agent_runtime_context_usage_is_turn_local_for_concurrent_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")

            def fake_llm(prompt: str) -> dict[str, object]:
                if "alpha request" in prompt:
                    time.sleep(0.05)
                    tokens = 111
                    reply = "alpha done"
                else:
                    tokens = 222
                    reply = "beta done"
                return {
                    "text": json.dumps({"action": "reply", "summary": reply, "reply": reply}),
                    "usage": {
                        "exact": True,
                        "inputTokens": tokens,
                        "outputTokens": 1,
                        "totalTokens": tokens + 1,
                        "provider": "test",
                        "model": "model",
                    },
                }

            bind_test_runtime_planner(gateway, fake_llm)
            results: dict[str, dict[str, object]] = {}

            def run_turn(name: str, message: str) -> None:
                results[name] = gateway.runtime_message({"message": message, "sessionId": f"sess-{name}"})

            threads = [
                threading.Thread(target=run_turn, args=("alpha", "alpha request")),
                threading.Thread(target=run_turn, args=("beta", "beta request")),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(results["alpha"]["contextUsage"]["inputTokens"], 111)
        self.assertEqual(results["beta"]["contextUsage"]["inputTokens"], 222)

    def test_agent_runtime_compacts_once_at_safe_mid_turn_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            gateway.register_tool(
                "vrcforge_test_read",
                "Read a bounded test value.",
                "read/debug",
                lambda _params: {"ok": True, "status": "ready"},
            )
            prompts: list[str] = []

            def fake_llm(prompt: str) -> dict[str, object]:
                prompts.append(prompt)
                if len(prompts) == 1:
                    payload = {
                        "action": "skill",
                        "skill_tool": "vrcforge_test_read",
                        "skill_params": {},
                        "summary": "read once",
                    }
                    input_tokens = 18_000
                else:
                    payload = {"action": "reply", "reply": "done after compacting"}
                    input_tokens = 8_000
                return {
                    "text": json.dumps(payload),
                    "usage": {
                        "exact": True,
                        "inputTokens": input_tokens,
                        "outputTokens": 10,
                        "totalTokens": input_tokens + 10,
                    },
                }

            compact_calls: list[tuple[list[dict[str, object]], dict[str, object]]] = []

            def compact_fn(history: list[dict[str, object]], metadata: dict[str, object]) -> dict[str, object]:
                compact_calls.append((history, metadata))
                return {
                    "ok": True,
                    "summary": "VRCForge successor summary",
                    "entryCount": len(history),
                    "retainedEntryCount": 2,
                    "sourceDigest": "source-digest",
                    "summaryDigest": "summary-digest",
                    "fidelity": "fitted",
                    "providerAttempts": 1,
                }

            bind_test_runtime_planner(gateway, fake_llm, compact=compact_fn)
            history = [
                {"role": "user" if index % 2 == 0 else "agent", "text": f"OLD_HISTORY_{index}_" + ("x" * 1200)}
                for index in range(24)
            ]
            response = gateway.runtime_message(
                {
                    "message": "continue safely",
                    "sessionId": "sess-mid-turn-compaction",
                    "history": history,
                    "_contextCompactionLimit": 20_000,
                }
            )
            audit_text = gateway.audit_log_path.read_text(encoding="utf-8")
            runtime_run_text = gateway.runtime_run_log_path.read_text(encoding="utf-8")
            runtime_runs = gateway.list_runtime_runs(session_id="sess-mid-turn-compaction")

        self.assertEqual(len(compact_calls), 1)
        self.assertEqual(compact_calls[0][1]["phase"], "mid_turn")
        self.assertEqual(len(prompts), 2)
        self.assertIn("OLD_HISTORY_0_", prompts[0])
        self.assertNotIn("OLD_HISTORY_0_", prompts[1])
        self.assertIn("VRCForge successor summary", prompts[1])
        self.assertTrue(response["contextCompaction"]["applied"])
        self.assertEqual(response["contextCompaction"]["phase"], "mid_turn")
        self.assertEqual(response["contextCompaction"]["attempts"], 1)
        self.assertGreaterEqual(response["contextCompaction"]["latencyMs"], 0)
        self.assertEqual(
            response["contextCompaction"]["retainedSummaryCharacters"],
            len("VRCForge successor summary"),
        )
        self.assertEqual(response["contextUsage"]["preCompactionPeakInputTokens"], 18_000)
        self.assertEqual(response["contextUsage"]["peakInputTokens"], 8_000)
        self.assertNotIn("VRCForge successor summary", audit_text)
        self.assertIn("summary-digest", audit_text)
        completed_run = next(run for run in runtime_runs["runs"] if run.get("status") == "completed")
        self.assertTrue(completed_run["contextCompaction"]["applied"])
        self.assertEqual(completed_run["contextCompaction"]["phase"], "mid_turn")
        self.assertEqual(completed_run["contextCompaction"]["summaryDigest"], "summary-digest")
        self.assertNotIn("summary", completed_run["contextCompaction"])
        self.assertNotIn("VRCForge successor summary", runtime_run_text)

    def test_agent_runtime_stops_at_hard_limit_when_mid_turn_compaction_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            gateway.register_tool(
                "vrcforge_test_read",
                "Read a bounded test value.",
                "read/debug",
                lambda _params: {"ok": True},
            )
            planner_calls = 0

            def fake_llm(_prompt: str) -> dict[str, object]:
                nonlocal planner_calls
                planner_calls += 1
                return {
                    "text": json.dumps(
                        {
                            "action": "skill",
                            "skill_tool": "vrcforge_test_read",
                            "skill_params": {},
                            "summary": "read once",
                        }
                    ),
                    "usage": {
                        "exact": True,
                        "inputTokens": 19_500,
                        "outputTokens": 10,
                        "totalTokens": 19_510,
                    },
                }

            bind_test_runtime_planner(
                gateway,
                fake_llm,
                compact=lambda _history, _metadata: (_ for _ in ()).throw(
                    TimeoutError("compactor unavailable")
                ),
            )
            response = gateway.runtime_message(
                {
                    "message": "continue safely",
                    "sessionId": "sess-mid-turn-hard-stop",
                    "history": [{"role": "user", "text": "original conversation remains"}],
                    "_contextCompactionLimit": 20_000,
                }
            )

        self.assertEqual(planner_calls, 1)
        self.assertFalse(response["contextCompaction"]["applied"])
        self.assertTrue(response["contextCompaction"]["blocked"])
        self.assertEqual(response["contextCompaction"]["attempts"], 1)
        self.assertGreaterEqual(response["contextCompaction"]["latencyMs"], 0)
        self.assertEqual(response["plan"]["nextStep"], "context_compaction_required")

    def test_agent_runtime_rechecks_hard_limit_after_suppressed_mid_turn_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            gateway.register_tool(
                "vrcforge_test_read_alpha",
                "Read the first bounded test value.",
                "read/debug",
                lambda _params: {"ok": True, "status": "alpha"},
            )
            gateway.register_tool(
                "vrcforge_test_read_beta",
                "Read the second bounded test value.",
                "read/debug",
                lambda _params: {"ok": True, "status": "beta"},
            )
            planner_calls = 0
            compactor_calls = 0

            def fake_llm(_prompt: str) -> dict[str, object]:
                nonlocal planner_calls
                planner_calls += 1
                tool = "vrcforge_test_read_alpha" if planner_calls == 1 else "vrcforge_test_read_beta"
                return {
                    "text": json.dumps(
                        {
                            "action": "skill",
                            "skill_tool": tool,
                            "skill_params": {},
                            "summary": f"read {planner_calls}",
                        }
                    ),
                    "usage": {
                        "exact": True,
                        "inputTokens": 18_000 if planner_calls == 1 else 19_500,
                        "outputTokens": 10,
                        "totalTokens": 18_010 if planner_calls == 1 else 19_510,
                    },
                }

            def failing_compactor(_history: list[dict[str, object]], _metadata: dict[str, object]) -> dict[str, object]:
                nonlocal compactor_calls
                compactor_calls += 1
                raise TimeoutError("compactor unavailable")

            bind_test_runtime_planner(gateway, fake_llm, compact=failing_compactor)
            response = gateway.runtime_message(
                {
                    "message": "continue safely",
                    "sessionId": "sess-mid-turn-recheck",
                    "history": [{"role": "user", "text": "original conversation remains"}],
                    "_contextCompactionLimit": 20_000,
                }
            )

        self.assertEqual(compactor_calls, 1)
        self.assertEqual(planner_calls, 2)
        self.assertFalse(response["contextCompaction"]["applied"])
        self.assertTrue(response["contextCompaction"]["blocked"])
        self.assertEqual(response["contextCompaction"]["failureClass"], "suppressed_after_attempt")
        self.assertEqual(response["contextCompaction"]["suppressionReason"], "suppressed_after_attempt")
        self.assertEqual(response["contextCompaction"]["attempts"], 0)
        self.assertEqual(response["plan"]["nextStep"], "context_compaction_required")

    def test_agent_runtime_cancel_during_compaction_keeps_original_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config.json", root / "audit")
            gateway.register_tool(
                "vrcforge_test_read",
                "Read a bounded test value.",
                "read/debug",
                lambda _params: {"ok": True, "status": "ready"},
            )
            planner_calls = 0

            def fake_llm(_prompt: str) -> dict[str, object]:
                nonlocal planner_calls
                planner_calls += 1
                return {
                    "text": json.dumps(
                        {
                            "action": "skill",
                            "skill_tool": "vrcforge_test_read",
                            "skill_params": {},
                            "summary": "read once",
                        }
                    ),
                    "usage": {
                        "exact": True,
                        "inputTokens": 18_000,
                        "outputTokens": 10,
                        "totalTokens": 18_010,
                    },
                }

            def cancelling_compactor(history: list[dict[str, object]], _metadata: dict[str, object]) -> dict[str, object]:
                gateway.request_runtime_cancel(
                    {
                        "sessionId": "sess-mid-turn-cancel",
                        "clientTurnId": "client-mid-turn-cancel",
                        "reason": "test_cancel",
                    }
                )
                return {
                    "ok": True,
                    "summary": "This summary must not replace the original history.",
                    "entryCount": len(history),
                    "retainedEntryCount": 1,
                    "summaryDigest": "cancelled-summary-digest",
                }

            bind_test_runtime_planner(gateway, fake_llm, compact=cancelling_compactor)
            response = gateway.runtime_message(
                {
                    "message": "continue safely",
                    "sessionId": "sess-mid-turn-cancel",
                    "clientTurnId": "client-mid-turn-cancel",
                    "history": [{"role": "user", "text": "original conversation remains"}],
                    "_contextCompactionLimit": 20_000,
                }
            )

        self.assertEqual(planner_calls, 1)
        self.assertFalse(response["contextCompaction"]["applied"])
        self.assertEqual(response["contextCompaction"]["failureClass"], "cancelled")
        self.assertNotIn("summary", response["contextCompaction"])
        self.assertEqual(response["contextUsage"]["peakInputTokens"], 18_000)
        self.assertEqual(response["plan"]["nextStep"], "cancelled")

    def test_agent_runtime_route_forwards_verified_context_limit_metadata(self) -> None:
        runtime_payload = {
            "ok": True,
            "session_id": "sess-context-limit",
            "sessionId": "sess-context-limit",
            "turn_id": "turn-context-limit",
            "turnId": "turn-context-limit",
            "observe": {},
            "plan": {"summary": "done", "reply": "done", "planner": "test"},
        }
        configured = ProviderApiConfig(
            provider="ollama",
            api_key="",
            base_url="http://127.0.0.1:11434",
            model="model-id",
        )
        with (
            patch.object(dashboard_server.AGENT_GATEWAY, "runtime_message", return_value=runtime_payload) as runtime_message,
            patch.object(
                dashboard_server.PROVIDER_CONFIGURATION,
                "current_api_config",
                return_value=configured,
            ),
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "continue",
                        "provider": "ollama",
                        "model": "model-id",
                        "contextLimit": 64_000,
                    },
                )

            forwarded = runtime_message.call_args.args[0]
            with dashboard_server.RUNTIME_PLANNER.bind_turn(forwarded) as matching_metadata:
                self.assertEqual(matching_metadata.verified_context_limit, 64_000)

            mismatched = dashboard_server.AgentRuntimeMessageRequest.model_validate(
                {
                    "message": "continue",
                    "provider": "ollama",
                    "model": "other-model",
                    "contextLimit": 64_000,
                }
            )
            with dashboard_server.RUNTIME_PLANNER.bind_turn(
                dashboard_server.agent_runtime_request_payload(mismatched)
            ) as mismatched_metadata:
                self.assertIsNone(mismatched_metadata.verified_context_limit)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(forwarded["_requestedContextLimit"], 64_000)
        self.assertEqual(forwarded["provider"], "ollama")
        self.assertEqual(forwarded["model"], "model-id")

    def test_agent_gateway_requires_token_and_is_disabled_by_default(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        with TestClient(dashboard_server.app) as client:
            missing_token = client.get("/api/agent/manifest")
            self.assertEqual(missing_token.status_code, 401)

            headers = {"Authorization": f"Bearer {config.token}"}
            manifest = client.get("/api/agent/manifest", headers=headers)
            self.assertEqual(manifest.status_code, 200)
            payload = manifest.json()
            self.assertFalse(payload["enabled"])
            self.assertTrue(payload["requiresToken"])
            self.assertNotIn("vrcforge_request_roslyn_advanced", {tool["name"] for tool in payload["tools"]})

            blocked_tool = client.post("/api/agent/tool/vrcforge_health", headers=headers, json={"params": {}})
            self.assertEqual(blocked_tool.status_code, 403)
            blocked_mcp = client.post("/mcp", json={})
            self.assertEqual(blocked_mcp.status_code, 401)

    def test_agentic_app_bootstrap_is_local_desktop_surface(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.get("/api/app/bootstrap")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["app"]["surface"], "tauri-agentic-desktop")
        self.assertFalse(payload["app"]["browserRequired"])
        self.assertEqual(payload["permission"]["executionMode"], "approval")
        self.assertIn("vrcforge_health", {tool["name"] for tool in payload["agentManifest"]["tools"]})
        serialized = json.dumps(payload).lower()
        self.assertNotIn("approval_token", serialized)
        self.assertNotIn("api_key", serialized)

    def test_app_doctor_report_is_read_only_and_redacted(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.token = "doctor-secret-token"
        config.approval_token = "doctor-approval-secret"
        dashboard_server.AGENT_GATEWAY.save_config(config)
        original_project_path = dashboard_server.DASHBOARD_STATE.selected_project_path
        private_project_path = r"C:\Users\TestUser\PrivateAvatarProjects\DoctorLeakTest"
        dashboard_server.DASHBOARD_STATE.selected_project_path = private_project_path

        try:
            with patch(
                "dashboard_server._selected_project_path_from_health",
                return_value=private_project_path,
            ), TestClient(dashboard_server.app) as client:
                response = client.get("/api/app/doctor")
        finally:
            dashboard_server.DASHBOARD_STATE.selected_project_path = original_project_path

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema"], "vrcforge.doctor.v1")
        self.assertEqual(payload["scope"], "vrcforge.environment.v1")
        self.assertFalse(payload["projectContentInspected"])
        self.assertNotIn("selectedProjectPath", payload)
        self.assertEqual(payload["selectedUnityEnvironment"]["label"], ".../DoctorLeakTest")
        self.assertIn("checks", payload)
        check_ids = {item["id"] for item in payload["checks"]}
        self.assertIn("desktop.runtime", check_ids)
        self.assertIn("backend.online", check_ids)
        self.assertIn("unity.project_root", check_ids)
        self.assertIn("provider.test", check_ids)
        self.assertIn("checkpoint.backend", check_ids)
        self.assertIn("session.storage", check_ids)
        self.assertIn("app.config", check_ids)
        self.assertIn("security.gateway_token_age", check_ids)
        self.assertIn("desktop.install_integrity", check_ids)
        self.assertIn("external.security_contract", check_ids)
        self.assertTrue(payload["sections"])
        provider_check = next(item for item in payload["checks"] if item["id"] == "provider.test")
        self.assertEqual(provider_check["section"], "Providers")
        self.assertIn("Settings", provider_check["fixCommand"])
        self.assertFalse(provider_check["fixable"])
        serialized = json.dumps(payload).lower()
        self.assertNotIn("doctor-secret-token", serialized)
        self.assertNotIn("doctor-approval-secret", serialized)
        self.assertNotIn(private_project_path.lower(), serialized)
        self.assertNotIn("privateavatarprojects", serialized)
        self.assertNotIn("approval_token", serialized)
        self.assertNotIn("api_key", serialized)

    def test_generic_doctor_fix_endpoint_uses_registered_service(self) -> None:
        service = Mock()
        service.fix.return_value = {
            "ok": True,
            "schema": "vrcforge.doctor_fix.v1",
            "checkId": "unity.mcp.package",
            "mode": "safe",
            "status": "healthy",
            "changed": False,
            "phases": [],
        }
        with patch("dashboard_server.app_doctor_service", return_value=service):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/doctor/fix/unity.mcp.package",
                    json={"mode": "safe", "projectPath": r"C:\Unity\SelectedAvatar"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["schema"], "vrcforge.doctor_fix.v1")
        service.fix.assert_called_once_with(
            "unity.mcp.package",
            "safe",
            {"selected_project_path": r"C:\Unity\SelectedAvatar"},
        )

    def test_generic_doctor_fix_endpoint_emits_terminal_audit_for_service_errors(self) -> None:
        service = Mock()
        service.fix.side_effect = [
            dashboard_server.DoctorServiceError(409, "approval required"),
            dashboard_server.DoctorServiceError(500, "repair failed"),
        ]
        with (
            patch("dashboard_server.app_doctor_service", return_value=service),
            patch("dashboard_server.emit_log_async", new_callable=AsyncMock) as emit_log,
        ):
            with TestClient(dashboard_server.app) as client:
                warning = client.post("/api/app/doctor/fix/test.warning", json={"mode": "safe"})
                error = client.post("/api/app/doctor/fix/test.error", json={"mode": "safe"})

        self.assertEqual(warning.status_code, 409)
        self.assertEqual(error.status_code, 500)
        terminal = [
            call
            for call in emit_log.await_args_list
            if len(call.args) >= 3 and call.args[2] == "Doctor repair was not applied."
        ]
        self.assertEqual([call.args[0] for call in terminal], ["warn", "error"])

    def test_generic_doctor_fix_endpoint_maps_every_terminal_status_to_audit_level(self) -> None:
        statuses = ("healthy", "repaired", "queued_for_approval", "needs_user_action", "failed", "error")
        service = Mock()
        service.fix.side_effect = [
            {"status": status, "changed": status == "repaired"}
            for status in statuses
        ]
        with (
            patch("dashboard_server.app_doctor_service", return_value=service),
            patch("dashboard_server.emit_log_async", new_callable=AsyncMock) as emit_log,
        ):
            with TestClient(dashboard_server.app) as client:
                for status in statuses:
                    response = client.post(f"/api/app/doctor/fix/test.{status}", json={"mode": "safe"})
                    self.assertEqual(response.status_code, 200)

        terminal = [
            call
            for call in emit_log.await_args_list
            if len(call.args) >= 3 and call.args[2] in {"Doctor repair finished.", "Doctor repair requires follow-up."}
        ]
        self.assertEqual(
            [call.args[0] for call in terminal],
            ["success", "success", "warn", "warn", "error", "error"],
        )

    def test_session_storage_doctor_uses_request_scoped_active_project(self) -> None:
        service = Mock()
        service.fix.return_value = {
            "ok": True,
            "schema": "vrcforge.doctor_fix.v1",
            "checkId": "session.storage",
            "mode": "safe",
            "status": "healthy",
            "changed": False,
            "phases": [],
        }
        with patch("dashboard_server.app_doctor_service", return_value=service):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/doctor/fix/session.storage",
                    json={"mode": "safe", "projectPath": r"C:\Unity\ActiveAvatar"},
                )

        self.assertEqual(response.status_code, 200)
        service.fix.assert_called_once_with(
            "session.storage",
            "safe",
            {"selected_project_path": r"C:\Unity\ActiveAvatar"},
        )

    def test_doctor_mcp_core_check_is_read_only_and_never_requests_a_package(self) -> None:
        rule = dashboard_server.app_doctor_service().rules["unity.mcp.package"]

        self.assertEqual(rule.title, "VRCForge MCP Core")
        self.assertFalse(rule.fixable)

    def test_doctor_core_bridge_repair_never_starts_the_legacy_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "Avatar"
            for relative in (
                "Assets/VRCForge/Core/MCP/VRCForgeCommandAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeInputAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolRegistry.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs",
                "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs",
            ):
                target = project / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("// probe", encoding="utf-8")

            with (
                patch(
                    "dashboard_server.build_unity_status_snapshot",
                    return_value={"connected": False, "error": "Unity is closed."},
                ),
                patch("dashboard_server.repair_unity_mcp_bridge_sync") as legacy_repair,
            ):
                result = dashboard_server._repair_unity_bridge_doctor(
                    {"selected_project_path": str(project)},
                    "force",
                    dashboard_server.PhaseLog(),
                )

        self.assertEqual(result, {"status": "needs_user_action", "changed": False})
        legacy_repair.assert_not_called()

    def test_mcp_core_install_detection_needs_the_complete_owned_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            required = (
                "Assets/VRCForge/Core/MCP/VRCForgeCommandAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeInputAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolRegistry.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs",
                "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs",
            )
            for relative in required:
                target = project / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("// probe", encoding="utf-8")

            self.assertTrue(dashboard_server.vrcforge_mcp_core_installed(project))
            (project / required[-1]).unlink()
            self.assertFalse(dashboard_server.vrcforge_mcp_core_installed(project))

    def test_manifest_dependency_check_accepts_bundled_core_without_legacy_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            manifest = project / "Packages" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"dependencies":{}}', encoding="utf-8")
            for relative in (
                "Assets/VRCForge/Core/MCP/VRCForgeCommandAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeInputAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolRegistry.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs",
                "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs",
            ):
                target = project / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("// probe", encoding="utf-8")

            self.assertTrue(dashboard_server.has_unity_mcp_dependency(manifest))

    def test_project_scoped_core_status_never_falls_back_to_legacy_transport(self) -> None:
        self.status_snapshot_patcher.stop()
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            descriptor = project / "Library" / "VRCForge" / "mcp-core.json"
            descriptor.parent.mkdir(parents=True)
            descriptor.write_text("{}", encoding="utf-8")
            for relative in (
                "Assets/VRCForge/Core/MCP/VRCForgeCommandAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeInputAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolRegistry.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs",
                "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs",
            ):
                marker = project / relative
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("// probe", encoding="utf-8")
            required = [{"name": name} for name in dashboard_server.REQUIRED_VRCFORGE_UNITY_TOOLS]
            core_client = Mock()
            core_client.list_tools.return_value = required
            try:
                with (
                    patch("unity_status_service.UnityMcpCoreClient", return_value=core_client),
                ):
                    status = dashboard_server.build_unity_status_snapshot(
                        SimpleNamespace(unity_mcp_timeout_seconds=10),
                        project,
                    )
            finally:
                self.status_snapshot_patcher.start()

        self.assertTrue(status["connected"])
        self.assertTrue(status["selectedInstanceMatched"])
        self.assertEqual(status["missingRequiredVrcForgeTools"], [])
        self.assertEqual(status["tools"]["vrcForgeToolsCount"], 64)
        self.assertEqual(status["mcpHealth"]["protocolVersion"], "2026-07-28")

    def test_core_only_repair_never_starts_or_registers_an_external_connector(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            for relative in (
                "Assets/VRCForge/Core/MCP/VRCForgeCommandAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeInputAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolRegistry.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs",
                "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs",
                "Packages/manifest.json",
                "ProjectSettings/ProjectVersion.txt",
            ):
                target = project / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("// probe", encoding="utf-8")
            healthy = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "vrcForgeToolsRegistered": True,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 64, "vrcForgeToolsCount": 64},
                "error": "",
            }
            with (
                patch("dashboard_server.build_unity_status_snapshot", return_value=healthy),
                patch("dashboard_server.subprocess.Popen") as external_start,
                patch("dashboard_server.launch_unity_project") as launch_unity,
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(projectPath=str(project), allowUnityRelaunch=True)
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "healthy")
        external_start.assert_not_called()
        launch_unity.assert_not_called()

    def test_core_only_repair_without_package_requires_import_and_never_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            for directory in ("Assets", "Packages", "ProjectSettings"):
                (project / directory).mkdir(parents=True, exist_ok=True)
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            with (
                patch("dashboard_server.subprocess.Popen") as external_start,
                patch("dashboard_server.launch_unity_project") as launch_unity,
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(projectPath=str(project), allowUnityRelaunch=True)
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_user_action")
        self.assertEqual(result["phases"][0]["id"], "import_vrcforge_package")
        external_start.assert_not_called()
        launch_unity.assert_not_called()

    def test_cached_doctor_service_reloads_runtime_settings_on_every_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            service = dashboard_server.app_doctor_service()
            with patch("dashboard_server.RUNTIME_SETTINGS_PATH", path):
                path.write_text('{"llm":{"provider":"ollama"}}', encoding="utf-8")
                healthy = service.detect("app.runtime_settings")

                damaged = b'{"unity_mcp":{"retry_backoff_seconds":1e999}}'
                path.write_bytes(damaged)
                broken = service.detect("app.runtime_settings")
                self.assertEqual(path.read_bytes(), damaged)

                path.write_text('{"llm":{"provider":"ollama"},"unity_mcp":{"port":8125}}', encoding="utf-8")
                restored = service.detect("app.runtime_settings")

            self.assertEqual(healthy["status"], "ok")
            self.assertEqual(broken["status"], "error")
            self.assertEqual(broken["detail"]["code"], "invalid_json")
            self.assertEqual(restored["status"], "ok")

    def test_doctor_security_context_tracks_disabled_approval_auto_and_full_modes(self) -> None:
        package_service = Mock()
        package_service.load_registry.return_value = {"governance": {}}
        package_service.list_installed.return_value = []
        cases = (
            ("disabled", "disabled", False, False, False, False, "ok", "ok"),
            ("approval", "approval", True, True, False, False, "ok", "ok"),
            ("auto", "auto", True, True, True, False, "warning", "warning"),
            ("full", "roslyn_full_auto", True, True, True, True, "warning", "warning"),
            ("full-write-disabled", "roslyn_full_auto", True, False, False, False, "ok", "ok"),
        )
        for case, mode, enabled, allow_write, broad, full, external_status, process_status in cases:
            with self.subTest(case=case):
                config = SimpleNamespace(
                    execution_mode=mode,
                    enabled=enabled,
                    allow_write_requests=allow_write,
                    developer_options_enabled=False,
                    require_token=True,
                    token="x" * 32,
                    token_created_at="",
                    token_rotated_at="",
                )
                with (
                    patch.object(dashboard_server.AGENT_GATEWAY, "ensure_config", return_value=config),
                    patch("dashboard_server.build_agentic_app_health", return_value={}),
                    patch("dashboard_server.skill_package_service", return_value=package_service),
                ):
                    context = dashboard_server.build_doctor_service_context()

                external = context["security"]["external_writes"]
                process = context["security"]["process_exec"]
                self.assertEqual(external["broadPermissions"], broad)
                self.assertEqual(external["fullPermission"], full)
                self.assertEqual(process["fullPermission"], full)
                self.assertEqual(
                    dashboard_server.DoctorService(context).detect("security.external_writes")["status"],
                    external_status,
                )
                self.assertEqual(
                    dashboard_server.DoctorService(context).detect("security.process_exec")["status"],
                    process_status,
                )
                self.assertEqual(
                    dashboard_server.DoctorService(context).detect("security.bind_auth")["status"],
                    "ok",
                )
                self.assertEqual(
                    dashboard_server.DoctorService(context).detect("security.mcp_exposure")["status"],
                    "ok",
                )

    def test_package_request_propagates_doctor_never_auto_policy(self) -> None:
        create_request = Mock(return_value={"ok": True, "status": "pending"})
        workflow = PackageInstallWorkflowService(
            PackageInstallWorkflowPorts(
                selected_project_path=lambda: r"C:\Unity\Avatar",
                locate_managers=lambda: [
                    {
                        "name": "vrc-get",
                        "source": "PATH",
                        "supportsCommandInstall": True,
                        "supportsUiHandoff": False,
                    }
                ],
                detect_package=lambda _project, _package_ids: {"installed": False},
                addon_frameworks={},
                optimizer_dependencies=[],
                summarize_debug=lambda value: value,
                read_compile_errors=lambda _params: {"ok": True, "errors": []},
                redact_support=lambda value: value,
                create_apply_request=create_request,
            )
        )
        result = workflow.request_install(
            {
                "projectPath": r"C:\Unity\Avatar",
                "packageId": "com.example.safe-package",
                "requiresExplicitApproval": True,
                "neverAutoApprove": True,
            },
            agent_name="doctor",
        )

        self.assertEqual(result["status"], "pending")
        request = create_request.call_args.args[0]
        self.assertTrue(request["requires_explicit_approval"])
        self.assertTrue(request["never_auto_approve"])
        self.assertEqual(request["agent_name"], "doctor")
        self.assertTrue(create_request.call_args.kwargs["internal_wrapper"])

    def test_doctor_project_chat_repair_queues_digest_bound_manual_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            store_path = project / ".vrcforge" / "chat-transcripts.json"
            store_path.parent.mkdir(parents=True)
            store_path.write_bytes(b'{"chats":[')
            project_key = dashboard_server.normalize_chat_project_key(str(project))
            suffix = hashlib.sha256(project_key.encode("utf-8", errors="replace")).hexdigest()[:16]
            target = dashboard_server.SessionStoreTarget(
                f"session.chat.project.{suffix}",
                store_path,
                "project_owned",
                "json",
            )
            with (
                patch("dashboard_server._session_store_targets", return_value=[target]),
                patch.object(
                    dashboard_server.AGENT_GATEWAY,
                    "create_apply_request",
                    return_value={"ok": True, "status": "pending"},
                ) as create_request,
            ):
                phases = dashboard_server.PhaseLog()
                result = dashboard_server._repair_session_storage_doctor(
                    {"selected_project_path": str(project)},
                    "safe",
                    phases,
                )

            self.assertEqual(result["status"], "queued_for_approval")
            request = create_request.call_args.args[0]
            self.assertEqual(request["target_tool"], "vrcforge_repair_project_chat_store")
            self.assertEqual(request["arguments"]["storeId"], target.store_id)
            self.assertEqual(request["arguments"]["expectedDigest"], hashlib.sha256(b'{"chats":[').hexdigest())
            self.assertTrue(request["requires_explicit_approval"])
            self.assertTrue(request["never_auto_approve"])
            self.assertTrue(create_request.call_args.kwargs["internal_wrapper"])
            detail = phases.snapshot()[0]["detail"]
            self.assertEqual(detail["detectedInvalidCount"], 1)
            self.assertEqual(detail["approvalQueuedInvalidCount"], 1)
            self.assertEqual(detail["invalidQuarantinedCount"], 0)

    def test_doctor_repairs_parseable_chat_document_with_invalid_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "chat-transcripts.json"
            original = b'{"version":1,"chats":{}}'
            store_path.write_bytes(original)
            target = dashboard_server.SessionStoreTarget(
                "session.chat.app",
                store_path,
                "app_owned",
                "json",
                required_list_field="chats",
            )
            with patch("dashboard_server._session_store_targets", return_value=[target]):
                detected = dashboard_server._detect_session_storage_doctor({})
                phases = dashboard_server.PhaseLog()
                repaired = dashboard_server._repair_session_storage_doctor({}, "safe", phases)

            self.assertEqual(detected["status"], "error")
            self.assertEqual(detected["detail"]["appRepairCount"], 1)
            self.assertEqual(repaired["status"], "repaired")
            self.assertTrue(repaired["changed"])
            self.assertFalse(store_path.exists())
            detail = phases.snapshot()[0]["detail"]
            self.assertEqual(detail["detectedInvalidCount"], 1)
            self.assertEqual(detail["invalidQuarantinedCount"], 1)
            digest = hashlib.sha256(original).hexdigest()[:16]
            self.assertEqual(
                store_path.with_name(f"{store_path.name}.vrcforge-quarantine-{digest}").read_bytes(),
                original,
            )

    def test_doctor_fix_does_not_report_unsupported_session_store_as_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "future-result.json"
            store_path.write_text('{"schema":"future.v9","payload":{}}', encoding="utf-8")
            target = dashboard_server.SessionStoreTarget(
                "session.future-result",
                store_path,
                "app_owned",
                "json",
                ("known.v1",),
            )
            with patch("dashboard_server._session_store_targets", return_value=[target]):
                phases = dashboard_server.PhaseLog()
                result = dashboard_server._repair_session_storage_doctor({}, "safe", phases)

            self.assertEqual(result, {"status": "needs_user_action", "changed": False})
            self.assertEqual(phases.snapshot()[0]["status"], "warning")
            self.assertEqual(phases.snapshot()[0]["detail"]["unresolvedCount"], 1)
            self.assertTrue(store_path.exists())

    def test_doctor_project_chat_repair_reuses_matching_pending_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            store_path = project / ".vrcforge" / "chat-transcripts.json"
            store_path.parent.mkdir(parents=True)
            original = b'{"chats":['
            store_path.write_bytes(original)
            project_key = dashboard_server.normalize_chat_project_key(str(project))
            suffix = hashlib.sha256(project_key.encode("utf-8", errors="replace")).hexdigest()[:16]
            target = dashboard_server.SessionStoreTarget(
                f"session.chat.project.{suffix}", store_path, "project_owned", "json"
            )
            pending = {
                "id": "approval-existing",
                "status": "pending",
                "targetTool": "vrcforge_repair_project_chat_store",
                "arguments": {
                    "storeId": target.store_id,
                    "expectedDigest": hashlib.sha256(original).hexdigest(),
                },
            }
            with (
                patch("dashboard_server._session_store_targets", return_value=[target]),
                patch.object(dashboard_server.AGENT_GATEWAY, "list_approvals", return_value=[pending]),
                patch.object(dashboard_server.AGENT_GATEWAY, "create_apply_request") as create_request,
            ):
                result = dashboard_server._repair_session_storage_doctor({}, "safe", dashboard_server.PhaseLog())

            self.assertEqual(result["status"], "queued_for_approval")
            create_request.assert_not_called()

    def test_approved_project_chat_repair_is_digest_bound_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            store_path = project / ".vrcforge" / "chat-transcripts.json"
            store_path.parent.mkdir(parents=True)
            original = b'{"chats":['
            store_path.write_bytes(original)
            project_key = dashboard_server.normalize_chat_project_key(str(project))
            suffix = hashlib.sha256(project_key.encode("utf-8", errors="replace")).hexdigest()[:16]
            store_id = f"session.chat.project.{suffix}"

            result = dashboard_server.repair_project_chat_store_sync(
                {
                    "projectRoot": str(project),
                    "expectedDigest": hashlib.sha256(original).hexdigest(),
                    "storeId": store_id,
                }
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "quarantined")
            self.assertFalse(store_path.exists())
            backup = store_path.with_name(
                f"{store_path.name}.vrcforge-backup-{hashlib.sha256(original).hexdigest()[:16]}"
            )
            self.assertEqual(backup.read_bytes(), original)

            repeated = dashboard_server.repair_project_chat_store_sync(
                {
                    "projectRoot": str(project),
                    "expectedDigest": hashlib.sha256(original).hexdigest(),
                    "storeId": store_id,
                }
            )
            self.assertTrue(repeated["ok"])
            self.assertEqual(repeated["status"], "already_repaired")

    def test_repeated_project_chat_approval_fails_stale_checkpoint_without_false_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "AvatarProject"
            for name in ("Assets", "Packages", "ProjectSettings", ".vrcforge"):
                (project / name).mkdir(parents=True)
            store_path = project / ".vrcforge" / "chat-transcripts.json"
            original = b'{"chats":['
            store_path.write_bytes(original)
            gateway = dashboard_server.AgentGateway(root / "config" / "gateway.json", root / "audit")
            gateway.register_write_handler(
                "vrcforge_repair_project_chat_store",
                "Repair project chat store.",
                "medium",
                dashboard_server.repair_project_chat_store_sync,
            )
            project_key = dashboard_server.normalize_chat_project_key(str(project))
            suffix = hashlib.sha256(project_key.encode("utf-8", errors="replace")).hexdigest()[:16]
            arguments = {
                "projectRoot": str(project),
                "projectPath": str(project),
                "expectedDigest": hashlib.sha256(original).hexdigest(),
                "storeId": f"session.chat.project.{suffix}",
            }
            with patch("dashboard_server.AGENT_GATEWAY", gateway):
                requests = [
                    gateway.create_apply_request(
                        {
                            "target_tool": "vrcforge_repair_project_chat_store",
                            "arguments": arguments,
                            "never_auto_approve": True,
                        },
                        internal_wrapper=True,
                    )
                    for _ in range(2)
                ]
                executions = []
                for request in requests:
                    approval_id = request["approval"]["id"]
                    gateway.approve(approval_id)
                    executions.append(gateway.apply_approved({"approvalId": approval_id}))

            self.assertEqual([item["status"] for item in executions], ["applied", "failed"], executions)
            self.assertEqual(executions[0]["result"]["status"], "quarantined")
            self.assertIn("changed after the approval snapshot", executions[1]["error"])
            active = gateway.list_interrupted_apply_recoveries({"includeResolved": False})
            self.assertEqual(active["count"], 0)
            self.assertFalse(active["blockingWrites"])

    def test_project_chat_snapshot_conflict_after_checkpoint_closes_nonblocking_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "AvatarProject"
            for name in ("Assets", "Packages", "ProjectSettings", ".vrcforge"):
                (project / name).mkdir(parents=True)
            store_path = project / ".vrcforge" / "chat-transcripts.json"
            original = b'{"chats":['
            concurrent = b'{"chats":[{"id":"newer-save"}]}'
            store_path.write_bytes(original)
            gateway = dashboard_server.AgentGateway(root / "config" / "gateway.json", root / "audit")
            gateway.register_write_handler(
                "vrcforge_repair_project_chat_store",
                "Repair project chat store.",
                "medium",
                dashboard_server.repair_project_chat_store_sync,
            )
            project_key = dashboard_server.normalize_chat_project_key(str(project))
            suffix = hashlib.sha256(project_key.encode("utf-8", errors="replace")).hexdigest()[:16]
            arguments = {
                "projectRoot": str(project),
                "projectPath": str(project),
                "expectedDigest": hashlib.sha256(original).hexdigest(),
                "storeId": f"session.chat.project.{suffix}",
            }
            request = gateway.create_apply_request(
                {
                    "target_tool": "vrcforge_repair_project_chat_store",
                    "arguments": arguments,
                    "never_auto_approve": True,
                },
                internal_wrapper=True,
            )
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            create_checkpoint = gateway._create_pre_write_checkpoint

            def checkpoint_then_change(approval: dict, supplied: dict) -> dict | None:
                checkpoint = create_checkpoint(approval, supplied)
                store_path.write_bytes(concurrent)
                return checkpoint

            with (
                patch("dashboard_server.AGENT_GATEWAY", gateway),
                patch.object(gateway, "_create_pre_write_checkpoint", side_effect=checkpoint_then_change),
            ):
                execution = gateway.apply_approved({"approvalId": approval_id})

            self.assertFalse(execution["ok"])
            self.assertEqual(execution["status"], "failed")
            self.assertEqual(execution["error"], "snapshot_changed")
            self.assertEqual(store_path.read_bytes(), concurrent)
            active = gateway.list_interrupted_apply_recoveries({"includeResolved": False})
            self.assertEqual(active["count"], 0)
            self.assertFalse(active["blockingWrites"])
            all_recoveries = gateway.list_interrupted_apply_recoveries({"includeResolved": True})
            no_write = [
                item
                for item in all_recoveries["recoveries"]
                if item.get("resolution") == "no_write_snapshot_conflict"
            ]
            self.assertEqual(len(no_write), 1)
            self.assertEqual(no_write[0]["status"], "not_applied")
            self.assertFalse(no_write[0]["blockingWrites"])

    def test_skill_quarantine_rejects_changed_manifest_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = dashboard_server.AgentGateway(root / "config" / "gateway.json", root / "audit")
            manifest = gateway.user_skills_dir / "broken-skill" / "SKILL.md"
            manifest.parent.mkdir(parents=True)
            original = b"broken-before"
            manifest.write_bytes(original)
            candidate = {
                "storagePath": str(manifest),
                "manifestDigest": hashlib.sha256(original).hexdigest(),
            }
            manifest.write_bytes(b"changed-after-scan")

            with patch("dashboard_server.AGENT_GATEWAY", gateway):
                changed = dashboard_server._quarantine_broken_user_skill(candidate)

            self.assertFalse(changed)
            self.assertEqual(manifest.read_bytes(), b"changed-after-scan")

    def test_skill_registry_read_failure_never_reports_doctor_fix_as_healthy(self) -> None:
        package_service = Mock()
        package_service.list_installed.side_effect = OSError("registry unavailable")
        with (
            patch.object(dashboard_server.AGENT_GATEWAY, "build_skill_registry", return_value={"skills": []}),
            patch("dashboard_server.skill_package_service", return_value=package_service),
        ):
            result = dashboard_server.app_doctor_service().fix("skills.registry", "safe")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_user_action")
        self.assertEqual(result["after"]["status"], "error")

    def test_app_doctor_degrades_when_diagnostics_fail(self) -> None:
        with patch(
            "dashboard_server.build_app_doctor_report",
            side_effect=RuntimeError(
                "doctor exploded at https://alice:cleartext@example.invalid/v1?token=query-secret afterwards"
            ),
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.get("/api/app/doctor")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["schema"], "vrcforge.doctor.v1")
        self.assertEqual(payload["scope"], "vrcforge.environment.v1")
        self.assertFalse(payload["projectContentInspected"])
        check_by_id = {item["id"]: item for item in payload["checks"]}
        self.assertEqual(check_by_id["desktop.runtime"]["status"], "ok")
        self.assertEqual(check_by_id["doctor.degraded"]["status"], "warning")
        self.assertIn("doctor exploded", check_by_id["doctor.degraded"]["message"])
        serialized = json.dumps(payload)
        self.assertNotIn("alice", serialized)
        self.assertNotIn("cleartext", serialized)
        self.assertNotIn("query-secret", serialized)
        self.assertIn("https://example.invalid", serialized)

    def test_debug_diagnostics_toggle_records_unified_redacted_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            privacy = dashboard_server.DiagnosticPrivacy(temp_path / "config")
            manager = dashboard_server.DiagnosticLogManager(
                temp_path / "logs",
                temp_path / "config" / "diagnostics.json",
                privacy,
            )
            with (
                patch.object(dashboard_server, "DIAGNOSTIC_PRIVACY", privacy),
                patch.object(dashboard_server, "DIAGNOSTIC_LOGGER", manager),
                patch.object(dashboard_server, "RECENT_LOGS", manager.recent_entries),
            ):
                with TestClient(dashboard_server.app) as client:
                    update = client.post("/api/app/diagnostics", json={"debugLogging": True})
                    self.assertEqual(update.status_code, 200)
                    self.assertTrue(update.json()["debugLogging"])
                    self.assertEqual(update.json()["logLevel"], "debug")

                    bootstrap = client.get("/api/app/bootstrap?app_token=query-secret&artifact_sig=artifact-secret")
                    self.assertEqual(bootstrap.status_code, 200)

                entries = [entry["data"] for entry in manager.tail_entries(100) if entry.get("scope") == "http"]
                paths = {entry.get("path") for entry in entries}
                self.assertIn("/api/app/diagnostics", paths)
                self.assertNotIn("/api/app/bootstrap", paths)
                serialized = json.dumps(entries).lower()
                self.assertNotIn("approval_token", serialized)
                self.assertNotIn("api_key", serialized)
                self.assertNotIn("query-secret", serialized)
                self.assertNotIn("artifact-secret", serialized)
                self.assertFalse((temp_path / "logs" / "interactions.jsonl").exists())

    def test_support_bundle_exports_redacted_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            privacy = dashboard_server.DiagnosticPrivacy(temp_path / "config")
            manager = dashboard_server.DiagnosticLogManager(
                temp_path / "logs",
                temp_path / "config" / "diagnostics.json",
                privacy,
            )
            manager.update_config(log_level="debug")
            separator = chr(92)
            private_path = "C:" + separator + separator.join(("Users", "BundleProbe", "PrivateAvatarProjects", "PaidAvatar"))
            manager.emit(
                "error",
                "test",
                "failure",
                {"api_key": "provider-secret", "projectPath": private_path},
            )
            with (
                patch.object(dashboard_server, "DIAGNOSTIC_PRIVACY", privacy),
                patch.object(dashboard_server, "DIAGNOSTIC_LOGGER", manager),
                patch.object(dashboard_server, "RECENT_LOGS", manager.recent_entries),
                patch.object(dashboard_server, "SUPPORT_BUNDLE_DIR", temp_path / "support-bundles"),
            ):
                with TestClient(dashboard_server.app) as client:
                    response = client.post(
                        "/api/app/support-bundle",
                        json={"logLimit": 20, "includeFullPaths": True},
                    )

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["ok"])
                self.assertTrue(payload["redacted"])
                bundle_path = Path(payload["bundlePath"])
                self.assertTrue(bundle_path.exists())
                with zipfile.ZipFile(bundle_path) as bundle:
                    names = set(bundle.namelist())
                    self.assertIn("metadata.json", names)
                    self.assertIn("diagnostic-log.txt", names)
                    self.assertNotIn("dashboard-log.json", names)
                    self.assertNotIn("interaction-log.json", names)
                    diagnostics = json.loads(bundle.read("diagnostics.json"))
                    self.assertNotIn("identities", diagnostics)
                    content = "\n".join(bundle.read(name).decode("utf-8") for name in names)
                lowered = content.lower()
                self.assertNotIn("provider-secret", lowered)
                self.assertNotIn("secret-token", lowered)
                self.assertNotIn("query-secret", lowered)
                self.assertNotIn("artifact-secret", lowered)
                self.assertNotIn(private_path.lower(), lowered)
                self.assertNotIn("privateavatarprojects", lowered)
                self.assertNotIn("diagnostic-identities.json", lowered)
                self.assertNotIn("diagnostic-alias.key", lowered)

    def test_validation_report_mvp_is_read_only_and_registered(self) -> None:
        run_validation_source = dashboard_server._run_validation_source
        wardrobe_readbacks = {
            "vrc_scan_avatar_controls": {
                "ok": True,
                "missingReferences": [{"path": "Menu/Missing"}],
            },
            "vrc_scan_wardrobe": {"ok": True, "wardrobeCandidateCount": 1},
            "vrc_scan_avatar_items": {"ok": True, "itemCount": 4},
        }

        def invoke_wardrobe_read(_settings, tool_name, _arguments):
            if tool_name not in wardrobe_readbacks:
                raise AssertionError(f"Unexpected live Unity read: {tool_name}")
            if tool_name == "vrc_scan_avatar_controls":
                self.assertEqual(_arguments["avatarPath"], "Scene/Avatar")
            return dashboard_server.McpResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                payload={"data": wardrobe_readbacks[tool_name]},
            )

        def package_manager_observation(name, callback):
            if name == "package_manager":
                return {
                    "ok": True,
                    "payload": {
                        "ok": True,
                        "preferredCli": {"name": "vrc-get"},
                        "managers": [{"name": "vrc-get"}],
                    },
                }
            return run_validation_source(name, callback)

        with (
            tempfile.TemporaryDirectory() as artifact_dir,
            patch.object(
                dashboard_server,
                "DASHBOARD_ARTIFACTS_DIR",
                Path(artifact_dir),
            ),
            patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()),
            patch("dashboard_server.invoke_unity_mcp", side_effect=invoke_wardrobe_read),
            patch(
                "dashboard_server.read_agent_compile_errors",
                return_value={"ok": True, "result": {"exitCode": 0, "stdout": "hasErrors: False\nerrorCount: 0"}},
            ),
            patch("dashboard_server.scan_avatar_parameters_gateway_sync", return_value={"ok": True, "warningCount": 1, "suggestions": [{"id": "compress"}]}),
            patch("dashboard_server.scan_fx_animator_sync", return_value={"ok": True, "parameterTypeMismatches": []}),
            patch("dashboard_server.scan_animation_bindings_sync", return_value={"ok": True, "brokenBindings": [{"clip": "BadClip"}]}),
            patch("dashboard_server.scan_shader_materials_direct", return_value={"materials": [], "summary": {"unsupportedShaderCount": 1}}),
            patch("dashboard_server.scan_avatar_performance_sync", side_effect=[
                {"ok": True, "rank": "Poor"},
                {"ok": True, "rank": "Excellent"},
            ]),
            patch(
                "dashboard_server.validation_dependency_status_sync",
                return_value={
                    "ok": True,
                    "projectConfigured": True,
                    "projectReadable": True,
                    "packages": {
                        "vrchat_sdk": {"installed": True, "packageId": "com.vrchat.avatars", "version": "3.0.0"},
                        "modular_avatar": {"installed": True, "packageId": "nadena.dev.modular-avatar", "version": "1.0.0"},
                        "vrcfury": {"installed": False},
                    },
                },
            ),
            patch(
                "dashboard_server.validation_environment_status_sync",
                return_value={
                    "ok": True,
                    "components": {
                        "unityPluginInstalled": {"status": "ok"},
                        "mcpPackageConfigured": {"status": "ok"},
                        "unityMcpBridgeReachable": {"status": "ok"},
                        "unityMcpInstance": {"status": "ok"},
                        "vrcForgeUnityTools": {"status": "ok"},
                    },
                },
            ),
            patch(
                "dashboard_server._run_validation_source",
                side_effect=package_manager_observation,
            ),
            patch("dashboard_server.scan_generated_asset_residue_sync", return_value={"ok": True, "projectReadable": True, "residueCount": 0}),
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.post("/api/app/validation/report", json={"avatarPath": "Scene/Avatar", "projectPath": r"C:\Private\UnityProject"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema"], "vrcforge.validation.v1")
        self.assertTrue(payload["readOnly"])
        self.assertFalse(payload["autoFix"])
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["rules"]["validationNeverFixes"])
        self.assertGreaterEqual(payload["summary"]["severityCounts"]["Warning"], 4)
        self.assertGreaterEqual(payload["summary"]["severityCounts"]["Suggestion"], 1)
        self.assertIn("sections", payload)
        self.assertIn("findings", payload)
        section_names = {section["name"] for section in payload["sections"]}
        self.assertIn("VRChat SDK", section_names)
        self.assertIn("MCP bridge", section_names)
        self.assertIn("Generated asset residue", section_names)
        self.assertEqual(payload["gate"]["status"], "pass")
        self.assertEqual(payload["summary"]["gateStatus"], "pass")
        self.assertNotIn(r"C:\Private\UnityProject".lower(), json.dumps(payload).lower())

        manifest = dashboard_server.AGENT_GATEWAY.build_manifest()
        tool_names = {tool["name"] for tool in manifest["tools"]}
        write_targets = {target["name"] for target in manifest["writeTargets"]}
        self.assertIn("vrcforge_run_validation_report", tool_names)
        self.assertIn("vrcforge_build_test_readiness", tool_names)
        self.assertNotIn("vrcforge_run_validation_report", write_targets)
        self.assertNotIn("vrcforge_build_test_readiness", write_targets)

    def test_validation_report_records_scanner_failures_as_findings(self) -> None:
        wardrobe_readbacks = {
            "vrc_scan_avatar_controls": {"ok": True},
            "vrc_scan_wardrobe": {"ok": True},
        }

        def invoke_wardrobe_read(_settings, tool_name, _arguments):
            if tool_name not in wardrobe_readbacks:
                raise AssertionError(f"Unexpected live Unity read: {tool_name}")
            return dashboard_server.McpResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                payload={"data": wardrobe_readbacks[tool_name]},
            )

        with (
            tempfile.TemporaryDirectory() as artifact_dir,
            patch.object(
                dashboard_server,
                "DASHBOARD_ARTIFACTS_DIR",
                Path(artifact_dir),
            ),
            patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()),
            patch("dashboard_server.invoke_unity_mcp", side_effect=invoke_wardrobe_read),
            patch(
                "dashboard_server.read_agent_compile_errors",
                return_value={"ok": True, "result": {"exitCode": 0, "stdout": "hasErrors: False\nerrorCount: 0"}},
            ),
            patch("dashboard_server.scan_avatar_parameters_gateway_sync", side_effect=RuntimeError("parameter scanner down")),
            patch("dashboard_server.scan_fx_animator_sync", return_value={"ok": True}),
            patch("dashboard_server.scan_animation_bindings_sync", return_value={"ok": True}),
            patch("dashboard_server.scan_shader_materials_direct", return_value={"materials": []}),
            patch("dashboard_server.scan_avatar_performance_sync", return_value={"ok": True, "rank": "Good"}),
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.post("/api/app/validation/report", json={"includeQuest": False, "includeReadiness": False})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["failedSourceCount"], 1)
        warnings = [finding for finding in payload["findings"] if finding["severity"] == "Warning"]
        self.assertTrue(any("parameter scanner down" in finding["message"] for finding in warnings))
        self.assertEqual(payload["gate"]["status"], "pass")

    def test_build_test_readiness_is_read_only_gate(self) -> None:
        readiness = {
            "ok": False,
            "schema": "vrcforge.build_test_readiness.v1",
            "readOnly": True,
            "autoBuild": False,
            "autoPublish": False,
            "status": "blocked",
            "gate": {"enabled": True, "status": "blocked", "blockingFindingIds": ["compile.1"]},
            "suggestedFixPlans": [
                {
                    "id": "resolve_validation_blockers",
                    "requiresPreviewApprovalCheckpointValidationRollback": True,
                }
            ],
            "rules": {"noUnattendedVrchatSdkPublish": True},
        }
        with patch("dashboard_server.build_test_readiness_sync", return_value=readiness):
            with TestClient(dashboard_server.app) as client:
                response = client.post("/api/app/build-test/readiness", json={"avatarPath": "Scene/Avatar", "projectPath": r"C:\Private\UnityProject"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema"], "vrcforge.build_test_readiness.v1")
        self.assertTrue(payload["readOnly"])
        self.assertFalse(payload["autoBuild"])
        self.assertFalse(payload["autoPublish"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "blocked")
        self.assertTrue(payload["rules"]["noUnattendedVrchatSdkPublish"])
        self.assertTrue(all(item.get("requiresPreviewApprovalCheckpointValidationRollback") for item in payload["suggestedFixPlans"]))
        self.assertNotIn(r"C:\Private\UnityProject".lower(), json.dumps(payload).lower())

    def test_provider_test_vision_is_explicit_skip_without_project_upload(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/provider/test",
                json={"provider": "ollama", "api_key": "", "base_url": "http://127.0.0.1:11434/v1", "model": "llama3.2", "capability": "vision"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "skipped")
        self.assertTrue(payload["skipped"])
        self.assertIn("no Unity screenshot", payload["message"])
        self.assertNotIn("api_key", json.dumps(payload).lower())

    def test_provider_test_structured_uses_local_typed_probe_without_secret_leak(self) -> None:
        probe_calls: list[tuple[ProviderApiConfig, str, bool]] = []

        class FakeProbe:
            def probe(self, config, prompt, *, structured=False):
                probe_calls.append((config, prompt, structured))
                return '{"ok":true,"name":"vrcforge"}'

        owner = ProviderTestIntegrationService(
            ProviderTestServicePorts(
                resolve_api_request=lambda request: ProviderApiConfig(
                    provider=request.provider,
                    api_key=request.api_key,
                    base_url=request.base_url or "https://api.openai.com/v1",
                    model=request.model or "gpt-4.1-mini",
                    api_type=request.api_type,
                    thinking_level=request.thinking_level,
                ),
                normalize_provider_name=dashboard_server.normalize_provider_name,
                provider_display_name=dashboard_server.provider_display_name,
                provider_config_descriptor=dashboard_server.PROVIDER_MODEL_CATALOG.provider_config_descriptor,
                provider_requires_api_key=dashboard_server.provider_requires_api_key,
                extract_json_block=dashboard_server.extract_json_block,
            ),
            FakeProbe(),
        )
        payload = owner.run(
            dashboard_server.ProviderTestRequest(
                provider="openai",
                api_key="provider-secret",
                base_url="",
                model="gpt-4.1-mini",
                capability="structured",
            )
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(probe_calls), 1)
        self.assertTrue(probe_calls[0][2])
        self.assertNotIn("provider-secret", json.dumps(payload))

    def test_read_avatars_sync_reports_execution_mode_without_name_error(self) -> None:
        export_payload = {"summary": {"avatarCount": 1}}
        with (
            patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()),
            patch("dashboard_server.load_dashboard_export_payload", return_value=(export_payload, "unit-test", False)),
            patch("dashboard_server.serialize_avatar_list", return_value=[{"name": "Avatar", "path": "Scene/Avatar"}]),
        ):
            payload = dashboard_server.AVATAR_TUNING_WORKFLOWS.read_avatars(
                dashboard_server.DashboardRequest()
            )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["executed"])
        self.assertEqual(payload["executionMode"], "live-unity")
        self.assertEqual(payload["avatarCount"], 1)

    def test_app_avatars_endpoint_uses_live_avatar_builder(self) -> None:
        export_payload = {"summary": {"avatarCount": 1}}
        seen: dict[str, object] = {}

        def fake_export(_settings: object, request: dashboard_server.DashboardRequest) -> tuple[dict[str, object], str, bool]:
            seen["source_mode"] = request.source_mode
            seen["mock_execute"] = request.mock_execute
            seen["save_artifacts"] = request.save_artifacts
            return export_payload, "unit-test", False

        with (
            patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()),
            patch("dashboard_server.load_dashboard_export_payload", side_effect=fake_export),
            patch(
                "dashboard_server.serialize_avatar_list",
                return_value=[
                    {
                        "avatarName": "Hero",
                        "avatarPath": "Scene/Hero",
                        "sceneName": "Scene",
                        "rendererCount": 3,
                        "blendshapeCount": 8,
                    }
                ],
            ),
            TestClient(dashboard_server.app) as client,
        ):
            response = client.post("/api/app/avatars", json={"projectPath": r"C:\Unity\Hero"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["executed"])
        self.assertEqual(payload["executionMode"], "live-unity")
        self.assertEqual(payload["avatars"][0]["avatarPath"], "Scene/Hero")
        self.assertEqual(payload["avatarCount"], 1)
        self.assertEqual(seen["source_mode"], "unity_live_export")
        self.assertIs(seen["mock_execute"], False)
        self.assertIs(seen["save_artifacts"], True)

    def test_blendshape_gateway_preserves_core_inventory_fields(self) -> None:
        export_payload = {
            "generatedAtUtc": "2026-08-04T00:00:00Z",
            "summary": {"avatarCount": 1, "blendshapeCount": 1},
            "avatars": [{"avatarPath": "Scene/Avatar", "renderers": []}],
        }
        selected = SimpleNamespace(avatar_name="Avatar", avatar_path="Scene/Avatar")
        with (
            patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()),
            patch("dashboard_server.load_dashboard_export_payload", return_value=(export_payload, "unity-mcp export", False)),
            patch("dashboard_server.resolve_avatar_selection", return_value=selected),
            patch("dashboard_server.remember_loaded_avatar"),
            patch("dashboard_server.serialize_selected_avatar", return_value={"avatarName": "Avatar", "avatarPath": "Scene/Avatar"}),
            patch("dashboard_server.serialize_blendshape_details", return_value=[]),
        ):
            payload = dashboard_server.AVATAR_TUNING_WORKFLOWS.read_avatar_blendshapes(
                dashboard_server.AvatarBlendshapeListRequest()
            )

        self.assertEqual(payload["generatedAtUtc"], export_payload["generatedAtUtc"])
        self.assertEqual(payload["summary"], export_payload["summary"])
        self.assertEqual(payload["avatars"], export_payload["avatars"])

    def test_app_auth_validation_checks_loopback_origin_and_token(self) -> None:
        original_required = dashboard_server.APP_AUTH_REQUIRED
        original_token = dashboard_server.APP_SESSION_TOKEN
        dashboard_server.APP_AUTH_REQUIRED = True
        dashboard_server.APP_SESSION_TOKEN = "test-app-session-token"
        try:
            with self.assertRaises(dashboard_server.HTTPException) as non_loopback:
                dashboard_server.validate_app_request_auth("192.0.2.10", "", "test-app-session-token")
            self.assertEqual(non_loopback.exception.status_code, 403)

            with self.assertRaises(dashboard_server.HTTPException) as bad_origin:
                dashboard_server.validate_app_request_auth(
                    "127.0.0.1",
                    "https://example.invalid",
                    "test-app-session-token",
                )
            self.assertEqual(bad_origin.exception.status_code, 403)

            with self.assertRaises(dashboard_server.HTTPException) as bad_token:
                dashboard_server.validate_app_request_auth("127.0.0.1", "", "wrong-token")
            self.assertEqual(bad_token.exception.status_code, 401)

            dashboard_server.validate_app_request_auth("127.0.0.1", "tauri://localhost", "test-app-session-token")
        finally:
            dashboard_server.APP_AUTH_REQUIRED = original_required
            dashboard_server.APP_SESSION_TOKEN = original_token

    def test_source_mode_app_session_handshake_is_local_and_lightweight(self) -> None:
        original_portable = dashboard_server.PORTABLE_MODE
        dashboard_server.PORTABLE_MODE = False
        try:
            with TestClient(dashboard_server.app) as client:
                missing_origin = client.get("/api/app/session")
                bad_origin = client.get("/api/app/session", headers={"Origin": "https://example.invalid"})
                dev_session = client.get("/api/app/session", headers={"Origin": "http://127.0.0.1:1420"})
                challenge = client.get(
                    "/api/app/session-challenge",
                    params={"nonce": "startup-nonce-1"},
                    headers={"Origin": "tauri://localhost"},
                )

            self.assertEqual(missing_origin.status_code, 403)
            self.assertEqual(bad_origin.status_code, 403)
            self.assertEqual(dev_session.status_code, 200)
            self.assertGreaterEqual(len(dev_session.json()["appSessionToken"]), 32)
            self.assertEqual(challenge.status_code, 200)
            self.assertEqual(
                challenge.json()["signature"],
                dashboard_server.app_session_challenge_signature("startup-nonce-1"),
            )
            self.assertNotIn("appSessionToken", challenge.json())
        finally:
            dashboard_server.PORTABLE_MODE = original_portable

    def test_app_cors_preflight_is_not_blocked_by_session_auth(self) -> None:
        original_required = dashboard_server.APP_AUTH_REQUIRED
        original_token = dashboard_server.APP_SESSION_TOKEN
        dashboard_server.APP_AUTH_REQUIRED = True
        dashboard_server.APP_SESSION_TOKEN = "test-app-session-token"
        try:
            with TestClient(dashboard_server.app) as client:
                preflight = client.options(
                    "/api/app/bootstrap",
                    headers={
                        "Origin": "tauri://localhost",
                        "Access-Control-Request-Method": "GET",
                        "Access-Control-Request-Headers": "authorization",
                    },
                )
                missing_token_get = client.get("/api/app/bootstrap", headers={"Origin": "tauri://localhost"})

            self.assertEqual(preflight.status_code, 200)
            self.assertEqual(preflight.headers.get("access-control-allow-origin"), "tauri://localhost")
            self.assertIn("authorization", preflight.headers.get("access-control-allow-headers", "").lower())
            self.assertEqual(missing_token_get.status_code, 401)
        finally:
            dashboard_server.APP_AUTH_REQUIRED = original_required
            dashboard_server.APP_SESSION_TOKEN = original_token

    def test_app_auth_covers_legacy_api_routes_but_keeps_health_public(self) -> None:
        original_required = dashboard_server.APP_AUTH_REQUIRED
        original_token = dashboard_server.APP_SESSION_TOKEN
        dashboard_server.APP_AUTH_REQUIRED = True
        dashboard_server.APP_SESSION_TOKEN = "test-app-session-token"
        headers = {"Authorization": "Bearer test-app-session-token"}
        try:
            with TestClient(dashboard_server.app) as client:
                health = client.get("/api/health")
                authorized_health = client.get("/api/health", headers=headers)
                missing_token_get = client.get("/api/projects")
                query_token_get = client.get("/api/projects?app_token=test-app-session-token")
                missing_token_post = client.post("/api/projects/install", json={})
                authorized_get = client.get("/api/projects", headers=headers)

            self.assertEqual(health.status_code, 200)
            health_payload = health.json()
            self.assertEqual(health_payload["schema"], "vrcforge.public_health.v1")
            self.assertNotIn("paths", health_payload)
            self.assertNotIn("projects", health_payload)
            self.assertNotIn("components", health_payload)
            self.assertNotIn("configPath", health_payload)
            self.assertEqual(authorized_health.status_code, 200)
            self.assertIn("components", authorized_health.json())
            self.assertEqual(missing_token_get.status_code, 401)
            self.assertEqual(query_token_get.status_code, 401)
            self.assertEqual(missing_token_post.status_code, 401)
            self.assertEqual(authorized_get.status_code, 200)
        finally:
            dashboard_server.APP_AUTH_REQUIRED = original_required
            dashboard_server.APP_SESSION_TOKEN = original_token

    def test_artifact_urls_require_scoped_signature(self) -> None:
        original_required = dashboard_server.APP_AUTH_REQUIRED
        original_token = dashboard_server.APP_SESSION_TOKEN
        dashboard_server.APP_AUTH_REQUIRED = True
        dashboard_server.APP_SESSION_TOKEN = "test-app-session-token"
        artifact_path = dashboard_server.DASHBOARD_ARTIFACTS_DIR / "latest" / "signed-artifact-test.txt"
        runtime_path = dashboard_server.ARTIFACTS_DIR / "optimizer-apply-smoke" / "runtime-signed-artifact-test.txt"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("artifact-ok", encoding="utf-8")
        try:
            signed_url = dashboard_server.to_artifact_url(str(artifact_path))
            repeated_url = dashboard_server.to_artifact_url(str(artifact_path))
            self.assertTrue(signed_url.startswith("/artifacts/latest/signed-artifact-test.txt?"))
            self.assertIn("artifact_sig=", signed_url)
            self.assertIn("artifact_v=", signed_url)
            self.assertNotIn("app_token=", signed_url)
            self.assertEqual(repeated_url, signed_url)

            with TestClient(dashboard_server.app) as client:
                unsigned = client.get("/artifacts/latest/signed-artifact-test.txt")
                signed = client.get(signed_url)

            self.assertEqual(unsigned.status_code, 401)
            self.assertEqual(signed.status_code, 200)
            self.assertEqual(signed.text, "artifact-ok")

            runtime_path.parent.mkdir(parents=True, exist_ok=True)
            runtime_path.write_text("runtime-artifact-ok", encoding="utf-8")
            runtime_url = dashboard_server.to_runtime_artifact_url(str(runtime_path))
            self.assertTrue(runtime_url.startswith("/runtime-artifacts/optimizer-apply-smoke/runtime-signed-artifact-test.txt?"))
            self.assertIn("artifact_sig=", runtime_url)
            with TestClient(dashboard_server.app) as client:
                runtime_unsigned = client.get("/runtime-artifacts/optimizer-apply-smoke/runtime-signed-artifact-test.txt")
                runtime_signed = client.get(runtime_url)
            self.assertEqual(runtime_unsigned.status_code, 401)
            self.assertEqual(runtime_signed.status_code, 200)
            self.assertEqual(runtime_signed.text, "runtime-artifact-ok")
        finally:
            artifact_path.unlink(missing_ok=True)
            runtime_path.unlink(missing_ok=True)
            dashboard_server.APP_AUTH_REQUIRED = original_required
            dashboard_server.APP_SESSION_TOKEN = original_token

    def test_packaged_backend_exe_resolves_payload_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload_root = Path(tmp) / "VRCForge_Windows_x64"
            backend_exe = payload_root / "backend" / "vrcforge_backend.exe"
            with (
                patch.object(dashboard_server.sys, "frozen", True, create=True),
                patch.object(dashboard_server.sys, "executable", str(backend_exe)),
            ):
                self.assertEqual(dashboard_server.default_runtime_root(), payload_root.resolve())

    def test_packaged_backend_defaults_to_user_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
                expected = Path(tmp) / "VRCForge" / "agentic-app"
                self.assertEqual(dashboard_server.default_user_data_root(), expected)

    def test_agentic_permission_full_auto_does_not_wait_for_unity_acknowledgement(self) -> None:
        with TestClient(dashboard_server.app) as client:
            blocked = client.post("/api/app/permission", json={"execution_mode": "roslyn_full_auto"})
            self.assertEqual(blocked.status_code, 200)
            self.assertEqual(blocked.json()["permission"]["executionMode"], "roslyn_full_auto")
            self.assertTrue(blocked.json()["permission"]["fullPermission"])
            self.assertFalse(blocked.json()["permission"]["allowRoslynAdvanced"])
            self.assertNotIn("unityAcknowledgement", blocked.json())

            enabled = client.post(
                "/api/app/permission",
                json={
                    "execution_mode": "roslyn_full_auto",
                    "acknowledge_roslyn_risk": True,
                },
            )
            self.assertEqual(enabled.status_code, 200)
            permission = enabled.json()["permission"]
            self.assertEqual(permission["executionMode"], "roslyn_full_auto")
            self.assertTrue(permission["roslynRiskAcknowledged"])
            self.assertFalse(permission["allowRoslynAdvanced"])
            self.assertTrue(enabled.json()["permission"]["fullPermission"])
            self.assertNotIn("unityAcknowledgement", enabled.json())

            approval = client.post("/api/app/permission", json={"execution_mode": "approval"})
            self.assertEqual(approval.status_code, 200)
            self.assertEqual(approval.json()["permission"]["executionMode"], "approval")
            self.assertTrue(approval.json()["permission"]["roslynRiskAcknowledged"])

            restored = client.post("/api/app/permission", json={"execution_mode": "roslyn_full_auto"})
            self.assertEqual(restored.status_code, 200)
            self.assertEqual(restored.json()["permission"]["executionMode"], "roslyn_full_auto")
            self.assertTrue(restored.json()["permission"]["roslynRiskAcknowledged"])
            self.assertNotIn("unityAcknowledgement", restored.json())

    def test_auto_permission_shell_delete_and_outside_read_require_manual_until_full_auto(self) -> None:
        def ps_quote(path: Path) -> str:
            return "'" + str(path).replace("'", "''") + "'"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside.txt"
            outside.write_text("outside-ok", encoding="utf-8")
            victim = workspace / "victim.txt"
            victim.write_text("delete-me", encoding="utf-8")
            shell_process_ports, shell_processes = fake_dashboard_shell_process_ports(stdout="outside-ok")
            gateway = AgentGateway(
                root / "config" / "agent_gateway.json",
                root / "audit",
                shell_process_ports=shell_process_ports,
            )

            config = gateway.ensure_config()
            config.enabled = True
            config.execution_mode = "auto"
            gateway.save_config(config)

            outside_read = gateway.shell.execute(
                {
                    "command": f"Get-Content -LiteralPath {ps_quote(outside)}",
                    "workspace_root": str(workspace),
                    "cwd": str(workspace),
                    "timeout_seconds": 5,
                }
            )
            self.assertEqual(outside_read["status"], "pending_approval")
            self.assertTrue(outside_read["approval"]["requiresExplicitApproval"])
            self.assertIn("outside", outside_read["approval"]["explicitApprovalReason"].lower())

            delete_request = gateway.shell.execute(
                {
                    "command": f"Remove-Item -LiteralPath {ps_quote(victim)}",
                    "workspace_root": str(workspace),
                    "cwd": str(workspace),
                    "timeout_seconds": 5,
                }
            )
            self.assertEqual(delete_request["status"], "pending_approval")
            self.assertTrue(delete_request["approval"]["requiresExplicitApproval"])
            self.assertTrue(victim.exists())

            config = gateway.ensure_config()
            config.execution_mode = "roslyn_full_auto"
            config.roslyn_risk_acknowledged = True
            config.allow_roslyn_advanced = True
            gateway.save_config(config)

            full_read = gateway.shell.execute(
                {
                    "command": f"Get-Content -LiteralPath {ps_quote(outside)}",
                    "workspace_root": str(workspace),
                    "cwd": str(workspace),
                    "timeout_seconds": 5,
                }
            )
            self.assertEqual(full_read["status"], "executed")
            self.assertIn("outside-ok", full_read["result"]["stdout"])
            self.assertEqual(len(shell_processes), 1)

            full_delete = gateway.shell.execute(
                {
                    "command": f"Remove-Item -LiteralPath {ps_quote(victim)}",
                    "workspace_root": str(workspace),
                    "cwd": str(workspace),
                    "timeout_seconds": 5,
                }
            )
            # Full permission removes the approval prompt, not the rollback
            # invariant: a mutating shell command still fails closed when the
            # workspace is not a checkpointable Unity project.
            self.assertEqual(full_delete["status"], "failed")
            self.assertTrue(victim.exists())
            self.assertIn("Unity project", full_delete["error"])

    def test_auto_permission_delete_write_requires_manual_until_full_auto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "Assets" / "target.txt").write_text("delete-me", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            gateway.checkpoint_prepare_handler = lambda _root: {"ok": True}

            def delete_handler(args: dict) -> dict:
                target = Path(args["assetPath"])
                target.unlink(missing_ok=True)
                return {"ok": True, "deleted": target.name}

            gateway.register_write_handler("vrcforge_test_delete_asset", "Delete test asset.", "high", delete_handler)
            config = gateway.ensure_config()
            config.enabled = True
            config.execution_mode = "auto"
            gateway.save_config(config)

            auto_request = gateway.create_apply_request(
                {
                    "target_tool": "vrcforge_test_delete_asset",
                    "arguments": {
                        "projectRoot": str(project),
                        "assetPath": str(project / "Assets" / "target.txt"),
                        "delete": True,
                    },
                }
            )
            self.assertEqual(auto_request["status"], "pending")
            self.assertTrue(auto_request["approval"]["requiresExplicitApproval"])
            self.assertTrue((project / "Assets" / "target.txt").exists())

            config = gateway.ensure_config()
            config.execution_mode = "roslyn_full_auto"
            config.roslyn_risk_acknowledged = True
            config.allow_roslyn_advanced = True
            gateway.save_config(config)
            full_request = gateway.create_apply_request(
                {
                    "target_tool": "vrcforge_test_delete_asset",
                    "arguments": {
                        "projectRoot": str(project),
                        "assetPath": str(project / "Assets" / "target.txt"),
                        "delete": True,
                    },
                }
            )
            self.assertEqual(full_request["status"], "executed")
            self.assertTrue(full_request["autoApproved"])
            self.assertTrue(full_request["fullPermission"])
            self.assertEqual(full_request["permissionLabel"], "full permission")
            self.assertTrue(full_request["approval"]["fullPermission"])
            self.assertEqual(full_request["approval"]["permissionMode"], "roslyn_full_auto")
            self.assertFalse((project / "Assets" / "target.txt").exists())
            audit_logs = gateway.recent_audit_logs(limit=30)
            self.assertTrue(
                any(
                    event.get("event") == "approval_auto_approved"
                    and event.get("fullPermission") is True
                    and event.get("permissionLabel") == "full permission"
                    and event.get("targetTool") == "vrcforge_test_delete_asset"
                    for event in audit_logs
                )
            )
            runtime_events = gateway.list_runtime_runs(limit=30)["events"]
            self.assertTrue(
                any(
                    event.get("event") == "approval_applied"
                    and event.get("fullPermission") is True
                    and event.get("permissionLabel") == "full permission"
                    for event in runtime_events
                )
            )

    def test_agent_runtime_message_observes_and_plans_without_unity(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/app/agent/message", json={"message": "检查仓库状态"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["observe"]["ok"])
        self.assertEqual(payload["plan"]["planner"], "deterministic-local")
        self.assertIn("session_id", payload)
        self.assertIn("turn_id", payload)

    def test_agent_runtime_run_ledger_records_message_turn(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/agent/message",
                json={
                    "message": "hello ledger",
                    "clientTurnId": "client-turn-1",
                    "provider": "deepseek",
                    "providerLabel": "DeepSeek",
                    "model": "deepseek-v4-pro",
                    "projectRoot": "D:/AvatarProject",
                },
            )
            self.assertEqual(response.status_code, 200)
            turn_payload = response.json()

            dashboard_server.AGENT_GATEWAY._runtime_sessions.clear()
            ledger_response = client.get(
                "/api/app/agent/runs",
                params={"sessionId": turn_payload["sessionId"], "limit": "10"},
            )

        self.assertEqual(ledger_response.status_code, 200)
        ledger = ledger_response.json()
        self.assertTrue(ledger["ok"])
        runs = ledger["runs"]
        self.assertGreaterEqual(len(runs), 1)
        run = next(item for item in runs if item.get("turnId") == turn_payload["turnId"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["clientTurnId"], "client-turn-1")
        self.assertEqual(run["providerLabel"], "DeepSeek")
        self.assertEqual(run["model"], "deepseek-v4-pro")
        self.assertIn("stepCount", run)

    def test_agent_runtime_cancel_records_request(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/agent/runs/cancel",
                json={"sessionId": "sess-cancel", "clientTurnId": "client-cancel-1", "reason": "user_stop"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "cancel_requested")

            ledger_response = client.get(
                "/api/app/agent/runs",
                params={"sessionId": "sess-cancel", "clientTurnId": "client-cancel-1"},
            )

        self.assertEqual(ledger_response.status_code, 200)
        runs = ledger_response.json()["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "cancel_requested")
        self.assertEqual(runs[0]["clientTurnId"], "client-cancel-1")

    def test_agent_runtime_cancel_by_session_is_observed_by_turn(self) -> None:
        session_id = "sess-cancel-observed"
        dashboard_server.AGENT_GATEWAY.request_runtime_cancel({"session_id": session_id, "reason": "user_stop"})
        payload = dashboard_server.AGENT_GATEWAY.runtime_message(
            {"message": "hello after cancel", "session_id": session_id},
        )

        self.assertEqual(payload["plan"]["nextStep"], "cancelled")

        followup = dashboard_server.AGENT_GATEWAY.runtime_message(
            {"message": "hello after consumed cancel", "session_id": session_id},
        )

        self.assertNotEqual(followup["plan"].get("nextStep"), "cancelled")

    def test_agent_runtime_cancel_after_planner_return_marks_turn_cancelled(self) -> None:
        client_turn_id = "client-cancel-after-plan"

        def fake_request(_settings, _prompt, *, stream_callback=None) -> LlmPlanResponse:
            dashboard_server.AGENT_GATEWAY.request_runtime_cancel(
                {"clientTurnId": client_turn_id, "reason": "user_stop"}
            )
            return LlmPlanResponse(
                text=json.dumps(
                    {
                        "action": "reply",
                        "summary": "planner completed after stop",
                        "reply": "this should not surface",
                    }
                ),
                usage={},
                reasoning={},
            )

        with (
            patch.object(
                dashboard_server.PROVIDER_CONFIGURATION,
                "current_api_config",
                return_value=_test_runtime_provider_config(),
            ),
            patch("dashboard_server.request_llm_plan_with_metadata", side_effect=fake_request),
        ):
            payload = dashboard_server.AGENT_GATEWAY.runtime_message(
                {
                    "message": "cancel after provider call",
                    "session_id": "sess-cancel-after-plan",
                    "clientTurnId": client_turn_id,
                },
            )

        self.assertEqual(payload["plan"]["nextStep"], "cancelled")
        self.assertEqual(payload["plan"]["reply"], "Request cancelled.")

    def test_background_runtime_propagates_provider_failure_while_interactive_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config.json", root / "audit")
            bind_test_runtime_planner(
                gateway,
                Mock(side_effect=ConnectionError("provider unavailable")),
            )

            interactive = gateway.runtime_message({"message": "hello"})
            self.assertEqual(interactive["plan"]["planner"], "deterministic-local")

            with self.assertRaises(ConnectionError):
                gateway.runtime_message(
                    {
                        "message": "resume the background goal",
                        "goalDeliveryId": "goal-delivery-provider-failure",
                        "_backgroundGoalRun": True,
                    }
                )

    def test_background_provider_probe_treats_http_error_as_reachable_but_connection_failure_as_offline(self) -> None:
        unavailable = dashboard_server.urllib.error.HTTPError(
            "http://127.0.0.1:11434/api/tags",
            503,
            "temporarily unavailable",
            hdrs=None,
            fp=None,
        )
        with patch("dashboard_server.urllib.request.urlopen", side_effect=unavailable):
            self.assertTrue(
                dashboard_server.probe_background_goal_provider(
                    "local",
                    "http://127.0.0.1:11434",
                )
            )
        with patch("dashboard_server.urllib.request.urlopen", side_effect=ConnectionError("offline")):
            self.assertFalse(
                dashboard_server.probe_background_goal_provider(
                    "local",
                    "http://127.0.0.1:11434",
                )
            )

    def test_background_goal_persistence_redacts_standalone_credentials_and_paths(self) -> None:
        fake_credential = "sk-" + "a" * 16
        private_path = "C:\\Users\\ProbeUser\\private\\result.txt"
        payload = redact_background_goal_persistence(
            {
                "detail": f"provider returned {fake_credential} at {private_path}",
                "cwd": "private-workspace-name",
                "api_key": fake_credential,
            }
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(fake_credential, serialized)
        self.assertNotIn("ProbeUser", serialized)
        self.assertNotIn("private-workspace-name", serialized)
        self.assertIn("<redacted>", serialized)
        self.assertIn("<path redacted>", serialized)

    def test_runtime_loop_suppresses_only_three_identical_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config.json", root / "audit")

            def fail_tool(_params: dict) -> dict:
                raise RuntimeError("bounded failure")

            gateway.register_tool("vrcforge_test_bounded_failure", "Test failure.", "read/debug", fail_tool)
            repeated_plan = {
                "summary": "retry the failed read",
                "reply": "retrying",
                "planner": "test",
                "nextStep": "call_skill",
                "skillNeeded": True,
                "skillTool": "vrcforge_test_bounded_failure",
                "skillParams": {"value": 1},
                "continueLoop": True,
            }

            bind_test_runtime_planner(gateway, lambda _prompt: repeated_plan)
            payload = gateway.runtime_message({"message": "exercise bounded failure"})

        self.assertEqual(len(payload["steps"]), 3)
        self.assertEqual(payload["plan"]["nextStep"], "loop_suppressed")
        self.assertEqual(payload["plan"]["loopSuppression"]["consecutive"], 3)
        self.assertEqual(payload["plan"]["loopSuppression"]["failureClass"], "tool_error")
        self.assertNotIn("value", payload["plan"]["loopSuppression"])

    def test_runtime_loop_does_not_suppress_distinct_failed_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config.json", root / "audit")

            def fail_tool(_params: dict) -> dict:
                raise RuntimeError("bounded failure")

            gateway.register_tool("vrcforge_test_distinct_failure", "Test failure.", "read/debug", fail_tool)
            plans = [
                {
                    "summary": "try a distinct input",
                    "reply": "retrying",
                    "planner": "test",
                    "nextStep": "call_skill",
                    "skillNeeded": True,
                    "skillTool": "vrcforge_test_distinct_failure",
                    "skillParams": {"value": value},
                    "continueLoop": True,
                }
                for value in (1, 2, 3)
            ]
            plans.append(
                {
                    "summary": "stopped honestly",
                    "reply": "The attempted inputs failed.",
                    "planner": "test",
                    "nextStep": "done",
                }
            )

            plan_iterator = iter(plans)
            bind_test_runtime_planner(gateway, lambda _prompt: next(plan_iterator))
            payload = gateway.runtime_message({"message": "exercise distinct failures"})

        self.assertEqual(len(payload["steps"]), 3)
        self.assertEqual(payload["plan"]["nextStep"], "done")
        self.assertNotIn("loopSuppression", payload["plan"])

    def test_runtime_loop_never_executes_a_fourth_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config.json", root / "audit")
            calls: list[str] = []
            plans = []
            for index in range(4):
                name = f"vrcforge_test_tool_budget_{index}"
                gateway.register_tool(
                    name,
                    "Tool-call budget fixture.",
                    "read/debug",
                    lambda _params, current=name: calls.append(current) or {"ok": True},
                )
                plans.append(
                    {
                        "summary": "request another distinct read",
                        "reply": "continuing",
                        "planner": "test",
                        "nextStep": "call_skill",
                        "skillNeeded": True,
                        "skillTool": name,
                        "skillParams": {"index": index},
                        "continueLoop": True,
                    }
                )

            plan_iterator = iter(plans)
            bind_test_runtime_planner(gateway, lambda _prompt: next(plan_iterator))
            payload = gateway.runtime_message({"message": "exercise tool-call budget"})

        self.assertEqual(calls, [f"vrcforge_test_tool_budget_{index}" for index in range(3)])
        self.assertEqual(len([step for step in payload["steps"] if step["kind"] == "skill"]), 3)
        self.assertEqual(payload["plan"]["nextStep"], "paused")
        self.assertTrue(payload["plan"]["stepLimitReached"])
        self.assertTrue(payload["plan"]["toolCallLimitReached"])
        self.assertEqual(payload["plan"]["toolCallCount"], 3)
        self.assertEqual(payload["plan"]["remainingAction"]["tool"], "vrcforge_test_tool_budget_3")
        self.assertIn("尚未执行：vrcforge_test_tool_budget_3", payload["plan"]["reply"])

    def test_desktop_bootstrap_counts_toward_three_tool_call_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config.json", root / "audit")
            calls: list[str] = []
            gateway.register_tool(
                "vrcforge_agent_desktop_action",
                "Desktop bootstrap fixture.",
                "read/debug",
                lambda _params: calls.append("bootstrap") or {"applications": [], "windows": []},
            )
            plans = []
            for index in range(3):
                name = f"vrcforge_test_after_bootstrap_{index}"
                gateway.register_tool(
                    name,
                    "Post-bootstrap budget fixture.",
                    "read/debug",
                    lambda _params, current=name: calls.append(current) or {"ok": True},
                )
                plans.append(
                    {
                        "summary": "request another read after bootstrap",
                        "reply": "continuing",
                        "planner": "test",
                        "nextStep": "call_skill",
                        "skillNeeded": True,
                        "skillTool": name,
                        "skillParams": {"index": index},
                        "continueLoop": True,
                    }
                )

            plan_iterator = iter(plans)
            bind_test_runtime_planner(gateway, lambda _prompt: next(plan_iterator))
            with patch.object(gateway.desktop, "consume_computer_use_turn_grant"):
                payload = gateway.runtime_message(
                    {"message": "exercise bootstrap tool-call budget", "_computerUseRequested": True}
                )

        self.assertEqual(calls, ["bootstrap", "vrcforge_test_after_bootstrap_0", "vrcforge_test_after_bootstrap_1"])
        self.assertNotIn("vrcforge_test_after_bootstrap_2", calls)
        self.assertTrue(payload["plan"]["toolCallLimitReached"])
        self.assertEqual(payload["plan"]["toolCallCount"], 3)

    def test_desktop_bootstrap_runs_once_per_runtime_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config.json", root / "audit")
            calls: list[str] = []
            gateway.register_tool(
                "vrcforge_agent_desktop_action",
                "Desktop bootstrap fixture.",
                "read/debug",
                lambda _params: calls.append("bootstrap") or {"applications": [], "windows": []},
            )
            terminal = {
                "summary": "done",
                "reply": "done",
                "planner": "test",
                "nextStep": "done",
            }
            bind_test_runtime_planner(gateway, lambda _prompt: terminal)
            with patch.object(gateway.desktop, "consume_computer_use_turn_grant"):
                first = gateway.runtime_message(
                    {"session_id": "same-session", "message": "first", "_computerUseRequested": True}
                )
                second = gateway.runtime_message(
                    {"session_id": "same-session", "message": "second", "_computerUseRequested": True}
                )
                gateway.runtime_message(
                    {"session_id": "other-session", "message": "third", "_computerUseRequested": True}
                )

        self.assertEqual(calls, ["bootstrap", "bootstrap"])
        self.assertEqual(first["steps"][0]["tool"], "vrcforge_agent_desktop_action")
        self.assertNotIn("steps", second)
        self.assertEqual(gateway._runtime_sessions["same-session"]["desktopBootstrapToolCalls"], 1)

    def test_agent_question_keeps_goal_delivery_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config.json", root / "audit")
            created = isolated_agent_question_service(gateway).create(
                {
                    "question": "Which avatar should this goal use?",
                    "options": ["First avatar", "Second avatar"],
                    "goalDeliveryId": "goal-delivery-question-link",
                }
            )

        self.assertEqual(created["question"]["goalDeliveryId"], "goal-delivery-question-link")
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/agent/questions",
                json={
                    "question": "Which avatar should this background goal use?",
                    "options": ["First avatar", "Second avatar"],
                    "goalDeliveryId": "goal-delivery-question-api-link",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["question"]["goalDeliveryId"], "goal-delivery-question-api-link")

    def test_agent_runtime_queue_records_request(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/agent/runs/queue",
                json={
                    "sessionId": "sess-queue",
                    "clientTurnId": "client-queue-1",
                    "message": "queued follow-up",
                    "providerLabel": "DeepSeek",
                    "model": "deepseek-v4-pro",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "queued")

            ledger_response = client.get(
                "/api/app/agent/runs",
                params={"sessionId": "sess-queue", "clientTurnId": "client-queue-1"},
            )

        self.assertEqual(ledger_response.status_code, 200)
        runs = ledger_response.json()["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "queued")
        self.assertEqual(runs[0]["messageSummary"], "queued follow-up")
        self.assertEqual(runs[0]["providerLabel"], "DeepSeek")

    @patch("dashboard_server.request_llm_plan_with_metadata")
    @patch.object(dashboard_server.PROVIDER_CONFIGURATION, "current_api_config")
    def test_agent_runtime_message_includes_provider_reasoning_trace(
        self,
        mock_current_api_config,
        mock_request_llm_plan,
    ) -> None:
        mock_current_api_config.return_value = _test_runtime_provider_config(model="qwen3")
        mock_request_llm_plan.return_value = LlmPlanResponse(
            text=json.dumps({"action": "reply", "reply": "ready"}),
            reasoning={
                "schema": "vrcforge.llm_reasoning.v1",
                "provider": "ollama",
                "providerLabel": "Ollama",
                "model": "qwen3",
                "collapsedDefault": True,
                "itemCount": 1,
                "items": [{"title": "thinking", "kind": "thinking", "text": "visible model thinking"}],
            },
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/app/agent/message", json={"message": "hello model planner"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["plan"]["planner"], "llm")
        self.assertEqual(payload["reasoning"]["provider"], "ollama")
        self.assertTrue(payload["reasoning"]["collapsedDefault"])
        self.assertEqual(payload["reasoning"]["items"][0]["text"], "visible model thinking")

    @patch("dashboard_server.request_llm_plan_with_metadata")
    @patch.object(dashboard_server.PROVIDER_CONFIGURATION, "current_api_config")
    def test_agent_runtime_message_reports_provider_context_usage(
        self,
        mock_current_api_config,
        mock_request_llm_plan,
    ) -> None:
        mock_current_api_config.return_value = _test_runtime_provider_config(model="deepseek-v4-pro")
        mock_request_llm_plan.return_value = LlmPlanResponse(
            text=json.dumps({"action": "reply", "reply": "ready"}),
            reasoning={},
            usage={
                "schema": "vrcforge.provider_usage.v1",
                "provider": "deepseek",
                "providerLabel": "DeepSeek",
                "model": "deepseek-v4-pro",
                "source": "openai-compatible",
                "exact": True,
                "inputTokens": 1234,
                "outputTokens": 56,
                "totalTokens": 1290,
            },
        )

        history = [{"role": "user", "text": "first visible request"}]
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/agent/message",
                json={"message": "hello model planner", "history": history},
            )

        self.assertEqual(response.status_code, 200)
        usage = response.json()["contextUsage"]
        self.assertTrue(usage["exact"])
        self.assertEqual(usage["inputTokens"], 1234)
        self.assertEqual(usage["outputTokens"], 56)
        self.assertEqual(usage["totalTokens"], 1290)
        self.assertEqual(usage["cumulativeInputTokens"], 1234)
        self.assertEqual(usage["cumulativeOutputTokens"], 56)
        self.assertEqual(usage["cumulativeTotalTokens"], 1290)
        self.assertEqual(usage["lastInputTokens"], 1234)
        self.assertEqual(usage["lastOutputTokens"], 56)
        self.assertEqual(usage["lastTotalTokens"], 1290)
        self.assertEqual(usage["peakInputTokens"], 1234)
        self.assertEqual(usage["peakTotalTokens"], 1290)
        self.assertEqual(usage["sentHistoryEntryCount"], 1)
        self.assertGreater(usage["promptCharacterCount"], 0)

    def test_llm_context_usage_tracks_cumulative_last_and_peak_without_erasing_missing(self) -> None:
        usage: dict[str, object] = {}

        for tokens in (40_000, 45_000):
            dashboard_server.RUNTIME_PLANNER.record_context_usage(
                usage,
                "prompt",
                [{"role": "user", "text": "history"}],
                {
                    "exact": True,
                    "inputTokens": tokens,
                    "outputTokens": 0,
                    "totalTokens": tokens,
                },
            )

        self.assertEqual(usage["requestCount"], 2)
        self.assertEqual(usage["inputTokens"], 85_000)
        self.assertEqual(usage["totalTokens"], 85_000)
        self.assertEqual(usage["cumulativeInputTokens"], 85_000)
        self.assertEqual(usage["cumulativeTotalTokens"], 85_000)
        self.assertEqual(usage["lastInputTokens"], 45_000)
        self.assertEqual(usage["lastTotalTokens"], 45_000)
        self.assertEqual(usage["peakInputTokens"], 45_000)
        self.assertEqual(usage["peakTotalTokens"], 45_000)

        measured_values = {
            key: usage[key]
            for key in (
                "inputTokens",
                "outputTokens",
                "totalTokens",
                "cumulativeInputTokens",
                "cumulativeOutputTokens",
                "cumulativeTotalTokens",
                "lastInputTokens",
                "lastOutputTokens",
                "lastTotalTokens",
                "peakInputTokens",
                "peakTotalTokens",
            )
        }
        dashboard_server.RUNTIME_PLANNER.record_context_usage(usage, "missing", [], None)

        self.assertEqual(usage["requestCount"], 3)
        self.assertFalse(usage["exact"])
        self.assertEqual(usage["unavailableReason"], "provider_usage_missing")
        self.assertEqual({key: usage[key] for key in measured_values}, measured_values)

    @patch("dashboard_server.EVENT_BUS.broadcast_from_sync")
    @patch("dashboard_server.request_llm_plan_with_metadata")
    @patch.object(dashboard_server.PROVIDER_CONFIGURATION, "current_api_config")
    def test_agent_runtime_stream_callback_emits_summary_deltas(
        self,
        mock_current_api_config,
        mock_request_llm_plan,
        mock_broadcast,
    ) -> None:
        mock_current_api_config.return_value = _test_runtime_provider_config(model="qwen3")

        def fake_request(_settings, _prompt, stream_callback=None):
            self.assertIsNotNone(stream_callback)
            stream_callback('{"action":"reply","summary":"hel')
            stream_callback('lo"}')
            return LlmPlanResponse(
                text=json.dumps({"action": "reply", "summary": "hello"}),
                reasoning={},
                usage={},
            )

        mock_request_llm_plan.side_effect = fake_request
        dashboard_server.AGENT_GATEWAY._runtime_stream_context.value = {
            "sessionId": "sess-stream",
            "turnId": "turn-stream",
            "clientTurnId": "client-stream",
        }
        try:
            with dashboard_server.RUNTIME_PLANNER.bind_turn({}):
                payload = dashboard_server.RUNTIME_PLANNER.plan_agent_turn("hello", {}, {})
        finally:
            dashboard_server.AGENT_GATEWAY._runtime_stream_context.value = {}

        self.assertEqual(payload["summary"], "hello")
        events = [call.args for call in mock_broadcast.call_args_list]
        self.assertEqual(events[0][0], "agentRuntimeDelta")
        self.assertEqual(events[0][1]["textDelta"], "hel")
        self.assertEqual(events[0][1]["clientTurnId"], "client-stream")
        self.assertEqual(events[1][1]["textDelta"], "lo")
        self.assertEqual(events[-1][1]["done"], True)

    @patch("dashboard_server.EVENT_BUS.broadcast_from_sync")
    @patch("dashboard_server.request_llm_plan_with_metadata")
    @patch.object(dashboard_server.PROVIDER_CONFIGURATION, "current_api_config")
    def test_agent_runtime_stream_callback_continues_when_reply_replaces_summary_prefix(
        self,
        mock_current_api_config,
        mock_request_llm_plan,
        mock_broadcast,
    ) -> None:
        mock_current_api_config.return_value = ProviderApiConfig(
            provider="deepseek",
            api_key="test-key",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
        )

        def fake_request(_settings, _prompt, stream_callback=None):
            self.assertIsNotNone(stream_callback)
            stream_callback('{"summary":"hel"')
            stream_callback(',"reply":"hello wor')
            stream_callback('ld"}')
            return LlmPlanResponse(
                text=json.dumps({"action": "reply", "reply": "hello world", "summary": "hel"}),
                reasoning={},
                usage={},
            )

        mock_request_llm_plan.side_effect = fake_request
        dashboard_server.AGENT_GATEWAY._runtime_stream_context.value = {
            "sessionId": "sess-stream-prefix",
            "turnId": "turn-stream-prefix",
            "clientTurnId": "client-stream-prefix",
        }
        try:
            with dashboard_server.RUNTIME_PLANNER.bind_turn({}):
                payload = dashboard_server.RUNTIME_PLANNER.plan_agent_turn("hello", {}, {})
        finally:
            dashboard_server.AGENT_GATEWAY._runtime_stream_context.value = {}

        self.assertEqual(payload["reply"], "hello world")
        delta_events = [call.args[1] for call in mock_broadcast.call_args_list if call.args[0] == "agentRuntimeDelta" and not call.args[1].get("done")]
        self.assertEqual([event["textDelta"] for event in delta_events], ["hel", "lo wor", "ld"])
        self.assertTrue(mock_broadcast.call_args_list[-1].args[1]["done"])

    def test_agent_runtime_prompt_uses_full_visible_dialogue_only(self) -> None:
        captured: dict[str, str] = {}
        def plan_fn(_settings, prompt: str, *, stream_callback=None) -> LlmPlanResponse:
            captured["prompt"] = prompt
            return LlmPlanResponse(
                text=json.dumps({"action": "reply", "reply": "done"}),
                usage={"exact": True, "inputTokens": 77, "outputTokens": 5, "totalTokens": 82},
                reasoning={},
            )

        long_tail = "visible-long-history-" + ("x" * 700) + "-tail-kept"
        history = [
            {"role": "user", "text": "first turn is still present", "tool": "DO_NOT_SEND_TOOL_FIELD"},
            {"role": "agent", "text": "assistant visible reply one", "reasoning": "DO_NOT_SEND_COT_FIELD"},
        ]
        for index in range(13):
            history.append({"role": "user", "text": f"middle user {index}"})
        history.append({"role": "agent", "text": long_tail, "shell": {"stdout": "DO_NOT_SEND_STDOUT_FIELD"}})

        try:
            with (
                patch.object(
                    dashboard_server.PROVIDER_CONFIGURATION,
                    "current_api_config",
                    return_value=_test_runtime_provider_config(),
                ),
                patch("dashboard_server.request_llm_plan_with_metadata", side_effect=plan_fn),
            ):
                payload = dashboard_server.AGENT_GATEWAY.runtime_message(
                    {
                    "message": "latest user message",
                    "session_id": "sess-full-visible-history",
                    "history": history,
                    }
                )
        finally:
            dashboard_server.AGENT_GATEWAY._runtime_sessions.pop("sess-full-visible-history", None)

        self.assertTrue(payload["ok"])
        prompt = captured["prompt"]
        self.assertIn("first turn is still present", prompt)
        self.assertIn("middle user 0", prompt)
        self.assertIn("middle user 12", prompt)
        self.assertIn("-tail-kept", prompt)
        self.assertIn("latest user message", prompt)
        self.assertNotIn("DO_NOT_SEND_TOOL_FIELD", prompt)
        self.assertNotIn("DO_NOT_SEND_COT_FIELD", prompt)
        self.assertNotIn("DO_NOT_SEND_STDOUT_FIELD", prompt)

    def test_agent_runtime_prompt_filters_project_memory_by_project_root(self) -> None:
        captured: list[str] = []
        try:
            dashboard_server.AGENT_GATEWAY.create_agent_memory(
                {"scope": "project", "kind": "style", "text": "ProjectA private memory", "projectRoot": "ProjectA"}
            )
            dashboard_server.AGENT_GATEWAY.create_agent_memory(
                {"scope": "project", "kind": "style", "text": "ProjectB private memory", "projectRoot": "ProjectB"}
            )
            dashboard_server.AGENT_GATEWAY.create_agent_memory(
                {"scope": "user", "kind": "preference", "text": "Global user memory"}
            )

            def plan_fn(_settings, prompt: str, *, stream_callback=None) -> LlmPlanResponse:
                captured.append(prompt)
                return LlmPlanResponse(
                    text=json.dumps({"action": "reply", "reply": "done"}),
                    usage={},
                    reasoning={},
                )

            with (
                patch.object(
                    dashboard_server.PROVIDER_CONFIGURATION,
                    "current_api_config",
                    return_value=_test_runtime_provider_config(),
                ),
                patch("dashboard_server.request_llm_plan_with_metadata", side_effect=plan_fn),
            ):
                dashboard_server.AGENT_GATEWAY.runtime_message(
                    {"message": "use scoped memory", "session_id": "sess-memory-project-a", "projectRoot": "ProjectA"}
                )
                dashboard_server.AGENT_GATEWAY.runtime_message(
                    {"message": "use only user memory", "session_id": "sess-memory-no-project"}
                )
        finally:
            dashboard_server.AGENT_GATEWAY._runtime_sessions.pop("sess-memory-project-a", None)
            dashboard_server.AGENT_GATEWAY._runtime_sessions.pop("sess-memory-no-project", None)

        self.assertEqual(len(captured), 2)
        project_prompt, no_project_prompt = captured
        self.assertIn("ProjectA private memory", project_prompt)
        self.assertIn("Global user memory", project_prompt)
        self.assertNotIn("ProjectB private memory", project_prompt)
        self.assertIn("Global user memory", no_project_prompt)
        self.assertNotIn("ProjectA private memory", no_project_prompt)
        self.assertNotIn("ProjectB private memory", no_project_prompt)

    def test_llm_planner_prompt_uses_bounded_semantic_loop_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            planner = bind_test_runtime_planner(
                gateway,
                lambda _prompt: {"action": "reply", "reply": "unused"},
            )
            prompt = planner._build_llm_plan_prompt(
                "continue after tool execution",
                history=[],
                loop_state=[
                    {
                        "tool": "shell",
                        "kind": "shell",
                        "status": "executed",
                        "result": {
                            "ok": True,
                            "exitCode": 0,
                            "timedOut": False,
                            "stdoutSummary": "Indexed 3 materials at C:\\Users\\Private\\Avatar; token=secret-value",
                            "resultSummary": {
                                "materialCount": 3,
                                **{f"phase{index}Count": index for index in range(20)},
                                "summary": "Three materials are ready for the next step.",
                                "summary_text": "Safe snake-case summaries are accepted.",
                                "discount": 99,
                                "payload": {"raw": "DO_NOT_SEND_NESTED_PAYLOAD"},
                                "dataUrl": "DO_NOT_SEND_DATA_URL",
                            },
                            "stderrSummary": "warning: cache is stale",
                            "warnings": ["first safe warning", "second safe warning"],
                            "message": "X" * 10_000,
                            "payload": {"raw": "RAW_PAYLOAD_MARKER" * 20_000},
                        },
                    },
                    {
                        "tool": "vrcforge_test_tool",
                        "kind": "skill",
                        "status": "failed",
                        "result": {
                            "ok": False,
                            "code": "missing_parameter",
                            "error": "missing avatar path",
                            "data": {"raw": "DO_NOT_SEND_DATA"},
                        },
                    },
                ],
            )
            observation = planner._llm_loop_step_observation(
                {
                    "tool": "shell",
                    "result": {
                        "ok": True,
                        "summary": "S" * 10_000,
                        "payload": {"raw": "RAW_PAYLOAD_MARKER" * 20_000},
                    },
                }
            )

        self.assertIn("shell", prompt)
        self.assertIn("exitCode=0", prompt)
        self.assertIn("Indexed 3 materials", prompt)
        self.assertIn("Three materials are ready", prompt)
        self.assertIn("materialCount", prompt)
        self.assertIn("Safe snake-case summaries", prompt)
        self.assertIn("first safe warning", prompt)
        self.assertIn("cache is stale", prompt)
        self.assertIn("<path redacted>", prompt)
        self.assertIn("token=<redacted>", prompt)
        self.assertIn("missing_parameter", prompt)
        self.assertIn("missing avatar path", prompt)
        self.assertNotIn("secret-value", prompt)
        self.assertNotIn("C:\\Users\\Private\\Avatar", prompt)
        self.assertNotIn("RAW_PAYLOAD_MARKER", prompt)
        self.assertNotIn("DO_NOT_SEND_NESTED_PAYLOAD", prompt)
        self.assertNotIn("DO_NOT_SEND_DATA_URL", prompt)
        self.assertNotIn("DO_NOT_SEND_DATA", prompt)
        self.assertNotIn("discount", prompt)
        self.assertLessEqual(prompt.count("X"), 600)
        self.assertLessEqual(len(observation), RUNTIME_PLANNER_TOOL_OBSERVATION_MAX_CHARS)

    def test_agent_runtime_routes_read_skill_without_shell(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/app/agent/message", json={"message": "检查 Unity MCP 状态"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["plan"]["shellNeeded"])
        self.assertTrue(payload["plan"]["skillNeeded"])
        self.assertEqual(payload["plan"]["skillTool"], "vrcforge_unity_status")
        self.assertEqual(payload["plan"]["nextStep"], "call_skill")
        self.assertEqual(payload["skill"]["tool"], "vrcforge_unity_status")
        self.assertEqual(payload["skill"]["status"], "executed")
        self.assertIn("result", payload["skill"])

    def test_agent_runtime_routes_skill_manifest_request(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/app/agent/message", json={"message": "列一下 skills"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["plan"]["skillTool"], "vrcforge_skill_manifest")
        self.assertEqual(payload["skill"]["status"], "executed")
        self.assertGreater(payload["skill"]["result"]["toolCount"], 10)
        self.assertNotIn("token", payload["skill"]["result"])

    def test_app_skill_registry_crud_uses_local_skill_markdown(self) -> None:
        with TestClient(dashboard_server.app) as client:
            initial = client.get("/api/app/skills")
            self.assertEqual(initial.status_code, 200)
            initial_payload = initial.json()
            self.assertEqual(initial_payload["schema"], "vrcforge.skills.v1")
            builtin_names = {skill["name"] for skill in initial_payload["skills"] if skill["source"] == "builtin"}
            self.assertNotIn("vrcforge_roslyn_status", builtin_names)
            self.assertNotIn("roslyn-advanced-power", builtin_names)
            self.assertIn("runtime-diagnostics", builtin_names)
            runtime_skill = next(skill for skill in initial_payload["skills"] if skill["name"] == "runtime-diagnostics")
            self.assertEqual(runtime_skill["skillType"], "group")
            self.assertIn("vrcforge_skill_check", runtime_skill["allowedTools"])

            created = client.post(
                "/api/app/skills",
                json={
                    "name": "avatar-review",
                    "title": "Avatar Review",
                    "description": "Check avatar state before edits.",
                    "whenToUse": "avatar review",
                    "inputs": ["Unity project context"],
                    "outputs": ["Review notes"],
                    "allowedTools": ["vrcforge_unity_status", "vrcforge_list_avatars"],
                    "disallowedTools": ["vrcforge_execute_shell"],
                    "entrypointTool": "vrcforge_unity_status",
                    "argumentHint": "avatar path",
                    "instructions": "Inspect Unity status before suggesting writes.",
                },
            )
            self.assertEqual(created.status_code, 200)
            created_payload = created.json()
            self.assertEqual(created_payload["skill"]["name"], "avatar-review")
            skill_file = dashboard_server.AGENT_GATEWAY.user_skills_dir / "avatar-review" / "SKILL.md"
            self.assertTrue(skill_file.exists())
            skill_text = skill_file.read_text(encoding="utf-8")
            self.assertIn("allowed-tools:", skill_text)
            self.assertIn("disallowed-tools:", skill_text)
            self.assertIn("entrypoint-tool: vrcforge_unity_status", skill_text)
            self.assertIn("Inspect Unity status", skill_text)

            check = client.get("/api/app/skills/check")
            self.assertEqual(check.status_code, 200)
            self.assertGreaterEqual(check.json()["count"], initial_payload["count"])

            turn = client.post(
                "/api/app/agent/message",
                json={"message": "/avatar-review Scene/Hero"},
            )
            self.assertEqual(turn.status_code, 200)
            self.assertEqual(turn.json()["skill"]["status"], "executed")
            self.assertEqual(turn.json()["skill"]["result"]["name"], "avatar-review")
            self.assertEqual(turn.json()["skill"]["result"]["arguments"], "Scene/Hero")
            self.assertEqual(turn.json()["skill"]["entrypointTool"], "vrcforge_unity_status")

            updated = client.put("/api/app/skills/avatar-review", json={"title": "Avatar Review Updated"})
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["skill"]["title"], "Avatar Review Updated")

            deleted = client.delete("/api/app/skills/avatar-review")
            self.assertEqual(deleted.status_code, 200)
            self.assertFalse(skill_file.exists())

    def test_skill_markdown_hyphen_frontmatter_and_dependency_check(self) -> None:
        skill_dir = dashboard_server.AGENT_GATEWAY.user_skills_dir / "hyphen-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "\n".join(
                [
                    "---",
                    "name: hyphen-skill",
                    "title: Hyphen Skill",
                    "permission-mode: read-only",
                    "risk-level: low",
                    "when-to-use: hyphen skill",
                    "allowed-tools:",
                    "  - vrcforge_health",
                    "entrypoint-tool: vrcforge_health",
                    "argument-hint: target",
                    "requires-env:",
                    "  - VRCFORGE_TEST_MISSING_ENV",
                    "supported-os:",
                    "  - windows",
                    "disable-model-invocation: true",
                    "---",
                    "Use $ARGUMENTS safely.",
                ]
            ),
            encoding="utf-8",
        )
        with TestClient(dashboard_server.app) as client:
            skills = client.get("/api/app/skills").json()["skills"]
            skill = next(item for item in skills if item["name"] == "hyphen-skill")
            self.assertEqual(skill["permissionMode"], "read_only")
            self.assertEqual(skill["entrypointTool"], "vrcforge_health")
            self.assertTrue(skill["disableModelInvocation"])
            self.assertEqual(skill["argumentHint"], "target")
            self.assertEqual(skill["validation"]["status"], "error")
            self.assertIn("missing env", "; ".join(skill["validation"]["reasons"]))

            check = client.get("/api/app/skills/check")
            self.assertEqual(check.status_code, 200)
            check_skill = next(item for item in check.json()["checks"] if item["name"] == "hyphen-skill")
            self.assertEqual(check_skill["status"], "error")

    def test_shell_classifier_low_high_and_reject_cases(self) -> None:
        workspace_root = str(Path(__file__).resolve().parents[1])

        low = dashboard_server.AGENT_GATEWAY.shell.classify(
            {"command": "git --no-pager status --short", "workspace_root": workspace_root}
        )
        self.assertEqual(low["risk"], "low")

        rg_low = dashboard_server.AGENT_GATEWAY.shell.classify(
            {"command": "rg TODO .", "workspace_root": workspace_root}
        )
        self.assertEqual(rg_low["risk"], "low")

        git_show_low = dashboard_server.AGENT_GATEWAY.shell.classify(
            {"command": "git show --stat HEAD", "workspace_root": workspace_root}
        )
        self.assertEqual(git_show_low["risk"], "low")

        high = dashboard_server.AGENT_GATEWAY.shell.classify(
            {"command": "Set-Content test.txt hi", "workspace_root": workspace_root}
        )
        self.assertEqual(high["risk"], "high")

        home_path = dashboard_server.AGENT_GATEWAY.shell.classify(
            {"command": "Get-Content ~\\.codex\\auth.json", "workspace_root": workspace_root}
        )
        self.assertEqual(home_path["risk"], "high")

        root_relative = dashboard_server.AGENT_GATEWAY.shell.classify(
            {"command": "Get-Content \\Windows\\win.ini", "workspace_root": workspace_root}
        )
        self.assertEqual(root_relative["risk"], "high")

        rg_preprocessor = dashboard_server.AGENT_GATEWAY.shell.classify(
            {"command": "rg --pre powershell TODO .", "workspace_root": workspace_root}
        )
        self.assertEqual(rg_preprocessor["risk"], "high")

        git_show_output = dashboard_server.AGENT_GATEWAY.shell.classify(
            {"command": "git show --stat --output=leak.txt HEAD", "workspace_root": workspace_root}
        )
        self.assertEqual(git_show_output["risk"], "high")

        redirected = dashboard_server.AGENT_GATEWAY.shell.classify(
            {"command": "Get-Content a.txt > b.txt", "workspace_root": workspace_root}
        )
        self.assertEqual(redirected["risk"], "high")

        chained = dashboard_server.AGENT_GATEWAY.shell.classify(
            {"command": "Get-ChildItem; Remove-Item test.txt", "workspace_root": workspace_root}
        )
        self.assertEqual(chained["risk"], "high")

        rejected = dashboard_server.AGENT_GATEWAY.shell.classify({"command": "", "workspace_root": workspace_root})
        self.assertEqual(rejected["risk"], "reject")


    def test_windows_process_tree_kill_falls_back_when_taskkill_is_rejected(self) -> None:
        process = Mock()
        process.pid = 4242
        process.poll.side_effect = [None, None]
        with (
            patch("agent_shell_service.os.name", "nt"),
            patch("agent_shell_service.subprocess.run", return_value=SimpleNamespace(returncode=1)) as taskkill,
        ):
            kill_process_tree(process)

        taskkill.assert_called_once()
        process.kill.assert_called_once_with()

    def test_windows_process_tree_kill_falls_back_when_taskkill_cannot_start(self) -> None:
        process = Mock()
        process.pid = 4242
        process.poll.side_effect = [None, None]
        with (
            patch("agent_shell_service.os.name", "nt"),
            patch("agent_shell_service.subprocess.run", side_effect=PermissionError) as taskkill,
        ):
            kill_process_tree(process)

        taskkill.assert_called_once()
        process.kill.assert_called_once_with()

    def test_native_shell_argv_accepts_plain_commands_only(self) -> None:
        argv = native_shell_argv("git --no-pager status --short")
        self.assertIsNotNone(argv)
        assert argv is not None
        self.assertEqual(argv[1:], ["--no-pager", "status", "--short"])
        self.assertIn("git", Path(argv[0]).name.lower())

        # PowerShell cmdlets/aliases never resolve to a real executable.
        self.assertIsNone(native_shell_argv("Get-ChildItem"))
        # Pipeline, redirection, variables, formats stay on PowerShell semantics.
        self.assertIsNone(native_shell_argv("git status | Out-Null"))
        self.assertIsNone(native_shell_argv("Get-Content a.txt > b.txt"))
        self.assertIsNone(native_shell_argv("echo $env:PATH"))
        self.assertIsNone(native_shell_argv("git log --format=%h"))
        self.assertIsNone(native_shell_argv("git status; git log"))
        self.assertIsNone(native_shell_argv("git status\ngit log"))

    def test_shell_classification_reports_planned_runner(self) -> None:
        workspace_root = str(Path(__file__).resolve().parents[1])
        native = dashboard_server.AGENT_GATEWAY.shell.classify(
            {"command": "git --no-pager status --short", "workspace_root": workspace_root}
        )
        self.assertEqual(native["plannedRunner"], SHELL_RUNNER_NATIVE)

        cmdlet = dashboard_server.AGENT_GATEWAY.shell.classify(
            {"command": "Get-ChildItem", "workspace_root": workspace_root}
        )
        self.assertEqual(cmdlet["plannedRunner"], SHELL_RUNNER_POWERSHELL)


    def test_resolve_powershell_executable_returns_nonempty_path(self) -> None:
        resolved = resolve_powershell_executable()
        self.assertIsInstance(resolved, str)
        self.assertTrue(resolved)

    def test_agent_runtime_shell_direct_and_approval_execution(self) -> None:
        shell_process_ports, shell_processes = fake_dashboard_shell_process_ports()
        original_process_ports = dashboard_server.AGENT_GATEWAY.shell._process
        dashboard_server.AGENT_GATEWAY.shell._process = shell_process_ports
        self.addCleanup(setattr, dashboard_server.AGENT_GATEWAY.shell, "_process", original_process_ports)
        with tempfile.TemporaryDirectory() as workspace:
            for directory in ("Assets", "Packages", "ProjectSettings"):
                (Path(workspace) / directory).mkdir()
            target = Path(workspace) / "Assets" / "agent-loop.txt"
            with TestClient(dashboard_server.app) as client:
                low = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "列目录",
                        "workspace_root": workspace,
                        "cwd": workspace,
                    },
                )
                self.assertEqual(low.status_code, 200)
                self.assertEqual(low.json()["shell"]["status"], "executed")
                self.assertEqual(low.json()["shell"]["classification"]["risk"], "low")

                high = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "写入测试文件",
                        "shell_command": "Set-Content -Path Assets/agent-loop.txt -Value hi -Encoding utf8",
                        "workspace_root": workspace,
                        "cwd": workspace,
                    },
                )
                self.assertEqual(high.status_code, 200)
                high_payload = high.json()
                self.assertEqual(high_payload["shell"]["status"], "pending_approval")
                self.assertFalse(target.exists())

                approval_id = high_payload["shell"]["approval_id"]
                with patch("dashboard_server.asyncio.to_thread", wraps=dashboard_server.asyncio.to_thread) as to_thread:
                    approved = client.post(f"/api/app/agent/approvals/{approval_id}/approve")
                self.assertEqual(approved.status_code, 200)
                approved_payload = approved.json()
                self.assertTrue(approved_payload["ok"])
                self.assertEqual(approved_payload["execution"]["status"], "applied")
                self.assertFalse(target.exists())
                self.assertEqual(len(shell_processes), 2)
                self.assertTrue(
                    any(
                        call.args and getattr(call.args[0], "__name__", "") == "approve_and_execute"
                        for call in to_thread.call_args_list
                    )
                )

                replay = client.post(f"/api/app/agent/approvals/{approval_id}/approve")
                self.assertEqual(replay.status_code, 200)
                self.assertFalse(replay.json()["ok"])

    def test_app_approval_revision_supersedes_pending_approval(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "revision.txt"
            with TestClient(dashboard_server.app) as client:
                high = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "write test file",
                        "shell_command": "Set-Content -Path revision.txt -Value hi -Encoding utf8",
                        "workspace_root": workspace,
                        "cwd": workspace,
                    },
                )
                self.assertEqual(high.status_code, 200)
                approval_id = high.json()["shell"]["approval_id"]

                revision = client.post(
                    f"/api/app/agent/approvals/{approval_id}/revision",
                    json={"reason": "change request", "note": "use another name"},
                )
                self.assertEqual(revision.status_code, 200)
                revision_payload = revision.json()
                self.assertTrue(revision_payload["ok"])
                self.assertEqual(revision_payload["approval"]["status"], "revision_requested")
                self.assertEqual(revision_payload["approval"]["revisionReason"], "change request")
                self.assertFalse(target.exists())

                stale_approval = client.post(f"/api/app/agent/approvals/{approval_id}/approve")
                self.assertEqual(stale_approval.status_code, 200)
                self.assertFalse(stale_approval.json()["ok"])
                self.assertFalse(target.exists())

    def test_app_approval_reject_does_not_rewrite_terminal_status(self) -> None:
        shell_process_ports, shell_processes = fake_dashboard_shell_process_ports()
        original_process_ports = dashboard_server.AGENT_GATEWAY.shell._process
        dashboard_server.AGENT_GATEWAY.shell._process = shell_process_ports
        self.addCleanup(setattr, dashboard_server.AGENT_GATEWAY.shell, "_process", original_process_ports)
        with tempfile.TemporaryDirectory() as workspace:
            for directory in ("Assets", "Packages", "ProjectSettings"):
                (Path(workspace) / directory).mkdir()
            target = Path(workspace) / "Assets" / "terminal.txt"
            with TestClient(dashboard_server.app) as client:
                high = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "write test file",
                        "shell_command": "Set-Content -Path Assets/terminal.txt -Value hi -Encoding utf8",
                        "workspace_root": workspace,
                        "cwd": workspace,
                    },
                )
                self.assertEqual(high.status_code, 200)
                approval_id = high.json()["shell"]["approval_id"]
                approved = client.post(f"/api/app/agent/approvals/{approval_id}/approve")
                self.assertEqual(approved.status_code, 200)
                self.assertTrue(approved.json()["ok"])
                self.assertFalse(target.exists())
                self.assertEqual(len(shell_processes), 1)

                rejected = client.post(f"/api/app/agent/approvals/{approval_id}/reject")
                self.assertEqual(rejected.status_code, 200)
                rejected_payload = rejected.json()
                self.assertFalse(rejected_payload["ok"])
                self.assertEqual(rejected_payload["approval"]["status"], "applied")

    def test_agent_gateway_preview_and_empty_apply_fails_before_approval(self) -> None:
        temp_project = tempfile.TemporaryDirectory()
        self.addCleanup(temp_project.cleanup)
        project = Path(temp_project.name) / "UnityProject"
        (project / "Assets").mkdir(parents=True)
        (project / "Packages").mkdir()
        (project / "ProjectSettings").mkdir()
        (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
        (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")
        original_prepare = dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler
        dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = lambda _root: {"ok": True}
        self.addCleanup(setattr, dashboard_server.AGENT_GATEWAY, "checkpoint_prepare_handler", original_prepare)

        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            preview = client.post(
                "/api/agent/tool/vrcforge_preview_blendshape_apply",
                headers=headers,
                json={
                    "agent_name": "codex-test",
                    "params": {
                        "avatar_path": "Scene/Avatar",
                        "adjustments": [
                            {"rendererPath": "Scene/Avatar/Face", "blendshapeName": "Smile", "targetWeight": 42}
                        ],
                    },
                },
            )
            self.assertEqual(preview.status_code, 200)
            self.assertTrue(preview.json()["ok"])
            self.assertIn("vrc_apply_blendshapes", preview.json()["result"]["applyPayload"])

            request_apply = client.post(
                "/api/agent/tool/vrcforge_request_apply",
                headers=headers,
                json={
                    "agent_name": "codex-test",
                    "params": {
                        "target_tool": "vrcforge_apply_blendshapes",
                        "arguments": {"projectRoot": str(project), "adjustments": []},
                        "reason": "test supervised loop",
                    },
                },
            )
            self.assertEqual(request_apply.status_code, 200)
            self.assertFalse(request_apply.json()["ok"])
            self.assertIn("No blendshape adjustments", request_apply.json()["error"])

            approvals = client.get("/api/agent/approvals", headers=headers)
            self.assertEqual(approvals.status_code, 200)
            self.assertEqual(approvals.json()["count"], 0)

    def test_agent_gateway_manifest_hides_user_activated_computer_use_tool(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest?exposure_layer=execution", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        self.assertTrue(all("When to use:" in tool["description"] for tool in payload["tools"]))
        self.assertTrue(all("When NOT to use:" in tool["description"] for tool in payload["tools"]))
        self.assertTrue(all("Negative example:" in tool["description"] for tool in payload["tools"]))
        self.assertIn("vrcforge_agent_observe", tool_names)
        self.assertIn("vrcforge_agent_message", tool_names)
        self.assertNotIn("vrcforge_agent_desktop_action", tool_names)
        self.assertIn("vrcforge_progress_replace", tool_names)
        self.assertIn("vrcforge_progress_update", tool_names)
        self.assertIn("vrcforge_progress_delete", tool_names)
        self.assertIn("vrcforge_ask_user", tool_names)
        self.assertIn("vrcforge_classify_shell", tool_names)
        self.assertIn("vrcforge_execute_shell", tool_names)
        self.assertNotIn("vrcforge_execute_approved_shell", tool_names)
        self.assertIn("vrcforge_skill_manifest", tool_names)
        self.assertIn("vrcforge_tool_registry", tool_names)
        self.assertIn("vrcforge_external_agent_connectors", tool_names)
        self.assertIn("vrcforge_list_skill_packages", tool_names)
        self.assertIn("vrcforge_preflight_skill_package", tool_names)
        self.assertNotIn("vrcforge_capture_screenshot", tool_names)
        self.assertIn("vrcforge_vision_audit", tool_names)
        self.assertNotIn("vrcforge_roslyn_status", tool_names)
        self.assertIn("vrcforge_get_compile_errors", tool_names)
        self.assertIn("vrcforge_request_apply", tool_names)
        self.assertIn("vrcforge_tool_registry", tool_names)
        self.assertNotIn("vrcforge_apply_approved", tool_names)
        self.assertNotIn("vrcforge_execute_approved_shell", tool_names)
        self.assertIn("vrcforge_read_recent_logs", tool_names)
        write_targets = {item["name"] for item in payload["writeTargets"]}
        self.assertIn("vrcforge_apply_blendshapes", write_targets)
        self.assertIn("vrcforge_import_skill_package", write_targets)
        self.assertIn("vrcforge_export_skill_package", write_targets)
        self.assertIn("vrcforge_set_skill_package_enabled", write_targets)
        self.assertIn("vrcforge_uninstall_skill_package", write_targets)
        self.assertNotIn("vrcforge_import_skill_package", tool_names)
        self.assertNotIn("vrcforge_export_skill_package", tool_names)
        self.assertNotIn("vrcforge_set_skill_package_enabled", tool_names)
        self.assertNotIn("vrcforge_uninstall_skill_package", tool_names)
        self.assertNotIn("api_key", json.dumps(payload).lower())
        self.assertNotIn("approval_token", json.dumps(payload).lower())

    def test_external_agent_connector_endpoint_uses_env_placeholder(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/external-agent/connectors",
                json={
                    "serverName": "vrcforge_local",
                    "tokenEnvVar": "CUSTOM_VRCFORGE_TOKEN",
                    "mcpUrl": "http://127.0.0.1:8757/mcp",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        rendered = json.dumps(payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mcp"]["url"], "http://127.0.0.1:8757/mcp")
        stdio = payload["clientConfigs"]["codexStdio"]["config"]["mcp_servers"]["vrcforge_local"]
        self.assertEqual(Path(stdio["cwd"]), dashboard_server.ROOT_DIR)
        self.assertEqual(Path(stdio["args"][0]), dashboard_server.ROOT_DIR / "tools" / "vrcforge_agent_mcp_stdio.py")
        self.assertIn("--no-start", stdio["args"])
        self.assertIn("CUSTOM_VRCFORGE_TOKEN", rendered)
        self.assertNotIn("real-token", rendered)

    def test_external_agent_connector_prefers_packaged_backend_stdio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend_dir = root / "backend"
            backend_dir.mkdir()
            backend_exe = backend_dir / "vrcforge_backend.exe"
            backend_exe.write_text("", encoding="utf-8")

            with patch("dashboard_server.ROOT_DIR", root):
                payload = dashboard_server.connector_bundle_sync({})

        stdio = payload["clientConfigs"]["codexStdio"]["config"]["mcp_servers"]["vrcforge"]
        self.assertEqual(Path(stdio["command"]), backend_exe)
        self.assertEqual(stdio["args"], ["--agent-mcp-stdio", "--no-start"])
        self.assertEqual(Path(stdio["cwd"]), root)

    def test_external_agent_connector_status_uses_project_query_for_claude_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "Unity Project"
            project.mkdir()
            (project / ".mcp.json").write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "vrcforge": {
                                "command": "vrcforge_backend.exe",
                                "args": ["--agent-mcp-stdio"],
                                "env": {},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with TestClient(dashboard_server.app) as client:
                without_project = client.get("/api/app/external-agent/connectors")
                with_project = client.get(
                    "/api/app/external-agent/connectors",
                    params={"projectPath": str(project)},
                )

        self.assertEqual(without_project.status_code, 200)
        self.assertEqual(with_project.status_code, 200)
        self.assertFalse(without_project.json()["clients"]["claudeCode"]["installed"])
        self.assertTrue(with_project.json()["clients"]["claudeCode"]["installed"])
        self.assertTrue(with_project.json()["clients"]["claudeCode"]["installable"])
        self.assertEqual(Path(with_project.json()["clients"]["claudeCode"]["configPath"]), project / ".mcp.json")

    def test_external_agent_generic_install_requires_config_path(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/external-agent/connectors/install",
                json={"client": "generic"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        action = payload["lastConnectorAction"]
        self.assertFalse(action["ok"])
        self.assertEqual(action["client"], "generic")
        self.assertEqual(action["action"], "install")
        self.assertEqual(action["stage"], "resolve_config_path")
        self.assertIn("generic", payload["clients"])
        self.assertEqual(payload["clients"]["generic"]["scope"], "custom")
        self.assertTrue(payload["clients"]["generic"]["requiresConfigPath"])

    def test_external_agent_gateway_settings_update_and_revoke_token(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        original_token = config.token

        with TestClient(dashboard_server.app) as client:
            enabled = client.post(
                "/api/app/external-agent/gateway",
                json={"enabled": True, "allowWriteRequests": False},
            )
            revoked = client.post("/api/app/external-agent/gateway", json={"revokeToken": True})
            old_token_manifest = client.get(
                "/api/agent/manifest",
                headers={"Authorization": f"Bearer {original_token}"},
            )

        self.assertEqual(enabled.status_code, 200)
        self.assertTrue(enabled.json()["gateway"]["enabled"])
        self.assertFalse(enabled.json()["gateway"]["allowWriteRequests"])
        self.assertEqual(revoked.status_code, 200)
        self.assertTrue(revoked.json()["gateway"]["tokenConfigured"])
        self.assertEqual(old_token_manifest.status_code, 401)
        serialized = json.dumps(revoked.json()).lower()
        self.assertNotIn("approval_token", serialized)
        self.assertNotIn(original_token.lower(), serialized)

    def test_external_agent_gateway_checkpoint_archive_limit_prunes_old_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            archive_dir = gateway.checkpoint_store_dir / "project"
            archive_dir.mkdir(parents=True)
            base_time = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
            paths: list[Path] = []
            for index in range(3):
                path = archive_dir / f"ckpt_{index}.zip"
                path.write_bytes(b"x" * 700_000)
                os.utime(path, (base_time + index, base_time + index))
                paths.append(path)

            original_gateway = dashboard_server.AGENT_GATEWAY
            try:
                dashboard_server.AGENT_GATEWAY = gateway
                with TestClient(dashboard_server.app) as client:
                    response = client.post("/api/app/external-agent/gateway", json={"checkpointArchiveMaxSizeMb": 1})
            finally:
                dashboard_server.AGENT_GATEWAY = original_gateway

            payload = response.json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["gateway"]["checkpointArchiveMaxSizeMb"], 1)
            self.assertEqual(payload["gateway"]["checkpointArchivePrune"]["deletedCount"], 1)
            self.assertFalse(paths[0].exists())
            self.assertTrue(paths[1].exists())
            self.assertTrue(paths[2].exists())
            self.assertGreater(payload["gateway"]["checkpointArchiveUsage"]["sizeBytes"], 1_048_576)

    def test_external_agent_gateway_checkpoint_archive_default_limit_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            config = gateway.ensure_config()

            self.assertEqual(config.checkpoint_archive_max_size_mb, CHECKPOINT_ARCHIVE_DEFAULT_MAX_SIZE_MB)
            self.assertEqual(gateway.checkpoint_archive_usage(config)["maxSizeMb"], CHECKPOINT_ARCHIVE_DEFAULT_MAX_SIZE_MB)

    def test_external_agent_gateway_checkpoint_ledgers_are_fsynced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")

            with patch("agent_gateway.os.fsync") as fsync:
                gateway._append_checkpoint({"id": "ckpt_fsync", "ok": True})
                gateway._append_apply_recovery_entry({"id": "rec_fsync", "checkpointId": "ckpt_fsync", "status": "applying"})

            self.assertGreaterEqual(fsync.call_count, 2)

    def test_external_agent_gateway_checkpoint_archive_limit_zero_keeps_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            archive_dir = gateway.checkpoint_store_dir / "project"
            archive_dir.mkdir(parents=True)
            archive_path = archive_dir / "ckpt_keep.zip"
            archive_path.write_bytes(b"x" * 700_000)

            original_gateway = dashboard_server.AGENT_GATEWAY
            try:
                dashboard_server.AGENT_GATEWAY = gateway
                with TestClient(dashboard_server.app) as client:
                    response = client.post("/api/app/external-agent/gateway", json={"checkpointArchiveMaxSizeMb": 0})
            finally:
                dashboard_server.AGENT_GATEWAY = original_gateway

            payload = response.json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["gateway"]["checkpointArchiveMaxSizeMb"], 0)
            self.assertEqual(payload["gateway"]["checkpointArchivePrune"]["deletedCount"], 0)
            self.assertTrue(archive_path.exists())

    def test_external_agent_gateway_relocate_checkpoint_archives_rewrites_paths(self) -> None:
        # 安全关键：检查点把绝对 archivePath 持久化在 checkpoints.jsonl，迁移必须复制 ZIP
        # 并改写记录，否则回滚会找不到存档。这里证明改写后的路径指向新目录里真实存在的文件。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            old_dir = gateway.checkpoint_store_dir / "project"
            old_dir.mkdir(parents=True)
            old_zip = old_dir / "ckpt_relocate.zip"
            old_zip.write_bytes(b"x" * 1024)
            gateway.checkpoint_log_path.parent.mkdir(parents=True, exist_ok=True)
            gateway.checkpoint_log_path.write_text(
                json.dumps(
                    {"id": "ckpt_relocate", "archivePath": str(old_zip), "strategy": "archive"}
                )
                + "\n",
                encoding="utf-8",
            )
            new_root = root / "moved-archives"

            original_gateway = dashboard_server.AGENT_GATEWAY
            try:
                dashboard_server.AGENT_GATEWAY = gateway
                with TestClient(dashboard_server.app) as client:
                    response = client.post(
                        "/api/app/external-agent/gateway",
                        json={"checkpointArchiveDirectory": str(new_root)},
                    )
            finally:
                dashboard_server.AGENT_GATEWAY = original_gateway

            payload = response.json()
            self.assertEqual(response.status_code, 200)
            relocate = payload["gateway"]["checkpointArchiveRelocate"]
            self.assertTrue(relocate["ok"])
            self.assertEqual(relocate["copiedCount"], 1)
            self.assertEqual(relocate["rewrittenCount"], 1)

            new_zip = new_root / "project" / "ckpt_relocate.zip"
            self.assertTrue(new_zip.exists())
            self.assertFalse(old_zip.exists())
            self.assertEqual(gateway.checkpoint_store_dir.resolve(), new_root.resolve())

            record = json.loads(gateway.checkpoint_log_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(Path(record["archivePath"]).resolve(), new_zip.resolve())

    def test_external_agent_gateway_relocate_blocked_by_active_recovery(self) -> None:
        # 有未结的写入恢复时迁移必须被安全闸拒绝，否则迁移途中回滚会找不到旧存档。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            old_dir = gateway.checkpoint_store_dir / "project"
            old_dir.mkdir(parents=True)
            old_zip = old_dir / "ckpt_guard.zip"
            old_zip.write_bytes(b"x" * 1024)
            gateway._append_apply_recovery_entry(
                {"id": "rec_1", "checkpointId": "ckpt_guard", "status": "needs_recovery"}
            )
            new_root = root / "moved-archives"

            result = gateway.relocate_checkpoint_archives(str(new_root))

            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "active_recovery")
            self.assertTrue(old_zip.exists())
            self.assertFalse((new_root / "project" / "ckpt_guard.zip").exists())

    def test_external_agent_gateway_delete_checkpoint_archives_protects_active_recovery(self) -> None:
        # 多选清理时，活跃恢复检查点对应的存档必须被强制保护、跳过不删。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            archive_dir = gateway.checkpoint_store_dir / "project"
            archive_dir.mkdir(parents=True)
            base_time = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
            keep = archive_dir / "ckpt_protected.zip"
            drop = archive_dir / "ckpt_free.zip"
            recent = archive_dir / "ckpt_recent.zip"
            keep.write_bytes(b"x" * 1024)
            drop.write_bytes(b"x" * 1024)
            recent.write_bytes(b"x" * 1024)
            os.utime(drop, (base_time, base_time))
            os.utime(keep, (base_time + 1, base_time + 1))
            os.utime(recent, (base_time + 2, base_time + 2))
            gateway._append_apply_recovery_entry(
                {"id": "rec_2", "checkpointId": "ckpt_protected", "status": "applying"}
            )

            original_gateway = dashboard_server.AGENT_GATEWAY
            try:
                dashboard_server.AGENT_GATEWAY = gateway
                with TestClient(dashboard_server.app) as client:
                    response = client.post(
                        "/api/app/external-agent/gateway",
                        json={"deleteCheckpointArchiveIds": ["ckpt_protected", "ckpt_free"]},
                    )
            finally:
                dashboard_server.AGENT_GATEWAY = original_gateway

            payload = response.json()
            self.assertEqual(response.status_code, 200)
            delete = payload["gateway"]["checkpointArchiveDelete"]
            self.assertTrue(delete["ok"])
            self.assertEqual(delete["deletedCount"], 1)
            self.assertIn("ckpt_protected", delete["protectedSkipped"])
            self.assertTrue(keep.exists())
            self.assertTrue(recent.exists())
            self.assertFalse(drop.exists())

    def test_skill_package_import_projects_skill_and_export_endpoint_builds_vsk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "package-source"
            source.mkdir()
            manifest = {
                "id": "community.avatar-review",
                "name": "Avatar Review Package",
                "skill_name": "avatar-review",
                "version": "1.0.0",
                "author": "Unit Test",
                "description": "Dashboard skill package fixture.",
                "min_vrcforge_version": "0.5.0",
                "permissions": ["read_project"],
                "entrypoints": {"skill": "SKILL.md"},
            }
            (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (source / "SKILL.md").write_text(
                "---\n"
                "name: avatar-review\n"
                "title: Avatar Review\n"
                "description: Imported package skill.\n"
                "allowed-tools:\n"
                "  - vrcforge_health\n"
                "entrypoint-tool: vrcforge_health\n"
                "---\n"
                "Inspect project state before edits.\n",
                encoding="utf-8",
            )
            package = SkillPackageService(root / "store", vrcforge_version="0.5.1").export_dev(
                source,
                root / "avatar-review.vsk",
            ).package_path

            with TestClient(dashboard_server.app) as client:
                preflight = client.post("/api/app/skill-packages/preflight", json={"packagePath": str(package)})
                imported = client.post("/api/app/skill-packages/import", json={"packagePath": str(package)})
                skills = client.get("/api/app/skills").json()["skills"]
                exported_path = root / "exported-avatar-review.vsk"
                exported = client.post(
                    "/api/app/skill-packages/export",
                    json={"skillName": "avatar-review", "outputPath": str(exported_path)},
                )
                disabled = client.put(
                    "/api/app/skill-packages/community.avatar-review",
                    json={"enabled": False},
                )
                skills_after_disable = client.get("/api/app/skills").json()["skills"]
                uninstalled = client.request(
                    "DELETE",
                    "/api/app/skill-packages/community.avatar-review",
                    json={"removeProjectedSkill": True},
                )
                packages_after_uninstall = client.get("/api/app/skill-packages").json()["installed"]
                skills_after_uninstall = client.get("/api/app/skills").json()["skills"]

            self.assertEqual(preflight.status_code, 200)
            self.assertEqual(preflight.json()["preview"]["manifest"]["id"], "community.avatar-review")
            self.assertEqual(imported.status_code, 200)
            self.assertEqual(imported.json()["projectedSkill"]["name"], "avatar-review")
            self.assertFalse(imported.json()["projectedSkill"]["enabled"])
            self.assertTrue(any(skill["name"] == "avatar-review" and skill["source"] == "user" and not skill["enabled"] for skill in skills))
            self.assertEqual(exported.status_code, 200)
            self.assertTrue(exported_path.is_file())
            self.assertEqual(exported.json()["exported"]["signature_status"], "dev")
            self.assertEqual(disabled.status_code, 200)
            self.assertFalse(disabled.json()["state"]["registry_entry"]["enabled"])
            self.assertTrue(any(skill["name"] == "avatar-review" and not skill["enabled"] for skill in skills_after_disable))
            self.assertEqual(uninstalled.status_code, 200)
            self.assertEqual(uninstalled.json()["uninstalled"]["skill_id"], "community.avatar-review")
            self.assertEqual(packages_after_uninstall, [])
            self.assertFalse(any(skill["name"] == "avatar-review" for skill in skills_after_uninstall))

    def test_user_path_to_skill_export_preserves_manifest_and_workflow_then_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skill_dir = dashboard_server.AGENT_GATEWAY.user_skills_dir / "roundtrip-profile"
            captured = dashboard_server.build_path_to_skill_source(
                {"status": "passed", "workflow": "optimizer_conservative_profile", "steps": ["inspect"]},
                package_id="community.path-to-skill.roundtrip-profile",
                skill_name="roundtrip-profile",
                title="Roundtrip Profile",
                version="2.3.4",
                author="Roundtrip Author",
            )
            captured.write_to(skill_dir)
            workflow_bytes = (skill_dir / "workflows" / "captured-path.json").read_bytes()
            (skill_dir / "private-notes.txt").write_text("must not be exported", encoding="utf-8")
            package_path = root / "roundtrip-profile.vsk"

            with TestClient(dashboard_server.app) as client:
                exported = client.post(
                    "/api/app/skill-packages/export",
                    json={"skillName": "roundtrip-profile", "outputPath": str(package_path)},
                )
                imported = client.post(
                    "/api/app/skill-packages/import",
                    json={"packagePath": str(package_path)},
                )

            self.assertEqual(exported.status_code, 200, exported.text)
            with zipfile.ZipFile(package_path, "r") as archive:
                names = set(archive.namelist())
                self.assertEqual(archive.read("workflows/captured-path.json"), workflow_bytes)
            self.assertNotIn("private-notes.txt", names)
            preview = SkillPackageService(root / "inspect", vrcforge_version="1.3.0").inspect_package(package_path)
            for field in ("id", "author", "version", "permissions", "agent"):
                self.assertEqual(preview.manifest[field], captured.manifest[field])
            self.assertEqual(preview.manifest["entrypoints"], captured.manifest["entrypoints"])
            self.assertEqual(imported.status_code, 200, imported.text)
            self.assertEqual(imported.json()["projectedSkill"]["supportFiles"], ["workflows/captured-path.json"])
            self.assertEqual(
                (dashboard_server.AGENT_GATEWAY.user_skills_dir / "roundtrip-profile" / "workflows" / "captured-path.json").read_bytes(),
                workflow_bytes,
            )

    def test_legacy_user_skill_export_still_synthesizes_minimal_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skill_dir = dashboard_server.AGENT_GATEWAY.user_skills_dir / "legacy-export"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: legacy-export\ntitle: Legacy Export\n---\nRead project state.\n",
                encoding="utf-8",
            )
            package_path = root / "legacy-export.vsk"

            result = dashboard_server.export_skill_package_sync(
                {"skillName": "legacy-export", "outputPath": str(package_path)}
            )

            self.assertTrue(result["ok"])
            preview = SkillPackageService(root / "inspect", vrcforge_version="1.3.0").inspect_package(package_path)
            self.assertEqual(preview.manifest["id"], "community.legacy-export")
            self.assertEqual(preview.manifest["version"], "1.0.0")
            self.assertEqual(preview.manifest["min_vrcforge_version"], "1.3.0")
            self.assertEqual(preview.manifest["entrypoints"], {"skill": "SKILL.md"})

    def test_manifest_user_skill_export_rejects_unsafe_support_without_residue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            temp_parent = root / "export-temp"
            temp_parent.mkdir()
            real_temporary_directory = tempfile.TemporaryDirectory

            def write_skill(
                name: str,
                *,
                support_files: list[str],
                entrypoints: dict[str, str],
                files: dict[str, bytes] | None = None,
                manifest_bytes: bytes | None = None,
            ) -> Path:
                skill_dir = dashboard_server.AGENT_GATEWAY.user_skills_dir / name
                skill_dir.mkdir(parents=True)
                support_yaml = "".join(f"  - {value}\n" for value in support_files)
                (skill_dir / "SKILL.md").write_text(
                    f"---\nname: {name}\nsupport-files:\n{support_yaml}---\nRead project state.\n",
                    encoding="utf-8",
                )
                if manifest_bytes is None:
                    manifest = {
                        "id": f"community.{name}",
                        "name": name,
                        "skill_name": name,
                        "version": "1.0.0",
                        "author": "Unit Test",
                        "description": "Unsafe export fixture.",
                        "min_vrcforge_version": "1.3.0",
                        "permissions": ["read_project"],
                        "entrypoints": entrypoints,
                    }
                    manifest_bytes = json.dumps(manifest).encode("utf-8")
                (skill_dir / "manifest.json").write_bytes(manifest_bytes)
                for relative, data in (files or {}).items():
                    destination = skill_dir.joinpath(*relative.split("/"))
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                return skill_dir

            cases = [
                (
                    "undeclared-support",
                    ["workflows/captured.json"],
                    {"skill": "SKILL.md"},
                    {"workflows/captured.json": b"{}\n"},
                    None,
                ),
                (
                    "missing-support",
                    ["workflows/captured.json"],
                    {"skill": "SKILL.md", "workflow": "workflows/captured.json"},
                    {},
                    None,
                ),
                (
                    "escape-support",
                    ["../outside.json"],
                    {"skill": "SKILL.md"},
                    {},
                    None,
                ),
                (
                    "backslash-support",
                    ["workflows\\captured.json"],
                    {"skill": "SKILL.md"},
                    {},
                    None,
                ),
                (
                    "collision-support",
                    ["workflows/captured.json", "workflows/captured.json"],
                    {"skill": "SKILL.md", "workflow": "workflows/captured.json"},
                    {"workflows/captured.json": b"{}\n"},
                    None,
                ),
                (
                    "alternate-skill-entrypoint",
                    [],
                    {"skill": "OTHER-SKILL.md"},
                    {"OTHER-SKILL.md": b"---\nname: other-skill\n---\nNot the loaded skill.\n"},
                    None,
                ),
                (
                    "entrypoint-case-collision",
                    ["workflows/captured.json"],
                    {
                        "skill": "SKILL.md",
                        "workflow": "workflows/captured.json",
                        "workflow_alias": "WORKFLOWS/CAPTURED.JSON",
                    },
                    {"workflows/captured.json": b"{}\n"},
                    None,
                ),
                (
                    "invalid-manifest",
                    [],
                    {"skill": "SKILL.md"},
                    {},
                    b"{not-json",
                ),
            ]
            for name, support_files, entrypoints, files, manifest_bytes in cases:
                with self.subTest(name=name):
                    write_skill(
                        name,
                        support_files=support_files,
                        entrypoints=entrypoints,
                        files=files,
                        manifest_bytes=manifest_bytes,
                    )
                    output = root / f"{name}.vsk"
                    with patch(
                        "dashboard_server.tempfile.TemporaryDirectory",
                        side_effect=lambda *args, **kwargs: real_temporary_directory(
                            *args, dir=temp_parent, **kwargs
                        ),
                    ):
                        with self.assertRaises(SkillPackageError):
                            dashboard_server.export_skill_package_sync(
                                {"skillName": name, "outputPath": str(output)}
                            )
                    self.assertFalse(output.exists())
                    self.assertEqual(list(temp_parent.iterdir()), [])

            linked_skill = write_skill(
                "linked-support",
                support_files=["workflows/captured.json"],
                entrypoints={"skill": "SKILL.md", "workflow": "workflows/captured.json"},
                files={"workflows/captured.json": b"{}\n"},
            )
            for unsafe_name in ("captured.json", "workflows"):
                with self.subTest(link_or_junction=unsafe_name):
                    output = root / f"linked-{unsafe_name}.vsk"
                    original_link_check = dashboard_server._skill_projection_path_is_link_like

                    def simulated_link_check(path: Path, *, target: str = unsafe_name) -> bool:
                        return path.name == target or original_link_check(path)

                    with (
                        patch(
                            "dashboard_server.tempfile.TemporaryDirectory",
                            side_effect=lambda *args, **kwargs: real_temporary_directory(
                                *args, dir=temp_parent, **kwargs
                            ),
                        ),
                        patch(
                            "dashboard_server._skill_projection_path_is_link_like",
                            side_effect=simulated_link_check,
                        ),
                    ):
                        with self.assertRaises(SkillPackageError):
                            dashboard_server.export_skill_package_sync(
                                {"skillName": linked_skill.name, "outputPath": str(output)}
                            )
                    self.assertFalse(output.exists())
                    self.assertEqual(list(temp_parent.iterdir()), [])

    def test_skill_package_safe_mode_import_dry_run_and_projection_disable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "package-source"
            source.mkdir()
            manifest = {
                "id": "community.risky-review",
                "name": "Risky Review Package",
                "skill_name": "risky-review",
                "version": "1.0.0",
                "author": "Unit Test",
                "description": "Dashboard risky skill package fixture.",
                "min_vrcforge_version": "0.5.0",
                "permissions": ["read_project", "unity_modify_materials"],
                "entrypoints": {"skill": "SKILL.md"},
            }
            (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (source / "SKILL.md").write_text(
                "---\n"
                "name: risky-review\n"
                "title: Risky Review\n"
                "description: Imported risky package skill.\n"
                "allowed-tools:\n"
                "  - vrcforge_health\n"
                "entrypoint-tool: vrcforge_health\n"
                "---\n"
                "Inspect project state before edits.\n",
                encoding="utf-8",
            )
            package = SkillPackageService(root / "build-store", vrcforge_version="0.5.1").export_dev(
                source,
                root / "risky-review.vsk",
            ).package_path

            with TestClient(dashboard_server.app) as client:
                safe_mode = client.post("/api/app/skill-packages/safe-mode", json={"enabled": True})
                dry_run = client.post(
                    "/api/app/skill-packages/import",
                    json={"packagePath": str(package), "dryRun": True},
                )
                installed_after_dry_run = client.get("/api/app/skill-packages").json()["installed"]
                imported = client.post("/api/app/skill-packages/import", json={"packagePath": str(package)})
                skills = client.get("/api/app/skills").json()["skills"]
                enable_blocked = client.put(
                    "/api/app/skill-packages/community.risky-review",
                    json={"enabled": True},
                )

            self.assertEqual(safe_mode.status_code, 200)
            self.assertEqual(dry_run.status_code, 200)
            self.assertTrue(dry_run.json()["dryRun"])
            self.assertFalse(dry_run.json()["preview"]["dryRun"]["willWrite"])
            self.assertFalse(dry_run.json()["preview"]["governance"]["safeMode"]["defaultEnabled"])
            self.assertEqual(installed_after_dry_run, [])
            self.assertEqual(imported.status_code, 200)
            self.assertFalse(imported.json()["imported"]["registry_entry"]["enabled"])
            self.assertFalse(imported.json()["projectedSkill"]["enabled"])
            self.assertTrue(any(skill["name"] == "risky-review" and not skill["enabled"] for skill in skills))
            self.assertEqual(enable_blocked.status_code, 400)
            self.assertIn("safe mode", enable_blocked.json()["detail"])

    def test_skill_package_revoked_signer_preflight_explains_and_import_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "package-source"
            source.mkdir()
            manifest = {
                "id": "community.signed-review",
                "name": "Signed Review Package",
                "skill_name": "signed-review",
                "version": "1.0.0",
                "author": "Unit Test",
                "description": "Dashboard signed skill package fixture.",
                "min_vrcforge_version": "0.5.0",
                "permissions": ["read_project"],
                "entrypoints": {"skill": "SKILL.md"},
            }
            (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (source / "SKILL.md").write_text("---\nname: signed-review\n---\nInspect safely.\n", encoding="utf-8")
            service = SkillPackageService(root / "build-store", vrcforge_version="0.5.1")
            key_pair = service.generate_signing_keypair()
            package = service.export_release(source, root / "signed-review.vsk", key_pair.private_key_pem).package_path

            with TestClient(dashboard_server.app) as client:
                untrusted = client.post("/api/app/skill-packages/preflight", json={"packagePath": str(package)})
                revoked = client.post(
                    "/api/app/skill-packages/revoke-signer",
                    json={"signerFingerprint": key_pair.fingerprint, "reason": "test compromise"},
                )
                blocked_preflight = client.post("/api/app/skill-packages/preflight", json={"packagePath": str(package)})
                blocked_import = client.post("/api/app/skill-packages/import", json={"packagePath": str(package)})

            self.assertEqual(untrusted.status_code, 200)
            self.assertTrue(untrusted.json()["preview"]["governance"]["signatureVerified"])
            self.assertFalse(untrusted.json()["preview"]["governance"]["verified"])
            self.assertEqual(untrusted.json()["preview"]["governance"]["signerTrustStatus"], "untrusted")
            self.assertEqual(revoked.status_code, 200)
            self.assertEqual(blocked_preflight.status_code, 200)
            self.assertEqual(blocked_preflight.json()["preview"]["governance"]["signerTrustStatus"], "revoked")
            self.assertFalse(blocked_preflight.json()["preview"]["governance"]["importAllowed"])
            self.assertEqual(blocked_import.status_code, 400)
            self.assertIn("revoked", blocked_import.json()["detail"])

    def test_path_to_skill_preview_write_and_export_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            preview_dir = root / "preview-should-not-write"
            source_dir = root / "captured-source"
            export_source_dir = root / "captured-export-source"
            occupied_source_dir = root / "occupied-source"
            occupied_source_dir.mkdir()
            occupied_marker = occupied_source_dir / "keep.txt"
            occupied_marker.write_text("keep", encoding="utf-8")
            temp_output_root = root / "server-temp-output"
            temp_output_root.mkdir()
            package_path = root / "shader-preset.vsk"
            occupied_package_path = root / "occupied-package.vsk"
            occupied_package_path.write_bytes(b"original-package-bytes")
            blocked_export_source = root / "blocked-export-source"
            summary = {
                "status": "passed",
                "workflow": "shader_adapter_semantic_tuning",
                "projectPath": "C:\\Users\\xiao123\\AvatarProject",
                "avatarPath": "AvatarRoot",
                "steps": [
                    {
                        "name": "shader.apply",
                        "tool": "vrcforge_apply_shader_tuning",
                        "params": {
                            "projectRoot": "C:\\Users\\xiao123\\AvatarProject",
                            "artifactPath": "C:\\Users\\xiao123\\AvatarProject\\Assets\\VRCForge\\proof.json",
                            "rendererPath": "AvatarRoot/Hat",
                        },
                    }
                ],
                "validation": {
                    "requiresApproval": True,
                    "requiresCheckpoint": True,
                    "requiresRollback": True,
                },
            }

            with TestClient(dashboard_server.app) as client:
                preview = client.post(
                    "/api/app/path-to-skill/preview",
                    json={
                        "summary": summary,
                        "packageId": "community.path-to-skill.shader-preset",
                        "skillName": "shader-preset",
                        "title": "Shader Preset",
                        "outputPath": str(preview_dir),
                        "writeSource": True,
                        "exportVsk": True,
                    },
                )
                written = client.post(
                    "/api/app/path-to-skill/write",
                    json={
                        "summary": summary,
                        "packageId": "community.path-to-skill.shader-preset",
                        "skillName": "shader-preset",
                        "title": "Shader Preset",
                        "outputPath": str(source_dir),
                        "writeSource": True,
                    },
                )
                existing_output = client.post(
                    "/api/app/path-to-skill/write",
                    json={
                        "summary": summary,
                        "packageId": "community.path-to-skill.shader-preset",
                        "skillName": "shader-preset",
                        "title": "Shader Preset",
                        "outputPath": str(occupied_source_dir),
                        "writeSource": True,
                    },
                )
                with patch(
                    "dashboard_server.tempfile",
                    SimpleNamespace(mkdtemp=Mock(return_value=str(temp_output_root))),
                ):
                    temp_exported = client.post(
                        "/api/app/path-to-skill/write",
                        json={
                            "summary": summary,
                            "packageId": "community.path-to-skill.shader-preset",
                            "skillName": "shader-preset",
                            "title": "Shader Preset",
                            "exportVsk": True,
                            "confirmExport": True,
                        },
                    )
                exported = client.post(
                    "/api/app/path-to-skill/write",
                    json={
                        "summary": summary,
                        "packageId": "community.path-to-skill.shader-preset",
                        "skillName": "shader-preset",
                        "title": "Shader Preset",
                        "outputPath": str(export_source_dir),
                        "exportVsk": True,
                        "confirmExport": True,
                        "packageOutputPath": str(package_path),
                    },
                )
                existing_package_output = client.post(
                    "/api/app/path-to-skill/write",
                    json={
                        "summary": summary,
                        "packageId": "community.path-to-skill.shader-preset",
                        "skillName": "shader-preset",
                        "title": "Shader Preset",
                        "outputPath": str(blocked_export_source),
                        "exportVsk": True,
                        "confirmExport": True,
                        "packageOutputPath": str(occupied_package_path),
                    },
                )

            self.assertEqual(preview.status_code, 200, preview.text)
            preview_payload = preview.json()
            self.assertTrue(preview_payload["dryRun"])
            self.assertTrue(preview_payload["writeSuppressed"])
            self.assertFalse(preview_dir.exists())
            serialized_source = json.dumps(preview_payload["sourceFiles"], ensure_ascii=False)
            self.assertIn("vrcforge.path_to_skill.v1", serialized_source)
            self.assertIn("{{projectPath}}", serialized_source)
            self.assertNotIn("C:\\Users", serialized_source)

            self.assertEqual(written.status_code, 200, written.text)
            self.assertFalse(written.json()["dryRun"])
            self.assertTrue((source_dir / "manifest.json").is_file())
            self.assertTrue((source_dir / "SKILL.md").is_file())
            self.assertTrue((source_dir / "workflows" / "captured-path.json").is_file())

            self.assertEqual(existing_output.status_code, 400, existing_output.text)
            self.assertIn("already exists", existing_output.json()["detail"])
            self.assertEqual(occupied_marker.read_text(encoding="utf-8"), "keep")
            self.assertEqual({item.name for item in occupied_source_dir.iterdir()}, {"keep.txt"})

            self.assertEqual(temp_exported.status_code, 200, temp_exported.text)
            temp_source_dir = temp_output_root / "source"
            temp_export_payload = temp_exported.json()
            self.assertEqual(Path(temp_export_payload["writtenSource"]["path"]), temp_source_dir)
            self.assertTrue((temp_source_dir / "SKILL.md").is_file())
            self.assertEqual(Path(temp_export_payload["exported"]["package_path"]).parent, temp_output_root)

            self.assertEqual(existing_package_output.status_code, 400, existing_package_output.text)
            self.assertIn("already exists", existing_package_output.json()["detail"])
            self.assertEqual(occupied_package_path.read_bytes(), b"original-package-bytes")
            self.assertFalse(blocked_export_source.exists())

            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertTrue(package_path.is_file())
            self.assertEqual(exported.json()["exported"]["signature_status"], "dev")
            package_preview = SkillPackageService(root / "inspect-store", vrcforge_version="1.3.0").inspect_package(package_path)
            self.assertEqual(package_preview.manifest["id"], "community.path-to-skill.shader-preset")
            self.assertEqual(package_preview.manifest["entrypoints"]["workflow"], "workflows/captured-path.json")

    def test_path_to_skill_write_requires_explicit_export_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package_path = root / "blocked.vsk"
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/path-to-skill/write",
                    json={
                        "summary": {"status": "passed", "workflow": "optimizer_conservative_profile"},
                        "packageId": "community.path-to-skill.optimizer-profile",
                        "exportVsk": True,
                        "packageOutputPath": str(package_path),
                    },
                )

            self.assertEqual(response.status_code, 400)
            self.assertIn("confirmExport=true", response.json()["detail"])
            self.assertFalse(package_path.exists())

    def test_path_to_skill_endpoint_rejects_secrets_and_embedded_private_paths(self) -> None:
        with TestClient(dashboard_server.app) as client:
            secret = client.post(
                "/api/app/path-to-skill/preview",
                json={
                    "summary": {"workflow": "bad", "gatewayToken": "test-token-123456789"},
                    "packageId": "community.path-to-skill.bad-secret",
                },
            )
            private_path = client.post(
                "/api/app/path-to-skill/preview",
                json={
                    "summary": {
                        "workflow": "bad",
                        "notes": "Proof file was C:\\Users\\xiao123\\Desktop\\private-proof.json",
                    },
                    "packageId": "community.path-to-skill.bad-path",
                },
            )

        self.assertEqual(secret.status_code, 400)
        self.assertIn("secret", secret.json()["detail"].lower())
        self.assertEqual(private_path.status_code, 400)
        self.assertIn("absolute path", private_path.json()["detail"].lower())

    def test_roslyn_advanced_skill_requires_full_auto_mode_and_confirmation(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            initial = client.get("/api/agent/manifest", headers=headers).json()
            initial_tool_names = {tool["name"] for tool in initial["tools"]}
            self.assertNotIn("vrcforge_roslyn_status", initial_tool_names)
            self.assertNotIn("vrcforge_request_roslyn_advanced", initial_tool_names)
            self.assertNotIn("roslyn-advanced-power", {skill["name"] for skill in initial["skills"]})

            dashboard_server.AGENT_GATEWAY.update_permission_state("roslyn_full_auto", acknowledge_roslyn_risk=True)
            payload = client.get("/api/agent/manifest", headers=headers).json()
            tool_names = {tool["name"] for tool in payload["tools"]}
            self.assertNotIn("vrcforge_request_roslyn_advanced", tool_names)
            self.assertNotIn("vrcforge_roslyn_advanced", {item["name"] for item in payload["writeTargets"]})
            self.assertNotIn("roslyn-advanced-power", {skill["name"] for skill in payload["skills"]})

    @patch("dashboard_server.invoke_unity_mcp")
    def test_generic_unity_write_cannot_bypass_roslyn_gate(self, mock_invoke) -> None:
        result = dashboard_server.unity_mcp_write_sync({"toolName": "vrc_execute_roslyn", "arguments": {"code": "return 42;"}})
        self.assertFalse(result["ok"])
        self.assertIn("Dynamic code execution is not supported", result["error"])
        mock_invoke.assert_not_called()

    @patch("dashboard_server.invoke_unity_mcp")
    def test_generic_unity_write_rejects_non_allowlisted_tools(self, mock_invoke) -> None:
        result = dashboard_server.unity_mcp_write_sync({"toolName": "vrc_import_unitypackage", "arguments": {"packagePath": "Assets/test.unitypackage"}})
        self.assertFalse(result["ok"])
        self.assertIn("static write allowlist", result["error"])
        mock_invoke.assert_not_called()

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_generic_unity_write_allows_static_vrcforge_tool(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace(unity_mcp_timeout_seconds=30)
        mock_invoke.return_value = dashboard_server.McpResult(exit_code=0, stdout="ok", stderr="", payload={"ok": True})

        result = dashboard_server.unity_mcp_write_sync(
            {
                "toolName": "vrc_set_material_shader",
                "arguments": {
                    "materialAssetPath": "Assets/Avatar/Test.mat",
                    "shaderName": "Standard",
                },
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(mock_invoke.call_args.args[1], "vrc_set_material_shader")
        self.assertEqual(mock_invoke.call_args.args[2]["shaderName"], "Standard")

    def test_agent_gateway_mcp_lists_codex_debug_loop_tools(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        base_headers = {
            "Authorization": f"Bearer {config.token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2026-07-28",
        }
        meta = {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {"name": "codex-test", "version": "1.4.0"},
        }

        with TestClient(dashboard_server.app) as client:
            discovered = client.post(
                "/mcp",
                headers={**base_headers, "Mcp-Method": "server/discover"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "server/discover",
                    "params": {"_meta": meta},
                },
            )
            self.assertEqual(discovered.status_code, 200)
            self.assertEqual(discovered.json()["result"]["supportedVersions"], ["2026-07-28"])
            self.assertEqual(discovered.json()["result"]["resultType"], "complete")

            listed = client.post(
                "/mcp",
                headers={**base_headers, "Mcp-Method": "tools/list"},
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {"_meta": meta, "exposureLayer": "execution"},
                },
            )
            self.assertEqual(listed.status_code, 200)

        tool_names = {tool["name"] for tool in listed.json()["result"]["tools"]}
        self.assertIn("vrcforge_agent_message", tool_names)
        self.assertIn("vrcforge_execute_shell", tool_names)
        self.assertNotIn("vrcforge_capture_screenshot", tool_names)
        self.assertIn("vrcforge_vision_audit", tool_names)
        self.assertNotIn("vrcforge_roslyn_status", tool_names)
        self.assertIn("vrcforge_get_compile_errors", tool_names)
        self.assertIn("vrcforge_request_apply", tool_names)
        self.assertNotIn("vrcforge_apply_approved", tool_names)
        self.assertNotIn("vrcforge_execute_approved_shell", tool_names)
        self.assertIn("vrcforge_preview_ensure_expression_parameter", tool_names)
        self.assertIn("vrcforge_preview_ensure_expression_menu_control", tool_names)
        self.assertIn("vrcforge_preview_ensure_animator_state", tool_names)
        self.assertIn("vrcforge_preview_create_wardrobe", tool_names)
        self.assertIn("vrcforge_preview_manage_wardrobe", tool_names)
        self.assertIn("vrcforge_preview_add_outfit_part", tool_names)
        self.assertIn("vrcforge_preview_add_modular_avatar_component", tool_names)
        self.assertIn("vrcforge_scan_project_index", tool_names)
        self.assertIn("vrcforge_inspect_outfit_package", tool_names)
        self.assertIn("vrcforge_avatar_encryption_research_report", tool_names)
        self.assertIn("vrcforge_avatar_encryption_scan", tool_names)
        self.assertIn("vrcforge_avatar_encryption_plan", tool_names)
        self.assertIn("vrcforge_avatar_encryption_preview", tool_names)
        self.assertIn("vrcforge_avatar_encryption_addon_status", tool_names)
        self.assertIn("vrcforge_avatar_encryption_liltoon_apply_request", tool_names)
        self.assertIn("vrcforge_avatar_encryption_poiyomi_apply_request", tool_names)
        self.assertIn("vrcforge_avatar_encryption_remove_request", tool_names)
        self.assertNotIn("vrcforge_ensure_expression_parameter", tool_names)
        self.assertNotIn("vrcforge_ensure_expression_menu_control", tool_names)
        self.assertNotIn("vrcforge_ensure_animator_state", tool_names)
        self.assertNotIn("vrcforge_create_wardrobe", tool_names)
        self.assertNotIn("vrcforge_manage_wardrobe", tool_names)
        self.assertNotIn("vrcforge_avatar_encryption_addon_apply", tool_names)
        self.assertNotIn("vrcforge_avatar_encryption_addon_remove", tool_names)

        with TestClient(dashboard_server.app) as client:
            planning = client.post(
                "/mcp",
                headers={**base_headers, "Mcp-Method": "tools/list"},
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/list",
                    "params": {"_meta": meta, "exposureLayer": "planning"},
                },
            )
        self.assertEqual(planning.status_code, 200)
        planning_tools = planning.json()["result"]["tools"]
        self.assertTrue(planning_tools)
        self.assertTrue(all(tool["_meta"]["permission"] == "ReadOnly" for tool in planning_tools))
        self.assertNotIn("vrcforge_request_apply", {tool["name"] for tool in planning_tools})

    def test_phase2_unity_tools_are_registered_without_roslyn(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        expected_tools = {
            "GameObjectTools.cs": "vrc_scan_avatar_items",
            "ComponentTools.cs": "vrc_scan_fx_animator",
            "AssetTools.cs": "vrc_scan_animation_bindings",
            "ConsoleTools.cs": "vrc_create_safe_backup",
            "PrefabTools.cs": "vrc_restore_safe_backup",
        }
        phase2_text = []

        for filename, tool_name in expected_tools.items():
            source = (editor_dir / filename).read_text(encoding="utf-8")
            phase2_text.append(source)
            self.assertIn("[VRCForgeCommand(", source)
            self.assertIn(f'toolId: "{tool_name}"', source)
            self.assertIn("public static object HandleCommand(JObject @params)", source)

        combined = "\n".join(phase2_text)
        old_dynamic_tool = "vrc_" + "execute_" + "roslyn"
        old_dynamic_type = "CSharp" + "Script"
        self.assertNotIn(old_dynamic_tool, combined)
        self.assertNotIn(old_dynamic_type, combined)

    def test_safe_backup_restore_source_constrains_manifest_paths(self) -> None:
        source = Path("Assets/VRCForge/Editor/PrefabTools.cs").read_text(encoding="utf-8-sig")
        create_source = Path("Assets/VRCForge/Editor/ConsoleTools.cs").read_text(encoding="utf-8-sig")

        self.assertIn("TryNormalizeManifestRelativePath", source)
        self.assertIn("ResolveContainedPath", source)
        self.assertIn("Path.GetFullPath", source)
        self.assertIn("Manifest path must be relative", source)
        self.assertIn("Manifest path is not a safe relative path", source)
        self.assertIn("ResolveManagedProjectPath", source)
        self.assertIn("ResolveManagedProjectPath", create_source)
        self.assertIn("Library/VRCForge/Backups", create_source)
        self.assertNotIn("Path.Combine(backupPath, backupRelativePath)", source)

    def test_unity_scan_outputs_use_managed_project_path_guard(self) -> None:
        editor_dir = Path("Assets/VRCForge/Editor")
        guard_source = (editor_dir / "VRCForgeOutputPathGuard.cs").read_text(encoding="utf-8-sig")
        self.assertIn("ResolveManagedProjectOutputPath", guard_source)
        self.assertIn("Assets/VRCForge", guard_source)
        for filename in (
            "GameObjectTools.cs",
            "ComponentTools.cs",
            "AssetTools.cs",
            "ShaderMaterialScanner.cs",
            "BlendshapeExporter.cs",
            "AvatarParameterScanner.cs",
            "AvatarControlScanner.cs",
            "WardrobeScanner.cs",
            "AvatarPerformanceTool.cs",
        ):
            source = (editor_dir / filename).read_text(encoding="utf-8-sig")
            self.assertIn("VRCForgeOutputPathGuard.ResolveManagedProjectOutputPath", source)

    def test_wardrobe_scanner_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        source = (editor_dir / "WardrobeScanner.cs").read_text(encoding="utf-8")
        # Declares the read-only int-exclusive wardrobe detection tool.
        self.assertIn("[VRCForgeCommand(", source)
        self.assertIn('toolId: "vrc_scan_wardrobe"', source)
        self.assertIn("public static object HandleCommand(JObject @params)", source)
        # Captures the menu toggle's int value (the gap the older scanners lacked).
        self.assertIn("control.value", source)
        # Reads per-state Write Defaults, which exclusivity in this style relies on.
        self.assertIn("writeDefaultValues", source)
        # Reconciles the FX layer via Any-State "Equals N" transitions.
        self.assertIn("AnimatorConditionMode.Equals", source)
        self.assertIn("anyStateTransitions", source)
        # Reads which objects each clip turns on vs off.
        self.assertIn("m_IsActive", source)
        # Strict wardrobes must have a selectable outfit object that turns on.
        # Off-only naked-base toggles stay in wardrobeCandidates, not wardrobes.
        self.assertIn("hasSelectableOutfitObject", source)
        self.assertIn("no FX clip turns an outfit object on; off-only toggles are not wardrobes", source)
        self.assertIn("wardrobeCandidateCount", source)
        self.assertIn("wardrobeCandidates", source)
        self.assertIn("looseControlCount", source)
        self.assertIn("looseControls", source)
        self.assertIn("LooksLikeDisableOnlyControl", source)
        self.assertIn("animatorEvidence", source)
        self.assertIn("fxTransitionCount", source)
        self.assertIn("clipWithOnObjectCount", source)
        self.assertIn("menu controls look like disable/off toggles", source)
        # Recurses into SubMenus when toggles overflow the 8-control cap.
        self.assertIn("ControlType.SubMenu", source)
        # Read-only: must NOT mutate the avatar (no FX/menu/param writes, no Undo).
        self.assertNotIn("Undo.", source)
        self.assertNotIn("SetDirty", source)
        self.assertNotIn(".AddLayer(", source)
        self.assertNotIn(".AddState(", source)
        self.assertNotIn("AssetDatabase.CreateAsset", source)

    def test_wardrobe_scan_registered_in_gateway(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        # Read tool is directly callable, never an approval-gated write target.
        self.assertIn("vrcforge_scan_wardrobe", tool_names)
        self.assertNotIn("vrcforge_scan_wardrobe", write_targets)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_wardrobe_scan_forwards_to_unity_tool(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"wardrobeCount": 1, "wardrobes": [{"parameterName": "Clothes"}]}},
        )
        result = dashboard_server.WARDROBE_OUTFIT_WORKFLOWS.scan_wardrobe(
            {"avatar_path": "Scene/HeroAvatar"}
        )
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_scan_wardrobe")
        self.assertEqual(params["avatarPath"], "Scene/HeroAvatar")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_wardrobe_scan_does_not_reuse_stale_artifact(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"wardrobeCount": 0, "wardrobes": []}},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            original_artifacts_dir = dashboard_server.DASHBOARD_ARTIFACTS_DIR
            dashboard_server.DASHBOARD_ARTIFACTS_DIR = Path(temp_dir)
            try:
                stale_path = dashboard_server.build_dashboard_artifact_path(
                    "wardrobe",
                    "Scene/HeroAvatar",
                    "json",
                )
                stale_path.write_text(
                    json.dumps({"wardrobeCount": 7, "wardrobes": [{"parameterName": "Stale"}]}),
                    encoding="utf-8",
                )

                result = dashboard_server.WARDROBE_OUTFIT_WORKFLOWS.scan_wardrobe(
                    {"avatar_path": "Scene/HeroAvatar"}
                )

                self.assertEqual(result["wardrobeCount"], 0)
                self.assertEqual(result["wardrobes"], [])
                self.assertTrue(stale_path.exists())
                saved = json.loads(stale_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["wardrobeCount"], 0)
            finally:
                dashboard_server.DASHBOARD_ARTIFACTS_DIR = original_artifacts_dir

    def test_wardrobe_outfit_writer_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        source = (editor_dir / "WardrobeOutfitWriter.cs").read_text(encoding="utf-8")
        # Declares the int-exclusive add-outfit write tool.
        self.assertIn("[VRCForgeCommand(", source)
        self.assertIn('toolId: "vrc_add_wardrobe_outfit"', source)
        self.assertIn("public static object HandleCommand(JObject @params)", source)
        # Assigns the next free int value and gates an Any-State Equals N transition.
        self.assertIn("AnimatorConditionMode.Equals", source)
        self.assertIn("AddAnyStateTransition", source)
        self.assertIn("AddState", source)
        # Matches the wardrobe's Write Defaults convention, which exclusivity relies on.
        self.assertIn("writeDefaultValues", source)
        # Authors a clip toggling objects on/off and adds a menu toggle (SubMenu overflow).
        self.assertIn("m_IsActive", source)
        self.assertIn("AssetDatabase.CreateAsset", source)
        self.assertIn("CreateOverflowSubMenu", source)
        self.assertIn("owner.controls.RemoveAt", source)
        self.assertIn("VRCExpressionsMenu.MAX_CONTROLS", source)
        self.assertIn("ControlType.Toggle", source)
        self.assertIn("VRCExpressionsMenu.MAX_CONTROLS", source)
        # Full wardrobe menus overflow inside the existing wardrobe menu tree,
        # not onto the avatar root menu.
        self.assertIn("FindBestMenuRef", source)
        self.assertIn("CreateOverflowSubMenu(existingHome.menu", source)
        self.assertIn("FindLastControlIndex", source)
        self.assertIn("var existingClip = st.motion as AnimationClip", source)
        self.assertNotIn("var clip = st.motion as AnimationClip", source)
        # Write tool: must register Undo entries so the checkpoint timeline can roll it back.
        self.assertIn("Undo.", source)
        # Supports a non-mutating preview path.
        self.assertIn("preview", source)

    def test_wardrobe_manager_writer_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        source = (editor_dir / "WardrobeManagerWriter.cs").read_text(encoding="utf-8")
        self.assertIn("[VRCForgeCommand(", source)
        self.assertIn('toolId: "vrc_manage_wardrobe"', source)
        self.assertIn("public static object HandleCommand(JObject @params)", source)
        for action in (
            "remove_outfit",
            "rename_outfit",
            "reorder_outfits",
            "set_default",
            "delete_wardrobe",
        ):
            self.assertIn(action, source)
        # WD-style wardrobe management must edit the same triangle the scanner reads.
        self.assertIn("RemoveAnyStateTransition", source)
        self.assertIn("RemoveState", source)
        self.assertIn("RemoveLayer", source)
        self.assertIn("VRCExpressionParameters.ValueType.Int", source)
        self.assertIn("ControlType.SubMenu", source)
        self.assertIn("m_IsActive", source)
        # Destructive object/asset removal is supported but opt-in and Undo/checkpoint friendly.
        self.assertIn("deleteObjects", source)
        self.assertIn("DestroyObjectImmediate", source)
        self.assertIn("DeleteAsset", source)
        self.assertIn("Undo.", source)
        self.assertIn("preview", source)
        self.assertIn("private static WardrobeManagePlan BuildPlan", source)
        self.assertIn("private class WardrobeManagePlan", source)
        self.assertNotIn("private static object BuildPlan", source)

    def test_avatar_authoring_primitives_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor" / "Generic"
        source = (editor_dir / "UnityAvatarAuthoringCrud.cs").read_text(encoding="utf-8")
        self.assertIn("[VRCForgeCommand(", source)
        self.assertIn('toolId: "vrc_ensure_expression_parameter"', source)
        self.assertIn('toolId: "vrc_ensure_expression_menu_control"', source)
        self.assertIn('toolId: "vrc_ensure_animator_state"', source)
        self.assertEqual(source.count("public static object HandleCommand(JObject @params)"), 3)
        # Generic primitives cover the scan-detectable int-exclusive wardrobe triangle.
        self.assertIn("VRCExpressionParameters.ValueType.Int", source)
        self.assertIn("AnimatorControllerParameterType.Int", source)
        self.assertIn("AddAnyStateTransition", source)
        self.assertIn("AnimatorConditionMode.Equals", source)
        self.assertIn("ControlType.SubMenu", source)
        self.assertIn("ControlType.Toggle", source)
        self.assertIn("controlValue", source)
        # Can bootstrap missing avatar assets and still uses Undo/preview.
        self.assertIn("CreateAnimatorControllerAtPath", source)
        self.assertIn("descriptor.expressionParameters = asset", source)
        self.assertIn("descriptor.expressionsMenu = asset", source)
        self.assertIn("EnsureMenuHasRoom", source)
        self.assertIn('name = "More"', source)
        self.assertIn("Undo.", source)
        self.assertIn("preview", source)

    def test_add_wardrobe_outfit_registered_in_gateway(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest?exposure_layer=execution", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        # Preview is a directly callable read/plan tool, never an approval-gated write target.
        self.assertIn("vrcforge_preview_add_wardrobe_outfit", tool_names)
        self.assertNotIn("vrcforge_preview_add_wardrobe_outfit", write_targets)
        self.assertIn("vrcforge_preview_manage_wardrobe", tool_names)
        self.assertNotIn("vrcforge_preview_manage_wardrobe", write_targets)
        self.assertIn("vrcforge_preview_ensure_expression_parameter", tool_names)
        self.assertIn("vrcforge_preview_ensure_expression_menu_control", tool_names)
        self.assertIn("vrcforge_preview_ensure_animator_state", tool_names)
        self.assertNotIn("vrcforge_preview_ensure_expression_parameter", write_targets)
        self.assertNotIn("vrcforge_preview_ensure_expression_menu_control", write_targets)
        self.assertNotIn("vrcforge_preview_ensure_animator_state", write_targets)
        self.assertIn("vrcforge_preview_create_wardrobe", tool_names)
        self.assertNotIn("vrcforge_preview_create_wardrobe", write_targets)
        # The write is approval-gated: a writeTarget, never a direct read tool.
        self.assertIn("vrcforge_add_wardrobe_outfit", write_targets)
        self.assertNotIn("vrcforge_add_wardrobe_outfit", tool_names)
        self.assertIn("vrcforge_manage_wardrobe", write_targets)
        self.assertNotIn("vrcforge_manage_wardrobe", tool_names)
        self.assertIn("vrcforge_ensure_expression_parameter", write_targets)
        self.assertIn("vrcforge_ensure_expression_menu_control", write_targets)
        self.assertIn("vrcforge_ensure_animator_state", write_targets)
        self.assertNotIn("vrcforge_ensure_expression_parameter", tool_names)
        self.assertNotIn("vrcforge_ensure_expression_menu_control", tool_names)
        self.assertNotIn("vrcforge_ensure_animator_state", tool_names)
        self.assertIn("vrcforge_create_wardrobe", write_targets)
        self.assertNotIn("vrcforge_create_wardrobe", tool_names)

    def test_authoring_wrappers_parse_string_booleans(self) -> None:
        wardrobe = build_create_wardrobe_request(
            {"parameterName": "Clothes", "writeDefaults": "false", "saved": "false", "networkSynced": "false"},
            preview=False,
        )
        parameter = dashboard_server.build_ensure_expression_parameter_request(
            {"parameterName": "Clothes", "saved": "false", "networkSynced": "false"},
            preview=False,
        )
        animator = dashboard_server.build_ensure_animator_state_request(
            {"layerName": "Clothes", "stateName": "Default", "parameterName": "Clothes", "writeDefaults": "false"},
            preview=False,
        )

        self.assertFalse(wardrobe["writeDefaults"])
        self.assertFalse(wardrobe["saved"])
        self.assertFalse(wardrobe["networkSynced"])
        self.assertFalse(parameter["saved"])
        self.assertFalse(parameter["networkSynced"])
        self.assertFalse(animator["writeDefaults"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_create_wardrobe_preview_forwards_with_flag(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"preview": True, "plan": {"parameterName": "Clothes"}}},
        )
        result = dashboard_server.WARDROBE_OUTFIT_WORKFLOWS.preview_create_wardrobe({
            "avatar_path": "Scene/HeroAvatar",
            "parameter_name": "Clothes",
            "menu_name": "Wardrobe",
        })
        self.assertTrue(result["ok"])
        self.assertEqual([call.args[1] for call in mock_invoke.call_args_list], [
            "vrc_ensure_expression_parameter",
            "vrc_ensure_animator_state",
            "vrc_ensure_expression_menu_control",
        ])
        params_by_tool = {call.args[1]: call.args[2] for call in mock_invoke.call_args_list}
        self.assertEqual(params_by_tool["vrc_ensure_expression_parameter"]["avatarPath"], "Scene/HeroAvatar")
        self.assertEqual(params_by_tool["vrc_ensure_expression_parameter"]["parameterName"], "Clothes")
        self.assertEqual(params_by_tool["vrc_ensure_animator_state"]["layerName"], "Clothes")
        self.assertEqual(params_by_tool["vrc_ensure_expression_menu_control"]["menuPath"], "Wardrobe")
        self.assertTrue(all(call.args[2]["preview"] for call in mock_invoke.call_args_list))
        self.assertTrue(
            all(call.kwargs.get("execution_context") == {"lane": "app_preview"} for call in mock_invoke.call_args_list)
        )

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_create_wardrobe_apply_forwards_without_preview(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "parameterName": "Clothes", "fxLayerName": "Clothes"}},
        )
        result = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.create_wardrobe({
            "avatarPath": "Scene/HeroAvatar",
            "parameterName": "Clothes",
        })
        self.assertTrue(result["ok"])
        self.assertEqual([call.args[1] for call in mock_invoke.call_args_list], [
            "vrc_ensure_expression_parameter",
            "vrc_ensure_animator_state",
            "vrc_ensure_expression_menu_control",
        ])
        self.assertFalse(any(call.args[2]["preview"] for call in mock_invoke.call_args_list))

    def test_create_wardrobe_registry_uses_typed_owner_and_canonical_plan(self) -> None:
        handler = dashboard_server.AGENT_GATEWAY._write_handlers[  # noqa: SLF001
            "vrcforge_create_wardrobe"
        ]
        self.assertEqual(
            handler.handler,
            dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.create_wardrobe,
        )
        self.assertTrue(handler.requires_approved_execution_context)
        self.assertIs(
            handler.checkpoint_prepare_handler,
            dashboard_server.prepare_authoritative_unity_checkpoint_sync,
        )
        arguments = {"parameterName": "Clothes", "menuName": False}
        self.assertEqual(
            handler.approved_execution_plan_builder(arguments),
            dashboard_server.build_workflow_execution_plan(
                "vrcforge_create_wardrobe",
                arguments,
            ),
        )

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_wardrobe_outfit_preview_forwards_with_flag(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"preview": True, "plan": {"value": 3}}},
        )
        result = dashboard_server.WARDROBE_OUTFIT_WORKFLOWS.preview_add_wardrobe_outfit({
            "avatar_path": "Scene/HeroAvatar",
            "parameter_name": "Clothes",
            "outfit_name": "Hoodie",
            "object_paths": ["Outfits/Hoodie"],
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_add_wardrobe_outfit")
        self.assertEqual(params["avatarPath"], "Scene/HeroAvatar")
        self.assertEqual(params["parameterName"], "Clothes")
        self.assertEqual(params["outfitName"], "Hoodie")
        self.assertEqual(params["objectPaths"], ["Outfits/Hoodie"])
        self.assertTrue(params["preview"])
        self.assertEqual(mock_invoke.call_args.kwargs.get("execution_context"), {"lane": "app_preview"})

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_wardrobe_outfit_apply_forwards_without_preview(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "assignedValue": 3, "fxStateName": "Hoodie"}},
        )
        result = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.add_wardrobe_outfit({
            "avatarPath": "Scene/HeroAvatar",
            "parameterName": "Clothes",
            "outfitName": "Hoodie",
            "objectPaths": ["Outfits/Hoodie"],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["assignedValue"], 3)
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_add_wardrobe_outfit")
        self.assertFalse(params["preview"])

    def test_add_wardrobe_outfit_requires_parameter_and_objects(self) -> None:
        missing_param = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.add_wardrobe_outfit({
            "outfit_name": "Hoodie",
            "object_paths": ["Outfits/Hoodie"],
        })
        self.assertFalse(missing_param["ok"])
        missing_objects = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.add_wardrobe_outfit({
            "parameter_name": "Clothes",
            "outfit_name": "Hoodie",
        })
        self.assertFalse(missing_objects["ok"])

    def test_outfit_part_writer_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        source = (editor_dir / "WardrobeOutfitPartWriter.cs").read_text(encoding="utf-8")
        # Declares the int-gated part toggle write tool.
        self.assertIn("[VRCForgeCommand(", source)
        self.assertIn('toolId: "vrc_add_outfit_part"', source)
        self.assertIn("public static object HandleCommand(JObject @params)", source)
        # Off -> On requires (int Equals N) AND (bool true); On -> Off fires on
        # (bool false) OR (int != N), so the toggle is inert unless outfit N is worn.
        self.assertIn("AnimatorConditionMode.Equals", source)
        self.assertIn("AnimatorConditionMode.If", source)
        self.assertIn("AnimatorConditionMode.IfNot", source)
        self.assertIn("AnimatorConditionMode.NotEqual", source)
        # Dedicated FX layer with explicit on/off clips, matching WD convention.
        self.assertIn("AddLayer", source)
        self.assertIn("writeDefaultValues", source)
        self.assertIn("m_IsActive", source)
        self.assertIn("AssetDatabase.CreateAsset", source)
        # Creates the Bool expression parameter and a bound menu toggle.
        self.assertIn("VRCExpressionParameters.ValueType.Bool", source)
        self.assertIn("ControlType.Toggle", source)
        # Write tool: Undo-registered for the checkpoint timeline, with preview path.
        self.assertIn("Undo.", source)
        self.assertIn("preview", source)
        # Apply payload must avoid the gateway unwrap-trap top-level keys.
        for trap_key in ('"data"', '"result"', '"payload"'):
            self.assertNotIn(trap_key + " =", source)

    def test_ma_component_writer_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        source = (editor_dir / "MAComponentWriter.cs").read_text(encoding="utf-8")
        self.assertIn("[VRCForgeCommand(", source)
        self.assertIn('toolId: "vrc_add_modular_avatar_component"', source)
        self.assertIn("public static object HandleCommand(JObject @params)", source)
        # Reflection-only MA access: no hard compile-time dependency on the package.
        self.assertIn("nadena.dev.modular_avatar.core.", source)
        self.assertNotIn("using nadena", source)
        # Common component aliases are supported.
        for alias in ("MergeArmature", "BoneProxy", "MenuInstaller", "MergeAnimator", "Parameters"):
            self.assertIn(alias, source)
        # Resolves MA's AvatarObjectReference fields via its Set(GameObject) method.
        self.assertIn("AvatarObjectReference", source)
        self.assertIn("referencePath", source)
        # Adds the component with Undo and supports a preview path.
        self.assertIn("Undo.AddComponent", source)
        self.assertIn("TryResolveReference", source)
        self.assertIn("Undo.RevertAllDownToGroup", source)
        self.assertIn("preview", source)
        self.assertIn(
            "FirstOrDefault(component => NormalizePath(GetTransformPath(component.transform)) == normalized)",
            source,
        )
        # Scene persistence is opt-in. A failed explicit save reverts the Undo
        # group, while a pre-existing dirty scene stays explicitly dirty.
        self.assertIn('var saveSceneToken = @params["saveScene"] ?? @params["save_scene"]', source)
        self.assertIn("EditorSceneManager.SaveScene(targetScene)", source)
        self.assertIn("EditorSceneManager.MarkSceneDirty(targetScene)", source)
        self.assertNotIn("EditorSceneManager.MarkSceneClean(targetScene)", source)
        self.assertNotIn("EditorSceneManager.ClearSceneDirtiness(targetScene)", source)
        self.assertLess(
            source.index("var sceneWasDirty = targetScene.IsValid() && targetScene.isDirty"),
            source.index("Undo.AddComponent(target, componentType)"),
        )
        # The paired inspector is read-only and reports exact component and
        # AvatarObjectReference state for post-apply/readback evidence.
        self.assertIn('toolId: "vrc_inspect_modular_avatar_component"', source)
        self.assertIn("HandleInspectCommand", source)
        self.assertIn("present = components.Length > 0", source)
        self.assertIn("count = components.Length", source)
        self.assertIn("type = componentType.FullName", source)
        self.assertIn("sceneDirty", source)
        self.assertIn("referencePath", source)
        self.assertIn("resolvedPath", source)
        inspect_body = source[
            source.index("internal static object HandleInspectCommand"):
            source.index("// --- reference / field application")
        ]
        for write_api in ("Undo.", "SetDirty(", "MarkSceneDirty(", "SaveScene("):
            self.assertNotIn(write_api, inspect_body)

    def test_add_outfit_part_and_ma_registered_in_gateway(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest?exposure_layer=execution", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        wardrobe_skill = next(skill for skill in payload["skills"] if skill["name"] == "wardrobe-control")
        allowed_tools = set(wardrobe_skill["allowedTools"])
        # Previews are directly callable read/plan tools, never approval-gated writes.
        self.assertIn("vrcforge_preview_add_outfit_part", tool_names)
        self.assertNotIn("vrcforge_preview_add_outfit_part", write_targets)
        self.assertIn("vrcforge_preview_add_modular_avatar_component", tool_names)
        self.assertNotIn("vrcforge_preview_add_modular_avatar_component", write_targets)
        self.assertIn("vrcforge_inspect_modular_avatar_component", tool_names)
        self.assertNotIn("vrcforge_inspect_modular_avatar_component", write_targets)
        self.assertNotIn(
            "vrc_inspect_modular_avatar_component",
            dashboard_server.VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST,
        )
        # The writes are approval-gated write targets, never direct read tools.
        self.assertIn("vrcforge_add_outfit_part", write_targets)
        self.assertNotIn("vrcforge_add_outfit_part", tool_names)
        self.assertIn("vrcforge_add_modular_avatar_component", write_targets)
        self.assertNotIn("vrcforge_add_modular_avatar_component", tool_names)
        self.assertIn("vrcforge_preview_add_outfit_part", allowed_tools)
        self.assertIn("vrcforge_add_outfit_part", allowed_tools)
        self.assertIn("vrcforge_preview_add_modular_avatar_component", allowed_tools)
        self.assertIn("vrcforge_add_modular_avatar_component", allowed_tools)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_outfit_part_preview_forwards_with_flag(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"preview": True, "plan": {"partParameterName": "Hat"}}},
        )
        result = dashboard_server.WARDROBE_OUTFIT_WORKFLOWS.preview_add_outfit_part({
            "avatar_path": "Scene/HeroAvatar",
            "parameter_name": "Clothes",
            "part_name": "Hat",
            "value": 2,
            "object_paths": ["Outfits/Hoodie/Hat"],
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_add_outfit_part")
        self.assertEqual(params["parameterName"], "Clothes")
        self.assertEqual(params["partName"], "Hat")
        self.assertEqual(params["value"], 2)
        self.assertEqual(params["objectPaths"], ["Outfits/Hoodie/Hat"])
        self.assertTrue(params["preview"])
        self.assertEqual(mock_invoke.call_args.kwargs.get("execution_context"), {"lane": "app_preview"})

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_modular_avatar_component_preview_uses_app_preview_lane(
        self,
        mock_load_settings,
        mock_invoke,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "preview": True}},
        )

        result = dashboard_server.WARDROBE_OUTFIT_WORKFLOWS.preview_add_modular_avatar_component(
            {
                "avatarPath": "Scene/HeroAvatar",
                "gameObjectPath": "Scene/HeroAvatar/Outfit",
                "componentType": "MergeArmature",
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(mock_invoke.call_args.args[1], "vrc_add_modular_avatar_component")
        self.assertEqual(mock_invoke.call_args.kwargs.get("execution_context"), {"lane": "app_preview"})

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_outfit_part_apply_forwards_without_preview(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "assignedPartParameter": "Hat", "fxLayerName": "Hat (part)"}},
        )
        result = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.add_outfit_part({
            "avatarPath": "Scene/HeroAvatar",
            "parameterName": "Clothes",
            "partName": "Hat",
            "outfitValue": 2,
            "objectPaths": ["Outfits/Hoodie/Hat"],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["assignedPartParameter"], "Hat")
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_add_outfit_part")
        self.assertEqual(params["value"], 2)
        self.assertFalse(params["preview"])

    def test_add_outfit_part_requires_parameter_value_and_objects(self) -> None:
        missing_value = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.add_outfit_part({
            "parameter_name": "Clothes",
            "part_name": "Hat",
            "object_paths": ["Outfits/Hoodie/Hat"],
        })
        self.assertFalse(missing_value["ok"])
        missing_objects = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.add_outfit_part({
            "parameter_name": "Clothes",
            "part_name": "Hat",
            "value": 2,
        })
        self.assertFalse(missing_objects["ok"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_modular_avatar_component_forwards_references_and_fields(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "addedComponent": True}},
        )
        result = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.add_modular_avatar_component({
            "game_object_path": "HeroAvatar/Outfits/Hoodie",
            "component_type": "MergeArmature",
            "avatar_path": "Scene/HeroAvatar",
            "references": {"mergeTarget": "Armature"},
            "fields": {"prefix": "", "suffix": ""},
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_add_modular_avatar_component")
        self.assertEqual(params["componentType"], "MergeArmature")
        self.assertEqual(params["references"], {"mergeTarget": "Armature"})
        self.assertEqual(params["fields"], {"prefix": "", "suffix": ""})
        self.assertFalse(params["preview"])
        self.assertFalse(params["saveScene"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_modular_avatar_component_only_forwards_explicit_scene_save(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "addedComponent": True, "sceneSaved": True}},
        )

        result = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.add_modular_avatar_component({
            "gameObjectPath": "HeroAvatar/Outfits/Hoodie",
            "componentType": "MergeArmature",
            "saveScene": True,
        })

        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_add_modular_avatar_component")
        self.assertTrue(params["saveScene"])

    @patch("dashboard_server.emit_log")
    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_modular_avatar_component_live_guard_uses_only_bound_connection(
        self,
        mock_load_settings,
        mock_invoke,
        mock_log,
    ) -> None:
        params = {"expectedRunIdDigest": "3" * 64}
        connection = Mock()
        connection.apply_component.return_value = {"ok": True, "live": True}

        with patch.object(
            dashboard_server,
            "PRIMITIVE_BASIS_LIVE_CONNECTION",
            connection,
        ):
            result = (
                dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES
                .add_modular_avatar_component(params)
            )

        self.assertEqual(result, {"ok": True, "live": True})
        connection.apply_component.assert_called_once_with(params)
        mock_load_settings.assert_not_called()
        mock_invoke.assert_not_called()
        mock_log.assert_not_called()

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_inspect_modular_avatar_component_is_exact_read_forwarding(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={
                "data": {
                    "ok": True,
                    "present": True,
                    "count": 1,
                    "type": "nadena.dev.modular_avatar.core.ModularAvatarMergeArmature",
                    "sceneDirty": True,
                    "references": [
                        {
                            "member": "mergeTarget",
                            "referencePath": "Armature",
                            "resolvedPath": "HeroAvatar/Armature",
                        }
                    ],
                }
            },
        )

        result = dashboard_server.inspect_modular_avatar_component_sync({
            "game_object_path": "HeroAvatar/Outfits/Hoodie",
            "component_type": "MergeArmature",
            "avatar_path": "HeroAvatar",
        })

        self.assertTrue(result["ok"])
        self.assertTrue(result["present"])
        self.assertEqual(result["references"][0]["referencePath"], "Armature")
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_inspect_modular_avatar_component")
        self.assertEqual(
            params,
            {
                "gameObjectPath": "HeroAvatar/Outfits/Hoodie",
                "componentType": "MergeArmature",
                "avatarPath": "HeroAvatar",
            },
        )

    def test_add_modular_avatar_component_requires_target_and_type(self) -> None:
        missing_type = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.add_modular_avatar_component({
            "game_object_path": "HeroAvatar/Outfits/Hoodie",
        })
        self.assertFalse(missing_type["ok"])
        missing_target = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.add_modular_avatar_component({
            "component_type": "MergeArmature",
        })
        self.assertFalse(missing_target["ok"])

    def test_avatar_primitive_crud_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor" / "Generic"
        source = (editor_dir / "UnityAvatarPrimitiveCrud.cs").read_text(encoding="utf-8")
        for tool_name in (
            "vrc_read_avatar_descriptor",
            "vrc_write_avatar_descriptor",
            "vrc_write_animation_curve",
            "vrc_manage_expression_parameters",
            "vrc_manage_expression_menu",
            "vrc_manage_fx_animator",
        ):
            self.assertIn(f'toolId: "{tool_name}"', source)
        self.assertIn("Undo.RegisterCompleteObjectUndo", source)
        self.assertIn("AnimationUtility.SetEditorCurve", source)
        self.assertIn("VRCAvatarDescriptor", source)
        self.assertIn("VRCExpressionsMenu", source)
        self.assertIn("AnimatorController", source)

    def test_avatar_primitive_tools_registered_with_safe_exposure(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest?exposure_layer=execution", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        authoring_skill = next(skill for skill in payload["skills"] if skill["name"] == "avatar-authoring-primitives")
        allowed_tools = set(authoring_skill["allowedTools"])
        for name in (
            "vrcforge_read_avatar_descriptor",
            "vrcforge_preview_write_avatar_descriptor",
            "vrcforge_preview_write_animation_curve",
            "vrcforge_preview_manage_expression_parameters",
            "vrcforge_preview_manage_expression_menu",
            "vrcforge_preview_manage_fx_animator",
        ):
            self.assertIn(name, tool_names)
            self.assertNotIn(name, write_targets)
            self.assertIn(name, allowed_tools)
        for name in (
            "vrcforge_write_avatar_descriptor",
            "vrcforge_write_animation_curve",
            "vrcforge_manage_expression_parameters",
            "vrcforge_manage_expression_menu",
            "vrcforge_manage_fx_animator",
        ):
            self.assertIn(name, write_targets)
            self.assertNotIn(name, tool_names)
            self.assertIn(name, allowed_tools)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_avatar_primitive_wrappers_forward_to_unity_tools(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "preview": True}},
        )

        calls = [
            (dashboard_server.read_avatar_descriptor_sync, {"avatar_path": "Avatar"}, "vrc_read_avatar_descriptor"),
            (lambda params: dashboard_server.write_avatar_descriptor_sync(params, preview=True), {"avatar_path": "Avatar", "view_position": {"x": 0, "y": 1.5, "z": 0}}, "vrc_write_avatar_descriptor"),
            (lambda params: dashboard_server.write_animation_curve_sync(params, preview=True), {"clip_path": "Assets/Test.anim", "binding_path": "Hat", "component_type": "GameObject", "property_name": "m_IsActive", "constant_float": 1}, "vrc_write_animation_curve"),
            (lambda params: dashboard_server.manage_expression_parameters_sync(params, preview=True), {"avatar_path": "Avatar", "action": "delete", "parameter_name": "Old"}, "vrc_manage_expression_parameters"),
            (lambda params: dashboard_server.manage_expression_menu_sync(params, preview=True), {"avatar_path": "Avatar", "action": "delete", "control_name": "Old"}, "vrc_manage_expression_menu"),
            (lambda params: dashboard_server.manage_fx_animator_sync(params, preview=True), {"avatar_path": "Avatar", "action": "delete_state", "layer_name": "FX", "state_name": "Old"}, "vrc_manage_fx_animator"),
        ]

        for func, params, expected_tool in calls:
            mock_invoke.reset_mock()
            result = func(params)
            self.assertTrue(result["ok"])
            _settings, tool_name, forwarded = mock_invoke.call_args.args
            self.assertEqual(tool_name, expected_tool)
            if expected_tool != "vrc_read_avatar_descriptor":
                self.assertTrue(forwarded["preview"])
                self.assertEqual(
                    mock_invoke.call_args.kwargs.get("execution_context"),
                    {"lane": "app_preview"},
                )

        _, _tool_name, curve_params = mock_invoke.call_args.args
        self.assertIn("action", curve_params)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_ensure_preview_wrappers_use_app_preview_lane(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "preview": True}},
        )
        calls = [
            (
                dashboard_server.ensure_expression_parameter_sync,
                {"avatarPath": "Avatar", "parameterName": "Clothes"},
                "vrc_ensure_expression_parameter",
            ),
            (
                dashboard_server.ensure_expression_menu_control_sync,
                {"avatarPath": "Avatar", "controlName": "Clothes"},
                "vrc_ensure_expression_menu_control",
            ),
            (
                dashboard_server.ensure_animator_state_sync,
                {
                    "avatarPath": "Avatar",
                    "layerName": "FX",
                    "stateName": "Clothes",
                    "parameterName": "Clothes",
                },
                "vrc_ensure_animator_state",
            ),
        ]
        for wrapper, params, tool_name in calls:
            with self.subTest(tool_name=tool_name):
                mock_invoke.reset_mock()
                result = wrapper(params, preview=True)
                self.assertTrue(result["ok"])
                self.assertEqual(mock_invoke.call_args.args[1], tool_name)
                self.assertTrue(mock_invoke.call_args.args[2]["preview"])
                self.assertEqual(mock_invoke.call_args.kwargs.get("execution_context"), {"lane": "app_preview"})

    def test_manage_wardrobe_request_parses_actions_values_and_flags(self) -> None:
        request = build_manage_wardrobe_request(
            {
                "action": "reorder_outfits",
                "avatarPath": "Scene/HeroAvatar",
                "parameterName": "Clothes",
                "orderValues": "3, 1, 2",
                "deleteObjects": "true",
                "deleteGeneratedAssets": "false",
                "confirmDeleteWardrobe": "true",
            },
            preview=False,
        )
        self.assertEqual(request["action"], "reorder_outfits")
        self.assertEqual(request["avatarPath"], "Scene/HeroAvatar")
        self.assertEqual(request["parameterName"], "Clothes")
        self.assertEqual(request["orderValues"], [3, 1, 2])
        self.assertTrue(request["deleteObjects"])
        self.assertFalse(request["deleteGeneratedAssets"])
        self.assertTrue(request["confirmDeleteWardrobe"])
        self.assertFalse(request["preview"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_manage_wardrobe_preview_forwards_with_flag(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"preview": True, "plan": {"action": "remove_outfit", "targetValues": [3]}}},
        )
        result = dashboard_server.WARDROBE_OUTFIT_WORKFLOWS.preview_manage_wardrobe({
            "avatar_path": "Scene/HeroAvatar",
            "parameter_name": "Clothes",
            "action": "remove_outfit",
            "target_value": 3,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_manage_wardrobe")
        self.assertEqual(params["avatarPath"], "Scene/HeroAvatar")
        self.assertEqual(params["parameterName"], "Clothes")
        self.assertEqual(params["action"], "remove_outfit")
        self.assertEqual(params["targetValue"], 3)
        self.assertTrue(params["preview"])
        self.assertEqual(mock_invoke.call_args.kwargs.get("execution_context"), {"lane": "app_preview"})

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_manage_wardrobe_apply_forwards_without_preview(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "action": "rename_outfit", "targetValues": [3], "newName": "Coat"}},
        )
        result = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.manage_wardrobe({
            "avatarPath": "Scene/HeroAvatar",
            "parameterName": "Clothes",
            "action": "rename_outfit",
            "value": 3,
            "newName": "Coat",
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "rename_outfit")
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_manage_wardrobe")
        self.assertFalse(params["preview"])
        self.assertEqual(params["value"], 3)
        self.assertEqual(params["newName"], "Coat")

    def test_manage_wardrobe_requires_action_and_parameter(self) -> None:
        missing_action = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.manage_wardrobe({"parameterName": "Clothes"})
        self.assertFalse(missing_action["ok"])
        missing_parameter = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.manage_wardrobe({"action": "remove_outfit", "targetValue": 3})
        self.assertFalse(missing_parameter["ok"])

    def test_manage_wardrobe_registry_keeps_handler_checkpoint_and_plan_identity(self) -> None:
        target = "vrcforge_manage_wardrobe"
        handler = dashboard_server.AGENT_GATEWAY._write_handlers[target]
        params = {
            "action": "rename_outfit",
            "parameterName": "Clothes",
            "targetValue": 3,
            "newName": "Coat",
        }

        self.assertIs(
            handler.handler,
            dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.manage_wardrobe,
        )
        self.assertIn(
            target,
            dashboard_server.VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS,
        )
        self.assertIsNotNone(handler.approved_execution_plan_builder)
        self.assertEqual(
            handler.approved_execution_plan_builder(params),
            [
                (
                    "vrc_manage_wardrobe",
                    build_manage_wardrobe_request(params, False),
                )
            ],
        )

    def test_checkpoint_timeline_wraps_approved_write_and_restores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "Assets" / "existing.txt").write_text("before", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            for args in (
                ["init"],
                ["config", "user.email", "test@example.invalid"],
                ["config", "user.name", "Test User"],
                ["add", "-A"],
                ["commit", "-m", "initial"],
            ):
                proc = subprocess.run(["git", *args], cwd=str(project), capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)

            def write_handler(args: dict) -> dict:
                (Path(args["projectRoot"]) / "Assets" / "generated.txt").write_text("after", encoding="utf-8")
                return {"ok": True, "wrote": "Assets/generated.txt"}

            original_handlers = dict(dashboard_server.AGENT_GATEWAY._write_handlers)
            original_prepare = dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler
            original_restore_prepare = dashboard_server.AGENT_GATEWAY.checkpoint_restore_prepare_handler
            original_reload = dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler
            try:
                dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = lambda _root: {"ok": True}
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_prepare_handler = lambda _root: {"ok": True, "scenes": []}
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler = lambda _root, _prepare: {"ok": True}
                dashboard_server.AGENT_GATEWAY.register_write_handler(
                    "vrcforge_test_checkpoint_write",
                    "Test checkpoint write.",
                    "high",
                    write_handler,
                )
                request = dashboard_server.AGENT_GATEWAY.create_apply_request({
                    "target_tool": "vrcforge_test_checkpoint_write",
                    "arguments": {"projectRoot": str(project)},
                })
                approval_id = request["approval"]["id"]
                dashboard_server.AGENT_GATEWAY.approve(approval_id)
                applied = dashboard_server.AGENT_GATEWAY.apply_approved({"approval_id": approval_id})

                self.assertTrue(applied["ok"])
                self.assertTrue(applied["checkpoint"]["ok"])
                self.assertTrue((project / "Assets" / "generated.txt").exists())

                listed = dashboard_server.AGENT_GATEWAY.list_checkpoints({"projectRoot": str(project)})
                self.assertEqual(listed["count"], 1)
                checkpoint_id = listed["checkpoints"][0]["id"]
                preview = dashboard_server.AGENT_GATEWAY.preview_restore_checkpoint({"checkpointId": checkpoint_id})
                self.assertTrue(preview["ok"])
                self.assertTrue(any("generated.txt" in item for item in preview["workingTreeStatus"] + preview["changedFiles"]))

                restored = dashboard_server.AGENT_GATEWAY.restore_checkpoint({
                    "checkpointId": checkpoint_id,
                    "confirmRestore": True,
                })
                self.assertTrue(restored["ok"])
                self.assertFalse((project / "Assets" / "generated.txt").exists())
            finally:
                dashboard_server.AGENT_GATEWAY._write_handlers = original_handlers
                dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = original_prepare
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_prepare_handler = original_restore_prepare
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler = original_reload

    def test_archive_checkpoint_restores_non_git_unity_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            existing = project / "Assets" / "existing.txt"
            existing.write_text("before", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            prepared: list[Path] = []
            reloaded: list[Path] = []
            gateway.checkpoint_prepare_handler = lambda path: prepared.append(path) or {"ok": True}
            gateway.checkpoint_restore_handler = lambda path, _prepare: reloaded.append(path) or {"ok": True}

            def write_handler(args: dict) -> dict:
                project_root = Path(args["projectRoot"])
                (project_root / "Assets" / "existing.txt").write_text("after", encoding="utf-8")
                (project_root / "Assets" / "generated.txt").write_text("generated", encoding="utf-8")
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_archive_write", "Archive write", "high", write_handler)
            request = gateway.create_apply_request({
                "target_tool": "vrcforge_test_archive_write",
                "arguments": {"projectRoot": str(project)},
            })
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            applied = gateway.apply_approved({"approval_id": approval_id})

            self.assertTrue(applied["ok"])
            self.assertEqual(applied["checkpoint"]["strategy"], "archive")
            self.assertTrue(Path(applied["checkpoint"]["archivePath"]).is_file())
            self.assertEqual(existing.read_text(encoding="utf-8"), "after")
            self.assertEqual(prepared, [project.resolve()])
            bee_cache = project / "Library" / "Bee"
            script_cache = project / "Library" / "ScriptAssemblies"
            package_cache = project / "Library" / "PackageCache"
            bee_cache.mkdir(parents=True)
            script_cache.mkdir(parents=True)
            package_cache.mkdir(parents=True)
            (bee_cache / "stale-inputdata.json").write_text("Packages/com.deleted.shader", encoding="utf-8")
            (script_cache / "stale.dll").write_text("stale", encoding="utf-8")
            (package_cache / "stale-package").write_text("stale", encoding="utf-8")

            checkpoint_id = applied["checkpoint"]["id"]
            preview = gateway.preview_restore_checkpoint({"checkpointId": checkpoint_id})
            self.assertTrue(preview["ok"])
            self.assertTrue(any("existing.txt" in item for item in preview["changedFiles"]))
            self.assertTrue(any("generated.txt" in item for item in preview["changedFiles"]))

            restored = gateway.restore_checkpoint({"checkpointId": checkpoint_id, "confirmRestore": True})
            self.assertTrue(restored["ok"])
            self.assertEqual(existing.read_text(encoding="utf-8"), "before")
            self.assertFalse((project / "Assets" / "generated.txt").exists())
            self.assertFalse(bee_cache.exists())
            self.assertFalse(script_cache.exists())
            self.assertTrue(package_cache.is_dir())
            self.assertEqual((package_cache / "stale-package").read_text(encoding="utf-8"), "stale")
            self.assertFalse(restored["unityCacheCleanup"]["skipped"])
            self.assertIn(str(bee_cache.resolve()), restored["unityCacheCleanup"]["deleted"])
            self.assertIn(str(script_cache.resolve()), restored["unityCacheCleanup"]["deleted"])
            self.assertNotIn(str(package_cache.resolve()), restored["unityCacheCleanup"]["deleted"])
            self.assertIn(str(package_cache.resolve()), restored["unityCacheCleanup"]["preserved"])
            self.assertEqual(reloaded, [project.resolve()])

    def test_archive_restore_closes_unity_before_files_and_reloads_exact_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            existing = project / "Assets" / "existing.txt"
            existing.write_text("before", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
                "m_EditorVersion: 2022.3", encoding="utf-8"
            )

            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            events: list[tuple[str, str]] = []
            restore_context = {
                "ok": True,
                "phase": "prepare_restore",
                "scenes": ["Assets/Avatar.unity", "Assets/Lighting.unity"],
                "activeScenePath": "Assets/Lighting.unity",
            }

            def prepare_restore(_path: Path) -> dict[str, object]:
                events.append(("prepare", existing.read_text(encoding="utf-8")))
                return dict(restore_context)

            def reload_restore(_path: Path, prepared: dict[str, object]) -> dict[str, object]:
                self.assertEqual(prepared, restore_context)
                events.append(("reload", existing.read_text(encoding="utf-8")))
                return {"ok": True}

            gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}
            gateway.checkpoint_restore_prepare_handler = prepare_restore
            gateway.checkpoint_restore_handler = reload_restore
            def write_restore_transaction(_args: dict[str, object]) -> dict[str, object]:
                existing.write_text("after", encoding="utf-8")
                return {"ok": True}

            gateway.register_write_handler(
                "vrcforge_test_restore_transaction",
                "Restore transaction ordering test.",
                "high",
                write_restore_transaction,
            )
            request = gateway.create_apply_request(
                {
                    "target_tool": "vrcforge_test_restore_transaction",
                    "arguments": {"projectRoot": str(project)},
                }
            )
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            applied = gateway.apply_approved({"approval_id": approval_id})

            restored = gateway.restore_checkpoint(
                {"checkpointId": applied["checkpoint"]["id"], "confirmRestore": True}
            )

            self.assertTrue(restored["ok"])
            self.assertEqual(events, [("prepare", "after"), ("reload", "before")])
            self.assertEqual(restored["unityRestorePrepare"], restore_context)

    def test_archive_restore_prepare_failure_leaves_project_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            existing = project / "Assets" / "existing.txt"
            existing.write_text("before", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
                "m_EditorVersion: 2022.3", encoding="utf-8"
            )

            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}
            gateway.checkpoint_restore_prepare_handler = lambda _path: {
                "ok": False,
                "error": "Unity scene close failed.",
            }
            gateway.checkpoint_restore_handler = lambda _path, _prepare: self.fail(
                "Reload must not run when restore preparation fails."
            )
            def write_restore_prepare_failure(_args: dict[str, object]) -> dict[str, object]:
                existing.write_text("after", encoding="utf-8")
                return {"ok": True}

            gateway.register_write_handler(
                "vrcforge_test_restore_prepare_failure",
                "Restore prepare failure test.",
                "high",
                write_restore_prepare_failure,
            )
            request = gateway.create_apply_request(
                {
                    "target_tool": "vrcforge_test_restore_prepare_failure",
                    "arguments": {"projectRoot": str(project)},
                }
            )
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            applied = gateway.apply_approved({"approval_id": approval_id})

            restored = gateway.restore_checkpoint(
                {"checkpointId": applied["checkpoint"]["id"], "confirmRestore": True}
            )

            self.assertFalse(restored["ok"])
            self.assertEqual(restored["status"], "restore_prepare_failed")
            self.assertEqual(existing.read_text(encoding="utf-8"), "after")

    def test_archive_checkpoint_rejects_mutable_archive_path_outside_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "Assets" / "existing.txt").write_text("before", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            checkpoint = gateway._create_pre_write_checkpoint(  # noqa: SLF001 - regression covers checkpoint metadata handling.
                {"id": "approval-test", "targetTool": "vrcforge_test_archive_write"},
                {"projectRoot": str(project)},
            )
            self.assertIsNotNone(checkpoint)
            self.assertTrue(checkpoint["ok"])

            external_zip = root / "outside.zip"
            with zipfile.ZipFile(external_zip, "w") as archive:
                archive.writestr("Assets/existing.txt", "outside")
            records = [json.loads(line) for line in gateway.checkpoint_log_path.read_text(encoding="utf-8").splitlines()]
            records[-1]["archivePath"] = str(external_zip)
            gateway.checkpoint_log_path.write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )

            preview = gateway.preview_restore_checkpoint({"checkpointId": checkpoint["id"]})
            restored = gateway.restore_checkpoint({"checkpointId": checkpoint["id"], "confirmRestore": True})

            self.assertFalse(preview["ok"])
            self.assertIn("outside configured storage", preview["error"])
            self.assertFalse(restored["ok"])
            self.assertEqual((project / "Assets" / "existing.txt").read_text(encoding="utf-8"), "before")

    def test_local_state_checkpoint_rejects_mutable_archive_path_outside_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            skill_dir = gateway.user_skills_dir / "avatar-review"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("before", encoding="utf-8")

            checkpoint = gateway._create_pre_write_checkpoint(  # noqa: SLF001 - regression covers checkpoint metadata handling.
                {"id": "approval-local", "targetTool": "vrcforge_import_skill_package"},
                {},
            )
            self.assertIsNotNone(checkpoint)
            self.assertTrue(checkpoint["ok"])

            external_zip = root / "outside-local.zip"
            with zipfile.ZipFile(external_zip, "w") as archive:
                archive.writestr("skills/avatar-review/SKILL.md", "outside")
            records = [json.loads(line) for line in gateway.checkpoint_log_path.read_text(encoding="utf-8").splitlines()]
            records[-1]["archivePath"] = str(external_zip)
            gateway.checkpoint_log_path.write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )

            preview = gateway.preview_restore_checkpoint({"checkpointId": checkpoint["id"]})
            restored = gateway.restore_checkpoint({"checkpointId": checkpoint["id"], "confirmRestore": True})

            self.assertFalse(preview["ok"])
            self.assertIn("outside configured storage", preview["error"])
            self.assertFalse(restored["ok"])
            self.assertEqual((skill_dir / "SKILL.md").read_text(encoding="utf-8"), "before")

    def test_checkpoint_rollback_coverage_audit_tracks_ma_vrcf_ndmf_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "UnityProject"
            (project / "Assets" / "Scenes").mkdir(parents=True)
            (project / "Assets" / "Prefabs").mkdir()
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            scene = project / "Assets" / "Scenes" / "Avatar.unity"
            prefab = project / "Assets" / "Prefabs" / "Outfit.prefab"
            generated = project / "Assets" / "VRCForge" / "Generated" / "RollbackAudit" / "generated.anim"
            manifest = project / "Packages" / "manifest.json"
            lock = project / "Packages" / "packages-lock.json"
            scene.write_text("before scene with MA component", encoding="utf-8")
            prefab.write_text("before prefab with VRCF component", encoding="utf-8")
            manifest.write_text(
                json.dumps(
                    {
                        "dependencies": {
                            "nadena.dev.modular-avatar": "1.17.1",
                            "com.vrcfury.vrcfury": "1.1334.0",
                            "nadena.dev.ndmf": "1.13.1",
                        }
                    }
                ),
                encoding="utf-8",
            )
            lock.write_text(
                json.dumps(
                    {
                        "dependencies": {
                            "nadena.dev.modular-avatar": {"version": "1.17.1"},
                            "com.vrcfury.vrcfury": {"version": "1.1334.0"},
                            "nadena.dev.ndmf": {"version": "1.13.1"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}
            gateway.checkpoint_restore_handler = lambda _path, _prepare: {"ok": True}

            def write_handler(args: dict) -> dict:
                project_root = Path(args["projectRoot"])
                (project_root / "Assets" / "Scenes" / "Avatar.unity").write_text("after scene", encoding="utf-8")
                (project_root / "Assets" / "Prefabs" / "Outfit.prefab").write_text("after prefab", encoding="utf-8")
                generated.parent.mkdir(parents=True)
                generated.write_text("generated", encoding="utf-8")
                manifest.write_text(
                    json.dumps({"dependencies": {"nadena.dev.modular-avatar": "1.17.1"}}),
                    encoding="utf-8",
                )
                package_cache = project_root / "Library" / "PackageCache"
                bee_cache = project_root / "Library" / "Bee"
                script_cache = project_root / "Library" / "ScriptAssemblies"
                package_cache.mkdir(parents=True)
                bee_cache.mkdir(parents=True)
                script_cache.mkdir(parents=True)
                (package_cache / "com.vrcfury.vrcfury@1.1334.0").write_text("stale", encoding="utf-8")
                (bee_cache / "inputdata.json").write_text("stale", encoding="utf-8")
                (script_cache / "Assembly-CSharp.dll").write_text("stale", encoding="utf-8")
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_ma_vrcf_rollback", "MA/VRCF rollback", "high", write_handler)
            request = gateway.create_apply_request({
                "target_tool": "vrcforge_test_ma_vrcf_rollback",
                "arguments": {"projectRoot": str(project)},
            })
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            applied = gateway.apply_approved({"approval_id": approval_id})

            self.assertTrue(applied["ok"])
            checkpoint_audit = applied["checkpoint"]["rollbackCoverageAudit"]
            self.assertEqual(checkpoint_audit["schema"], "vrcforge.rollback_coverage_audit.v1")
            checkpoint_checks = {item["id"]: item for item in checkpoint_audit["checks"]}
            self.assertEqual(checkpoint_checks["scene_prefab_component_state"]["status"], "covered")
            self.assertEqual(checkpoint_checks["packages_manifest"]["status"], "covered")
            frameworks = checkpoint_checks["packages_manifest"]["frameworkPackages"]["packages"]
            self.assertTrue(frameworks["modular_avatar"]["detected"])
            self.assertTrue(frameworks["vrcfury"]["detected"])
            self.assertTrue(frameworks["ndmf"]["detected"])

            preview = gateway.preview_restore_checkpoint({"checkpointId": applied["checkpoint"]["id"]})
            self.assertTrue(preview["ok"])
            self.assertEqual(preview["rollbackCoverageAudit"]["phase"], "preview")
            preview_checks = {item["id"]: item for item in preview["rollbackCoverageAudit"]["checks"]}
            preview_frameworks = preview_checks["packages_manifest"]["frameworkPackages"]["packages"]
            self.assertTrue(preview_frameworks["vrcfury"]["detected"])

            restored = gateway.restore_checkpoint({"checkpointId": applied["checkpoint"]["id"], "confirmRestore": True})

            self.assertTrue(restored["ok"])
            self.assertEqual(scene.read_text(encoding="utf-8"), "before scene with MA component")
            self.assertEqual(prefab.read_text(encoding="utf-8"), "before prefab with VRCF component")
            self.assertFalse(generated.exists())
            restored_manifest = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertIn("com.vrcfury.vrcfury", restored_manifest["dependencies"])
            package_cache = project / "Library" / "PackageCache"
            self.assertTrue(package_cache.is_dir())
            self.assertEqual((package_cache / "com.vrcfury.vrcfury@1.1334.0").read_text(encoding="utf-8"), "stale")
            self.assertFalse((project / "Library" / "Bee").exists())
            self.assertFalse((project / "Library" / "ScriptAssemblies").exists())
            restore_audit = restored["rollbackCoverageAudit"]
            restore_checks = {item["id"]: item for item in restore_audit["checks"]}
            self.assertEqual(restore_audit["gateStatus"], "todo")
            self.assertEqual(restore_checks["package_cache_generated_folders"]["status"], "passed")
            self.assertEqual(restore_checks["unity_reload_after_restore"]["status"], "passed")
            self.assertEqual(restore_checks["validation_after_restore"]["status"], "todo")
            self.assertTrue(any(item["id"] == "run_post_restore_validation" for item in restore_audit["todos"]))

    def test_skill_package_write_uses_local_state_checkpoint_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "avatar-review.vsk"
            source = root / "source"
            source.mkdir()
            (source / "manifest.json").write_text(
                json.dumps(
                    {
                        "id": "community.avatar-review",
                        "name": "Avatar Review Package",
                        "skill_name": "avatar-review",
                        "version": "1.0.0",
                        "author": "Unit Test",
                        "description": "Dashboard skill package fixture.",
                        "min_vrcforge_version": "0.5.0",
                        "permissions": ["read_project"],
                        "entrypoints": {"skill": "SKILL.md"},
                    }
                ),
                encoding="utf-8",
            )
            (source / "SKILL.md").write_text(
                "---\n"
                "name: avatar-review\n"
                "title: Avatar Review\n"
                "description: Imported package skill.\n"
                "allowed-tools:\n"
                "  - vrcforge_health\n"
                "entrypoint-tool: vrcforge_health\n"
                "---\n"
                "Inspect project state before edits.\n",
                encoding="utf-8",
            )
            SkillPackageService(root / "build-store", vrcforge_version="0.5.1").export_dev(source, package)

            gateway = AgentGateway(root / "app" / "config" / "agent_gateway.json", root / "audit")
            original_gateway = dashboard_server.AGENT_GATEWAY
            try:
                dashboard_server.AGENT_GATEWAY = gateway
                dashboard_server.register_agent_gateway_tools()
                request = gateway.create_apply_request(
                    {
                        "target_tool": "vrcforge_import_skill_package",
                        "arguments": {"packagePath": str(package)},
                    }
                )
                gateway.approve(request["approval"]["id"])
                applied = gateway.apply_approved({"approval_id": request["approval"]["id"]})

                self.assertTrue(applied["ok"])
                checkpoint = applied["checkpoint"]
                self.assertEqual(checkpoint["strategy"], "local_state_archive")
                self.assertEqual(checkpoint["pathspecs"], ["skill-packages", "skills"])
                self.assertTrue((gateway.user_skills_dir / "avatar-review" / "SKILL.md").is_file())
                self.assertTrue((gateway.user_constraints_path.parent / "skill-packages" / "community.avatar-review").is_dir())
                preview = gateway.preview_restore_checkpoint({"checkpointId": checkpoint["id"]})
                self.assertTrue(preview["ok"])
                self.assertTrue(any("avatar-review" in item for item in preview["workingTreeStatus"] + preview["changedFiles"]))

                restored = gateway.restore_checkpoint({"checkpointId": checkpoint["id"], "confirmRestore": True})

                self.assertTrue(restored["ok"])
                self.assertEqual(restored["status"], "restored")
                self.assertFalse((gateway.user_skills_dir / "avatar-review").exists())
                self.assertFalse((gateway.user_constraints_path.parent / "skill-packages").exists())
                audit = restored["rollbackCoverageAudit"]
                checks = {item["id"]: item for item in audit["checks"]}
                self.assertEqual(checks["local_skill_package_store"]["status"], "covered")
                self.assertEqual(checks["local_projected_user_skills"]["status"], "covered")
            finally:
                dashboard_server.AGENT_GATEWAY = original_gateway
                dashboard_server.register_agent_gateway_tools()

    def test_failed_write_after_checkpoint_returns_checkpoint_for_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "Assets" / "existing.txt").write_text("before", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}

            def failing_write(args: dict) -> dict:
                project_root = Path(args["projectRoot"])
                (project_root / "Assets" / "generated-before-fail.txt").write_text("generated", encoding="utf-8")
                raise RuntimeError("Unity MCP disconnected after checkpoint")

            gateway.register_write_handler("vrcforge_test_failing_write", "Failing write", "high", failing_write)
            request = gateway.create_apply_request({
                "target_tool": "vrcforge_test_failing_write",
                "arguments": {"projectRoot": str(project)},
            })
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            applied = gateway.apply_approved({"approval_id": approval_id})

            self.assertFalse(applied["ok"])
            self.assertEqual(applied["status"], "failed")
            self.assertIn("Unity MCP disconnected", applied["error"])
            self.assertTrue(applied["checkpoint"]["ok"])
            self.assertEqual(applied["approval"]["checkpoint"]["id"], applied["checkpoint"]["id"])

            restored = gateway.restore_checkpoint({"checkpointId": applied["checkpoint"]["id"], "confirmRestore": True})
            self.assertTrue(restored["ok"])
            self.assertFalse((project / "Assets" / "generated-before-fail.txt").exists())

    def test_audit_log_approval_is_not_executable_after_memory_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            called: list[dict] = []

            def write_handler(args: dict) -> dict:
                called.append(args)
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_audit_write", "Audit write", "high", write_handler)
            request = gateway.create_apply_request({
                "target_tool": "vrcforge_test_audit_write",
                "arguments": {
                    "projectRoot": str(root / "UnityProject"),
                    "repository": "https://example.com/vpm/index.json",
                    "nested": {"key": "value"},
                },
            })
            approval_id = request["approval"]["id"]

            gateway._approvals.clear()

            with self.assertRaises(AgentGatewayError) as approve_error:
                gateway.approve(approval_id)
            self.assertEqual(approve_error.exception.status_code, 404)

            with self.assertRaises(AgentGatewayError) as apply_error:
                gateway.apply_approved({"approval_id": approval_id})
            self.assertEqual(apply_error.exception.status_code, 404)
            self.assertEqual(called, [])

    def test_checkpoint_blocks_write_when_project_root_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            called: list[dict] = []

            def write_handler(args: dict) -> dict:
                called.append(args)
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_missing_root_write", "Missing root write", "high", write_handler)
            request = gateway.create_apply_request({
                "target_tool": "vrcforge_test_missing_root_write",
                "arguments": {"avatar_path": "Scene/Avatar"},
            })
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            applied = gateway.apply_approved({"approval_id": approval_id})

            self.assertFalse(applied["ok"])
            self.assertEqual(applied["status"], "failed")
            self.assertIn("No Unity project root", applied["error"])
            self.assertTrue(applied["checkpoint"]["blocking"])
            self.assertEqual(called, [])

    def test_checkpoint_blocks_write_for_any_non_success_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            called: list[dict] = []
            gateway.register_write_handler(
                "vrcforge_test_failed_checkpoint",
                "Failed checkpoint",
                "high",
                lambda args: called.append(args) or {"ok": True},
            )
            request = gateway.create_apply_request(
                {"target_tool": "vrcforge_test_failed_checkpoint", "arguments": {"projectRoot": str(root)}}
            )
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            gateway._create_pre_write_checkpoint = lambda _approval, _arguments: {  # type: ignore[method-assign]  # noqa: SLF001
                "id": "ckpt_failed_without_blocking",
                "ok": False,
                "error": "checkpoint backend returned an incomplete result",
            }

            applied = gateway.apply_approved({"approval_id": approval_id})

        self.assertFalse(applied["ok"])
        self.assertEqual(applied["status"], "failed")
        self.assertIn("incomplete result", applied["error"])
        self.assertTrue(applied["checkpoint"]["blocking"])
        self.assertEqual(called, [])

    def test_checkpoint_non_unity_root_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            non_unity = root / "NotUnity"
            non_unity.mkdir()
            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")

            checkpoint = gateway._create_pre_write_checkpoint(  # noqa: SLF001
                {"id": "approval-test", "targetTool": "vrcforge_test_non_unity_write"},
                {"projectRoot": str(non_unity)},
            )

        self.assertIsNotNone(checkpoint)
        self.assertFalse(checkpoint["ok"])
        self.assertTrue(checkpoint["blocking"])
        self.assertEqual(checkpoint["status"], "failed")

    def test_checkpoint_archive_restore_fails_closed_when_unity_reload_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            existing = project / "Assets" / "existing.txt"
            existing.write_text("before", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            gateway = AgentGateway(root / "config" / "agent_gateway.json", root / "audit")
            gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}
            gateway.checkpoint_restore_handler = lambda _path, _prepare: {"ok": False, "error": "Unity bridge unavailable"}

            def write_handler(args: dict) -> dict:
                project_root = Path(args["projectRoot"])
                (project_root / "Assets" / "existing.txt").write_text("after", encoding="utf-8")
                (project_root / "Assets" / "generated.txt").write_text("generated", encoding="utf-8")
                return {"ok": True}

            gateway.register_write_handler("vrcforge_test_reload_warning", "Reload warning write", "high", write_handler)
            request = gateway.create_apply_request({
                "target_tool": "vrcforge_test_reload_warning",
                "arguments": {"projectRoot": str(project)},
            })
            approval_id = request["approval"]["id"]
            gateway.approve(approval_id)
            applied = gateway.apply_approved({"approval_id": approval_id})

            self.assertTrue(applied["ok"])
            restored = gateway.restore_checkpoint({"checkpointId": applied["checkpoint"]["id"], "confirmRestore": True})

            self.assertFalse(restored["ok"])
            self.assertTrue(restored["restored"])
            self.assertEqual(restored["status"], "restored_unity_reload_failed")
            self.assertTrue(restored["checkpointRecoveryRequired"])
            self.assertIn("Unity bridge unavailable", restored["error"])
            self.assertEqual(existing.read_text(encoding="utf-8"), "before")
            self.assertFalse((project / "Assets" / "generated.txt").exists())

    def test_checkpoint_reload_transport_close_waits_for_new_core_without_resending(self) -> None:
        project = Path("C:/Unity/ReloadProject")
        settings = SimpleNamespace(
            unity_mcp_timeout_seconds=180,
            unity_mcp_retries=3,
            unity_mcp_retry_backoff_seconds=2,
        )
        previous = SimpleNamespace(process_id=77, project_hash="project-hash", instance_id="old")
        ready = {
            "ok": True,
            "projectPath": str(project),
            "coreReady": True,
            "domainReloadObserved": True,
        }
        connection_closed = dashboard_server.UnityMcpError("reload transport failed")
        connection_closed.__cause__ = dashboard_server.UnityMcpCoreError(
            dashboard_server.CHECKPOINT_RELOAD_CONNECTION_CLOSED_ERROR
        )
        with (
            patch("dashboard_server.load_dashboard_settings", return_value=settings),
            patch("dashboard_server.load_unity_mcp_core_connection", return_value=previous),
            patch(
                "dashboard_server.invoke_unity_mcp",
                side_effect=connection_closed,
            ) as invoke,
            patch("dashboard_server._wait_for_reloaded_unity_core", return_value=ready) as wait_ready,
        ):
            result = dashboard_server.reload_unity_checkpoint_sync(project)

        self.assertEqual(result, ready)
        self.assertEqual(settings.unity_mcp_retries, 1)
        self.assertEqual(
            settings.unity_mcp_timeout_seconds,
            dashboard_server.CHECKPOINT_RELOAD_CALL_TIMEOUT_SECONDS,
        )
        invoke.assert_called_once()
        wait_ready.assert_called_once_with(project, previous)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    @patch("dashboard_server.load_unity_mcp_core_connection", side_effect=dashboard_server.UnityMcpCoreError("offline"))
    def test_checkpoint_reload_forwards_exact_prepared_scenes_and_active_scene(
        self,
        _load_connection,
        load_settings,
        invoke,
    ) -> None:
        load_settings.return_value = SimpleNamespace(
            unity_mcp_timeout_seconds=30,
            unity_mcp_retries=3,
        )
        invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="",
            stderr="",
            payload={"structuredContent": {"success": True, "data": {"ok": True}}},
        )
        project = Path("C:/Unity/ReloadProject")
        prepared = {
            "ok": True,
            "scenes": ["Assets/Avatar.unity", "Assets/Lighting.unity"],
            "activeScenePath": "Assets/Lighting.unity",
        }

        result = dashboard_server.reload_unity_checkpoint_sync(project, prepared)

        self.assertTrue(result["ok"])
        _settings, tool_name, arguments = invoke.call_args.args
        self.assertEqual(tool_name, "vrc_reload_after_checkpoint_restore")
        self.assertEqual(arguments["scenePaths"], prepared["scenes"])
        self.assertEqual(arguments["activeScenePath"], prepared["activeScenePath"])
        self.assertEqual(invoke.call_args.kwargs["execution_context"], {"lane": "app_safety_control"})

    def test_checkpoint_reload_transport_close_times_out_without_resending(self) -> None:
        project = Path("C:/Unity/ReloadProject")
        settings = SimpleNamespace(
            unity_mcp_timeout_seconds=30,
            unity_mcp_retries=3,
            unity_mcp_retry_backoff_seconds=2,
        )
        previous = SimpleNamespace(process_id=77, project_hash="project-hash", instance_id="old")
        not_ready = {"ok": False, "error": "Core readiness timed out."}
        connection_closed = dashboard_server.UnityMcpError("reload transport failed")
        connection_closed.__cause__ = dashboard_server.UnityMcpCoreError(
            dashboard_server.CHECKPOINT_RELOAD_CONNECTION_CLOSED_ERROR
        )
        with (
            patch("dashboard_server.load_dashboard_settings", return_value=settings),
            patch("dashboard_server.load_unity_mcp_core_connection", return_value=previous),
            patch(
                "dashboard_server.invoke_unity_mcp",
                side_effect=connection_closed,
            ) as invoke,
            patch("dashboard_server._wait_for_reloaded_unity_core", return_value=not_ready),
        ):
            result = dashboard_server.reload_unity_checkpoint_sync(project)

        self.assertEqual(result, not_ready)
        invoke.assert_called_once()

    def test_checkpoint_reload_non_close_failure_never_waits_for_new_core(self) -> None:
        project = Path("C:/Unity/ReloadProject")
        settings = SimpleNamespace(
            unity_mcp_timeout_seconds=30,
            unity_mcp_retries=3,
            unity_mcp_retry_backoff_seconds=2,
        )
        previous = SimpleNamespace(process_id=77, project_hash="project-hash", instance_id="old")
        protocol_failure = dashboard_server.UnityMcpError("reload transport failed")
        protocol_failure.__cause__ = dashboard_server.UnityMcpCoreError(
            "Unity MCP Core returned an invalid transport response."
        )
        with (
            patch("dashboard_server.load_dashboard_settings", return_value=settings),
            patch("dashboard_server.load_unity_mcp_core_connection", return_value=previous),
            patch("dashboard_server.invoke_unity_mcp", side_effect=protocol_failure) as invoke,
            patch("dashboard_server._wait_for_reloaded_unity_core") as wait_ready,
        ):
            result = dashboard_server.reload_unity_checkpoint_sync(project)

        self.assertFalse(result["ok"])
        self.assertIn("did not confirm", result["error"])
        invoke.assert_called_once()
        wait_ready.assert_not_called()

    def test_checkpoint_reload_readiness_requires_same_process_new_instance_and_full_tools(self) -> None:
        project = Path("C:/Unity/ReloadProject")
        previous = SimpleNamespace(process_id=77, project_hash="project-hash", instance_id="old")
        unchanged = SimpleNamespace(process_id=77, project_hash="project-hash", instance_id="old")
        replaced = SimpleNamespace(process_id=77, project_hash="project-hash", instance_id="new")
        core_client = Mock()
        core_client.list_tools.return_value = [
            {"name": name} for name in dashboard_server.REQUIRED_VRCFORGE_UNITY_TOOLS
        ]
        with (
            patch(
                "dashboard_server.load_unity_mcp_core_connection",
                side_effect=[unchanged, replaced],
            ),
            patch("dashboard_server.UnityMcpCoreClient", return_value=core_client) as client_type,
            patch("dashboard_server.time.monotonic", side_effect=[0.0, 0.0, 0.1, 0.2, 0.3]),
            patch("dashboard_server.time.sleep"),
        ):
            result = dashboard_server._wait_for_reloaded_unity_core(
                project,
                previous,
                timeout_seconds=1.0,
                poll_seconds=0.01,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["domainReloadObserved"])
        client_type.assert_called_once_with(project, timeout_seconds=1.0)
        core_client.list_tools.assert_called_once_with(exposure_layer="execution")

    def test_checkpoint_reload_readiness_rejects_wrong_process_and_incomplete_tools(self) -> None:
        project = Path("C:/Unity/ReloadProject")
        previous = SimpleNamespace(process_id=77, project_hash="project-hash", instance_id="old")
        wrong_process = SimpleNamespace(process_id=88, project_hash="project-hash", instance_id="new-a")
        incomplete = SimpleNamespace(process_id=77, project_hash="project-hash", instance_id="new-b")
        core_client = Mock()
        core_client.list_tools.return_value = [{"name": "vrc_get_compile_errors"}]
        with (
            patch(
                "dashboard_server.load_unity_mcp_core_connection",
                side_effect=[wrong_process, incomplete],
            ),
            patch("dashboard_server.UnityMcpCoreClient", return_value=core_client),
            patch(
                "dashboard_server.time.monotonic",
                side_effect=[0.0, 0.0, 0.1, 0.2, 0.3, 0.4, 1.1],
            ),
            patch("dashboard_server.time.sleep"),
        ):
            result = dashboard_server._wait_for_reloaded_unity_core(
                project,
                previous,
                timeout_seconds=1.0,
                poll_seconds=0.01,
            )

        self.assertFalse(result["ok"])
        self.assertIn("did not become ready", result["error"])
        core_client.list_tools.assert_called_once_with(exposure_layer="execution")

    def test_checkpoint_tools_registered_with_restore_as_write_target(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest?exposure_layer=execution", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        self.assertIn("vrcforge_list_checkpoints", tool_names)
        self.assertIn("vrcforge_preview_restore_checkpoint", tool_names)
        self.assertNotIn("vrcforge_restore_checkpoint", tool_names)
        self.assertIn("vrcforge_restore_checkpoint", write_targets)
        self.assertIn("vrcforge_unity_mcp_write", write_targets)

    def test_write_targets_publish_rollback_policy(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            manifest = client.get("/api/agent/manifest?exposure_layer=execution", headers=headers).json()
            registry = client.get("/api/app/tools/registry").json()

        targets = {item["name"]: item for item in manifest["writeTargets"]}
        self.assertGreater(len(targets), 10)
        for name, target in targets.items():
            policy = target.get("rollbackPolicy")
            self.assertIsInstance(policy, dict, name)
            self.assertEqual(policy["schema"], "vrcforge.write_rollback_policy.v1")
            self.assertTrue(policy["required"], name)
            self.assertEqual(policy["restoreTool"], "vrcforge_restore_checkpoint")
            self.assertEqual(policy["coverageAudit"], "vrcforge.rollback_coverage_audit.v1")

        unity_policy = targets["vrcforge_add_modular_avatar_component"]["rollbackPolicy"]
        self.assertEqual(unity_policy["kind"], "unity_project_checkpoint")
        self.assertEqual(unity_policy["checkpointScope"], ["Assets", "Packages", "ProjectSettings"])
        self.assertIn("Modular Avatar", unity_policy["ecosystemCoverageRequired"])
        self.assertIn("VRCFury", unity_policy["ecosystemCoverageRequired"])
        self.assertIn("NDMF", unity_policy["ecosystemCoverageRequired"])

        package_policy = targets["vrcforge_import_skill_package"]["rollbackPolicy"]
        self.assertEqual(package_policy["kind"], "local_state_archive")
        self.assertEqual(package_policy["checkpointScope"], ["skill-packages", "skills"])

        restore_policy = targets["vrcforge_restore_checkpoint"]["rollbackPolicy"]
        self.assertEqual(restore_policy["kind"], "checkpoint_restore")
        self.assertFalse(restore_policy["preWriteCheckpointRequired"])

        registry_targets = {item["name"]: item for item in registry["tools"] if item.get("source") == "write-target"}
        self.assertEqual(
            registry_targets["vrcforge_import_skill_package"]["rollbackPolicy"],
            package_policy,
        )

    def test_checkpoint_recovery_unity_tools_save_and_reload_scenes(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "Assets"
            / "VRCForge"
            / "Editor"
            / "CheckpointRecoveryTool.cs"
        ).read_text(encoding="utf-8")
        self.assertIn('toolId: "vrc_prepare_checkpoint"', source)
        self.assertIn('toolId: "vrc_reload_after_checkpoint_restore"', source)
        self.assertNotIn("EditorSceneManager.SaveOpenScenes", source)
        self.assertIn('"unsaved_open_scene"', source)
        self.assertIn('"scene_outside_project_assets"', source)
        self.assertIn('private const string NdmfPreviewSceneGuid = "8cbd3f19cef3477439841053ced0661b";', source)
        self.assertIn("scene.isDirty || !scene.isSubScene", source)
        self.assertIn("scene == SceneManager.GetActiveScene()", source)
        self.assertIn('string.Equals(scene.name, "___NDMF Preview___", StringComparison.Ordinal)', source)
        self.assertIn("AssetDatabase.GUIDToAssetPath(NdmfPreviewSceneGuid)", source)
        self.assertIn("ignoredTransientScenes", source)
        self.assertIn("!CheckpointPrepareTool.IsKnownTransientPreviewScene(scene)", source)
        self.assertIn("activeScenePath", source)
        self.assertIn("closedScenes", source)
        self.assertIn("reopenErrors", source)
        self.assertIn("recoveryRequired = true", source)
        self.assertIn("EditorSceneManager.SaveScene(scene)", source)
        self.assertLess(
            source.index('"unsaved_open_scene"'),
            source.index("AssetDatabase.SaveAssets()"),
        )
        self.assertIn("EditorSceneManager.OpenScene", source)
        self.assertIn("NewSceneSetup.EmptyScene", source)
        self.assertIn("EditorSceneManager.CloseScene(scene, true)", source)
        self.assertIn("ForceSynchronousImport", source)
        self.assertIn("AssetDatabase.Refresh", source)
        self.assertEqual(
            source.count("PrimitiveBasisLiveGuard.RequireBoundRequest(@params)"),
            2,
        )

        compile_source = (
            Path(__file__).resolve().parents[1]
            / "Assets"
            / "VRCForge"
            / "Editor"
            / "CompileErrorReader.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("PrimitiveBasisLiveGuard.RequireBoundRequest(@params)", compile_source)
        for field in (
            "unityProcessId",
            "unityProcessStartedAtUtc",
            "unityExecutableDigest",
            "projectPathDigest",
        ):
            self.assertIn(field, compile_source)

    def test_refresh_asset_database_tool_can_resolve_packages(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "Assets"
            / "VRCForge"
            / "Editor"
            / "OutfitPackageImporter.cs"
        ).read_text(encoding="utf-8")

        self.assertIn("UnityEditor.PackageManager", source)
        self.assertIn("resolvePackages", source)
        self.assertIn("Client.Resolve()", source)
        self.assertIn("packageResolve", source)
        self.assertIn('status = "started"', source)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_setup_outfit_preview_uses_strict_app_preview_lane(
        self,
        mock_load_settings,
        mock_invoke,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "confirmed": False, "outfitPath": "Avatar/Outfit"}},
        )

        result = dashboard_server.WARDROBE_OUTFIT_WORKFLOWS.preview_setup_outfit(
            {"avatarPath": "Avatar", "outfitPath": "Avatar/Outfit", "saveScene": False}
        )

        self.assertTrue(result["ok"])
        _settings, tool_name, arguments = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_setup_outfit")
        self.assertFalse(arguments["confirmSetup"])
        self.assertEqual(mock_invoke.call_args.kwargs.get("execution_context"), {"lane": "app_preview"})

    def test_setup_outfit_uses_modular_avatar_public_api(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "Assets"
            / "VRCForge"
            / "Editor"
            / "SetupOutfitTool.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("nadena.dev.modular_avatar.core.editor.SetupOutfit", source)
        self.assertIn('"SetupOutfitUI"', source)
        self.assertIn("method.Invoke(null, new object[] { outfit })", source)
        self.assertIn("ESOErrorWindow", source)
        self.assertIn("suppressField?.SetValue(null, true)", source)
        self.assertNotIn("EditorApplication.ExecuteMenuItem(", source)

    def test_setup_outfit_saves_target_scene_only(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "Assets"
            / "VRCForge"
            / "Editor"
            / "SetupOutfitTool.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("SaveTargetScene(outfit.gameObject.scene)", source)
        self.assertIn("EditorSceneManager.SaveScene(scene)", source)
        self.assertNotIn("EditorSceneManager.SaveOpenScenes", source)

    def test_setup_outfit_preview_requires_humanoid_avatar_hips(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "Assets"
            / "VRCForge"
            / "Editor"
            / "SetupOutfitTool.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("avatarHasHumanoidHips", source)
        self.assertIn('["ready"] = hasSkinnedMesh && avatarHasHumanoidHips', source)

    def test_setup_outfit_write_uses_job_polling(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "dashboard_server.py").read_text(encoding="utf-8")
        self.assertNotIn("def setup_outfit_sync", source)
        self.assertNotIn("def wait_for_setup_outfit_job", source)
        self.assertIn("SETUP_OUTFIT_APPROVED_WRITE.wait_for_existing_job", source)
        self.assertIn('execution_context={"lane": "app_setup_outfit_poll"}', source)
        self.assertNotIn(
            "unity_mcp_timeout_seconds = max(settings.unity_mcp_timeout_seconds, 120)",
            source,
        )

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_setup_outfit_sync_polls_pending_job_to_completion(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace(unity_mcp_timeout_seconds=30)
        mock_invoke.side_effect = [
            dashboard_server.McpResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                payload={"data": {"ok": True, "pending": True, "status": "pending", "jobId": "job-1"}},
            ),
            dashboard_server.McpResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                payload={"data": {"ok": True, "pending": True, "status": "running", "jobId": "job-1"}},
            ),
            dashboard_server.McpResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                payload={"data": {
                    "ok": True,
                    "pending": False,
                    "status": "completed",
                    "jobId": "job-1",
                    "sceneSaved": True,
                    "committed": True,
                    "commitState": "complete",
                    "checkpointRecoveryRequired": False,
                    "outfitGlobalObjectId": "GlobalObjectId_V1-2-fixture",
                }},
            ),
        ]

        result = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.setup_outfit(
            {
                "avatarPath": "Avatar",
                "outfitPath": "Avatar/Hoodie",
                "setupOutfitPollIntervalSeconds": 0,
                "setupOutfitPollTimeoutSeconds": 1,
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual([call.args[2] for call in mock_invoke.call_args_list], [
            {"avatarPath": "Avatar", "outfitPath": "Avatar/Hoodie", "confirmSetup": True, "saveScene": True},
            {"jobId": "job-1"},
            {"jobId": "job-1"},
        ])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_setup_outfit_sync_returns_job_error(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace(unity_mcp_timeout_seconds=30)
        mock_invoke.side_effect = [
            dashboard_server.McpResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                payload={"data": {"ok": True, "pending": True, "status": "pending", "jobId": "job-2"}},
            ),
            dashboard_server.McpResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                payload={"data": {"ok": False, "pending": False, "status": "error", "jobId": "job-2", "error": "MA failed"}},
            ),
        ]

        result = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.setup_outfit(
            {
                "avatarPath": "Avatar",
                "outfitPath": "Avatar/Hoodie",
                "setupOutfitPollIntervalSeconds": 0,
                "setupOutfitPollTimeoutSeconds": 1,
            }
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "MA failed")
        self.assertEqual(mock_invoke.call_count, 2)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_setup_outfit_sync_returns_timeout_for_unfinished_job(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace(unity_mcp_timeout_seconds=30)
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "pending": True, "status": "pending", "jobId": "job-3"}},
        )

        result = dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.setup_outfit(
            {
                "avatarPath": "Avatar",
                "outfitPath": "Avatar/Hoodie",
                "setupOutfitPollTimeoutSeconds": 0,
            }
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["jobId"], "job-3")
        self.assertEqual(mock_invoke.call_count, 1)

    def test_app_approval_executes_non_shell_write_handler(self) -> None:
        temp_project = tempfile.TemporaryDirectory()
        self.addCleanup(temp_project.cleanup)
        project = Path(temp_project.name) / "UnityProject"
        (project / "Assets").mkdir(parents=True)
        (project / "Packages").mkdir()
        (project / "ProjectSettings").mkdir()
        (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
        (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

        original_handlers = dict(dashboard_server.AGENT_GATEWAY._write_handlers)
        original_prepare = dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler
        dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = lambda _root: {"ok": True}
        calls: list[dict] = []

        def write_handler(args: dict) -> dict:
            calls.append(args)
            return {"ok": True, "wrote": args.get("name")}

        try:
            dashboard_server.AGENT_GATEWAY.register_write_handler(
                "vrcforge_test_app_write",
                "Test app write.",
                "high",
                write_handler,
            )
            request = dashboard_server.AGENT_GATEWAY.create_apply_request({
                "target_tool": "vrcforge_test_app_write",
                "arguments": {"projectRoot": str(project), "name": "value"},
            })
            approval_id = request["approval"]["id"]

            with TestClient(dashboard_server.app) as client:
                payload = client.post(
                    f"/api/app/agent/approvals/{approval_id}/approve",
                    json={"expectedProjectRoot": str(project)},
                ).json()

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["execution"]["status"], "applied")
            self.assertEqual(payload["execution"]["result"]["wrote"], "value")
            self.assertEqual(calls, [{"projectRoot": str(project), "name": "value"}])
        finally:
            dashboard_server.AGENT_GATEWAY._write_handlers = original_handlers
            dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = original_prepare

    def test_app_checkpoint_restore_request_uses_approval_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "Assets" / "existing.txt").write_text("before", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            for args in (
                ["init"],
                ["config", "user.email", "test@example.invalid"],
                ["config", "user.name", "Test User"],
                ["add", "-A"],
                ["commit", "-m", "initial"],
            ):
                proc = subprocess.run(["git", *args], cwd=str(project), capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)

            original_handlers = dict(dashboard_server.AGENT_GATEWAY._write_handlers)
            original_prepare = dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler
            original_restore_prepare = dashboard_server.AGENT_GATEWAY.checkpoint_restore_prepare_handler
            original_reload = dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler

            def write_handler(args: dict) -> dict:
                (Path(args["projectRoot"]) / "Assets" / "generated.txt").write_text("after", encoding="utf-8")
                return {"ok": True}

            try:
                dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = lambda _root: {"ok": True}
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_prepare_handler = lambda _root: {"ok": True, "scenes": []}
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler = lambda _root, _prepare: {"ok": True}
                dashboard_server.AGENT_GATEWAY.register_write_handler(
                    "vrcforge_test_checkpoint_write",
                    "Test checkpoint write.",
                    "high",
                    write_handler,
                )
                request = dashboard_server.AGENT_GATEWAY.create_apply_request({
                    "target_tool": "vrcforge_test_checkpoint_write",
                    "arguments": {"projectRoot": str(project)},
                })
                approval_id = request["approval"]["id"]
                dashboard_server.AGENT_GATEWAY.approve(approval_id)
                dashboard_server.AGENT_GATEWAY.apply_approved({"approval_id": approval_id})

                with TestClient(dashboard_server.app) as client:
                    listed = client.get("/api/app/checkpoints", params={"projectRoot": str(project)}).json()
                    checkpoint_id = listed["checkpoints"][0]["id"]
                    preview = client.post(f"/api/app/checkpoints/{checkpoint_id}/preview").json()
                    restore_request = client.post(f"/api/app/checkpoints/{checkpoint_id}/restore").json()
                    restore_approval_id = restore_request["approval"]["id"]
                    applied = client.post(
                        f"/api/app/agent/approvals/{restore_approval_id}/approve",
                        json={"expectedProjectRoot": str(project.resolve())},
                    ).json()

                self.assertTrue(preview["ok"])
                self.assertEqual(restore_request["status"], "pending")
                self.assertEqual(restore_request["approval"]["targetTool"], "vrcforge_restore_checkpoint")
                stored_restore = dashboard_server.AGENT_GATEWAY._approvals[restore_approval_id]  # noqa: SLF001 - verify executable approval payload.
                self.assertEqual(stored_restore["arguments"]["projectRoot"], str(project.resolve()))
                self.assertTrue(applied["execution"]["ok"])
                self.assertFalse((project / "Assets" / "generated.txt").exists())
            finally:
                dashboard_server.AGENT_GATEWAY._write_handlers = original_handlers
                dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = original_prepare
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_prepare_handler = original_restore_prepare
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler = original_reload

    def test_adjustment_checkpoint_timeline_supports_crud_select_apply_and_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "UnityProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "Assets" / "face.txt").write_text("current", encoding="utf-8")
            (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")

            original_prepare = dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler
            original_restore_prepare = dashboard_server.AGENT_GATEWAY.checkpoint_restore_prepare_handler
            original_reload = dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler
            try:
                dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = lambda _root: {"ok": True}
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_prepare_handler = lambda _root: {"ok": True, "scenes": []}
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler = lambda _root, _prepare: {"ok": True}
                with TestClient(dashboard_server.app) as client:
                    created = client.post(
                        "/api/app/adjustment-checkpoints",
                        json={
                            "kind": "face",
                            "projectRoot": str(project),
                            "avatarPath": "Scene/HeroAvatar",
                            "label": "Face A",
                            "tags": ["nose", "smile"],
                        },
                    ).json()
                    entry_id = created["checkpoint"]["id"]

                    listed = client.get("/api/app/adjustment-checkpoints", params={"kind": "face"}).json()
                    fetched = client.get(f"/api/app/adjustment-checkpoints/{entry_id}").json()
                    updated = client.put(
                        f"/api/app/adjustment-checkpoints/{entry_id}",
                        json={"label": "Face A tuned", "compareGroup": "smile-test"},
                    ).json()
                    selected = client.post(
                        f"/api/app/adjustment-checkpoints/{entry_id}/select",
                        json={"slot": "A", "compareGroup": "smile-test"},
                    ).json()
                    selections = client.get(
                        "/api/app/adjustment-checkpoints/selection",
                        params={"kind": "face", "compareGroup": "smile-test"},
                    ).json()

                    (project / "Assets" / "face.txt").write_text("variant b", encoding="utf-8")
                    overwritten = client.post(
                        f"/api/app/adjustment-checkpoints/{entry_id}/overwrite",
                        json={"label": "Face B", "projectRoot": str(project), "compareGroup": "smile-test"},
                    ).json()
                    preview = client.post(f"/api/app/adjustment-checkpoints/{entry_id}/preview").json()
                    apply_request = client.post(f"/api/app/adjustment-checkpoints/{entry_id}/apply").json()
                    deleted = client.delete(f"/api/app/adjustment-checkpoints/{entry_id}").json()
                    listed_after_delete = client.get("/api/app/adjustment-checkpoints", params={"kind": "face"}).json()
                    listed_with_deleted = client.get(
                        "/api/app/adjustment-checkpoints",
                        params={"kind": "face", "includeDeleted": "true"},
                    ).json()

                self.assertTrue(created["ok"])
                self.assertEqual(created["checkpoint"]["label"], "Face A")
                self.assertTrue(created["checkpoint"]["checkpointId"])
                self.assertEqual(listed["count"], 1)
                self.assertEqual(fetched["checkpoint"]["id"], entry_id)
                self.assertEqual(updated["checkpoint"]["label"], "Face A tuned")
                self.assertEqual(selected["selection"]["slot"], "A")
                self.assertIn("face:smile-test:A", selections["selections"])
                self.assertEqual(overwritten["checkpoint"]["label"], "Face B")
                self.assertEqual(overwritten["checkpoint"]["overwriteCount"], 1)
                self.assertTrue(preview["ok"])
                self.assertEqual(apply_request["approval"]["targetTool"], "vrcforge_restore_checkpoint")
                self.assertTrue(deleted["checkpoint"]["deletedAt"])
                self.assertEqual(listed_after_delete["count"], 0)
                self.assertEqual(listed_with_deleted["count"], 1)
            finally:
                dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = original_prepare
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_prepare_handler = original_restore_prepare
                dashboard_server.AGENT_GATEWAY.checkpoint_restore_handler = original_reload

    def test_face_shader_checkpoint_records_auto_adjustment_timeline_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gateway = AgentGateway(Path(tmp) / "config" / "agent_gateway.json", Path(tmp) / "audit")

            gateway._append_checkpoint(  # noqa: SLF001 - unit test verifies checkpoint index side effect.
                {
                    "id": "ckpt_face_auto",
                    "createdAt": "2026-06-24T00:00:00+00:00",
                    "ok": True,
                    "status": "ready",
                    "targetTool": "vrcforge_run_face_tuning",
                    "projectRoot": "D:/AvatarProject",
                }
            )
            gateway._append_checkpoint(  # noqa: SLF001 - unit test verifies checkpoint index side effect.
                {
                    "id": "ckpt_shader_auto",
                    "createdAt": "2026-06-24T00:00:01+00:00",
                    "ok": True,
                    "status": "ready",
                    "targetTool": "vrcforge_apply_shader_tuning",
                    "projectRoot": "D:/AvatarProject",
                }
            )

            face = gateway.list_adjustment_checkpoints({"kind": "face"})
            shader = gateway.list_adjustment_checkpoints({"kind": "shader"})

            self.assertEqual(face["count"], 1)
            self.assertEqual(face["checkpoints"][0]["checkpointId"], "ckpt_face_auto")
            self.assertEqual(face["checkpoints"][0]["source"], "automatic")
            self.assertEqual(shader["count"], 1)
            self.assertEqual(shader["checkpoints"][0]["checkpointId"], "ckpt_shader_auto")

    def test_add_outfit_workflow_registered_in_gateway(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest?exposure_layer=execution", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        self.assertIn("vrcforge_preview_add_outfit", tool_names)
        self.assertNotIn("vrcforge_add_outfit", tool_names)
        self.assertIn("vrcforge_add_outfit", write_targets)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def _legacy_add_outfit_workflow_preview_matches_apply_order(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"ok": True, "assets": [{"assetPath": "Assets/Outfits/Hoodie.prefab", "guid": "abc", "name": "Hoodie"}]}},
        )

        result = dashboard_server.PREPARED_ADD_OUTFIT_PREVIEW.preview({
            "avatarPath": "Avatar",
            "assetQuery": "hoodie",
            "outfitName": "Hoodie",
        })

        self.assertTrue(result["ok"])
        self.assertEqual([step["tool"] for step in result["plan"]["steps"]], [
            "vrc_find_assets",
            "vrc_scan_wardrobe",
            "vrc_create_wardrobe",
            "vrc_instantiate_prefab",
            "vrc_setup_outfit",
            "vrc_add_wardrobe_outfit",
        ])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def _legacy_add_outfit_workflow_resolves_prefab_and_runs_ordered_steps(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()

        def fake_invoke(_settings, tool_name, params):
            if tool_name == "vrc_find_assets":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "assets": [{"assetPath": "Assets/Outfits/Hoodie.prefab", "guid": "abc", "name": "Hoodie"}]}},
                )
            if tool_name == "vrc_scan_wardrobe":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "wardrobeCount": 1, "wardrobes": [{"parameterName": "Clothes"}]}},
                )
            if tool_name == "vrc_instantiate_prefab":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "gameObjectPath": "Avatar/Hoodie"}},
                )
            if tool_name == "vrc_setup_outfit":
                return dashboard_server.McpResult(exit_code=0, stdout="ok", stderr="", payload={"data": {"ok": True, "confirmed": True}})
            if tool_name == "vrc_add_wardrobe_outfit":
                return dashboard_server.McpResult(exit_code=0, stdout="ok", stderr="", payload={"data": {"ok": True, "assignedValue": 4}})
            raise AssertionError(tool_name)

        mock_invoke.side_effect = fake_invoke
        result = dashboard_server.add_outfit_workflow_sync({
            "avatarPath": "Avatar",
            "assetQuery": "hoodie",
            "outfitName": "Hoodie",
            "parameterName": "Clothes",
        })

        self.assertTrue(result["ok"])
        self.assertEqual(result["outfitPath"], "Avatar/Hoodie")
        self.assertEqual([call.args[1] for call in mock_invoke.call_args_list], [
            "vrc_find_assets",
            "vrc_scan_wardrobe",
            "vrc_instantiate_prefab",
            "vrc_setup_outfit",
            "vrc_add_wardrobe_outfit",
        ])
        wardrobe_params = mock_invoke.call_args_list[-1].args[2]
        self.assertEqual(wardrobe_params["objectPaths"], ["Avatar/Hoodie"])
        self.assertEqual(wardrobe_params["parameterName"], "Clothes")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def _legacy_add_outfit_workflow_creates_missing_wardrobe_before_binding(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()

        def fake_invoke(_settings, tool_name, params):
            if tool_name == "vrc_find_assets":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "assets": [{"assetPath": "Assets/Outfits/Hoodie.prefab", "guid": "abc", "name": "Hoodie"}]}},
                )
            if tool_name == "vrc_scan_wardrobe":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={
                        "data": {
                            "ok": True,
                            "wardrobeCount": 0,
                            "wardrobeCandidateCount": 0,
                            "wardrobes": [],
                            "wardrobeCandidates": [],
                            "looseControlCount": 2,
                            "looseControls": [{"parameterName": "sock"}, {"parameterName": "hat"}],
                        }
                    },
                )
            if tool_name in {"vrc_ensure_expression_parameter", "vrc_ensure_animator_state", "vrc_ensure_expression_menu_control"}:
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "tool": tool_name, "parameterName": params.get("parameterName")}},
                )
            if tool_name == "vrc_instantiate_prefab":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "gameObjectPath": "Avatar/Hoodie"}},
                )
            if tool_name == "vrc_setup_outfit":
                return dashboard_server.McpResult(exit_code=0, stdout="ok", stderr="", payload={"data": {"ok": True, "confirmed": True}})
            if tool_name == "vrc_add_wardrobe_outfit":
                return dashboard_server.McpResult(exit_code=0, stdout="ok", stderr="", payload={"data": {"ok": True, "assignedValue": 1}})
            raise AssertionError(tool_name)

        mock_invoke.side_effect = fake_invoke
        result = dashboard_server.add_outfit_workflow_sync({
            "avatarPath": "Avatar",
            "assetQuery": "hoodie",
            "outfitName": "Hoodie",
        })

        self.assertTrue(result["ok"])
        self.assertEqual([call.args[1] for call in mock_invoke.call_args_list], [
            "vrc_find_assets",
            "vrc_scan_wardrobe",
            "vrc_ensure_expression_parameter",
            "vrc_ensure_animator_state",
            "vrc_ensure_expression_menu_control",
            "vrc_instantiate_prefab",
            "vrc_setup_outfit",
            "vrc_add_wardrobe_outfit",
        ])
        create_params = mock_invoke.call_args_list[2].args[2]
        wardrobe_params = mock_invoke.call_args_list[-1].args[2]
        self.assertEqual(create_params["parameterName"], "Clothes")
        self.assertEqual(mock_invoke.call_args_list[3].args[2]["layerName"], "Clothes")
        self.assertEqual(mock_invoke.call_args_list[4].args[2]["menuPath"], "Wardrobe")
        self.assertEqual(wardrobe_params["parameterName"], "Clothes")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def _legacy_add_outfit_workflow_does_not_auto_use_candidate_wardrobe(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()

        def fake_invoke(_settings, tool_name, _params):
            if tool_name == "vrc_find_assets":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "assets": [{"assetPath": "Assets/Outfits/Hoodie.prefab", "guid": "abc", "name": "Hoodie"}]}},
                )
            if tool_name == "vrc_scan_wardrobe":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={
                        "data": {
                            "ok": True,
                            "wardrobeCount": 0,
                            "wardrobeCandidateCount": 1,
                            "wardrobes": [],
                            "wardrobeCandidates": [{"parameterName": "MaybeClothes"}],
                            "looseControlCount": 0,
                            "looseControls": [],
                        }
                    },
                )
            raise AssertionError(tool_name)

        mock_invoke.side_effect = fake_invoke
        result = dashboard_server.add_outfit_workflow_sync({
            "avatarPath": "Avatar",
            "assetQuery": "hoodie",
            "outfitName": "Hoodie",
        })

        self.assertFalse(result["ok"])
        self.assertIn("No high-confidence wardrobe was found", result["error"])
        self.assertEqual(result["wardrobeCandidates"], ["MaybeClothes"])
        self.assertEqual([call.args[1] for call in mock_invoke.call_args_list], [
            "vrc_find_assets",
            "vrc_scan_wardrobe",
        ])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def _legacy_add_outfit_workflow_allows_explicit_candidate_wardrobe(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()

        def fake_invoke(_settings, tool_name, params):
            if tool_name == "vrc_find_assets":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "assets": [{"assetPath": "Assets/Outfits/Hoodie.prefab", "guid": "abc", "name": "Hoodie"}]}},
                )
            if tool_name == "vrc_scan_wardrobe":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={
                        "data": {
                            "ok": True,
                            "wardrobeCount": 0,
                            "wardrobeCandidateCount": 1,
                            "wardrobes": [],
                            "wardrobeCandidates": [{"parameterName": "MaybeClothes"}],
                            "looseControlCount": 0,
                            "looseControls": [],
                        }
                    },
                )
            if tool_name == "vrc_instantiate_prefab":
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "gameObjectPath": "Avatar/Hoodie"}},
                )
            if tool_name == "vrc_setup_outfit":
                return dashboard_server.McpResult(exit_code=0, stdout="ok", stderr="", payload={"data": {"ok": True}})
            if tool_name == "vrc_add_wardrobe_outfit":
                return dashboard_server.McpResult(exit_code=0, stdout="ok", stderr="", payload={"data": {"ok": True, "assignedValue": 2}})
            raise AssertionError(tool_name)

        mock_invoke.side_effect = fake_invoke
        result = dashboard_server.add_outfit_workflow_sync({
            "avatarPath": "Avatar",
            "assetQuery": "hoodie",
            "outfitName": "Hoodie",
            "parameterName": "MaybeClothes",
        })

        self.assertTrue(result["ok"])
        self.assertEqual([call.args[1] for call in mock_invoke.call_args_list], [
            "vrc_find_assets",
            "vrc_scan_wardrobe",
            "vrc_instantiate_prefab",
            "vrc_setup_outfit",
            "vrc_add_wardrobe_outfit",
        ])
        self.assertEqual(mock_invoke.call_args_list[-1].args[2]["parameterName"], "MaybeClothes")

    def test_generic_component_crud_tool_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor" / "Generic"
        source = (editor_dir / "UnityComponentCrud.cs").read_text(encoding="utf-8")
        for tool_name in (
            "vrc_get_property",
            "vrc_add_component",
            "vrc_remove_component",
            "vrc_set_property",
        ):
            self.assertIn(f'toolId: "{tool_name}"', source)
        self.assertIn("[VRCForgeCommand(", source)
        self.assertEqual(source.count("public static object HandleCommand(JObject @params)"), 4)
        # Write tools must register Undo entries so the checkpoint timeline can roll them back.
        self.assertIn("Undo.AddComponent", source)
        self.assertIn("Undo.DestroyObjectImmediate", source)
        self.assertIn("Undo.RecordObject", source)

    def test_component_crud_tools_registered_in_gateway(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest?exposure_layer=execution", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        # Read tool is directly callable.
        self.assertIn("vrcforge_get_property", tool_names)
        # Write tools are approval-gated: present as writeTargets, never as direct read tools.
        self.assertNotIn("vrcforge_add_component", tool_names)
        self.assertNotIn("vrcforge_remove_component", tool_names)
        self.assertNotIn("vrcforge_set_property", tool_names)
        self.assertIn("vrcforge_add_component", write_targets)
        self.assertIn("vrcforge_remove_component", write_targets)
        self.assertIn("vrcforge_set_property", write_targets)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_get_property_forwards_to_unity_tool(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {
                "componentType": "UnityEngine.SkinnedMeshRenderer",
                "propertyPath": "enabled",
                "valueType": "System.Boolean",
                "propertyValue": True,
            }},
        )
        result = dashboard_server.read_component_property_sync({
            "game_object_path": "Scene/Avatar/Body",
            "component_type": "SkinnedMeshRenderer",
            "property_path": "enabled",
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_get_property")
        self.assertEqual(params["gameObjectPath"], "Scene/Avatar/Body")
        self.assertEqual(params["componentType"], "SkinnedMeshRenderer")
        self.assertEqual(params["propertyPath"], "enabled")

    def test_get_property_requires_target_fields(self) -> None:
        self.assertFalse(dashboard_server.read_component_property_sync({})["ok"])
        self.assertFalse(
            dashboard_server.read_component_property_sync(
                {"game_object_path": "A", "component_type": "C"}
            )["ok"]
        )

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_add_component_forwards_with_preview_flag(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "add_component", "preview": True, "componentType": "X"}},
        )
        result = dashboard_server.add_component_sync({
            "game_object_path": "Scene/Avatar/Outfit",
            "component_type": "nadena.dev.modular_avatar.core.ModularAvatarMergeArmature",
            "preview": True,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_add_component")
        self.assertTrue(params["preview"])
        self.assertEqual(
            params["componentType"],
            "nadena.dev.modular_avatar.core.ModularAvatarMergeArmature",
        )

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_remove_component_forwards_index(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "remove_component"}},
        )
        result = dashboard_server.remove_component_sync({
            "gameObjectPath": "Scene/Avatar/Body",
            "componentType": "BoxCollider",
            "componentIndex": 2,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_remove_component")
        self.assertEqual(params["componentIndex"], 2)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_set_property_requires_value_and_forwards(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "set_property", "newValue": False}},
        )
        missing = dashboard_server.set_component_property_sync({
            "game_object_path": "Scene/Avatar/Body",
            "component_type": "SkinnedMeshRenderer",
            "property_path": "enabled",
        })
        self.assertFalse(missing["ok"])
        mock_invoke.assert_not_called()
        result = dashboard_server.set_component_property_sync({
            "game_object_path": "Scene/Avatar/Body",
            "component_type": "SkinnedMeshRenderer",
            "property_path": "enabled",
            "value": False,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_set_property")
        self.assertEqual(params["propertyPath"], "enabled")
        self.assertIn("value", params)
        self.assertEqual(params["value"], False)

    def test_generic_gameobject_crud_tool_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor" / "Generic"
        source = (editor_dir / "UnityGameObjectCrud.cs").read_text(encoding="utf-8")
        for tool_name in (
            "vrc_get_gameobject",
            "vrc_create_gameobject",
            "vrc_rename_gameobject",
            "vrc_reparent_gameobject",
            "vrc_delete_gameobject",
            "vrc_set_gameobject_active",
        ):
            self.assertIn(f'toolId: "{tool_name}"', source)
        self.assertIn("[VRCForgeCommand(", source)
        self.assertEqual(source.count("public static object HandleCommand(JObject @params)"), 6)
        # Reuses the shared reflection core rather than hard-referencing MA/VRC SDK assemblies.
        self.assertIn("ComponentCrudCore.ResolveGameObject", source)
        # Every write tool registers a Unity Undo entry for the checkpoint timeline.
        self.assertIn("Undo.RegisterCreatedObjectUndo", source)
        self.assertIn("Undo.SetTransformParent", source)
        self.assertIn("Undo.DestroyObjectImmediate", source)
        self.assertIn("Undo.RecordObject", source)
        create_source = source[source.index("public static class CreateGameObjectTool") : source.index("public static class RenameGameObjectTool")]
        self.assertIn("EditorSceneManager.SaveScene(targetScene)", create_source)
        self.assertIn("GlobalObjectId.GetGlobalObjectIdSlow(readback)", create_source)
        self.assertIn("sceneSaved = true", create_source)
        self.assertIn("persistedReadback = true", create_source)
        self.assertIn("SceneObjectCopyCore.ResolveSavedScene", create_source)
        self.assertIn("SceneObjectCopyCore.ResolveUniqueGameObject", create_source)
        self.assertIn("AssetPrefabCore.CountHierarchyPath", create_source)
        self.assertIn("parent.scene.handle != targetScene.handle", create_source)
        self.assertIn("UnityEngine.Object.DestroyImmediate(created)", create_source)
        self.assertIn("EditorSceneManager.SaveScene(beforeScene.Scene)", create_source)
        self.assertIn("cleanup.FileDigest == beforeScene.FileDigest", create_source)
        self.assertIn("cleanup.FileIdentity == beforeScene.FileIdentity", create_source)
        self.assertIn("AssetPrefabCore.CountHierarchyPath(createdPath, cleanup.Handle) == 0", create_source)
        self.assertNotIn("Undo.RevertAllDownToGroup", create_source)
        self.assertIn("checkpointRecoveryRequired = !restored", create_source)
        self.assertLess(
            create_source.index('ResolveSavedScene(targetScene.path, "target scene")'),
            create_source.index("created = new GameObject(name)"),
        )
        # read payload must avoid auto-unwrap keys (data/result/payload/value).
        self.assertNotIn("value =", source)

    def test_gameobject_crud_tools_registered_in_gateway(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest?exposure_layer=execution", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        # Read tool is directly callable.
        self.assertIn("vrcforge_get_gameobject", tool_names)
        # Write tools are approval-gated: present as writeTargets, never as direct read tools.
        for write_name in (
            "vrcforge_create_gameobject",
            "vrcforge_rename_gameobject",
            "vrcforge_reparent_gameobject",
            "vrcforge_delete_gameobject",
            "vrcforge_set_gameobject_active",
        ):
            self.assertNotIn(write_name, tool_names)
            self.assertIn(write_name, write_targets)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_get_gameobject_forwards_to_unity_tool(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {
                "gameObjectPath": "Avatar/Body",
                "name": "Body",
                "activeSelf": True,
                "childCount": 0,
                "componentCount": 2,
            }},
        )
        result = dashboard_server.get_gameobject_sync({
            "game_object_path": "Avatar/Body",
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_get_gameobject")
        self.assertEqual(params["gameObjectPath"], "Avatar/Body")

    def test_get_gameobject_requires_path(self) -> None:
        self.assertFalse(dashboard_server.get_gameobject_sync({})["ok"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_create_gameobject_forwards_with_preview_flag(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "create_gameobject", "preview": True, "name": "Outfit"}},
        )
        result = dashboard_server.create_gameobject_sync({
            "name": "Outfit",
            "parent_path": "Avatar",
            "preview": True,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_create_gameobject")
        self.assertTrue(params["preview"])
        self.assertEqual(params["name"], "Outfit")
        self.assertEqual(params["parentPath"], "Avatar")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_rename_gameobject_requires_new_name_and_forwards(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "rename_gameobject", "newName": "Hips"}},
        )
        missing = dashboard_server.rename_gameobject_sync({"game_object_path": "Avatar/Armature/Hip"})
        self.assertFalse(missing["ok"])
        mock_invoke.assert_not_called()
        result = dashboard_server.rename_gameobject_sync({
            "game_object_path": "Avatar/Armature/Hip",
            "new_name": "Hips",
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_rename_gameobject")
        self.assertEqual(params["newName"], "Hips")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_reparent_gameobject_forwards_world_position_stays(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "reparent_gameobject"}},
        )
        result = dashboard_server.reparent_gameobject_sync({
            "game_object_path": "Avatar/Outfit",
            "new_parent_path": "Avatar/Armature/Hips",
            "world_position_stays": False,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_reparent_gameobject")
        self.assertEqual(params["newParentPath"], "Avatar/Armature/Hips")
        self.assertFalse(params["worldPositionStays"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_delete_gameobject_forwards(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "delete_gameobject", "preview": True}},
        )
        missing = dashboard_server.delete_gameobject_sync({})
        self.assertFalse(missing["ok"])
        result = dashboard_server.delete_gameobject_sync({
            "game_object_path": "Avatar/OldOutfit",
            "preview": True,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_delete_gameobject")
        self.assertEqual(params["gameObjectPath"], "Avatar/OldOutfit")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_set_gameobject_active_requires_active_and_forwards(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "set_gameobject_active", "newActive": False}},
        )
        missing = dashboard_server.set_gameobject_active_sync({"game_object_path": "Avatar/Hat"})
        self.assertFalse(missing["ok"])
        mock_invoke.assert_not_called()
        result = dashboard_server.set_gameobject_active_sync({
            "game_object_path": "Avatar/Hat",
            "active": False,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_set_gameobject_active")
        self.assertIn("active", params)
        self.assertFalse(params["active"])

    def test_asset_prefab_crud_tool_source_exists(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor" / "Generic"
        source = (editor_dir / "UnityAssetPrefabCrud.cs").read_text(encoding="utf-8")
        for tool_name in (
            "vrc_find_assets",
            "vrc_get_asset_info",
            "vrc_instantiate_prefab",
            "vrc_unpack_prefab",
        ):
            self.assertIn(f'toolId: "{tool_name}"', source)
        self.assertIn("[VRCForgeCommand(", source)
        self.assertEqual(source.count("public static object HandleCommand(JObject @params)"), 4)
        # Reuses the shared reflection core rather than hard-referencing MA/VRC SDK assemblies.
        self.assertIn("ComponentCrudCore.ResolveGameObject", source)
        # Reads sit on stable AssetDatabase APIs.
        self.assertIn("AssetDatabase.FindAssets", source)
        # Both write tools register a Unity Undo entry for the checkpoint timeline.
        self.assertIn("Undo.RegisterCreatedObjectUndo", source)
        self.assertIn("PrefabUtility.InstantiatePrefab", source)
        self.assertIn("PrefabUtility.UnpackPrefabInstance", source)
        # payload must avoid auto-unwrap keys (data/result/payload/value).
        self.assertNotIn("value =", source)

    def test_asset_prefab_tools_registered_in_gateway(self) -> None:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.enabled = True
        dashboard_server.AGENT_GATEWAY.save_config(config)
        headers = {"Authorization": f"Bearer {config.token}"}

        with TestClient(dashboard_server.app) as client:
            payload = client.get("/api/agent/manifest?exposure_layer=execution", headers=headers).json()

        tool_names = {tool["name"] for tool in payload["tools"]}
        write_targets = {item["name"] for item in payload["writeTargets"]}
        # Read tools are directly callable.
        self.assertIn("vrcforge_find_assets", tool_names)
        self.assertIn("vrcforge_get_asset_info", tool_names)
        # Write tools are approval-gated: present as writeTargets, never as direct read tools.
        for write_name in (
            "vrcforge_instantiate_prefab",
            "vrcforge_unpack_prefab",
        ):
            self.assertNotIn(write_name, tool_names)
            self.assertIn(write_name, write_targets)

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_find_assets_forwards_query_and_type(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"filter": "t:Prefab outfit", "count": 1, "assets": []}},
        )
        result = dashboard_server.find_assets_sync({
            "query": "outfit",
            "type_name": "Prefab",
            "folder": "Assets/Outfits",
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_find_assets")
        self.assertEqual(params["query"], "outfit")
        self.assertEqual(params["typeName"], "Prefab")
        self.assertEqual(params["folder"], "Assets/Outfits")

    def test_get_asset_info_requires_path_or_guid(self) -> None:
        self.assertFalse(dashboard_server.get_asset_info_sync({})["ok"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_get_asset_info_forwards(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"assetPath": "Assets/Outfits/Dress.prefab", "isPrefab": True}},
        )
        result = dashboard_server.get_asset_info_sync({
            "asset_path": "Assets/Outfits/Dress.prefab",
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_get_asset_info")
        self.assertEqual(params["assetPath"], "Assets/Outfits/Dress.prefab")

    def test_instantiate_prefab_requires_asset(self) -> None:
        self.assertFalse(dashboard_server.instantiate_prefab_sync({})["ok"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_instantiate_prefab_forwards_with_preview(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "instantiate_prefab", "preview": True, "name": "Dress"}},
        )
        result = dashboard_server.instantiate_prefab_sync({
            "asset_path": "Assets/Outfits/Dress.prefab",
            "parent_path": "Avatar",
            "preview": True,
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_instantiate_prefab")
        self.assertTrue(params["preview"])
        self.assertEqual(params["assetPath"], "Assets/Outfits/Dress.prefab")
        self.assertEqual(params["parentPath"], "Avatar")

    def test_unpack_prefab_requires_path(self) -> None:
        self.assertFalse(dashboard_server.unpack_prefab_sync({})["ok"])

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_unpack_prefab_forwards_mode(self, mock_load_settings, mock_invoke) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"action": "unpack_prefab", "unpackMode": "completely"}},
        )
        result = dashboard_server.unpack_prefab_sync({
            "game_object_path": "Avatar/Dress",
            "mode": "completely",
        })
        self.assertTrue(result["ok"])
        _settings, tool_name, params = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_unpack_prefab")
        self.assertEqual(params["gameObjectPath"], "Avatar/Dress")
        self.assertEqual(params["mode"], "completely")

    def test_dynamic_code_execution_files_are_not_distributed(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        self.assertFalse((editor_dir / "RoslynExecutor.cs").exists())
        self.assertFalse((editor_dir / "RoslynSupportBootstrap.cs").exists())
        self.assertFalse((Path(__file__).resolve().parents[1] / "tools" / "install-roslyn-support.ps1").exists())

    def test_release_payload_excludes_dynamic_unity_mcp_execution_files(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "packaging" / "build_release.ps1").read_text(encoding="utf-8")
        self.assertIn("Assets\\VRCForge", script)
        self.assertIn("Editor\\MCP\\VRCForgeMcpTrustedRelease.cs", script)
        self.assertIn("build_unitypackage.ps1", script)
        self.assertIn("VRCForge.unitypackage", script)
        self.assertNotIn("Editor\\Tools\\ExecuteCode.cs", script)
        self.assertNotIn("Editor\\Setup\\RoslynInstaller.cs", script)

    def test_unity_editor_branding_uses_vrcforge_menu_and_paths(self) -> None:
        editor_dir = Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor"
        combined = "\n".join(path.read_text(encoding="utf-8") for path in editor_dir.glob("*.cs"))

        self.assertIn('MenuItem("VRCForge/MCP/Start Bridge Now")', combined)
        self.assertIn('[MenuItem("VRCForge/Uninstall VRCForge...")]', combined)
        self.assertIn("Assets/VRCForge/blendshapes_export.json", combined)
        self.assertIn('DefaultBackupRoot = "Library/VRCForge/Backups"', combined)
        old_brand = "VRC" + "AutoRig"
        self.assertNotIn(f'MenuItem("{old_brand}', combined)
        self.assertNotIn(f"[{old_brand}", combined)
        self.assertNotIn(f"Assets/{old_brand}", combined)

        self.assertTrue(callable(dashboard_server.install_vrcforge_into_unity_project))

    def test_unity_status_uses_project_scoped_core_identity(self) -> None:
        self.status_snapshot_patcher.stop()
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            for relative in (
                "Assets/VRCForge/Core/MCP/VRCForgeCommandAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeInputAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolRegistry.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs",
                "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs",
            ):
                marker = project / relative
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("// probe", encoding="utf-8")
            core_client = Mock()
            core_client.list_tools.return_value = [
                {"name": name} for name in dashboard_server.REQUIRED_VRCFORGE_UNITY_TOOLS
            ]
            try:
                with patch("unity_status_service.UnityMcpCoreClient", return_value=core_client) as mock_core:
                    status = dashboard_server.build_unity_status_snapshot(
                        SimpleNamespace(unity_mcp_timeout_seconds=5), project
                    )
            finally:
                self.status_snapshot_patcher.start()

        mock_core.assert_called_once_with(project, timeout_seconds=5)
        self.assertTrue(status["selectedInstanceMatched"])
        self.assertEqual(status["activeInstance"]["cliInstanceId"], "project-scoped")
        self.assertEqual(status["mcpHealth"]["transport"], "vrcforge-mcp-core")

    def test_doctor_marks_unity_bridge_checks_repairable(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.get("/api/app/doctor")

        self.assertEqual(response.status_code, 200)
        checks = {item["id"]: item for item in response.json()["checks"]}
        self.assertTrue(checks["unity.mcp.bridge"]["fixable"])
        self.assertIn("repair_unity_bridge", checks["unity.mcp.bridge"]["actions"])
        self.assertTrue(checks["unity.mcp.instance"]["fixable"])
        self.assertIn("repair_unity_bridge", checks["unity.mcp.instance"]["actions"])

    def test_extract_unity_project_path_from_command_line(self) -> None:
        command_line = r'"E:\unity\Unity 2022.3.22f1\Editor\Unity.exe" -projectPath "E:\unity\milltina"'

        self.assertEqual(
            dashboard_server.extract_unity_project_path_from_command_line(command_line),
            "E:/unity/milltina",
        )

    def test_discover_projects_includes_running_unity_project_path(self) -> None:
        previous_selected = dashboard_server.DASHBOARD_STATE.selected_project_path
        previous_status = dashboard_server.CURRENT_UNITY_STATUS
        try:
            dashboard_server.DASHBOARD_STATE.selected_project_path = ""
            dashboard_server.CURRENT_UNITY_STATUS = {"instances": []}
            with tempfile.TemporaryDirectory() as temp_dir:
                project = Path(temp_dir) / "Running Avatar"
                (project / "Assets").mkdir(parents=True)
                (project / "Packages").mkdir()
                (project / "ProjectSettings").mkdir()
                (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
                empty_catalog = Path(temp_dir) / "empty-catalog"
                with (
                    patch.dict(
                        os.environ,
                        {"APPDATA": str(empty_catalog), "LOCALAPPDATA": str(empty_catalog)},
                        clear=False,
                    ),
                    patch("dashboard_server.load_project_prefs", return_value={"customPaths": [], "hiddenPaths": []}),
                    patch(
                        "dashboard_server.list_running_unity_processes",
                        return_value=[
                            {
                                "processId": 123,
                                "executablePath": r"E:\unity\Unity 2022.3.22f1\Editor\Unity.exe",
                                "commandLine": f'"E:\\unity\\Unity 2022.3.22f1\\Editor\\Unity.exe" -projectPath "{project}"',
                            }
                        ],
                    ),
                ):
                    projects = dashboard_server.discover_projects([], include_external=True)

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["path"], dashboard_server.normalize_path_string(str(project)))
            self.assertIn("running-unity", projects[0]["sources"])
        finally:
            dashboard_server.DASHBOARD_STATE.selected_project_path = previous_selected
            dashboard_server.CURRENT_UNITY_STATUS = previous_status

    @unittest.skip("Replaced by Core-only repair tests; external connector repair is removed.")
    def test_repair_unity_mcp_bridge_already_healthy_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            healthy = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "activeInstance": {
                    "project": project.name,
                    "projectPath": str(project),
                    "hash": "abc123",
                },
                "vrcForgeToolsRegistered": True,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 78, "vrcForgeToolsCount": 48},
                "error": "",
            }
            with (
                patch("dashboard_server.build_unity_status_snapshot", return_value=healthy),
                patch("dashboard_server.verify_unity_mcp_execution_connection", return_value=(True, {"tool": "vrc_check_roslyn_status"})),
                patch("dashboard_server.subprocess.Popen") as mock_popen,
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(projectPath=str(project), allowUnityRelaunch=True)
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "healthy")
        mock_popen.assert_not_called()

    @unittest.skip("Replaced by Core-only repair tests; external connector repair is removed.")
    def test_repair_unity_mcp_bridge_does_not_accept_another_selected_project_as_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "selected" / "SharedProject"
            other = root / "other" / "SharedProject"
            for candidate in (project, other):
                (candidate / "Assets").mkdir(parents=True)
                (candidate / "Packages").mkdir()
                (candidate / "ProjectSettings").mkdir()
                (candidate / "ProjectSettings" / "ProjectVersion.txt").write_text(
                    "m_EditorVersion: 2022.3.22f1\n",
                    encoding="utf-8",
                )
            healthy_other = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "activeInstance": {
                    "project": other.name,
                    "projectPath": str(other),
                    "hash": "other123",
                },
                "vrcForgeToolsRegistered": True,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 78, "vrcForgeToolsCount": 48},
                "error": "",
            }
            with (
                patch("dashboard_server.build_unity_status_snapshot", return_value=healthy_other) as mock_status,
                patch("dashboard_server.recent_unity_mcp_execution_error", return_value={}),
                patch("dashboard_server.ensure_unity_mcp_server_running", return_value=False),
                patch("dashboard_server.verify_unity_mcp_execution_connection") as mock_probe,
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(projectPath=str(project), allowUnityRelaunch=False)
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["before"]["selectedInstanceMatched"])
        self.assertTrue(mock_status.call_args_list)
        self.assertTrue(all(call.args[1] == project for call in mock_status.call_args_list))
        mock_probe.assert_not_called()

    def test_repair_unity_mcp_bridge_returns_busy_when_repair_running(self) -> None:
        acquired = dashboard_server.UNITY_MCP_REPAIR_LOCK.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            result = dashboard_server.repair_unity_mcp_bridge_sync(
                dashboard_server.UnityMcpRepairRequest(projectPath=r"C:\Unity\AvatarProject")
            )
        finally:
            dashboard_server.UNITY_MCP_REPAIR_LOCK.release()

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "busy")
        self.assertIn("repair_lock", {phase["id"] for phase in result["phases"]})

    @unittest.skip("Replaced by Core-only repair tests; external connector repair is removed.")
    def test_repair_unity_mcp_bridge_refuses_to_close_unmatched_unity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "selected" / "SharedProject"
            other = root / "other" / "SharedProject"
            editor = root / "Unity.exe"
            for candidate in (project, other):
                (candidate / "Assets").mkdir(parents=True)
                (candidate / "Packages").mkdir()
                (candidate / "ProjectSettings").mkdir()
                (candidate / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            editor.write_text("", encoding="utf-8")
            offline = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": False,
                "selectedInstanceMatched": False,
                "activeInstanceCount": 0,
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0},
                "error": "",
            }
            with (
                patch("dashboard_server.build_unity_status_snapshot", return_value=offline),
                patch("dashboard_server.ensure_unity_mcp_server_running", return_value=True),
                patch("dashboard_server.wait_for_unity_project_registration", return_value=(False, {"instances": []})),
                patch(
                    "dashboard_server.list_running_unity_processes",
                    return_value=[
                        {
                            "processId": 123,
                            "executablePath": str(editor),
                            "commandLine": f'"{editor}" -projectPath "{other}"',
                        }
                    ],
                ),
                patch("dashboard_server.launch_unity_project") as mock_launch,
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(projectPath=str(project), unityEditorPath=str(editor), allowUnityRelaunch=True)
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_user_action")
        self.assertIn("did not close any editor", json.dumps(result["phases"]))
        mock_launch.assert_not_called()

    def test_close_unity_project_fails_closed_when_process_evidence_is_unavailable(self) -> None:
        with (
            patch(
                "dashboard_server.list_running_unity_processes",
                side_effect=dashboard_server.UnityProcessDiscoveryUnavailable("unavailable"),
            ) as mock_list,
            patch("dashboard_server.request_windows_process_close") as mock_close,
        ):
            ok, message, detail = dashboard_server.close_unity_project_gracefully(
                Path(r"C:\Unity\AvatarProject"),
                5,
            )

        self.assertFalse(ok)
        self.assertIn("evidence is unavailable", message)
        self.assertFalse(detail["evidenceAvailable"])
        mock_list.assert_called_once_with(require_discovery_evidence=True)
        mock_close.assert_not_called()

    @unittest.skip("Replaced by Core-only repair tests; external connector repair is removed.")
    def test_repair_unity_mcp_bridge_registered_without_tools_needs_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            registered_without_tools = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": ["vrc_export_blendshapes"],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0},
                "error": "",
            }
            with (
                patch("dashboard_server.build_unity_status_snapshot", return_value=registered_without_tools),
                patch("dashboard_server.ensure_unity_mcp_server_running", return_value=True),
                patch("dashboard_server.wait_for_unity_project_registration", return_value=(True, {"instances": [{"project": project.name}]})),
                patch("dashboard_server.restart_unity_mcp_server", return_value=False),
                patch("dashboard_server.recent_unity_mcp_execution_error", return_value={}),
                patch("dashboard_server.close_unity_project_gracefully") as mock_close,
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(projectPath=str(project), allowUnityRelaunch=False, waitSeconds=5)
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_user_action")
        self.assertIn("unity_tools", {phase["id"] for phase in result["phases"]})
        self.assertFalse(result["after"]["vrcForgeToolsRegistered"])
        mock_close.assert_not_called()

    def test_launch_unity_project_uses_editor_directory_as_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            editor_dir = root / "Editor"
            project = root / "AvatarProject"
            editor_dir.mkdir()
            project.mkdir()
            editor = editor_dir / "Unity.exe"
            editor.write_text("", encoding="utf-8")

            internal_dir = str(dashboard_server.ROOT_DIR / "backend" / "_internal")
            with (
                patch.dict(dashboard_server.os.environ, {"PATH": internal_dir + os.pathsep + r"C:\Windows"}),
                patch("dashboard_server.pyinstaller_internal_dir", return_value=Path(internal_dir)),
                patch("dashboard_server.set_windows_dll_directory") as mock_set_dll_directory,
                patch("dashboard_server.subprocess.Popen") as mock_popen,
            ):
                ok, error = dashboard_server.launch_unity_project(editor, project)

            self.assertTrue(ok)
            self.assertEqual(error, "")
            mock_popen.assert_called_once_with(
                [
                    str(editor),
                    "-projectPath",
                    str(project),
                    "-executeMethod",
                    "VRCForge.Editor.McpBridgeBootstrap.StartBridgeNow",
                ],
                cwd=str(editor_dir),
                env=ANY,
            )
            self.assertNotIn(internal_dir, mock_popen.call_args.kwargs["env"]["PATH"])
            self.assertEqual([call.args[0] for call in mock_set_dll_directory.call_args_list], [None, internal_dir])

    def test_open_project_route_accepts_project_path_alias_and_uses_editor_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "AvatarProject"
            editor_dir = root / "Editor"
            editor = editor_dir / "Unity.exe"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            editor_dir.mkdir()
            editor.write_text("", encoding="utf-8")
            previous_editor = dashboard_server.DASHBOARD_STATE.unity_editor_path
            previous_selected = dashboard_server.DASHBOARD_STATE.selected_project_path
            dashboard_server.DASHBOARD_STATE.unity_editor_path = str(editor)
            try:
                for payload in ({"projectPath": str(project)}, {"project_path": str(project)}):
                    with patch("dashboard_server.subprocess.Popen") as mock_popen:
                        with TestClient(dashboard_server.app) as client:
                            response = client.post("/api/projects/open", json=payload)
                    self.assertEqual(response.status_code, 200)
                    command = mock_popen.call_args.args[0]
                    self.assertEqual(command[0], str(editor))
                    self.assertEqual(command[1], "-projectPath")
                    self.assertEqual(Path(command[2]).resolve(), project.resolve())
                    self.assertEqual(mock_popen.call_args.kwargs["cwd"], str(editor_dir))
                    self.assertNotIn(str(dashboard_server.ROOT_DIR / "backend" / "_internal"), mock_popen.call_args.kwargs["env"]["PATH"])
            finally:
                dashboard_server.DASHBOARD_STATE.unity_editor_path = previous_editor
                dashboard_server.DASHBOARD_STATE.selected_project_path = previous_selected

    def test_discover_vrcforge_unity_tool_definitions_uses_core_registry_not_legacy_attributes(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        contract_text = (
            repo_root / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpToolContract.cs"
        ).read_text(encoding="utf-8-sig")
        contract_names = set(re.findall(r'\{\s*"(vrc_[a-z0-9_]+)"\s*,\s*"VRCForge\.', contract_text))
        self.assertEqual(contract_names, set(dashboard_server.VRCFORGE_UNITY_TOOL_REGISTRY))
        self.assertEqual(len(contract_names), 64)
        legacy_hits = [
            path for path in (repo_root / "Assets" / "VRCForge").rglob("*.cs")
            if "McpForUnityTool" in path.read_text(encoding="utf-8-sig")
        ]
        self.assertEqual(legacy_hits, [])
        self.assertFalse(hasattr(dashboard_server, "discover_vrcforge_unity_tool_definitions"))

    @unittest.skip("Replaced by Core-only repair tests; external connector repair is removed.")
    def test_repair_unity_mcp_bridge_reregisters_empty_tool_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            editor = project / "Assets" / "VRCForge" / "Editor"
            editor.mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            core = project / "Assets" / "VRCForge" / "Core" / "MCP"
            core.mkdir(parents=True)
            for name in ("VRCForgeCommandAttribute.cs", "VRCForgeInputAttribute.cs", "VRCForgeToolRegistry.cs", "VRCForgeToolResult.cs"):
                (core / name).write_text("// core\n", encoding="utf-8")
            (editor / "MCP").mkdir()
            (editor / "MCP" / "VRCForgeMcpCoreServer.cs").write_text("// core server\n", encoding="utf-8")
            (editor / "SampleTool.cs").write_text(
                """
using MCPForUnity.Editor.Tools;

namespace VRCForge.Editor
{
    [McpForUnityTool(name: "vrc_export_blendshapes", Description = "Export blendshapes.")]
    public static class SampleTool {}
}
""",
                encoding="utf-8",
            )
            registered_without_tools = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "activeInstance": {
                    "project": project.name,
                    "projectPath": str(project),
                    "hash": "abc123",
                    "cliInstanceId": "abc123",
                },
                "instances": [
                    {
                        "project": project.name,
                        "projectPath": str(project),
                        "hash": "abc123",
                        "cliInstanceId": "abc123",
                    }
                ],
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": ["vrc_export_blendshapes"],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0},
                "error": "",
            }
            healthy_summary = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "vrcForgeToolsRegistered": True,
                "totalTools": 78,
                "vrcForgeToolsCount": 48,
                "missingRequiredVrcForgeTools": [],
                "toolsError": "",
                "error": "",
            }
            with (
                patch("dashboard_server.build_unity_status_snapshot", return_value=registered_without_tools),
                patch("dashboard_server.ensure_unity_mcp_server_running", return_value=True),
                patch("dashboard_server.wait_for_unity_project_registration", return_value=(True, {"instances": [{"project": project.name}]})),
                patch("dashboard_server.wait_for_unity_tools_ready", side_effect=[(False, dashboard_server._unity_repair_status_summary(registered_without_tools)), (True, healthy_summary)]),
                patch("dashboard_server.post_unity_http_json", return_value=(True, {"ok": True}, "", 200)) as mock_post,
                patch("dashboard_server.verify_unity_mcp_execution_connection", return_value=(True, {"tool": "vrc_check_roslyn_status"})),
                patch("dashboard_server.recent_unity_mcp_execution_error", return_value={}),
                patch("dashboard_server.restart_unity_mcp_server") as mock_restart,
                patch("dashboard_server.close_unity_project_gracefully") as mock_close,
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(projectPath=str(project), allowUnityRelaunch=False, waitSeconds=5)
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "recovered")
        self.assertIn("unity_tool_registration", {phase["id"] for phase in result["phases"]})
        self.assertEqual(mock_post.call_args.args[1], "/register-tools")
        self.assertEqual(mock_post.call_args.args[2]["project_id"], "abc123")
        self.assertEqual(
            [tool["name"] for tool in mock_post.call_args.args[2]["tools"]],
            list(dashboard_server.VRCFORGE_UNITY_TOOL_REGISTRY),
        )
        mock_restart.assert_not_called()
        mock_close.assert_not_called()

    def test_unity_repair_tools_message_distinguishes_execution_disconnect(self) -> None:
        message = dashboard_server.unity_repair_tools_message(
            {
                "unityInstanceRegistered": True,
                "totalTools": 0,
                "vrcForgeToolsRegistered": False,
                "toolsError": "HTTP 503: No Unity instances connected.",
            }
        )

        self.assertIn("execution connection", message)

    @unittest.skip("Replaced by Core-only repair tests; external connector repair is removed.")
    def test_repair_unity_mcp_bridge_restart_recovers_empty_tool_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            registered_without_tools = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": ["vrc_export_blendshapes"],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0},
                "error": "",
            }
            healthy_summary = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "vrcForgeToolsRegistered": True,
                "totalTools": 78,
                "vrcForgeToolsCount": 48,
                "missingRequiredVrcForgeTools": [],
                "error": "",
            }
            with (
                patch("dashboard_server.build_unity_status_snapshot", return_value=registered_without_tools),
                patch("dashboard_server.ensure_unity_mcp_server_running", return_value=True),
                patch(
                    "dashboard_server.wait_for_unity_project_registration",
                    side_effect=[
                        (True, {"instances": [{"project": project.name}]}),
                        (True, {"instances": [{"project": project.name}]}),
                    ],
                ),
            patch("dashboard_server.wait_for_unity_tools_ready", side_effect=[(False, dashboard_server._unity_repair_status_summary(registered_without_tools)), (True, healthy_summary)]),
            patch("dashboard_server.restart_unity_mcp_server", return_value=True) as mock_restart,
            patch("dashboard_server.verify_unity_mcp_execution_connection", return_value=(True, {"tool": "vrc_check_roslyn_status"})),
            patch("dashboard_server.recent_unity_mcp_execution_error", return_value={}),
            patch("dashboard_server.close_unity_project_gracefully") as mock_close,
        ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(projectPath=str(project), allowUnityRelaunch=False, waitSeconds=5)
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(result["after"]["totalTools"], 78)
        mock_restart.assert_called_once()
        mock_close.assert_not_called()

    @unittest.skip("Replaced by Core-only repair tests; external connector repair is removed.")
    def test_repair_unity_mcp_bridge_relaunches_and_reconnects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "AvatarProject"
            editor = root / "Unity.exe"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            editor.write_text("", encoding="utf-8")
            offline = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": False,
                "selectedInstanceMatched": False,
                "activeInstanceCount": 0,
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0},
                "error": "",
            }
            healthy = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "activeInstance": {
                    "project": project.name,
                    "projectPath": str(project),
                    "hash": "abc123",
                },
                "vrcForgeToolsRegistered": True,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 78, "vrcForgeToolsCount": 48},
                "error": "",
            }
            with (
                patch("dashboard_server.build_unity_status_snapshot", side_effect=[offline, healthy]),
                patch("dashboard_server.ensure_unity_mcp_server_running", return_value=True),
                patch(
                    "dashboard_server.wait_for_unity_project_registration",
                    side_effect=[
                        (False, {"instances": []}),
                        (True, {"instances": [{"project": project.name, "hash": "abc123"}]}),
                    ],
                ) as mock_wait,
                patch("dashboard_server.verify_unity_mcp_execution_connection", return_value=(True, {"tool": "vrc_check_roslyn_status"})),
                patch("dashboard_server.close_unity_project_gracefully", return_value=(True, "Unity closed cleanly.", {})) as mock_close,
                patch("dashboard_server.launch_unity_project", return_value=(True, "")) as mock_launch,
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(projectPath=str(project), unityEditorPath=str(editor), allowUnityRelaunch=True)
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(mock_wait.call_count, 2)
        mock_close.assert_called_once()
        mock_launch.assert_called_once()

    @unittest.skip("Replaced by Core-only repair tests; external connector repair is removed.")
    def test_repair_unity_mcp_bridge_relaunch_recovers_after_slow_tool_list_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "AvatarProject"
            editor = root / "Unity.exe"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            editor.write_text("", encoding="utf-8")
            settings = SimpleNamespace(unity_mcp_timeout_seconds=30, unity_mcp_retries=3, unity_mcp_retry_backoff_seconds=1.0)
            offline = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": False,
                "selectedInstanceMatched": False,
                "activeInstanceCount": 0,
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0},
                "error": "",
            }
            tool_list_timeout = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "activeInstance": {
                    "project": project.name,
                    "projectPath": str(project),
                    "hash": "abc123",
                },
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": ["vrc_export_blendshapes"],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0, "error": "tool list timed out"},
                "error": "tool list timed out",
            }
            healthy = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "activeInstance": {
                    "project": project.name,
                    "projectPath": str(project),
                    "hash": "abc123",
                },
                "vrcForgeToolsRegistered": True,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 78, "vrcForgeToolsCount": 48},
                "error": "",
            }
            status_snapshots = [offline, tool_list_timeout, healthy]
            observed_timeouts: list[int] = []

            def fake_status_snapshot(
                snapshot_settings: SimpleNamespace,
                _project_root: Path | None = None,
            ) -> dict[str, object]:
                observed_timeouts.append(snapshot_settings.unity_mcp_timeout_seconds)
                return status_snapshots.pop(0)

            with (
                patch("dashboard_server.load_dashboard_settings", return_value=settings),
                patch("dashboard_server.build_unity_status_snapshot", side_effect=fake_status_snapshot),
                patch("dashboard_server.ensure_unity_mcp_server_running", return_value=True),
                patch(
                    "dashboard_server.wait_for_unity_project_registration",
                    side_effect=[
                        (False, {"instances": []}),
                        (True, {"instances": [{"project": project.name, "hash": "abc123"}]}),
                    ],
                ),
                patch("dashboard_server.verify_unity_mcp_execution_connection", return_value=(True, {"tool": "vrc_check_roslyn_status"})),
                patch("dashboard_server.recent_unity_mcp_execution_error", return_value={}),
                patch("dashboard_server.close_unity_project_gracefully", return_value=(True, "Unity closed cleanly.", {})),
                patch("dashboard_server.launch_unity_project", return_value=(True, "")),
                patch("dashboard_server.time.sleep"),
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(
                        projectPath=str(project),
                        unityEditorPath=str(editor),
                        allowUnityRelaunch=True,
                        waitSeconds=12,
                    )
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(result["after"]["totalTools"], 78)
        self.assertEqual(observed_timeouts, [3, 10, 10])
        self.assertEqual(settings.unity_mcp_timeout_seconds, 3)

    @unittest.skip("Replaced by Core-only repair tests; external connector repair is removed.")
    def test_repair_unity_mcp_bridge_relaunch_keeps_actionable_error_when_tools_still_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "AvatarProject"
            editor = root / "Unity.exe"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
            editor.write_text("", encoding="utf-8")
            settings = SimpleNamespace(unity_mcp_timeout_seconds=30, unity_mcp_retries=3, unity_mcp_retry_backoff_seconds=1.0)
            offline = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": False,
                "selectedInstanceMatched": False,
                "activeInstanceCount": 0,
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": [],
                "tools": {"totalTools": 0, "vrcForgeToolsCount": 0},
                "error": "",
            }
            registered_without_tools = {
                "connected": True,
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "activeInstanceCount": 1,
                "vrcForgeToolsRegistered": False,
                "totalTools": 0,
                "vrcForgeToolsCount": 0,
                "missingRequiredVrcForgeTools": ["vrc_export_blendshapes"],
                "toolsError": "tool list timed out",
                "error": "tool list timed out",
            }

            with (
                patch("dashboard_server.load_dashboard_settings", return_value=settings),
                patch("dashboard_server.build_unity_status_snapshot", return_value=offline),
                patch("dashboard_server.ensure_unity_mcp_server_running", return_value=True),
                patch(
                    "dashboard_server.wait_for_unity_project_registration",
                    side_effect=[
                        (False, {"instances": []}),
                        (True, {"instances": [{"project": project.name, "hash": "abc123"}]}),
                    ],
                ),
                patch("dashboard_server.wait_for_unity_tools_ready", return_value=(False, registered_without_tools)) as mock_wait_tools,
                patch("dashboard_server.register_vrcforge_unity_tools_from_project", return_value=(False, {"error": "no tools"})),
                patch("dashboard_server.recent_unity_mcp_execution_error", return_value={}),
                patch("dashboard_server.close_unity_project_gracefully", return_value=(True, "Unity closed cleanly.", {})),
                patch("dashboard_server.launch_unity_project", return_value=(True, "")),
            ):
                result = dashboard_server.repair_unity_mcp_bridge_sync(
                    dashboard_server.UnityMcpRepairRequest(
                        projectPath=str(project),
                        unityEditorPath=str(editor),
                        allowUnityRelaunch=True,
                        waitSeconds=12,
                    )
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_user_action")
        self.assertFalse(result["after"]["vrcForgeToolsRegistered"])
        self.assertIn("unity_tools_after_launch", {phase["id"] for phase in result["phases"]})
        self.assertEqual(mock_wait_tools.call_args.args[0].unity_mcp_timeout_seconds, 10)

    def test_scene_capture_tool_supports_play_mode_game_view_status(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "Assets" / "VRCForge" / "Editor" / "SceneViewCaptureTool.cs").read_text(
            encoding="utf-8"
        )

        self.assertIn("EditorApplication.isPlaying", source)
        self.assertIn("statusOnly", source)
        self.assertIn("requirePlayMode", source)
        self.assertIn('captureMode = isPlayMode ? "game_view" : "scene_view"', source)
        self.assertIn("ScreenCapture.CaptureScreenshotAsTexture", source)
        self.assertIn("CaptureCameraToPng(camera, absolutePath, width, height)", source)
        self.assertIn("active_game_camera", source)
        self.assertIn("avoid Gesture Manager menu overlays", source)
        self.assertIn("IsLikelyOverlayCamera", source)
        self.assertIn("IsGestureManagerRunning", source)
        self.assertIn("Gesture Manager recommended for accurate preview", source)
        self.assertIn("Play Mode with Gesture Manager is recommended", source)

    def test_health_returns_defaults_and_state(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.get("/api/health")
            self.assertEqual(response.status_code, 200)

            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertIn("defaults", payload)
            self.assertIn("state", payload)
            self.assertIn("projects", payload)
            self.assertIn("apiConfig", payload)
            self.assertIn("configPath", payload)
            self.assertIn("components", payload)
            self.assertIn("paths", payload)
            self.assertIn("backend", payload["components"])
            self.assertIn("dashboardFiles", payload["components"])
            self.assertIn("configReadWrite", payload["components"])
            self.assertIn("logsWrite", payload["components"])
            self.assertIn("artifactsWrite", payload["components"])
            self.assertIn("selectedUnityProject", payload["components"])
            self.assertIn("unityPluginInstalled", payload["components"])
            self.assertIn("mcpPackageConfigured", payload["components"])
            self.assertIn("unityMcpBridgeReachable", payload["components"])
            self.assertIn("providerConfigPresent", payload["components"])
            self.assertEqual(payload["defaults"]["sourceMode"], "unity_live_export")
            self.assertFalse(payload["defaults"]["mockExecute"])
            self.assertNotIn("recentLogs", payload)
            self.assertEqual(payload["logRetentionHours"], 120)

    def test_windows_installer_sources_enforce_x64_and_release_gates(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        build_script = (repo_root / "packaging" / "build_release.ps1").read_text(encoding="utf-8")
        local_build_script = (repo_root / "packaging" / "build_local.ps1").read_text(encoding="utf-8")
        publish_script = (repo_root / "packaging" / "publish_release.ps1").read_text(encoding="utf-8")
        launcher_project = (repo_root / "launcher" / "VRCForge.Launcher" / "VRCForge.Launcher.csproj").read_text(encoding="utf-8")
        offline_nsis = (repo_root / "installer" / "VRCForge_Offline_Installer_x64.nsi").read_text(encoding="utf-8")
        web_nsis = (repo_root / "installer" / "VRCForge_Web_Installer_x64.nsi").read_text(encoding="utf-8")

        self.assertIn("git status --short", build_script)
        self.assertIn("git log origin/main..HEAD --oneline", build_script)
        self.assertIn("git show origin/main:VERSION", build_script)
        self.assertIn("[switch]$AllowVersionMismatch", build_script)
        self.assertIn("$AllowUnpushed -and $AllowVersionMismatch", build_script)
        self.assertIn("AllowVersionMismatch = $true", local_build_script)
        self.assertNotIn("AllowVersionMismatch", publish_script)
        self.assertIn('$strictSourceBuild = -not ($AllowDirty -or $AllowUnpushed -or $AllowVersionMismatch)', build_script)
        self.assertIn('$strictEvidenceBuild = $strictSourceBuild -and [bool]$StrictEvidence', build_script)
        self.assertIn('$strictReleaseBuild = $strictSourceBuild -and -not $strictEvidenceBuild', build_script)
        self.assertIn('mode = if ($strictEvidenceBuild) { "strict-evidence" }', build_script)
        self.assertIn("releaseEligible = [bool]$strictReleaseBuild", build_script)
        self.assertIn("$buildPolicy.evidenceEligible = $true", build_script)
        self.assertIn('Package version must match the source VERSION file', build_script)
        self.assertIn('Strict release build requires a pinned 64-hex UvDownloadSha256', build_script)
        self.assertIn('Strict release build requires a successful git fetch of origin', build_script)
        self.assertIn('Strict release build requires HEAD to equal origin/main', build_script)
        self.assertIn('$finalHeadCommit -ne $headCommit', build_script)
        self.assertIn('$finalOriginMainCommit -ne $originMainCommit', build_script)
        self.assertIn('buildPolicy', publish_script)
        self.assertIn('Local-acceptance artifacts are not publishable', publish_script)
        self.assertIn('Publishing requires a successful git fetch of origin', publish_script)
        self.assertIn('Release manifest is missing a pinned uv download SHA-256', publish_script)
        self.assertIn('Get-StreamSha256 -Stream $guardStream', publish_script)
        self.assertIn("check_third_party_licenses.ps1", build_script)
        self.assertNotIn("check_coplaydev_mcp_license.ps1", build_script)
        self.assertIn('schema = "vrcforge.payload-integrity.v1"', build_script)
        self.assertIn('Join-Path $payloadRoot "payload-integrity.json"', build_script)
        self.assertLess(build_script.index("payload-integrity.json"), build_script.index("Compress-Archive"))
        self.assertIn("smoke_packaged_backend.py", build_script)
        self.assertLess(build_script.index("smoke_packaged_backend.py"), build_script.index("$offlineInstaller ="))
        self.assertIn('schema = "vrcforge.packaged_backend_smoke.v2"', build_script)
        self.assertNotIn("CoplayDev-Unity-MCP-LICENSE.txt", build_script)
        self.assertNotIn("CoplayDev-Unity-MCP-DISTRIBUTION-NOTES.txt", build_script)
        self.assertIn("Install-UvRuntime", build_script)
        self.assertIn("uv-x86_64-pc-windows-msvc.zip", build_script)
        self.assertIn("uv-LICENSE-MIT.txt", build_script)
        self.assertIn("uv-LICENSE-APACHE-2.0.txt", build_script)
        self.assertIn("start_dashboard.cmd", build_script)
        self.assertIn("VRCForge-NOTICE.txt", build_script)
        self.assertIn("build_unitypackage.ps1", build_script)
        self.assertIn("PayloadDownloadUrl must exactly match the official version-bound release asset URL", build_script)
        self.assertIn("win-x64", build_script)
        self.assertIn("-p:DebugType=none", build_script)
        self.assertIn("-p:DebugSymbols=false", build_script)
        self.assertIn("Build-TauriDesktopApp", build_script)
        self.assertIn("vrcforge-agentic-app.exe", build_script)
        self.assertIn('Join-Path $payloadRoot "VRCForge.exe"', build_script)
        self.assertIn('Get-ChildItem -LiteralPath (Join-Path $payloadRoot "tools") -Recurse -Filter "*.ps1"', build_script)
        self.assertIn('Join-Path $payloadRoot "tools\\legacy-launcher"', build_script)
        self.assertIn('Remove-Item -LiteralPath (Join-Path $legacyLauncherBuildRoot "VRCForge.pdb")', build_script)
        self.assertIn("Resolve-DotNetExe", build_script)
        self.assertIn("Resolve-NpmExe", build_script)
        self.assertIn("Resolve-CargoExe", build_script)
        self.assertIn("Resolve-MakeNsisExe", build_script)
        self.assertIn("VRCForge_Web_Installer_x64.exe", publish_script)
        self.assertIn("VRCForge_Offline_Installer_x64.exe", publish_script)
        self.assertIn("VRCForge_Windows_x64_$Version.zip", publish_script)
        self.assertIn("VRCForge.unitypackage", publish_script)
        self.assertIn('(?i)(alpha|beta|rc)', publish_script)
        self.assertIn("target_commitish", publish_script)
        self.assertNotIn("--clobber", publish_script)
        self.assertIn("Refusing remote asset mutation", publish_script)
        self.assertIn('git ls-remote --tags origin $directRef $peeledRef', publish_script)
        self.assertIn('"--verify-tag"', publish_script)
        self.assertIn("win-x64", launcher_project)
        self.assertIn("<Platforms>x64</Platforms>", launcher_project)
        self.assertIn("<DebugType>none</DebugType>", launcher_project)
        self.assertIn("<DebugSymbols>false</DebugSymbols>", launcher_project)
        self.assertIn("VRCForge_Offline_Installer_x64.exe", offline_nsis)
        self.assertIn("VRCForge_Web_Installer_x64.exe", web_nsis)
        for source in (offline_nsis, web_nsis):
            self.assertIn('!define INSTALL_LEAF "VRCForge"', source)
            self.assertIn('!define USER_DATA_RELATIVE "VRCForge\\agentic-app"', source)
            self.assertIn('StrCpy $UserDataRoot "$LOCALAPPDATA\\${USER_DATA_RELATIVE}"', source)
            self.assertIn('CreateDirectory "$UserDataRoot\\config"', source)
        self.assertIn("nsDialogs.nsh", offline_nsis)
        self.assertIn("nsDialogs.nsh", web_nsis)
        self.assertIn("MUI_UNGETLANGUAGE", offline_nsis)
        self.assertIn("MUI_UNGETLANGUAGE", web_nsis)
        self.assertIn("UninstallShortcutName", offline_nsis)
        self.assertIn("UninstallShortcutName", web_nsis)
        self.assertIn('CreateShortCut "$SMPROGRAMS\\${START_MENU_GROUP}\\$(UninstallShortcutName)"', offline_nsis)
        self.assertIn('CreateShortCut "$SMPROGRAMS\\${START_MENU_GROUP}\\$(UninstallShortcutName)"', web_nsis)
        for shortcut_name in (
            "Uninstall VRCForge.lnk",
            "卸载 VRCForge.lnk",
            "解除安裝 VRCForge.lnk",
            "VRCForge をアンインストール.lnk",
        ):
            self.assertIn(shortcut_name, offline_nsis)
            self.assertIn(shortcut_name, web_nsis)
        self.assertIn("清除用户数据和历史对话", offline_nsis)
        self.assertIn("Clear user data and chat history", web_nsis)
        self.assertIn("--cleanup-user-data", offline_nsis)
        self.assertIn("--cleanup-user-data", web_nsis)
        self.assertNotIn("powershell -NoProfile", offline_nsis)
        self.assertNotIn("NSISdl::download", web_nsis)
        self.assertNotIn("taskkill", offline_nsis)
        self.assertNotIn("taskkill", web_nsis)
        for vulnerable_command in ("cmd /D /C", "certutil", "findstr", "tar.exe"):
            self.assertNotIn(vulnerable_command, web_nsis)
        self.assertIn("$SYSDIR\\WindowsPowerShell\\v1.0\\powershell.exe", web_nsis)
        self.assertIn("$WINDIR\\Sysnative\\WindowsPowerShell\\v1.0\\powershell.exe", web_nsis)
        self.assertIn("$WINDIR\\Sysnative\\WindowsPowerShell\\v1.0\\powershell.exe", offline_nsis)
        self.assertIn("-WindowStyle Hidden", web_nsis)
        self.assertIn("-WindowStyle Hidden", offline_nsis)
        self.assertIn('InstallDir "$PROGRAMFILES64\\${INSTALL_LEAF}"', offline_nsis)
        self.assertIn('InstallDir "$PROGRAMFILES64\\${INSTALL_LEAF}"', web_nsis)
        self.assertNotIn('StrCpy $INSTDIR "$PROGRAMFILES64\\VRCForge"', offline_nsis)
        self.assertNotIn('StrCpy $INSTDIR "$PROGRAMFILES64\\VRCForge"', web_nsis)
        self.assertIn("VRCForge_WebPayload.ps1", web_nsis)
        self.assertIn("VRCForge_WebPayload.ps1", offline_nsis)
        self.assertIn("-Action ValidateDestination", web_nsis)
        self.assertIn("-Action ValidateDestination", offline_nsis)
        self.assertIn('-ProgramFilesRoot "$PROGRAMFILES64"', web_nsis)
        self.assertIn('-ProgramFilesRoot "$PROGRAMFILES64"', offline_nsis)
        self.assertIn("-Action Prepare", web_nsis)
        self.assertIn("-Action Extract", web_nsis)
        self.assertIn("Pop $0", offline_nsis)
        self.assertIn("Pop $0", web_nsis)
        self.assertIn("Call un.ClearUserDataIfRequested", offline_nsis)
        self.assertIn("Call un.ClearUserDataIfRequested", web_nsis)

    def test_release_scripts_lock_uploads_and_bind_strict_uv_provenance(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        build_script = (repo_root / "packaging" / "build_release.ps1").read_text(encoding="utf-8")
        publish_script = (repo_root / "packaging" / "publish_release.ps1").read_text(encoding="utf-8")

        mutex_name = "Local\\VRCForge.Release.BuildPublish.v1"
        for script in (build_script, publish_script):
            self.assertIn(mutex_name, script)
            self.assertIn("$mutex.WaitOne(0)", script)
            self.assertIn("catch [System.Threading.AbandonedMutexException]", script)
            self.assertIn("Another VRCForge build or publish operation is already running.", script)
            self.assertIn("$releaseOperationMutex.ReleaseMutex()", script)

        self.assertIn("-not $RequireVerifiedDownload", build_script)
        self.assertIn("$uvEntries.Count -ne 1 -or $uvxEntries.Count -ne 1", build_script)
        self.assertIn("$entryStream.CopyTo($outputStream)", build_script)
        self.assertNotIn("Expand-Archive -LiteralPath $zipPath", build_script)
        self.assertNotIn("Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath", build_script)
        self.assertIn("Get-StreamSha256 -Stream $archiveStream", build_script)
        self.assertIn("[System.IO.FileShare]::Read", build_script)
        self.assertIn("[System.IO.Compression.ZipArchive]::new", build_script)
        self.assertIn("Add-Type -AssemblyName System.IO.Compression", build_script)
        self.assertIn("Remove-Item -LiteralPath $DestinationDir -Recurse -Force", build_script)
        self.assertNotIn("Remove-Item -LiteralPath $DestinationDir -Recurse -Force -ErrorAction SilentlyContinue", build_script)
        self.assertIn("Installed uv runtime directory must contain exactly regular uv.exe and uvx.exe files.", build_script)
        self.assertIn('source = "download"', build_script)
        self.assertIn("archiveDigestVerified = $archiveDigestVerified", build_script)
        self.assertIn("uvRuntime = $uvRuntimeProvenance", build_script)
        self.assertLess(
            build_script.index("Invoke-WebRequest -UseBasicParsing -Uri $uvDownloadUrl"),
            build_script.index("Remove-Item -LiteralPath $DestinationDir -Recurse -Force"),
        )

        self.assertIn('$uvRuntimeSource -ne "download"', publish_script)
        self.assertIn("$uvRuntimeArchiveDigestVerified -ne $true", publish_script)
        self.assertIn("$uvRuntimeArchiveSha256.ToLowerInvariant()", publish_script)
        self.assertIn("Portable payload $expectedEntryPath digest does not match strict uv provenance.", publish_script)
        self.assertIn("Portable payload tools/uv subtree must contain exactly uv.exe and uvx.exe.", publish_script)
        self.assertIn("Add-Type -AssemblyName System.IO.Compression", publish_script)
        self.assertIn("vrcforge_release_publish_", publish_script)
        self.assertIn("[System.IO.FileShare]::Read", publish_script)
        self.assertIn("Get-StreamSha256 -Stream $guardStream", publish_script)
        self.assertNotIn("Get-FileHash -Algorithm SHA256 -LiteralPath $artifact", publish_script)
        self.assertIn('"repos/{owner}/{repo}/releases/tags/$Tag"', publish_script)
        self.assertIn('"repos/{owner}/{repo}/releases/$ReleaseId"', publish_script)
        self.assertNotIn("gh release view", publish_script)
        self.assertIn('Get-RequiredProperty -InputObject $remoteEntries[0] -Name "digest"', publish_script)
        self.assertIn("Assert-RemoteTagTarget -Tag $tag -Target $target", publish_script)
        self.assertIn('"--verify-tag"', publish_script)
        self.assertIn("Release manifest must contain exactly the four publishable artifact names.", publish_script)
        self.assertIn("GitHub Release must contain exactly the four manifest-bound assets after upload.", publish_script)
        self.assertIn('"--draft"', publish_script)
        self.assertIn("Refusing remote asset mutation", publish_script)
        self.assertIn("-ExpectedDraft $true", publish_script)
        self.assertIn("$draftReleaseId = [long]$draftReleaseByTag.id", publish_script)
        self.assertNotIn("--method PATCH", publish_script)
        self.assertIn("Get-GitHubReleaseSnapshot -ReleaseId $draftReleaseId", publish_script)
        self.assertIn("-ExpectedReleaseId $draftReleaseId", publish_script)
        self.assertIn("-ExpectedName $releaseTitle", publish_script)
        self.assertIn("-ExpectedBody $releaseNotes", publish_script)
        self.assertIn("$remoteTag -cne $Tag", publish_script)
        self.assertIn("$remoteName -cne $ExpectedName", publish_script)
        self.assertIn("$remoteBody -cne $ExpectedBody", publish_script)
        self.assertIn("Sort-Object -Unique -CaseSensitive", publish_script)
        self.assertIn(
            "Compare-Object -ReferenceObject $RequiredArtifactNames -DifferenceObject $remoteAssetNames -CaseSensitive",
            publish_script,
        )
        self.assertIn("[string]$_.name -ceq $artifactName", publish_script)
        self.assertIn("[long]$draftTagRelease.id -ne $draftReleaseId", publish_script)
        self.assertNotIn("gh release edit", publish_script)
        self.assertNotIn("-ExpectedDraft $false", publish_script)
        self.assertLess(
            publish_script.index("Copy-Item -LiteralPath $sourceArtifact -Destination $stagedArtifact"),
            publish_script.index("Get-StreamSha256 -Stream $guardStream"),
        )
        self.assertLess(
            publish_script.index("Get-StreamSha256 -Stream $guardStream"),
            publish_script.index("& gh @createArgs"),
        )
        self.assertLess(
            publish_script.index("Get-GitHubReleaseSnapshot -ReleaseId $draftReleaseId"),
            publish_script.index("-ExpectedDraft $true"),
        )
        self.assertLess(
            publish_script.index("$guardStream.Dispose()"),
            publish_script.index("Remove-Item -LiteralPath $stagingRoot -Recurse -Force"),
        )

    def test_cleanup_user_data_root_removes_appdata_and_known_project_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "VRCForge" / "agentic-app"
            project = Path(temp_dir) / "AvatarProject"
            project_transcript = project / ".vrcforge" / "chat-transcripts.json"
            project_transcript.parent.mkdir(parents=True)
            project_transcript.write_text("{}", encoding="utf-8")
            root.mkdir(parents=True)
            (root / "chat-projects.json").write_text(json.dumps({"projectPaths": [str(project)]}), encoding="utf-8")

            payload = dashboard_server.cleanup_user_data_root(root)

            self.assertTrue(payload["ok"])
            self.assertTrue(payload["rootRemoved"])
            self.assertEqual(payload["projectTranscriptCount"], 1)
            self.assertFalse(root.exists())
            self.assertFalse(project_transcript.exists())

    def test_cleanup_user_data_root_rejects_unrelated_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RuntimeError, "outside the VRCForge agentic-app"):
                dashboard_server.cleanup_user_data_root(Path(temp_dir) / "not-vrcforge")

    def test_project_install_request_accepts_camel_case_project_path(self) -> None:
        request = dashboard_server.ProjectInstallRequest(projectPath="C:/AvatarProject")
        self.assertEqual(request.project_path, "C:/AvatarProject")

    def test_core_only_launcher_bootstrap_keeps_backend_runtime_separate(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        uv_root = repo_root / "third_party" / "uv-runtime"
        manifest_text = (repo_root / "packaging" / "THIRD_PARTY_LICENSES.json").read_text(encoding="utf-8")
        general_gate = (repo_root / "packaging" / "check_third_party_licenses.ps1").read_text(encoding="utf-8")
        runtime_manager = (repo_root / "launcher" / "VRCForge.Launcher" / "RuntimeDependencyManager.cs").read_text(encoding="utf-8")
        backend_process = (repo_root / "launcher" / "VRCForge.Launcher" / "BackendProcess.cs").read_text(encoding="utf-8")
        main_form = (repo_root / "launcher" / "VRCForge.Launcher" / "MainForm.cs").read_text(encoding="utf-8")
        start_cmd = (repo_root / "start_dashboard.cmd").read_text(encoding="utf-8")

        self.assertIn("MIT License", (uv_root / "LICENSE-MIT").read_text(encoding="utf-8"))
        self.assertIn("Apache License", (uv_root / "LICENSE-APACHE").read_text(encoding="utf-8"))
        self.assertIn("uv Windows runtime", manifest_text)
        self.assertNotIn("CoplayDev", manifest_text)
        self.assertIn("requiredLicenseFiles", manifest_text)
        self.assertIn("Assert-LicenseFile", general_gate)
        self.assertIn("VRCForge MCP2 Core is bundled", runtime_manager)
        self.assertNotIn("mcpforunityserver", runtime_manager)
        self.assertNotIn("uvx", runtime_manager.lower())
        self.assertIn("UV_PYTHON_INSTALL_DIR", backend_process)
        self.assertIn("StartViaCmdFallback", backend_process)
        self.assertIn("Dashboard HTTP page is reachable", backend_process)
        self.assertIn("start_dashboard.cmd fallback", main_form)
        self.assertIn("启动 Dashboard", main_form)
        self.assertNotIn("安装 / 更新 Unity 插件", main_form)
        self.assertNotIn("外部 Agent 接入 / 打开 Dashboard", main_form)
        self.assertIn("backend\\vrcforge_backend.exe", start_cmd)
        self.assertIn("VRCFORGE_DASHBOARD_DIR", start_cmd)
        self.assertNotIn("start-dashboard.ps1", start_cmd)
        self.assertNotIn("powershell", start_cmd.lower())

    def test_unity_project_install_uses_project_backups_and_bundled_mcp_core(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            (project / "Assets" / "VRCAutoRig").mkdir(parents=True)
            (project / "Packages").mkdir(parents=True)
            (project / "ProjectSettings").mkdir(parents=True)
            (project / "Packages" / "manifest.json").write_text('{"dependencies":{}}\n', encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")

            payload = dashboard_server.install_vrcforge_into_unity_project(project)

            self.assertIn(".vrcforge", payload["backupRoot"])
            self.assertTrue((project / "Assets" / "VRCForge").is_dir())
            self.assertFalse((project / "Assets" / "VRCAutoRig").exists())
            self.assertIn("legacy", payload["backups"])
            manifest = json.loads((project / "Packages" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest, {"dependencies": {}})
            self.assertFalse((project / "Packages" / "com.coplaydev.unity-mcp").exists())
            self.assertTrue(payload["mcpCoreBundled"])
            self.assertTrue(payload["configuredMcp"])
            self.assertFalse(payload["installedMcp"])
            self.assertNotIn("manifest", payload["backups"])

    def test_unity_project_install_rolls_back_when_manifest_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "AvatarProject"
            legacy = project / "Assets" / "VRCAutoRig"
            legacy.mkdir(parents=True)
            (legacy / "legacy.txt").write_text("before", encoding="utf-8")
            (project / "Packages").mkdir(parents=True)
            (project / "ProjectSettings").mkdir(parents=True)
            (project / "Packages" / "manifest.json").write_text("[]", encoding="utf-8")
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "manifest root is not an object"):
                dashboard_server.install_vrcforge_into_unity_project(project)

            self.assertTrue((project / "Assets" / "VRCAutoRig" / "legacy.txt").is_file())
            self.assertFalse((project / "Assets" / "VRCForge").exists())
            self.assertFalse((project / "Packages" / "com.coplaydev.unity-mcp").exists())
            self.assertEqual((project / "Packages" / "manifest.json").read_text(encoding="utf-8"), "[]")

    def test_recent_log_snapshot_keeps_only_last_five_days(self) -> None:
        dashboard_server.RECENT_LOGS.clear()
        old_entry = {
            "timestamp": (datetime.now(timezone.utc) - timedelta(days=6)).isoformat(),
            "level": "info",
            "scope": "test",
            "message": "old",
            "data": {},
        }
        fresh_entry = {
            "timestamp": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "level": "info",
            "scope": "test",
            "message": "fresh",
            "data": {},
        }
        dashboard_server.RECENT_LOGS.extend([old_entry, fresh_entry])

        self.assertEqual(dashboard_server.recent_log_snapshot(), [fresh_entry])

    def test_unified_log_cleanup_removes_stale_time_named_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            privacy = dashboard_server.DiagnosticPrivacy(temp_path / "config")
            manager = dashboard_server.DiagnosticLogManager(
                temp_path / "logs",
                temp_path / "config" / "diagnostics.json",
                privacy,
            )
            logs = temp_path / "logs"
            logs.mkdir(parents=True)
            stale = logs / "vrcforge_2026-01-01_00-00-00_0.log"
            stale.write_text("old\n", encoding="utf-8")
            old_time = (datetime.now(timezone.utc) - timedelta(days=6)).timestamp()
            os.utime(stale, (old_time, old_time))
            manager.cleanup()
            self.assertFalse(stale.exists())

    def test_unified_log_cleanup_removes_known_legacy_raw_logs_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            privacy = dashboard_server.DiagnosticPrivacy(temp_path / "config")
            manager = dashboard_server.DiagnosticLogManager(
                temp_path / "logs",
                temp_path / "config" / "diagnostics.json",
                privacy,
            )
            logs = temp_path / "logs"
            logs.mkdir(parents=True)
            legacy = [logs / name for name in ("dashboard.log", "backend_stdout.log", "backend_stderr.log", "interactions.jsonl")]
            durable = logs / "agent-goals.jsonl"
            for path in legacy:
                path.write_text("legacy raw", encoding="utf-8")
            durable.write_text("durable", encoding="utf-8")
            manager.cleanup()
            self.assertTrue(all(not path.exists() for path in legacy))
            self.assertTrue(durable.exists())

    def test_reference_image_context_supports_optional_source_and_target_images(self) -> None:
        data_url = "data:image/png;base64,aW1hZ2U="
        with tempfile.TemporaryDirectory() as temp_dir:
            original_artifacts_dir = dashboard_server.ARTIFACTS_DIR
            original_dashboard_artifacts_dir = dashboard_server.DASHBOARD_ARTIFACTS_DIR
            temp_artifacts = Path(temp_dir) / "artifacts"
            dashboard_server.ARTIFACTS_DIR = temp_artifacts
            dashboard_server.DASHBOARD_ARTIFACTS_DIR = temp_artifacts / "dashboard"
            try:
                request = dashboard_server.DashboardRequest(
                    instruction="match target",
                    source_reference_image_data_urls=[data_url],
                    target_reference_image_data_urls=[data_url, data_url],
                )

                context = dashboard_server.build_reference_image_context(request)

                self.assertIsNotNone(context)
                self.assertEqual(context["count"], 3)
                self.assertEqual(len(context["imagePaths"]), 3)
                self.assertEqual([group["role"] for group in context["groups"]], ["source", "target"])
                self.assertEqual(len(context["groups"][0]["images"]), 1)
                self.assertEqual(len(context["groups"][1]["images"]), 2)
                self.assertIn("原图", context["imageLabels"][0])
                self.assertIn("目标参考图", context["imageLabels"][1])
            finally:
                dashboard_server.ARTIFACTS_DIR = original_artifacts_dir
                dashboard_server.DASHBOARD_ARTIFACTS_DIR = original_dashboard_artifacts_dir

    def test_extract_tool_result_payload_does_not_parse_legacy_stdout(self) -> None:
        result = dashboard_server.McpResult(
            exit_code=0,
            stdout=(
                "objectPath: Avatar/Hood\n"
                "active: False\n"
                "createdCount: 1\n"
                "skipped: [0 items]\n"
                "assetDir: Assets/VRCForge/Generated/FX\n"
                "✅ Executed custom tool: vrc_toggle_scene_object"
            ),
            stderr="",
            payload=[0],
        )

        payload = dashboard_server.extract_tool_result_payload(result)

        self.assertEqual(payload, [0])

    def test_extract_tool_result_payload_unwraps_mcp_2026_structured_content(self) -> None:
        result = dashboard_server.McpResult(
            exit_code=0,
            stdout="",
            stderr="",
            payload={
                "resultType": "complete",
                "structuredContent": {
                    "success": True,
                    "data": {
                        "gameObjectPath": "Main Camera/Child",
                        "sceneSaved": True,
                        "persistedReadback": True,
                    },
                },
            },
        )

        payload = dashboard_server.extract_tool_result_payload(result)

        self.assertEqual(
            payload,
            {
                "gameObjectPath": "Main Camera/Child",
                "sceneSaved": True,
                "persistedReadback": True,
            },
        )

    def test_checkpoint_result_preserves_nonrecoverable_unity_rejection(self) -> None:
        result = dashboard_server.McpResult(
            exit_code=1,
            stdout="",
            stderr="",
            payload={
                "isError": True,
                "structuredContent": {
                    "success": False,
                    "code": "unsaved_open_scene",
                    "error": "unsaved_open_scene",
                    "data": {
                        "message": "Save every open scene before an App-approved write.",
                        "blocking": True,
                        "recoverable": False,
                    },
                },
            },
        )

        normalized = dashboard_server.normalize_unity_checkpoint_result(
            result,
            Path("C:/UnityProject"),
        )

        self.assertFalse(normalized["ok"])
        self.assertTrue(normalized["blocking"])
        self.assertFalse(normalized["recoverable"])
        self.assertEqual(normalized["code"], "unsaved_open_scene")
        self.assertIn("Save every open scene", normalized["error"])

    def test_provider_config_default_path_is_outside_source_root(self) -> None:
        if os.environ.get("VRCFORGE_CONFIG_PATH") or os.environ.get("VRCFORGE_CONFIG_DIR"):
            self.skipTest("config location is explicitly overridden")

        source_root_config = (dashboard_server.ROOT_DIR / "config.json").resolve()

        self.assertNotEqual(dashboard_server.CONFIG_PATH.resolve(), source_root_config)
        self.assertNotEqual(dashboard_server.CONFIG_PATH.resolve().parent, dashboard_server.ROOT_DIR.resolve())
        self.assertEqual(dashboard_server.CONFIG_PATH.resolve().parent, dashboard_server.CONFIG_DIR.resolve())

    def test_api_config_endpoint_persists_and_returns_effective_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            owner = isolated_provider_configuration_service(config_path)
            config = owner.resolve_api_request(
                dashboard_server.ApiConfigRequest(
                    provider="anthropic",
                    api_key="anthropic-secret",
                    base_url="https://ignored.example.com",
                    model="claude-opus-4-6",
                )
            )
            owner.save_api_config(config)

            payload = {
                "apiConfig": owner.serialize_api_config(include_secret=True),
                "effective": owner.build_effective_model_summary(),
            }
            self.assertEqual(payload["apiConfig"]["provider"], "anthropic")
            self.assertEqual(payload["apiConfig"]["base_url"], "")
            self.assertEqual(payload["effective"]["model"], "claude-opus-4-6")
            self.assertTrue(config_path.exists())
            saved_payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_payload["api"]["provider"], "anthropic")
            self.assertEqual(saved_payload["api"]["base_url"], "")

    def test_provider_config_save_preserves_invalid_source_in_verified_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid = b'{"api": invalid}\xff'
            config_path = Path(temp_dir) / "config.json"
            config_path.write_bytes(invalid)
            digest = hashlib.sha256(invalid).hexdigest()
            owner = isolated_provider_configuration_service(config_path)
            self.assertEqual(owner.load_config_document(), {})
            self.assertEqual(config_path.read_bytes(), invalid)

            owner.save_config_document()

            backup = config_path.with_name(f"config.json.backup-{digest}.bak")
            self.assertEqual(backup.read_bytes(), invalid)
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["api"]["provider"], "ollama")

    def test_provider_config_save_rejects_corrupt_existing_backup_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            original = b'{"api":{"provider":"ollama"}}'
            config_path = Path(temp_dir) / "config.json"
            config_path.write_bytes(original)
            digest = hashlib.sha256(original).hexdigest()
            backup = config_path.with_name(f"config.json.backup-{digest}.bak")
            backup.write_bytes(b"wrong-backup-bytes")
            owner = isolated_provider_configuration_service(config_path)
            with self.assertRaises(OSError):
                owner.save_config_document(
                    api_config=ProviderApiConfig(
                        provider="openai", api_key="", base_url="", model="gpt-5"
                    )
                )
            self.assertEqual(config_path.read_bytes(), original)
            self.assertEqual(backup.read_bytes(), b"wrong-backup-bytes")

    def test_provider_config_save_rejects_symlink_backup_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            original = b'{"api":{"provider":"ollama"}}'
            config_path = Path(temp_dir) / "config.json"
            config_path.write_bytes(original)
            digest = hashlib.sha256(original).hexdigest()
            backup = config_path.with_name(f"config.json.backup-{digest}.bak")
            try:
                backup.symlink_to(config_path)
            except OSError as exc:
                self.skipTest(f"File symlinks are unavailable: {exc}")
            owner = isolated_provider_configuration_service(config_path)
            with self.assertRaises(OSError):
                owner.save_config_document(
                    api_config=ProviderApiConfig(
                        provider="openai", api_key="", base_url="", model="gpt-5"
                    )
                )
            self.assertEqual(config_path.read_bytes(), original)
            self.assertTrue(backup.is_symlink())

    def test_provider_catalog_reads_models_from_draft_config_with_local_fake(self) -> None:
        catalog = isolated_provider_model_catalog_service([
            {"id": "gemini-2.5-flash", "label": "gemini-2.5-flash"},
            {"id": "gemini-2.5-pro", "label": "gemini-2.5-pro"},
        ])
        with tempfile.TemporaryDirectory() as temp_dir:
            configuration = isolated_provider_configuration_service(
                Path(temp_dir) / "config.json"
            )
            config = configuration.resolve_api_request(
                dashboard_server.ApiConfigRequest(
                    provider="gemini",
                    api_key="draft-secret",
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                    model="gemini-2.5-pro",
                )
            )
            models = catalog.fetch_provider_models(config)

        self.assertEqual(len(models), 2)
        self.assertEqual(models[1]["id"], "gemini-2.5-pro")
        self.assertEqual(config.api_key, "draft-secret")
        self.assertEqual(config.model, "gemini-2.5-pro")

    def test_provider_catalog_requires_api_key_before_local_fake_sdk(self) -> None:
        catalog = isolated_provider_model_catalog_service([])
        with tempfile.TemporaryDirectory() as temp_dir:
            configuration = isolated_provider_configuration_service(
                Path(temp_dir) / "config.json"
            )
            config = configuration.resolve_api_request(
                dashboard_server.ApiConfigRequest(
                    provider="openai",
                    api_key="",
                    base_url="https://api.openai.com/v1",
                    model="gpt-4.1-mini",
                )
            )
            with self.assertRaisesRegex(RuntimeError, "API key is empty"):
                catalog.fetch_provider_models(config)

    def test_provider_catalog_allows_ollama_without_api_key_with_local_fake(self) -> None:
        catalog = isolated_provider_model_catalog_service(
            [{"id": "llama3.2", "label": "llama3.2"}]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            configuration = isolated_provider_configuration_service(
                Path(temp_dir) / "config.json"
            )
            config = configuration.resolve_api_request(
                dashboard_server.ApiConfigRequest(
                    provider="ollama",
                    api_key="",
                    base_url="http://127.0.0.1:11434/v1",
                    model="llama3.2",
                )
            )
            models = catalog.fetch_provider_models(config)

        self.assertEqual(config.provider, "ollama")
        self.assertEqual(models[0]["id"], "llama3.2")

    @patch("dashboard_server.export_blendshapes")
    @patch("dashboard_server.load_dashboard_settings")
    def test_scene_avatar_scan_endpoint_returns_vrchat_avatars_from_export(
        self,
        mock_load_settings,
        mock_export_blendshapes,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace(
            unity_mcp_timeout_seconds=30,
            unity_mcp_host="127.0.0.1",
            unity_mcp_port=8080,
            unity_mcp_instance="",
        )
        mock_export_blendshapes.return_value = {
            "summary": {"avatarCount": 2, "rendererCount": 2, "blendshapeCount": 3},
            "avatars": [
                {
                    "avatarName": "HeroAvatar",
                    "avatarPath": "Scene/HeroAvatar",
                    "sceneName": "AvatarScene",
                    "isVrChatAvatar": True,
                    "renderers": [{"blendshapes": [{"name": "Smile"}, {"name": "Blink"}]}],
                },
                {
                    "avatarName": "PreviewProxy",
                    "avatarPath": "PreviewProxy",
                    "sceneName": "___NDMF Preview___",
                    "isVrChatAvatar": False,
                    "renderers": [{"blendshapes": [{"name": "Proxy"}]}],
                },
            ],
        }

        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/scene/avatars", json={"unity_host": "127.0.0.1", "unity_port": 8080})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["avatarCount"], 1)
        self.assertEqual(payload["avatars"][0]["avatarName"], "HeroAvatar")
        self.assertEqual(payload["avatars"][0]["blendshapeCount"], 2)

    @patch("dashboard_server.load_dashboard_export_payload")
    @patch("dashboard_server.load_dashboard_settings")
    @patch("dashboard_server.invoke_unity_mcp")
    def test_manual_blendshape_apply_uses_direct_unity_tool(
        self,
        mock_invoke_unity_mcp,
        mock_load_settings,
        mock_load_dashboard_export_payload,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_load_dashboard_export_payload.return_value = (
            {
                "avatars": [
                    {
                        "avatarName": "HeroAvatar",
                        "avatarPath": "Scene/HeroAvatar",
                        "sceneName": "AvatarScene",
                        "renderers": [
                            {
                                "rendererPath": "Scene/HeroAvatar/Face",
                                "blendshapes": [{"name": "Smile", "currentWeight": 10.0}],
                            }
                        ],
                    }
                ]
            },
            "test-export",
            False,
        )
        mock_invoke_unity_mcp.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"ok": True, "appliedCount": 1},
        )

        with patch.object(
            dashboard_server.AGENT_GATEWAY,
            "create_apply_request",
            return_value={"ok": True, "approval": {"id": "approval_blendshape", "targetTool": "vrcforge_apply_blendshapes"}},
        ) as create_request:
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/blendshapes/apply",
                    json={
                        "source_mode": "unity_live_export",
                        "mock_execute": False,
                        "avatar": "Scene/HeroAvatar",
                        "adjustments": [
                            {
                                "renderer_path": "Scene/HeroAvatar/Face",
                                "blendshape_name": "Smile",
                                "target_weight": 42.0,
                            }
                        ],
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending_approval")
        self.assertEqual(response.json()["approvalId"], "approval_blendshape")
        mock_invoke_unity_mcp.assert_not_called()
        self.assertEqual(create_request.call_args.args[0]["target_tool"], "vrcforge_apply_blendshapes")
        self.assertEqual(create_request.call_args.args[0]["arguments"]["adjustments"][0]["target_weight"], 42.0)

    @patch("dashboard_server.verify_live_blendshape_changes")
    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    @patch("dashboard_server.load_dashboard_export_payload")
    @patch("dashboard_server.create_blendshape_plan")
    def test_pipeline_run_live_queues_approval_without_direct_apply(
        self,
        mock_create_blendshape_plan,
        mock_load_dashboard_export_payload,
        mock_load_settings,
        mock_invoke_unity_mcp,
        mock_verify_live_blendshape_changes,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace(
            llm_provider="gemini",
            llm_model="gemini-test",
            min_confidence=0.65,
        )
        mock_load_dashboard_export_payload.return_value = (
            {
                "avatars": [
                    {
                        "avatarName": "HeroAvatar",
                        "avatarPath": "Scene/HeroAvatar",
                        "sceneName": "AvatarScene",
                        "renderers": [
                            {
                                "rendererPath": "Scene/HeroAvatar/Face",
                                "blendshapes": [{"name": "Smile", "currentWeight": 10.0}],
                            }
                        ],
                    }
                ]
            },
            "test-export",
            False,
        )
        mock_create_blendshape_plan.return_value = BlendshapePlan(
            summary="Make the face rounder.",
            adjustments=[
                BlendshapeAdjustment(
                    avatar_path="Scene/HeroAvatar",
                    renderer_path="Scene/HeroAvatar/Face",
                    blendshape_name="Smile",
                    target_weight=55.0,
                    reason="Smile softens the face.",
                    confidence=0.9,
                )
            ],
        )
        mock_invoke_unity_mcp.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"ok": True, "appliedCount": 1},
        )
        mock_verify_live_blendshape_changes.return_value = [
            {
                "rendererPath": "Scene/HeroAvatar/Face",
                "blendshapeName": "Smile",
                "targetWeight": 55.0,
                "actualWeight": 55.0,
                "verified": True,
                "verificationStatus": "verified",
            }
        ]

        with patch.object(
            dashboard_server.AGENT_GATEWAY,
            "create_apply_request",
            return_value={"ok": True, "approval": {"id": "approval_pipeline", "targetTool": "vrcforge_run_face_tuning"}},
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/pipeline/run",
                    json={
                        "source_mode": "unity_live_export",
                        "mock_execute": False,
                        "avatar": "Scene/HeroAvatar",
                        "instruction": "把脸变得更圆润一些",
                        "allow_low_confidence": True,
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "pending_approval")
        self.assertEqual(payload["approvalId"], "approval_pipeline")
        mock_invoke_unity_mcp.assert_not_called()

    @patch("dashboard_server.load_dashboard_settings")
    @patch("dashboard_server.load_dashboard_export_payload")
    @patch("dashboard_server.create_blendshape_plan")
    def test_pipeline_plan_saves_history_and_excludes_locked_blendshapes(
        self,
        mock_create_blendshape_plan,
        mock_load_dashboard_export_payload,
        mock_load_settings,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace(
            llm_provider="gemini",
            llm_model="gemini-test",
            min_confidence=0.65,
        )
        mock_load_dashboard_export_payload.return_value = (
            {
                "avatars": [
                    {
                        "avatarName": "HeroAvatar",
                        "avatarPath": "Scene/HeroAvatar",
                        "sceneName": "AvatarScene",
                        "renderers": [
                            {
                                "rendererPath": "Scene/HeroAvatar/Face",
                                "blendshapes": [
                                    {"name": "Smile", "currentWeight": 10.0},
                                    {"name": "eye_morph_narrow", "currentWeight": 5.0},
                                ],
                            }
                        ],
                    }
                ]
            },
            "test-export",
            True,
        )
        dashboard_server.save_tuning_store(
            dashboard_server.TUNING_LOCKS_PATH,
            {
                "type": "blendshape_tuning_locks",
                "version": "0.1",
                "avatars": {
                    "Scene/HeroAvatar": [
                        {"rendererPath": "Scene/HeroAvatar/Face", "blendshapeName": "Smile"}
                    ]
                },
            },
        )

        def create_plan_side_effect(_settings, planning_payload, *_args, **_kwargs):
            blendshape_names = [
                blendshape["name"]
                for avatar in planning_payload["avatars"]
                for renderer in avatar["renderers"]
                for blendshape in renderer["blendshapes"]
            ]
            self.assertEqual(blendshape_names, ["eye_morph_narrow"])
            return BlendshapePlan(
                summary="Reroll unlocked eye shape.",
                adjustments=[
                    BlendshapeAdjustment(
                        avatar_path="Scene/HeroAvatar",
                        renderer_path="Scene/HeroAvatar/Face",
                        blendshape_name="eye_morph_narrow",
                        target_weight=30.0,
                        reason="Only unlocked Blendshape is available.",
                        confidence=0.95,
                    )
                ],
            )

        mock_create_blendshape_plan.side_effect = create_plan_side_effect

        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/pipeline/plan",
                json={
                    "source_mode": "unity_live_export",
                    "mock_execute": True,
                    "avatar": "Scene/HeroAvatar",
                    "instruction": "保留嘴巴，只重抽眼睛",
                    "allow_low_confidence": True,
                    "save_artifacts": False,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["changePreview"][0]["blendshapeName"], "eye_morph_narrow")
        self.assertEqual(payload["lockedBlendshapes"][0]["blendshapeName"], "Smile")
        self.assertFalse(payload["historyRecord"]["applied"])
        self.assertEqual(payload["historyRecord"]["changes"][0]["blendshape"], "eye_morph_narrow")

        history = dashboard_server.AVATAR_TUNING_STORES.load_history()
        self.assertEqual(len(history["records"]), 1)
        self.assertEqual(history["records"][0]["locked_blendshapes"][0]["blendshapeName"], "Smile")

    @patch("dashboard_server.load_dashboard_settings")
    @patch("dashboard_server.load_dashboard_export_payload")
    def test_preset_apply_uses_saved_after_values_without_delta_stacking(
        self,
        mock_load_dashboard_export_payload,
        mock_load_settings,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_load_dashboard_export_payload.return_value = (
            {
                "avatars": [
                    {
                        "avatarName": "HeroAvatar",
                        "avatarPath": "Scene/HeroAvatar",
                        "sceneName": "AvatarScene",
                        "renderers": [
                            {
                                "rendererPath": "Scene/HeroAvatar/Face",
                                "blendshapes": [{"name": "Smile", "currentWeight": 30.0}],
                            }
                        ],
                    }
                ]
            },
            "test-export",
            True,
        )
        dashboard_server.save_tuning_store(
            dashboard_server.TUNING_HISTORY_PATH,
            {
                "type": "blendshape_tuning_history",
                "version": "0.1",
                "records": [
                    {
                        "id": "hist_test",
                        "created_at": "2026-05-16T00:00:00+00:00",
                        "avatar_name": "HeroAvatar",
                        "avatar_path": "Scene/HeroAvatar",
                        "user_prompt": "make a soft smile",
                        "provider": "Gemini",
                        "model": "gemini-test",
                        "reference_image_count": 1,
                        "applied": False,
                        "changes": [
                            {
                                "renderer_path": "Scene/HeroAvatar/Face",
                                "blendshape": "Smile",
                                "before": 10.0,
                                "after": 55.0,
                                "delta": 45.0,
                                "confidence": 0.95,
                            }
                        ],
                        "locked_blendshapes": [],
                    }
                ],
            },
        )

        with TestClient(dashboard_server.app) as client:
            create_response = client.post(
                "/api/tuning/presets",
                json={"history_id": "hist_test", "name": "soft_smile_face", "tags": ["mouth"]},
            )
            self.assertEqual(create_response.status_code, 200)
            preset_id = create_response.json()["preset"]["id"]
            apply_response = client.post(
                f"/api/tuning/presets/{preset_id}/apply",
                json={
                    "source_mode": "unity_live_export",
                    "mock_execute": True,
                    "avatar": "Scene/HeroAvatar",
                },
            )

        self.assertEqual(apply_response.status_code, 200)
        payload = apply_response.json()
        self.assertEqual(payload["appliedAdjustments"][0]["targetWeight"], 55.0)
        self.assertEqual(payload["changePreview"][0]["previousWeight"], 30.0)
        self.assertEqual(payload["changePreview"][0]["delta"], 25.0)
        self.assertEqual(payload["undoDepth"], 1)

    def test_avatar_tuning_local_store_routes_preserve_exact_error_mapping(self) -> None:
        prepare_error = (
            dashboard_server.AVATAR_TUNING_PREPARED._ports.make_prepare_error(
                "cannot approve",
                409,
            )
        )
        self.assertIsInstance(prepare_error, AgentGatewayError)
        self.assertEqual(prepare_error.status_code, 409)

        broad_400_cases = (
            (
                "post",
                "/api/tuning/presets",
                {"history_id": "missing", "name": "preset"},
            ),
            (
                "post",
                "/api/tuning/presets/missing/rename",
                {"name": "renamed"},
            ),
            (
                "post",
                "/api/tuning/presets/missing/duplicate",
                {},
            ),
        )
        validation_400_cases = (
            (
                "post",
                "/api/tuning/presets/missing/delete",
                None,
            ),
            (
                "post",
                "/api/tuning/locks",
                {"locked_blendshapes": []},
            ),
        )
        with TestClient(
            dashboard_server.app,
            raise_server_exceptions=False,
        ) as client:
            for http_method, path, payload in broad_400_cases + validation_400_cases:
                with self.subTest(path=path):
                    response = getattr(client, http_method)(path, json=payload)
                    self.assertEqual(response.status_code, 400)

            dashboard_server.TUNING_PRESETS_PATH.write_text(
                "not-json",
                encoding="utf-8",
            )
            corrupt_preset_response = client.post(
                "/api/tuning/presets/missing/delete"
            )
            self.assertEqual(corrupt_preset_response.status_code, 500)

            dashboard_server.TUNING_LOCKS_PATH.write_text(
                "not-json",
                encoding="utf-8",
            )
            corrupt_locks_response = client.post(
                "/api/tuning/locks",
                json={"avatar_path": "Avatar/Current", "locked_blendshapes": []},
            )
            self.assertEqual(corrupt_locks_response.status_code, 500)

    @patch("dashboard_server.load_dashboard_settings")
    @patch("dashboard_server.load_dashboard_export_payload")
    def test_preset_apply_skips_locked_and_missing_blendshapes_without_crash(
        self,
        mock_load_dashboard_export_payload,
        mock_load_settings,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_load_dashboard_export_payload.return_value = (
            {
                "avatars": [
                    {
                        "avatarName": "HeroAvatar",
                        "avatarPath": "Scene/HeroAvatar",
                        "sceneName": "AvatarScene",
                        "renderers": [
                            {
                                "rendererPath": "Scene/HeroAvatar/Face",
                                "blendshapes": [{"name": "Smile", "currentWeight": 30.0}],
                            }
                        ],
                    }
                ]
            },
            "test-export",
            True,
        )
        dashboard_server.save_tuning_store(
            dashboard_server.TUNING_PRESETS_PATH,
            {
                "type": "blendshape_tuning_presets",
                "version": "0.1",
                "presets": [
                    {
                        "id": "preset_test",
                        "name": "mixed_targets",
                        "avatar_name": "HeroAvatar",
                        "avatar_path": "Scene/HeroAvatar",
                        "apply_mode": "after_values",
                        "changes": [
                            {
                                "renderer_path": "Scene/HeroAvatar/Face",
                                "blendshape": "Smile",
                                "after": 55.0,
                            },
                            {
                                "renderer_path": "Scene/HeroAvatar/Face",
                                "blendshape": "Missing",
                                "after": 80.0,
                            },
                        ],
                    }
                ],
            },
        )
        dashboard_server.save_tuning_store(
            dashboard_server.TUNING_LOCKS_PATH,
            {
                "type": "blendshape_tuning_locks",
                "version": "0.1",
                "avatars": {
                    "Scene/HeroAvatar": [
                        {"rendererPath": "Scene/HeroAvatar/Face", "blendshapeName": "Smile"}
                    ]
                },
            },
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/tuning/presets/preset_test/apply",
                json={
                    "source_mode": "unity_live_export",
                    "mock_execute": True,
                    "avatar": "Scene/HeroAvatar",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["appliedAdjustments"], [])
        self.assertEqual({item["reason"] for item in payload["skippedAdjustments"]}, {"locked", "missing_blendshape"})
        self.assertEqual(payload["undoDepth"], 0)

    def test_preset_limit_trims_latest_presets_per_avatar(self) -> None:
        presets = [
            {"id": "a_old", "avatar_path": "AvatarA"},
            {"id": "a_mid", "avatar_path": "AvatarA"},
            {"id": "b_keep", "avatar_path": "AvatarB"},
            {"id": "a_new", "avatar_path": "AvatarA"},
        ]

        trimmed = dashboard_server.trim_presets_for_avatar(presets, 2)

        self.assertEqual([item["id"] for item in trimmed], ["a_mid", "a_new", "b_keep"])

    def test_ai_lock_selection_only_accepts_candidate_pairs(self) -> None:
        candidates = [
            {"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"},
            {"rendererPath": "Avatar/Face", "blendshapeName": "MouthSmile"},
        ]

        selected = dashboard_server.validate_ai_lock_selection(
            {
                "selected": [
                    {"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"},
                    {"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"},
                    {"rendererPath": "Avatar/Face", "blendshapeName": "Hair_Fluffy"},
                ]
            },
            candidates,
        )

        self.assertEqual(selected, [{"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"}])

    @patch("dashboard_server.request_llm_plan")
    @patch("dashboard_server.load_dashboard_settings")
    def test_ai_lock_selection_endpoint_returns_model_selected_blendshapes(
        self,
        mock_load_settings,
        mock_request_llm_plan,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace(llm_provider="ollama", llm_api_key="")
        mock_request_llm_plan.return_value = json.dumps(
            {
                "summary": "eye area",
                "selected": [
                    {"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"},
                    {"rendererPath": "Avatar/Face", "blendshapeName": "MouthSmile"},
                    {"rendererPath": "Avatar/Face", "blendshapeName": "NotACandidate"},
                ],
            }
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/tuning/locks/ai-select",
                json={
                    "source_mode": "mvp_sample",
                    "avatar_path": "Avatar",
                    "action": "unlock",
                    "selection_instruction": "解锁眼睛相关形态键",
                    "candidate_blendshapes": [
                        {"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"},
                        {"rendererPath": "Avatar/Face", "blendshapeName": "MouthSmile"},
                    ],
                    "current_locked_blendshapes": [
                        {"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"}
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selectedBlendshapes"], [{"rendererPath": "Avatar/Face", "blendshapeName": "EyeSmile_L"}])
        self.assertIn("解锁眼睛", mock_request_llm_plan.call_args.args[1])

    def test_shader_plan_validation_rejects_arbitrary_shader_property_names(self) -> None:
        validation = dashboard_server.validate_shader_material_tuning_plan(
            plan={
                "type": "material_tuning_plan",
                "version": "0.2",
                "changes": [
                    {
                        "material_id": "mat_skin",
                        "shader_property": "_Color",
                        "semantic_property": "_Color",
                        "after": "#FFFFFF",
                    }
                ],
            },
            inventory=make_shader_inventory(),
        )

        self.assertEqual(validation["validatedChanges"], [])
        self.assertEqual(validation["skippedChanges"][0]["validation_status"], "skipped")
        self.assertIn("Real shader property names", validation["skippedChanges"][0]["warning"])

    def test_shader_plan_validation_clamps_and_skips_unsupported_targets(self) -> None:
        validation = dashboard_server.validate_shader_material_tuning_plan(
            plan={
                "type": "material_tuning_plan",
                "version": "0.2",
                "changes": [
                    {"material_id": "mat_skin", "semantic_property": "outline_width", "after": 9.0},
                    {"material_id": "mat_unsupported", "semantic_property": "smoothness", "after": 0.5},
                    {"material_id": "missing", "semantic_property": "smoothness", "after": 0.5},
                ],
            },
            inventory=make_shader_inventory(),
        )

        self.assertEqual(validation["validatedChanges"][0]["after"], 0.25)
        self.assertEqual(len(validation["skippedChanges"]), 2)
        self.assertTrue(any("Unsupported shader family" in item["warning"] for item in validation["skippedChanges"]))
        self.assertTrue(any("Unknown material_id" in item["warning"] for item in validation["skippedChanges"]))

    def test_shader_plan_validation_allows_generic_semantic_fallback(self) -> None:
        inventory = make_shader_inventory()
        inventory["materials"].append(
            {
                "material_id": "mat_generic",
                "material_name": "Standardish",
                "shader_family": "Generic",
                "category": "clothes",
                "supported_properties": {
                    "base_color": {"type": "color", "value": "#FFFFFFFF", "writable": True},
                    "smoothness": {"type": "float", "value": 0.4, "writable": True},
                },
            }
        )

        validation = dashboard_server.validate_shader_material_tuning_plan(
            plan={
                "type": "material_tuning_plan",
                "version": "0.2",
                "changes": [
                    {"material_id": "mat_generic", "semantic_property": "smoothness", "after": 0.75},
                    {"material_id": "mat_generic", "shader_property": "_Color", "semantic_property": "_Color", "after": "#000000"},
                ],
            },
            inventory=inventory,
        )

        self.assertEqual(validation["validatedChanges"][0]["after"], 0.75)
        self.assertEqual(validation["skippedChanges"][0]["warning"], "Real shader property names are not accepted; use semantic_property only.")

    def test_duplicate_compatible_material_ids_fail_closed_without_breaking_unique_ids(self) -> None:
        inventory = make_shader_inventory()
        first = dict(inventory["materials"][0])
        first["renderer_component_id"] = "1" * 64
        duplicate = dict(first)
        duplicate["renderer_component_id"] = "2" * 64
        duplicate["category"] = "hair"
        inventory["materials"] = [first, duplicate, inventory["materials"][1]]

        self.assertIn("mat_unsupported", dashboard_server.build_shader_material_index(inventory))
        self.assertNotIn("mat_skin", dashboard_server.build_shader_material_index(inventory))
        validation = dashboard_server.validate_shader_material_tuning_plan(
            plan={
                "changes": [
                    {"material_id": "mat_skin", "semantic_property": "smoothness", "after": 0.5},
                ]
            },
            inventory=inventory,
        )
        self.assertIn("Ambiguous material_id", validation["skippedChanges"][0]["warning"])

        dashboard_server.apply_shader_category_overrides(inventory, {"mat_skin": "eyes"})
        self.assertEqual([item["category"] for item in inventory["materials"][:2]], ["skin", "hair"])

    def test_unity_shader_adapter_source_keeps_poiyomi_and_generic_fallback(self) -> None:
        source = Path("Assets/VRCForge/Editor/ShaderMaterialAdapters.cs").read_text(encoding="utf-8-sig")

        self.assertIn("new PoiyomiShaderAdapter()", source)
        self.assertIn("new GenericShaderAdapter()", source)
        self.assertIn('base("Generic"', source)
        self.assertIn('"_BaseColor"', source)

    def test_material_shader_tool_has_precondition_impact_and_exact_readback(self) -> None:
        source = Path("Assets/VRCForge/Editor/MaterialShaderTool.cs").read_text(encoding="utf-8-sig")

        self.assertIn('toolId: "vrc_set_material_shader"', source)
        self.assertIn('"vrcforge.material_shader_assignment.v1"', source)
        self.assertIn("ResolveShader(shaderName, shaderAssetPath)", source)
        self.assertIn("shaderAssetPath", source)
        self.assertIn("AssetDatabase.FindAssets", source)
        self.assertIn("AssetDatabase.LoadAssetAtPath<Shader>", source)
        self.assertIn("expectedBeforeShader", source)
        self.assertIn("expectedBeforeShaderAssetPath", source)
        self.assertIn("expectedBeforeShaderAssetGuid", source)
        self.assertIn("expectedMaterialAssetPath", source)
        self.assertIn("expectedMaterialAssetGuid", source)
        self.assertIn("expectedMaterialFileDigest", source)
        self.assertIn("expectedSharedImpactDigest", source)
        self.assertIn("expectedRendererComponentId", source)
        self.assertIn("rendererSceneGuid", source)
        self.assertIn("rendererComponentType", source)
        self.assertIn("rendererComponentIndex", source)
        self.assertIn("RendererComponentIdentity.Create", source)
        self.assertIn("expectedProjectPath", source)
        self.assertIn("MatchesCurrentProject", source)
        self.assertIn("rendererPath and materialAssetPath cannot be combined", source)
        self.assertIn("InspectWritableMaterialAsset", source)
        self.assertIn("AssetDatabase.IsMainAsset", source)
        self.assertIn('StartsWith("Assets/"', source)
        self.assertIn("FileAttributes.ReadOnly", source)
        self.assertIn("FileAttributes.ReparsePoint", source)
        self.assertIn("ComputeFileSha256", source)
        self.assertIn("BuildSharedMaterialImpact", source)
        self.assertIn("ComputeImpactPartitionDigest", source)
        self.assertIn("ComputeImpactCommitment", source)
        self.assertIn("sharedImpactDisplayDigest", source)
        self.assertIn("sharedImpactTailDigest", source)
        self.assertIn("vrcforge.material_shader_impact.v2", source)
        self.assertIn("scenePath", source)
        self.assertIn("sceneGuid", source)
        self.assertIn("sceneHandle", source)
        self.assertIn("loadedRendererSlotCount", source)
        self.assertIn("dependentAssetCount", source)
        self.assertIn("matchingRenderers.Length > 1", source)
        self.assertIn("Undo.RecordObject", source)
        self.assertIn("beforeShaderObject != shader", source)
        self.assertIn("target.material.shader = shader", source)
        self.assertIn("AssetDatabase.SaveAssetIfDirty", source)
        self.assertIn("EditorUtility.IsDirty", source)
        self.assertIn("readback.shader == shader", source)
        self.assertIn("rendererPath or materialAssetPath is required", source)
        self.assertNotIn("AssetDatabase.ImportAsset", source)
        self.assertNotIn("AssetDatabase.Refresh", source)
        self.assertNotIn("fixture", source.lower())

        scanner_source = Path("Assets/VRCForge/Editor/ShaderMaterialScanner.cs").read_text(encoding="utf-8-sig")
        self.assertIn("renderer_component_id", scanner_source)
        self.assertIn("renderer_scene_guid", scanner_source)
        self.assertIn("RendererComponentIdentity.Create", scanner_source)
        self.assertIn("new Dictionary<int, Transform>()", scanner_source)
        self.assertIn("var rootInstanceId = root.GetInstanceID()", scanner_source)
        self.assertIn("rendererIdentity.componentId", scanner_source)
        self.assertIn("MaterialInventoryIdentity.CreateMaterialId", scanner_source)
        self.assertIn("rendererComponentIds.Add", scanner_source)
        self.assertIn("material_id_ambiguous", scanner_source)
        self.assertIn("renderer_id_ambiguous", scanner_source)
        self.assertIn("ThenBy(root => root.gameObject.scene.handle)", scanner_source)
        self.assertIn("ThenBy(root => root.GetInstanceID())", scanner_source)
        self.assertIn("ReferenceEquals(FindAvatarRoot(renderer.transform), avatarRoot)", scanner_source)
        identity_source = Path("Assets/VRCForge/Editor/RendererComponentIdentity.cs").read_text(encoding="utf-8-sig")
        self.assertIn("GlobalObjectId.GetGlobalObjectIdSlow", identity_source)
        self.assertIn("globalObjectId.identifierType == 0", identity_source)
        self.assertIn('"instance:" + renderer.GetInstanceID()', identity_source)
        self.assertIn('"vrcforge.renderer_component.v1"', identity_source)
        self.assertIn("CreateRendererId(string rendererPath)", identity_source)
        self.assertIn("string rendererPath,", identity_source)
        tuning_source = Path("Assets/VRCForge/Editor/MaterialTuningApplier.cs").read_text(encoding="utf-8-sig")
        self.assertIn("RendererComponentIdentity.Create", tuning_source)
        self.assertIn("MaterialInventoryIdentity.CreateMaterialId", tuning_source)
        self.assertIn("componentSlots.Add", tuning_source)
        self.assertIn("index.ContainsKey(materialId)", tuning_source)

    def test_avatar_encryption_public_repo_keeps_only_connector_boundary(self) -> None:
        self.assertFalse(Path("Assets/VRCForge/Editor/AvatarEncryptionTool.cs").exists())
        self.assertFalse(Path("Assets/VRCForge/Runtime/AvatarEncryption/VRCForgeAvatarEncryptionRestore.shader").exists())
        source = Path("dashboard_server.py").read_text(encoding="utf-8-sig")
        self.assertIn("VRCFORGE_AVATAR_ENCRYPTION_ADDON_URL", source)
        self.assertIn("vrcforge_avatar_encryption_addon_apply", source)
        self.assertIn("vrcforge_avatar_encryption_addon_remove", source)
        self.assertNotIn("vrc_apply_avatar_encryption", source)
        self.assertNotIn("vrc_remove_avatar_encryption", source)

    def test_shader_adapter_smoke_script_uses_supervised_paths(self) -> None:
        source = Path("scripts/smoke_shader_adapter_apply_rollback.py").read_text(encoding="utf-8-sig")

        self.assertIn("vrcforge.shader_adapter_apply_rollback_smoke.v1", source)
        self.assertIn("/api/app/package-install/request", source)
        self.assertIn("vrcforge_unity_mcp_write", source)
        self.assertIn("vrc_set_material_shader", source)
        self.assertIn("vrcforge.material_shader_assignment_approval.v1", source)
        self.assertIn("authoritativePreviewBound", source)
        shader_switch_source = source.split("def apply_shader_switch", 1)[1].split("def apply_semantic_tuning", 1)[0]
        self.assertNotIn('"preview": {', shader_switch_source)
        self.assertNotIn('"expectedBeforeShader"', shader_switch_source)
        self.assertIn("vrcforge_apply_shader_tuning", source)
        self.assertIn('"projectPath": self.project_root', source)
        self.assertIn("/api/app/doctor/unity-mcp/repair", source)
        self.assertIn("/api/app/checkpoints/{checkpoint_id}/restore", source)
        self.assertIn("vrcforge.path_to_skill.v1", source)

    def test_agent_mcp_stdio_supports_no_start_flag(self) -> None:
        args = dashboard_server.parse_args(["--agent-mcp-stdio", "--no-start"])

        self.assertTrue(args.agent_mcp_stdio)
        self.assertTrue(args.no_start)
        self.assertFalse(args.start_runtime)

    def test_agent_mcp_stdio_start_runtime_is_explicit_opt_in(self) -> None:
        default_args = dashboard_server.parse_args(["--agent-mcp-stdio"])
        start_args = dashboard_server.parse_args(["--agent-mcp-stdio", "--start-runtime"])

        self.assertTrue(default_args.agent_mcp_stdio)
        self.assertFalse(default_args.start_runtime)
        self.assertTrue(start_args.start_runtime)

    def test_agent_mcp_stdio_main_does_not_reconcile_sub_agent_tasks(self) -> None:
        args = dashboard_server.parse_args(["--agent-mcp-stdio", "--preflight", "--no-start"])
        reconcile_sub_agents = Mock(side_effect=AssertionError("stdio mode must not reconcile backend-owned tasks"))
        with (
            patch("dashboard_server.parse_args", return_value=args),
            patch.object(
                dashboard_server,
                "_SUB_AGENT_COLLABORATION",
                SimpleNamespace(reconcile_startup=reconcile_sub_agents),
            ),
            patch("tools.vrcforge_agent_mcp_stdio.VRCForgeBridge") as bridge_class,
            patch.object(
                dashboard_server.BACKEND_OWNER_LEASE,
                "acquire",
                side_effect=AssertionError("stdio mode must not acquire the backend owner lease"),
            ) as acquire_owner,
            patch("builtins.print"),
        ):
            bridge_class.return_value.preflight.return_value = {"ok": True}
            result = dashboard_server.main()

        self.assertEqual(result, 0)
        reconcile_sub_agents.assert_not_called()
        acquire_owner.assert_not_called()

    def test_cli_main_does_not_reconcile_sub_agent_tasks(self) -> None:
        args = dashboard_server.parse_args(["--cli", "skill", "list"])
        reconcile_sub_agents = Mock(side_effect=AssertionError("CLI mode must not reconcile backend-owned tasks"))
        with (
            patch("dashboard_server.parse_args", return_value=args),
            patch.object(
                dashboard_server,
                "_SUB_AGENT_COLLABORATION",
                SimpleNamespace(reconcile_startup=reconcile_sub_agents),
            ),
            patch("tools.vrcforge_cli.main", return_value=0) as cli_main,
            patch.object(
                dashboard_server.BACKEND_OWNER_LEASE,
                "acquire",
                side_effect=AssertionError("CLI mode must not acquire the backend owner lease"),
            ) as acquire_owner,
        ):
            result = dashboard_server.main()

        self.assertEqual(result, 0)
        cli_main.assert_called_once_with(["skill", "list"])
        reconcile_sub_agents.assert_not_called()
        acquire_owner.assert_not_called()

    def test_backend_main_refuses_an_occupied_bind_target_before_taking_owner(self) -> None:
        args = dashboard_server.parse_args([])
        lease = Mock()
        with (
            patch("dashboard_server.parse_args", return_value=args),
            patch("dashboard_server.backend_bind_target_occupied", return_value=True),
            patch("dashboard_server.BACKEND_OWNER_LEASE", lease),
            patch("dashboard_server.run_owned_uvicorn_server") as run_server,
            patch("builtins.print"),
        ):
            result = dashboard_server.main()

        self.assertEqual(result, 1)
        lease.acquire.assert_not_called()
        run_server.assert_not_called()

    def test_backend_bind_target_probe_detects_a_live_listener(self) -> None:
        listener = dashboard_server.socket.socket(dashboard_server.socket.AF_INET, dashboard_server.socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = int(listener.getsockname()[1])
            self.assertTrue(dashboard_server.backend_bind_target_occupied("127.0.0.1", port))
        finally:
            listener.close()
        self.assertFalse(dashboard_server.backend_bind_target_occupied("127.0.0.1", port))

    def test_backend_main_refuses_a_second_owner_without_starting_uvicorn(self) -> None:
        args = dashboard_server.parse_args([])
        lease = Mock()
        lease.acquire.return_value = False
        lease.path = Path("backend-owner.lock")
        with (
            patch("dashboard_server.parse_args", return_value=args),
            patch("dashboard_server.backend_bind_target_occupied", return_value=False),
            patch("dashboard_server.BACKEND_OWNER_LEASE", lease),
            patch("dashboard_server.run_owned_uvicorn_server") as run_server,
            patch("builtins.print"),
        ):
            result = dashboard_server.main()

        self.assertEqual(result, 1)
        lease.acquire.assert_called_once_with()
        lease.release.assert_not_called()
        run_server.assert_not_called()

    def test_backend_main_keeps_process_owner_with_active_daemon_after_uvicorn_returns(self) -> None:
        args = dashboard_server.parse_args([])
        lease = Mock()
        lease.acquire.return_value = True
        stop_worker = threading.Event()
        worker = threading.Thread(target=stop_worker.wait, daemon=True)
        worker.start()
        try:
            with (
                patch("dashboard_server.parse_args", return_value=args),
                patch("dashboard_server.backend_bind_target_occupied", return_value=False),
                patch("dashboard_server.BACKEND_OWNER_LEASE", lease),
                patch("dashboard_server.run_owned_uvicorn_server") as run_server,
            ):
                result = dashboard_server.main()

            self.assertEqual(result, 0)
            self.assertTrue(worker.is_alive())
            run_server.assert_called_once_with(args.host, args.port)
            lease.release.assert_not_called()
        finally:
            stop_worker.set()
            worker.join(timeout=2)

    def test_shader_plan_validation_respects_locked_materials_and_properties(self) -> None:
        validation = dashboard_server.validate_shader_material_tuning_plan(
            plan={
                "type": "material_tuning_plan",
                "version": "0.2",
                "changes": [
                    {"material_id": "mat_skin", "semantic_property": "smoothness", "after": 0.6},
                    {"material_id": "mat_skin", "semantic_property": "base_color", "after": "#FFFFFF"},
                ],
            },
            inventory=make_shader_inventory(),
            locked_materials=set(),
            locked_properties={"mat_skin::smoothness"},
        )

        self.assertEqual([item["semantic_property"] for item in validation["validatedChanges"]], ["base_color"])
        self.assertEqual(validation["skippedChanges"][0]["warning"], "Semantic property is locked: smoothness")

    @patch("dashboard_server.apply_shader_material_tuning_direct")
    @patch("dashboard_server.scan_shader_materials_direct")
    @patch("dashboard_server.load_dashboard_settings")
    def test_shader_preset_apply_uses_saved_after_values(
        self,
        mock_load_settings,
        mock_scan_shader_materials_direct,
        mock_apply_shader_material_tuning_direct,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_scan_shader_materials_direct.return_value = make_shader_inventory()

        def apply_side_effect(_settings, _avatar_path, changes):
            self.assertEqual(changes[0]["after"], 0.8)
            return {
                "applied": [
                    {
                        "material_id": "mat_skin",
                        "semantic_property": "smoothness",
                        "before": 0.2,
                        "after": 0.8,
                    }
                ],
                "skipped": [],
            }

        mock_apply_shader_material_tuning_direct.side_effect = apply_side_effect
        dashboard_server.save_tuning_store(
            dashboard_server.SHADER_TUNING_PRESETS_PATH,
            {
                "type": "shader_tuning_presets",
                "version": "0.2",
                "presets": [
                    {
                        "id": "shader_preset_test",
                        "name": "soft_skin",
                        "avatar_path": "Scene/HeroAvatar",
                        "apply_mode": "after_values",
                        "changes": [
                            {
                                "material_id": "mat_skin",
                                "material_name": "Face_Skin",
                                "shader_family": "lilToon",
                                "category": "skin",
                                "semantic_property": "smoothness",
                                "before": 0.2,
                                "after": 0.8,
                            }
                        ],
                    }
                ],
            },
        )

        with patch.object(
            dashboard_server.AGENT_GATEWAY,
            "create_apply_request",
            return_value={"ok": True, "approval": {"id": "approval_shader_preset", "targetTool": "vrcforge_apply_shader_tuning_preset"}},
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/shader/presets/shader_preset_test/apply",
                    json={"avatar": "Scene/HeroAvatar", "source_mode": "unity_live_export"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending_approval")
        self.assertEqual(response.json()["approvalId"], "approval_shader_preset")

    def test_supervised_unity_write_queues_without_calling_live_callback(self) -> None:
        called = False

        def callback() -> dict:
            nonlocal called
            called = True
            return {"ok": True}

        with patch.object(
            dashboard_server.AGENT_GATEWAY,
            "create_apply_request",
            return_value={"ok": True, "approval": {"id": "approval_test"}},
        ):
            result = dashboard_server.request_supervised_unity_write(
                "vrcforge_apply_shader_tuning",
                dashboard_server.ShaderMaterialApplyRequest(
                    avatar_path="Scene/HeroAvatar",
                    inventory=make_shader_inventory(),
                    changes=[{"material_id": "mat_skin", "semantic_property": "smoothness", "after": 0.8}],
                ),
                reason="test",
                preview_callback=callback,
            )

        self.assertFalse(called)
        self.assertEqual(result["status"], "pending_approval")

    def test_vpm_install_handler_uses_sealed_external_process_lane_not_unity_core(self) -> None:
        self.assertNotIn("vrcforge_install_vpm_package", dashboard_server.VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS)
        handler = dashboard_server.AGENT_GATEWAY._write_handlers[  # noqa: SLF001 - registry contract.
            "vrcforge_install_vpm_package"
        ]
        self.assertFalse(handler.requires_approved_execution_context)
        self.assertIs(
            handler.request_preparer,
            dashboard_server.PACKAGE_INSTALL_APPROVED_WRITE.prepare,
        )

    def test_safe_backup_is_approval_bound_to_one_canonical_core_call(self) -> None:
        self.assertNotIn("vrcforge_create_safe_backup", dashboard_server.AGENT_GATEWAY._tools)  # noqa: SLF001 - manifest boundary.
        self.assertIn("vrcforge_create_safe_backup", dashboard_server.VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS)
        handler = dashboard_server.AGENT_GATEWAY._write_handlers[  # noqa: SLF001 - registry contract.
            "vrcforge_create_safe_backup"
        ]
        self.assertTrue(handler.requires_approved_execution_context)
        self.assertIs(handler.checkpoint_prepare_handler, dashboard_server.prepare_authoritative_unity_checkpoint_sync)
        self.assertIs(handler.approved_execution_plan_builder, dashboard_server.build_safe_backup_execution_plan)

        arguments = {
            "avatarPath": "Scene/HeroAvatar",
            "assetPaths": ["Assets/Hero.prefab"],
            "includeOpenScenes": False,
        }
        expected_call = (
            "vrc_create_safe_backup",
            {
                "avatarPath": "Scene/HeroAvatar",
                "assetPaths": ["Assets/Hero.prefab"],
                "includeOpenScenes": False,
                "refreshAssets": False,
            },
        )
        self.assertEqual(dashboard_server.build_safe_backup_execution_plan(arguments), [expected_call])
        self.assertEqual(dashboard_server.build_safe_backup_core_request(arguments), expected_call[1])

    def test_safe_backup_rejects_caller_selected_backup_root(self) -> None:
        for key in ("backupRoot", "backup_root"):
            with self.assertRaisesRegex(ValueError, "Custom backupRoot"):
                dashboard_server.build_safe_backup_core_request({key: "Assets/UnsafeBackups"})

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_safe_backup_restore_preview_uses_app_preview_lane(
        self,
        mock_load_settings,
        mock_invoke_unity_mcp,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke_unity_mcp.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"confirmed": False, "planned": []}},
        )

        result = dashboard_server.preview_safe_backup_restore_sync({"backupId": "vrcforge_backup_test"})

        self.assertTrue(result["ok"])
        _settings, tool_name, arguments = mock_invoke_unity_mcp.call_args.args
        self.assertEqual(tool_name, "vrc_restore_safe_backup")
        self.assertFalse(arguments["confirmRestore"])
        self.assertEqual(mock_invoke_unity_mcp.call_args.kwargs["execution_context"], {"lane": "app_preview"})

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_clothes_scan_reads_avatar_menu_and_parameters(
        self,
        mock_load_settings,
        mock_invoke_unity_mcp,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke_unity_mcp.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={
                "data": {
                    "items": [
                        {
                            "displayName": "Jacket",
                            "source": "menu_control",
                            "menuPath": "Clothes/Jacket",
                            "parameterName": "Cloth_Jacket",
                            "active": True,
                            "canToggleSceneObject": False,
                        }
                    ]
                }
            },
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/clothes/scan", json={"avatar_path": "Scene/HeroAvatar"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["clothes"][0]["parameterName"], "Cloth_Jacket")
        _settings, tool_name, params = mock_invoke_unity_mcp.call_args.args
        self.assertEqual(tool_name, "vrc_scan_avatar_controls")
        self.assertEqual(params["avatarPath"], "Scene/HeroAvatar")
        self.assertEqual(params["outputPath"], "")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_parameter_scan_uses_direct_unity_tool(
        self,
        mock_load_settings,
        mock_invoke_unity_mcp,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke_unity_mcp.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={"data": {"boolCount": 2, "intCount": 1, "floatCount": 3, "suggestions": []}},
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/parameters/scan", json={"avatar_path": "Scene/HeroAvatar"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stats"]["boolCount"], 2)
        _settings, tool_name, params = mock_invoke_unity_mcp.call_args.args
        self.assertEqual(tool_name, "vrc_scan_avatar_parameters")
        self.assertEqual(params["outputPath"], "")

    @patch("dashboard_server.AGENT_GATEWAY.create_apply_request")
    def test_vision_capture_requests_approved_scene_view_write(self, mock_create_apply_request) -> None:
        mock_create_apply_request.return_value = {"ok": True, "status": "pending", "approvalId": "approval_capture"}

        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/vision/capture", json={"avatar_path": "Scene/HeroAvatar"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending")
        payload = mock_create_apply_request.call_args.args[0]
        self.assertEqual(payload["target_tool"], "vrcforge_capture_screenshot")
        self.assertEqual(payload["arguments"]["avatar_path"], "Scene/HeroAvatar")

    @patch("dashboard_server.invoke_unity_mcp")
    @patch("dashboard_server.load_dashboard_settings")
    def test_vision_capture_status_uses_scene_capture_status_mode(
        self,
        mock_load_settings,
        mock_invoke_unity_mcp,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_invoke_unity_mcp.return_value = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={
                "data": {
                    "isPlayMode": False,
                    "captureMode": "scene_view",
                    "gestureManagerDetected": False,
                    "warnings": ["Play Mode with Gesture Manager is recommended"],
                }
            },
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/vision/capture-status", json={"require_play_mode": False})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["isPlayMode"])
        _settings, tool_name, params = mock_invoke_unity_mcp.call_args.args
        self.assertEqual(tool_name, "vrc_capture_scene_view")
        self.assertTrue(params["statusOnly"])
        self.assertFalse(params["requirePlayMode"])

    @patch("dashboard_server.load_dashboard_export_payload")
    @patch("dashboard_server.load_dashboard_settings")
    def test_avatar_blendshape_list_is_limited_to_face_scope(
        self,
        mock_load_settings,
        mock_load_dashboard_export_payload,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace()
        mock_load_dashboard_export_payload.return_value = (
            {
                "avatars": [
                    {
                        "avatarName": "HeroAvatar",
                        "avatarPath": "Scene/HeroAvatar",
                        "sceneName": "Scene",
                        "renderers": [
                            {
                                "rendererName": "Body",
                                "rendererPath": "Scene/HeroAvatar/Body",
                                "meshName": "Body",
                                "blendshapes": [
                                    {"name": "Smile", "currentWeight": 0},
                                    {"name": "Breast_big", "currentWeight": 0},
                                ],
                            }
                        ],
                    }
                ]
            },
            "test-export",
            False,
        )

        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/avatar/blendshapes",
                json={
                    "source_mode": "unity_live_export",
                    "mock_execute": False,
                    "avatar": "Scene/HeroAvatar",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item["blendshapeName"] for item in payload["blendshapes"]], ["Smile"])
        self.assertEqual(payload["filterScope"], "face")

    def test_discover_projects_reads_unity_project_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "Sample Avatar Project"
            (project_dir / "ProjectSettings").mkdir(parents=True)
            (project_dir / "Packages").mkdir(parents=True)
            (project_dir / "Assets" / "VRCForge" / "Editor").mkdir(parents=True)

            (project_dir / "ProjectSettings" / "ProjectVersion.txt").write_text(
                "m_EditorVersion: 2022.3.22f1\n",
                encoding="utf-8",
            )
            (project_dir / "Packages" / "manifest.json").write_text('{"dependencies": {}}', encoding="utf-8")
            (project_dir / "Assets" / "VRCForge" / "Editor" / "BlendshapeExporter.cs").write_text(
                "// test",
                encoding="utf-8",
            )
            for relative in (
                "Assets/VRCForge/Core/MCP/VRCForgeCommandAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeInputAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolRegistry.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs",
                "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs",
            ):
                marker = project_dir / relative
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("// test", encoding="utf-8")

            original_selected = dashboard_server.DASHBOARD_STATE.selected_project_path
            dashboard_server.DASHBOARD_STATE.selected_project_path = dashboard_server.normalize_path_string(str(project_dir))
            try:
                projects = dashboard_server.discover_projects([root])
            finally:
                dashboard_server.DASHBOARD_STATE.selected_project_path = original_selected

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["name"], "Sample Avatar Project")
            self.assertEqual(projects[0]["editorVersion"], "2022.3.22f1")
            self.assertTrue(projects[0]["hasVrcForge"])
            self.assertTrue(projects[0]["hasUnityMcpPackage"])
            self.assertTrue(projects[0]["selected"])

    def test_project_snapshot_refresh_updates_cache_for_fast_bootstrap(self) -> None:
        originals = {
            "roots": list(dashboard_server.DASHBOARD_STATE.project_roots),
            "selected": dashboard_server.DASHBOARD_STATE.selected_project_path,
            "service": dashboard_server._PROJECT_SNAPSHOT_SELECTION,
            "unity_status": dashboard_server.CURRENT_UNITY_STATUS,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "Cached Project"
            stale_project = root / "Removed Project"
            (project_dir / "ProjectSettings").mkdir(parents=True)
            (project_dir / "Packages").mkdir(parents=True)
            (project_dir / "Assets").mkdir(parents=True)
            (project_dir / "ProjectSettings" / "ProjectVersion.txt").write_text(
                "m_EditorVersion: 2022.3.22f1\n",
                encoding="utf-8",
            )
            dashboard_server.DASHBOARD_STATE.project_roots = [root]
            dashboard_server.DASHBOARD_STATE.selected_project_path = dashboard_server.normalize_path_string(str(project_dir))
            service = isolated_project_snapshot_service(cache_path=root / "project-cache.json")
            service._cache = {
                "selectedProjectPath": "",
                "unityEditorPath": "",
                "projects": [
                    {
                        "name": "Removed Project",
                        "path": dashboard_server.normalize_path_string(str(stale_project)),
                        "sources": ["cached-test"],
                    }
                ],
            }
            service._cache_loaded = True
            dashboard_server._PROJECT_SNAPSHOT_SELECTION = service
            dashboard_server.CURRENT_UNITY_STATUS = {"instances": []}
            try:
                with (
                    patch.dict(
                        os.environ,
                        {"APPDATA": str(root / "empty-catalog"), "LOCALAPPDATA": str(root / "empty-catalog")},
                        clear=False,
                    ),
                    patch("dashboard_server.discover_running_unity_projects", return_value=[]),
                    patch("dashboard_server.load_project_prefs", return_value={"customPaths": [], "hiddenPaths": []}),
                ):
                    refreshed = dashboard_server.refresh_project_snapshot_cache_sync()
                    cached = dashboard_server.project_snapshot_payload(use_cache=True, refresh_async=False)

                self.assertEqual(refreshed["scan"]["status"], "ready")
                self.assertFalse(refreshed["scan"]["refreshing"])
                self.assertEqual(cached["scan"]["status"], "ready")
                self.assertTrue(cached["scan"]["cached"])
                self.assertEqual(len(cached["projects"]), 1)
                self.assertEqual(cached["projects"][0]["name"], "Cached Project")
                self.assertEqual(cached["scan"]["addedCount"], 1)
                self.assertEqual(cached["scan"]["removedCount"], 1)
                self.assertEqual(cached["scan"]["addedProjects"][0]["name"], "Cached Project")
                self.assertEqual(cached["scan"]["removedProjects"][0]["name"], "Removed Project")
                self.assertTrue(service.cache_path.is_file())
            finally:
                dashboard_server.DASHBOARD_STATE.project_roots = originals["roots"]
                dashboard_server.DASHBOARD_STATE.selected_project_path = originals["selected"]
                dashboard_server._PROJECT_SNAPSHOT_SELECTION = originals["service"]
                dashboard_server.CURRENT_UNITY_STATUS = originals["unity_status"]

    def test_discover_projects_merges_active_mcp_instance_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "milltina"
            (project_dir / "ProjectSettings").mkdir(parents=True)
            (project_dir / "Assets" / "VRCForge" / "Editor").mkdir(parents=True)
            (project_dir / "ProjectSettings" / "ProjectVersion.txt").write_text(
                "m_EditorVersion: 2022.3.22f1\n",
                encoding="utf-8",
            )
            original_status = dashboard_server.CURRENT_UNITY_STATUS
            dashboard_server.CURRENT_UNITY_STATUS = {
                "instances": [
                    {
                        "project": "milltina",
                        "projectName": "milltina",
                        "projectPath": "",
                        "unityVersion": "2022.3.22f1",
                        "sessionId": "session-123",
                        "hash": "hash-456",
                        "cliInstanceId": "hash-456",
                        "cliSelectorStable": True,
                    }
                ]
            }
            try:
                with patch.dict(
                    os.environ,
                    {"APPDATA": str(root / "empty-catalog"), "LOCALAPPDATA": str(root / "empty-catalog")},
                    clear=False,
                ), patch("dashboard_server.discover_running_unity_projects", return_value=[]):
                    projects = dashboard_server.discover_projects([root], include_external=True)
            finally:
                dashboard_server.CURRENT_UNITY_STATUS = original_status

            milltina = [project for project in projects if project["name"] == "milltina"]
            self.assertEqual(len(milltina), 1)
            self.assertEqual(milltina[0]["path"], dashboard_server.normalize_path_string(str(project_dir)))
            self.assertTrue(milltina[0]["activeMcp"])
            self.assertEqual(milltina[0]["cliInstanceId"], "project-scoped")

    def test_discover_projects_merges_vcc_alcom_and_unity_hub_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "Avatar Project"
            (project_dir / "Assets").mkdir(parents=True)
            (project_dir / "Packages").mkdir(parents=True)
            (project_dir / "ProjectSettings").mkdir(parents=True)
            (project_dir / "ProjectSettings" / "ProjectVersion.txt").write_text(
                "m_EditorVersion: 2022.3.22f1\n",
                encoding="utf-8",
            )

            local_app_data = root / "local-app-data"
            roaming_app_data = root / "roaming-app-data"
            vcc_settings = local_app_data / "VRChatCreatorCompanion" / "settings.json"
            alcom_settings = roaming_app_data / "ALCOM" / "settings.json"
            unity_hub_projects = roaming_app_data / "UnityHub" / "projects-v1.json"
            vcc_settings.parent.mkdir(parents=True)
            alcom_settings.parent.mkdir(parents=True)
            unity_hub_projects.parent.mkdir(parents=True)
            vcc_settings.write_text(json.dumps({"userProjects": [str(project_dir)]}), encoding="utf-8")
            alcom_settings.write_text(json.dumps({"projects": [{"path": str(project_dir)}]}), encoding="utf-8")
            unity_hub_projects.write_text(
                json.dumps({"data": {str(project_dir): {"path": str(project_dir), "title": "Avatar Project", "version": "2022.3.22f1"}}}),
                encoding="utf-8",
            )

            with patch.object(dashboard_server.DASHBOARD_STATE, "selected_project_path", ""), patch.object(
                dashboard_server, "CURRENT_UNITY_STATUS", {"instances": []}
            ), patch.dict(
                os.environ,
                {"APPDATA": str(roaming_app_data), "LOCALAPPDATA": str(local_app_data)},
                clear=False,
            ), patch("dashboard_server.load_project_prefs", return_value={"customPaths": []}), patch(
                "dashboard_server.discover_running_unity_projects", return_value=[]
            ):
                projects = dashboard_server.discover_projects([], include_external=True)

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["name"], "Avatar Project")
            self.assertEqual(projects[0]["sources"], ["vcc", "alcom", "unity-hub"])
            self.assertEqual(projects[0]["editorVersion"], "2022.3.22f1")

            self.assertEqual(
                dashboard_server.PROJECT_CATALOG_DISCOVERY.discover_projects_from_settings_files([vcc_settings]),
                [dashboard_server.normalize_path_string(str(project_dir))],
            )
            self.assertEqual(
                dashboard_server.PROJECT_CATALOG_DISCOVERY.discover_projects_from_settings_files([alcom_settings]),
                [dashboard_server.normalize_path_string(str(project_dir))],
            )

    def test_has_unity_mcp_dependency_accepts_bundled_core_with_utf8_bom_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            manifest_path = project / "Packages" / "manifest.json"
            manifest_path.parent.mkdir()
            manifest_path.write_text(
                json.dumps({"dependencies": {}}),
                encoding="utf-8-sig",
            )
            for relative in (
                "Assets/VRCForge/Core/MCP/VRCForgeCommandAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeInputAttribute.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolRegistry.cs",
                "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs",
                "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs",
            ):
                marker = project / relative
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("// test", encoding="utf-8")

            self.assertTrue(dashboard_server.has_unity_mcp_dependency(manifest_path))

    def test_to_artifact_url_maps_local_artifacts_path(self) -> None:
        path = str((dashboard_server.ARTIFACTS_DIR / "dashboard" / "latest" / "vision_capture.png").resolve())
        url = dashboard_server.to_artifact_url(path)
        self.assertTrue(url.startswith("/artifacts/latest/vision_capture.png?"))
        self.assertIn("artifact_expires=", url)
        self.assertIn("artifact_sig=", url)

    def test_optimizer_proof_index_detail_and_screenshot_are_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            original_artifacts_dir = dashboard_server.ARTIFACTS_DIR
            original_workflows = dashboard_server.OPTIMIZATION_WORKFLOWS
            temp_artifacts = Path(temp_dir) / "artifacts"
            dashboard_server.ARTIFACTS_DIR = temp_artifacts
            proofs = OptimizerProofStore(
                OptimizerProofStorePorts(
                    artifact_root=temp_artifacts,
                    to_artifact_url=dashboard_server.to_artifact_url,
                    to_runtime_artifact_url=dashboard_server.to_runtime_artifact_url,
                )
            )
            dashboard_server.OPTIMIZATION_WORKFLOWS = OptimizationWorkflowService(
                replace(original_workflows._ports, proofs=proofs)
            )
            proof_root = temp_artifacts / "optimizer-apply-smoke"
            run_id = "optimizer-apply-smoke-20260624-010101"
            screenshot = proof_root / run_id / "screenshots" / "before.png"
            screenshot.parent.mkdir(parents=True)
            screenshot.write_bytes(b"proof image")
            (proof_root / f"{run_id}.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "schema": "vrcforge.optimizer_apply_rollback_smoke.v1",
                        "startedAt": "2026-06-24T01:01:01+00:00",
                        "finishedAt": "2026-06-24T01:02:01+00:00",
                        "summary": {
                            "status": "passed",
                            "tool": "optimization.meshia.simplify-apply-request",
                            "checkpointId": "ckpt_123",
                            "rollbackDone": True,
                            "failedSteps": [],
                        },
                        "steps": [
                            {"name": "optimizer.verify_checkpoint_delta", "ok": True, "changedFileCount": 1},
                            {
                                "name": "validation.delta_after_rollback",
                                "ok": True,
                                "rollbackProof": {"matchesBeforeSeverityAndGate": True},
                                "profileDiff": {"pc": {"rankBefore": "Poor", "rankAfter": "Medium"}},
                                "parameterBudgetDelta": {"syncedBitsDelta": -12},
                            },
                        ],
                        "visualRegression": {
                            "schema": "vrcforge.visual_regression.v1",
                            "status": "captured",
                            "proofPassed": True,
                            "requiresHumanReview": True,
                            "scoring": {"mode": "not-run"},
                            "screenshots": {
                                "before": {
                                    "stage": "before",
                                    "captured": True,
                                    "artifactOk": True,
                                    "exists": True,
                                    "artifactImagePath": str(screenshot),
                                }
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            try:
                with TestClient(dashboard_server.app) as client:
                    index_response = client.get("/api/app/optimization/proofs")
                    detail_response = client.get(f"/api/app/optimization/proofs/{run_id}")
                    screenshot_response = client.get(f"/api/app/optimization/proofs/{run_id}/screenshots/before")

                self.assertEqual(index_response.status_code, 200)
                index_payload = index_response.json()
                self.assertTrue(index_payload["readOnly"])
                self.assertEqual(index_payload["proofs"][0]["runId"], run_id)
                self.assertEqual(index_payload["proofs"][0]["profileDiff"]["pc"]["rankAfter"], "Medium")
                self.assertEqual(index_payload["proofs"][0]["parameterBudgetDelta"]["syncedBitsDelta"], -12)
                image_url = index_payload["proofs"][0]["visualRegression"]["screenshots"]["before"]["imageUrl"]
                self.assertTrue(image_url.startswith("/runtime-artifacts/optimizer-apply-smoke/"))
                self.assertIn("artifact_sig=", image_url)

                self.assertEqual(detail_response.status_code, 200)
                detail_payload = detail_response.json()
                self.assertTrue(detail_payload["readOnly"])
                self.assertEqual(detail_payload["proof"]["checkpointId"], "ckpt_123")
                self.assertEqual(detail_payload["report"]["summary"]["tool"], "optimization.meshia.simplify-apply-request")
                self.assertEqual(screenshot_response.status_code, 200)
                self.assertEqual(screenshot_response.content, b"proof image")
            finally:
                dashboard_server.OPTIMIZATION_WORKFLOWS = original_workflows
                dashboard_server.ARTIFACTS_DIR = original_artifacts_dir

    def test_optimizer_proof_screenshot_rejects_paths_outside_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            original_artifacts_dir = dashboard_server.ARTIFACTS_DIR
            original_workflows = dashboard_server.OPTIMIZATION_WORKFLOWS
            temp_path = Path(temp_dir)
            temp_artifacts = temp_path / "artifacts"
            dashboard_server.ARTIFACTS_DIR = temp_artifacts
            proofs = OptimizerProofStore(
                OptimizerProofStorePorts(
                    artifact_root=temp_artifacts,
                    to_artifact_url=dashboard_server.to_artifact_url,
                    to_runtime_artifact_url=dashboard_server.to_runtime_artifact_url,
                )
            )
            dashboard_server.OPTIMIZATION_WORKFLOWS = OptimizationWorkflowService(
                replace(original_workflows._ports, proofs=proofs)
            )
            proof_root = temp_artifacts / "optimizer-apply-smoke"
            proof_root.mkdir(parents=True)
            outside = temp_path / "outside.png"
            outside.write_bytes(b"outside")
            run_id = "optimizer-apply-smoke-20260624-020202"
            (proof_root / f"{run_id}.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "summary": {"status": "passed"},
                        "visualRegression": {
                            "screenshots": {
                                "before": {"artifactImagePath": str(outside), "artifactOk": True},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            try:
                with TestClient(dashboard_server.app) as client:
                    response = client.get(f"/api/app/optimization/proofs/{run_id}/screenshots/before")

                self.assertEqual(response.status_code, 403)
            finally:
                dashboard_server.OPTIMIZATION_WORKFLOWS = original_workflows
                dashboard_server.ARTIFACTS_DIR = original_artifacts_dir

    # ------------------------------------------------------------------
    # /api/clothes/apply-fx (dry_run=True — no Unity needed)
    # ------------------------------------------------------------------
    def test_apply_clothing_fx_dry_run_returns_apply_payload(self) -> None:
        items = [
            {
                "displayName": "Jacket",
                "parameterName": "Cloth_Jacket",
                "sampleObjectPath": "MyAvatar/Body/Jacket",
                "animationClipName": "FX_Jacket_Toggle",
            }
        ]
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/clothes/apply-fx",
                json={"avatar_path": "MyAvatar", "items": items, "dry_run": True},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dryRun"])
        self.assertIn("applyPayload", payload)
        self.assertIn("vrc_apply_clothing_fx", payload["applyPayload"])
        self.assertIn("Cloth_Jacket", payload["applyPayload"])

    def test_apply_clothing_fx_dry_run_no_items_raises_400(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/clothes/apply-fx",
                json={"avatar_path": "MyAvatar", "items": [], "dry_run": True},
            )
        self.assertEqual(response.status_code, 400)

    def test_apply_clothing_fx_registry_uses_only_the_typed_approved_owner(
        self,
    ) -> None:
        registration = dashboard_server.AGENT_GATEWAY._write_handlers[
            "vrcforge_apply_clothing_fx"
        ]
        handler = registration.handler
        ports = dashboard_server.CLOTHING_FX_APPROVED_WRITE._ports

        self.assertIs(
            handler,
            dashboard_server.WARDROBE_OUTFIT_APPROVED_WRITES.apply_clothing_fx,
        )
        self.assertIs(handler.__self__, dashboard_server.CLOTHING_FX_APPROVED_WRITE)
        self.assertEqual(handler.__func__.__name__, "execute")
        self.assertIs(ports.apply_approved, dashboard_server.apply_clothing_fx_direct)
        self.assertIs(ports.preview.__self__, dashboard_server.CLOTHING_FX_READ)
        self.assertEqual(ports.preview.__func__.__name__, "preview_apply_clothing_fx")
        self.assertIs(
            ports.build_apply_preview,
            dashboard_server.build_clothes_fx_apply_preview,
        )
        self.assertIsInstance(
            ports.parse_request(
                {
                    "avatarPath": "MyAvatar",
                    "items": [{"displayName": "Jacket"}],
                    "dry_run": False,
                }
            ),
            dashboard_server.ClothingApplyFxRequest,
        )
        self.assertTrue(registration.requires_approved_execution_context)
        self.assertIs(
            registration.checkpoint_prepare_handler,
            dashboard_server.prepare_authoritative_unity_checkpoint_sync,
        )
        self.assertIs(
            registration.request_preparer,
            dashboard_server.prepare_avatar_scoped_tuning_write_request,
        )

    def test_registered_clothing_fx_live_uses_fixed_lower_port_once(self) -> None:
        settings = SimpleNamespace()
        items = [{"displayName": "Jacket", "parameterName": "Cloth_Jacket"}]
        transport = dashboard_server.McpResult(
            exit_code=0,
            stdout="",
            stderr="",
            payload={"createdCount": 1},
        )
        handler = dashboard_server.AGENT_GATEWAY._write_handlers[
            "vrcforge_apply_clothing_fx"
        ].handler

        with (
            patch(
                "dashboard_server.load_dashboard_settings",
                return_value=settings,
            ) as load_settings,
            patch(
                "dashboard_server.invoke_unity_mcp",
                return_value=transport,
            ) as invoke,
            patch.object(
                dashboard_server.DIAGNOSTIC_LOGGER,
                "emit",
                return_value=None,
            ) as log,
        ):
            payload = handler(
                {
                    "avatarPath": "MyAvatar",
                    "items": items,
                    "dryRun": False,
                }
            )

        self.assertEqual(
            payload,
            {
                "ok": True,
                "avatarPath": "MyAvatar",
                "dryRun": False,
                "applyPayload": dashboard_server.build_clothes_fx_apply_preview(
                    "MyAvatar",
                    items,
                ),
                "result": {"createdCount": 1},
                "itemCount": 1,
            },
        )
        load_settings.assert_called_once()
        invoke.assert_called_once_with(
            settings,
            "vrc_apply_clothing_fx",
            {"avatarPath": "MyAvatar", "items": items},
        )
        self.assertEqual(log.call_count, 1)

    def test_clothing_fx_production_parser_preserves_string_false_and_plan_rejection(
        self,
    ) -> None:
        calls: list[tuple] = []
        production_ports = dashboard_server.CLOTHING_FX_APPROVED_WRITE._ports
        local_owner = dashboard_server.ClothingFxApprovedWriteService(
            replace(
                production_ports,
                preview=lambda request: calls.append(("preview", request)) or {},
                load_settings=lambda request: calls.append(("settings", request))
                or "settings",
                current_avatar_path=lambda: calls.append(("current-avatar",))
                or "Scene/CurrentAvatar",
                build_apply_preview=lambda avatar, items: calls.append(
                    ("build", avatar, items)
                )
                or "frozen-preview",
                apply_approved=lambda settings, avatar, items: calls.append(
                    ("apply", settings, avatar, items)
                )
                or {"createdCount": 1},
                log=lambda level, scope, message, data=None: calls.append(
                    ("log", level, scope, message, data)
                ),
            )
        )
        arguments = {
            "avatarPath": "MyAvatar",
            "items": [{"displayName": "Jacket"}],
            "dryRun": "false",
        }

        payload = local_owner.execute(arguments)

        self.assertFalse(payload["dryRun"])
        self.assertEqual([call[0] for call in calls], ["settings", "build", "apply", "log"])
        self.assertEqual(calls[2][1:], ("settings", "MyAvatar", arguments["items"]))
        registration = dashboard_server.AGENT_GATEWAY._write_handlers[
            "vrcforge_apply_clothing_fx"
        ]
        with self.assertRaisesRegex(ValueError, "dry_run=false"):
            registration.approved_execution_plan_builder(arguments)

    def test_clothing_fx_production_parser_fails_before_all_effect_ports(self) -> None:
        calls: list[str] = []
        production_ports = dashboard_server.CLOTHING_FX_APPROVED_WRITE._ports
        local_owner = dashboard_server.ClothingFxApprovedWriteService(
            replace(
                production_ports,
                preview=lambda _request: calls.append("preview") or {},
                load_settings=lambda _request: calls.append("settings") or object(),
                current_avatar_path=lambda: calls.append("current-avatar") or "Avatar",
                build_apply_preview=lambda _avatar, _items: calls.append("build") or "{}",
                apply_approved=lambda _settings, _avatar, _items: calls.append("apply") or {},
                log=lambda _level, _scope, _message, _data=None: calls.append("log"),
                map_error=lambda exc: calls.append("map-error") or exc,
            )
        )

        for arguments in (
            {"items": None, "dry_run": False},
            {"items": [], "dry_run": None},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValidationError):
                    local_owner.execute(arguments)
                self.assertEqual(calls, [])

    def test_registered_clothing_fx_handler_dry_run_never_calls_unity(self) -> None:
        settings = SimpleNamespace()
        handler = dashboard_server.AGENT_GATEWAY._write_handlers[
            "vrcforge_apply_clothing_fx"
        ].handler
        with patch("dashboard_server.load_dashboard_settings", return_value=settings):
            payload = handler(
                {
                    "avatarPath": "MyAvatar",
                    "items": [{"displayName": "Jacket"}],
                    "dryRun": True,
                }
            )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dryRun"])
        self.assertIn("vrc_apply_clothing_fx", payload["applyPayload"])

    def test_registered_clothing_fx_handler_prefers_snake_dry_run_on_conflict(
        self,
    ) -> None:
        handler = dashboard_server.AGENT_GATEWAY._write_handlers[
            "vrcforge_apply_clothing_fx"
        ].handler
        with patch(
            "dashboard_server.load_dashboard_settings",
            return_value=SimpleNamespace(),
        ):
            payload = handler(
                {
                    "avatarPath": "MyAvatar",
                    "items": [{"displayName": "Jacket"}],
                    "dry_run": True,
                    "dryRun": False,
                }
            )

        self.assertTrue(payload["dryRun"])

    # ------------------------------------------------------------------
    # /api/parameters/apply-optimization (dry_run=True)
    # ------------------------------------------------------------------
    def test_apply_parameter_optimization_dry_run_returns_diff_and_apply_payload(self) -> None:
        suggestions = [
            {"name": "IsWearing", "currentType": "Int", "suggestedType": "Bool", "reason": "heuristic"},
        ]
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/parameters/apply-optimization",
                json={"avatar_path": "MyAvatar", "suggestions": suggestions, "dry_run": True},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dryRun"])
        self.assertEqual(payload["appliedCount"], 1)
        self.assertEqual(payload["diff"][0]["name"], "IsWearing")
        self.assertEqual(payload["diff"][0]["from"], "Int")
        self.assertEqual(payload["diff"][0]["to"], "Bool")
        self.assertIn("vrc_apply_parameter_optimization", payload["applyPayload"])
        self.assertIn("IsWearing", payload["applyPayload"])

    def test_apply_parameter_optimization_no_suggestions_raises_400(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/parameters/apply-optimization",
                json={"avatar_path": "MyAvatar", "suggestions": [], "dry_run": True},
            )
        self.assertEqual(response.status_code, 400)

    def test_parameter_rollback_preview_accepts_scanner_parameter_names(self) -> None:
        preview = dashboard_server.build_parameter_rollback_preview(
            "MyAvatar",
            {
                "parameterNames": [
                    {
                        "name": "DPS",
                        "valueType": "Int",
                        "defaultValue": 0.0,
                        "saved": True,
                        "networkSynced": True,
                    }
                ]
            },
        )

        self.assertIn("vrc_rollback_avatar_parameters", preview)
        self.assertIn("DPS", preview)
        self.assertIn("Int", preview)

    def test_apply_parameter_optimization_non_dry_run_saves_snapshot_first(self) -> None:
        suggestions = [
            {"name": "IsWearing", "currentType": "Int", "suggestedType": "Bool", "reason": "heuristic"},
        ]
        original_snapshot_dir = dashboard_server.PARAMETER_SNAPSHOT_DIR
        original_latest_snapshot = dashboard_server.DASHBOARD_RUNTIME.latest_parameter_snapshot_path

        with tempfile.TemporaryDirectory() as temp_dir:
            dashboard_server.PARAMETER_SNAPSHOT_DIR = Path(temp_dir) / "parameter_snapshots"
            dashboard_server.DASHBOARD_RUNTIME.latest_parameter_snapshot_path = ""

            calls: list[str] = []

            def invoke_side_effect(_settings, tool_name, _params):
                calls.append(tool_name)
                if tool_name == "vrc_scan_avatar_parameters":
                    return dashboard_server.McpResult(
                        exit_code=0,
                        stdout="ok",
                        stderr="",
                        payload={
                            "data": {
                                "ok": True,
                                "avatarPath": "MyAvatar",
                                "parameterCount": 1,
                                "parameterNames": [
                                    {
                                        "name": "IsWearing",
                                        "valueType": "Int",
                                        "defaultValue": 0.0,
                                        "saved": False,
                                        "networkSynced": True,
                                    }
                                ],
                            }
                        },
                    )
                return dashboard_server.McpResult(
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    payload={"data": {"ok": True, "appliedCount": 1}},
                )

            try:
                with patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()), patch(
                    "dashboard_server.invoke_unity_mcp",
                    side_effect=invoke_side_effect,
                ):
                    with patch.object(
                        dashboard_server.AGENT_GATEWAY,
                        "create_apply_request",
                        return_value={"ok": True, "approval": {"id": "approval_parameters", "targetTool": "vrcforge_apply_parameter_optimization"}},
                    ):
                        with TestClient(dashboard_server.app) as client:
                            response = client.post(
                                "/api/parameters/apply-optimization",
                                json={"avatar_path": "MyAvatar", "suggestions": suggestions, "dry_run": False},
                            )

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["status"], "pending_approval")
                self.assertEqual(payload["approvalId"], "approval_parameters")
                self.assertEqual(calls, [])
            finally:
                dashboard_server.PARAMETER_SNAPSHOT_DIR = original_snapshot_dir
                dashboard_server.DASHBOARD_RUNTIME.latest_parameter_snapshot_path = original_latest_snapshot

    def test_parameter_rollback_restores_explicit_snapshot(self) -> None:
        original_snapshot_dir = dashboard_server.PARAMETER_SNAPSHOT_DIR
        original_latest_snapshot = dashboard_server.DASHBOARD_RUNTIME.latest_parameter_snapshot_path

        with tempfile.TemporaryDirectory() as temp_dir:
            dashboard_server.PARAMETER_SNAPSHOT_DIR = Path(temp_dir) / "parameter_snapshots"
            dashboard_server.PARAMETER_SNAPSHOT_DIR.mkdir(parents=True)
            snapshot_path = dashboard_server.PARAMETER_SNAPSHOT_DIR / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "avatarPath": "MyAvatar",
                        "parameterCount": 1,
                        "parameters": [
                            {
                                "name": "IsWearing",
                                "valueType": "Int",
                                "defaultValue": 0.0,
                                "saved": False,
                                "networkSynced": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            try:
                with patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()), patch(
                    "dashboard_server.invoke_unity_mcp",
                    return_value=dashboard_server.McpResult(
                        exit_code=0,
                        stdout="ok",
                        stderr="",
                        payload={"data": {"ok": True, "restoredCount": 1}},
                    ),
                ) as mock_invoke:
                    with patch.object(
                        dashboard_server.AGENT_GATEWAY,
                        "create_apply_request",
                        return_value={"ok": True, "approval": {"id": "approval_rollback", "targetTool": "vrcforge_rollback_parameters"}},
                    ):
                        with TestClient(dashboard_server.app) as client:
                            response = client.post(
                                "/api/parameters/rollback",
                                json={"avatar_path": "MyAvatar", "snapshot_path": str(snapshot_path)},
                            )

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["status"], "pending_approval")
                self.assertEqual(payload["approvalId"], "approval_rollback")
                mock_invoke.assert_not_called()
            finally:
                dashboard_server.PARAMETER_SNAPSHOT_DIR = original_snapshot_dir
                dashboard_server.DASHBOARD_RUNTIME.latest_parameter_snapshot_path = original_latest_snapshot

    # ------------------------------------------------------------------
    # /api/vision/capture-multi (needs Unity — verify endpoint exists + 503)
    # ------------------------------------------------------------------
    @patch("dashboard_server.AGENT_GATEWAY.create_apply_request")
    def test_capture_multi_endpoint_requests_approval(self, mock_create_apply_request) -> None:
        mock_create_apply_request.return_value = {"ok": True, "status": "pending", "approvalId": "approval_multi_capture"}
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/vision/capture-multi",
                json={"angles": ["front", "back"], "width": 512, "height": 512},
            )
        self.assertEqual(response.status_code, 200)
        payload = mock_create_apply_request.call_args.args[0]
        self.assertEqual(payload["target_tool"], "vrcforge_capture_multi_screenshot")
        self.assertEqual(payload["arguments"]["angles"], ["front", "back"])

    # ------------------------------------------------------------------
    # /api/vision/audit-multi — validates multi-path logic
    # ------------------------------------------------------------------
    def test_audit_multi_requires_image_paths(self) -> None:
        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/vision/audit-multi", json={"image_paths": []})
        self.assertEqual(response.status_code, 400)

    def test_normalize_vision_audit_payload_keeps_position_annotations(self) -> None:
        payload = dashboard_server.normalize_vision_audit_payload(
            {
                "status": "clipping",
                "summary": "Hair clips through hood",
                "issues": [{"summary": "hair/hood intersection"}],
                "annotations": [
                    {
                        "label": "hood edge",
                        "reason": "hair intersects the hood",
                        "severity": "high",
                        "box": {"x": 10, "y": 20, "width": 30, "height": 40},
                    }
                ],
            }
        )
        self.assertEqual(payload["status"], "clipping")
        self.assertEqual(payload["issues"], ["hair/hood intersection"])
        self.assertEqual(payload["annotations"][0]["severity"], "high")
        self.assertAlmostEqual(payload["annotations"][0]["box"]["x"], 0.1)
        self.assertAlmostEqual(payload["annotations"][0]["box"]["width"], 0.3)

    def test_normalize_vision_box_accepts_gemini_1000_scale(self) -> None:
        box = dashboard_server.normalize_vision_box({"x_min": 100, "y_min": 200, "x_max": 500, "y_max": 650})
        self.assertIsNotNone(box)
        self.assertAlmostEqual(box["x"], 0.1)
        self.assertAlmostEqual(box["y"], 0.2)
        self.assertAlmostEqual(box["width"], 0.4)
        self.assertAlmostEqual(box["height"], 0.45)

    # ------------------------------------------------------------------
    # Payload preview unit tests (no server)
    # ------------------------------------------------------------------
    def test_build_clothes_fx_apply_preview_contains_key_tokens(self) -> None:
        items = [{"displayName": "Hat", "parameterName": "Cloth_Hat", "sampleObjectPath": "Avatar/Hat", "animationClipName": "FX_Hat_Toggle"}]
        preview = dashboard_server.build_clothes_fx_apply_preview("Avatar", items)
        self.assertIn("vrc_apply_clothing_fx", preview)
        self.assertIn("Cloth_Hat", preview)
        self.assertIn("Avatar/Hat", preview)

    def test_build_parameter_apply_optimization_preview_contains_key_tokens(self) -> None:
        suggestions = [{"name": "IsWearing"}]
        preview = dashboard_server.build_parameter_apply_optimization_preview("Avatar", suggestions)
        self.assertIn("vrc_apply_parameter_optimization", preview)
        self.assertIn("IsWearing", preview)

    def test_build_parameter_rollback_preview_contains_key_tokens(self) -> None:
        rollback_preview = dashboard_server.build_parameter_rollback_preview(
            "Avatar",
            {
                "parameters": [
                    {
                        "name": "IsWearing",
                        "valueType": "Int",
                        "defaultValue": 0.0,
                        "saved": False,
                        "networkSynced": True,
                    }
                ],
            },
        )
        self.assertIn("vrc_rollback_avatar_parameters", rollback_preview)
        self.assertIn("IsWearing", rollback_preview)
        self.assertIn("networkSynced", rollback_preview)

    @patch("dashboard_server.export_blendshapes")
    def test_verify_live_blendshape_changes_reports_actual_weight(self, mock_export_blendshapes) -> None:
        selected_avatar = dashboard_server.SelectedAvatar(
            avatar_name="HeroAvatar",
            avatar_path="Scene/HeroAvatar",
            scene_name="Scene",
            renderer_count=1,
            blendshape_count=1,
        )
        mock_export_blendshapes.return_value = {
            "avatars": [
                {
                    "avatarPath": "Scene/HeroAvatar",
                    "renderers": [
                        {
                            "rendererPath": "Scene/HeroAvatar/Face",
                            "blendshapes": [{"name": "Smile", "currentWeight": 55.0}],
                        }
                    ],
                }
            ]
        }

        verified = dashboard_server.verify_live_blendshape_changes(
            SimpleNamespace(),
            selected_avatar,
            [
                {
                    "rendererPath": "Scene/HeroAvatar/Face",
                    "blendshapeName": "Smile",
                    "targetWeight": 55.0,
                    "previousWeight": 10.0,
                }
            ],
        )

        self.assertTrue(verified[0]["verified"])
        self.assertEqual(verified[0]["actualWeight"], 55.0)
        self.assertEqual(verified[0]["verificationStatus"], "verified")

    def test_outfit_import_request_creates_supervised_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "UnityProject"
            source = root / "LooseOutfit"
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1", encoding="utf-8")
            source.mkdir()
            (source / "Dress.prefab").write_text("%YAML prefab", encoding="utf-8")
            (source / "body.png").write_bytes(b"secret texture bytes")

            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/outfit-imports/request",
                    json={"packagePath": str(source), "projectPath": str(project)},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            approval = payload["approval"]
            self.assertEqual(approval["targetTool"], "vrcforge_import_outfit_package")
            self.assertEqual(approval["status"], "pending")
            self.assertEqual(approval["preview"]["plan"]["kind"], "loose_prefab_copy")
            self.assertTrue(approval["preview"]["plan"]["requiresCheckpoint"])
            self.assertTrue(approval["preview"]["plan"]["rollbackProofRequired"])

    def test_package_install_diagnostics_is_read_only_and_suggests_supervised_fix(self) -> None:
        workflow = PackageInstallWorkflowService(
            PackageInstallWorkflowPorts(
                selected_project_path=lambda: "E:/unity/milltina",
                locate_managers=lambda: [
                    {
                        "name": "vrc-get",
                        "source": "PATH",
                        "supportsCommandInstall": True,
                        "supportsUiHandoff": False,
                    }
                ],
                detect_package=lambda _project, _package_ids: {"installed": False},
                addon_frameworks={},
                optimizer_dependencies=[],
                summarize_debug=lambda value: value,
                read_compile_errors=lambda _params: {
                    "ok": True,
                    "result": {"payload": {"errors": [{"message": "CS0246 missing type"}]}},
                },
                redact_support=lambda value: value,
                create_apply_request=lambda _params, **_kwargs: {"ok": False},
            )
        )
        payload = workflow.diagnose_install(
            {
                "projectPath": "E:/unity/milltina",
                "packageId": "nadena.dev.modular-avatar",
                "stderrSummary": "network timeout then compilation failed CS0246",
            }
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema"], "vrcforge.package_install_diagnostics.v1")
        self.assertTrue(payload["readOnly"])
        codes = {symptom["code"] for symptom in payload["symptoms"]}
        self.assertIn("network", codes)
        self.assertIn("compile", codes)
        self.assertFalse(payload["repairPolicy"]["automaticRepair"])
        self.assertTrue(payload["repairPolicy"]["requiresPreviewApprovalCheckpointValidationRollback"])
        self.assertIn("retry_vpm_install_request", {item["id"] for item in payload["suggestedFixPlans"]})

    def test_package_install_diagnostics_does_not_scan_status_snapshot_as_log(self) -> None:
        workflow = PackageInstallWorkflowService(
            PackageInstallWorkflowPorts(
                selected_project_path=lambda: "E:/unity/milltina",
                locate_managers=lambda: [
                    {
                        "name": "vrc-get",
                        "source": "PATH",
                        "supportsCommandInstall": True,
                        "supportsUiHandoff": False,
                    }
                ],
                detect_package=lambda _project, package_ids: {
                    "installed": True,
                    "packageId": package_ids[0],
                    "sourceSummary": {"vpmManifest": True, "manifest": True},
                },
                addon_frameworks={
                    "fixture": {"packageIds": ["com.example.fixture"]},
                },
                optimizer_dependencies=[],
                summarize_debug=lambda value: value,
                read_compile_errors=lambda _params: {
                    "ok": True,
                    "result": {"payload": {"errors": []}},
                },
                redact_support=lambda value: value,
                create_apply_request=lambda _params, **_kwargs: {"ok": False},
            )
        )
        payload = workflow.diagnose_install(
            {
                "projectPath": "E:/unity/milltina",
                "packageId": "com.anatawa12.avatar-optimizer",
                "stdoutSummary": "",
                "stderrSummary": "",
            }
        )

        self.assertTrue(payload["ok"])
        self.assertEqual({symptom["code"] for symptom in payload["symptoms"]}, {"unknown"})


class MaterialMagentaValidationTests(unittest.TestCase):
    """Fix #2: a post-import magenta/missing-shader material must block validation."""

    @staticmethod
    def _materials_result(materials: list[dict]) -> dict:
        return {"ok": True, "payload": {"inventory": {"materials": materials}, "materials": materials}}

    def test_magenta_material_emits_blocking_error_finding(self) -> None:
        findings: list[dict] = []
        result = self._materials_result(
            [
                {"material_id": "m_ok", "renderer_path": "Body", "shader_name": "lilToon"},
                {"material_id": "m_missing", "renderer_path": "Dress", "shader_name": ""},
                {"material_id": "m_err", "renderer_path": "Hair", "shader_name": "Hidden/InternalErrorShader"},
            ]
        )

        dashboard_server._material_validation(findings, result)

        magenta = [item for item in findings if item.get("severity") == "Error"]
        self.assertEqual(len(magenta), 1)
        detail = magenta[0].get("detail") or {}
        self.assertEqual(detail.get("magentaCount"), 2)
        self.assertIn("Dress", detail.get("affectedRenderers", []))
        self.assertIn("Hair", detail.get("affectedRenderers", []))
        self.assertTrue(detail.get("remediation"))

    def test_healthy_materials_do_not_emit_error(self) -> None:
        findings: list[dict] = []
        result = self._materials_result(
            [{"material_id": "m_ok", "renderer_path": "Body", "shader_name": "Poiyomi/Toon"}]
        )

        dashboard_server._material_validation(findings, result)

        self.assertFalse([item for item in findings if item.get("severity") == "Error"])
        self.assertTrue([item for item in findings if item.get("severity") == "Info"])


if __name__ == "__main__":
    unittest.main()
