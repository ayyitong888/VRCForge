import json
import sys
import zipfile
from tempfile import TemporaryDirectory
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dashboard_server
from dashboard_server import build_support_feedback_preview, preview_latest_support_bundle
from diagnostic_privacy import DiagnosticPrivacy


def test_feedback_preview_redacts_text_and_requires_review() -> None:
    with TemporaryDirectory() as temp_dir:
        privacy = DiagnosticPrivacy(Path(temp_dir), current_user_fn=lambda: "Alice")
        with patch.object(dashboard_server, "DIAGNOSTIC_PRIVACY", privacy):
            result = build_support_feedback_preview(
                {
                    "summary": "Provider failed with api_key=secret-value Bearer abcdefghijklmnop",
                    "steps": "Open C:\\Users\\Alice\\Project",
                    "supportBundleAvailable": True,
                },
                redact=dashboard_server.redact_support_payload,
            )
    assert result["redacted"] is True
    assert result["reviewRequired"] is True
    assert result["externalSubmissionAuthorized"] is False
    assert "secret-value" not in result["text"]
    assert "[REDACTED]" in result["text"]
    assert "Alice" not in result["text"]


def test_latest_bundle_preview_returns_actual_redacted_member_content(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    bundle_path = bundle_dir / "vrcforge-support-20260924-000000.zip"
    with zipfile.ZipFile(bundle_path, "w") as bundle:
        bundle.writestr("metadata.json", json.dumps({"token": "secret", "ok": True}))
        bundle.writestr("long.txt", "x" * 2000)
    def redact(value):
        return {"token": "[REDACTED]", "ok": True} if isinstance(value, dict) else value

    result = preview_latest_support_bundle(bundle_dir, redact=redact)
    assert result["available"] is True
    assert result["bundleName"] == bundle_path.name
    assert result["members"][0]["name"] == "metadata.json"
    assert "[REDACTED]" in result["members"][0]["preview"]
    assert "secret" not in result["members"][0]["preview"]
    assert len(result["bundleSha256"]) == 64
    long_member = next(member for member in result["members"] if member["name"] == "long.txt")
    assert long_member["truncated"] is True
