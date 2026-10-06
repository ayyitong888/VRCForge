"""Private, owner-scoped scan snapshots. Connections exist only during an operation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_scan_scope_client import AgentScanScopeClient, SCAN_SCOPE_SCHEMA
from agent_scan_rescan import WHITELIST, ScanRescanVerifier, comparison_metadata, eligible
from agent_scan_scope_budget import ScopeCaptureBudget
from agent_tool_result_reader import result_reference

_PROCESS_BOOT_ID = uuid.uuid4().hex
MAX_STORE_BYTES = 128 * 1024 * 1024
MAX_ASSET_PATHS = 256
MAX_HASH_BYTES = 128 * 1024 * 1024
MAX_SCOPE_PROOF_BYTES = 256 * 1024
_SCOPE_TOKEN = re.compile(r"scope_[0-9a-f]{32}\Z")
_TRANSIENT_SCOPE_FAILURES = frozenset({
    "capability_unavailable", "core_unavailable", "invalid_validation_response",
    "validation_failed", "unity_core_timeout", "unity_core_connection_failed",
    "unity_core_project_busy",
})
KNOWN_SCOPED_WRITE_REASONS = frozenset({
    "vrcforge_add_component", "vrcforge_add_modular_avatar_component", "vrcforge_add_outfit",
    "vrcforge_add_outfit_part", "vrcforge_add_wardrobe_outfit", "vrcforge_apply_blendshapes",
    "vrcforge_apply_clothing_fx", "vrcforge_apply_parameter_optimization", "vrcforge_apply_shader_tuning",
    "vrcforge_apply_shader_tuning_preset", "vrcforge_apply_tuning_preset", "vrcforge_atomic_reference_rename",
    "vrcforge_build_parameter_bit_packed_clone", "vrcforge_configure_optimizer_component",
    "vrcforge_convert_unity_constraint", "vrcforge_create_component_feature", "vrcforge_create_gameobject",
    "vrcforge_create_wardrobe", "vrcforge_delete_gameobject", "vrcforge_duplicate_project_asset",
    "vrcforge_duplicate_scene_asset", "vrcforge_duplicate_scene_object", "vrcforge_ensure_animator_state",
    "vrcforge_ensure_expression_menu_control", "vrcforge_ensure_expression_parameter",
    "vrcforge_flatten_material_variant", "vrcforge_gesture_manager_enter_play_mode",
    "vrcforge_gesture_manager_set_parameter", "vrcforge_import_outfit_package", "vrcforge_instantiate_prefab",
    "vrcforge_manage_expression_menu", "vrcforge_manage_expression_parameters", "vrcforge_manage_fx_animator",
    "vrcforge_manage_wardrobe", "vrcforge_remap_skinned_mesh_bone", "vrcforge_remove_component",
    "vrcforge_rename_gameobject", "vrcforge_reparent_gameobject", "vrcforge_refresh_asset_database",
    "vrcforge_save_new_scene", "vrcforge_save_scene_object_as_prefab", "vrcforge_scene_save",
    "vrcforge_scene_transition", "vrcforge_set_constraint_sources", "vrcforge_set_gameobject_active",
    "vrcforge_set_material_shader", "vrcforge_set_material_texture", "vrcforge_set_play_mode",
    "vrcforge_set_property", "vrcforge_set_renderer_material_slot", "vrcforge_set_texture_import_settings",
    "vrcforge_setup_outfit", "vrcforge_texture_patch", "vrcforge_toggle_scene_object", "vrcforge_unpack_prefab",
    "vrcforge_undo_blendshapes", "vrcforge_user_adjustment_handoff", "vrcforge_write_animation_curve",
    "vrcforge_write_avatar_descriptor",
})


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _owner(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError("A nonempty authenticated owner identity is required.")
    return value


def _project(value: str | Path, *, global_allowed: bool = False) -> str:
    if not str(value).strip():
        if global_allowed:
            return ""
        raise ValueError("An authenticated project root is required.")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Project roots must be absolute owner-supplied paths.")
    resolved = str(path.resolve())
    return resolved.casefold() if os.name == "nt" else resolved


def _asset_hint(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 1024 or any(c in value for c in ("\\", ":", "\x00")):
        return None
    parts = value.split("/")
    return value if len(parts) > 1 and parts[0] == "Assets" and all(p not in ("", ".", "..") for p in parts) else None


def _asset_hints(value: Any, key: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for name, child in value.items():
            found.update(_asset_hints(child, str(name).replace("_", "").lower()))
    elif isinstance(value, list):
        for child in value:
            found.update(_asset_hints(child, key))
    elif key.endswith(("path", "paths")):
        hint = _asset_hint(value)
        if hint:
            found.add(hint)
    return found


def describe_scan_scope(step: dict, arguments: dict) -> dict:
    return {"tool": step.get("tool", ""), "arguments": arguments,
            "assetPaths": sorted(_asset_hints(arguments) | _asset_hints(step)),
            "dependencyCoverage": "unverified"}


def _file_fingerprint(path: Path, project: Path, budget: list[int]) -> dict | None:
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(project / "Assets") or not resolved.is_file():
            return None
        before = resolved.stat()
        if before.st_size > budget[0]:
            return None
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            while chunk := stream.read(min(1024 * 1024, budget[0] + 1)):
                budget[0] -= len(chunk)
                if budget[0] < 0:
                    return None
                digest.update(chunk)
        after = resolved.stat()
        fields = ("st_size", "st_mtime_ns", "st_ctime_ns", "st_ino", "st_dev")
        if any(getattr(before, k) != getattr(after, k) for k in fields):
            return None
        return {"sha256": digest.hexdigest(), "mtime_ns": after.st_mtime_ns, "size": after.st_size}
    except (OSError, ValueError, RuntimeError):
        return None


def _fingerprints(project: str, paths: list[str]) -> dict:
    root, budget = Path(project), [MAX_HASH_BYTES]
    return {p: {"asset": _file_fingerprint(root / p, root, budget),
                "meta": _file_fingerprint(root / (p + ".meta"), root, budget)} for p in paths}


def _valid_fingerprint(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(part), dict)
        and isinstance(value[part].get("sha256"), str)
        and len(value[part]["sha256"]) == 64
        and all(c in "0123456789abcdef" for c in value[part]["sha256"])
        and type(value[part].get("mtime_ns")) is int
        and type(value[part].get("size")) is int
        for part in ("asset", "meta")
    )


def _valid_scope_proof(proof: Any, expected_token: str | None = None) -> bool:
    if not isinstance(proof, dict) or proof.get("schema") != SCAN_SCOPE_SCHEMA \
            or proof.get("verified") is not True or proof.get("dependencyCoverage") != "complete":
        return False
    token, editor_session = proof.get("token"), proof.get("editorSession")
    if not isinstance(token, str) or not _SCOPE_TOKEN.fullmatch(token) \
            or (expected_token is not None and token != expected_token):
        return False
    if not isinstance(editor_session, str):
        return False
    try:
        if str(uuid.UUID(editor_session)) != editor_session:
            return False
    except (ValueError, AttributeError):
        return False
    paths, captured_at = proof.get("assetPaths"), proof.get("capturedAt")
    return (
        isinstance(paths, list)
        and len(paths) <= MAX_ASSET_PATHS
        and all(_asset_hint(path) == path for path in paths)
        and len(set(paths)) == len(paths)
        and isinstance(captured_at, str)
        and bool(captured_at.strip())
        and len(captured_at) <= 80
    )


class ScanResultStore:
    """Gateway supplies authenticated owners; this store has no public raw-resource route."""

    def __init__(self, root: Path, shared_lock: Any = None) -> None:
        self._path = Path(root) / "scan-results.sqlite3"
        self._lock = shared_lock if shared_lock is not None else threading.RLock()
        self._boot_id = _PROCESS_BOOT_ID
        self._capture_budget = ScopeCaptureBudget()
        self._rescan = ScanRescanVerifier(self)

    @contextmanager
    def _connection(self, *, create: bool = False):
        if create:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute("BEGIN IMMEDIATE")
            if create:
                connection.execute("CREATE TABLE IF NOT EXISTS snapshots (ref TEXT PRIMARY KEY, session TEXT NOT NULL, turn TEXT NOT NULL, project TEXT NOT NULL, boot TEXT NOT NULL, captured_at TEXT NOT NULL, raw BLOB NOT NULL, scope TEXT NOT NULL, fingerprints TEXT NOT NULL, size INTEGER NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS validity (ref TEXT PRIMARY KEY REFERENCES snapshots(ref) ON DELETE CASCADE, reason TEXT NOT NULL DEFAULT '')")
                connection.execute("CREATE TABLE IF NOT EXISTS mutations (project TEXT PRIMARY KEY, pending INTEGER NOT NULL CHECK(pending >= 0), epoch INTEGER NOT NULL CHECK(epoch >= 0))")
            connection.execute("CREATE TABLE IF NOT EXISTS scan_scope_proofs (token TEXT PRIMARY KEY, project TEXT NOT NULL, generation INTEGER NOT NULL, proof TEXT NOT NULL, size INTEGER NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS scan_scope_links (ref TEXT PRIMARY KEY REFERENCES snapshots(ref) ON DELETE CASCADE, token TEXT NOT NULL REFERENCES scan_scope_proofs(token))")
            connection.execute("CREATE TABLE IF NOT EXISTS scan_scope_expiries (ref TEXT PRIMARY KEY REFERENCES snapshots(ref) ON DELETE CASCADE, reason TEXT NOT NULL, changed_paths TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS scan_rescan_checks (session TEXT NOT NULL, turn TEXT NOT NULL, ref TEXT NOT NULL REFERENCES snapshots(ref) ON DELETE CASCADE, generation INTEGER NOT NULL, result TEXT NOT NULL, PRIMARY KEY(session, turn, ref))")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _pending(connection, project: str) -> bool:
        return bool(connection.execute("SELECT 1 FROM mutations WHERE project IN (?, '') AND pending > 0 LIMIT 1", (project,)).fetchone())

    @staticmethod
    def _generation(connection, project: str) -> int:
        return connection.execute("SELECT COALESCE(SUM(epoch), 0) FROM mutations WHERE project IN (?, '')", (project,)).fetchone()[0]

    def generation(self, project_root: str | Path) -> int:
        project = _project(project_root, global_allowed=True)
        with self._lock:
            if not self._path.is_file():
                return 0
            with self._connection() as connection:
                return self._generation(connection, project)

    @staticmethod
    def _expire(connection, ref: str, reason: str) -> None:
        connection.execute("UPDATE validity SET reason=? WHERE ref=? AND reason=''", (reason, ref))

    @staticmethod
    def _expiry(row, reason: str, proof: dict | None = None, changed_paths: Any = None) -> dict:
        scope = json.loads(row["scope"])
        if proof is not None:
            scope["assetPaths"] = sorted(set(scope.get("assetPaths", [])) | set(proof["assetPaths"]))
            scope["schema"] = SCAN_SCOPE_SCHEMA
            scope["dependencyCoverage"] = "complete"
        values = changed_paths if isinstance(changed_paths, list) else []
        changed = sorted({path for path in values if _asset_hint(path) == path})[:MAX_ASSET_PATHS]
        return {
            "expired": True,
            "reason": reason,
            "rescanScope": scope,
            "changedAssetPaths": changed,
            "rescanPolicy": "full_scope",
            "capturedAt": row["captured_at"],
        }

    @staticmethod
    def _metadata(row, reason: str = "", proof: dict | None = None, changed_paths: Any = None) -> dict:
        result = {"resultRef": row["ref"], "capturedAt": row["captured_at"], "scanScope": json.loads(row["scope"]),
                  "scope": "current_turn", "freshness": "unverified", "persisted": True}
        if proof is not None and not reason:
            scan_scope = result["scanScope"]
            scan_scope["assetPaths"] = sorted(set(scan_scope.get("assetPaths", [])) | set(proof["assetPaths"]))
            scan_scope["schema"] = SCAN_SCOPE_SCHEMA
            scan_scope["dependencyCoverage"] = "complete"
            result.update(scope="validated_scope", freshness="validated")
        if changed_paths is not None:
            values = changed_paths if isinstance(changed_paths, list) else []
            result["changedAssetPaths"] = sorted(
                {path for path in values if _asset_hint(path) == path}
            )[:MAX_ASSET_PATHS]
        if reason:
            result.update(expired=True, reason=reason, rescanPolicy="full_scope")
        return result

    @staticmethod
    def _scope_expiry_paths(connection, ref: str) -> list[str] | None:
        row = connection.execute(
            "SELECT changed_paths FROM scan_scope_expiries WHERE ref=?", (ref,)
        ).fetchone()
        if row is None:
            return None
        try:
            paths = json.loads(row["changed_paths"])
        except (TypeError, ValueError):
            return []
        return paths if isinstance(paths, list) else []

    @staticmethod
    def _remember_scope_expiry(connection, ref: str, reason: str, changed_paths: Any) -> None:
        values = changed_paths if isinstance(changed_paths, list) else []
        changed = sorted({path for path in values if _asset_hint(path) == path})[:MAX_ASSET_PATHS]
        connection.execute(
            "INSERT OR IGNORE INTO scan_scope_expiries VALUES (?, ?, ?)",
            (ref, reason, _json(changed)),
        )

    @staticmethod
    def _scope_proof(connection, start_context: dict | None, project: str, generation: int | None) -> dict | None:
        if not isinstance(start_context, dict):
            return None
        supplied = start_context.get("proof")
        token = supplied.get("token") if isinstance(supplied, dict) else None
        if not isinstance(token, str):
            return None
        row = connection.execute(
            "SELECT project, generation, proof FROM scan_scope_proofs WHERE token=?", (token,)
        ).fetchone()
        if row is None or row["project"] != project or row["generation"] != generation:
            return None
        try:
            proof = json.loads(row["proof"])
        except (TypeError, ValueError):
            return None
        if proof != supplied or not _valid_scope_proof(proof, token):
            return None
        return proof

    @staticmethod
    def _proof_for_ref(connection, ref: str) -> dict | None:
        row = connection.execute(
            "SELECT l.token, p.proof FROM scan_scope_links l JOIN scan_scope_proofs p USING(token) WHERE l.ref=?",
            (ref,),
        ).fetchone()
        if row is None:
            return None
        try:
            proof = json.loads(row["proof"])
        except (TypeError, ValueError):
            return None
        return proof if _valid_scope_proof(proof, row["token"]) else None

    def start(self, project_root: str | Path, tool: str, arguments: dict) -> dict:
        """Capture a Core-owned scan scope before the caller begins scanning."""
        project = _project(project_root)
        if not isinstance(tool, str) or not tool or len(tool) > 160 or not isinstance(arguments, dict):
            raise ValueError("Scan tool and arguments must be valid JSON inputs.")
        try:
            with self._lock:
                if not self._path.is_file():
                    generation_before, pending_before = 0, False
                else:
                    with self._connection() as connection:
                        generation_before = self._generation(connection, project)
                        pending_before = self._pending(connection, project)
        except (OSError, sqlite3.Error):
            return {"generation": 0, "proof": {
                "schema": SCAN_SCOPE_SCHEMA, "verified": False, "reason": "proof_store_unavailable",
            }}
        client = AgentScanScopeClient(project)
        client.capture_budget = self._capture_budget
        proof = client.capture(tool, arguments)
        if not isinstance(proof, dict) or proof.get("verified") is not True:
            if not isinstance(proof, dict):
                proof = {"schema": SCAN_SCOPE_SCHEMA, "verified": False, "reason": "scope_unverified"}
            return {"generation": generation_before, "proof": proof}
        if not _valid_scope_proof(proof):
            return {"generation": generation_before, "proof": {
                "schema": SCAN_SCOPE_SCHEMA, "verified": False, "reason": "invalid_scope_response",
            }}
        if pending_before:
            return {"generation": generation_before, "proof": {
                "schema": SCAN_SCOPE_SCHEMA, "verified": False, "reason": "mutation_in_progress",
            }}
        try:
            proof_json = _json(proof)
            if len(proof_json.encode("utf-8")) > MAX_SCOPE_PROOF_BYTES:
                raise ValueError("Core scan scope exceeds the proof size limit.")
            with self._lock, self._connection(create=True) as connection:
                generation = self._generation(connection, project)
                if generation != generation_before or self._pending(connection, project):
                    return {"generation": generation_before, "proof": {
                        "schema": SCAN_SCOPE_SCHEMA, "verified": False,
                        "reason": "mutation_during_scope_capture",
                    }}
                token = proof.get("token")
                existing = connection.execute(
                    "SELECT project, proof FROM scan_scope_proofs WHERE token=?", (token,)
                ).fetchone()
                if existing is not None and (existing["project"], existing["proof"]) != (project, proof_json):
                    raise ValueError("Core scan scope token collision.")
                if existing is None:
                    total = connection.execute("SELECT COALESCE(SUM(size), 0) FROM scan_scope_proofs").fetchone()[0]
                    if total + len(proof_json.encode("utf-8")) > MAX_STORE_BYTES:
                        raise ValueError("Scan scope proof store exceeds its 128 MiB limit.")
                    connection.execute(
                        "INSERT INTO scan_scope_proofs VALUES (?, ?, ?, ?, ?)",
                        (token, project, generation, proof_json, len(proof_json.encode("utf-8"))),
                    )
            return {"generation": generation_before, "proof": proof}
        except (OSError, sqlite3.Error, ValueError):
            return {"generation": generation_before, "proof": {
                "schema": SCAN_SCOPE_SCHEMA, "verified": False, "reason": "proof_store_unavailable",
            }}

    def capture(self, session_id: str, turn_id: str, project_root: str | Path, step: dict, arguments: dict,
                *, expected_generation: int | dict | None = None) -> dict:
        try:
            return self._capture(session_id, turn_id, project_root, step, arguments,
                                 expected_generation=expected_generation)
        except sqlite3.Error as exc:
            raise ValueError("Private scan snapshot storage is unavailable.") from exc

    def _capture(self, session_id: str, turn_id: str, project_root: str | Path, step: dict, arguments: dict,
                 *, expected_generation: int | dict | None = None) -> dict:
        session_id, turn_id, project = _owner(session_id), _owner(turn_id), _project(project_root)
        if not isinstance(step, dict) or not isinstance(arguments, dict):
            raise ValueError("Step and owner-supplied arguments must be JSON objects.")
        start_context = expected_generation if isinstance(expected_generation, dict) else None
        generation = start_context.get("generation") if start_context is not None else expected_generation
        if generation is not None and (type(generation) is not int or generation < 0):
            raise ValueError("Expected mutation generation must be a nonnegative integer.")
        ref = result_reference(session_id, turn_id, str(project_root), step)
        descriptor = step.get("resultRead")
        if descriptor is not None and (not isinstance(descriptor, dict) or descriptor.get("resultRef") != ref):
            raise ValueError("Owned step result reference does not match its canonical reader reference.")
        raw = _json({"step": step, "arguments": arguments}).encode("utf-8")
        declared_scope = describe_scan_scope(step, arguments)
        paths = declared_scope["assetPaths"]
        if len(raw) > MAX_STORE_BYTES:
            raise ValueError("Scan snapshot exceeds the private store size limit.")
        scope = _json(declared_scope)
        proof = None
        proof_validated = False
        validation_changed_paths = None
        with self._lock, self._connection(create=True) as connection:
            reason = "mutation_in_progress" if self._pending(connection, project) else ""
            if not reason and generation is not None and generation != self._generation(connection, project):
                reason = "mutation_during_scan"
            proof = self._scope_proof(connection, start_context, project, generation)
            if proof is not None:
                paths = sorted(set(paths) | set(proof["assetPaths"]))
                if len(paths) > MAX_ASSET_PATHS:
                    proof = None
                    paths = declared_scope["assetPaths"]
            if len(paths) > MAX_ASSET_PATHS:
                if not eligible(step, arguments):
                    raise ValueError("Scan snapshot exceeds the private store path limit.")
                # Preserve every result byte and declared path. No partial
                # fingerprint set may grant the Core fast path; use comparison.
                paths, proof = [], None
                reason = reason or "fingerprint_path_budget_exceeded"
            old = connection.execute("SELECT * FROM snapshots WHERE ref=?", (ref,)).fetchone()
            if old is not None:
                if (old["raw"], old["scope"]) != (raw, scope):
                    raise ValueError("Scan result reference collision with a different immutable snapshot.")
                try:
                    prior_fingerprints = json.loads(old["fingerprints"])
                except (TypeError, ValueError):
                    prior_fingerprints = {}
                if proof is not None and set(prior_fingerprints) != set(paths):
                    proof = None
                if reason:
                    self._expire(connection, ref, reason)
            else:
                observed = _fingerprints(project, paths)
                if not reason and any(not _valid_fingerprint(observed[p]) for p in paths):
                    reason = "fingerprint_unavailable"
                fingerprints = _json(observed)
                size = len(raw) + len(scope.encode()) + len(fingerprints.encode())
                total = connection.execute("SELECT COALESCE(SUM(size), 0) FROM snapshots").fetchone()[0]
                if total + size > MAX_STORE_BYTES:
                    raise ValueError("Private scan result store exceeds its 128 MiB payload limit.")
                connection.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ref, session_id, turn_id, project, self._boot_id, datetime.now(timezone.utc).isoformat(), raw, scope, fingerprints, size))
                connection.execute("INSERT INTO validity(ref, reason) VALUES (?, ?)",
                    (ref, reason))
            if proof is not None:
                linked = connection.execute("SELECT token FROM scan_scope_links WHERE ref=?", (ref,)).fetchone()
                if linked is None:
                    connection.execute("INSERT INTO scan_scope_links VALUES (?, ?)", (ref, proof["token"]))
            row = connection.execute("SELECT s.*, v.reason FROM snapshots s JOIN validity v USING(ref) WHERE ref=?", (ref,)).fetchone()
        if proof is not None and not row["reason"]:
            validation = AgentScanScopeClient(project).validate(proof["token"])
            if validation.get("valid") is True and validation.get("editorSession") == proof["editorSession"]:
                with self._lock, self._connection() as connection:
                    latest = connection.execute(
                        "SELECT s.*, v.reason FROM snapshots s JOIN validity v USING(ref) WHERE ref=?", (ref,)
                    ).fetchone()
                    validation_reason = latest["reason"] or ("mutation_in_progress" if self._pending(connection, project) else "")
                    if not validation_reason and generation != self._generation(connection, project):
                        validation_reason = "mutation_during_scan"
                    if not validation_reason:
                        try:
                            saved = json.loads(latest["fingerprints"])
                            current = _fingerprints(project, paths)
                            if set(saved) != set(paths) or any(not _valid_fingerprint(saved[p]) for p in paths) \
                                    or any(not _valid_fingerprint(current[p]) for p in paths):
                                validation_reason = "fingerprint_unavailable"
                            elif saved != current:
                                validation_reason = "asset_fingerprint_changed"
                        except (KeyError, TypeError, ValueError):
                            validation_reason = "fingerprint_unavailable"
                    if validation_reason:
                        self._expire(connection, ref, validation_reason)
                    else:
                        linked = connection.execute("SELECT token FROM scan_scope_links WHERE ref=?", (ref,)).fetchone()
                        if linked is None:
                            connection.execute("INSERT INTO scan_scope_links VALUES (?, ?)", (ref, proof["token"]))
                    row = connection.execute(
                        "SELECT s.*, v.reason FROM snapshots s JOIN validity v USING(ref) WHERE ref=?", (ref,)
                    ).fetchone()
                    proof_validated = not validation_reason
            elif validation.get("valid") is False:
                validation_reason = str(validation.get("reason") or "scope_invalid")[:160]
                validation_changed_paths = validation.get("changedAssetPaths")
                if validation_reason not in _TRANSIENT_SCOPE_FAILURES:
                    with self._lock, self._connection() as connection:
                        self._expire(connection, ref, validation_reason)
                        self._remember_scope_expiry(connection, ref, validation_reason, validation_changed_paths)
            elif validation.get("valid") is True:
                validation_reason = "editor_session_changed"
                with self._lock, self._connection() as connection:
                    self._expire(connection, ref, validation_reason)
                    self._remember_scope_expiry(connection, ref, validation_reason, [])
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT s.*, v.reason FROM snapshots s JOIN validity v USING(ref) WHERE ref=?", (ref,)).fetchone()
            linked_proof = self._proof_for_ref(connection, ref)
            if validation_changed_paths is None:
                validation_changed_paths = self._scope_expiry_paths(connection, ref)
            metadata = self._metadata(
                row, row["reason"], linked_proof if proof_validated else None, validation_changed_paths
            )
        if eligible(step, arguments) and row["reason"] not in {"mutation_in_progress", "mutation_during_scan"}:
            return comparison_metadata(metadata)
        if step.get("tool") not in WHITELIST or (isinstance(step.get("result"), dict)
                                                and step["result"].get("ok") is True):
            metadata.update(scope="current_turn", freshness="unverified")
        return metadata

    def lookup(self, session_id: str, turn_id: str, project_root: str | Path, result_ref: str) -> dict | None:
        session_id, turn_id, project = _owner(session_id), _owner(turn_id), _project(project_root)
        if not self._path.is_file():
            return None
        try:
            return self._rescan.lookup(session_id, turn_id, project, result_ref, self._lookup_scope)
        except (OSError, sqlite3.Error, ValueError):
            return {"expired": True, "reason": "comparison_store_unavailable", "rescanScope": {}}

    def _lookup_scope(self, session_id: str, turn_id: str, project_root: str | Path, result_ref: str,
                      *, require_proof: bool = False) -> dict | None:
        session_id, turn_id, project = _owner(session_id), _owner(turn_id), _project(project_root)
        if not self._path.is_file():
            return None
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT s.*, v.reason FROM snapshots s JOIN validity v USING(ref) WHERE ref=? AND session=? AND project=?", (result_ref, session_id, project)).fetchone()
            if row is None:
                return None
            proof = self._proof_for_ref(connection, result_ref)
            changed_paths = self._scope_expiry_paths(connection, result_ref)
            reason = row["reason"]
            if not reason and self._pending(connection, project):
                reason = "mutation_in_progress"
            if not reason and proof is None and row["boot"] != self._boot_id:
                reason = "runtime_observation_gap"
            if not reason and proof is None and row["turn"] != turn_id:
                reason = "core_scope_unavailable"
            if not reason:
                try:
                    paths = json.loads(row["scope"])["assetPaths"]
                    if proof is not None:
                        paths = sorted(set(paths) | set(proof["assetPaths"]))
                    saved = json.loads(row["fingerprints"])
                    if not isinstance(paths, list) or any(_asset_hint(p) != p for p in paths) or not isinstance(saved, dict) or set(saved) != set(paths) or any(not _valid_fingerprint(saved[p]) for p in paths):
                        reason = "fingerprint_unavailable"
                    else:
                        current = _fingerprints(project, paths)
                        if any(not _valid_fingerprint(current[p]) for p in paths):
                            reason = "fingerprint_unavailable"
                        elif current != saved:
                            reason = "asset_fingerprint_changed"
                except (ValueError, KeyError, TypeError):
                    reason = "fingerprint_unavailable"
            if reason:
                if not (proof is not None and reason == "mutation_in_progress"):
                    self._expire(connection, result_ref, reason)
                result = self._expiry(row, reason, proof, changed_paths)
            else:
                result = {"step": json.loads(row["raw"])["step"]}
                saved = json.loads(row["fingerprints"])
                paths = sorted(saved)
                boot, turn = row["boot"], row["turn"]
        if proof is None or reason:
            return result

        validation = AgentScanScopeClient(project).validate(proof["token"])
        valid = validation.get("valid") is True and validation.get("editorSession") == proof.get("editorSession")
        if not valid:
            validation_reason = validation.get("reason") or "scope_invalid"
            transient = validation_reason in _TRANSIENT_SCOPE_FAILURES
            if transient and boot == self._boot_id and turn == turn_id:
                return result
            reason = validation_reason if not transient else (
                "runtime_observation_gap" if boot != self._boot_id else "core_scope_unavailable"
            )
            with self._lock, self._connection() as connection:
                current = connection.execute("SELECT * FROM snapshots WHERE ref=?", (result_ref,)).fetchone()
                self._expire(connection, result_ref, reason)
                changed_paths = validation.get("changedAssetPaths")
                self._remember_scope_expiry(connection, result_ref, reason, changed_paths)
                return self._expiry(current, reason, proof, changed_paths)

        with self._lock, self._connection() as connection:
            latest = connection.execute(
                "SELECT s.*, v.reason FROM snapshots s JOIN validity v USING(ref) WHERE ref=? AND session=? AND project=?",
                (result_ref, session_id, project),
            ).fetchone()
            reason = latest["reason"] or ("mutation_in_progress" if self._pending(connection, project) else "")
            if not reason:
                try:
                    current = _fingerprints(project, paths)
                    if any(not _valid_fingerprint(current[path]) for path in paths):
                        reason = "fingerprint_unavailable"
                    elif current != saved:
                        reason = "asset_fingerprint_changed"
                except (KeyError, TypeError, ValueError):
                    reason = "fingerprint_unavailable"
            if reason:
                if reason != "mutation_in_progress":
                    self._expire(connection, result_ref, reason)
                return self._expiry(latest, reason, proof)
        if require_proof:
            result["scopeValidated"] = True
        return result

    @staticmethod
    def _invalidate(connection, project: str, reason: str) -> None:
        condition, params = ("", ()) if not project else (" WHERE project=?", (project,))
        name, separator, phase = reason.rpartition(":")
        normalized = (name if separator and phase in {"starting", "finished"} else reason).strip().casefold()
        preserve_proven_scopes = project and normalized in KNOWN_SCOPED_WRITE_REASONS
        proof_filter = " AND ref NOT IN (SELECT ref FROM scan_scope_links)" if preserve_proven_scopes else ""
        connection.execute(
            "UPDATE validity SET reason=? WHERE reason='' AND ref IN (SELECT ref FROM snapshots" + condition + proof_filter + ")",
            (reason, *params),
        )

    def invalidate(self, project_root: str | Path, paths: Iterable[str] | None, reason: str) -> None:
        project, reason = _project(project_root, global_allowed=True), _owner(reason)
        with self._lock, self._connection(create=True) as connection:
            connection.execute("INSERT INTO mutations VALUES (?, 0, 1) ON CONFLICT(project) DO UPDATE SET epoch=epoch+1", (project,))
            self._invalidate(connection, project, reason)

    def begin_mutation(self, project_root: str | Path, reason: str) -> None:
        project, reason = _project(project_root, global_allowed=True), _owner(reason)
        with self._lock, self._connection(create=True) as connection:
            connection.execute("INSERT INTO mutations VALUES (?, 1, 1) ON CONFLICT(project) DO UPDATE SET pending=pending+1, epoch=epoch+1", (project,))
            self._invalidate(connection, project, reason)

    def end_mutation(self, project_root: str | Path, reason: str) -> None:
        project, reason = _project(project_root, global_allowed=True), _owner(reason)
        with self._lock, self._connection(create=True) as connection:
            self._invalidate(connection, project, reason)
            connection.execute("INSERT INTO mutations VALUES (?, 0, 1) ON CONFLICT(project) DO UPDATE SET pending=MAX(0, pending-1), epoch=epoch+1", (project,))

    def delete(self, session_id: str) -> None:
        session_id = _owner(session_id)
        if self._path.is_file():
            with self._lock, self._connection() as connection:
                connection.execute("DELETE FROM snapshots WHERE session=?", (session_id,))
