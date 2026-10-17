"""Project-local Core evidence references, retained for an incident's lifetime.

These are ordinary files owned by the Editor's current OS identity. No network
endpoint or new authentication boundary is introduced; the authenticated Core
response supplies the expected digest, and the backend verifies it before use.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

_REFERENCE = re.compile(r"Library/VRCForge/CheckpointEvidence/[0-9a-f]{32}\.json\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _evidence_path(project_root: Path, reference: str) -> Path:
    if not _REFERENCE.fullmatch(reference):
        raise ValueError("Invalid checkpoint evidence reference.")
    root = project_root.resolve(strict=True)
    current = root
    for part in PurePosixPath(reference).parts:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError("Checkpoint evidence must not traverse links or reparse points.")
    if not current.is_file() or root not in current.resolve(strict=True).parents:
        raise ValueError("Checkpoint evidence is outside the selected project.")
    return current


def validate_evidence_reference(project_root: Path, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Checkpoint evidence must be a reference object.")
    reference, digest = value.get('reference'), value.get('sha256')
    byte_count, object_count = value.get('byteCount'), value.get('objectCount')
    if (not isinstance(reference, str) or not isinstance(digest, str)
            or not _DIGEST.fullmatch(digest)
            or type(byte_count) is not int or byte_count < 0
            or type(object_count) is not int or object_count < 0):
        raise ValueError("Checkpoint evidence metadata is invalid.")
    path = _evidence_path(project_root, reference)
    raw = path.read_bytes()
    if len(raw) != byte_count or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Checkpoint evidence bytes do not match the Core receipt.")
    content = json.loads(raw.decode('utf-8'))
    if not isinstance(content, list) or len(content) != object_count:
        raise ValueError("Checkpoint evidence object count is invalid.")
    summary = value.get('summary', {})
    if len(json.dumps(summary, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > 4096:
        raise ValueError("Checkpoint evidence summary exceeds its bound.")
    return {'reference': reference, 'sha256': digest, 'byteCount': byte_count,
            'objectCount': object_count, 'summary': summary}


def collect_evidence_references(project_root: Path, result: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    references, warnings = [], []
    transport = result.get('result')
    envelope = transport.get('payload') if isinstance(transport, dict) else None
    structured = envelope.get('structuredContent') if isinstance(envelope, dict) else None
    data = structured.get('data') if isinstance(structured, dict) else None
    source = {**(data if isinstance(data, dict) else {}), **result}
    for field in ('dirtyAssetEvidenceBefore', 'dirtyAssetEvidenceAfter'):
        value = source.get(field)
        if not isinstance(value, dict) or 'reference' not in value:
            continue
        try:
            references.append(validate_evidence_reference(project_root, value))
        except (OSError, ValueError, UnicodeError) as exc:
            warnings.append(f'{field}: {exc}')
    return references, warnings


def cleanup_recovery_evidence(recovery: dict[str, Any]) -> dict[str, Any]:
    """Delete only registered exact-name, exact-digest files after incident close."""
    result: dict[str, Any] = {'deleted': [], 'preserved': [], 'errors': []}
    if recovery.get('status') in {'applying', 'needs_recovery', 'restore_failed'}:
        return result
    root = Path(str(recovery.get('projectRoot') or ''))
    for ref in recovery.get('unityEvidenceReferences', []):
        name = str(ref.get('reference') or '') if isinstance(ref, dict) else ''
        try:
            verified = validate_evidence_reference(root, ref)
            _evidence_path(root, verified['reference']).unlink()
            result['deleted'].append(name)
        except FileNotFoundError:
            # A preceding successful close may already have removed this file.
            continue
        except (OSError, ValueError, UnicodeError) as exc:
            result['preserved'].append(name)
            result['errors'].append(f'{name}: {exc}')
    return result


def checkpoint_core_error(exc: Exception) -> dict[str, Any]:
    """Keep bounded transport diagnostics, including the original size receipt."""
    def bounded(value: Any, depth: int = 0) -> Any:
        if depth > 4:
            return '[truncated]'
        if isinstance(value, dict):
            return {str(k)[:100]: bounded(v, depth + 1) for k, v in list(value.items())[:16]}
        if isinstance(value, list):
            return [bounded(item, depth + 1) for item in value[:8]]
        if isinstance(value, str):
            return value[:256]
        return value if value is None or isinstance(value, (bool, int, float)) else str(value)[:256]
    result: dict[str, Any] = {'ok': False, 'error': str(exc)[:2048]}
    details = getattr(exc, 'details', None)
    if isinstance(details, dict):
        result['details'] = bounded(details)
        if len(json.dumps(result['details'], ensure_ascii=True)) > 8192:
            data = details.get('data') if isinstance(details.get('data'), dict) else {}
            result['details'] = {'kind': str(details.get('kind', ''))[:100],
                                 'truncated': True, 'data': {
                key: data[key] for key in ('actualResponseBytes', 'maxResponseBytes')
                if type(data.get(key)) is int}}
    return result


def record_unity_recovery_evidence(service: Any, checkpoint: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
    """Persist immediately, before a safety backup or subsequent restore can fail."""
    recovery = service._select_apply_recovery({'checkpointId': checkpoint.get('id')}, include_resolved=False)
    references, warnings = collect_evidence_references(Path(str(checkpoint.get('projectRoot') or '')), result)
    if not references and not warnings:
        return recovery
    owner = recovery or service._load_checkpoint(str(checkpoint.get('id') or '')) or checkpoint
    combined = list(owner.get('unityEvidenceReferences') or [])
    known = {(item.get('reference'), item.get('sha256')) for item in combined if isinstance(item, dict)}
    for ref in references:
        identity = (ref['reference'], ref['sha256'])
        if identity not in known:
            combined.append(ref)
            known.add(identity)
    updated = {**owner, 'unityEvidenceReferences': combined}
    if warnings:
        updated['unityEvidenceWarnings'] = [*(owner.get('unityEvidenceWarnings') or []), *warnings]
        result.update(ok=False, errorCode='checkpoint_evidence_invalid',
                      error='Core checkpoint evidence could not be verified.', evidenceWarnings=warnings)
    if recovery:
        return service._append_apply_recovery_entry(updated)
    service._append_checkpoint(updated)
    return None


def cleanup_checkpoint_evidence(service: Any, checkpoint_id: str) -> None:
    checkpoint = service._load_checkpoint(checkpoint_id)
    if not checkpoint or not checkpoint.get('unityEvidenceReferences'):
        return
    cleanup = cleanup_recovery_evidence({**checkpoint, 'status': 'closed'})
    service._append_checkpoint({**checkpoint, 'unityEvidenceCleanup': cleanup})
    service._ports.append_audit({'event': 'checkpoint_unity_evidence_cleaned',
                                 'checkpointId': checkpoint_id, **cleanup})
