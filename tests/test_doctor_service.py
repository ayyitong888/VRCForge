import hashlib
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import doctor_service
from doctor_service import DoctorRule, DoctorService, DoctorServiceError


class DoctorServiceContractTests(unittest.TestCase):
    def test_builtin_registry_is_static_and_contains_first_wave_rules(self) -> None:
        expected = {
            "app.config",
            "doctor.port",
            "desktop.install_integrity",
            "security.external_writes",
            "security.bind_auth",
            "security.mcp_exposure",
            "security.process_exec",
        }
        self.assertEqual(set(doctor_service.DOCTOR_RULE_REGISTRY), expected)
        with self.assertRaises(TypeError):
            doctor_service.DOCTOR_RULE_REGISTRY["extra"] = object()  # type: ignore[index]

    def test_context_factory_is_refreshed_and_callback_rules_can_be_registered(self) -> None:
        state = {"healthy": False, "calls": 0}

        def context_factory() -> dict:
            state["calls"] += 1
            return {"state": state}

        def detect(context: dict) -> dict:
            return {
                "status": "ok" if context["state"]["healthy"] else "warning",
                "message": "ready" if context["state"]["healthy"] else "needs repair",
                "detail": {"healthy": context["state"]["healthy"]},
            }

        def repair(context: dict, mode: str, phases: doctor_service.PhaseLog) -> dict:
            self.assertEqual(mode, "safe")
            phases.add("apply", "ok", "callback applied")
            context["state"]["healthy"] = True
            return {"status": "repaired", "changed": True}

        service = DoctorService(context_factory, rules=[])
        service.register_rule(DoctorRule("checkpoint.backend", "Writes", "Checkpoint", detect, repair))
        result = service.fix("checkpoint.backend")

        self.assertEqual(result["schema"], "vrcforge.doctor_fix.v1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "repaired")
        self.assertTrue(result["changed"])
        self.assertEqual(result["before"]["status"], "warning")
        self.assertEqual(result["after"]["status"], "ok")
        self.assertGreaterEqual(state["calls"], 2)
        datetime.fromisoformat(result["generatedAt"])
        datetime.fromisoformat(result["phases"][0]["timestamp"])

        second = service.fix("checkpoint.backend")
        self.assertEqual(second["status"], "healthy")
        self.assertFalse(second["changed"])
        self.assertEqual(second["phases"][0]["id"], "already_healthy")

    def test_unknown_nonfixable_and_invalid_mode_have_transport_neutral_statuses(self) -> None:
        service = DoctorService({"doctor_port": {"listeners": [], "can_bind": lambda _host, _port: True}})
        with self.assertRaises(DoctorServiceError) as unknown:
            service.detect("missing.rule")
        self.assertEqual(unknown.exception.status_code, 404)

        with self.assertRaises(DoctorServiceError) as read_only:
            service.fix("doctor.port")
        self.assertEqual(read_only.exception.status_code, 409)

        with self.assertRaises(DoctorServiceError) as invalid_mode:
            service.fix("app.config", "destroy")
        self.assertEqual(invalid_mode.exception.status_code, 422)

    def test_single_flight_returns_busy_without_running_second_callback(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def detect(_context: dict) -> dict:
            return {"status": "warning", "message": "waiting", "detail": {}}

        def repair(_context: dict, _mode: str, phases: doctor_service.PhaseLog) -> dict:
            calls.append("repair")
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            phases.add("done", "ok", "done")
            return {"status": "repaired", "changed": True}

        service = DoctorService({}, rules=[DoctorRule("session.store", "Runtime", "Session store", detect, repair)])
        first_result: list[dict] = []
        worker = threading.Thread(target=lambda: first_result.append(service.fix("session.store")), daemon=True)
        worker.start()
        self.assertTrue(entered.wait(timeout=5))
        busy = service.fix("session.store", "force")
        release.set()
        worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(calls, ["repair"])
        self.assertEqual(busy["status"], "busy")
        self.assertFalse(busy["changed"])
        self.assertEqual(busy["phases"][0]["id"], "single_flight")
        self.assertEqual(first_result[0]["status"], "repaired")

    def test_callback_output_and_phases_are_redacted(self) -> None:
        secret = "doctor-secret-sentinel"
        local_path = r"C:\Users\example\private\config.json"
        credential_url = "https://alice:cleartext@example.invalid/v1?token=query-secret"

        def detect(_context: dict) -> dict:
            return {
                "status": "warning",
                "message": f"remote failed at {credential_url} afterwards",
                "detail": {
                    "token": secret,
                    "fingerprint": "abcdef",
                    "configPath": local_path,
                    "safeFlag": True,
                },
            }

        def repair(_context: dict, _mode: str, phases: doctor_service.PhaseLog) -> dict:
            phases.add(
                "repair",
                "error",
                f"failed with Bearer {secret} token=another-secret fingerprint={'a' * 64} at {local_path}",
                {"rawContent": secret},
            )
            return {"status": "failed", "changed": False}

        service = DoctorService({}, rules=[DoctorRule("app.secret_test", "App", "Secret", detect, repair)])
        serialized = json.dumps(service.fix("app.secret_test"), ensure_ascii=False)
        self.assertNotIn(secret, serialized)
        self.assertNotIn(local_path, serialized)
        self.assertNotIn("abcdef", serialized)
        self.assertNotIn("another-secret", serialized)
        self.assertNotIn("a" * 64, serialized)
        self.assertNotIn("alice", serialized)
        self.assertNotIn("cleartext", serialized)
        self.assertNotIn("query-secret", serialized)
        self.assertIn("https://example.invalid", serialized)
        self.assertIn("[redacted]", serialized)

    def test_invalid_callback_result_is_a_structured_failure(self) -> None:
        rule = DoctorRule(
            "session.invalid_callback",
            "Runtime",
            "Invalid callback",
            lambda _context: {"status": "warning", "message": "needs repair", "detail": {}},
            lambda _context, _mode, _phases: None,  # type: ignore[arg-type,return-value]
        )
        result = DoctorService({}, rules=[rule]).fix("session.invalid_callback")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["ok"])
        self.assertEqual(result["phases"][-1]["id"], "repair_contract")


class AppConfigDoctorRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_path = self.root / "config.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def service(self) -> DoctorService:
        return DoctorService({"app_config": {"path": self.config_path}})

    def test_legacy_config_is_backed_up_canonicalized_and_idempotent(self) -> None:
        original_payload = {
            "provider": "openai",
            "apiKey": "private-config-key-sentinel",
            "baseUrl": "https://example.invalid/v1",
            "model": "gpt-test",
            "thinkingLevel": "high",
            "vision": {"provider": "openai", "apiKey": "vision-private", "unknown": "preserve"},
            "unknownTop": {"private": "preserve"},
        }
        original = json.dumps(original_payload, separators=(",", ":")).encode("utf-8")
        self.config_path.write_bytes(original)
        service = self.service()

        detected = service.detect("app.config")
        self.assertEqual(detected["status"], "warning")
        self.assertGreater(detected["detail"]["legacyKeyCount"], 0)
        self.assertEqual(detected["detail"]["unknownTopLevelCount"], 1)

        result = service.fix("app.config", "safe")
        self.assertEqual(result["status"], "repaired")
        self.assertTrue(result["changed"])
        canonical = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(
            canonical,
            {
                "api": {
                    "provider": "openai",
                    "api_key": "private-config-key-sentinel",
                    "base_url": "https://example.invalid/v1",
                    "model": "gpt-test",
                    "thinking_level": "high",
                },
                "vision": {
                    "unknown": "preserve",
                    "provider": "openai",
                    "api_key": "vision-private",
                },
                "unknownTop": {"private": "preserve"},
            },
        )
        digest = hashlib.sha256(original).hexdigest()
        backup = self.config_path.with_name(f"config.json.backup-{digest}.bak")
        self.assertEqual(backup.read_bytes(), original)
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("private-config-key-sentinel", serialized)
        self.assertNotIn(str(self.config_path), serialized)
        self.assertNotIn(digest, serialized)

        second = service.fix("app.config", "force")
        self.assertEqual(second["status"], "healthy")
        self.assertFalse(second["changed"])
        self.assertEqual(len(list(self.root.glob("*.bak"))), 1)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_semantically_canonical_config_is_not_rewritten_for_formatting(self) -> None:
        original = b'{"api":{"provider":"openai"},"future":{"enabled":true}}'
        self.config_path.write_bytes(original)

        detected = self.service().detect("app.config")
        result = self.service().fix("app.config", "safe")

        self.assertEqual(detected["status"], "ok")
        self.assertEqual(result["status"], "healthy")
        self.assertFalse(result["changed"])
        self.assertEqual(self.config_path.read_bytes(), original)
        self.assertFalse(list(self.root.glob("*.bak")))

    def test_invalid_json_is_preserved_in_safe_and_force_modes(self) -> None:
        invalid = b'{"api":{"api_key":"private"}'
        self.config_path.write_bytes(invalid)
        service = self.service()

        detected = service.detect("app.config")
        self.assertEqual(detected["status"], "error")
        for mode in ("safe", "force"):
            result = service.fix("app.config", mode)
            self.assertEqual(result["status"], "needs_user_action")
            self.assertFalse(result["changed"])
            self.assertEqual(self.config_path.read_bytes(), invalid)
        self.assertFalse(list(self.root.glob("*.bak")))
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_non_object_and_ambiguous_sections_are_not_replaced(self) -> None:
        for payload in (
            ["not", "an", "object"],
            {"api": "not-an-object"},
            {"api": {"api_key": "one", "apiKey": "two"}},
            {"api": {"provider": "openai"}, "provider": "anthropic"},
        ):
            original = json.dumps(payload).encode("utf-8")
            self.config_path.write_bytes(original)
            result = self.service().fix("app.config", "force")
            self.assertEqual(result["status"], "needs_user_action")
            self.assertEqual(self.config_path.read_bytes(), original)

    def test_backup_failure_aborts_before_configuration_write(self) -> None:
        original = b'{"provider":"openai","unknown":true}'
        self.config_path.write_bytes(original)
        with patch("doctor_service._write_content_addressed_backup", side_effect=OSError("disk full")):
            result = self.service().fix("app.config")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["changed"])
        self.assertEqual(self.config_path.read_bytes(), original)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_compare_and_swap_refuses_to_overwrite_a_concurrent_save(self) -> None:
        original = b'{"provider":"openai"}'
        concurrent = b'{"api":{"provider":"anthropic"}}\n'
        self.config_path.write_bytes(original)
        real_backup = doctor_service._write_content_addressed_backup

        def backup_then_change(path: Path, content: bytes) -> bool:
            created = real_backup(path, content)
            path.write_bytes(concurrent)
            return created

        with patch("doctor_service._write_content_addressed_backup", side_effect=backup_then_change):
            result = self.service().fix("app.config")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["changed"])
        self.assertEqual(self.config_path.read_bytes(), concurrent)
        self.assertEqual(result["phases"][-1]["status"], "warning")


