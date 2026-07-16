from __future__ import annotations

import ipaddress
import json
import ntpath
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from skill_packages import SKILL_ID_RE, SkillPackageService


PATH_TO_SKILL_SCHEMA = "vrcforge.path_to_skill.v1"
DEFAULT_MIN_VRCFORGE_VERSION = "1.3.0"
WORKFLOW_ENTRYPOINT = "workflows/captured-path.json"

SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|"
    r"gateway[_-]?token|session[_-]?token|secret|password|credential|"
    r"authorization|cookie|private[_-]?key|signing[_-]?key|client[_-]?secret)",
    re.IGNORECASE,
)
DISALLOWED_PAYLOAD_FIELD_RE = re.compile(
    r"(?:bytes|binary|blob|base64|filecontents|rawcontent|archivecontents|"
    r"zipbytes|payloadbytes|binarypayload|packagepayload|unitypackagebytes|"
    r"fbxbytes|texturebytes|materialbytes|assetpayload|paidasset|"
    r"boothzipcontents|packagecontents|screenshotbytes|"
    r"imagebytes|meshbytes|prefabbytes)",
    re.IGNORECASE,
)
ABSOLUTE_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
EMBEDDED_WINDOWS_PATH_RE = re.compile(r"(?P<path>(?<![A-Za-z0-9+.-])(?:[A-Za-z]:[\\/]|\\\\)[^\s\"'<>|]+)")
ABSOLUTE_PATH_PREFIX_RE = re.compile(r"(?<![A-Za-z0-9+.-])(?:[A-Za-z]:[\\/]|\\\\)")
URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
URL_CANDIDATE_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'<>]+", re.IGNORECASE)
PRIVATE_URL_QUERY_RE = re.compile(
    r"(?:^|[&;])(?:[^=&;]*(?:api[_-]?key|token|signature|credential|authorization|x-amz-)[^=&;]*)=",
    re.IGNORECASE,
)
PATH_KEY_RE = re.compile(r"(?:path|root|dir|directory|file)$", re.IGNORECASE)


class PathToSkillError(ValueError):
    """Base exception for unsafe or invalid Path-to-Skill captures."""


class PathToSkillSecurityError(PathToSkillError):
    pass


class PathToSkillValidationError(PathToSkillError):
    pass


@dataclass(frozen=True)
class PathToSkillRecipeDefinition:
    recipe_type: str
    title: str
    shape: str
    write_path: str
    permission_mode: str
    risk_level: str
    argument_hint: str
    requires_approval: bool
    requires_checkpoint: bool
    requires_rollback: bool
    aliases: tuple[str, ...]
    permissions: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    entrypoint_tool: str
    detector_rules: tuple[str, ...]
    required_evidence: tuple[str, ...]
    instructions: tuple[str, ...]

    def as_workflow_payload(self) -> dict[str, Any]:
        payload = {
            "type": self.recipe_type,
            "title": self.title,
            "shape": self.shape,
            "writePath": self.write_path,
            "permissionMode": self.permission_mode,
            "riskLevel": self.risk_level,
            "argumentHint": self.argument_hint,
            "permissions": list(self.permissions),
            "entrypointTool": self.entrypoint_tool,
            "allowedTools": list(self.allowed_tools),
            "detectorRules": list(self.detector_rules),
            "requiredEvidence": list(self.required_evidence),
            "validationDefaults": {
                "requiresApproval": self.requires_approval,
                "requiresCheckpoint": self.requires_checkpoint,
                "requiresRollback": self.requires_rollback,
            },
        }
        if self.write_path == "blocked_preview":
            payload["futureApplyGate"] = {
                "status": "blocked",
                "applyToolExposed": False,
                "requiresApproval": True,
                "requiresCheckpoint": True,
                "requiresRollback": True,
            }
        return payload


