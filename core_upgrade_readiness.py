"""Read-only freshness check for actual compilation-pipeline evidence."""
from collections.abc import Mapping
from datetime import datetime
from os.path import normcase, normpath
from time import monotonic


def await_core_upgrade_snapshot(read, wait, *, timeout=180.0, clock=monotonic):
    """Keep the owned runtime call open while its read receipt requests this same observation."""
    deadline = clock() + timeout
    payload = read()
    while True:
        receipt = payload.get("result") or {}
        if not (
            isinstance(receipt, dict)
            and receipt.get("schema") == "vrcforge.core_upgrade_status.v1"
            and receipt.get("ok") is True
            and receipt.get("ready") is False
            and (receipt.get("nextAction") or {}).get("tool") == "unity_core_upgrade_status"
        ):
            return payload, False
        remaining = deadline - clock()
        if remaining <= 0:
            return payload, True
        if not wait(min(3.0, remaining)):
            return payload, False  # The runtime owns cancellation and records the last observation.
        if clock() >= deadline:
            return payload, True
        # Reuse the original validated read and its bound scope, never a write or a suggested argument.
        payload = read()


def compile_snapshot_is_fresh(snapshot, installed_at):
    if not isinstance(snapshot, dict) or snapshot.get("source") != "compilation_pipeline":
        return False
    if snapshot.get("captureComplete") is not True or snapshot.get("isCompiling") is not False:
        return False
    if type(snapshot.get("errorCount")) is not int or snapshot["errorCount"] < 0:
        return False
    try:
        captured = datetime.fromisoformat(str(snapshot.get("capturedAt") or "").replace("Z", "+00:00"))
        if captured.utcoffset() is None:
            return False
        if not installed_at:
            return True  # Health query without an installation freshness boundary.
        installed = datetime.fromisoformat(installed_at.replace("Z", "+00:00"))
        return installed.utcoffset() is not None and captured >= installed
    except (ValueError, TypeError, OverflowError):
        return False

def editor_process_receipt(result):
    """The OS confirmed this launch or discovery; Core connectivity is a separate read."""
    return {
        **result, "schema": "vrcforge.unity_editor_launch.v1", "persistent": False,
        "operationStatus": "no_change" if result["status"] == "already_open" else "success",
        "readbackVerified": True,
        "readback": {key: result[key] for key in ("projectPath", "processId", "status")},
    }


def installed_core_receipt(result):
    """Called only after the installer has copied and checked the managed Core files."""
    return {
        **result, "commitState": "complete", "persistenceState": "persisted", "readbackVerified": True,
        "readback": {"scope": "installed_core_files", "projectPath": result["projectPath"],
                     "installedSha256": result["installedSha256"]},
    }


def core_action_binding(tool, views):
    for receipt in views:
        schema = receipt.get("schema")
        if not receipt.get("projectPath") or receipt.get("ok") is not True:
            continue
        project = normcase(normpath(receipt["projectPath"]))
        if tool == "vrcforge_install_unity_core" and schema == "vrcforge.unity_core_install.v1" and receipt.get("installedAt"):
            return {"projectPath": project, "installedAt": receipt["installedAt"]}
        if tool == "vrcforge_open_unity_project" and schema == "vrcforge.unity_editor_launch.v1" and type(receipt.get("processId")) is int:
            return {"projectPath": project, "processId": receipt["processId"]}
    return None


def ready_process_binding(receipt):
    core = receipt.get("runtimeCoreInfo")
    if not (receipt.get("ready") is True and receipt.get("runtimeIdentityMatchesTarget") is True
            and receipt.get("compileSnapshotFresh") is True and receipt.get("consoleErrorCount") == 0
            and isinstance(core, Mapping) and type(core.get("processId")) is int and core["processId"] > 0):
        return None
    return {"projectPath": normcase(normpath(receipt["projectPath"])), "installedAt": receipt["installedAt"],
            "processId": core["processId"]}
