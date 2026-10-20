"""Owner-bound, read-only comparison of complete observable scan snapshots.

Core connections use the existing project authentication and last one call.
Receipts belong to the private snapshot store and are deleted with their owner.
No artifact, process, model request, or Unity write capability is created here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import sqlite3
import threading
import uuid

from unity_mcp_core_client import UnityMcpCoreClient
from wardrobe_outfit_workflow_service import (
    WardrobeArtifactReadPorts, WardrobeArtifactReadService, build_avatar_item_scan_arguments,
)

RESCAN_SCHEMA = "vrcforge.scan_result_equivalence.v1"
_MATERIAL_SCAN_ALIAS = "vrcforge_scan_materials"
WHITELIST = frozenset({
    "vrcforge_scan_wardrobe", "vrcforge_scan_animation_bindings",
    "vrcforge_scan_avatar_items", "vrcforge_scan_avatar_controls", "vrcforge_scan_fx_animator",
})
# These are invocation-envelope fields only. Nested business IDs/order/coverage
# and snapshot digests are deliberately retained, including next-page guards.
OUTER_NOISE = frozenset({
    "requestId", "operationId", "durationMs", "elapsedMs", "jsonPath", "outputPath",
    "receiptPath", "generatedAt", "scannedAt", "scanTimestamp",
})
_SELECTION = ("bindingView", "bindingSelectors", "bindingOffset", "bindingLimit",
              "clipOffset", "keyOffset", "maxTotalKeys", "expectedSnapshotDigest")


def _alias(arguments, snake, camel, default):
    return arguments.get(snake, arguments.get(camel, default))


def normalized_arguments(tool: str, arguments: dict) -> dict:
    if tool not in WHITELIST or not isinstance(arguments, dict):
        raise ValueError("Scan is not in the read-only comparison whitelist.")
    avatar = str(arguments.get("source_avatar_path") or arguments.get("sourceAvatarPath")
                 or arguments.get("avatar_path") or arguments.get("avatarPath") or "").strip()
    result = {"avatarPath": avatar, "outputPath": ""}
    if tool == "vrcforge_scan_avatar_items":
        result.update(build_avatar_item_scan_arguments(arguments))
    if tool in {"vrcforge_scan_fx_animator", "vrcforge_scan_animation_bindings"}:
        result.update(controllerPath=str(_alias(arguments, "controller_path", "controllerPath", "") or "").strip(), refreshAssets=False)
    if tool == "vrcforge_scan_animation_bindings":
        if not any(name in arguments for name in _SELECTION):
            raise ValueError("Legacy animation reads remain current-turn only.")
        clips = _alias(arguments, "clip_paths", "clipPaths", []) or []
        if not isinstance(clips, list) or not all(isinstance(path, str) for path in clips):
            raise ValueError("Clip selection must be explicit JSON paths.")
        details = _alias(arguments, "include_binding_details", "includeBindingDetails", False)
        if not isinstance(details, bool):
            details = str(details).lower() in {"1", "true", "yes", "y", "on"}
        result.update(clipPaths=[p for p in clips if p.strip()],
                      includeAllProjectClips=bool(_alias(arguments, "include_all_project_clips", "includeAllProjectClips", False)),
                      includeBindingDetails=details,
                      maxClips=int(_alias(arguments, "max_clips", "maxClips", 300) or 300),
                      maxKeysPerBinding=int(_alias(arguments, "max_keys_per_binding", "maxKeysPerBinding", 256)))
        result.update({name: deepcopy(arguments[name]) for name in _SELECTION if name in arguments})
    # Avatar-items defines an empty selector as all loaded scene avatar roots.
    if tool not in {"vrcforge_scan_avatar_items", _MATERIAL_SCAN_ALIAS} and not avatar \
            and not result.get("controllerPath") and not result.get("clipPaths"):
        raise ValueError("An exact scan target is required for comparison.")
    return result


def business_digest(payload: dict) -> str:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ValueError("Only successful structured business results can be compared.")
    canonical = {key: value for key, value in payload.items() if key not in OUTER_NOISE}
    batch_prefix = {"fx_animator_snapshot": "fx", "avatar_items_snapshot": "items"}.get(payload.get("type"))
    if batch_prefix and isinstance(payload.get("id"), str) and re.fullmatch(batch_prefix + r"_\d{8}_\d{6}", payload["id"]):
        canonical.pop("id", None)
        canonical.pop("created_at", None)
    encoded = json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def eligible(step: dict, arguments: dict) -> bool:
    try:
        normalized = normalized_arguments(step["tool"], arguments)
        payload = step["result"]
        business_digest(payload)
        if step["tool"] == "vrcforge_scan_avatar_items" and not normalized["avatarPath"]:
            scenes = payload.get("scenes")
            if (payload.get("type") != "avatar_items_snapshot"
                    or payload.get("requested_avatar_path") != ""
                    or not isinstance(scenes, list) or not scenes
                    or not all(isinstance(scene, str) and scene.strip() for scene in scenes)
                    or not isinstance(payload.get("items"), list)
                    or not isinstance(payload.get("snapshotDigest"), str)
                    or not re.fullmatch(r"[0-9a-fA-F]{64}", payload["snapshotDigest"])):
                return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


def comparison_metadata(metadata: dict) -> dict:
    result = deepcopy(metadata)
    # Eligibility is not a claim that dependency closure or freshness is proven.
    result.update(scope="validated_scope", freshness="validated_on_read", expired=False)
    result.pop("reason", None)
    result["scanScope"].update(schema=RESCAN_SCHEMA, validation="observable_result_on_read")
    return result


def _public_projection(tool: str, payload: dict) -> dict:
    payload = deepcopy(payload)
    payload.setdefault("ok", True)
    if tool == "vrcforge_scan_wardrobe":
        ports = WardrobeArtifactReadPorts(lambda _: payload, lambda _: payload, lambda _: payload)
        return WardrobeArtifactReadService(ports).scan_wardrobe({})
    if tool == "vrcforge_scan_animation_bindings":
        def visit(value):
            if isinstance(value, list):
                return [visit(child) for child in value]
            if not isinstance(value, dict):
                return value
            return {key: ({name: visit(child) for name, child in item.items()
                           if name not in {"outputPath", "refreshAssets"}}
                          if key in {"nextRequest", "nextKeyRequest"} and isinstance(item, dict)
                          else visit(item)) for key, item in value.items()}
        return visit(payload)
    return payload


def direct_scan(project: str, tool: str, arguments: dict) -> dict:
    """One authenticated DirectRead; never load or write a latest artifact."""
    client = UnityMcpCoreClient(project, timeout_seconds=15, allow_previous_contract=True)
    envelope = client.call_tool(tool.replace("vrcforge_", "vrc_", 1), arguments)
    structured = envelope.get("structuredContent")
    if envelope.get("isError") is True or not isinstance(structured, dict) \
            or structured.get("success") is not True or not isinstance(structured.get("data"), dict):
        message = str((structured or {}).get("message", "Read-only scan failed.")) if isinstance(structured, dict) else "Read-only scan failed."
        raise ValueError(message)
    return _public_projection(tool, structured["data"])


class ScanRescanVerifier:
    """Serialize verification only, outside the Gateway/session shared lock."""

    def __init__(self, store):
        self.store = store
        self._lock = threading.RLock()

    def _owned(self, session, turn, project, ref):
        with self.store._lock, self.store._connection() as db:
            row = db.execute("SELECT s.*, v.reason FROM snapshots s JOIN validity v USING(ref) WHERE ref=? AND session=? AND project=?",
                             (ref, session, project)).fetchone()
            if row is None:
                return None
            return dict(row), json.loads(row["raw"]), self.store._generation(db, project), self.store._pending(db, project)

    def _receipt(self, session, turn, ref, generation, result=None, *, project=None):
        with self.store._lock, self.store._connection() as db:
            if result is None:
                row = db.execute("SELECT generation, result FROM scan_rescan_checks WHERE session=? AND turn=? AND ref=?",
                                 (session, turn, ref)).fetchone()
                return (row["generation"], json.loads(row["result"])) if row else None
            if project is not None and (self.store._generation(db, project) != generation
                                        or self.store._pending(db, project)):
                result = {"reason": "mutation_during_verification"}
            encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            total = db.execute("SELECT COALESCE(SUM(length(CAST(result AS BLOB))),0) FROM scan_rescan_checks").fetchone()[0]
            if total + len(encoded.encode("utf-8")) > 16 * 1024 * 1024:
                raise ValueError("Comparison receipt budget exhausted.")
            db.execute("INSERT INTO scan_rescan_checks VALUES (?, ?, ?, ?, ?) ON CONFLICT(session, turn, ref) DO UPDATE SET result=excluded.result",
                       (session, turn, ref, generation, encoded))
            return result

    def _reply(self, receipt, raw, row, session, turn, project):
        if receipt.get("reuse"):
            return {"step": raw["step"], "verification": receipt["verification"]}
        result = self.store._expiry(row, receipt["reason"])
        replacement = receipt.get("replacementRef")
        if replacement:
            owned = self._owned(session, turn, project, replacement)
            if owned:
                result["replacement"] = {"resultRef": replacement, "capturedAt": owned[0]["captured_at"],
                                         "step": {**owned[1]["step"], "scanSnapshot": comparison_metadata(self.store._metadata(owned[0]))}}
        return result

    def lookup(self, session, turn, project, ref, fallback):
        with self._lock:
            owned = self._owned(session, turn, project, ref)
            if owned is None:
                return None
            row, raw, generation, pending = owned
            if raw["step"].get("tool") == _MATERIAL_SCAN_ALIAS:
                # A retained full material index is not comparable to a fresh
                # bounded source page. Recheck its exact Core proof on every
                # read, and never rescan/replace this result reference.
                return fallback(session, turn, project, ref, require_proof=True)
            if not eligible(raw["step"], raw["arguments"]):
                payload = raw["step"].get("result")
                if row["turn"] != turn and (raw["step"].get("tool") not in WHITELIST
                        or not isinstance(payload, dict) or "ok" in payload
                        or (raw["step"].get("tool") == "vrcforge_scan_animation_bindings"
                            and not any(name in raw["arguments"] for name in _SELECTION))):
                    return self.store._expiry(row, "current_turn_only")
                return fallback(session, turn, project, ref)
            prior = self._receipt(session, turn, ref, generation)
            if prior:
                if prior[0] != generation or pending:
                    return self.store._expiry(row, "mutation_after_verification")
                return self._reply(prior[1], raw, row, session, turn, project)
            verdict = {"reason": "mutation_in_progress"}
            self._receipt(session, turn, ref, generation, {"reason": "verification_incomplete"})
            if not pending:
                # The producing turn keeps its existing checked reader path;
                # comparison is for a later turn or an expired current result.
                scoped = fallback(session, turn, project, ref, require_proof=row["turn"] != turn)
                if row["turn"] == turn and scoped and "step" in scoped and not scoped.get("expired"):
                    verdict = {"reuse": True, "verification": "current_turn"}
                elif scoped and scoped.get("scopeValidated") is True:
                    verdict = {"reuse": True, "verification": "core_scope"}
                else:
                    verdict = self._compare(session, turn, project, ref, row, raw, generation)
            latest = self._owned(session, turn, project, ref)
            if latest is None:
                return None
            if latest[2] != generation or latest[3]:
                verdict = {"reason": "mutation_during_verification"}
            verdict = self._receipt(session, turn, ref, generation, verdict, project=project)
            if verdict.get("replacementRef"):
                self._receipt(session, turn, verdict["replacementRef"], generation,
                              {"reuse": True, "verification": "observable_result"}, project=project)
            return self._reply(verdict, raw, row, session, turn, project)

    def _compare(self, session, turn, project, ref, row, raw, generation):
        step, supplied = raw["step"], raw["arguments"]
        arguments = normalized_arguments(step["tool"], supplied)
        digest_mismatch = False
        try:
            fresh = direct_scan(project, step["tool"], arguments)
        except Exception as exc:
            # Guard rejection means the old selection is expired, not a valid
            # empty result. Restart the same selection from its first page.
            if "expectedSnapshotDigest" not in arguments or "snapshot" not in str(exc).lower() \
                    or not any(word in str(exc).lower() for word in ("changed", "mismatch", "match")):
                return {"reason": "comparison_unavailable"}
            arguments.pop("expectedSnapshotDigest")
            for key in ("offset", "clipOffset", "bindingOffset", "keyOffset"):
                if key in arguments:
                    arguments[key] = 0
            digest_mismatch = True
            try:
                fresh = direct_scan(project, step["tool"], arguments)
            except Exception:
                return {"reason": "comparison_unavailable"}
        try:
            if not eligible({**step, "result": fresh}, arguments):
                return {"reason": "comparison_unavailable"}
            digest = business_digest(fresh)
            if not digest_mismatch and digest == business_digest(step["result"]):
                return {"reuse": True, "verification": "observable_result"}
            from agent_tool_result_reader import result_continuation
            from runtime_planner_service import sanitize_planner_observation_text
            replacement = {"index": step.get("index", 0), "actionId": "rescan-" + uuid.uuid4().hex,
                           "tool": step["tool"], "status": "ok", "result": fresh}
            replacement["resultRead"] = result_continuation(session, turn, project, replacement, sanitize_planner_observation_text)
            # Same generation makes the replacement an immutable, distinct scan.
            metadata = self.store.capture(session, turn, project, replacement, arguments, expected_generation=generation)
            replacement["scanSnapshot"] = metadata
            return {"reason": "snapshot_digest_mismatch" if digest_mismatch else "observable_result_changed",
                    "replacementRef": metadata["resultRef"]}
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return {"reason": "comparison_unavailable"}
