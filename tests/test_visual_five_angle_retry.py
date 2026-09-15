"""The default five-angle capture survives a transient provider failure."""
import dashboard_server as dashboard
from agent_visual_capture_evidence import ManagedVisualCaptureAuthority
from test_agent_visual_capture_evidence import TASK_BINDING


def test_default_capture_can_retry_all_five_frozen_images(tmp_path, monkeypatch):
    authority = ManagedVisualCaptureAuthority(tmp_path)
    captures = []
    for angle in dashboard.VisionCaptureMultiRequest().angles:
        path = tmp_path / f"{angle}.png"
        path.write_bytes(angle.encode())
        captures.append({"imagePath": str(path), "angle": angle})
    issued = authority.issue(captures, binding=TASK_BINDING)
    monkeypatch.setattr(dashboard, "MANAGED_VISUAL_CAPTURE_AUTHORITY", authority)
    monkeypatch.setattr(dashboard, "_audit_verified_avatar_images", lambda *a, **k: {
        "ok": False, "results": [{"status": "failed", "providerError": {
            "retryable": True, "retainImages": True, "error": "temporary provider failure"
        }}]
    })
    result = dashboard.audit_managed_avatar_multi_screenshot_sync(
        dashboard.ManagedVisionAuditMultiRequest(captureReceipt=issued["captureReceipt"]),
        task_binding=TASK_BINDING,
    )
    assert result["ok"] is False
    assert result["retryable"] is True and result["retainImages"] is True
    retried = authority.consume(result["captureReceipt"], binding=TASK_BINDING)
    assert retried["captureEvidenceId"] == issued["captureEvidenceId"]
    assert len(retried["images"]) == 5
    assert [item["imageBytes"] for item in retried["images"]] == [
        item["angle"].encode() for item in captures
    ]
