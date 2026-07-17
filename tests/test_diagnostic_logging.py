from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import dashboard_server
from developer_options_guard import DeveloperOptionsChallengeError, DeveloperOptionsGuard
from diagnostic_logging import (
    DiagnosticLogManager,
    DiagnosticTextStream,
    format_log_line,
    parse_log_line,
)
from diagnostic_privacy import DiagnosticPrivacy
from diagnostic_safety import SAFETY_POSTURE_SCHEMA, build_safety_posture


class MutableClock:
    def __init__(self, value: datetime | float) -> None:
        self.value = value

    def __call__(self):
        return self.value


def windows_user_path(user: str, *parts: str) -> str:
    separator = chr(92)
    return "C:" + separator + separator.join(("Users", user, *parts))


def make_manager(
    root: Path,
    *,
    clock: MutableClock | None = None,
    max_files: int = 40,
    max_total_bytes: int = 52_428_800,
    max_file_bytes: int = 8_388_608,
) -> tuple[DiagnosticPrivacy, DiagnosticLogManager]:
    privacy = DiagnosticPrivacy(root / "config", now_fn=clock or (lambda: datetime.now(timezone.utc)))
    manager = DiagnosticLogManager(
        root / "logs",
        root / "config" / "diagnostics.json",
        privacy,
        now_fn=clock or (lambda: datetime.now(timezone.utc)),
        max_files=max_files,
        max_total_bytes=max_total_bytes,
        max_file_bytes=max_file_bytes,
    )
    return privacy, manager


