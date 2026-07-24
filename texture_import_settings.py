from __future__ import annotations

import hashlib
import os
import re
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any


RESULT_SCHEMA = "vrcforge.texture_import_settings.v1"
APPROVAL_PREVIEW_SCHEMA = "vrcforge.texture_import_settings_approval.v1"
SETTINGS_DIGEST_SCHEMA = "vrcforge.texture_import_settings_digest.v1"
TOOL_NAME = "vrc_set_texture_import_settings"

REQUEST_ARGUMENT_KEYS = (
    "textureAssetPath",
    "platform",
    "maxTextureSize",
    "format",
    "compression",
    "crunch",
    "quality",
)

_PRECONDITION_KEYS = (
    "expectedProjectPath",
    "expectedTextureAssetPath",
    "expectedTextureAssetGuid",
    "expectedSourceFileDigest",
    "expectedSourceFileIdentityDigest",
    "expectedMetaFileDigest",
    "expectedMetaFileIdentityDigest",
    "expectedImporterType",
    "expectedImporterSettingsDigest",
    "expectedTargetSettingsDigest",
)

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GUID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_IMPORTER_TYPES = {
    "Default",
    "NormalMap",
    "GUI",
    "Sprite",
    "Cursor",
    "Cookie",
    "Lightmap",
    "SingleChannel",
}
_PLATFORM_NAMES = {
    "default": "DefaultTexturePlatform",
    "standalone": "Standalone",
    "android": "Android",
    "ios": "iPhone",
}
_MAX_TEXTURE_SIZES = {32, 64, 128, 256, 512, 1024, 2048, 4096, 8192}
_FORMATS_BY_PLATFORM = {
    "default": {"automatic", "rgb24", "rgba32"},
    "standalone": {
        "automatic",
        "rgb24",
        "rgba32",
        "dxt1",
        "dxt5",
        "dxt1_crunched",
        "dxt5_crunched",
        "bc7",
    },
    "android": {
        "automatic",
        "rgb24",
        "rgba32",
        "etc_rgb4",
        "etc2_rgb4",
        "etc2_rgba8",
        "etc_rgb4_crunched",
        "etc2_rgba8_crunched",
        "astc_4x4",
        "astc_6x6",
        "astc_8x8",
    },
    "ios": {
        "automatic",
        "rgb24",
        "rgba32",
        "pvrtc_rgb4",
        "pvrtc_rgba4",
        "astc_4x4",
        "astc_6x6",
        "astc_8x8",
    },
}
_CRUNCHED_FORMATS = {
    "dxt1_crunched",
    "dxt5_crunched",
    "etc_rgb4_crunched",
    "etc2_rgba8_crunched",
}
_CRUNCH_FORMATS_BY_PLATFORM = {
    "default": {"automatic"},
    "standalone": {"automatic", "dxt1_crunched", "dxt5_crunched"},
    "android": {"automatic", "etc_rgb4_crunched", "etc2_rgba8_crunched"},
    "ios": set(),
}
_UNCOMPRESSED_FORMATS = {"rgb24", "rgba32"}
_COMPRESSIONS = {"uncompressed", "normal", "high", "low"}


class TextureImportSettingsError(ValueError):
    pass


def build_wrapper_arguments(params: dict[str, Any]) -> dict[str, Any]:
    wrapper = deepcopy(params or {})
    nested = wrapper.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper.get("params")
    if not isinstance(nested, dict):
        nested = {key: wrapper[key] for key in REQUEST_ARGUMENT_KEYS if key in wrapper}
    for key in REQUEST_ARGUMENT_KEYS:
        wrapper.pop(key, None)
    wrapper.pop("params", None)
    wrapper.pop("tool_name", None)
    wrapper["toolName"] = TOOL_NAME
    wrapper["arguments"] = deepcopy(nested)
    return wrapper


def build_preview_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    request = arguments if isinstance(arguments, dict) else {}
    preview_arguments = {
        key: deepcopy(request[key])
        for key in REQUEST_ARGUMENT_KEYS
        if key in request
    }
    preview_arguments["preview"] = True
    preview_arguments["saveAndReimport"] = False
    return preview_arguments


