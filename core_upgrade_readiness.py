"""Read-only freshness check for actual compilation-pipeline evidence."""
from datetime import datetime


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