def test_internal_runtime_shutdown_requires_exact_ipc_proof_and_runs_after_response() -> None:
    token = "shutdown-test-session-token"
    server = SimpleNamespace(should_exit=False)
    proof = hmac.new(
        token.encode("utf-8"),
        (
            "vrcforge.tauri-ipc-bridge.v1\n"
            f"POST\n{dashboard_server.APP_INTERNAL_SHUTDOWN_PATH}"
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    direct_headers = {
        "Authorization": f"Bearer {token}",
        "Origin": "tauri://localhost",
    }
    ipc_headers = {
        **direct_headers,
        "X-VRCForge-Transport": "tauri-ipc-bridge",
        "X-VRCForge-Transport-Proof": proof,
    }

    dashboard_server.register_owned_uvicorn_server(server)
    try:
        with (
            patch.object(dashboard_server, "APP_AUTH_REQUIRED", False),
            patch.object(dashboard_server, "APP_SESSION_TOKEN", token),
            TestClient(dashboard_server.app) as client,
        ):
            proof_without_bearer = client.post(
                dashboard_server.APP_INTERNAL_SHUTDOWN_PATH,
                headers={key: value for key, value in ipc_headers.items() if key != "Authorization"},
            )
            assert proof_without_bearer.status_code == 401
            assert server.should_exit is False

            direct = client.post(dashboard_server.APP_INTERNAL_SHUTDOWN_PATH, headers=direct_headers)
            assert direct.status_code == 403
            assert server.should_exit is False

            accepted = client.post(dashboard_server.APP_INTERNAL_SHUTDOWN_PATH, headers=ipc_headers)
            assert accepted.status_code == 202
            assert accepted.json() == {
                "ok": True,
                "schema": "vrcforge.runtime_shutdown.v1",
                "scheduled": True,
            }
            # TestClient waits for response background tasks. Reaching this
            # assertion therefore proves the response was produced first.
            assert server.should_exit is True
    finally:
        dashboard_server.clear_owned_uvicorn_server(server)


def test_owned_uvicorn_server_lifecycle_clears_global_after_return_and_error() -> None:
    config = object()
    returned_server = Mock()
    returned_server.run.side_effect = lambda: (
        dashboard_server.current_owned_uvicorn_server() is returned_server
    ) or pytest.fail("owned server was not registered while running")

    with (
        patch.object(dashboard_server.uvicorn, "Config", return_value=config) as config_factory,
        patch.object(dashboard_server.uvicorn, "Server", return_value=returned_server),
    ):
        dashboard_server.run_owned_uvicorn_server("127.0.0.1", 18757)

    config_factory.assert_called_once_with(
        app=dashboard_server.app,
        host="127.0.0.1",
        port=18757,
        log_level="info",
        access_log=False,
    )
    assert dashboard_server.current_owned_uvicorn_server() is None

    failed_server = Mock()
    failed_server.run.side_effect = RuntimeError("test server failure")
    with (
        patch.object(dashboard_server.uvicorn, "Config", return_value=config),
        patch.object(dashboard_server.uvicorn, "Server", return_value=failed_server),
        pytest.raises(RuntimeError, match="test server failure"),
    ):
        dashboard_server.run_owned_uvicorn_server("127.0.0.1", 18758)
    assert dashboard_server.current_owned_uvicorn_server() is None


def test_timestamp_filename_exclusive_suffix_and_readable_single_line_utf8() -> None:
    fixed = datetime(2026, 7, 16, 14, 30, 5, 123000, tzinfo=timezone(timedelta(hours=9)))
    clock = MutableClock(fixed)
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        privacy, manager = make_manager(root, clock=clock)
        manager.update_config(log_level="trace")
        logs = root / "logs"
        logs.mkdir(parents=True)
        stamp = fixed.astimezone().strftime("%Y-%m-%d_%H-%M-%S")
        (logs / f"vrcforge_{stamp}_0.log").write_text("occupied\n", encoding="utf-8")

        entry = manager.emit("warning", "unity]scope", "多语言\nmessage | readable", {"z": 1, "a": "值"})

        assert entry is not None
        active = manager.active_path
        assert active is not None
        assert active.name == f"vrcforge_{stamp}_1.log"
        physical_lines = active.read_text(encoding="utf-8").splitlines()
        assert len(physical_lines) == 1
        assert "多语言" in physical_lines[0]
        parsed = parse_log_line(physical_lines[0])
        assert parsed is not None
        assert parsed["level"] == "warn"
        assert parsed["scope"] == "unity]scope"
        assert parsed["message"] == "多语言\nmessage | readable"
        assert parsed["data"] == {"a": "值", "z": 1}
        assert format_log_line(parsed).count("\n") == 0


def test_five_level_threshold_is_live_and_legacy_bool_migrates() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        config = root / "config" / "diagnostics.json"
        config.parent.mkdir(parents=True)
        config.write_text('{"debugLogging": true}\n', encoding="utf-8")
        privacy = DiagnosticPrivacy(config.parent)
        manager = DiagnosticLogManager(root / "logs", config, privacy)

        assert manager.log_level == "debug"
        status = manager.status()
        assert status["schema"] == "vrcforge.diagnostics.v2"
        assert status["logLevels"] == ["error", "warn", "info", "debug", "trace"]
        assert status["retentionDays"] == 5
        assert status["maxFiles"] == 40
        assert status["maxTotalBytes"] == 52_428_800
        assert status["maxFileBytes"] == 8_388_608
        assert isinstance(status["identities"], list)
        assert manager.emit("trace", "test", "trace filtered") is None
        assert manager.emit("debug", "test", "debug kept") is not None
        manager.update_config(log_level="error")
        assert manager.emit("warn", "test", "warn filtered") is None
        assert manager.emit("error", "test", "error kept") is not None
        manager.update_config(debug_logging=False)
        assert manager.log_level == "info"
        assert manager.emit("info", "test", "info kept") is not None
        assert manager.emit("debug", "test", "debug filtered") is None
        with pytest.raises(ValueError) as invalid:
            manager.update_config(log_level="TEST_ONLY_SECRET_LEVEL")
        assert str(invalid.value) == "Unsupported diagnostic log level."
        assert "TEST_ONLY_SECRET_LEVEL" not in str(invalid.value)

        config.write_text('{"debugLogging": false}\n', encoding="utf-8")
        reloaded = DiagnosticLogManager(root / "other-logs", config, privacy)
        assert reloaded.log_level == "info"


def test_error_threshold_keeps_only_essential_baseline_and_real_errors() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        privacy, manager = make_manager(root)
        manager.update_config(log_level="error")
        assert manager.emit("info", "ordinary", "ordinary info") is None
        assert manager.emit("warn", "ordinary", "ordinary warning") is None
        essential = manager.emit("info", "safety", "essential posture", essential=True)
        error = manager.emit("error", "runtime", "real failure", {"code": "missing_command"})
        assert essential is not None
        assert essential["data"]["baseline"] is True
        assert error is not None
        assert error["data"]["baseline"] is True

        permission = {
            "executionMode": "approval",
            "perActionApproval": True,
            "allowWriteRequests": True,
        }
        advanced = {
            "developerOptionsEnabled": False,
            "developerOptionsEverEnabled": True,
            "computerUseEnabled": False,
            "computerUseEverEnabled": False,
        }
        with (
            patch.object(dashboard_server, "DIAGNOSTIC_PRIVACY", privacy),
            patch.object(dashboard_server, "DIAGNOSTIC_LOGGER", manager),
            patch.object(dashboard_server, "RECENT_LOGS", manager.recent_entries),
            patch.object(dashboard_server.AGENT_GATEWAY, "permission_state", return_value=permission),
            patch.object(dashboard_server.AGENT_GATEWAY, "advanced_settings_state", return_value=advanced),
            patch.object(dashboard_server.EVENT_BUS, "broadcast", new=AsyncMock()),
            patch.object(dashboard_server, "current_diagnostic_identity_context", return_value={}),
        ):
            asyncio.run(dashboard_server.emit_safety_posture_snapshot("startup"))
            asyncio.run(dashboard_server.emit_safety_posture_snapshot("normal_shutdown"))

        snapshots = [
            entry
            for entry in manager.tail_entries(20)
            if entry["scope"] == "safety" and entry["data"].get("phase") in {"startup", "normal_shutdown"}
        ]
        assert [entry["data"]["phase"] for entry in snapshots[-2:]] == ["startup", "normal_shutdown"]
        assert all(entry["data"]["baseline"] is True for entry in snapshots)
        assert all(entry["data"]["posture"]["schema"] == SAFETY_POSTURE_SCHEMA for entry in snapshots)


def test_safety_posture_describes_auto_execution_and_restore_exception_truthfully() -> None:
    posture = build_safety_posture(
        {
            "executionMode": "roslyn_full_auto",
            "perActionApproval": False,
            "autoApprove": True,
            "autoApproveDangerousRequiresApproval": False,
            "allowWriteRequests": True,
            "roslynRiskAcknowledged": True,
            "roslynFullAutoEverEnabled": True,
        },
        {
            "developerOptionsEnabled": True,
            "developerOptionsEverEnabled": True,
            "computerUseEnabled": True,
            "computerUseEverEnabled": True,
        },
        "trace",
    )
    assert posture["externalAgent"] == {
        "requestOnly": True,
        "directApplyExposed": False,
        "requestMayAutoExecute": True,
        "selectedMode": "roslyn_full_auto",
    }
    assert posture["fullPermission"]["canOverrideExplicitApproval"] is True
    assert posture["checkpoint"] == {
        "requiredBeforeOrdinaryWrite": True,
        "restoreAndRecoveryExempt": True,
    }
    assert posture["rollback"] == {
        "availableForCheckpointedWrite": True,
        "approvalRecordRequired": True,
        "manualApprovalRequired": False,
        "restoreMayAutoExecute": True,
    }


def test_exact_error_bursts_are_bounded_without_hiding_first_or_distinct_context() -> None:
    clock = MutableClock(datetime(2026, 7, 17, 0, 0, 0, tzinfo=timezone.utc))
    with TemporaryDirectory() as temp_dir:
        _, manager = make_manager(Path(temp_dir), clock=clock)
        manager.update_config(log_level="error")
        for _ in range(5):
            manager.emit("error", "unity-cli", "Command is missing.", {"command": "alpha"})
        manager.emit("error", "unity-cli", "Command is missing.", {"command": "beta"})
        entries = manager.tail_entries(20)
        assert len(entries) == 3
        assert entries[0]["data"]["command"] == "alpha"
        assert entries[0]["data"]["baseline"] is True
        assert entries[1]["data"]["aggregated"] is True
        assert entries[1]["data"]["occurrenceCount"] == 5
        assert entries[1]["data"]["repeatCount"] == 4
        assert entries[2]["data"]["command"] == "beta"

        clock.value += timedelta(seconds=6)
        manager.emit("error", "unity-cli", "Command is missing.", {"command": "alpha"})
        assert len(manager.tail_entries(20)) == 4


def test_config_write_failure_rolls_back_live_level() -> None:
    with TemporaryDirectory() as temp_dir:
        _, manager = make_manager(Path(temp_dir))
        assert manager.log_level == "info"
        with patch.object(manager, "_write_config_locked", side_effect=OSError("config unavailable")):
            with pytest.raises(OSError):
                manager.update_config(log_level="trace")
        assert manager.log_level == "info"
        assert manager.emit("debug", "test", "must remain filtered") is None


def test_file_size_limit_rotates_to_a_new_same_second_shard() -> None:
    fixed = datetime(2026, 7, 16, 5, 4, 3, tzinfo=timezone.utc)
    clock = MutableClock(fixed)
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _, manager = make_manager(root, clock=clock, max_file_bytes=320)
        manager.emit("info", "rotation", "first " + "a" * 180)
        manager.emit("info", "rotation", "second " + "b" * 180)
        files = sorted((root / "logs").glob("vrcforge_*.log"))
        stamp = fixed.astimezone().strftime("%Y-%m-%d_%H-%M-%S")
        assert [path.name for path in files] == [
            f"vrcforge_{stamp}_0.log",
            f"vrcforge_{stamp}_1.log",
        ]
        assert all(len(path.read_text(encoding="utf-8").splitlines()) == 1 for path in files)


def test_privacy_sentinels_and_quoted_and_unquoted_space_paths_never_reach_disk() -> None:
    user = "Probe User"
    project = windows_user_path(user, "My Private Project")
    quoted_asset = project + chr(92) + "Assets" + chr(92) + "Avatar File.prefab"
    unquoted_asset = project + chr(92) + "Library" + chr(92) + "Artifact Cache.bin"
    blueprint = "avtr_TESTONLY_ABC123"
    token = "TEST_ONLY_BEARER_123456"
    api_secret = "TEST_ONLY_QUERY_SECRET_789"
    basic_secret = "TEST_ONLY_BASIC_SECRET_246"
    cookie_secret = "TEST_ONLY_COOKIE_SECRET_135"
    quoted_secret = "TEST ONLY QUOTED SECRET 864"
    client_secret = "TEST_ONLY_CLIENT_SECRET_975"
    oauth_secret = "TEST_ONLY_OAUTH_SECRET_531"
    credential_secret = "TEST_ONLY_CREDENTIAL_642"
    private_key_secret = "TEST_ONLY_PRIVATE_KEY_753"
    mac = "02:42:ac:11:00:02"
    private_ip = "192.168.77.31"
    link_local_ipv6 = "fe80::1234"
    ula_ipv6 = "fc00::abcd"
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _, manager = make_manager(root)
        manager.update_config(log_level="trace")
        manager.emit(
            "error",
            "privacy",
            f'quoted "{quoted_asset}"; unquoted {unquoted_asset} failed badly',
            {
                "projectPath": project,
                "avatarName": "Probe Avatar",
                "blueprintId": blueprint,
                "authorization": f"Bearer {token}",
                "endpoint": (
                    f"https://example.invalid/run?api_key={api_secret}&client_secret={client_secret}"
                    f"&oauth_token={oauth_secret}&mode=test"
                ),
                "headers": (
                    f"Authorization: Basic {basic_secret}==\n"
                    f"Cookie: session={cookie_secret}; preference=private"
                ),
                "serialized": (
                    "{'api_key': '" + quoted_secret + "'} "
                    + '{"clientSecret":"' + client_secret + '"}'
                ),
                "clientSecret": client_secret,
                "oauthToken": oauth_secret,
                "credential": credential_secret,
                "privateKey": private_key_secret,
                "tokenCount": 37,
                "untrustedMetrics": {"tokenCount": credential_secret},
                "network": f"client={private_ip} ipv6={link_local_ipv6} ula={ula_ipv6} mac={mac}",
                "exception": f"at {unquoted_asset}:42 token={token}",
            },
        )

        content = "\n".join(path.read_text(encoding="utf-8") for path in (root / "logs").glob("*.log"))
        mapping_content = (root / "config" / "diagnostic-identities.json").read_text(encoding="utf-8")
        for sentinel in (
            project,
            quoted_asset,
            unquoted_asset,
            blueprint,
            token,
            api_secret,
            basic_secret,
            cookie_secret,
            quoted_secret,
            client_secret,
            oauth_secret,
            credential_secret,
            private_key_secret,
            mac,
            private_ip,
            link_local_ipv6,
            ula_ipv6,
        ):
            assert sentinel not in content
        assert "TEST_ONLY_SECRET_LEVEL" not in content
        assert "prj_" in content
        assert "avt_" in content
        assert "path_" in content
        assert "net_" in content
        parsed = manager.tail_entries(1)[0]
        assert parsed["data"]["tokenCount"] == 37
        assert parsed["data"]["untrustedMetrics"]["tokenCount"] == "[REDACTED]"
        # Generic paths may be retained only inside the private local mapping;
        # their labels are generic and can never reflect swallowed tail text.
        mapping = json.loads(mapping_content)
        path_rows = [row for row in mapping["records"] if row["kind"] == "path"]
        assert path_rows
        assert all(row["label"] == "local path" for row in path_rows)


def test_aliases_are_stable_per_install_context_separated_and_summaries_are_safe() -> None:
    user = "Probe User"
    other_user = "SecondProbe"
    project = windows_user_path(user, "Unity Projects", "Avatar Alpha")
    other_project = windows_user_path(other_user, "Unity Projects", "Avatar Alpha")
    blueprint = "avtr_TESTONLY_SHARED999"

    def payload(project_path: str) -> dict[str, str]:
        return {
            "projectPath": project_path,
            "avatarName": "Avatar Alpha",
            "avatarPath": "Scene/Avatar Alpha",
            "blueprintId": blueprint,
        }

    with TemporaryDirectory() as first_dir, TemporaryDirectory() as second_dir:
        first_root = Path(first_dir)
        privacy = DiagnosticPrivacy(first_root / "config")
        first_safe = privacy.redact(payload(project))
        restarted = DiagnosticPrivacy(first_root / "config")
        restarted_safe = restarted.redact(payload(project))
        other_context = restarted.redact(payload(other_project))
        other_install = DiagnosticPrivacy(Path(second_dir) / "config")
        other_install_safe = other_install.redact(payload(project))

        assert first_safe["projectPath"] == restarted_safe["projectPath"]
        assert first_safe["blueprintId"] == restarted_safe["blueprintId"]
        assert first_safe["projectPath"] != other_context["projectPath"]
        assert first_safe["blueprintId"] != other_context["blueprintId"]
        assert first_safe["projectPath"] != other_install_safe["projectPath"]
        assert first_safe["blueprintId"] != other_install_safe["blueprintId"]

        summaries = restarted.safe_identity_summaries()
        assert isinstance(summaries, list)
        user_row = next(row for row in summaries if row["kind"] == "user" and row["windowsUser"] == user)
        project_row = next(row for row in summaries if row["kind"] == "project" and row["windowsUser"] == user)
        avatar_row = next(row for row in summaries if row["kind"] == "avatar" and row["windowsUser"] == user)
        assert project_row["userAlias"] == user_row["alias"]
        assert avatar_row["userAlias"] == user_row["alias"]
        assert avatar_row["projectAlias"] == project_row["alias"]
        assert project_row["projectName"] == "Avatar Alpha"
        assert avatar_row["avatarName"] == "Avatar Alpha"
        serialized = json.dumps(summaries, ensure_ascii=False)
        assert project not in serialized
        assert blueprint not in serialized
        assert "value" not in serialized
        assert "path" not in serialized.lower()


def test_drive_project_uses_os_user_fallback_and_multiple_projects_do_not_collapse() -> None:
    separator = chr(92)
    project_a = "D:" + separator + separator.join(("UnityWork", "Project A"))
    project_b = "D:" + separator + separator.join(("UnityWork", "Project B"))
    blueprint = "avtr_TESTONLY_CROSSPROJECT"
    with TemporaryDirectory() as temp_dir:
        privacy = DiagnosticPrivacy(
            Path(temp_dir) / "config",
            current_user_fn=lambda: "Drive Probe User",
        )
        safe = privacy.redact(
            {
                "items": [
                    {"projectPath": project_a, "avatarName": "Avatar", "blueprintId": blueprint},
                    {"projectPath": project_b, "avatarName": "Avatar", "blueprintId": blueprint},
                ]
            }
        )
        first, second = safe["items"]
        assert first["projectPath"].startswith("prj_")
        assert second["projectPath"].startswith("prj_")
        assert first["projectPath"] != second["projectPath"]
        assert first["blueprintId"] != second["blueprintId"]
        summaries = privacy.safe_identity_summaries()
        user_row = next(row for row in summaries if row["kind"] == "user")
        projects = [row for row in summaries if row["kind"] == "project"]
        avatars = [row for row in summaries if row["kind"] == "avatar"]
        assert user_row["windowsUser"] == "Drive Probe User"
        assert {row["projectName"] for row in projects} == {"Project A", "Project B"}
        assert all(row["userAlias"] == user_row["alias"] for row in projects)
        assert {row["projectAlias"] for row in avatars} == {row["alias"] for row in projects}
        serialized = json.dumps(safe, ensure_ascii=False)
        assert "Drive Probe User" not in serialized
        assert project_a not in serialized
        assert project_b not in serialized


def test_ordinary_log_carries_only_safe_identity_context_aliases() -> None:
    windows_user = "Context Probe"
    project = windows_user_path(windows_user, "Unity Projects", "Mapped Project")
    context = {
        "projectPath": project,
        "avatarPath": "Scene/Mapped Avatar",
        "avatarName": "Mapped Avatar",
    }
    with TemporaryDirectory() as temp_dir:
        _, manager = make_manager(Path(temp_dir))
        encoded_project = "D%3A%5CUnityWork%5CEncoded%20Project"
        encoded_blueprint = "avtr_TESTONLY_ENCODED123"
        encoded_secret = "TEST_ONLY_ENCODED_QUERY_SECRET"
        entry = manager.emit(
            "info",
            "runtime",
            f"{windows_user} opened Mapped Project with Mapped Avatar",
            {
                "ok": True,
                "thirdPartyError": (
                    f"failed https://example.invalid/?projectPath={encoded_project}"
                    f"&blueprintId={encoded_blueprint}&token={encoded_secret}"
                ),
            },
            context=context,
        )
        assert entry is not None
        identity = entry["data"]["identityContext"]
        assert identity["projectPath"].startswith("prj_")
        assert identity["avatarPath"].startswith("avt_")
        assert identity["avatarName"] == identity["avatarPath"]
        serialized = json.dumps(entry, ensure_ascii=False)
        assert "usr_" in entry["message"]
        assert "prj_" in entry["message"]
        assert "avt_" in entry["message"]
        assert project not in serialized
        assert windows_user not in serialized
        assert "Mapped Project" not in serialized
        assert "Mapped Avatar" not in serialized
        assert encoded_project not in serialized
        assert encoded_blueprint not in serialized
        assert encoded_secret not in serialized


def test_identity_mapping_prunes_after_five_days() -> None:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    clock = MutableClock(start)
    with TemporaryDirectory() as temp_dir:
        config_dir = Path(temp_dir) / "config"
        privacy = DiagnosticPrivacy(config_dir, now_fn=clock)
        privacy.redact({"projectPath": windows_user_path("ProbeUser", "Project")})
        assert privacy.safe_identity_summaries()
        clock.value = start + timedelta(days=6)
        reloaded = DiagnosticPrivacy(config_dir, now_fn=clock)
        manager = DiagnosticLogManager(
            Path(temp_dir) / "logs",
            config_dir / "diagnostics.json",
            reloaded,
            now_fn=clock,
        )
        manager.cleanup()
        mapping = json.loads((config_dir / "diagnostic-identities.json").read_text(encoding="utf-8"))
        assert mapping["records"] == []


def test_low_volume_log_rotates_daily_and_drops_closed_files_older_than_five_days() -> None:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    clock = MutableClock(start)
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _, manager = make_manager(root, clock=clock)
        manager.emit("info", "retention", "day one")
        first = manager.active_path
        assert first is not None
        clock.value = start + timedelta(days=1)
        manager.emit("info", "retention", "day two")
        second = manager.active_path
        assert second is not None and second != first
        assert first.exists() and second.exists()
        clock.value = start + timedelta(days=7)
        manager.emit("info", "retention", "day eight")
        current = manager.active_path
        assert current is not None and current not in {first, second}
        assert not first.exists()
        assert not second.exists()
        assert [path.resolve() for path in (root / "logs").glob("vrcforge_*.log")] == [current.resolve()]


def test_cleanup_enforces_age_count_total_and_never_deletes_active_or_durable_jsonl() -> None:
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    clock = MutableClock(now)
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _, manager = make_manager(root, clock=clock, max_files=3, max_total_bytes=180, max_file_bytes=128)
        manager.update_config(log_level="trace")
        manager.emit("info", "retention", "active")
        active = manager.active_path
        assert active is not None
        old_timestamp = (now - timedelta(days=6)).timestamp()
        os.utime(active, (old_timestamp, old_timestamp))
        logs = root / "logs"
        durable = logs / "agent-goals.jsonl"
        durable.write_text("durable\n", encoding="utf-8")
        legacy_names = ("dashboard.log", "backend_stdout.log", "backend_stderr.log", "interactions.jsonl")
        for name in legacy_names:
            (logs / name).write_text("raw private legacy data\n", encoding="utf-8")
        stale = logs / "vrcforge_2026-07-09_00-00-00_0.log"
        stale.write_text("stale\n", encoding="utf-8")
        os.utime(stale, (old_timestamp, old_timestamp))
        for index in range(8):
            fresh = logs / f"vrcforge_2026-07-16_11-59-{index:02d}_0.log"
            fresh.write_text("x" * 70, encoding="utf-8")

        manager.cleanup()

        remaining = list(logs.glob("vrcforge_*.log"))
        assert active.exists()
        assert not stale.exists()
        assert len(remaining) <= 3
        assert sum(path.stat().st_size for path in remaining) <= 180 or remaining == [active]
        assert durable.read_text(encoding="utf-8") == "durable\n"
        assert all(not (logs / name).exists() for name in legacy_names)


def test_invalid_timestamp_log_name_does_not_break_emit_or_retention() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _, manager = make_manager(root)
        logs = root / "logs"
        logs.mkdir(parents=True)
        invalid = logs / "vrcforge_2026-99-99_00-00-00_0.log"
        invalid.write_text("invalid timestamp file\n", encoding="utf-8")
        old = (datetime.now(timezone.utc) - timedelta(days=6)).timestamp()
        os.utime(invalid, (old, old))
        entry = manager.emit("info", "retention", "normal event")
        assert entry is not None
        assert not invalid.exists()
        assert manager.tail_entries(5)[-1]["message"] == "normal event"


def test_concurrent_writes_remain_complete_and_non_interleaved() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _, manager = make_manager(root)
        manager.update_config(log_level="trace")
        threads = [
            threading.Thread(target=manager.emit, args=("info", "concurrency", f"event-{index}", {"index": index}))
            for index in range(64)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert all(not thread.is_alive() for thread in threads)
        entries = manager.tail_entries(100)
        assert len(entries) == 64
        assert {entry["data"]["index"] for entry in entries} == set(range(64))
        assert all(parse_log_line(format_log_line(entry)) is not None for entry in entries)


def test_parser_round_trips_delimiter_shapes_backslashes_newlines_and_nonfinite_numbers() -> None:
    entry = {
        "timestamp": datetime(2026, 7, 16, tzinfo=timezone.utc).isoformat(),
        "level": "debug",
        "scope": "parser]scope",
        "message": 'message \\ line\nwith | data={"fake":1}',
        "data": {
            "nested": 'evil | data={"q":1}',
            "slash": "a\\b\nc",
            "nan": float("nan"),
            "positive": float("inf"),
            "negative": float("-inf"),
        },
    }
    line = format_log_line(entry)
    assert line.count("\n") == 0
    assert ":NaN" not in line
    assert ":Infinity" not in line
    parsed = parse_log_line(line)
    assert parsed is not None
    assert parsed["scope"] == entry["scope"]
    assert parsed["message"] == entry["message"]
    assert parsed["data"]["nested"] == entry["data"]["nested"]
    assert parsed["data"]["slash"] == entry["data"]["slash"]
    assert parsed["data"]["nan"] == "NaN"
    assert parsed["data"]["positive"] == "Infinity"
    assert parsed["data"]["negative"] == "-Infinity"


def test_redaction_is_idempotent_and_tail_does_not_expand_identity_mapping() -> None:
    project = windows_user_path("Idempotent Probe", "Unity", "Alias Project")
    context = {"projectPath": project, "avatarName": "Alias Avatar", "avatarPath": "Scene/Alias Avatar"}
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        privacy, manager = make_manager(root)
        entry = manager.emit("info", "identity", "mapped", {"projectPath": project}, context=context)
        assert entry is not None
        once = privacy.redact(entry)
        twice = privacy.redact(once)
        assert twice == once
        mapping_path = root / "config" / "diagnostic-identities.json"
        before = json.loads(mapping_path.read_text(encoding="utf-8"))["records"]
        lines = manager.tail_lines(20)
        after = json.loads(mapping_path.read_text(encoding="utf-8"))["records"]
        assert len(after) == len(before)
        assert {row["alias"] for row in after} == {row["alias"] for row in before}
        assert entry["data"]["projectPath"] in lines[0]


def test_logging_io_and_privacy_failures_drop_safely_without_breaking_stream_or_writing_raw() -> None:
    sentinel = "TEST_ONLY_MUST_NOT_REACH_DISK_123"
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        privacy, manager = make_manager(root)
        with patch.object(privacy, "redact", side_effect=OSError("private map unavailable")):
            assert manager.emit("error", "failure", sentinel, {"token": sentinel}) is None
        assert not list((root / "logs").glob("*.log")) if (root / "logs").exists() else True
        assert manager.recent_snapshot() == []

        with patch.object(manager, "_append_line_locked", side_effect=OSError("disk full")):
            assert manager.emit("error", "failure", "safe event", {"token": sentinel}) is None
        disk_content = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in root.rglob("*")
            if path.is_file() and path.name != "diagnostic-alias.key"
        )
        assert sentinel not in disk_content
        assert manager.recent_snapshot() == []

        stream = DiagnosticTextStream(manager, level="error", scope="backend.stderr")
        with patch.object(manager, "emit", side_effect=OSError("unexpected logger error")):
            assert stream.write(sentinel + "\n") == len(sentinel) + 1


def test_status_degrades_when_private_identity_mapping_is_unavailable() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        config = root / "config"
        config.mkdir(parents=True)
        (config / "diagnostic-identities.json").write_text("not valid json", encoding="utf-8")
        privacy, manager = make_manager(root)
        corrupt_status = manager.status()
        assert corrupt_status["ok"] is True
        assert corrupt_status["identities"] == []
        assert corrupt_status["redaction"]["mappingAvailable"] is False
        with patch.object(privacy, "safe_identity_summaries", side_effect=OSError("mapping permission denied")):
            status = manager.status()
        assert status["ok"] is True
        assert status["identities"] == []
        assert status["redaction"]["mappingAvailable"] is False
        assert "permission" not in json.dumps(status).lower()


def test_stderr_prefix_level_detection_respects_error_threshold() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _, manager = make_manager(root)
        manager.update_config(log_level="error")
        stream = DiagnosticTextStream(manager, level="error", scope="backend.stderr", detect_prefixed_level=True)
        stream.write("INFO:     server ready\n")
        stream.write("WARNING:  recoverable\n")
        stream.write("[07/16/26 23:48:59] INFO     StreamableHTTP      streamable_http_manager.py:128\n")
        stream.write("                             session manager\n")
        stream.write("                             started\n")
        stream.write("ERROR:    failed\n")
        stream.write("traceback detail\n")
        messages = [entry["message"] for entry in manager.tail_entries(10)]
        assert "INFO:     server ready" not in messages
        assert "WARNING:  recoverable" not in messages
        assert not any("StreamableHTTP" in message for message in messages)
        assert "                             session manager" not in messages
        assert "                             started" not in messages
        assert "ERROR:    failed" in messages
        assert "traceback detail" in messages


def test_stderr_rich_explicit_level_and_continuations_remain_info_records() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _, manager = make_manager(root)
        manager.update_config(log_level="info")
        stream = DiagnosticTextStream(manager, level="error", scope="backend.stderr", detect_prefixed_level=True)
        stream.write(
            "[07/16/26 23:48:59] INFO     StreamableHTTP      streamable_http_manager.py:128\n"
            "                             session manager\n"
            "                             started\n"
        )
        entries = manager.tail_entries(10)
        assert len(entries) == 3
        assert [entry["level"] for entry in entries] == ["info", "info", "info"]
        assert all(entry["data"].get("baseline") is not True for entry in entries)
        assert all(parse_log_line(line) is not None for line in manager.tail_lines(10))


def test_stderr_continuation_inheritance_requires_indent_and_expires() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _, manager = make_manager(root)
        manager.update_config(log_level="trace")
        stream = DiagnosticTextStream(manager, level="error", scope="backend.stderr", detect_prefixed_level=True)
        with patch("diagnostic_logging.time.monotonic", side_effect=[100.0, 100.1, 101.0, 101.1, 102.0, 104.1]):
            stream.write("[07/16/26 23:48:59] INFO     StreamableHTTP      streamable_http_manager.py:128\n")
            stream.write("                             immediate continuation\n")
            stream.write("INFO:     another record\n")
            stream.write("unprefixed stderr failure\n")
            stream.write("INFO:     final info record\n")
            stream.write("    delayed indented failure\n")
        entries = manager.tail_entries(10)
        assert [entry["level"] for entry in entries] == ["info", "info", "info", "error", "info", "error"]
        assert entries[3]["data"]["baseline"] is True
        assert entries[5]["data"]["baseline"] is True


def test_http_interaction_levels_keep_successful_reads_out_of_debug() -> None:
    with TemporaryDirectory() as temp_dir:
        privacy, manager = make_manager(Path(temp_dir))
        manager.update_config(log_level="debug")
        with (
            patch.object(dashboard_server, "DIAGNOSTIC_PRIVACY", privacy),
            patch.object(dashboard_server, "DIAGNOSTIC_LOGGER", manager),
            patch.object(dashboard_server, "RECENT_LOGS", manager.recent_entries),
            patch.object(dashboard_server.EVENT_BUS, "broadcast_from_sync"),
            patch.object(dashboard_server, "current_diagnostic_identity_context", return_value={}),
        ):
            dashboard_server.record_debug_interaction(
                {"method": "GET", "path": "/poll", "status": 200, "error": ""}
            )
            dashboard_server.record_debug_interaction(
                {"method": "POST", "path": "/write", "status": 200, "error": ""}
            )
            dashboard_server.record_debug_interaction(
                {"method": "GET", "path": "/failed", "status": 500, "error": ""}
            )
            dashboard_server.record_debug_interaction(
                {"method": "GET", "path": "/error", "status": 200, "error": "runtime failure"}
            )
            dashboard_server.record_debug_interaction(
                {"method": "GET", "path": "/bad-request", "status": 404, "error": "not found"}
            )
            assert [entry["data"]["path"] for entry in manager.tail_entries(10)] == [
                "/write",
                "/failed",
                "/error",
                "/bad-request",
            ]
            assert [entry["level"] for entry in manager.tail_entries(10)] == ["debug", "error", "error", "warn"]

            manager.update_config(log_level="trace")
            dashboard_server.record_debug_interaction(
                {"method": "HEAD", "path": "/head-poll", "status": 204, "error": ""}
            )
            dashboard_server.record_debug_interaction(
                {"method": "OPTIONS", "path": "/preflight", "status": 200, "error": ""}
            )
            trace_entries = manager.tail_entries(10)
            assert [entry["level"] for entry in trace_entries[-2:]] == ["trace", "trace"]

            manager.update_config(log_level="error")
            dashboard_server.record_debug_interaction(
                {"method": "GET", "path": "/filtered-read", "status": 200, "error": ""}
            )
            dashboard_server.record_debug_interaction(
                {"method": "POST", "path": "/filtered-write", "status": 200, "error": ""}
            )
            dashboard_server.record_debug_interaction(
                {"method": "GET", "path": "/filtered-4xx", "status": 401, "error": "unauthorized"}
            )
            dashboard_server.record_debug_interaction(
                {"method": "GET", "path": "/kept-5xx", "status": 503, "error": ""}
            )
            dashboard_server.record_debug_interaction(
                {"method": "POST", "path": "/kept-exception", "status": 200, "error": "uncaught"}
            )
        entries = manager.tail_entries(20)
        assert [entry["data"]["path"] for entry in entries[-2:]] == ["/kept-5xx", "/kept-exception"]
        assert all(entry["level"] == "error" for entry in entries[-2:])
        assert all(entry["data"]["baseline"] is True for entry in entries[-2:])
        assert all(entry["message"] == "Direct HTTP interaction." for entry in entries)
        assert all(entry["data"]["kind"] == "direct_http" for entry in entries)


def test_tauri_ipc_transport_requires_exact_hmac_proof_and_never_logs_headers() -> None:
    token = "TEST_ONLY_APP_SESSION_TOKEN_1234567890"

    def make_request(case: str, proof: str = "", marker: str = ""):
        raw_target = f"/api/health?case={case}"
        headers: list[tuple[bytes, bytes]] = []
        if marker:
            headers.append((b"x-vrcforge-transport", marker.encode("ascii")))
        if proof:
            headers.append((b"x-vrcforge-transport-proof", proof.encode("ascii")))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/health",
            "raw_path": b"/api/health",
            "query_string": f"case={case}".encode("ascii"),
            "headers": headers,
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8757),
            "root_path": "",
        }
        return dashboard_server.Request(scope), raw_target

    async def call_next(_request):
        return dashboard_server.JSONResponse({"ok": True}, status_code=200)

    valid_request, valid_target = make_request("valid")
    valid_message = f"vrcforge.tauri-ipc-bridge.v1\nGET\n{valid_target}".encode("utf-8")
    valid_proof = hmac.new(token.encode("utf-8"), valid_message, hashlib.sha256).hexdigest()
    valid_request, _ = make_request("valid", valid_proof, "tauri-ipc-bridge")
    forged_request, _ = make_request("forged", "0" * 64, "tauri-ipc-bridge")
    direct_request, _ = make_request("direct")

    with TemporaryDirectory() as temp_dir:
        privacy, manager = make_manager(Path(temp_dir))
        manager.update_config(log_level="trace")
        with (
            patch.object(dashboard_server, "APP_SESSION_TOKEN", token),
            patch.object(dashboard_server, "DIAGNOSTIC_PRIVACY", privacy),
            patch.object(dashboard_server, "DIAGNOSTIC_LOGGER", manager),
            patch.object(dashboard_server, "RECENT_LOGS", manager.recent_entries),
            patch.object(dashboard_server.EVENT_BUS, "broadcast_from_sync"),
            patch.object(dashboard_server, "current_diagnostic_identity_context", return_value={}),
        ):
            asyncio.run(dashboard_server.authorize_local_requests(valid_request, call_next))
            asyncio.run(dashboard_server.authorize_local_requests(forged_request, call_next))
            asyncio.run(dashboard_server.authorize_local_requests(direct_request, call_next))

        interactions = {
            entry["data"]["query"]["case"]: entry
            for entry in manager.tail_entries(20)
            if entry["scope"] in {"ipc", "http"} and "query" in entry["data"]
        }
        assert interactions["valid"]["message"] == "Tauri IPC bridge interaction."
        assert interactions["valid"]["scope"] == "ipc"
        assert interactions["valid"]["data"]["component"] == "ipc"
        assert interactions["valid"]["data"]["kind"] == "tauri_ipc_bridge"
        for case in ("forged", "direct"):
            assert interactions[case]["message"] == "Direct HTTP interaction."
            assert interactions[case]["scope"] == "http"
            assert interactions[case]["data"]["component"] == "http"
            assert interactions[case]["data"]["kind"] == "direct_http"
        log_content = "\n".join(
            path.read_text(encoding="utf-8") for path in (Path(temp_dir) / "logs").glob("vrcforge_*.log")
        )
        assert token not in log_content
        assert valid_proof not in log_content
        assert "x-vrcforge-transport" not in log_content.lower()