def normalize_requested_settings(arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise TextureImportSettingsError("Texture import settings are required.")
    platform = _canonical_choice(arguments.get("platform"), label="platform", choices=set(_PLATFORM_NAMES))
    max_texture_size = _strict_int(arguments.get("maxTextureSize"), label="maxTextureSize")
    if max_texture_size not in _MAX_TEXTURE_SIZES:
        raise TextureImportSettingsError("maxTextureSize is not supported.")
    texture_format = _canonical_choice(
        arguments.get("format"),
        label="format",
        choices=_FORMATS_BY_PLATFORM[platform],
    )
    compression = _canonical_choice(
        arguments.get("compression"),
        label="compression",
        choices=_COMPRESSIONS,
    )
    crunch = _strict_bool(arguments.get("crunch"), label="crunch")
    quality = _bounded_int(arguments.get("quality"), label="quality", minimum=0, maximum=100)

    if crunch and texture_format not in _CRUNCH_FORMATS_BY_PLATFORM[platform]:
        raise TextureImportSettingsError("Crunch is incompatible with the requested platform or format.")
    if not crunch and texture_format in _CRUNCHED_FORMATS:
        raise TextureImportSettingsError("A crunched texture format requires crunch=true.")
    if compression == "uncompressed" and (crunch or texture_format not in _UNCOMPRESSED_FORMATS | {"automatic"}):
        raise TextureImportSettingsError("Uncompressed mode is incompatible with the requested format.")
    if texture_format in _UNCOMPRESSED_FORMATS and compression != "uncompressed":
        raise TextureImportSettingsError("An uncompressed format requires compression=uncompressed.")
    if texture_format not in _UNCOMPRESSED_FORMATS | {"automatic"} and compression == "uncompressed":
        raise TextureImportSettingsError("A compressed format cannot use compression=uncompressed.")

    return {
        "platform": platform,
        "maxTextureSize": max_texture_size,
        "format": texture_format,
        "compression": compression,
        "crunch": crunch,
        "quality": quality,
    }


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(wrapper_arguments, dict):
        raise TextureImportSettingsError("Texture import wrapper arguments are required.")
    if wrapper_arguments.get("toolName", TOOL_NAME) != TOOL_NAME:
        raise TextureImportSettingsError("Texture import tool name is invalid.")
    nested = wrapper_arguments.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper_arguments.get("params")
    if not isinstance(nested, dict):
        raise TextureImportSettingsError("Texture import arguments are required.")

    project_path = _canonical_project_path(wrapper_arguments.get("projectPath"))
    requested_path = _safe_asset_path(nested.get("textureAssetPath"), label="textureAssetPath")
    requested_settings = normalize_requested_settings(nested)

    result = _require_dict(payload, "preview result")
    if result.get("schema") != RESULT_SCHEMA:
        raise TextureImportSettingsError("Texture import preview schema is invalid.")
    for key, expected in (("ok", True), ("preview", True), ("verified", True)):
        if result.get(key) is not expected:
            raise TextureImportSettingsError(f"Texture import preview {key} is invalid.")
    for key in ("changed", "saved", "reimported", "importerDirtyBefore", "importerDirtyAfter"):
        if _strict_bool(result.get(key), label=key):
            raise TextureImportSettingsError(f"Texture import preview reported {key}.")

    actual_project_path = _canonical_project_path(result.get("projectPath"))
    if os.path.normcase(actual_project_path) != os.path.normcase(project_path):
        raise TextureImportSettingsError("Texture import preview changed the selected project.")
    texture_path = _safe_asset_path(result.get("textureAssetPath"), label="textureAssetPath")
    if texture_path != requested_path:
        raise TextureImportSettingsError("Texture import preview changed the requested texture asset.")
    texture_guid = _lower_hex(result.get("textureAssetGuid"), label="textureAssetGuid", pattern=_GUID_PATTERN)
    source_digest = _lower_hex(
        result.get("sourceFileDigestBefore"),
        label="sourceFileDigestBefore",
        pattern=_DIGEST_PATTERN,
    )
    source_digest_after = _lower_hex(
        result.get("sourceFileDigestAfter"),
        label="sourceFileDigestAfter",
        pattern=_DIGEST_PATTERN,
    )
    source_identity_digest = _lower_hex(
        result.get("sourceFileIdentityDigest"),
        label="sourceFileIdentityDigest",
        pattern=_DIGEST_PATTERN,
    )
    if _strict_int(result.get("sourceFileLinkCount"), label="sourceFileLinkCount") != 1:
        raise TextureImportSettingsError("Texture source must have exactly one filesystem link.")
    meta_digest = _lower_hex(
        result.get("metaFileDigestBefore"),
        label="metaFileDigestBefore",
        pattern=_DIGEST_PATTERN,
    )
    meta_digest_after = _lower_hex(
        result.get("metaFileDigestAfter"),
        label="metaFileDigestAfter",
        pattern=_DIGEST_PATTERN,
    )
    meta_identity_digest = _lower_hex(
        result.get("metaFileIdentityDigest"),
        label="metaFileIdentityDigest",
        pattern=_DIGEST_PATTERN,
    )
    if _strict_int(result.get("metaFileLinkCount"), label="metaFileLinkCount") != 1:
        raise TextureImportSettingsError("Texture metadata must have exactly one filesystem link.")
    if source_digest_after != source_digest or meta_digest_after != meta_digest:
        raise TextureImportSettingsError("Texture import preview changed project files.")

    importer_type = _canonical_importer_type(result.get("importerType"))
    before_settings = _canonical_settings(
        result.get("beforeSettings"),
        expected_platform=requested_settings["platform"],
        require_target_override=False,
    )
    target_settings = _canonical_settings(
        result.get("targetSettings"),
        expected_platform=requested_settings["platform"],
        require_target_override=True,
    )
    expected_target = {
        "platform": requested_settings["platform"],
        "platformName": _PLATFORM_NAMES[requested_settings["platform"]],
        "overridden": requested_settings["platform"] != "default",
        "maxTextureSize": requested_settings["maxTextureSize"],
        "format": requested_settings["format"],
        "compression": requested_settings["compression"],
        "crunch": requested_settings["crunch"],
        "quality": requested_settings["quality"],
        "ignorePlatformSupport": False,
    }
    if target_settings != expected_target:
        raise TextureImportSettingsError("Texture import preview changed the requested settings.")

    before_settings_digest = _lower_hex(
        result.get("importerSettingsDigestBefore"),
        label="importerSettingsDigestBefore",
        pattern=_DIGEST_PATTERN,
    )
    importer_settings_digest_after = _lower_hex(
        result.get("importerSettingsDigestAfter"),
        label="importerSettingsDigestAfter",
        pattern=_DIGEST_PATTERN,
    )
    target_settings_digest = _lower_hex(
        result.get("targetSettingsDigest"),
        label="targetSettingsDigest",
        pattern=_DIGEST_PATTERN,
    )
    if before_settings_digest != compute_settings_digest(importer_type, before_settings):
        raise TextureImportSettingsError("Texture import preview before-settings digest is invalid.")
    if importer_settings_digest_after != before_settings_digest:
        raise TextureImportSettingsError("Texture import preview changed importer settings.")
    if target_settings_digest != compute_settings_digest(importer_type, target_settings):
        raise TextureImportSettingsError("Texture import preview target-settings digest is invalid.")

    would_change = _strict_bool(result.get("wouldChange"), label="wouldChange")
    if would_change != (before_settings != target_settings):
        raise TextureImportSettingsError("Texture import preview wouldChange is inconsistent.")

    canonical_nested = deepcopy(nested)
    for key in tuple(canonical_nested):
        if key.startswith("expected"):
            canonical_nested.pop(key, None)
    canonical_nested["textureAssetPath"] = texture_path
    canonical_nested.update(requested_settings)
    canonical_nested["preview"] = False
    canonical_nested["saveAndReimport"] = True
    canonical_nested["expectedProjectPath"] = project_path
    canonical_nested["expectedTextureAssetPath"] = texture_path
    canonical_nested["expectedTextureAssetGuid"] = texture_guid
    canonical_nested["expectedSourceFileDigest"] = source_digest
    canonical_nested["expectedSourceFileIdentityDigest"] = source_identity_digest
    canonical_nested["expectedMetaFileDigest"] = meta_digest
    canonical_nested["expectedMetaFileIdentityDigest"] = meta_identity_digest
    canonical_nested["expectedImporterType"] = importer_type
    canonical_nested["expectedImporterSettingsDigest"] = before_settings_digest
    canonical_nested["expectedTargetSettingsDigest"] = target_settings_digest

    canonical_wrapper = deepcopy(wrapper_arguments)
    canonical_wrapper.pop("params", None)
    canonical_wrapper.pop("tool_name", None)
    canonical_wrapper["projectPath"] = project_path
    canonical_wrapper["toolName"] = TOOL_NAME
    canonical_wrapper["arguments"] = canonical_nested

    approval_preview = {
        "schema": APPROVAL_PREVIEW_SCHEMA,
        "toolName": TOOL_NAME,
        "projectPath": project_path,
        "target": {
            "textureAssetPath": texture_path,
            "textureAssetGuid": texture_guid,
            "sourceFileDigest": source_digest,
            "sourceFileIdentityDigest": source_identity_digest,
            "metaFileDigest": meta_digest,
            "metaFileIdentityDigest": meta_identity_digest,
            "importerType": importer_type,
        },
        "change": {
            "before": before_settings,
            "after": target_settings,
            "beforeSettingsDigest": before_settings_digest,
            "afterSettingsDigest": target_settings_digest,
            "wouldChange": would_change,
        },
        "settingsDigestSchema": SETTINGS_DIGEST_SCHEMA,
        "rollbackRequired": True,
    }
    return canonical_wrapper, approval_preview


def compute_settings_digest(importer_type: str, settings: dict[str, Any]) -> str:
    canonical_importer_type = _canonical_importer_type(importer_type)
    if not isinstance(settings, dict):
        raise TextureImportSettingsError("Importer settings must be an object.")
    fields = [
        SETTINGS_DIGEST_SCHEMA,
        canonical_importer_type,
        str(settings.get("platform", "")),
        str(settings.get("platformName", "")),
        _bool_digest_value(settings.get("overridden"), label="overridden"),
        str(_strict_int(settings.get("maxTextureSize"), label="maxTextureSize")),
        str(settings.get("format", "")),
        str(settings.get("compression", "")),
        _bool_digest_value(settings.get("crunch"), label="crunch"),
        str(_bounded_int(settings.get("quality"), label="quality", minimum=0, maximum=100)),
        _bool_digest_value(settings.get("ignorePlatformSupport"), label="ignorePlatformSupport"),
    ]
    framed = "".join(f"{_utf16_length(value)}:{value}" for value in fields)
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def _canonical_settings(
    value: Any,
    *,
    expected_platform: str,
    require_target_override: bool,
) -> dict[str, Any]:
    settings = _require_dict(value, "importer settings")
    platform = _canonical_choice(settings.get("platform"), label="platform", choices=set(_PLATFORM_NAMES))
    if platform != expected_platform:
        raise TextureImportSettingsError("Importer settings platform is invalid.")
    platform_name = _bounded_text(settings.get("platformName"), label="platformName", max_length=64)
    if platform_name != _PLATFORM_NAMES[platform]:
        raise TextureImportSettingsError("Importer settings platformName is invalid.")
    overridden = _strict_bool(settings.get("overridden"), label="overridden")
    if platform == "default" and overridden:
        raise TextureImportSettingsError("Default importer settings cannot be an override.")
    if require_target_override and overridden != (platform != "default"):
        raise TextureImportSettingsError("Target importer override state is invalid.")
    max_texture_size = _strict_int(settings.get("maxTextureSize"), label="maxTextureSize")
    if max_texture_size not in _MAX_TEXTURE_SIZES:
        raise TextureImportSettingsError("Importer maxTextureSize is invalid.")
    texture_format = _canonical_choice(
        settings.get("format"),
        label="format",
        choices=_FORMATS_BY_PLATFORM[platform],
    )
    compression = _canonical_choice(settings.get("compression"), label="compression", choices=_COMPRESSIONS)
    crunch = _strict_bool(settings.get("crunch"), label="crunch")
    quality = _bounded_int(settings.get("quality"), label="quality", minimum=0, maximum=100)
    ignore_platform_support = _strict_bool(
        settings.get("ignorePlatformSupport"),
        label="ignorePlatformSupport",
    )
    return {
        "platform": platform,
        "platformName": platform_name,
        "overridden": overridden,
        "maxTextureSize": max_texture_size,
        "format": texture_format,
        "compression": compression,
        "crunch": crunch,
        "quality": quality,
        "ignorePlatformSupport": ignore_platform_support,
    }


def _canonical_importer_type(value: Any) -> str:
    importer_type = _bounded_text(value, label="importerType", max_length=64)
    if importer_type not in _IMPORTER_TYPES:
        raise TextureImportSettingsError("Texture importer type is unsupported.")
    return importer_type


def _canonical_project_path(value: Any) -> str:
    raw = _bounded_text(value, label="projectPath", max_length=32_768)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise TextureImportSettingsError("projectPath must be absolute.")
    try:
        return str(path.resolve(strict=False))
    except OSError as exc:
        raise TextureImportSettingsError("projectPath is invalid.") from exc


def _safe_asset_path(value: Any, *, label: str) -> str:
    raw = _bounded_text(value, label=label, max_length=2048)
    if "\\" in raw or raw.startswith("/") or raw.endswith("/"):
        raise TextureImportSettingsError(f"{label} is outside Assets/.")
    path = PurePosixPath(raw)
    parts = path.parts
    if len(parts) < 2 or parts[0] != "Assets" or any(part in {"", ".", ".."} for part in parts):
        raise TextureImportSettingsError(f"{label} is outside Assets/.")
    return path.as_posix()


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TextureImportSettingsError(f"{label} must be an object.")
    return value


def _strict_bool(value: Any, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise TextureImportSettingsError(f"{label} must be a boolean.")
    return value


def _bool_digest_value(value: Any, *, label: str) -> str:
    return "true" if _strict_bool(value, label=label) else "false"


def _strict_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TextureImportSettingsError(f"{label} must be an integer.")
    return value


def _bounded_int(value: Any, *, label: str, minimum: int, maximum: int) -> int:
    parsed = _strict_int(value, label=label)
    if not minimum <= parsed <= maximum:
        raise TextureImportSettingsError(f"{label} is out of range.")
    return parsed


def _bounded_text(value: Any, *, label: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise TextureImportSettingsError(f"{label} must be text.")
    parsed = value.strip()
    if not parsed or len(parsed) > max_length or any(ord(character) < 32 for character in parsed):
        raise TextureImportSettingsError(f"{label} is invalid.")
    return parsed


def _canonical_choice(value: Any, *, label: str, choices: set[str]) -> str:
    parsed = _bounded_text(value, label=label, max_length=128).lower()
    if parsed not in choices:
        raise TextureImportSettingsError(f"{label} is unsupported.")
    return parsed


def _lower_hex(value: Any, *, label: str, pattern: re.Pattern[str]) -> str:
    parsed = _bounded_text(value, label=label, max_length=64).lower()
    if pattern.fullmatch(parsed) is None:
        raise TextureImportSettingsError(f"{label} is invalid.")
    return parsed


def _utf16_length(value: str) -> int:
    try:
        return len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise TextureImportSettingsError("Texture settings contain invalid Unicode.") from exc
