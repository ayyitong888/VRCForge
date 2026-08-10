"""Process-owned capabilities for VRCForge-managed visual capture evidence.

The authority deliberately owns no Provider or Unity capability.  It only
binds files created in the dashboard capture directory to a short-lived,
single-use opaque token and verifies that the bytes did not change before a
later visual audit reads them.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import stat
from threading import RLock
import time
from typing import Any, Callable, Mapping, Sequence


_ALLOWED_ANGLES = frozenset({"front", "side_left", "side_right", "back"})


class ManagedVisualCaptureError(RuntimeError):
    """Raised when a capture capability is invalid, stale, or no longer exact."""


@dataclass(frozen=True)
class _ManagedImage:
    path: str
    angle: str
    sha256: str
    size: int


@dataclass(frozen=True)
class _CaptureRecord:
    evidence_id: str
    issued_at: float
    expires_at: float
    project_digest: str
    avatar_digest: str
    task_id: str
    session_id: str
    approval_id: str
    requested_action_id: str
    images: tuple[_ManagedImage, ...]


def _digest_text(value: Any) -> str:
    normalized = str(value or "").strip()
    return hashlib.sha256(normalized.encode("utf-8", errors="surrogatepass")).hexdigest()


class ManagedVisualCaptureAuthority:
    """Issue and consume bounded visual evidence inside one backend process.

    Permission scope: read metadata and bytes only below ``managed_root``.
    Lifecycle owner: the backend process; records expire and are capacity-bound.
    Authentication: an unguessable opaque token that is consumed once.
    """

    def __init__(
        self,
        managed_root: Path,
        *,
        trusted_anchor: Path | None = None,
        clock: Callable[[], float] = time.time,
        ttl_seconds: int = 300,
        max_outstanding: int = 128,
        max_image_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        root = Path(managed_root)
        if not callable(clock):
            raise TypeError("clock must be callable")
        if ttl_seconds < 1 or max_outstanding < 1 or max_image_bytes < 1:
            raise ValueError("Managed visual evidence bounds must be positive.")
        self._root = root
        self._trusted_anchor = Path(trusted_anchor) if trusted_anchor is not None else root.parent
        self._clock = clock
        self._ttl_seconds = int(ttl_seconds)
        self._max_outstanding = int(max_outstanding)
        self._max_image_bytes = int(max_image_bytes)
        self._records: dict[str, _CaptureRecord] = {}
        self._lock = RLock()

    @staticmethod
    def _absolute_lexical(path: Path) -> Path:
        return Path(os.path.abspath(os.fspath(path)))

    @staticmethod
    def _reject_link_like(path: Path) -> None:
        try:
            path_stat = path.lstat()
        except OSError as exc:
            raise ManagedVisualCaptureError("The managed capture root is unavailable.") from exc
        if stat.S_ISLNK(path_stat.st_mode) or int(
            getattr(path_stat, "st_file_attributes", 0)
        ) & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise ManagedVisualCaptureError(
                "The managed capture root and its trusted ancestors cannot be links or reparse points."
            )

    def _assert_trusted_root_chain(self) -> Path:
        root = self._absolute_lexical(self._root)
        anchor = self._absolute_lexical(self._trusted_anchor)
        try:
            relative = root.relative_to(anchor)
        except ValueError as exc:
            raise ManagedVisualCaptureError(
                "The managed capture root is outside its trusted artifact anchor."
            ) from exc
        current = anchor
        self._reject_link_like(current)
        for part in relative.parts:
            current = current / part
            self._reject_link_like(current)
        return root

    def _resolved_root(self) -> Path:
        root = self._assert_trusted_root_chain()
        try:
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise ManagedVisualCaptureError("The managed capture root is unavailable.") from exc
        if not resolved.is_dir():
            raise ManagedVisualCaptureError("The managed capture root is unavailable.")
        return resolved

    def _read_image(self, raw_path: Any, angle: Any) -> tuple[_ManagedImage, bytes]:
        angle_text = str(angle or "").strip().casefold()
        if angle_text not in _ALLOWED_ANGLES:
            raise ManagedVisualCaptureError("Visual capture evidence contains an unsupported angle.")
        candidate = Path(str(raw_path or "").strip())
        if not str(candidate):
            raise ManagedVisualCaptureError("Visual capture evidence contains an empty path.")
        if candidate.is_symlink() or bool(
            getattr(candidate, "is_junction", lambda: False)()
        ):
            raise ManagedVisualCaptureError("Managed capture images cannot be symbolic links.")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ManagedVisualCaptureError("A managed capture image is unavailable.") from exc
        root = self._resolved_root()
        if resolved.parent != root:
            raise ManagedVisualCaptureError("Visual evidence must come from the managed capture directory.")
        if resolved.suffix.casefold() != ".png" or not resolved.is_file():
            raise ManagedVisualCaptureError("Visual evidence must be a managed PNG capture.")
        with resolved.open("rb") as handle:
            descriptor_stat = os.fstat(handle.fileno())
            path_stat = resolved.stat()
            if (
                descriptor_stat.st_dev != path_stat.st_dev
                or descriptor_stat.st_ino != path_stat.st_ino
            ):
                raise ManagedVisualCaptureError("Managed capture identity changed while it was opened.")
            if int(getattr(descriptor_stat, "st_nlink", 1)) != 1:
                raise ManagedVisualCaptureError("Managed capture images cannot be hard links.")
            if int(getattr(descriptor_stat, "st_file_attributes", 0)) & int(
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                raise ManagedVisualCaptureError("Managed capture images cannot be reparse points.")
            image_bytes = handle.read(self._max_image_bytes + 1)
            final_stat = resolved.stat()
            if (
                descriptor_stat.st_dev != final_stat.st_dev
                or descriptor_stat.st_ino != final_stat.st_ino
            ):
                raise ManagedVisualCaptureError("Managed capture identity changed while it was read.")
        size = len(image_bytes)
        if size <= 0 or size > self._max_image_bytes:
            raise ManagedVisualCaptureError("A managed capture image has an invalid size.")
        image = _ManagedImage(
            path=str(resolved),
            angle=angle_text,
            sha256=hashlib.sha256(image_bytes).hexdigest(),
            size=size,
        )
        return image, image_bytes

    def inspect_managed_image(self, path: Any) -> Path:
        """Validate one advisory image without creating completion evidence."""

        image, _image_bytes = self._read_image(path, "front")
        return Path(image.path)

    def read_managed_image(self, path: Any) -> dict[str, Any]:
        """Return immutable bytes read from one verified managed file handle."""

        image, image_bytes = self._read_image(path, "front")
        return {
            "imagePath": image.path,
            "imageBytes": image_bytes,
            "sha256": image.sha256,
            "size": image.size,
        }

    def _prune_locked(self, now: float) -> None:
        expired = [token for token, record in self._records.items() if record.expires_at <= now]
        for token in expired:
            self._records.pop(token, None)
        if len(self._records) < self._max_outstanding:
            return
        oldest = sorted(self._records.items(), key=lambda item: item[1].issued_at)
        for token, _record in oldest[: len(self._records) - self._max_outstanding + 1]:
            self._records.pop(token, None)

    def issue(
        self,
        captures: Sequence[Mapping[str, Any]],
        *,
        project_path: Any = "",
        avatar_path: Any = "",
        binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(captures, Sequence) or isinstance(captures, (str, bytes)):
            raise ManagedVisualCaptureError("Managed visual evidence needs a capture list.")
        if not 1 <= len(captures) <= 4:
            raise ManagedVisualCaptureError("Managed visual evidence needs one to four captures.")
        images = tuple(
            self._read_image(capture.get("imagePath"), capture.get("angle"))[0]
            for capture in captures
            if isinstance(capture, Mapping)
        )
        if len(images) != len(captures):
            raise ManagedVisualCaptureError("Managed visual evidence contains an invalid capture.")
        if len({image.angle for image in images}) != len(images):
            raise ManagedVisualCaptureError("Managed visual evidence angles must be unique.")
        if len({image.path.casefold() for image in images}) != len(images):
            raise ManagedVisualCaptureError("Managed visual evidence images must be distinct.")
        binding_fields = {
            key: str(binding.get(key) or "").strip()
            for key in ("taskId", "sessionId", "approvalId", "requestedActionId")
        }
        if any(not value or len(value) > 180 for value in binding_fields.values()):
            raise ManagedVisualCaptureError("Managed visual evidence is missing its task binding.")

        now = float(self._clock())
        token = secrets.token_urlsafe(32)
        evidence_id = "visual_" + secrets.token_hex(16)
        record = _CaptureRecord(
            evidence_id=evidence_id,
            issued_at=now,
            expires_at=now + self._ttl_seconds,
            project_digest=_digest_text(project_path),
            avatar_digest=_digest_text(avatar_path),
            task_id=binding_fields["taskId"],
            session_id=binding_fields["sessionId"],
            approval_id=binding_fields["approvalId"],
            requested_action_id=binding_fields["requestedActionId"],
            images=images,
        )
        with self._lock:
            self._prune_locked(now)
            self._records[token] = record
        return {
            "captureReceipt": token,
            "captureEvidenceId": evidence_id,
            "angles": [image.angle for image in images],
            "evidence": [{"ref": evidence_id, "kind": "managed_visual_capture"}],
        }

    def consume(self, token: Any, *, binding: Mapping[str, Any]) -> dict[str, Any]:
        token_text = str(token or "").strip()
        if not token_text or len(token_text) > 256:
            raise ManagedVisualCaptureError("The managed capture receipt is invalid.")
        now = float(self._clock())
        expected_task_id = str(binding.get("taskId") or "").strip()
        expected_session_id = str(binding.get("sessionId") or "").strip()
        raw_action_ids = binding.get("captureActionIds")
        expected_action_ids = {
            str(item or "").strip()
            for item in (
                raw_action_ids
                if isinstance(raw_action_ids, Sequence)
                and not isinstance(raw_action_ids, (str, bytes))
                else [binding.get("requestedActionId")]
            )
            if str(item or "").strip()
        }
        expected_approval_id = str(binding.get("approvalId") or "").strip()
        with self._lock:
            self._prune_locked(now)
            record = self._records.get(token_text)
            if record is not None and (
                not expected_task_id
                or not expected_session_id
                or expected_task_id != record.task_id
                or expected_session_id != record.session_id
                or (
                    expected_action_ids
                    and record.requested_action_id not in expected_action_ids
                )
                or (
                    expected_approval_id
                    and expected_approval_id != record.approval_id
                )
            ):
                raise ManagedVisualCaptureError("The managed capture receipt belongs to another task.")
            if record is not None:
                self._records.pop(token_text, None)
        if record is None:
            raise ManagedVisualCaptureError("The managed capture receipt is invalid or already consumed.")
        if record.expires_at <= now:
            raise ManagedVisualCaptureError("The managed capture receipt expired.")

        verified: list[dict[str, Any]] = []
        for image in record.images:
            current, image_bytes = self._read_image(image.path, image.angle)
            if current.size != image.size or current.sha256 != image.sha256:
                raise ManagedVisualCaptureError("Managed capture bytes changed after the receipt was issued.")
            verified.append(
                {
                    "imagePath": current.path,
                    "angle": current.angle,
                    "sha256": current.sha256,
                    "size": current.size,
                    "imageBytes": image_bytes,
                }
            )
        return {
            "captureEvidenceId": record.evidence_id,
            "projectDigest": record.project_digest,
            "avatarDigest": record.avatar_digest,
            "captureActionId": record.requested_action_id,
            "approvalId": record.approval_id,
            "images": verified,
            "angles": [item["angle"] for item in verified],
            "evidence": [{"ref": record.evidence_id, "kind": "managed_visual_capture"}],
        }
