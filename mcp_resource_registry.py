"""Shared MCP Resource registry for internal and external VRCForge Agents.

The registry owns SQLite snapshots under the Gateway audit directory.
Storage v2 is a breaking update: legacy registry.json is preserved but never read;
old immutable handles must be captured again. Each database owns a URI epoch.  It never
scans Unity or resolves a hierarchy path: producers must explicitly publish a
captured value, and readers can only retrieve an already-published immutable
revision.  Authentication remains owned by the loopback Gateway MCP boundary.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
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
        "uriTemplate": "vrcforge://session/{sessionId}/identity?revision={revision}&store={store}",
        "name": "Session Identity Lock",
        "description": "Exact project, Unity process, Core, Scene, Avatar, object and component identity captured for one session; unavailable fields are explicit and hierarchy paths are display-only.",
        "mimeType": RESOURCE_MIME_TYPE,
        "resourceType": "session_identity_lock",
    },
    {
        "uriTemplate": "vrcforge://snapshot/{scope}/{stableId}?revision={revision}&store={store}",
        "name": "Unity Snapshot",
        "description": "Previously captured Scene, object, component or asset snapshot. Reading never performs an implicit Unity scan.",
        "mimeType": RESOURCE_MIME_TYPE,
        "resourceType": "unity_snapshot",
    },
    {
        "uriTemplate": "vrcforge://operation/{operationId}/receipt?revision={revision}&store={store}",
        "name": "Operation Receipt",
        "description": "Immutable read or supervised-write result, including operationId, ExecutionTarget digest, commit and readback state.",
        "mimeType": RESOURCE_MIME_TYPE,
        "resourceType": "operation_receipt",
    },
    {
        "uriTemplate": "vrcforge://checkpoint/{checkpointId}/diff?revision={revision}&store={store}",
        "name": "Checkpoint Diff",
        "description": "Previously captured checkpoint or diff evidence; reading cannot create, restore or mutate a checkpoint.",
        "mimeType": RESOURCE_MIME_TYPE,
        "resourceType": "checkpoint_diff",
    },
    {
        "uriTemplate": "vrcforge://catalog/tools/{catalogGeneration}?revision={revision}&store={store}",
        "name": "Tool Catalog Snapshot",
        "description": "Immutable projection of the shared internal/external Tool registry for one catalog generation.",
        "mimeType": RESOURCE_MIME_TYPE,
        "resourceType": "tool_catalog",
    },
    {
        "uriTemplate": "vrcforge://control-graph/{graphId}?revision={revision}&store={store}",
        "name": "Control Graph",
        "description": "Previously captured avatar control-graph evidence such as parameters, menus and animation links.",
        "mimeType": RESOURCE_MIME_TYPE,
        "resourceType": "control_graph",
    },
    {
        "uriTemplate": "vrcforge://gesture-manager/{sessionId}/runtime?revision={revision}&store={store}",
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


def _revision_uri(base_uri: str, revision: int, epoch: str) -> str:
    parts = urlsplit(base_uri)
    query = parse_qs(parts.query, keep_blank_values=True)
    query["revision"] = [str(revision)]
    query["store"] = [epoch]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment))


def _base_uri(uri: str) -> str:
    parts = urlsplit(uri)
    query = parse_qs(parts.query, keep_blank_values=True)
    query.pop("revision", None)
    query.pop("store", None)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment))


class McpResourceError(ValueError):
    pass


class McpResourceRegistry:
    """Shared on-disk Resource store with transaction-locked immutable revisions."""

    def __init__(self, store_dir: Path, *, lock: threading.RLock | None = None) -> None:
        self.store_dir = Path(store_dir)
        self._lock = lock or threading.RLock()
        self._disk_lease = BackendOwnerLease(self.store_dir / "registry.lock")
        self._connection = None
        # Construction does not open a database or inspect legacy history.

    @property
    def generation(self) -> int:
        with self._transaction() as connection:
            return int(connection.execute("SELECT generation FROM metadata").fetchone()[0])

    def revision_uri(self, base_uri: str, revision: int) -> str:
        """Build an immutable handle in this database's persistent namespace."""
        with self._transaction() as connection:
            epoch = connection.execute("SELECT epoch FROM metadata").fetchone()[0]
            return _revision_uri(base_uri, revision, epoch)

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
        """Publish a validated finite batch in one SQLite transaction.

        Only affected revisions are read or written. A failure, including domain
        validation or commit, rolls back the entire batch and revision counters.
        """
        prepared = _json_clone(list(entries))
        if not prepared:
            return []
        with self._transaction(write=True) as connection:
            before = connection.total_changes
            records = [self._publish_staged(**entry) for entry in prepared]
            if validate_records is not None:
                validate_records(records)
            if connection.total_changes != before:
                self._persist_locked()
            return records

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
        if not base_uri.startswith("vrcforge://") or {"revision", "store"}.intersection(parse_qs(urlsplit(base_uri).query, keep_blank_values=True)):
            raise McpResourceError("base_uri must be a revision-free vrcforge:// URI")
        if not name.strip() or not resource_type.strip():
            raise McpResourceError("name and resource_type are required")
        cloned_data = _json_clone(data)
        cloned_identity = _json_clone(identity or {})
        captured_at = datetime.now(timezone.utc).isoformat()
        payload_hash = _content_hash({"identity": cloned_identity, "data": cloned_data})
        connection = self._connection
        row = connection.execute("SELECT r.payload FROM latest l JOIN records r ON r.uri=l.uri WHERE l.base_uri=?", (base_uri,)).fetchone()
        latest = json.loads(row[0]) if row else None
        if latest and only_if_absent:
            return latest
        if latest and latest.get("contentHash") == payload_hash and bool(latest.get("stale")) == stale:
            return latest
        revision = int(latest.get("revision", 0) if latest else 0) + 1
        epoch = connection.execute("SELECT epoch FROM metadata").fetchone()[0]
        uri = _revision_uri(base_uri, revision, epoch)
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
        connection.execute("INSERT INTO records(uri,payload) VALUES (?,?)", (uri, json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), allow_nan=False)))
        connection.execute("INSERT INTO latest(base_uri,uri) VALUES (?,?) ON CONFLICT(base_uri) DO UPDATE SET uri=excluded.uri", (base_uri, uri))
        connection.execute("UPDATE metadata SET generation=generation+1")
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
        with self._transaction() as connection:
            # Newly published immutable receipts come first so list-first hosts
            # can discover the operation they just invoked without old pages.
            rows = connection.execute("SELECT r.payload FROM latest l JOIN records r ON r.uri=l.uri ORDER BY r.rowid DESC LIMIT ? OFFSET ?", (page_size + 1, offset)).fetchall()
            page = [json.loads(row[0]) for row in rows[:page_size]]
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
                "resourceGeneration": int(connection.execute("SELECT generation FROM metadata").fetchone()[0]),
                **({"nextCursor": str(next_offset)} if len(rows) > page_size else {}),
            }

    def read(self, uri: str) -> dict[str, Any]:
        if not isinstance(uri, str) or not uri.startswith("vrcforge://"):
            raise McpResourceError("resources/read requires a vrcforge:// URI")
        with self._transaction() as connection:
            query = parse_qs(urlsplit(uri).query, keep_blank_values=True)
            stores = query.get("store")
            if stores is not None and stores != [connection.execute("SELECT epoch FROM metadata").fetchone()[0]]:
                raise McpResourceError("Resource store is retired or unknown; explicitly recapture with a read Tool")
            if "revision" not in query:
                row = connection.execute("SELECT r.payload FROM latest l JOIN records r ON r.uri=l.uri WHERE l.base_uri=?", (_base_uri(uri),)).fetchone()
            else:
                row = connection.execute("SELECT payload FROM records WHERE uri=?", (uri,)).fetchone()
            if row is None:
                raise McpResourceError("Resource was not previously captured in this store; legacy or retired handles must explicitly recapture with a read Tool")
            envelope = json.loads(row[0])
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
        with self._transaction() as connection:
            row = connection.execute("SELECT payload FROM records WHERE uri=?", (uri,)).fetchone()
            if row is None:
                raise McpResourceError("Resource reference is unknown or stale; legacy or retired handles require explicit recapture")
            selected = json.loads(row[0])
            if expected_type == "session_identity_lock":
                latest = connection.execute("SELECT uri FROM latest WHERE base_uri=?", (_base_uri(uri),)).fetchone()
                if latest is None or latest[0] != uri:
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
        return self.store_dir / "registry-v2.sqlite3"

    @contextmanager
    def _transaction(self, *, write=False):
        # One current-user local file connection per operation; the caller's
        # Gateway session remains the authorization boundary. The connection,
        # journal handles and OS lease always close before returning.
        with self._lock, self._disk_guard():
            self.store_dir.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self._index_path(), timeout=5, isolation_level=None)
            try:
                connection.execute("PRAGMA synchronous=FULL")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), epoch TEXT NOT NULL, generation INTEGER NOT NULL)")
                    connection.execute("CREATE TABLE records (uri TEXT PRIMARY KEY, payload TEXT NOT NULL)")
                    connection.execute("CREATE TABLE latest (base_uri TEXT PRIMARY KEY, uri TEXT NOT NULL)")
                    connection.execute("CREATE INDEX latest_uri ON latest(uri)")
                    connection.execute("INSERT INTO metadata VALUES (1, ?, 0)", (secrets.token_hex(16),))
                    connection.execute("PRAGMA user_version=2")
                    connection.commit()
                elif version != 2:
                    raise McpResourceError("Unsupported Resource database version; existing store was not changed")
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                self._connection = connection
                yield connection
                if connection.in_transaction:
                    connection.rollback()
            except sqlite3.Error as exc:
                raise McpResourceError("Resource registry could not be loaded or committed; no publication was committed.") from exc
            finally:
                self._connection = None
                connection.close()

    def _persist_locked(self) -> None:
        self._connection.commit()

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



__all__ = [
    "McpResourceError",
    "McpResourceRegistry",
    "RESOURCE_MIME_TYPE",
    "RESOURCE_SCHEMA",
    "RESOURCE_TEMPLATES",
]