PATH_TO_SKILL_RECIPE_DEFINITIONS: dict[str, PathToSkillRecipeDefinition] = {
    "ttt_material_group": PathToSkillRecipeDefinition(
        recipe_type="ttt_material_group",
        title="TTT Material Group",
        shape="approval_gated_material_group",
        write_path="request_only",
        permission_mode="approval_required",
        risk_level="high",
        argument_hint="projectPath={{projectPath}} avatarPath={{avatarPath}} rendererPath=<path> materialSlots=<indices>",
        requires_approval=True,
        requires_checkpoint=True,
        requires_rollback=True,
        aliases=(
            "ttt-material-group",
            "textrans-material-group",
            "ttt-atlas-group",
            "ttt-atlas-material-group",
        ),
        permissions=(
            "read_project",
            "unity_modify_components",
            "unity_modify_materials",
            "unity_run_validation",
            "unity_scan_scene",
        ),
        allowed_tools=(
            "vrcforge_health",
            "vrcforge_unity_status",
            "vrcforge_optimization_material_slot_audit",
            "vrcforge_optimization_ttt_atlas_plan",
            "vrcforge_optimization_ttt_atlas_apply_request",
            "vrcforge_optimization_validation_delta",
        ),
        entrypoint_tool="vrcforge_optimization_ttt_atlas_plan",
        detector_rules=(
            "Require a detected TexTransTool dependency before proposing an apply request.",
            "Capture explicit renderer and material-slot membership; never guess an atlas group.",
            "Flag special shaders or material settings for manual review.",
        ),
        required_evidence=(
            "material-slot audit",
            "user-confirmed renderer and material group",
            "validation delta and rollback verification after an approved request",
        ),
        instructions=(
            "Run the material-slot audit and TTT atlas plan before requesting a write.",
            "Require the user to confirm the exact renderer/material group; do not infer membership.",
            "At most one TTT atlas apply request may be created per execution.",
        ),
    ),
    "booth_import_preflight": PathToSkillRecipeDefinition(
        recipe_type="booth_import_preflight",
        title="Booth Import Preflight",
        shape="read_only_package_preflight",
        write_path="read_only",
        permission_mode="read_only",
        risk_level="low",
        argument_hint="projectPath={{projectPath}} packagePath={{packagePath}}",
        requires_approval=False,
        requires_checkpoint=False,
        requires_rollback=False,
        aliases=("booth-import-preflight", "booth-package-preflight", "outfit-import-preflight"),
        permissions=("read_project", "unity_run_validation", "unity_scan_scene"),
        allowed_tools=(
            "vrcforge_health",
            "vrcforge_unity_status",
            "vrcforge_scan_project_index",
            "vrcforge_inspect_outfit_package",
            "vrcforge_plan_outfit_import",
            "vrcforge_build_test_readiness",
        ),
        entrypoint_tool="vrcforge_inspect_outfit_package",
        detector_rules=(
            "Accept a local Booth folder, ZIP pathname, or UnityPackage pathname as a remapped input only.",
            "Inspect structure and pathname metadata without embedding package contents.",
            "Stop at a supervised import plan; this recipe never imports or writes assets.",
        ),
        required_evidence=(
            "package structure summary",
            "candidate UnityPackage or prefab selection",
            "warnings and expected project targets",
        ),
        instructions=(
            "Inspect package structure first, then produce a no-write outfit import plan.",
            "Never copy Booth, paid-asset, archive, texture, model, or prefab payloads into the skill.",
            "Do not create an import or apply request from this preflight recipe.",
        ),
    ),
    "parameter_compression": PathToSkillRecipeDefinition(
        recipe_type="parameter_compression",
        title="Parameter Compression",
        shape="blocked_parameter_compression_plan",
        write_path="blocked_preview",
        permission_mode="preview",
        risk_level="high",
        argument_hint="projectPath={{projectPath}} avatarPath={{avatarPath}}",
        requires_approval=False,
        requires_checkpoint=False,
        requires_rollback=False,
        aliases=("parameter-compression", "parameter-compressor", "vrcfury-parameter-compression"),
        permissions=("read_project", "unity_run_validation", "unity_scan_scene"),
        allowed_tools=(
            "vrcforge_health",
            "vrcforge_unity_status",
            "vrcforge_optimization_parameter_budget_audit",
            "vrcforge_optimization_parameter_inventory",
            "vrcforge_optimization_parameter_menu_map",
            "vrcforge_optimization_parameter_animator_usage",
            "vrcforge_optimization_parameter_compressibility_plan",
            "vrcforge_optimization_parameter_vrcfury_compressor_plan",
            "vrcforge_optimization_parameter_behavior_regression",
            "vrcforge_optimization_parameter_path_to_skill",
            "vrcforge_optimization_validation_delta",
        ),
        entrypoint_tool="vrcforge_optimization_parameter_path_to_skill",
        detector_rules=(
            "Inventory Expression Parameters, menu controls, and FX animator usage before classifying candidates.",
            "Exclude puppets, OSC or face-tracking inputs, continuous floats, and unknown usages by default.",
            "Keep apply blocked until behavior-regression and rollback proof exist for the selected primitive.",
        ),
        required_evidence=(
            "parameter budget and usage inventory",
            "menu, FX, puppet, OSC, and face-tracking regression plan",
            "explicit hard-gate results for every compression candidate",
        ),
        instructions=(
            "Build the parameter Path-to-Skill plan from inventory and behavior-regression evidence.",
            "Treat unknown or risky parameters as excluded; never infer that they are safe to compress.",
            "This recipe is blocked-preview only and must not expose an apply-request tool.",
        ),
    ),
    "pc_quest_upload_pass": PathToSkillRecipeDefinition(
        recipe_type="pc_quest_upload_pass",
        title="PC / Quest Upload Pass",
        shape="read_only_upload_gate",
        write_path="read_only",
        permission_mode="read_only",
        risk_level="low",
        argument_hint="projectPath={{projectPath}} avatarPath={{avatarPath}} platforms=pc,quest",
        requires_approval=False,
        requires_checkpoint=False,
        requires_rollback=False,
        aliases=("pc-quest-upload-pass", "pc-android-upload-pass", "cross-platform-upload-gate"),
        permissions=("read_project", "unity_run_validation", "unity_scan_scene"),
        allowed_tools=(
            "vrcforge_health",
            "vrcforge_unity_status",
            "vrcforge_run_validation_report",
            "vrcforge_build_test_readiness",
            "vrcforge_optimization_upload_gate_audit",
            "vrcforge_optimization_upload_gate_fix_plan",
        ),
        entrypoint_tool="vrcforge_optimization_upload_gate_audit",
        detector_rules=(
            "Evaluate PC and Quest/Android limits independently from the same remapped project context.",
            "Separate hard upload blockers from performance-rank offenders and risky fixes.",
            "Keep missing SDK metrics unknown; never infer an upload pass from absent data.",
        ),
        required_evidence=(
            "PC upload-gate audit",
            "Quest/Android upload-gate audit",
            "build-test readiness with unknown metrics called out",
        ),
        instructions=(
            "Run read-only upload-gate audits for PC and Quest/Android and report each result separately.",
            "Unknown metrics remain unknown and cannot be converted into a pass.",
            "This recipe never builds, uploads, publishes, or creates a write request.",
        ),
    ),
}


@dataclass(frozen=True)
class CapturedSkillSource:
    manifest: dict[str, Any]
    skill_markdown: str
    workflow: dict[str, Any]
    source_files: dict[str, str]

    def write_to(self, output_dir: str | Path, *, overwrite: bool = False) -> Path:
        return _write_source_tree_atomically(output_dir, self.source_files, overwrite=overwrite)


