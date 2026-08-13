from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import dashboard_server


def _make_unity_project(root: Path) -> Path:
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir()
    (root / "ProjectSettings").mkdir()
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.22f1\n",
        encoding="utf-8",
    )
    return root


def test_project_prefs_persist_explicit_general_and_unity_types(tmp_path: Path, monkeypatch) -> None:
    general = tmp_path / "ordinary-repository"
    general.mkdir()
    unity = _make_unity_project(tmp_path / "unity-project")
    prefs_path = tmp_path / "project-prefs.json"
    monkeypatch.setattr(dashboard_server, "project_prefs_path", lambda: prefs_path)

    with TestClient(dashboard_server.app) as client:
        response = client.post(
            "/api/app/projects/prefs",
            json={
                "customProjects": [
                    {"path": str(general), "projectType": "general"},
                    {"path": str(unity), "projectType": "unity"},
                ],
                "hiddenPaths": [],
            },
        )

    assert response.status_code == 200, response.text
    projects = response.json()["customProjects"]
    assert projects == [
        {"path": str(general).replace("\\", "/"), "projectType": "general"},
        {"path": str(unity).replace("\\", "/"), "projectType": "unity"},
    ]
    persisted = json.loads(prefs_path.read_text(encoding="utf-8"))
    assert persisted["version"] == 2
    assert persisted["customProjects"] == projects


def test_unity_type_rejects_plain_directory_without_changing_general_acceptance(tmp_path: Path, monkeypatch) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    prefs_path = tmp_path / "project-prefs.json"
    monkeypatch.setattr(dashboard_server, "project_prefs_path", lambda: prefs_path)

    with TestClient(dashboard_server.app) as client:
        response = client.post(
            "/api/app/projects/prefs",
            json={
                "customProjects": [
                    {"path": str(plain), "projectType": "unity"},
                    {"path": str(plain), "projectType": "general"},
                ],
                "hiddenPaths": [],
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["customProjects"] == [
        {"path": str(plain).replace("\\", "/"), "projectType": "general"},
    ]


def test_runtime_payload_keeps_general_directory_out_of_unity_tool_context(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    request = dashboard_server.AgentRuntimeMessageRequest.model_validate(
        {"message": "inspect this repository", "projectPath": str(plain), "projectType": "general"}
    )

    payload = dashboard_server.agent_runtime_request_payload(request)

    assert payload["projectType"] == "general"
    assert payload["_projectType"] == "general"
    assert payload["_projectContextActive"] is False
    assert payload["projectPath"] == str(plain)


def test_exact_turn_steer_preserves_general_project_type(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def submit(payload: dict[str, object]) -> dict[str, object]:
        captured.update(payload)
        return {"accepted": True, "mode": "steer", "status": "accepted"}

    monkeypatch.setattr(dashboard_server.AGENT_GATEWAY, "submit_runtime_steer", submit)
    with TestClient(dashboard_server.app) as client:
        response = client.post(
            "/api/app/agent/runs/queue",
            json={
                "laneId": "lane-general",
                "sessionId": "session-general",
                "clientTurnId": "input-general",
                "targetClientTurnId": "active-general",
                "message": "continue",
                "projectPath": str(tmp_path),
                "projectType": "general",
            },
        )

    assert response.status_code == 200, response.text
    assert captured["projectType"] == "general"


def test_general_project_shell_uses_workspace_without_claiming_unity_root(tmp_path: Path, monkeypatch) -> None:
    gateway = dashboard_server.AGENT_GATEWAY
    planner_calls = 0
    shell_payload: dict[str, object] = {}

    def plan(_message, _params, _observe, _history=None, *, loop_state=None, **_kwargs):
        nonlocal planner_calls
        planner_calls += 1
        if planner_calls == 1:
            return {
                "planner": "llm",
                "summary": "Inspect the General project.",
                "shellNeeded": True,
                "shellCommand": "dir",
                "continueLoop": True,
                "nextStep": "call_shell",
            }
        action_ids = [
            str(item.get("actionId") or "")
            for item in (loop_state or [])
            if item.get("actionId") and item.get("status") == "executed"
        ]
        return {
            "planner": "llm",
            "summary": "Inspection complete.",
            "reply": "The directory is readable.",
            "continueLoop": False,
            "nextStep": "done",
            "completionClaim": {"satisfied": True, "evidenceActionIds": action_ids},
        }

    def execute(params, **_kwargs):
        shell_payload.update(params)
        return {
            "ok": True,
            "status": "finished",
            "result": {"ok": True, "exitCode": 0, "stdout": "note.txt"},
            "outcome": {
                "status": "ok",
                "summary": "Directory listed.",
                "verification": {"state": "passed", "checks": []},
            },
        }

    monkeypatch.setattr(gateway.runtime_planner, "plan_agent_turn", plan)
    monkeypatch.setattr(gateway.shell, "execute", execute)
    result = gateway.runtime_message(
        {
            "message": "Inspect this General project.",
            "projectPath": str(tmp_path),
            "projectType": "general",
            "_projectContextActive": False,
            "session_id": "general-project-shell-session",
            "client_turn_id": "general-project-shell-turn",
        }
    )

    assert result["plan"]["nextStep"] in {"done", "completion_unverified"}, result
    assert shell_payload["projectRoot"] == ""
    assert Path(str(shell_payload["workspace_root"])) == tmp_path.resolve()
    assert Path(str(shell_payload["cwd"])) == tmp_path.resolve()