def test_developer_options_guard_exact_wait_cancel_and_single_use() -> None:
    clock = MutableClock(100.0)
    ids = iter(("challenge_AAAAAAAAAAAAAAAAAAAAAAAA", "challenge_BBBBBBBBBBBBBBBBBBBBBBBB"))
    guard = DeveloperOptionsGuard(clock=clock, id_factory=lambda: next(ids))
    first = guard.create()
    challenge_id = str(first["challengeId"])
    assert first["waitMs"] == 5_000
    clock.value = 104.999
    with pytest.raises(DeveloperOptionsChallengeError):
        guard.consume(challenge_id)
    clock.value = 105.0
    guard.consume(challenge_id)
    with pytest.raises(DeveloperOptionsChallengeError):
        guard.consume(challenge_id)

    second = guard.create()
    assert guard.cancel(str(second["challengeId"]))
    clock.value = 200.0
    with pytest.raises(DeveloperOptionsChallengeError):
        guard.consume(str(second["challengeId"]))
    assert not guard.cancel("bad/path")


def test_trace_gate_dynamic_levels_and_atomic_disable_downgrade() -> None:
    advanced = {
        "developerOptionsEnabled": False,
        "developerOptionsEverEnabled": False,
        "computerUseEnabled": False,
        "computerUseEverEnabled": False,
    }
    permission = {
        "executionMode": "approval",
        "perActionApproval": True,
        "allowWriteRequests": True,
    }

    def update_advanced(*, developer_options_enabled: bool, computer_use_enabled: bool):
        advanced["developerOptionsEnabled"] = developer_options_enabled
        advanced["developerOptionsEverEnabled"] = advanced["developerOptionsEverEnabled"] or developer_options_enabled
        advanced["computerUseEnabled"] = computer_use_enabled and developer_options_enabled
        advanced["computerUseEverEnabled"] = advanced["computerUseEverEnabled"] or advanced["computerUseEnabled"]
        return {"ok": True, "settings": dict(advanced)}

    with TemporaryDirectory() as temp_dir:
        privacy, manager = make_manager(Path(temp_dir))
        with (
            patch.object(dashboard_server, "DIAGNOSTIC_PRIVACY", privacy),
            patch.object(dashboard_server, "DIAGNOSTIC_LOGGER", manager),
            patch.object(dashboard_server, "RECENT_LOGS", manager.recent_entries),
            patch.object(dashboard_server.AGENT_GATEWAY, "permission_state", return_value=permission),
            patch.object(dashboard_server.AGENT_GATEWAY, "advanced_settings_state", side_effect=lambda: dict(advanced)),
            patch.object(dashboard_server.AGENT_GATEWAY, "update_advanced_settings", side_effect=update_advanced),
            patch.object(dashboard_server.EVENT_BUS, "broadcast", new=AsyncMock()),
            patch.object(dashboard_server.EVENT_BUS, "broadcast_from_sync"),
            patch.object(dashboard_server, "current_diagnostic_identity_context", return_value={}),
        ):
            state = dashboard_server.diagnostics_state()
            assert state["logLevels"] == ["error", "warn", "info", "debug"]
            assert state["traceRequiresDeveloperOptions"] is True
            assert state["safetyPosture"]["diagnostics"]["traceAllowed"] is False
            with pytest.raises(HTTPException) as blocked:
                asyncio.run(
                    dashboard_server.update_app_diagnostics(
                        dashboard_server.DiagnosticsConfigRequest(logLevel="trace")
                    )
                )
            assert blocked.value.status_code == 409
            assert manager.log_level == "info"

            advanced["developerOptionsEnabled"] = True
            advanced["developerOptionsEverEnabled"] = True
            enabled = asyncio.run(
                dashboard_server.update_app_diagnostics(
                    dashboard_server.DiagnosticsConfigRequest(logLevel="trace")
                )
            )
            assert enabled["logLevel"] == "trace"
            assert enabled["logLevels"] == ["error", "warn", "info", "debug", "trace"]

            with patch.object(
                dashboard_server.AGENT_GATEWAY,
                "update_advanced_settings",
                side_effect=RuntimeError("persist failed"),
            ):
                with pytest.raises(RuntimeError):
                    dashboard_server.update_agentic_app_advanced_settings_guarded(
                        dashboard_server.AdvancedSettingsRequest(
                            developerOptionsEnabled=False,
                            computerUseEnabled=False,
                        )
                    )
            assert advanced["developerOptionsEnabled"] is True
            assert manager.log_level == "trace"

            disabled = dashboard_server.update_agentic_app_advanced_settings_guarded(
                dashboard_server.AdvancedSettingsRequest(
                    developerOptionsEnabled=False,
                    computerUseEnabled=False,
                )
            )
            assert disabled["settings"]["developerOptionsEnabled"] is False
            assert manager.log_level == "debug"
            assert dashboard_server.diagnostics_state()["logLevels"] == ["error", "warn", "info", "debug"]


