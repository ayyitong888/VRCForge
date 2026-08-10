from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from agent_visual_capture_evidence import (
    ManagedVisualCaptureAuthority,
    ManagedVisualCaptureError,
)


TASK_BINDING = {
    "taskId": "task-1",
    "sessionId": "session-1",
    "approvalId": "approval-1",
    "requestedActionId": "action-capture-1",
}


def _capture(path: Path, angle: str) -> dict[str, str]:
    return {"imagePath": str(path), "angle": angle}


def test_managed_capture_receipt_is_exact_hash_bound_and_single_use(tmp_path: Path) -> None:
    managed = tmp_path / "dashboard" / "latest"
    managed.mkdir(parents=True)
    front = managed / "vision_front.png"
    back = managed / "vision_back.png"
    front.write_bytes(b"front")
    back.write_bytes(b"back")
    authority = ManagedVisualCaptureAuthority(managed)

    issued = authority.issue(
        [_capture(front, "front"), _capture(back, "back")],
        project_path="D:/Unity/Project",
        avatar_path="Avatar",
        binding=TASK_BINDING,
    )
    consumed = authority.consume(issued["captureReceipt"], binding=TASK_BINDING)

    assert consumed["captureEvidenceId"] == issued["captureEvidenceId"]
    assert consumed["angles"] == ["front", "back"]
    assert all(item["sha256"] for item in consumed["images"])
    assert consumed["evidence"] == issued["evidence"]
    with pytest.raises(ManagedVisualCaptureError, match="already consumed"):
        authority.consume(issued["captureReceipt"], binding=TASK_BINDING)


def test_managed_capture_rejects_outside_path_and_changed_bytes(tmp_path: Path) -> None:
    managed = tmp_path / "dashboard" / "latest"
    managed.mkdir(parents=True)
    outside = tmp_path / "private.png"
    outside.write_bytes(b"private")
    authority = ManagedVisualCaptureAuthority(managed)

    with pytest.raises(ManagedVisualCaptureError, match="managed capture directory"):
        authority.issue([_capture(outside, "front")], binding=TASK_BINDING)

    managed_image = managed / "vision_front.png"
    managed_image.write_bytes(b"before")
    issued = authority.issue([_capture(managed_image, "front")], binding=TASK_BINDING)
    managed_image.write_bytes(b"after")
    with pytest.raises(ManagedVisualCaptureError, match="changed"):
        authority.consume(issued["captureReceipt"], binding=TASK_BINDING)


def test_managed_capture_expiry_and_capacity_are_bounded(tmp_path: Path) -> None:
    managed = tmp_path / "dashboard" / "latest"
    managed.mkdir(parents=True)
    image = managed / "vision_front.png"
    image.write_bytes(b"image")
    now = [100.0]
    authority = ManagedVisualCaptureAuthority(
        managed,
        clock=lambda: now[0],
        ttl_seconds=2,
        max_outstanding=1,
    )
    first = authority.issue([_capture(image, "front")], binding=TASK_BINDING)
    second = authority.issue([_capture(image, "front")], binding=TASK_BINDING)
    with pytest.raises(ManagedVisualCaptureError, match="invalid"):
        authority.consume(first["captureReceipt"], binding=TASK_BINDING)
    now[0] = 103.0
    with pytest.raises(ManagedVisualCaptureError, match="invalid"):
        authority.consume(second["captureReceipt"], binding=TASK_BINDING)


def test_managed_capture_rejects_cross_task_consumption_without_burning_receipt(tmp_path: Path) -> None:
    managed = tmp_path / "latest"
    managed.mkdir()
    image = managed / "front.png"
    image.write_bytes(b"image")
    authority = ManagedVisualCaptureAuthority(managed)
    issued = authority.issue([_capture(image, "front")], binding=TASK_BINDING)

    with pytest.raises(ManagedVisualCaptureError, match="another task"):
        authority.consume(
            issued["captureReceipt"],
            binding={**TASK_BINDING, "taskId": "task-2"},
        )
    with pytest.raises(ManagedVisualCaptureError, match="another task"):
        authority.consume(
            issued["captureReceipt"],
            binding={**TASK_BINDING, "requestedActionId": "action-other"},
        )

    assert authority.consume(issued["captureReceipt"], binding=TASK_BINDING)[
        "captureEvidenceId"
    ] == issued["captureEvidenceId"]


def test_managed_capture_rejects_hard_links(tmp_path: Path) -> None:
    managed = tmp_path / "latest"
    managed.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"shared")
    hard_link = managed / "front.png"
    try:
        hard_link.hardlink_to(outside)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    authority = ManagedVisualCaptureAuthority(managed)

    with pytest.raises(ManagedVisualCaptureError, match="hard links"):
        authority.issue([_capture(hard_link, "front")], binding=TASK_BINDING)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_managed_capture_rejects_a_junction_in_the_trusted_root_chain(
    tmp_path: Path,
) -> None:
    trusted_anchor = tmp_path / "logical"
    trusted_anchor.mkdir()
    outside_dashboard = tmp_path / "outside-dashboard"
    managed_target = outside_dashboard / "latest"
    managed_target.mkdir(parents=True)
    image = managed_target / "front.png"
    image.write_bytes(b"image")
    junction = trusted_anchor / "dashboard"
    linked = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside_dashboard)],
        capture_output=True,
        text=True,
        check=False,
    )
    if linked.returncode != 0:
        pytest.skip(f"junction creation unavailable: {linked.stderr or linked.stdout}")
    authority = ManagedVisualCaptureAuthority(
        junction / "latest",
        trusted_anchor=trusted_anchor,
    )

    with pytest.raises(ManagedVisualCaptureError, match="trusted ancestors"):
        authority.issue([_capture(junction / "latest" / "front.png", "front")], binding=TASK_BINDING)