class ReadOnlyDoctorRuleTests(unittest.TestCase):
    def test_port_rule_recognizes_current_shared_listener_without_exposing_process_paths(self) -> None:
        current_pid = os.getpid()
        service = DoctorService(
            {
                "doctor_port": {
                    "host": "127.0.0.1",
                    "port": 8757,
                    "gateway_url": "http://127.0.0.1:8757",
                    "current_pid": current_pid,
                    "owner_lease_owned": True,
                    "listeners": [
                        {
                            "port": 8757,
                            "pid": current_pid,
                            "name": "vrcforge_backend.exe",
                            "exe": r"C:\private\vrcforge_backend.exe",
                            "cmdline": ["--token", "private"],
                        }
                    ],
                }
            }
        )
        result = service.detect("doctor.port")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["detail"]["sharedListener"])
        self.assertEqual(result["detail"]["listeners"][0]["state"], "owned")
        serialized = json.dumps(result)
        self.assertNotIn("C:\\private", serialized)
        self.assertNotIn("--token", serialized)

    def test_port_rule_reports_foreign_listener_but_never_offers_repair(self) -> None:
        service = DoctorService(
            {
                "doctor_port": {
                    "host": "127.0.0.1",
                    "port": 8757,
                    "gateway_port": 8758,
                    "current_pid": 100,
                    "listeners": [{"port": 8757, "pid": 200, "processName": "other.exe"}],
                    "can_bind": lambda _host, _port: True,
                }
            }
        )
        result = service.detect("doctor.port")
        self.assertEqual(result["status"], "warning")
        self.assertFalse(result["fixable"])
        foreign = next(row for row in result["detail"]["listeners"] if row["port"] == 8757)
        self.assertEqual(foreign["state"], "foreign")
        self.assertEqual(foreign["pid"], 200)
        with self.assertRaises(DoctorServiceError) as raised:
            service.fix("doctor.port", "force")
        self.assertEqual(raised.exception.status_code, 409)

    def test_port_rule_does_not_trust_a_lease_owned_by_another_pid_or_address(self) -> None:
        service = DoctorService(
            {
                "doctor_port": {
                    "host": "127.0.0.1",
                    "port": 8757,
                    "gateway_host": "::1",
                    "gateway_port": 8757,
                    "current_pid": 100,
                    "owner_pid": 200,
                    "owner_lease_owned": True,
                    "listeners": [],
                    "can_bind": lambda _host, _port: True,
                }
            }
        )
        result = service.detect("doctor.port")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["detail"]["ownerLeaseOwned"])
        self.assertEqual(
            [row["state"] for row in result["detail"]["listeners"]],
            ["available", "available"],
        )

    def test_source_install_is_skipped(self) -> None:
        result = DoctorService({"desktop_install": {"packaged": False}}).detect("desktop.install_integrity")
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(result["fixable"])

    def test_packaged_integrity_validates_three_hashes_and_cleans_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            desktop = root / "VRCForge.exe"
            backend = root / "backend.exe"
            version_file = root / "VERSION"
            state_dir = root / "state"
            state_dir.mkdir()
            desktop.write_bytes(b"desktop")
            backend.write_bytes(b"backend")
            version_file.write_text("1.3.4\n", encoding="utf-8")
            manifest = self._manifest("1.3.4", desktop, backend, version_file)
            context = self._install_context(manifest, desktop, backend, version_file, state_dir)

            result = DoctorService(context).detect("desktop.install_integrity")
            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["detail"]["schemaValid"])
            self.assertTrue(result["detail"]["manifestVersionMatched"])
            self.assertTrue(all(row["hashMatched"] for row in result["detail"]["fileChecks"]))
            self.assertTrue(result["detail"]["stateWritable"])
            self.assertFalse(result["detail"]["stateProbePerformed"])
            self.assertFalse(list(state_dir.glob(".vrcforge-doctor-*.tmp")))

            backend.write_bytes(b"tampered")
            mismatch = DoctorService(context).detect("desktop.install_integrity")
            self.assertEqual(mismatch["status"], "error")
            backend_check = next(row for row in mismatch["detail"]["fileChecks"] if row["component"] == "backend")
            self.assertFalse(backend_check["hashMatched"])
            serialized = json.dumps(mismatch)
            self.assertNotIn(hashlib.sha256(b"backend").hexdigest(), serialized)
            self.assertNotIn(str(root), serialized)

    def test_missing_manifest_is_unknown_and_cloud_location_is_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "OneDrive-Work"
            root.mkdir()
            desktop = root / "VRCForge.exe"
            backend = root / "backend.exe"
            version_file = root / "VERSION"
            state_dir = root / "state"
            state_dir.mkdir()
            desktop.write_bytes(b"desktop")
            backend.write_bytes(b"backend")
            version_file.write_text("1.3.4", encoding="utf-8")
            without_manifest = self._install_context(None, desktop, backend, version_file, state_dir)
            self.assertEqual(DoctorService(without_manifest).detect("desktop.install_integrity")["status"], "unknown")

            manifest = self._manifest("1.3.4", desktop, backend, version_file)
            with_manifest = self._install_context(manifest, desktop, backend, version_file, state_dir)
            warning = DoctorService(with_manifest).detect("desktop.install_integrity")
            self.assertEqual(warning["status"], "warning")
            self.assertEqual(warning["detail"]["cloudSyncProviders"], ["OneDrive"])

    def test_security_rules_are_warn_only_and_read_only(self) -> None:
        context = {
            "security": {
                "external_writes": {
                    "broadPermissions": True,
                    "approvalRequired": False,
                    "checkpointRequired": False,
                },
                "bind_auth": {"publicBind": True, "tokenRequired": False, "tokenStrong": False},
                "mcp_exposure": {"broadExposure": True, "writeToolsSupervised": False},
                "process_exec": {"unsafeExec": True, "approvalRequired": False, "policyBounded": False},
            }
        }
        service = DoctorService(context)
        for check_id in (
            "security.external_writes",
            "security.bind_auth",
            "security.mcp_exposure",
            "security.process_exec",
        ):
            result = service.detect(check_id)
            self.assertEqual(result["status"], "warning")
            self.assertFalse(result["fixable"])
            self.assertTrue(result["detail"]["readOnly"])
            with self.assertRaises(DoctorServiceError) as raised:
                service.fix(check_id, "force")
            self.assertEqual(raised.exception.status_code, 409)

    @staticmethod
    def _manifest(version: str, desktop: Path, backend: Path, version_file: Path) -> dict:
        return {
            "schema": "vrcforge.payload-integrity.v1",
            "version": version,
            "files": {
                "desktop": {"sha256": hashlib.sha256(desktop.read_bytes()).hexdigest()},
                "backend": {"sha256": hashlib.sha256(backend.read_bytes()).hexdigest()},
                "version": {"sha256": hashlib.sha256(version_file.read_bytes()).hexdigest()},
            },
        }

    @staticmethod
    def _install_context(
        manifest: dict | None,
        desktop: Path,
        backend: Path,
        version_file: Path,
        state_dir: Path,
    ) -> dict:
        value = {
            "packaged": True,
            "desktop_version": "1.3.4",
            "desktop_path": desktop,
            "backend_path": backend,
            "version_path": version_file,
            "state_dir": state_dir,
        }
        if manifest is not None:
            value["manifest"] = manifest
        return {"desktop_install": value}


if __name__ == "__main__":
    unittest.main()