def test_permission_and_advanced_flag_changes_emit_safe_essential_security_evidence() -> None:
    challenge_id = "challenge_EEEEEEEEEEEEEEEEEEEEEEEE"
    clock = MutableClock(10.0)
    guard = DeveloperOptionsGuard(clock=clock, id_factory=lambda: challenge_id)
    guard.create()
    clock.value = 15.0
    permission = {
        "executionMode": "approval",
        "perActionApproval": True,
        "autoApprove": False,
        "autoApproveDangerousRequiresApproval": False,
        "roslynRiskAcknowledged": False,
        "allowWriteRequests": True,
    }
    advanced = {
        "developerOptionsEnabled": False,
        "developerOptionsEverEnabled": False,
        "computerUseEnabled": False,
        "computerUseEverEnabled": False,
    }

    def update_permission(execution_mode: str, acknowledge_roslyn_risk: bool = False):
        permission.update(
            {
                "executionMode": execution_mode,
                "perActionApproval": False,
                "autoApprove": True,
                "roslynRiskAcknowledged": acknowledge_roslyn_risk,
                "roslynFullAutoEverEnabled": acknowledge_roslyn_risk,
            }
        )
        return {"ok": True, "permission": dict(permission)}

    def update_advanced(*, developer_options_enabled: bool, computer_use_enabled: bool):
        advanced.update(
            {
                "developerOptionsEnabled": developer_options_enabled,
                "developerOptionsEverEnabled": developer_options_enabled,
                "computerUseEnabled": computer_use_enabled and developer_options_enabled,
                "computerUseEverEnabled": computer_use_enabled and developer_options_enabled,
            }
        )
        return {"ok": True, "settings": dict(advanced)}

    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        privacy, manager = make_manager(root)
        manager.update_config(log_level="error")
        with (
            patch.object(dashboard_server, "DEVELOPER_OPTIONS_GUARD", guard),
            patch.object(dashboard_server, "DIAGNOSTIC_PRIVACY", privacy),
            patch.object(dashboard_server, "DIAGNOSTIC_LOGGER", manager),
            patch.object(dashboard_server, "RECENT_LOGS", manager.recent_entries),
            patch.object(dashboard_server.AGENT_GATEWAY, "permission_state", side_effect=lambda: dict(permission)),
            patch.object(dashboard_server.AGENT_GATEWAY, "update_permission_state", side_effect=update_permission),
            patch.object(dashboard_server.AGENT_GATEWAY, "advanced_settings_state", side_effect=lambda: dict(advanced)),
            patch.object(dashboard_server.AGENT_GATEWAY, "update_advanced_settings", side_effect=update_advanced),
            patch.object(dashboard_server.EVENT_BUS, "broadcast", new=AsyncMock()),
            patch.object(dashboard_server.EVENT_BUS, "broadcast_from_sync"),
            patch.object(dashboard_server, "current_diagnostic_identity_context", return_value={}),
        ):
            asyncio.run(
                dashboard_server.update_agentic_app_permission(
                    dashboard_server.AgentPermissionRequest(
                        execution_mode="roslyn_full_auto",
                        acknowledge_roslyn_risk=True,
                    )
                )
            )
            dashboard_server.update_agentic_app_advanced_settings_guarded(
                dashboard_server.AdvancedSettingsRequest(
                    developerOptionsEnabled=True,
                    computerUseEnabled=True,
                    developerChallengeId=challenge_id,
                )
            )

        entries = [entry for entry in manager.tail_entries(20) if entry["scope"] == "security"]
        assert [entry["message"] for entry in entries] == [
            "Permission safety flags changed.",
            "Advanced safety flags changed.",
        ]
        assert all(entry["level"] == "info" for entry in entries)
        assert all(entry["data"]["baseline"] is True for entry in entries)
        assert all(entry["data"]["source"] == "app_settings_api" for entry in entries)
        assert all(entry["data"]["strongConfirmationCompleted"] is True for entry in entries)
        assert entries[0]["data"]["before"]["executionMode"] == "approval"
        assert entries[0]["data"]["after"]["executionMode"] == "roslyn_full_auto"
        assert entries[0]["data"]["before"]["perActionApproval"] is True
        assert entries[0]["data"]["after"]["autoApprove"] is True
        assert entries[0]["data"]["after"]["autoApproveDangerousRequiresApproval"] is False
        assert entries[1]["data"]["before"]["developerOptionsEnabled"] is False
        assert entries[1]["data"]["after"]["computerUseEnabled"] is True
        content = "\n".join(
            path.read_text(encoding="utf-8") for path in (root / "logs").glob("vrcforge_*.log")
        )
        assert challenge_id not in content
        assert "developerChallengeId" not in content
        assert "proof" not in content.lower()
        assert "token" not in content.lower()


