from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from session_store_integrity import scan_session_store


def inspect_project_chat_store(params: dict[str, Any] | None, *, resolve_project_root: Callable[[str], Path | None], target_factory: Callable[[Path], Any]) -> dict[str, Any]:
    value = str((params or {}).get("projectPath") or "").strip()
    root = resolve_project_root(value) if value else None
    if root is None:
        return {"ok": False, "status": "invalid_project_root", "changed": False}
    target = target_factory(root)
    scan = scan_session_store(target)
    status = str(scan.get("status") or "unknown")
    digest = str(scan.get("digest") or "")
    return {
        "ok": True,
        "schema": "vrcforge.project_chat_store_inspection.v1",
        "status": status,
        "projectPath": str(root),
        "storeId": target.store_id,
        "basename": target.path.name,
        "exists": bool(scan.get("exists")),
        "digest": digest,
        "recordCount": int(scan.get("recordCount") or 0),
        "invalidCount": int(scan.get("invalidCount") or 0),
        "unknownSchemaCount": int(scan.get("unknownSchemaCount") or 0),
        "repairRequired": status == "needs_repair",
        "repairHint": ({"tool": "vrcforge_repair_project_chat_store", "projectPath": str(root), "expectedDigest": digest, "storeId": target.store_id, "requiresApproval": True} if status == "needs_repair" and digest else None),
    }
