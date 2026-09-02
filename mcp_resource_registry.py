"""Shared MCP Resource registry for internal and external VRCForge Agents.

The registry owns JSON snapshots under the Gateway audit directory.  It never
scans Unity or resolves a hierarchy path: producers must explicitly publish a
captured value, and readers can only retrieve an already-published immutable
revision.  Authentication remains owned by the loopback Gateway MCP boundary.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


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
    """Process-owned, lock-protected Resource store with immutable revisions."""

    def __init__(self, store_dir: Path, *, lock: threading.RLock | None = None) -> None:
        self.store_dir = Path(store_dir)
        self._lock = lock or threading.RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._latest: dict[str, str] = {}
        self._generation = 0
        self._load()

    @property
    def generation(self) -> int:
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
            self._persist_locked()
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
                    "mimeType": RESOURCE_MIME_TYPE,
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

    def _index_path(self) -> Path:
        return self.store_dir / "registry.json"

    def _load(self) -> None:
        path = self._index_path()
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            records = payload.get("records") if isinstance(payload, Mapping) else None
            latest = payload.get("latest") if isinstance(payload, Mapping) else None
            if isinstance(records, Mapping) and isinstance(latest, Mapping):
                self._records = {str(key): dict(value) for key, value in records.items() if isinstance(value, Mapping)}
                self._latest = {str(key): str(value) for key, value in latest.items()}
                self._generation = int(payload.get("generation", len(self._records)))
        except (OSError, ValueError, TypeError):
            self._records = {}
            self._latest = {}
            self._generation = 0

    def _persist_locked(self) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        path = self._index_path()
        temporary = path.with_suffix(".tmp")
        payload = {
            "schema": "vrcforge.resource_registry.v1",
            "generation": self._generation,
            "latest": self._latest,
            "records": self._records,
        }
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )
        temporary.replace(path)


__all__ = [
    "McpResourceError",
    "McpResourceRegistry",
    "RESOURCE_MIME_TYPE",
    "RESOURCE_SCHEMA",
    "RESOURCE_TEMPLATES",
]