def test_support_bundle_forces_redaction_and_excludes_identity_mapping() -> None:
    user = "BundleProbe"
    project = windows_user_path(user, "Unity Projects", "Bundle Avatar")
    blueprint = "avtr_TESTONLY_BUNDLE123"
    secret = "TEST_ONLY_BUNDLE_SECRET_456"
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        privacy, manager = make_manager(root)
        manager.update_config(log_level="trace")
        privacy.redact({"projectPath": project, "avatarName": "Bundle Avatar", "blueprintId": blueprint})
        identities = privacy.safe_identity_summaries()
        assert identities
        manager.emit(
            "info",
            "support",
            "mapped support event",
            {"projectPath": project, "blueprintId": blueprint},
            context={"projectPath": project, "avatarName": "Bundle Avatar", "blueprintId": blueprint},
        )
        manager.emit(
            "debug",
            "http",
            "HTTP interaction.",
            {
                "query": {
                    "api_key": secret,
                    "authorization": f"Bearer {secret}",
                    "projectPath": project,
                    "blueprintId": blueprint,
                }
            },
            context={"projectPath": project, "avatarName": "Bundle Avatar", "blueprintId": blueprint},
        )
        identity_aliases_before = {
            row["alias"]
            for row in json.loads(privacy.mapping_path.read_text(encoding="utf-8"))["records"]
            if row["kind"] in {"project", "avatar"}
        }
        support_dir = root / "support"
        with (
            patch.object(dashboard_server, "DIAGNOSTIC_PRIVACY", privacy),
            patch.object(dashboard_server, "DIAGNOSTIC_LOGGER", manager),
            patch.object(dashboard_server, "SUPPORT_BUNDLE_DIR", support_dir),
            patch.object(dashboard_server, "current_diagnostic_identity_context", return_value={}),
            patch.object(
                dashboard_server,
                "build_agentic_app_bootstrap_payload",
                return_value={"projectPath": project, "api_key": secret, "blueprintId": blueprint},
            ),
            patch.object(dashboard_server, "read_agentic_app_doctor", return_value={"detail": project}),
            patch.object(
                dashboard_server.AGENT_GATEWAY,
                "permission_state",
                return_value={
                    "executionMode": "approval",
                    "perActionApproval": True,
                    "allowWriteRequests": True,
                    "roslynRiskAcknowledged": False,
                },
            ),
            patch.object(
                dashboard_server.AGENT_GATEWAY,
                "advanced_settings_state",
                return_value={
                    "developerOptionsEnabled": True,
                    "developerOptionsEverEnabled": True,
                    "computerUseEnabled": False,
                    "computerUseEverEnabled": True,
                },
            ),
            patch.object(dashboard_server.AGENT_GATEWAY, "list_checkpoints", return_value={"items": []}),
            patch.object(dashboard_server.AGENT_GATEWAY, "recent_audit_logs", return_value=[]),
            patch.object(dashboard_server.SUB_AGENT_REGISTRY, "recent_events", return_value=[]),
            patch.object(dashboard_server.SUB_AGENT_REGISTRY, "list_tasks", return_value={"tasks": []}),
        ):
            result = dashboard_server.build_support_bundle(
                dashboard_server.SupportBundleRequest(includeFullPaths=True, logLimit=20)
            )

        assert result["redacted"] is True
        bundle_path = Path(result["bundlePath"])
        with zipfile.ZipFile(bundle_path) as bundle:
            names = set(bundle.namelist())
            content = "\n".join(bundle.read(name).decode("utf-8") for name in names)
            diagnostics = json.loads(bundle.read("diagnostics.json"))
            safety_posture = json.loads(bundle.read("safety-posture.json"))
            diagnostic_log = bundle.read("diagnostic-log.txt").decode("utf-8")
        assert "diagnostic-log.txt" in names
        assert "safety-posture.json" in names
        assert "dashboard-log.json" not in names
        assert "interaction-log.json" not in names
        assert "backend-stdout-tail.json" not in names
        assert "backend-stderr-tail.json" not in names
        assert "identities" not in diagnostics
        assert diagnostics["schema"] == "vrcforge.diagnostics.v2"
        assert diagnostics["safetyPosture"] == safety_posture
        assert safety_posture["schema"] == SAFETY_POSTURE_SCHEMA
        assert safety_posture["externalAgent"]["requestOnly"] is True
        assert safety_posture["checkpoint"]["requiredBeforeOrdinaryWrite"] is True
        assert safety_posture["checkpoint"]["restoreAndRecoveryExempt"] is True
        assert safety_posture["rollback"]["approvalRecordRequired"] is True
        assert safety_posture["rollback"]["manualApprovalRequired"] is True
        assert safety_posture["rollback"]["restoreMayAutoExecute"] is False
        assert safety_posture["externalAgent"]["directApplyExposed"] is False
        assert safety_posture["externalAgent"]["requestMayAutoExecute"] is False
        assert safety_posture["externalAgent"]["selectedMode"] == "approval"
        assert safety_posture["fullPermission"]["canOverrideExplicitApproval"] is False
        assert safety_posture["developerOptions"]["everEnabled"] is True
        assert safety_posture["computerUse"]["everEnabled"] is True
        assert safety_posture["diagnostics"]["currentLevel"] == "trace"
        assert safety_posture["diagnostics"]["traceRequiresDeveloperOptions"] is True
        for sentinel in (project, blueprint, secret, user, "Bundle Avatar"):
            assert sentinel not in content
        assert "diagnostic-alias.key" not in content
        assert "diagnostic-identities.json" not in content
        assert "vrcforge.diagnostic-identities.v1" not in content
        assert any(alias in content for alias in identity_aliases_before)
        parsed_log_lines = [parse_log_line(line) for line in diagnostic_log.splitlines() if line.strip()]
        assert parsed_log_lines
        assert all(line is not None for line in parsed_log_lines)
        http_line = next(line for line in parsed_log_lines if line and line["scope"] == "http")
        assert http_line["data"]["query"]["api_key"] == "[REDACTED]"
        assert http_line["data"]["query"]["authorization"] == "[REDACTED]"
        identity_aliases_after = {
            row["alias"]
            for row in json.loads(privacy.mapping_path.read_text(encoding="utf-8"))["records"]
            if row["kind"] in {"project", "avatar"}
        }
        assert identity_aliases_after == identity_aliases_before


