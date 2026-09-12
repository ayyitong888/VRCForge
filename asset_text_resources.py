"""Bounded, identity-bound readback for Unity project text assets."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from prepared_file_imports import _stat_identity, capture_directory, verify_directory

RESOURCE_TYPE = "unity_asset_text"
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024
RESOURCE_CHUNK_BYTES = 8192
_GUID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,100}$", re.IGNORECASE)
_SHADER_INCLUDE_SUFFIXES = frozenset({".cginc", ".hlsl", ".shader"})
_UPM_MONOSCRIPT_SUFFIXES = frozenset({".cs"})


def _literal_text_search(text: str, full_encoded: bytes, params: dict[str, Any], source_hash: str) -> dict[str, Any]:
    spec = params.get("textSearch")
    if not isinstance(spec, dict):
        raise ValueError("textSearch must be an object.")
    literal = spec.get("literal")
    if not isinstance(literal, str) or not literal:
        raise ValueError("textSearch literal must be a non-empty string.")
    literal_bytes = literal.encode("utf-8")
    if len(literal) > 512:
        raise ValueError("textSearch literal must be at most 512 characters.")
    before = spec.get("contextBeforeBytes", 96)
    after = spec.get("contextAfterBytes", 160)
    maximum = spec.get("maxMatches", 32)
    if type(before) is not int or not 0 <= before <= 4096:
        raise ValueError("textSearch contextBeforeBytes must be an integer from 0 to 4096.")
    if type(after) is not int or not 0 <= after <= 4096:
        raise ValueError("textSearch contextAfterBytes must be an integer from 0 to 4096.")
    if type(maximum) is not int or not 1 <= maximum <= 128:
        raise ValueError("textSearch maxMatches must be an integer from 1 to 128.")

    resume = spec.get("matchStartByte", 0)
    if type(resume) is not int or resume < 0 or resume > len(full_encoded):
        raise ValueError("textSearch matchStartByte must be a valid UTF-8 byte offset.")
    starts = []
    match_count = 0
    remaining_count = 0
    cursor = 0
    while True:
        found = full_encoded.find(literal_bytes, cursor)
        if found < 0:
            break
        match_count += 1
        if found >= resume:
            remaining_count += 1
            if len(starts) <= maximum:
                starts.append(found)
        cursor = found + len(literal_bytes)
    matches = []
    result_truncated = False
    for match_start in starts[:maximum]:
        match_end = match_start + len(literal_bytes)
        context_start = max(0, match_start - before)
        context_end = min(len(full_encoded), match_end + after)
        while context_start < match_start and context_start < len(full_encoded) and (full_encoded[context_start] & 0xC0) == 0x80:
            context_start += 1
        while context_end < len(full_encoded) and context_end > match_end and (full_encoded[context_end] & 0xC0) == 0x80:
            context_end -= 1
        context_text = full_encoded[context_start:context_end].decode("utf-8")
        matches.append({
            "lineStart": full_encoded.count(b"\n", 0, match_start) + 1,
            "lineEnd": full_encoded.count(b"\n", 0, match_end - 1) + 1,
            "matchStartByte": match_start,
            "matchEndByte": match_end,
            "matchText": literal,
            "contextStartByte": context_start,
            "contextEndByte": context_end,
            "contextText": context_text,
        })
    truncated = remaining_count > len(matches) or result_truncated
    result = {
        "schema": "vrcforge.unity_asset_text_search.v1",
        "literal": literal,
        "sourceContentHash": source_hash,
        "totalBytes": len(full_encoded),
        "matchCount": match_count,
        "remainingMatchCount": remaining_count,
        "returnedMatchCount": len(matches),
        "matchTruncated": truncated,
        "resultTruncated": result_truncated,
        "resultBytes": 0,
        "searchComplete": not truncated,
        "matches": matches,
    }
    while True:
        result.pop("nextMatchStartByte", None)
        result.pop("continuation", None)
        result["returnedMatchCount"] = len(matches)
        result["matchTruncated"] = remaining_count > len(matches) or result_truncated
        result["searchComplete"] = not result["matchTruncated"]
        result["resultTruncated"] = result_truncated
        result["matches"] = matches
        if result["matchTruncated"]:
            next_index = len(matches) if result_truncated else maximum
            result["nextMatchStartByte"] = starts[next_index]
            result["continuation"] = {
                "method": "get_asset_info",
                "textSearch": {**spec, "matchStartByte": starts[next_index]},
                "note": "Repeat this textSearch for the same asset; require the same sourceContentHash before combining pages.",
            }
        result["resultBytes"] = 0
        while True:
            actual_bytes = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            if result["resultBytes"] == actual_bytes:
                break
            result["resultBytes"] = actual_bytes
        if actual_bytes <= MAX_TEXT_BYTES or not matches:
            break
        matches.pop()
        result_truncated = True
    return result


def _confined_asset(root: Path, asset_path: str, asset_info: dict[str, Any] | None = None) -> tuple[Path, list[dict[str, Any]]]:
    normalized = str(asset_path or "").replace("\\", "/")
    parts = normalized.split("/")
    is_assets = normalized.startswith("Assets/")
    is_embedded_package = normalized.startswith("Packages/")
    if ((not is_assets and not is_embedded_package) or len(parts) < 2
            or any(part in {"", ".", ".."} for part in parts)
            or any(char in normalized for char in ("\\", ":", "\x00"))):
        raise ValueError("Text read requires an exact project-relative Assets path.")
    if is_embedded_package:
        asset_type = asset_info.get("assetType") if isinstance(asset_info, dict) else None
        if asset_type not in {"UnityEditor.ShaderInclude", "UnityEditor.MonoScript"}:
            raise ValueError("UPM text read requires Core assetType UnityEditor.ShaderInclude or UnityEditor.MonoScript.")
        is_mono_script = asset_type == "UnityEditor.MonoScript"
        if len(parts) < 3 or not _PACKAGE_ID_RE.fullmatch(parts[1]):
            raise ValueError("UPM text read requires an exact embedded package id.")
        package_root = root / "Packages" / parts[1]
        package_manifest = package_root / "package.json"
        packages_root = root / "Packages"
        if not packages_root.is_dir() or packages_root.is_symlink():
            raise ValueError("UPM Packages ancestor may not be a symlink or reparse point.")
        if package_root.is_symlink() or package_manifest.is_symlink():
            raise ValueError("UPM package path may not be a symlink or reparse point.")
        if not package_root.is_dir() or not package_manifest.is_file():
            raise ValueError("UPM text read requires a real embedded package with package.json.")
        package_ancestors = [
            capture_directory(root, label="Text asset ancestor"),
            capture_directory(packages_root, label="UPM Packages ancestor"),
            capture_directory(package_root, label="UPM package ancestor"),
        ]
        try:
            manifest_identity = _stat_identity(package_manifest, kind="UPM package manifest file")
            _, manifest_text = _read_source(package_manifest)
            if _stat_identity(package_manifest, kind="UPM package manifest file") != manifest_identity:
                raise ValueError("UPM package manifest changed while it was read.")
        except (OSError, UnicodeError) as exc:
            raise ValueError("UPM embedded package manifest is unreadable.") from exc
        try:
            manifest = json.loads(manifest_text)
        except json.JSONDecodeError as exc:
            raise ValueError("UPM embedded package manifest is unreadable.") from exc
        for identity in package_ancestors:
            verify_directory(identity, label="UPM package ancestor")
        if not isinstance(manifest, dict) or str(manifest.get("name") or "").casefold() != parts[1].casefold():
            raise ValueError("UPM embedded package manifest does not match the requested package id.")
        suffix = Path(parts[-1]).suffix.casefold()
        allowed_suffixes = _UPM_MONOSCRIPT_SUFFIXES if is_mono_script else _SHADER_INCLUDE_SUFFIXES
        if suffix not in allowed_suffixes:
            raise ValueError("UPM MonoScript reads require a .cs file." if is_mono_script else "UPM text read requires a shader include file.")
        allowed_root = package_root
    else:
        allowed_root = root / "Assets"
    target = root.joinpath(*parts)
    try:
        target.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError("Text read escaped the allowed Unity project root.") from exc
    if not target.is_file():
        raise ValueError("Text asset source is unavailable or is not a regular file.")
    ancestors = []
    seen_ancestor_paths: set[str] = set()
    if is_embedded_package:
        for identity in package_ancestors:
            ancestors.append(identity)
            seen_ancestor_paths.add(str(identity["path"]))
    for parent in target.parents:
        if parent != root and not parent.is_relative_to(allowed_root):
            continue
        identity = capture_directory(parent, label="Text asset ancestor")
        if str(identity["path"]) not in seen_ancestor_paths:
            ancestors.append(identity)
            seen_ancestor_paths.add(str(identity["path"]))
    return target, ancestors


def _read_source(path: Path) -> tuple[bytes, str]:
    identity = _stat_identity(path, kind="Text asset file")
    size = identity["size"]
    if size > MAX_SOURCE_BYTES:
        raise ValueError("Text asset exceeds its 4 MiB source limit.")
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        opened_identity = {
            "path": os.path.abspath(path), "device": int(before.st_dev),
            "inode": int(before.st_ino), "attributes": int(getattr(before, "st_file_attributes", 0) or 0),
            "size": int(before.st_size), "mtimeNs": int(before.st_mtime_ns),
        }
        if opened_identity != identity:
            raise ValueError("Text asset changed before its read handle was opened.")
        raw = handle.read(MAX_SOURCE_BYTES + 1)
        after = os.fstat(handle.fileno())
    if (len(raw) != size or before.st_ino != after.st_ino or before.st_size != after.st_size
            or path.stat().st_mtime_ns != before.st_mtime_ns or _stat_identity(path, kind="Text asset file") != identity):
        raise ValueError("Text asset changed while it was read.")
    if b"\x00" in raw:
        raise ValueError("Binary asset content is not supported by the text reader.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Text asset is not valid UTF-8.") from exc
    return raw, text


def _publish_meta_text(registry, root: Path, target: Path, asset_path: str, guid: str,
                       asset_info: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    if not asset_path.startswith("Assets/"):
        raise ValueError("Metadata read requires an exact project-relative Assets path.")
    meta_path, meta_ancestors = _confined_asset(root, asset_path + ".meta")
    for identity in meta_ancestors:
        verify_directory(identity, label="Text asset metadata ancestor")
    raw, text = _read_source(meta_path)
    for identity in meta_ancestors:
        verify_directory(identity, label="Text asset metadata ancestor")
    matches = re.findall(r"(?m)^guid:[ \t]*([0-9a-f]{32})[ \t]*\r?$", text, re.IGNORECASE)
    if len(matches) != 1 or matches[0].lower() != guid:
        raise ValueError("Unity asset meta GUID does not match the Core asset GUID.")
    raw_digest = hashlib.sha256(raw).hexdigest()
    text_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    encoded = text.encode("utf-8")
    max_bytes = params.get("textMaxBytes", MAX_TEXT_BYTES)
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_TEXT_BYTES:
        raise ValueError("textMaxBytes must be an integer from 1 to 65536.")
    preview_bytes = encoded[:max_bytes]
    preview = preview_bytes.decode("utf-8", errors="ignore")
    returned_bytes = len(preview.encode("utf-8"))
    reconstruction = {
        "bomPresent": raw.startswith(b"\xef\xbb\xbf"),
        "bomBytes": 3 if raw.startswith(b"\xef\xbb\xbf") else 0,
        "rawReconstruction": "Prepend UTF-8 BOM iff bomPresent to concatenated chunk text encoded as UTF-8; preserve line endings.",
        "sourceAssetSha256": asset_info.get("sourceAssetSha256"),
        "sourceTextSha256": asset_info.get("sourceTextSha256"),
        "dependencyHash": asset_info.get("dependencyHash"),
    }
    chunks = []
    offset = 0
    while offset < len(encoded):
        chunk_text = encoded[offset:offset + RESOURCE_CHUNK_BYTES].decode("utf-8", errors="ignore")
        end_byte = offset + len(chunk_text.encode("utf-8"))
        chunks.append({"startByte": offset, "endByte": end_byte, "text": chunk_text})
        offset = end_byte
    stable = hashlib.sha256((str(root) + "\0meta\0" + guid).encode()).hexdigest()
    record = registry.publish(
        base_uri=f"vrcforge://unity-asset-meta/{stable}",
        name=f"Unity asset metadata: {asset_path}.meta", resource_type="unity_asset_meta",
        data={"schema": "vrcforge.unity_asset_meta.v1", "assetPath": asset_path,
              "metaPath": asset_path + ".meta", "assetGuid": guid,
              "rawSha256": raw_digest, "textSha256": text_digest, "rawBytes": len(raw),
              "textBytes": len(encoded), "encoding": "utf-8", "chunks": chunks,
              "chunkMaxBytes": RESOURCE_CHUNK_BYTES, **reconstruction},
        identity={"projectRoot": str(root), "assetPath": asset_path, "assetGuid": guid,
                  "metaSha256": raw_digest, "sourceAssetSha256": asset_info.get("sourceAssetSha256"),
                  "dependencyHash": asset_info.get("dependencyHash")}, source_mode="captured_unity_asset_meta",
        refresh_rule="Immutable metadata capture; call get_asset_info includeMetaText for new evidence.",
        description="Persistent Unity asset .meta source; not imported runtime evidence.",
    )
    payload = {"metaPath": asset_path + ".meta", "assetGuid": guid,
            "rawSha256": raw_digest, "textSha256": text_digest,
            "rawBytes": len(raw), "textBytes": len(encoded), "encoding": "utf-8",
            **reconstruction,
            "reconstructedRawSha256": hashlib.sha256((b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"") + text.encode("utf-8")).hexdigest(),
            "truncated": returned_bytes < len(encoded), "returnedBytes": returned_bytes,
            "complete": returned_bytes == len(encoded), "resourceSourceComplete": True,
            "text": preview, "resourceUri": record["uri"], "revision": record["revision"],
            "contentHash": record["contentHash"]}
    if payload["truncated"]:
        index = next(i for i, chunk in enumerate(chunks) if chunk["endByte"] > returned_bytes)
        payload["continuation"] = {
            "method": "resources/read",
            "params": {"uri": record["uri"], "_meta": {"io.vrcforge/resourceSelection": {
                "pointer": "/data/chunks", "offset": index, "limit": 1,
            }}},
            "resumeTextByteOffset": returned_bytes,
        }
    return payload


def publish_asset_text(registry, params: dict[str, Any], asset_info: dict[str, Any]) -> dict[str, Any]:
    project = params.get("projectPath")
    if not isinstance(project, str) or not project or not Path(project).is_absolute():
        raise ValueError("Text read requires an explicit absolute projectPath.")
    root = Path(os.path.abspath(project))
    asset_path = str(asset_info.get("assetPath") or "").replace("\\", "/")
    guid = str(asset_info.get("guid") or "").lower()
    if not _GUID_RE.fullmatch(guid):
        raise ValueError("Text read requires a Core asset GUID.")
    requested_path = str(params.get("assetPath") or "").replace("\\", "/")
    if requested_path and requested_path != asset_path:
        raise ValueError("Core assetPath differs from requested asset.")
    requested_guid = str(params.get("guid") or "").lower()
    if requested_guid and requested_guid != guid:
        raise ValueError("Core asset GUID differs from requested asset.")
    if params.get("includeMetaText") is True and not asset_path.startswith("Assets/"):
        raise ValueError("Metadata read requires an exact project-relative Assets path.")
    target, ancestors = _confined_asset(root, asset_path, asset_info)
    raw, text = _read_source(target)
    for identity in ancestors:
        verify_directory(identity, label="Text asset ancestor")
    digest = hashlib.sha256(raw).hexdigest()
    max_bytes = params.get("textMaxBytes", MAX_TEXT_BYTES)
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_TEXT_BYTES:
        raise ValueError("textMaxBytes must be an integer from 1 to 65536.")
    start = params.get("textStartLine", 1)
    end = params.get("textEndLine")
    if type(start) is not int or start < 1 or (end is not None and (type(end) is not int or end < start)):
        raise ValueError("Text line range must be one-based and ordered.")
    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    selected = lines[start - 1:end] if end is not None else lines[start - 1:]
    selected_text = "".join(selected)
    encoded = selected_text.encode("utf-8")
    truncated = len(encoded) > max_bytes
    preview = encoded[:max_bytes].decode("utf-8", errors="ignore")
    if "textSearch" in params and params.get("includeText") is not True:
        preview = ""
        truncated = bool(encoded)
    range_complete = not truncated
    source_complete = range_complete and start == 1 and (end is None or end >= total_lines)
    full_encoded = text.encode("utf-8")
    source_text_sha = hashlib.sha256(full_encoded).hexdigest()
    selected_line_end = min(total_lines, end if end is not None else total_lines)
    prefix_bytes = len("".join(lines[: start - 1]).encode("utf-8"))
    returned_bytes = len(preview.encode("utf-8"))
    whole_lines = whole_line_bytes = 0
    for line in selected:
        size = len(line.encode("utf-8"))
        if whole_line_bytes + size > returned_bytes:
            break
        whole_lines += 1
        whole_line_bytes += size
    line_interrupted = truncated and (returned_bytes == 0 or whole_line_bytes != returned_bytes)
    next_byte_offset = prefix_bytes + returned_bytes
    if next_byte_offset >= len(full_encoded):
        next_byte_offset = None
    next_line = start + whole_lines if next_byte_offset is not None and not line_interrupted else None
    identity = {"projectRoot": str(root), "assetPath": asset_path, "assetGuid": guid,
                "dependencyHash": asset_info.get("dependencyHash")}
    payload = {
        "schema": "vrcforge.unity_asset_text.v1", "assetPath": asset_path, "assetGuid": guid,
        "assetType": asset_info.get("assetType"), "sha256": digest, "encoding": "utf-8",
        "rawBytes": len(raw), "sourceSha256": digest, "textSha256": source_text_sha,
        "totalBytes": len(raw), "totalLines": total_lines, "startLine": start,
        "selectedEndLine": selected_line_end,
        "endLine": start + len(preview.splitlines()) - 1 if preview else None,
        "returnedBytes": returned_bytes,
        "truncated": truncated, "rangeComplete": range_complete, "sourceComplete": source_complete,
        "complete": source_complete, "nextLine": next_line, "nextByteOffset": next_byte_offset,
        "byteOffsetEncoding": "UTF-8 decoded text, excluding source BOM",
        "bomPresent": raw.startswith(b"\xef\xbb\xbf"), "bomBytes": 3 if raw.startswith(b"\xef\xbb\xbf") else 0,
        "lineInterrupted": line_interrupted, "text": preview,
        "interpretation": "Persistent Unity project text source; not compiled shader or GPU/runtime evidence.",
    }
    if "textSearch" in params:
        payload["textSearch"] = _literal_text_search(text, full_encoded, params, source_text_sha)
    # The full source is bounded by _read_source, so the Resource always keeps
    # the complete immutable text even when the Tool response is a short page.
    if len(raw) <= MAX_SOURCE_BYTES:
        chunks = []
        offset = 0
        while offset < len(full_encoded):
            chunk_text = full_encoded[offset:offset + RESOURCE_CHUNK_BYTES].decode("utf-8", errors="ignore")
            end_byte = offset + len(chunk_text.encode("utf-8"))
            chunks.append({"startByte": offset, "endByte": end_byte, "text": chunk_text})
            offset = end_byte
        stable = hashlib.sha256((str(root) + "\0" + guid).encode()).hexdigest()
        record = registry.publish(
            base_uri=f"vrcforge://unity-asset-text/{stable}",
            name=f"Unity asset text: {asset_path}", resource_type=RESOURCE_TYPE,
            data={
                "schema": "vrcforge.unity_asset_text.v1", "assetPath": asset_path, "assetGuid": guid,
                "assetType": asset_info.get("assetType"), "sha256": digest, "sourceSha256": digest,
                "textSha256": source_text_sha, "encoding": "utf-8", "rawBytes": len(raw),
                "totalBytes": len(raw), "totalLines": total_lines, "chunks": chunks,
                "textBytes": len(full_encoded), "chunkMaxBytes": RESOURCE_CHUNK_BYTES,
                "byteOffsetEncoding": payload["byteOffsetEncoding"],
                "interpretation": payload["interpretation"], "sourceComplete": True,
            }, identity=identity,
            source_mode="captured_unity_asset_text",
            refresh_rule="Immutable text capture; call get_asset_info includeText for new evidence.",
            description=payload["interpretation"],
        )
        payload.update({"resourceUri": record["uri"], "revision": record["revision"], "contentHash": record["contentHash"]})
        payload["resourceSourceComplete"] = True
        if next_byte_offset is not None:
            index = next(i for i, chunk in enumerate(chunks) if chunk["endByte"] > next_byte_offset)
            payload["continuation"] = {
                "method": "resources/read",
                "params": {"uri": record["uri"], "_meta": {"io.vrcforge/resourceSelection": {
                    "pointer": "/data/chunks", "offset": index, "limit": 1,
                }}},
                "resumeTextByteOffset": next_byte_offset,
                "note": "Chunks may overlap the preview; start at resumeTextByteOffset and page by chunk index.",
            }
    if params.get("includeMetaText") is True:
        payload["meta"] = _publish_meta_text(registry, root, target, asset_path, guid,
            {**asset_info, "sourceAssetSha256": digest, "sourceTextSha256": source_text_sha}, params)
        payload["metaSha256"] = payload["meta"]["rawSha256"]
        payload["meta"]["sourceAssetSha256"] = digest
        payload["meta"]["sourceTextSha256"] = source_text_sha
    return payload


def publish_asset_meta(registry, params: dict[str, Any], asset_info: dict[str, Any]) -> dict[str, Any]:
    """Read only the exact sidecar; meta-only calls must not scan the main asset."""
    project = params.get("projectPath")
    if not isinstance(project, str) or not project or not Path(project).is_absolute():
        raise ValueError("Metadata read requires an explicit absolute projectPath.")
    root = Path(os.path.abspath(project))
    asset_path = str(asset_info.get("assetPath") or "").replace("\\", "/")
    guid = str(asset_info.get("guid") or "").lower()
    if not _GUID_RE.fullmatch(guid):
        raise ValueError("Metadata read requires a Core asset GUID.")
    requested_path = str(params.get("assetPath") or "").replace("\\", "/")
    if requested_path and requested_path != asset_path:
        raise ValueError("Core assetPath differs from requested asset.")
    requested_guid = str(params.get("guid") or "").lower()
    if requested_guid and requested_guid != guid:
        raise ValueError("Core asset GUID differs from requested asset.")
    if not asset_path.startswith("Assets/"):
        raise ValueError("Metadata read requires an exact project-relative Assets path.")
    target, ancestors = _confined_asset(root, asset_path)
    for identity in ancestors:
        verify_directory(identity, label="Text asset ancestor")
    meta = _publish_meta_text(registry, root, target, asset_path, guid, asset_info, params)
    return {"schema": "vrcforge.unity_asset_meta_read.v1", "assetPath": asset_path,
            "assetGuid": guid, "dependencyHash": asset_info.get("dependencyHash"),
            "meta": meta, "metaSha256": meta["rawSha256"],
            "interpretation": "Persistent Unity asset metadata source; main asset content was not read."}
