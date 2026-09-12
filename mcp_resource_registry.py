"""Shared MCP Resource registry for internal and external VRCForge Agents.

The registry owns JSON snapshots under the Gateway audit directory.  It never
scans Unity or resolves a hierarchy path: producers must explicitly publish a
captured value, and readers can only retrieve an already-published immutable
revision.  Authentication remains owned by the loopback Gateway MCP boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from backend_owner_lease import BackendOwnerLease


RESOURCE_SCHEMA = "vrcforge.resource.v1"
RESOURCE_MIME_TYPE = "application/vnd.vrcforge.resource+json"


RESOURCE_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "uriTemplate": "vrcforge://session/{sessionId}/identity?revision={revision}",
        "name": "Session Identity Lock",
        "description": "Exact project, Unity process, Core, Scene, Avatar, object and component identity captured for one session; unavailable fields are explicit and hierarchy paths are display-only.",
        "mimeType": RESOURCE_MIME_TYPE,
        "resourceType": "session_identity_lock",
    },
    {
        "uriTemplate": "vrcforge://snapshot/{scope}/{stableId}?revision={revision}",
        "name": "Unity Snapshot",
        "description": "Previously captured Scene, object, component or asset snapshot. Reading never performs an implicit Unity scan.",
        "mimeType": RESOURCE_MIME_TYPE,
        "resourceType": "unity_snapshot",
    },
    {
        "uriTemplate": "vrcforge://operation/{operationId}/receipt?revision={revision}",
        "name": "Operation Receipt",
        "description": "Immutable read or supervised-write result, including operationId, ExecutionTarget digest, commit and readback state.",
        "mimeType": RESOURCE_MIME_TYPE,
        "resourceType": "operation_receipt",
    },
    {
        "uriTemplate": "vrcforge://checkpoint/{checkpointId}/diff?revision={revision}",
        "name": "Checkpoint Diff",
        "description": "Previously captured checkpoint or diff evidence; reading cannot create, restore or mutate a checkpoint.",
        "mimeType": RESOURCE_MIME_TYPE,
        "resourceType": "checkpoint_diff",
    },
    {
        "uriTemplate": "vrcforge://catalog/tools/{catalogGeneration}?revision={revision}",
        "name": "Tool Catalog Snapshot",
        "description": "Immutable projection of the shared internal/external Tool registry for one catalog generation.",
        "mimeType": RESOURCE_MIME_TYPE,
        "resourceType": "tool_catalog",
    },
    {
        "uriTemplate": "vrcforge://control-graph/{graphId}?revision={revision}",
        "name": "Control Graph",
        "description": "Previously captured avatar control-graph evidence such as parameters, menus and animation links.",
        "mimeType": RESOURCE_MIME_TYPE,
        "resourceType": "control_graph",
    },
    {
        "uriTemplate": "vrcforge://gesture-manager/{sessionId}/runtime?revision={revision}",
        "name": "Gesture Manager Runtime",
        "description": "Previously captured Gesture Manager runtime state and test evidence; reading does not enter Play Mode.",
        "mimeType": RESOURCE_MIME_TYPE,
        "resourceType": "gm_runtime",
    },
)


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _content_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _revision_uri(base_uri: str, revision: int) -> str:
    parts = urlsplit(base_uri)
    query = parse_qs(parts.query, keep_blank_values=True)
    query["revision"] = [str(revision)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment))


def _base_uri(uri: str) -> str:
    parts = urlsplit(uri)
    query = parse_qs(parts.query, keep_blank_values=True)
    query.pop("revision", None)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment))


class McpResourceError(ValueError):
    pass


class McpResourceRegistry:
    """Shared on-disk Resource store with transaction-locked immutable revisions."""

    def __init__(self, store_dir: Path, *, lock: threading.RLock | None = None) -> None:
        self.store_dir = Path(store_dir)
        self._lock = lock or threading.RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._latest: dict[str, str] = {}
        # Immutable revision bytes belong to this registry lifetime and lock.
        # They cache serialization only, never authorization or target freshness.
        self._encoded_records: dict[str, bytes] = {}
        self._generation = 0
        self._disk_stamp = None
        self._disk_lease = BackendOwnerLease(self.store_dir / "registry.lock")
        # Public reads and publishes load under the existing disk guard.
        # Large historical indexes must not delay Gateway startup.

    @property
    def generation(self) -> int:
        self._load()
        with self._lock:
            return self._generation

    def templates(self) -> list[dict[str, Any]]:
        return _json_clone(RESOURCE_TEMPLATES)

    def publish(
        self,
        *,
        base_uri: str,
        name: str,
        resource_type: str,
        data: Any,
        identity: Mapping[str, Any] | None = None,
        source_mode: str,
        refresh_rule: str,
        description: str = "",
        stale: bool = False,
        stale_reason: str = "",
        only_if_absent: bool = False,
    ) -> dict[str, Any]:
        return self.publish_many([{
            "base_uri": base_uri, "name": name, "resource_type": resource_type,
            "data": data, "identity": identity, "source_mode": source_mode,
            "refresh_rule": refresh_rule, "description": description, "stale": stale,
            "stale_reason": stale_reason, "only_if_absent": only_if_absent,
        }])[0]

    def publish_many(self, entries: Sequence[Mapping[str, Any]], *, validate_records=None) -> list[dict[str, Any]]:
        """Publish a finite resource batch with one atomic index replacement.

        Instance and OS file locks cover reload, staging, persistence and commit;
        failure restores the previous in-memory view, while atomic replacement
        preserves the prior disk index. Existing concurrent records are retained.
        """
        prepared = _json_clone(list(entries))
        if not prepared:
            return []
        with self._lock, self._disk_guard():
            self._load_locked()
            before = self._records, self._latest, self._generation
            self._records, self._latest = dict(self._records), dict(self._latest)
            try:
                records = [self._publish_staged(**entry) for entry in prepared]
                if validate_records is not None:
                    validate_records(records)
                if self._generation != before[2]:
                    self._persist_locked()
                return records
            except Exception:
                self._records, self._latest, self._generation = before
                raise

    def _publish_staged(
        self,
        *,
        base_uri: str,
        name: str,
        resource_type: str,
        data: Any,
        identity: Mapping[str, Any] | None = None,
        source_mode: str,
        refresh_rule: str,
        description: str = "",
        stale: bool = False,
        stale_reason: str = "",
        only_if_absent: bool = False,
    ) -> dict[str, Any]:
        if not base_uri.startswith("vrcforge://") or "?revision=" in base_uri:
            raise McpResourceError("base_uri must be a revision-free vrcforge:// URI")
        if not name.strip() or not resource_type.strip():
            raise McpResourceError("name and resource_type are required")
        cloned_data = _json_clone(data)
        cloned_identity = _json_clone(identity or {})
        captured_at = datetime.now(timezone.utc).isoformat()
        payload_hash = _content_hash({"identity": cloned_identity, "data": cloned_data})
        with self._lock:
            latest_uri = self._latest.get(base_uri)
            latest = self._records.get(latest_uri or "")
            if latest and only_if_absent:
                return _json_clone(latest)
            if latest and latest.get("contentHash") == payload_hash and bool(latest.get("stale")) == stale:
                return _json_clone(latest)
            revision = int(latest.get("revision", 0) if latest else 0) + 1
            uri = _revision_uri(base_uri, revision)
            envelope = {
                "schema": RESOURCE_SCHEMA,
                "uri": uri,
                "canonicalUri": base_uri,
                "name": name,
                "description": description or name,
                "resourceType": resource_type,
                "schemaVersion": 1,
                "identity": cloned_identity,
                "revision": revision,
                "contentHash": payload_hash,
                "capturedAt": captured_at,
                "sourceMode": source_mode,
                "stale": bool(stale),
                "staleReason": stale_reason if stale else "",
                "refreshRule": refresh_rule,
                "data": cloned_data,
            }
            self._records[uri] = envelope
            self._latest[base_uri] = uri
            self._generation += 1
            return _json_clone(envelope)

    def list(self, *, cursor: str = "", page_size: int = 100) -> dict[str, Any]:
        if page_size < 1 or page_size > 500:
            raise McpResourceError("page_size must be between 1 and 500")
        try:
            offset = int(cursor or "0")
        except ValueError as exc:
            raise McpResourceError("cursor must be a non-negative integer") from exc
        if offset < 0:
            raise McpResourceError("cursor must be a non-negative integer")
        self._load()
        with self._lock:
            records = sorted(
                (self._records[uri] for uri in self._latest.values()),
                key=lambda item: str(item["uri"]),
            )
            page = records[offset : offset + page_size]
            next_offset = offset + len(page)
            resources = [
                {
                    "uri": item["uri"],
                    "name": item["name"],
                    "description": item["description"],
                    "mimeType": "image/png" if item["resourceType"] in {"runtime_observation_frame", "texture_asset_preview"} else RESOURCE_MIME_TYPE,
                    "_meta": {
                        "resourceType": item["resourceType"],
                        "revision": item["revision"],
                        "contentHash": item["contentHash"],
                        "stale": item["stale"],
                        "identity": item["identity"],
                    },
                }
                for item in page
            ]
            return {
                "resources": resources,
                "resourceGeneration": self._generation,
                **({"nextCursor": str(next_offset)} if next_offset < len(records) else {}),
            }

    def read(self, uri: str) -> dict[str, Any]:
        if not isinstance(uri, str) or not uri.startswith("vrcforge://"):
            raise McpResourceError("resources/read requires a vrcforge:// URI")
        self._load()
        with self._lock:
            selected_uri = uri
            if "revision=" not in urlsplit(uri).query:
                selected_uri = self._latest.get(_base_uri(uri), "")
            record = self._records.get(selected_uri)
            if record is None:
                raise McpResourceError(
                    "Resource was not previously captured; use a read Tool to capture it explicitly before resources/read"
                )
            envelope = _json_clone(record)
        if envelope["resourceType"] == "runtime_observation_frame":
            from runtime_frame_resources import read_published_frame
            return read_published_frame(envelope)
        if envelope["resourceType"] == "texture_asset_preview":
            from texture_preview_resources import read_texture_preview
            return read_texture_preview(envelope)
        if envelope["resourceType"] == "runtime_observation_state_page":
            from runtime_state_resources import read_state_page
            return read_state_page(envelope)
        return {
            "contents": [
                {
                    "uri": envelope["uri"],
                    "mimeType": RESOURCE_MIME_TYPE,
                    "text": json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                }
            ],
            "structuredContent": envelope,
        }

    def validate_reference(
        self,
        uri: str,
        *,
        expected_type: str | None = None,
        expected_identity: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate an exact, immutable Resource reference for a Prompt context."""
        if not isinstance(uri, str) or not uri.startswith("vrcforge://"):
            raise McpResourceError("Resource reference must be a vrcforge:// URI")
        parts = urlsplit(uri)
        query = parse_qs(parts.query, keep_blank_values=True)
        revisions = query.get("revision") or []
        if len(revisions) != 1 or not revisions[0].isdigit() or int(revisions[0]) < 1:
            raise McpResourceError("Resource reference must include one positive revision")
        self._load()
        with self._lock:
            envelope = self._records.get(uri)
            if envelope is None:
                raise McpResourceError("Resource reference is unknown or stale")
            selected = _json_clone(envelope)
            if expected_type == "session_identity_lock" and self._latest.get(_base_uri(uri)) != uri:
                raise McpResourceError("Session Identity Lock revision is no longer current")
        if expected_type and selected.get("resourceType") != expected_type:
            raise McpResourceError("Resource reference has the wrong resource type")
        if selected.get("stale"):
            raise McpResourceError("Resource reference is stale")
        if expected_identity:
            actual = selected.get("identity")
            if not isinstance(actual, Mapping) or any(
                actual.get(key) != value for key, value in expected_identity.items()
            ):
                raise McpResourceError("Resource reference does not match the expected identity")
        return selected

    def _index_path(self) -> Path:
        return self.store_dir / "registry.json"

    def _load(self) -> None:
        with self._lock, self._disk_guard():
            self._load_locked()

    @contextmanager
    def _disk_guard(self):
        # The existing OS lease owns one local registry.lock handle only for
        # this <=5s acquisition/transaction; release/close is guaranteed. No
        # network interface or new auth boundary: Gateway authorizes callers.
        deadline = time.monotonic() + 5.0
        while not self._disk_lease.acquire():
            if time.monotonic() >= deadline:
                raise McpResourceError("Resource registry is busy or unavailable; no publication was committed.")
            time.sleep(0.02)
        try:
            yield
        finally:
            self._disk_lease.release()

    def _index_stamp(self):
        try:
            stat = self._index_path().stat()
            return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
        except FileNotFoundError:
            return None

    def _load_locked(self) -> None:
        stamp = self._index_stamp()
        if stamp == self._disk_stamp:
            return
        path = self._index_path()
        if stamp is None:
            self._records, self._latest, self._encoded_records = {}, {}, {}
            self._generation, self._disk_stamp = 0, None
            return
        try:
            payload = json.loads(path.read_bytes().decode("utf-8"))
            records = payload.get("records") if isinstance(payload, Mapping) else None
            latest = payload.get("latest") if isinstance(payload, Mapping) else None
            if isinstance(records, Mapping) and isinstance(latest, Mapping):
                loaded = {str(key): dict(value) for key, value in records.items() if isinstance(value, Mapping)}
                self._encoded_records = {uri: value for uri, value in self._encoded_records.items() if self._records.get(uri) == loaded.get(uri)}
                self._records = loaded
                self._latest = {str(key): str(value) for key, value in latest.items()}
                self._generation = int(payload.get("generation", len(self._records)))
                self._disk_stamp = stamp
            else:
                raise ValueError("Missing resource index records or latest mapping")
        except (OSError, ValueError, TypeError) as exc:
            raise McpResourceError("Resource registry could not be loaded; existing index was not replaced.") from exc

    def _persist_locked(self) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        path = self._index_path()
        temporary = path.with_name(f".registry.{os.getpid()}.{secrets.token_hex(8)}.tmp")
        header = {
            "generation": self._generation,
            "latest": self._latest,
        }
        encoded = {}
        for uri, record in self._records.items():
            cached = self._encoded_records.get(uri)
            if cached is None:
                cached = json.dumps(
                    {uri: record}, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False,
                )[1:-1].encode("utf-8")
            encoded[uri] = cached
        # Stream the same JSON index; avoid reencoding or joining all historical
        # image/state payloads whenever a small new operation receipt is added.
        try:
            with temporary.open("xb") as stream:
                stream.write(json.dumps(
                    header, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False,
                )[:-1].encode("utf-8") + b',"records":{')
                for index, uri in enumerate(sorted(encoded)):
                    if index:
                        stream.write(b",")
                    stream.write(encoded[uri])
                stream.write(b'},"schema":"vrcforge.resource_registry.v1"}')
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(5):
                try:
                    temporary.replace(path)
                    break
                except PermissionError as exc:
                    # Bounded replacement-only retry for transient Windows
                    # sharing/access denial; never re-run a tool or mutation.
                    if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 4:
                        raise
                    time.sleep(0.05)
        finally:
            temporary.unlink(missing_ok=True)
        # A failed write/replace must not cache a revision that the enclosing
        # publish_many rolls back and may later reuse for a different value.
        self._encoded_records = encoded
        self._disk_stamp = self._index_stamp()


__all__ = [
    "McpResourceError",
    "McpResourceRegistry",
    "RESOURCE_MIME_TYPE",
    "RESOURCE_SCHEMA",
    "RESOURCE_TEMPLATES",
]