def test_recent_unity_error_reads_new_text_parser_and_safe_memory() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        privacy, manager = make_manager(root)
        manager.emit(
            "error",
            "unity-mcp",
            "Unity MCP disconnected while awaiting command_result",
            {"projectPath": windows_user_path("ProbeUser", "Project")},
        )
        with (
            patch.object(dashboard_server, "DIAGNOSTIC_PRIVACY", privacy),
            patch.object(dashboard_server, "DIAGNOSTIC_LOGGER", manager),
            patch.object(dashboard_server, "RECENT_LOGS", manager.recent_entries),
        ):
            result = dashboard_server.recent_unity_mcp_execution_error()
        assert result["level"] == "error"
        assert "disconnected while awaiting command_result" in result["message"]
        assert "prj_" in json.dumps(result)


def test_developer_challenge_rest_contract_uses_no_body_delete_and_server_guard() -> None:
    clock = MutableClock(10.0)
    ids = iter(("challenge_CCCCCCCCCCCCCCCCCCCCCCCC", "challenge_DDDDDDDDDDDDDDDDDDDDDDDD"))
    guard = DeveloperOptionsGuard(clock=clock, id_factory=lambda: next(ids))
    state = {"developerOptionsEnabled": False, "computerUseEnabled": False}

    def update_settings(*, developer_options_enabled: bool, computer_use_enabled: bool):
        state["developerOptionsEnabled"] = developer_options_enabled
        state["computerUseEnabled"] = computer_use_enabled
        return {"ok": True, "settings": dict(state)}

    with TemporaryDirectory() as temp_dir:
        privacy, manager = make_manager(Path(temp_dir))
        manager.update_config(log_level="debug")
        issued_ids: list[str] = []
        with (
            patch.object(dashboard_server, "DEVELOPER_OPTIONS_GUARD", guard),
            patch.object(dashboard_server, "DIAGNOSTIC_PRIVACY", privacy),
            patch.object(dashboard_server, "DIAGNOSTIC_LOGGER", manager),
            patch.object(dashboard_server, "RECENT_LOGS", manager.recent_entries),
            patch.object(dashboard_server.AGENT_GATEWAY, "advanced_settings_state", side_effect=lambda: dict(state)),
            patch.object(dashboard_server.AGENT_GATEWAY, "update_advanced_settings", side_effect=update_settings),
            patch.object(dashboard_server, "desktop_executor_enabled", return_value=False),
        ):
            with TestClient(dashboard_server.app) as client:
                created = client.post("/api/app/advanced-settings/developer-challenge")
                assert created.status_code == 200
                challenge_id = created.json()["challengeId"]
                issued_ids.append(challenge_id)
                early = client.post(
                    "/api/app/advanced-settings",
                    json={
                        "developerOptionsEnabled": True,
                        "computerUseEnabled": False,
                        "developerChallengeId": challenge_id,
                    },
                )
                assert early.status_code == 409
                clock.value = 15.0
                accepted = client.post(
                    "/api/app/advanced-settings",
                    json={
                        "developerOptionsEnabled": True,
                        "computerUseEnabled": False,
                        "developerChallengeId": challenge_id,
                    },
                )
                assert accepted.status_code == 200
                state["developerOptionsEnabled"] = False
                replay = client.post(
                    "/api/app/advanced-settings",
                    json={
                        "developerOptionsEnabled": True,
                        "computerUseEnabled": False,
                        "developerChallengeId": challenge_id,
                    },
                )
                assert replay.status_code == 409

                created_cancel = client.post("/api/app/advanced-settings/developer-challenge").json()
                issued_ids.append(created_cancel["challengeId"])
                cancelled = client.delete(
                    f"/api/app/advanced-settings/developer-challenge/{created_cancel['challengeId']}"
                )
                assert cancelled.status_code == 200
                assert cancelled.json()["cancelled"] is True
                cancelled_replay = client.post(
                    "/api/app/advanced-settings",
                    json={
                        "developerOptionsEnabled": True,
                        "computerUseEnabled": False,
                        "developerChallengeId": created_cancel["challengeId"],
                    },
                )
                assert cancelled_replay.status_code == 409
                invalid_delete = client.delete("/api/app/advanced-settings/developer-challenge/bad-path")
                assert invalid_delete.status_code == 404

        log_content = "\n".join(
            path.read_text(encoding="utf-8") for path in (Path(temp_dir) / "logs").glob("vrcforge_*.log")
        )
        assert issued_ids
        assert all(challenge_id not in log_content for challenge_id in issued_ids)
        assert "/developer-challenge/[REDACTED]" in log_content

    routes = {
        (route.path, method)
        for route in dashboard_server.app.routes
        for method in getattr(route, "methods", set())
    }
    assert ("/api/app/advanced-settings/developer-challenge", "POST") in routes
    assert ("/api/app/advanced-settings/developer-challenge/{challenge_id}", "DELETE") in routes


