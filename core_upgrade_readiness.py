"""Read-only freshness check for actual compilation-pipeline evidence."""
from datetime import datetime
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