class _CaptureContext:
    def __init__(self, project_root: str | None) -> None:
        self.project_root = _normalize_windows_path(project_root) if project_root and _is_absolute_path(project_root) else None
        self.variables: dict[str, dict[str, Any]] = {}
        self.remappings: list[dict[str, str]] = []
        self._absolute_path_variables: dict[str, str] = {}
        self._relative_path_variables: dict[str, str] = {}
        self._private_location_variables: dict[str, str] = {}

    def sanitize(self, value: Any, field_path: str = "source") -> Any:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for raw_key, raw_item in value.items():
                key = str(raw_key)
                _reject_key(key, field_path)
                result[key] = self.sanitize(raw_item, f"{field_path}.{key}" if field_path else key)
            return result
        if isinstance(value, (list, tuple)):
            return [self.sanitize(item, f"{field_path}[{index}]") for index, item in enumerate(value)]
        if isinstance(value, (bytes, bytearray, memoryview)):
            raise PathToSkillSecurityError(f"{field_path} contains binary data and cannot be captured.")
        if isinstance(value, str):
            return self._sanitize_text(value, field_path)
        return value

    def _sanitize_text(self, value: str, field_path: str) -> str:
        _reject_sensitive_text(value, field_path)
        stripped = value.strip()
        placeholder_name = _path_placeholder_name(stripped)
        if placeholder_name:
            self._ensure_variable(placeholder_name, f"Provide {field_path} before dry-run or apply.")
            self._record_remapping(field_path, placeholder_name, "existing path variable required")
            return stripped
        if URL_RE.match(stripped):
            if _is_private_url(stripped):
                return self._remap_private_location(stripped, field_path)
            return value
        path_field = _looks_like_path_field(field_path)
        if path_field and _has_parent_path_segment(stripped):
            raise PathToSkillSecurityError(f"{field_path} contains a parent-directory traversal segment.")
        if _is_absolute_path(stripped):
            return self._remap_absolute_path(stripped, field_path)
        if path_field:
            if not stripped or _is_path_placeholder(stripped) or _is_safe_unity_relative_path(stripped, field_path):
                return value
            return self._remap_relative_path(stripped, field_path)
        if ABSOLUTE_PATH_PREFIX_RE.search(value):
            raise PathToSkillSecurityError(
                f"{field_path} contains an embedded absolute path; capture it as a remappable path field instead."
            )
        return value

    def _remap_absolute_path(self, raw_path: str, field_path: str) -> str:
        normalized = _normalize_windows_path(raw_path)
        project_root = self.project_root
        if project_root and normalized == project_root:
            self._ensure_variable("projectPath", "Unity project root selected at import or dry-run time.")
            self._record_remapping(field_path, "projectPath", "absolute project path redacted")
            return "{{projectPath}}"
        if project_root and _is_child_windows_path(normalized, project_root):
            self._ensure_variable("projectPath", "Unity project root selected at import or dry-run time.")
            relative = ntpath.relpath(raw_path, project_root).replace("\\", "/")
            self._record_remapping(field_path, "projectPath", "absolute in-project path redacted")
            return "{{projectPath}}/" + relative
        variable_name = self._absolute_path_variables.get(normalized)
        if not variable_name:
            variable_name = self._variable_name_from_field(field_path)
            suffix = 2
            base = variable_name
            while variable_name in self.variables:
                variable_name = f"{base}{suffix}"
                suffix += 1
            self._absolute_path_variables[normalized] = variable_name
        self._ensure_variable(variable_name, f"Remap {field_path} before dry-run or apply.")
        self._record_remapping(field_path, variable_name, "absolute path redacted")
        return "{{" + variable_name + "}}"

    def _remap_private_location(self, raw_value: str, field_path: str) -> str:
        normalized = raw_value.strip().casefold()
        variable_name = self._private_location_variables.get(normalized)
        if not variable_name:
            variable_name = self._variable_name_from_field(field_path)
            if variable_name in self.variables:
                # Reuse an explicitly supplied placeholder for the same
                # semantic field instead of creating an unbound suffixed name.
                self._private_location_variables[normalized] = variable_name
            else:
                suffix = 2
                base = variable_name
                while variable_name in self.variables:
                    variable_name = f"{base}{suffix}"
                    suffix += 1
                self._private_location_variables[normalized] = variable_name
        self._ensure_variable(variable_name, f"Remap the private location in {field_path} before dry-run or apply.")
        self._record_remapping(field_path, variable_name, "private URL or file location redacted")
        return "{{" + variable_name + "}}"

    def _remap_relative_path(self, raw_path: str, field_path: str) -> str:
        normalized = raw_path.replace("\\", "/").casefold()
        variable_name = self._relative_path_variables.get(normalized)
        if not variable_name:
            variable_name = self._variable_name_from_field(field_path)
            suffix = 2
            base = variable_name
            while variable_name in self.variables:
                variable_name = f"{base}{suffix}"
                suffix += 1
            self._relative_path_variables[normalized] = variable_name
        self._ensure_variable(variable_name, f"Remap {field_path} before dry-run or apply.")
        self._record_remapping(field_path, variable_name, "external relative path redacted")
        return "{{" + variable_name + "}}"

    def _variable_name_from_field(self, field_path: str) -> str:
        key = re.split(r"[.\[]", field_path)[-1].strip("]")
        aliases = {
            "projectpath": "projectPath",
            "projectroot": "projectPath",
            "avatarpath": "avatarPath",
            "rendererpath": "rendererPath",
            "materialpath": "materialPath",
            "packagepath": "packagePath",
            "assetpath": "assetPath",
            "artifactpath": "artifactPath",
            "outputpath": "outputPath",
        }
        compact = re.sub(r"[^A-Za-z0-9]+", "", key)
        variable = aliases.get(compact.lower())
        if variable:
            return variable
        if not compact or not compact[0].isalpha():
            compact = "path"
        return compact[0].lower() + compact[1:]

    def _ensure_variable(self, name: str, description: str) -> None:
        if name not in self.variables:
            self.variables[name] = {
                "placeholder": "{{" + name + "}}",
                "required": True,
                "description": description,
                "source": "redacted",
            }

    def _record_remapping(self, field_path: str, variable_name: str, reason: str) -> None:
        record = {"field": field_path, "variable": variable_name, "reason": reason}
        if record not in self.remappings:
            self.remappings.append(record)