def test_advanced_settings_route_rejects_missing_challenge_without_mutating_gateway() -> None:
    state = {"developerOptionsEnabled": False, "computerUseEnabled": False}
    with (
        patch.object(dashboard_server.AGENT_GATEWAY, "advanced_settings_state", return_value=state),
        patch.object(dashboard_server.AGENT_GATEWAY, "update_advanced_settings") as update,
    ):
        with pytest.raises(HTTPException) as rejected:
            asyncio.run(
                dashboard_server.update_agentic_app_advanced_settings(
                    dashboard_server.AdvancedSettingsRequest(
                        developerOptionsEnabled=True,
                        computerUseEnabled=False,
                    )
                )
            )
    assert rejected.value.status_code == 409
    update.assert_not_called()


def test_advanced_settings_transition_lock_prevents_disable_enable_challenge_bypass() -> None:
    state = {"developerOptionsEnabled": True, "computerUseEnabled": False}
    disable_started = threading.Event()
    allow_disable_to_finish = threading.Event()
    failures: list[Exception] = []

    def update_settings(*, developer_options_enabled: bool, computer_use_enabled: bool):
        if not developer_options_enabled:
            disable_started.set()
            assert allow_disable_to_finish.wait(timeout=5)
        state["developerOptionsEnabled"] = developer_options_enabled
        state["computerUseEnabled"] = computer_use_enabled
        return {"ok": True, "settings": dict(state)}

    disable_request = dashboard_server.AdvancedSettingsRequest(
        developerOptionsEnabled=False,
        computerUseEnabled=False,
    )
    unchallenged_enable = dashboard_server.AdvancedSettingsRequest(
        developerOptionsEnabled=True,
        computerUseEnabled=False,
    )

    def run(request: dashboard_server.AdvancedSettingsRequest) -> None:
        try:
            dashboard_server.update_agentic_app_advanced_settings_guarded(request)
        except Exception as exc:  # noqa: BLE001 - asserted below.
            failures.append(exc)

    with (
        patch.object(dashboard_server.AGENT_GATEWAY, "advanced_settings_state", side_effect=lambda: dict(state)),
        patch.object(dashboard_server.AGENT_GATEWAY, "update_advanced_settings", side_effect=update_settings),
    ):
        disabling = threading.Thread(target=run, args=(disable_request,))
        enabling = threading.Thread(target=run, args=(unchallenged_enable,))
        disabling.start()
        assert disable_started.wait(timeout=5)
        enabling.start()
        assert enabling.is_alive()
        allow_disable_to_finish.set()
        disabling.join(timeout=5)
        enabling.join(timeout=5)

    assert not disabling.is_alive()
    assert not enabling.is_alive()
    assert state["developerOptionsEnabled"] is False
    assert len(failures) == 1
    assert isinstance(failures[0], HTTPException)
    assert failures[0].status_code == 409