def build_path_to_skill_source(
    summary: Mapping[str, Any],
    *,
    package_id: str | None = None,
    skill_name: str | None = None,
    title: str | None = None,
    version: str = "1.0.0",
    author: str = "VRCForge User",
    min_vrcforge_version: str = DEFAULT_MIN_VRCFORGE_VERSION,
) -> CapturedSkillSource:
    if not isinstance(summary, Mapping):
        raise PathToSkillValidationError("Path-to-Skill summary must be a JSON object.")
    _reject_key_tree(summary)
    _reject_frontmatter_delimiter_tree(summary)
    for field_name, field_value in {
        "package_id": package_id,
        "skill_name": skill_name,
        "title": title,
        "version": version,
        "author": author,
        "min_vrcforge_version": min_vrcforge_version,
    }.items():
        if field_value is not None:
            _reject_frontmatter_delimiter(str(field_value), field_name)
    workflow_id = str(summary.get("workflow") or summary.get("name") or "captured_workflow").strip()
    workflow_slug = _slug(workflow_id, fallback="captured-workflow")
    recipe_definition = _resolve_recipe_definition(summary, workflow_id)
    package_id = package_id or f"community.path-to-skill.{workflow_slug}"
    if not SKILL_ID_RE.fullmatch(package_id):
        raise PathToSkillValidationError("package_id must be a lowercase reverse-domain skill package id.")
    skill_name = _skill_slug(skill_name or workflow_slug)
    title = (title or (recipe_definition.title if recipe_definition else _title_from_slug(workflow_slug)))[:120]

    context = _CaptureContext(_find_project_root(summary))
    sanitized_summary = context.sanitize(dict(summary), "source")
    if recipe_definition is None and not _has_captured_operation(sanitized_summary):
        raise PathToSkillValidationError(
            "Path-to-Skill requires at least one non-empty steps or skillPath item for a generic capture."
        )
    workflow_payload = _build_workflow_payload(
        package_id=package_id,
        skill_name=skill_name,
        title=title,
        workflow_id=workflow_id,
        sanitized_summary=sanitized_summary,
        context=context,
        recipe_definition=recipe_definition,
    )
    manifest = _build_manifest(
        package_id=package_id,
        skill_name=skill_name,
        title=title,
        description=_description(workflow_id, recipe_definition),
        version=version,
        author=author,
        min_vrcforge_version=min_vrcforge_version,
        permissions=_infer_permissions(workflow_id, sanitized_summary, recipe_definition),
        write_path=recipe_definition.write_path if recipe_definition else "request_only",
    )
    skill_markdown = _render_skill_markdown(
        _build_skill_metadata(skill_name, title, workflow_id, workflow_payload, recipe_definition)
    )
    source_files = {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "SKILL.md": skill_markdown,
        WORKFLOW_ENTRYPOINT: json.dumps(workflow_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    }
    _assert_no_private_paths_or_secrets(source_files)
    return CapturedSkillSource(
        manifest=manifest,
        skill_markdown=skill_markdown,
        workflow=workflow_payload,
        source_files=source_files,
    )


def write_path_to_skill_source(
    summary: Mapping[str, Any],
    output_dir: str | Path,
    *,
    overwrite: bool = False,
    **kwargs: Any,
) -> CapturedSkillSource:
    captured = build_path_to_skill_source(summary, **kwargs)
    captured.write_to(output_dir, overwrite=overwrite)
    return captured


def _write_source_tree_atomically(
    output_dir: str | Path,
    source_files: Mapping[str, str],
    *,
    overwrite: bool,
) -> Path:
    # Do not resolve here: resolve() follows a user-controlled output symlink
    # before we get the opportunity to reject it.
    destination = Path(output_dir).expanduser().absolute()
    if destination == destination.parent:
        raise PathToSkillValidationError("Path-to-Skill output cannot be a filesystem root.")

    normalized_files = _validate_source_file_map(source_files)
    _assert_no_private_paths_or_secrets(normalized_files)
    _prepare_output_parent(destination)
    _validate_existing_output(destination, overwrite=overwrite)

    staging_root = Path(
        tempfile.mkdtemp(
            prefix=f".{_slug(destination.name, fallback='captured-skill')}.vrcforge-stage-",
            dir=destination.parent,
        )
    )
    staged_tree = staging_root / "tree"
    previous_tree = staging_root / "previous"
    moved_existing = False
    preserve_staging = False
    try:
        staged_tree.mkdir()
        for relative, content in normalized_files.items():
            target = staged_tree.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")

        # Recheck immediately before publication so a newly introduced link
        # or dirty destination is not silently followed or merged.
        _validate_existing_output(destination, overwrite=overwrite)
        if _path_lexists(destination):
            os.replace(destination, previous_tree)
            moved_existing = True
        try:
            os.replace(staged_tree, destination)
        except OSError as publish_error:
            if moved_existing and _path_lexists(previous_tree) and not _path_lexists(destination):
                try:
                    os.replace(previous_tree, destination)
                    moved_existing = False
                except OSError as restore_error:
                    preserve_staging = True
                    raise PathToSkillValidationError(
                        "Could not publish the staged Path-to-Skill source or restore the previous output; "
                        f"the previous tree remains at {previous_tree}: {restore_error}"
                    ) from publish_error
            raise PathToSkillValidationError(
                f"Could not atomically publish the Path-to-Skill source: {publish_error}"
            ) from publish_error
        return destination
    except (PathToSkillError, OSError) as exc:
        if isinstance(exc, PathToSkillError):
            raise
        raise PathToSkillValidationError(f"Could not stage the Path-to-Skill source: {exc}") from exc
    finally:
        # The stage is a fresh sibling owned by this call. On success this
        # removes the replaced tree; on any pre-publish failure it removes all
        # partial files. A failed restoration is preserved for manual recovery.
        if not preserve_staging:
            shutil.rmtree(staging_root, ignore_errors=True)


def _validate_source_file_map(source_files: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    seen_casefold: set[str] = set()
    for raw_relative, content in source_files.items():
        if not isinstance(raw_relative, str) or not raw_relative:
            raise PathToSkillSecurityError("Captured source file paths must be non-empty relative strings.")
        if "\\" in raw_relative or "\x00" in raw_relative or raw_relative.startswith("/"):
            raise PathToSkillSecurityError(f"Unsafe captured source file path: {raw_relative!r}.")
        parts = raw_relative.split("/")
        if any(
            not part
            or part in {".", ".."}
            or ":" in part
            or part.endswith((" ", "."))
            for part in parts
        ):
            raise PathToSkillSecurityError(f"Unsafe captured source file path: {raw_relative!r}.")
        canonical = "/".join(parts)
        casefolded = canonical.casefold()
        if casefolded in seen_casefold:
            raise PathToSkillSecurityError(f"Duplicate captured source file path: {canonical}.")
        if not isinstance(content, str):
            raise PathToSkillSecurityError(f"Captured source file {canonical} must contain UTF-8 text.")
        seen_casefold.add(casefolded)
        normalized[canonical] = content
    if not normalized:
        raise PathToSkillValidationError("Captured source contains no files to write.")
    return normalized


def _prepare_output_parent(destination: Path) -> None:
    _assert_no_link_components(destination)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PathToSkillValidationError(f"Could not create the Path-to-Skill output parent: {exc}") from exc
    _assert_no_link_components(destination)
    if not destination.parent.is_dir():
        raise PathToSkillValidationError("Path-to-Skill output parent must be a directory.")


def _validate_existing_output(destination: Path, *, overwrite: bool) -> None:
    _assert_no_link_components(destination)
    if not _path_lexists(destination):
        return
    if _path_is_link_or_reparse(destination):
        raise PathToSkillSecurityError("Path-to-Skill output cannot be a symlink, junction, or reparse point.")
    if not destination.is_dir():
        raise PathToSkillValidationError("Path-to-Skill output must be a directory.")
    if not overwrite:
        raise PathToSkillValidationError(
            "Path-to-Skill output already exists; pass overwrite=True only after explicitly confirming replacement."
        )
    _assert_tree_has_no_links(destination)


def _assert_no_link_components(path: Path) -> None:
    candidate = path
    while True:
        if _path_lexists(candidate) and _path_is_link_or_reparse(candidate):
            raise PathToSkillSecurityError(
                f"Path-to-Skill output cannot traverse a symlink, junction, or reparse point: {candidate}"
            )
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent


def _assert_tree_has_no_links(root: Path) -> None:
    def raise_walk_error(error: OSError) -> None:
        raise error

    try:
        for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False, onerror=raise_walk_error):
            current_path = Path(current)
            for name in (*directory_names, *file_names):
                candidate = current_path / name
                if _path_is_link_or_reparse(candidate):
                    raise PathToSkillSecurityError(
                        f"Refusing to overwrite a Path-to-Skill tree containing a link or reparse point: {candidate}"
                    )
    except OSError as exc:
        raise PathToSkillValidationError(f"Could not inspect the existing Path-to-Skill output: {exc}") from exc


def _path_lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _path_is_link_or_reparse(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PathToSkillValidationError(f"Could not inspect Path-to-Skill output path {path}: {exc}") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(details.st_mode) or bool(getattr(details, "st_file_attributes", 0) & reparse_flag)


def _resolve_recipe_definition(
    summary: Mapping[str, Any],
    workflow_id: str,
) -> PathToSkillRecipeDefinition | None:
    explicit_value = summary.get("recipeType")
    if explicit_value is None:
        explicit_value = summary.get("recipe_type")
    if explicit_value is not None:
        if not isinstance(explicit_value, str) or not explicit_value.strip():
            raise PathToSkillValidationError("recipeType must be a non-empty string when provided.")
        normalized = _normalize_recipe_type(explicit_value)
        for recipe_type, definition in PATH_TO_SKILL_RECIPE_DEFINITIONS.items():
            names = (recipe_type, *definition.aliases)
            if normalized in {_normalize_recipe_type(name) for name in names}:
                return definition
        supported = ", ".join(sorted(PATH_TO_SKILL_RECIPE_DEFINITIONS))
        raise PathToSkillValidationError(f"Unknown Path-to-Skill recipeType: {explicit_value}. Supported: {supported}.")

    normalized = _normalize_recipe_type(workflow_id)
    for definition in PATH_TO_SKILL_RECIPE_DEFINITIONS.values():
        names = (definition.recipe_type, *definition.aliases)
        if normalized in {_normalize_recipe_type(name) for name in names}:
            return definition
    return None


def _normalize_recipe_type(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _has_captured_operation(summary: Mapping[str, Any]) -> bool:
    def meaningful(value: Any) -> bool:
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, Mapping):
            return any(meaningful(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(meaningful(item) for item in value)
        return False

    return any(
        isinstance(summary.get(key), list) and meaningful(summary.get(key))
        for key in ("steps", "skillPath")
    )


def _build_workflow_payload(
    *,
    package_id: str,
    skill_name: str,
    title: str,
    workflow_id: str,
    sanitized_summary: dict[str, Any],
    context: _CaptureContext,
    recipe_definition: PathToSkillRecipeDefinition | None,
) -> dict[str, Any]:
    validation = sanitized_summary.get("validation") if isinstance(sanitized_summary.get("validation"), dict) else {}
    default_approval = recipe_definition.requires_approval if recipe_definition else True
    default_checkpoint = recipe_definition.requires_checkpoint if recipe_definition else True
    default_rollback = recipe_definition.requires_rollback if recipe_definition else True
    validation = {
        "requiresApproval": bool(validation.get("requiresApproval", default_approval)),
        "requiresCheckpoint": bool(validation.get("requiresCheckpoint", default_checkpoint)),
        "requiresRollback": bool(validation.get("requiresRollback", default_rollback)),
        **validation,
    }
    write_path = recipe_definition.write_path if recipe_definition else "request_only"
    if write_path == "blocked_preview":
        # These gates describe the current preview execution. Any future apply
        # path is separately and explicitly represented by recipe.futureApplyGate.
        validation["requiresApproval"] = False
        validation["requiresCheckpoint"] = False
        validation["requiresRollback"] = False
    elif write_path == "request_only":
        # Every current write-capable capture, including a generic capture,
        # must retain the complete supervised write gate. Captured evidence
        # may make policy more conservative but can never turn these off.
        validation["requiresApproval"] = True
        validation["requiresCheckpoint"] = True
        validation["requiresRollback"] = True
    elif recipe_definition:
        validation["requiresApproval"] = bool(validation.get("requiresApproval")) or default_approval
        validation["requiresCheckpoint"] = bool(validation.get("requiresCheckpoint")) or default_checkpoint
        validation["requiresRollback"] = bool(validation.get("requiresRollback")) or default_rollback
    steps = sanitized_summary.get("steps")
    if not isinstance(steps, list):
        steps = sanitized_summary.get("skillPath") if isinstance(sanitized_summary.get("skillPath"), list) else []
    payload = {
        "schema": PATH_TO_SKILL_SCHEMA,
        "id": package_id,
        "skillName": skill_name,
        "title": title,
        "workflow": workflow_id,
        "proofPassed": _proof_passed(sanitized_summary),
        "variables": context.variables,
        "remapping": {
            "required": sorted(context.variables),
            "fields": context.remappings,
        },
        "steps": steps,
        "validation": validation,
        "sourceSummary": sanitized_summary,
        "privacy": {
            "absolutePaths": "redacted_to_variables",
            "secrets": "rejected",
            "binaryPayloads": "rejected",
            "paidAssetPayloads": "rejected",
        },
    }
    if recipe_definition:
        payload["recipe"] = recipe_definition.as_workflow_payload()
    return payload


def _build_manifest(
    *,
    package_id: str,
    skill_name: str,
    title: str,
    description: str,
    version: str,
    author: str,
    min_vrcforge_version: str,
    permissions: list[str],
    write_path: str,
) -> dict[str, Any]:
    return {
        "id": package_id,
        "name": title[:160],
        "skill_name": skill_name,
        "version": version,
        "author": author[:160],
        "description": description[:4000],
        "min_vrcforge_version": min_vrcforge_version,
        "permissions": permissions,
        "entrypoints": {
            "skill": "SKILL.md",
            "workflow": WORKFLOW_ENTRYPOINT,
        },
        "agent": {
            "schema": PATH_TO_SKILL_SCHEMA,
            "dry_run_required": True,
            "write_path": write_path,
        },
    }


def _build_skill_metadata(
    skill_name: str,
    title: str,
    workflow_id: str,
    workflow_payload: dict[str, Any],
    recipe_definition: PathToSkillRecipeDefinition | None,
) -> dict[str, Any]:
    metadata = {
        "name": skill_name,
        "title": title,
        "description": _description(workflow_id, recipe_definition),
        "category": "workflow",
        "permissionMode": "approval_required",
        "riskLevel": "high",
        "whenToUse": f"Reuse the proven {workflow_id} workflow with remapped project variables.",
        "inputs": ["projectPath remapping", "avatarPath or workflow target remapping", "dry-run evidence"],
        "outputs": ["Preview plan", "approval request", "validation delta", "rollback verification"],
        "sideEffects": "dry-run is read-only; approved execution may request one supervised VRCForge write",
        "backupRestore": "requires approval, checkpoint, validation, and rollback verification",
        "allowedTools": _infer_allowed_tools(workflow_id, workflow_payload, recipe_definition),
        "disallowedTools": ["vrcforge_execute_shell", "direct_unity_asset_write"],
        "entrypointTool": _entrypoint_tool(workflow_id, recipe_definition),
        "userInvocable": True,
        "disableModelInvocation": False,
        "argumentHint": "projectPath={{projectPath}} avatarPath={{avatarPath}}",
        "supportFiles": [WORKFLOW_ENTRYPOINT],
        "testCommand": "dry-run captured workflow before any apply request",
        "enabled": True,
        "tags": ["path-to-skill", "captured-workflow", "request-only"],
        "instructions": _skill_instructions(workflow_id, recipe_definition),
    }
    if recipe_definition:
        metadata.update(
            {
                "permissionMode": recipe_definition.permission_mode,
                "riskLevel": recipe_definition.risk_level,
                "whenToUse": (
                    f"Reuse the {recipe_definition.title} recipe with remapped project variables and its detector rules."
                ),
                "inputs": [
                    "all variables listed in the captured workflow remapping",
                    "detector evidence required by the selected recipe",
                ],
                "outputs": list(recipe_definition.required_evidence),
                "argumentHint": recipe_definition.argument_hint,
                "testCommand": "dry-run the captured recipe and verify every detector and validation gate",
                "tags": [
                    "path-to-skill",
                    "captured-workflow",
                    recipe_definition.recipe_type.replace("_", "-"),
                    recipe_definition.shape.replace("_", "-"),
                    recipe_definition.write_path.replace("_", "-"),
                ],
            }
        )
        if recipe_definition.write_path == "request_only":
            metadata["sideEffects"] = "creates at most one supervised approval request; it never applies directly"
            metadata["backupRestore"] = "approved execution requires checkpoint, validation, and rollback verification"
        elif recipe_definition.write_path == "blocked_preview":
            metadata["sideEffects"] = "none; the apply path remains blocked"
            metadata["backupRestore"] = "not required for preview; future apply remains blocked pending rollback proof"
        else:
            metadata["sideEffects"] = "none"
            metadata["backupRestore"] = "not required; this recipe is read-only"
    return metadata


def _skill_instructions(
    workflow_id: str,
    recipe_definition: PathToSkillRecipeDefinition | None,
) -> str:
    lines = [
        f"Use {WORKFLOW_ENTRYPOINT} as the captured workflow definition for `{workflow_id}`.",
        "Before using it on a new avatar, require the user to remap every variable listed in the workflow payload.",
        "Run the read/preview tools first and compare validation signals with the captured proof summary.",
    ]
    if recipe_definition:
        lines[1:1] = list(recipe_definition.instructions)
    if recipe_definition and recipe_definition.write_path == "blocked_preview":
        lines.extend(
            [
                "Do not request or perform a project write from this preview-only recipe.",
                "The workflow futureApplyGate records the approval, checkpoint, and rollback proof required before any future apply path may be exposed.",
            ]
        )
    elif recipe_definition and recipe_definition.write_path == "read_only":
        lines.append("Do not request or perform a project write from this read-only recipe.")
    else:
        lines.extend(
            [
                "For any project write, create a VRCForge approval request only; do not call direct apply tools yourself.",
                "After an approved write, verify the checkpoint, validation delta, and rollback verification evidence.",
            ]
        )
    return "\n".join(lines)


def _infer_allowed_tools(
    workflow_id: str,
    workflow_payload: Mapping[str, Any],
    recipe_definition: PathToSkillRecipeDefinition | None = None,
) -> list[str]:
    if recipe_definition:
        return list(dict.fromkeys(recipe_definition.allowed_tools))
    text = json.dumps({"workflow": workflow_id, "payload": workflow_payload}, ensure_ascii=False).lower()
    tools = ["vrcforge_health", "vrcforge_unity_status", "vrcforge_request_apply"]
    if "shader" in text or "material" in text:
        tools.extend(["vrcforge_scan_materials", "vrcforge_plan_shader_tuning", "vrcforge_apply_shader_tuning"])
    if "optimizer" in text or "optimization" in text or "meshia" in text or "aao" in text or "parameter" in text:
        tools.extend(["vrcforge_optimization_plan", "vrcforge_optimization_validation_delta"])
    if "booth" in text or "outfit" in text or "prefab" in text:
        tools.extend(["vrcforge_inspect_outfit_package", "vrcforge_preview_add_outfit"])
    return sorted(dict.fromkeys(tools))


def _entrypoint_tool(
    workflow_id: str,
    recipe_definition: PathToSkillRecipeDefinition | None = None,
) -> str:
    if recipe_definition:
        return recipe_definition.entrypoint_tool
    text = workflow_id.lower()
    if "shader" in text or "material" in text:
        return "vrcforge_plan_shader_tuning"
    if "optimizer" in text or "optimization" in text or "parameter" in text or "meshia" in text:
        return "vrcforge_optimization_plan"
    if "outfit" in text or "booth" in text:
        return "vrcforge_preview_add_outfit"
    return "vrcforge_health"


def _infer_permissions(
    workflow_id: str,
    sanitized_summary: Mapping[str, Any],
    recipe_definition: PathToSkillRecipeDefinition | None = None,
) -> list[str]:
    if recipe_definition:
        return sorted(dict.fromkeys(recipe_definition.permissions))
    text = json.dumps({"workflow": workflow_id, "summary": sanitized_summary}, ensure_ascii=False).lower()
    permissions = {"read_project", "unity_scan_scene", "unity_run_validation"}
    if "shader" in text or "material" in text:
        permissions.add("unity_modify_materials")
    if "optimizer" in text or "optimization" in text or "meshia" in text or "aao" in text or "parameter" in text:
        permissions.add("unity_modify_components")
    if "booth" in text or "outfit" in text or "prefab" in text:
        permissions.add("unity_modify_prefab")
    return sorted(permissions)


def _render_skill_markdown(skill: Mapping[str, Any]) -> str:
    metadata_keys = [
        ("name", "name"),
        ("title", "title"),
        ("description", "description"),
        ("category", "category"),
        ("permission-mode", "permissionMode"),
        ("risk-level", "riskLevel"),
        ("when-to-use", "whenToUse"),
        ("inputs", "inputs"),
        ("outputs", "outputs"),
        ("side-effects", "sideEffects"),
        ("backup-restore", "backupRestore"),
        ("allowed-tools", "allowedTools"),
        ("disallowed-tools", "disallowedTools"),
        ("entrypoint-tool", "entrypointTool"),
        ("user-invocable", "userInvocable"),
        ("disable-model-invocation", "disableModelInvocation"),
        ("argument-hint", "argumentHint"),
        ("support-files", "supportFiles"),
        ("test-command", "testCommand"),
        ("enabled", "enabled"),
        ("tags", "tags"),
    ]
    lines = ["---"]
    for yaml_key, key in metadata_keys:
        value = skill.get(key)
        if isinstance(value, list):
            lines.append(f"{yaml_key}:")
            for item in value:
                lines.append(f"  - {_yaml_scalar(str(item))}")
        elif isinstance(value, bool):
            lines.append(f"{yaml_key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{yaml_key}: {_yaml_scalar(str(value or ''))}")
    lines.append("---")
    lines.append("")
    lines.append(str(skill.get("instructions") or "").strip())
    lines.append("")
    return "\n".join(lines)


def _yaml_scalar(value: str) -> str:
    _reject_frontmatter_delimiter(value, "generated frontmatter scalar")
    text = value.replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return '""'
    if re.search(r"[:#\[\],`{}]|^\s|\s$", text):
        return json.dumps(text, ensure_ascii=False)
    return text


def _proof_passed(summary: Mapping[str, Any]) -> bool:
    if "proofPassed" in summary:
        return bool(summary.get("proofPassed"))
    status = str(summary.get("status") or summary.get("result") or "").strip().lower()
    return status in {"passed", "pass", "ok", "success", "succeeded"}


def _find_project_root(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            compact = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if compact in {"projectpath", "projectroot", "unityprojectroot"} and isinstance(item, str):
                return item
        for item in value.values():
            found = _find_project_root(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_project_root(item)
            if found:
                return found
    return None


def _reject_key_tree(value: Any, field_path: str = "source") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            _reject_key(key, field_path)
            _reject_key_tree(item, f"{field_path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_key_tree(item, f"{field_path}[{index}]")
    elif isinstance(value, (bytes, bytearray, memoryview)):
        raise PathToSkillSecurityError(f"{field_path} contains binary data and cannot be captured.")


def _reject_frontmatter_delimiter_tree(value: Any, field_path: str = "source") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            _reject_frontmatter_delimiter(key, f"{field_path} key")
            _reject_frontmatter_delimiter_tree(item, f"{field_path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_frontmatter_delimiter_tree(item, f"{field_path}[{index}]")
    elif isinstance(value, str):
        _reject_frontmatter_delimiter(value, field_path)


def _reject_frontmatter_delimiter(value: str, field_path: str) -> None:
    if "---" in value:
        raise PathToSkillSecurityError(
            f"{field_path} contains the reserved YAML frontmatter delimiter and cannot be captured."
        )


def _reject_key(key: str, field_path: str) -> None:
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    if SECRET_KEY_RE.search(key) or SECRET_KEY_RE.search(compact):
        raise PathToSkillSecurityError(f"{field_path}.{key} is a secret/token field and cannot be captured.")
    if DISALLOWED_PAYLOAD_FIELD_RE.search(compact):
        raise PathToSkillSecurityError(f"{field_path}.{key} looks like a paid asset or binary payload field.")


def _reject_sensitive_text(value: str, field_path: str) -> None:
    if not value:
        return
    if SkillPackageService._contains_sensitive_content(value.encode("utf-8", errors="ignore")):
        raise PathToSkillSecurityError(f"{field_path} contains secret or binary-looking material.")


def _looks_like_path_field(field_path: str) -> bool:
    key = re.split(r"[.\[]", field_path)[-1].strip("]")
    return bool(PATH_KEY_RE.search(key))


def _has_parent_path_segment(value: str) -> bool:
    return any(segment == ".." for segment in re.split(r"[\\/]+", value))


def _is_path_placeholder(value: str) -> bool:
    return _path_placeholder_name(value) is not None


def _path_placeholder_name(value: str) -> str | None:
    match = re.fullmatch(r"\{\{([A-Za-z][A-Za-z0-9]*)\}\}", value)
    return match.group(1) if match else None


def _is_private_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return True
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        return True
    if parsed.username or parsed.password:
        return True
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    if not hostname:
        return True
    if (
        hostname == "localhost"
        or hostname.endswith((".localhost", ".local", ".internal", ".private", ".lan", ".home.arpa"))
        or "." not in hostname
    ):
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address and (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return True
    if PRIVATE_URL_QUERY_RE.search(parsed.query):
        return True
    decoded_location = unquote(" ".join((parsed.path, parsed.query, parsed.fragment)))
    return bool(ABSOLUTE_PATH_PREFIX_RE.search(decoded_location))


def _is_safe_unity_relative_path(value: str, field_path: str) -> bool:
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    first_segment = normalized.split("/", 1)[0].casefold()
    if first_segment in {"assets", "packages", "projectsettings"}:
        return True

    key = re.split(r"[.\[]", field_path)[-1].strip("]")
    compact = re.sub(r"[^a-z0-9]", "", key.casefold())
    return compact in {
        "avatarpath",
        "gameobjectpath",
        "hierarchypath",
        "objectpath",
        "rendererpath",
        "sceneobjectpath",
        "transformpath",
    }


def _is_absolute_path(value: str) -> bool:
    text = str(value or "").strip()
    if not text or URL_RE.match(text):
        return False
    return bool(ABSOLUTE_WINDOWS_PATH_RE.match(text) or text.startswith("\\\\") or (text.startswith("/") and not text.startswith("//")))


def _normalize_windows_path(value: str | None) -> str:
    if not value:
        return ""
    return ntpath.normcase(ntpath.normpath(str(value).strip()))


def _is_child_windows_path(path: str, parent: str) -> bool:
    try:
        common = ntpath.commonpath([path, parent])
    except ValueError:
        return False
    return common == parent and path != parent


def _assert_no_private_paths_or_secrets(source_files: Mapping[str, str]) -> None:
    serialized = "\n".join(source_files.values())
    if EMBEDDED_WINDOWS_PATH_RE.search(serialized):
        raise PathToSkillSecurityError("Captured skill output still contains an absolute Windows path.")
    if any(_is_private_url(match.group(0)) for match in URL_CANDIDATE_RE.finditer(serialized)):
        raise PathToSkillSecurityError("Captured skill output still contains a private URL or file location.")
    if SkillPackageService._contains_sensitive_content(serialized.encode("utf-8")):
        raise PathToSkillSecurityError("Captured skill output still contains secret or binary-looking material.")


def _slug(value: str, *, fallback: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-_")
    return text or fallback


def _skill_slug(value: str) -> str:
    text = _slug(value, fallback="captured-workflow")
    if not re.match(r"^[a-z]", text):
        text = f"skill-{text}"
    return text[:80].rstrip("-_") or "captured-workflow"


def _title_from_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", slug) if part) or "Captured Workflow"


def _description(
    workflow_id: str,
    recipe_definition: PathToSkillRecipeDefinition | None = None,
) -> str:
    recipe_prefix = f"{recipe_definition.title} Path-to-Skill" if recipe_definition else "Captured VRCForge workflow"
    return (
        f"{recipe_prefix} recipe for {workflow_id}; variables must be remapped "
        "and any write must use approval, checkpoint, validation, and rollback verification."
    )
