from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, TextIO


DEFAULT_ENDPOINT = "http://127.0.0.1:8757"
PLAN_SCHEMA = "vrcforge.cli_plan.v1"
SKILL_WORKFLOW_SCHEMA = "vrcforge.skill-package.workflow.v1"
SKILL_LOCK_VALIDATION_SCHEMA = "vrcforge.skill-lock-validation.v1"
DEFAULT_SKILL_MIN_VRCFORGE_VERSION = "1.3.0"
SKILL_WRITE_ENTRYPOINT = "vrcforge_request_apply"
SKILL_WRITE_CONTROL_TOOLS = {
    SKILL_WRITE_ENTRYPOINT,
    "vrcforge_apply_approved",
    "vrcforge_execute_approved_shell",
}
SKILL_MUTATING_PERMISSIONS = {
    "unity_modify_materials",
    "unity_modify_prefab",
    "unity_modify_components",
    "write_project_files",
    "delete_files",
    "execute_shell",
    "run_editor_script",
    "write_outside_project",
}
SKILL_ID_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?)+$"
)


class CliError(RuntimeError):
    pass


class VRCForgeClient:
    def __init__(self, endpoint: str = DEFAULT_ENDPOINT, token: str = "", timeout: float = 30.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.token = token.strip()
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.endpoint}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - loopback user-selected endpoint.
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CliError(f"HTTP {exc.code} {method.upper()} {path}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CliError(
                f"Cannot reach VRCForge runtime at {self.endpoint}. Open VRCForge Desktop or start the local backend first. {exc}"
            ) from exc
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CliError(f"Runtime returned non-JSON for {method.upper()} {path}: {raw[:500]}") from exc


def default_token() -> str:
    env_token = os.environ.get("VRCFORGE_APP_SESSION_TOKEN", "").strip()
    if env_token:
        return env_token
    local_base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or ""
    if not local_base:
        return ""
    token_path = Path(local_base) / "VRCForge" / "agentic-app" / "config" / "app-session-token"
    try:
        return token_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_json(payload: Any, stdout: TextIO) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    stdout.write("\n")


def write_summary(payload: dict[str, Any], stdout: TextIO) -> None:
    write_json(payload, stdout)


def confirm(prompt: str, expected: str, stdin: TextIO, stdout: TextIO) -> bool:
    stdout.write(f"{prompt}\nType {expected} to continue: ")
    stdout.flush()
    return stdin.readline().strip() == expected


def request_payload_from_common(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if getattr(args, "project", ""):
        payload["projectPath"] = args.project
    if getattr(args, "avatar", ""):
        payload["avatarPath"] = args.avatar
    return payload


def build_outfit_plan_document(args: argparse.Namespace, preview: dict[str, Any]) -> dict[str, Any]:
    request = {
        "packagePath": args.package,
        "projectPath": args.project or "",
        "targetFolder": args.target_folder or "",
        "selectedUnityPackage": args.selected_unity_package or "",
        "selectedPrefab": args.selected_prefab or "",
        "baseAvatarName": args.base_avatar_name or "",
        "maxEntries": args.max_entries,
    }
    request = {key: value for key, value in request.items() if value not in ("", None)}
    return {
        "schema": PLAN_SCHEMA,
        "kind": "outfit_import",
        "request": request,
        "preview": preview,
        "readyToApply": bool((preview.get("plan") or {}).get("readyToApply")),
    }


def load_plan(path: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise CliError(f"Cannot read request plan: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CliError(f"Request plan is not valid JSON: {path}") from exc
    if payload.get("schema") != PLAN_SCHEMA:
        raise CliError(f"Unsupported request plan schema: {payload.get('schema')!r}")
    return payload


def path_is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def checkpoint_project_root(preview: dict[str, Any]) -> str:
    checkpoint = preview.get("checkpoint") if isinstance(preview.get("checkpoint"), dict) else {}
    return str(checkpoint.get("projectRoot") or checkpoint.get("project_root") or "").strip()


def maybe_execute_approval(
    client: VRCForgeClient,
    approval: dict[str, Any] | None,
    args: argparse.Namespace,
    stdin: TextIO,
    stdout: TextIO,
) -> dict[str, Any] | None:
    if not getattr(args, "execute", False):
        return None
    approval_id = str((approval or {}).get("id") or "").strip()
    if not approval_id:
        raise CliError("Runtime did not return an approval id to execute.")
    if not getattr(args, "yes", False) and not confirm("This executes through VRCForge approval/checkpoint/apply.", "APPLY", stdin, stdout):
        raise CliError("Execution cancelled.")
    return client.request("POST", f"/api/app/agent/approvals/{urllib.parse.quote(approval_id)}/approve")


def command_doctor(client: VRCForgeClient, args: argparse.Namespace) -> dict[str, Any]:
    return client.request("GET", "/api/app/doctor")


def command_provider_test(client: VRCForgeClient, args: argparse.Namespace) -> dict[str, Any]:
    return client.request(
        "POST",
        "/api/app/provider/test",
        {
            "provider": args.provider,
            "api_key": args.api_key or "",
            "base_url": args.base_url or "",
            "model": args.model or "",
            "capability": args.capability,
        },
    )


def command_unity_status(client: VRCForgeClient, args: argparse.Namespace) -> dict[str, Any]:
    payload = client.request("GET", "/api/app/bootstrap")
    health = payload.get("health") or {}
    components = health.get("components") or {}
    return {
        "ok": bool(payload.get("ok")),
        "version": (payload.get("app") or {}).get("version"),
        "selectedProject": (health.get("state") or {}).get("selected_project_path"),
        "unity": {
            key: value
            for key, value in components.items()
            if "unity" in key.lower() or "mcp" in key.lower() or "vrcforge" in key.lower()
        },
    }


def command_project_scan(client: VRCForgeClient, args: argparse.Namespace) -> dict[str, Any]:
    return client.request("POST", "/api/app/project-index/scan", {"projectPath": args.project, "maxFiles": args.max_files})


def command_avatar_scan(client: VRCForgeClient, args: argparse.Namespace) -> dict[str, Any]:
    return client.request(
        "POST",
        "/api/app/agent/message",
        {
            "agent_name": "cli-agent",
            "message": "Run a read-only avatar scan.",
            "skill_tool": "vrcforge_scan_avatar_items",
            "skill_params": {"avatarPath": args.avatar or None},
            "history": [],
        },
    )


def command_validation_run(client: VRCForgeClient, args: argparse.Namespace) -> dict[str, Any]:
    return client.request("POST", "/api/app/validation/report", request_payload_from_common(args))


def command_build_test_readiness(client: VRCForgeClient, args: argparse.Namespace) -> dict[str, Any]:
    return client.request("POST", "/api/app/build-test/readiness", request_payload_from_common(args))


def command_checkpoint_list(client: VRCForgeClient, args: argparse.Namespace) -> dict[str, Any]:
    query = urllib.parse.urlencode({"projectRoot": args.project or "", "limit": args.limit})
    return client.request("GET", f"/api/app/checkpoints?{query}")


def command_checkpoint_preview(client: VRCForgeClient, args: argparse.Namespace) -> dict[str, Any]:
    return client.request("POST", f"/api/app/checkpoints/{urllib.parse.quote(args.checkpoint_id)}/preview")


def command_skill_list(client: VRCForgeClient, args: argparse.Namespace) -> dict[str, Any]:
    return client.request("GET", "/api/app/skills")


def _skill_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").strip().lower()).strip("-_")
    if not normalized or len(normalized) > 80:
        raise CliError("Skill name must contain lowercase letters, numbers, dashes, or underscores.")
    return normalized


def _skill_title(value: str) -> str:
    raw = str(value or "")
    if "---" in raw:
        raise CliError("Skill frontmatter values cannot contain the '---' document delimiter.")
    title = re.sub(r"[\r\n]+", " ", raw).strip()
    if not title:
        raise CliError("Skill title cannot be empty.")
    return title[:120]


def _frontmatter_scalar(value: str) -> str:
    raw = str(value or "")
    if "---" in raw:
        raise CliError("Skill frontmatter values cannot contain the '---' document delimiter.")
    text = re.sub(r"[\r\n]+", " ", raw).strip()
    if not text:
        return '""'
    if re.search(r"[:#\[\],]|^\s|\s$", text):
        return json.dumps(text, ensure_ascii=False)
    return text


def _skill_skeleton_files(args: argparse.Namespace) -> dict[str, str]:
    package_id = str(args.package_id or "").strip().lower()
    if not SKILL_ID_RE.fullmatch(package_id):
        raise CliError("--id must be a lowercase reverse-domain package id.")
    name = _skill_name(args.name or package_id.rsplit(".", 1)[-1])
    title = _skill_title(args.title or name.replace("-", " ").replace("_", " ").title())
    tool = str(args.tool or "").strip()
    if not re.fullmatch(r"vrcforge_[a-z0-9_]{1,71}", tool):
        raise CliError("--tool must be a static VRCForge tool id.")
    permissions = sorted({str(item).strip() for item in (args.permission or ["read_project"]) if str(item).strip()})
    if not permissions:
        raise CliError("At least one --permission is required.")
    writes = bool(args.writes)
    if writes and tool in SKILL_WRITE_CONTROL_TOOLS:
        raise CliError("--tool must name the explicit write target, not an approval control or wrapper tool.")
    if not writes and tool == SKILL_WRITE_ENTRYPOINT:
        raise CliError("vrcforge_request_apply is only a generated wrapper entrypoint for --writes skills.")
    if writes and not SKILL_MUTATING_PERMISSIONS.intersection(permissions):
        supported = ", ".join(sorted(SKILL_MUTATING_PERMISSIONS))
        raise CliError(
            "--writes requires at least one explicit mutating --permission; "
            f"choose the permission matching the target tool ({supported})."
        )
    workflow_tool = SKILL_WRITE_ENTRYPOINT if writes else tool
    runtime_entrypoint_tool = "" if writes else tool
    target_tool = tool if writes else ""
    workflow_path = f"workflows/{name}.json"
    test_path = "tests/test_skill_smoke.py"
    raw_description = str(args.description or f"{title} VRCForge skill package.")
    if "---" in raw_description:
        raise CliError("Skill frontmatter values cannot contain the '---' document delimiter.")
    description = re.sub(
        r"[\r\n]+",
        " ",
        raw_description,
    ).strip()
    author = re.sub(r"[\r\n]+", " ", str(args.author or "VRCForge Skill Author")).strip()
    manifest = {
        "id": package_id,
        "name": title,
        "skill_name": name,
        "version": str(args.version or "1.0.0").strip(),
        "author": author,
        "description": description,
        "min_vrcforge_version": str(
            args.min_vrcforge_version or DEFAULT_SKILL_MIN_VRCFORGE_VERSION
        ).strip(),
        "permissions": permissions,
        "entrypoints": {"skill": "SKILL.md", "workflow": workflow_path},
    }
    step: dict[str, Any] = {
        "name": "request_apply" if writes else "run",
        "tool": workflow_tool,
        "writes": writes,
    }
    if writes:
        step["request"] = {"targetTool": target_tool}
    workflow = {
        "schema": SKILL_WORKFLOW_SCHEMA,
        "mode": "approval_gated" if writes else "read_only",
        "steps": [step],
        "approval": {
            "required": writes,
            "reason": "Write tools must use the VRCForge approval boundary." if writes else "The tool call is read-only.",
        },
        "checkpoint": {
            "required": writes,
            "scope": ["Assets", "Packages", "ProjectSettings"] if writes else [],
        },
        "rollback": {
            "required": writes,
            "reason": "Write tools require checkpoint and rollback evidence." if writes else "No project state is mutated.",
        },
    }
    allowed_tool_names = [workflow_tool, *([target_tool] if target_tool else [])]
    allowed_tools = "\n".join(f"  - {item}" for item in allowed_tool_names)
    if writes:
        instructions = (
            f"Execute exactly one gated `{SKILL_WRITE_ENTRYPOINT}` call with `target_tool` fixed to "
            f"`{target_tool}`. Never call `{target_tool}` directly. Preserve the runtime approval, "
            "checkpoint, audit, and rollback boundaries."
        )
    else:
        instructions = (
            f"Execute exactly one read-only `{tool}` call. Preserve the runtime audit boundary and do not "
            "introduce project writes."
        )
    runtime_entrypoint_line = (
        f"entrypoint-tool: {runtime_entrypoint_tool}\n" if runtime_entrypoint_tool else ""
    )
    skill_markdown = (
        "---\n"
        f"name: {name}\n"
        f"title: {_frontmatter_scalar(title)}\n"
        f"description: {_frontmatter_scalar(description)}\n"
        f"permission-mode: {'approval_required' if writes else 'read_only'}\n"
        f"risk-level: {'high' if writes else 'low'}\n"
        "allowed-tools:\n"
        f"{allowed_tools}\n"
        f"{runtime_entrypoint_line}"
        "support-files:\n"
        f"  - {workflow_path}\n"
        f"test-command: python -m pytest {test_path} -q\n"
        "---\n\n"
        f"{instructions}\n"
    )
    readme = (
        f"# {title}\n\n"
        "Generated by `vrcforge skill init`. The workflow intentionally contains "
        "one static tool call so package review can reason about its complete effect.\n\n"
        "Without `--writes`, `--tool` must be a runtime-direct read/plan tool. For a write target, "
        "use `--writes`; the generated package is request-only, intentionally has no direct runtime "
        "entrypoint, and keeps that target behind the fixed `vrcforge_request_apply` approval wrapper.\n\n"
        f"Run the source smoke test with `python -m pytest {test_path} -q`.\n"
    )
    smoke = (
        "import json\n"
        "from pathlib import Path\n\n\n"
        "def test_skill_workflow_has_one_gated_tool_call() -> None:\n"
        "    root = Path(__file__).resolve().parents[1]\n"
        "    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))\n"
        "    workflow_path = manifest['entrypoints']['workflow']\n"
        "    workflow = json.loads((root / workflow_path).read_text(encoding='utf-8'))\n"
        f"    assert manifest['id'] == {package_id!r}\n"
        f"    assert workflow['schema'] == {SKILL_WORKFLOW_SCHEMA!r}\n"
        "    assert len(workflow['steps']) == 1\n"
        f"    assert workflow['steps'][0] == {step!r}\n"
        f"    assert workflow['approval']['required'] is {writes!r}\n"
        f"    assert workflow['checkpoint']['required'] is {writes!r}\n"
        f"    assert workflow['checkpoint']['scope'] == {workflow['checkpoint']['scope']!r}\n"
        f"    assert workflow['rollback']['required'] is {writes!r}\n"
        f"    skill_markdown = (root / 'SKILL.md').read_text(encoding='utf-8')\n"
        "    entrypoint_lines = [line for line in skill_markdown.splitlines() if line.startswith('entrypoint-tool:')]\n"
        f"    assert entrypoint_lines == {([f'entrypoint-tool: {runtime_entrypoint_tool}'] if runtime_entrypoint_tool else [])!r}\n"
        f"    assert '\\n  - {workflow_path}\\n' in skill_markdown\n"
    )
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        workflow_path: json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "SKILL.md": skill_markdown,
        "README.md": readme,
        test_path: smoke,
    }


def _validate_skill_skeleton_contract(
    files: dict[str, str],
    manifest: dict[str, Any],
    *,
    writes: bool,
    declared_tool: str,
) -> None:
    workflow_path = str((manifest.get("entrypoints") or {}).get("workflow") or "")
    try:
        workflow = json.loads(files[workflow_path])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CliError("Generated skill workflow is missing or invalid.") from exc
    steps = workflow.get("steps")
    if not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], dict):
        raise CliError("Generated skill workflow must contain exactly one static tool call.")
    step = steps[0]
    expected_workflow_tool = SKILL_WRITE_ENTRYPOINT if writes else declared_tool
    expected_runtime_entrypoint = "" if writes else declared_tool
    if step.get("tool") != expected_workflow_tool or step.get("writes") is not writes:
        raise CliError("Generated skill entrypoint does not match its declared permission mode.")
    if writes:
        request = step.get("request")
        target_tool = str(request.get("targetTool") or "").strip() if isinstance(request, dict) else ""
        if target_tool != declared_tool or target_tool in SKILL_WRITE_CONTROL_TOOLS:
            raise CliError("Generated write skill must preserve one explicit non-wrapper target tool.")
    elif "request" in step:
        raise CliError("Generated read-only skill cannot contain a write request target.")
    expected_gate = bool(writes)
    for key in ("approval", "checkpoint", "rollback"):
        gate = workflow.get(key)
        if not isinstance(gate, dict) or gate.get("required") is not expected_gate:
            raise CliError(f"Generated skill {key} contract does not match its permission mode.")
    expected_scope = ["Assets", "Packages", "ProjectSettings"] if writes else []
    if workflow["checkpoint"].get("scope") != expected_scope:
        raise CliError("Generated skill checkpoint scope is invalid.")
    skill_markdown = files.get("SKILL.md", "")
    entrypoint_lines = [line for line in skill_markdown.splitlines() if line.startswith("entrypoint-tool:")]
    expected_entrypoint_lines = (
        [f"entrypoint-tool: {expected_runtime_entrypoint}"] if expected_runtime_entrypoint else []
    )
    if entrypoint_lines != expected_entrypoint_lines:
        raise CliError(
            "Generated SKILL.md must use a direct entrypoint only for read/plan skills; "
            "write skills must remain request-only."
        )


def _skill_scaffold_path_is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(os.path, "isjunction", None)
    if callable(is_junction):
        try:
            if is_junction(path):
                return True
        except OSError:
            return True
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except (FileNotFoundError, OSError):
        return False
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def _skill_scaffold_existing_entries(destination: Path) -> list[str]:
    if not destination.exists():
        return []
    return sorted(entry.name for entry in destination.iterdir())


def _prepare_staged_file(root: Path, relative: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise CliError("Generated skill paths must stay inside the skill source tree.")
    parent = root
    for part in relative_path.parts[:-1]:
        parent = parent / part
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            if parent.is_dir() and not parent.is_symlink():
                shutil.rmtree(parent)
            else:
                parent.unlink()
        parent.mkdir(exist_ok=True)
    target = root / relative_path
    if target.is_symlink() or target.exists():
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    return target


def _write_skill_tree_atomically(
    destination: Path,
    files: dict[str, str],
    *,
    force: bool,
    validate_staged: Callable[[Path], Any],
) -> None:
    if _skill_scaffold_path_is_link_like(destination) or (
        destination.exists() and not destination.is_dir()
    ):
        raise CliError("Skill skeleton output must be a directory, not a file or symlink.")
    existing_entries = _skill_scaffold_existing_entries(destination)
    if existing_entries and not force:
        raise CliError(
            "Skill skeleton would overwrite existing content: "
            f"{', '.join(existing_entries)}. Use --force to replace the entire directory."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{destination.name}.vrcforge-stage-", dir=destination.parent))
    staged_tree = staging_root / "tree"
    backup_tree = staging_root / "previous"
    moved_existing = False
    preserve_staging = False
    try:
        # A scaffold is a closed source tree. Always build it from an empty
        # staging directory so unrelated local files can never leak into a
        # later package export, including when --force is explicit.
        staged_tree.mkdir()
        for relative, content in files.items():
            target = _prepare_staged_file(staged_tree, relative)
            target.write_text(content, encoding="utf-8", newline="\n")
        validate_staged(staged_tree)

        if destination.exists():
            if _skill_scaffold_path_is_link_like(destination) or not destination.is_dir():
                raise CliError("Skill skeleton output became a file or symlink before publication.")
            existing_entries = _skill_scaffold_existing_entries(destination)
            if existing_entries and not force:
                raise CliError(
                    "Skill skeleton would overwrite existing content: "
                    f"{', '.join(existing_entries)}. Use --force to replace the entire directory."
                )
            os.replace(destination, backup_tree)
            moved_existing = True
        try:
            os.replace(staged_tree, destination)
        except OSError as swap_error:
            if moved_existing and backup_tree.exists() and not destination.exists():
                try:
                    os.replace(backup_tree, destination)
                    moved_existing = False
                except OSError as restore_error:
                    preserve_staging = True
                    raise CliError(
                        "Could not publish the staged skill tree or restore the previous tree; "
                        f"the previous tree remains at {backup_tree}: {restore_error}"
                    ) from swap_error
            raise
    finally:
        # Once the staged tree has been swapped in, this removes the old tree;
        # before the swap it removes all partial output. The destination itself
        # is never populated file-by-file.
        if not preserve_staging:
            shutil.rmtree(staging_root, ignore_errors=True)


def command_skill_init(client: VRCForgeClient, args: argparse.Namespace) -> dict[str, Any]:
    del client
    # Keep the final path unresolved so the atomic writer can reject a
    # symlinked output directory instead of silently following it.
    destination = Path(args.output).expanduser().absolute()
    files = _skill_skeleton_files(args)
    try:
        from skill_packages import SkillPackageService

        service = SkillPackageService(destination.parent / ".vrcforge-cli-validation")
        manifest = json.loads(files["manifest.json"])
        service.validate_manifest(manifest)
        declared_tool = str(args.tool or "").strip()
        _validate_skill_skeleton_contract(
            files,
            manifest,
            writes=bool(args.writes),
            declared_tool=declared_tool,
        )

        def validate_staged(root: Path) -> None:
            staged_manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            workflow_path = str((staged_manifest.get("entrypoints") or {}).get("workflow") or "")
            staged_contract_files = {
                "SKILL.md": (root / "SKILL.md").read_text(encoding="utf-8"),
                workflow_path: (root / workflow_path).read_text(encoding="utf-8"),
            }
            service.validate_manifest(staged_manifest, package_root=root)
            _validate_skill_skeleton_contract(
                staged_contract_files,
                staged_manifest,
                writes=bool(args.writes),
                declared_tool=declared_tool,
            )

        _write_skill_tree_atomically(
            destination,
            files,
            force=bool(args.force),
            validate_staged=validate_staged,
        )
    except (OSError, ValueError) as exc:
        raise CliError(f"Could not create a valid skill skeleton: {exc}") from exc
    return {
        "ok": True,
        "schema": "vrcforge.skill-sdk-skeleton.v1",
        "path": str(destination),
        "packageId": manifest["id"],
        "skillName": manifest["skill_name"],
        "files": sorted(files),
        "toolCallCount": 1,
        "entrypointTool": None if args.writes else str(args.tool or "").strip(),
        "workflowTool": SKILL_WRITE_ENTRYPOINT if args.writes else str(args.tool or "").strip(),
        "requestOnly": bool(args.writes),
        "targetTool": str(args.tool or "").strip() if args.writes else None,
    }


def command_skill_lock_validate(client: VRCForgeClient, args: argparse.Namespace) -> dict[str, Any]:
    del client
    package_path = Path(args.package).expanduser().resolve()
    try:
        from skill_packages import SkillPackageError, SkillPackageService

        preview = SkillPackageService(
            package_path.parent / ".vrcforge-cli-validation",
            vrcforge_version=DEFAULT_SKILL_MIN_VRCFORGE_VERSION,
        ).inspect_package(package_path)
    except (OSError, SkillPackageError, ValueError) as exc:
        raise CliError(f"Skill package lock validation failed: {exc}") from exc
    return {
        "ok": True,
        "schema": SKILL_LOCK_VALIDATION_SCHEMA,
        "packagePath": str(package_path),
        "packageId": preview.manifest["id"],
        "version": preview.manifest["version"],
        "packageSha256": preview.package_sha256,
        "lockSha256": preview.lock_sha256,
        "signatureStatus": preview.signature_status,
        "signerFingerprint": preview.signer_fingerprint,
        "fileCount": preview.file_count,
    }


def command_tool_registry(client: VRCForgeClient, args: argparse.Namespace) -> dict[str, Any]:
    return client.request("GET", "/api/app/tools/registry")


def command_plan_outfit(client: VRCForgeClient, args: argparse.Namespace) -> dict[str, Any]:
    preview = client.request(
        "POST",
        "/api/app/outfit-imports/plan",
        {
            "packagePath": args.package,
            "projectPath": args.project or "",
            "targetFolder": args.target_folder or "",
            "selectedUnityPackage": args.selected_unity_package or "",
            "selectedPrefab": args.selected_prefab or "",
            "baseAvatarName": args.base_avatar_name or "",
            "maxEntries": args.max_entries,
        },
    )
    document = build_outfit_plan_document(args, preview)
    if args.out:
        out_path = Path(args.out)
        if args.project and path_is_under(out_path, Path(args.project)) and not args.allow_project_plan_output:
            raise CliError(
                "Refusing to write a CLI plan file inside the selected Unity project. "
                "Choose an output path outside the project, or pass --allow-project-plan-output for a local artifact."
            )
        out_path.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        document["savedTo"] = str(out_path)
    return document


def command_apply_request(client: VRCForgeClient, args: argparse.Namespace, stdin: TextIO, stdout: TextIO) -> dict[str, Any]:
    plan = load_plan(args.request)
    if plan.get("kind") != "outfit_import":
        raise CliError(f"Unsupported plan kind for apply: {plan.get('kind')!r}")
    if not plan.get("readyToApply"):
        raise CliError("Plan is not ready to apply. Re-run planning and resolve warnings first.")
    if not args.yes and not confirm("This will create a VRCForge approval request for the desktop/apply path.", "REQUEST", stdin, stdout):
        raise CliError("Request cancelled.")
    payload = client.request("POST", "/api/app/outfit-imports/request", plan.get("request") or {})
    execution = maybe_execute_approval(client, payload.get("approval"), args, stdin, stdout)
    if execution is not None:
        payload["execution"] = execution
        project = (plan.get("request") or {}).get("projectPath") or ""
        payload["validationAfterApply"] = client.request("POST", "/api/app/validation/report", {"projectPath": project})
    return payload


def command_rollback_request(client: VRCForgeClient, args: argparse.Namespace, stdin: TextIO, stdout: TextIO) -> dict[str, Any]:
    preview = client.request("POST", f"/api/app/checkpoints/{urllib.parse.quote(args.checkpoint_id)}/preview")
    if not args.yes and not confirm("This will create a VRCForge approval request to restore the checkpoint.", "REQUEST", stdin, stdout):
        raise CliError("Rollback request cancelled.")
    payload = client.request("POST", f"/api/app/checkpoints/{urllib.parse.quote(args.checkpoint_id)}/restore")
    payload["preview"] = preview
    execution = maybe_execute_approval(client, payload.get("approval"), args, stdin, stdout)
    if execution is not None:
        payload["execution"] = execution
        project = args.project or checkpoint_project_root(preview)
        payload["validationAfterRollback"] = client.request("POST", "/api/app/validation/report", {"projectPath": project})
    return payload


def command_optimization_plan(client: VRCForgeClient, args: argparse.Namespace) -> dict[str, Any]:
    return client.request(
        "POST",
        "/api/app/optimization/plan",
        {
            "avatarPath": args.avatar or "",
            "projectPath": args.project or "",
            "targetProfile": args.target_profile,
            "includeQuest": not args.no_quest,
        },
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vrcforge", description="VRCForge local CLI.")
    parser.add_argument("--endpoint", default=os.environ.get("VRCFORGE_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--token", default="")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", action="store_true", help="Print full JSON output.")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.set_defaults(handler=command_doctor)

    provider = sub.add_parser("provider")
    provider_sub = provider.add_subparsers(dest="provider_command", required=True)
    provider_test = provider_sub.add_parser("test")
    provider_test.add_argument("--provider", required=True)
    provider_test.add_argument("--model", default="")
    provider_test.add_argument("--api-key", default="")
    provider_test.add_argument("--base-url", default="")
    provider_test.add_argument("--capability", choices=["text", "structured", "vision"], default="text")
    provider_test.set_defaults(handler=command_provider_test)

    unity = sub.add_parser("unity")
    unity_sub = unity.add_subparsers(dest="unity_command", required=True)
    unity_status = unity_sub.add_parser("status")
    unity_status.set_defaults(handler=command_unity_status)

    project = sub.add_parser("project")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    project_scan = project_sub.add_parser("scan")
    project_scan.add_argument("--project", required=True)
    project_scan.add_argument("--max-files", type=int, default=20000)
    project_scan.set_defaults(handler=command_project_scan)

    avatar = sub.add_parser("avatar")
    avatar_sub = avatar.add_subparsers(dest="avatar_command", required=True)
    avatar_scan = avatar_sub.add_parser("scan")
    avatar_scan.add_argument("--avatar", default="")
    avatar_scan.set_defaults(handler=command_avatar_scan)

    validation = sub.add_parser("validation")
    validation_sub = validation.add_subparsers(dest="validation_command", required=True)
    validation_run = validation_sub.add_parser("run")
    validation_run.add_argument("--project", default="")
    validation_run.add_argument("--avatar", default="")
    validation_run.set_defaults(handler=command_validation_run)

    build_test = sub.add_parser("build-test")
    build_test_sub = build_test.add_subparsers(dest="build_test_command", required=True)
    readiness = build_test_sub.add_parser("readiness")
    readiness.add_argument("--project", default="")
    readiness.add_argument("--avatar", default="")
    readiness.set_defaults(handler=command_build_test_readiness)

    checkpoint = sub.add_parser("checkpoint")
    checkpoint_sub = checkpoint.add_subparsers(dest="checkpoint_command", required=True)
    checkpoint_list = checkpoint_sub.add_parser("list")
    checkpoint_list.add_argument("--project", default="")
    checkpoint_list.add_argument("--limit", type=int, default=50)
    checkpoint_list.set_defaults(handler=command_checkpoint_list)
    checkpoint_preview = checkpoint_sub.add_parser("preview")
    checkpoint_preview.add_argument("checkpoint_id")
    checkpoint_preview.set_defaults(handler=command_checkpoint_preview)

    skill = sub.add_parser("skill")
    skill_sub = skill.add_subparsers(dest="skill_command", required=True)
    skill_list = skill_sub.add_parser("list")
    skill_list.set_defaults(handler=command_skill_list)
    skill_init = skill_sub.add_parser("init", aliases=["scaffold"], help="Generate a reviewable one-tool skill source tree.")
    skill_init.add_argument("output")
    skill_init.add_argument("--id", dest="package_id", required=True)
    skill_init.add_argument("--name", default="")
    skill_init.add_argument("--title", default="")
    skill_init.add_argument("--description", default="")
    skill_init.add_argument("--version", default="1.0.0")
    skill_init.add_argument("--author", default="VRCForge Skill Author")
    skill_init.add_argument(
        "--min-vrcforge-version",
        default=DEFAULT_SKILL_MIN_VRCFORGE_VERSION,
    )
    skill_init.add_argument(
        "--tool",
        required=True,
        help="Runtime-direct read/plan tool, or the explicit write target when --writes is set.",
    )
    skill_init.add_argument("--permission", action="append", default=[])
    skill_init.add_argument(
        "--writes",
        action="store_true",
        help="Generate a request_apply wrapper around --tool with approval/checkpoint/rollback gates.",
    )
    skill_init.add_argument("--force", action="store_true")
    skill_init.set_defaults(handler=command_skill_init)
    skill_lock_validate = skill_sub.add_parser("lock-validate", help="Validate a .vsk lock, hashes, and signature metadata.")
    skill_lock_validate.add_argument("package")
    skill_lock_validate.set_defaults(handler=command_skill_lock_validate)

    tool = sub.add_parser("tool")
    tool_sub = tool.add_subparsers(dest="tool_command", required=True)
    tool_registry = tool_sub.add_parser("registry")
    tool_registry.set_defaults(handler=command_tool_registry)

    plan = sub.add_parser("plan")
    plan_sub = plan.add_subparsers(dest="plan_command", required=True)
    plan_outfit = plan_sub.add_parser("outfit")
    plan_outfit.add_argument("package")
    plan_outfit.add_argument("--project", default="")
    plan_outfit.add_argument("--target-folder", default="")
    plan_outfit.add_argument("--selected-unity-package", default="")
    plan_outfit.add_argument("--selected-prefab", default="")
    plan_outfit.add_argument("--base-avatar-name", default="")
    plan_outfit.add_argument("--max-entries", type=int, default=20000)
    plan_outfit.add_argument("--out", default="")
    plan_outfit.add_argument(
        "--allow-project-plan-output",
        action="store_true",
        help="Allow writing the local CLI plan JSON inside the selected Unity project. This file is not checkpointed.",
    )
    plan_outfit.set_defaults(handler=command_plan_outfit)

    apply = sub.add_parser("apply")
    apply.add_argument("--request", required=True)
    apply.add_argument("--execute", action="store_true", help="Approve and execute the queued request after explicit confirmation.")
    apply.add_argument("--yes", action="store_true", help="Skip terminal confirmation prompts.")
    apply.set_defaults(handler=command_apply_request)

    rollback = sub.add_parser("rollback")
    rollback.add_argument("--request", dest="checkpoint_id", required=True)
    rollback.add_argument("--project", default="")
    rollback.add_argument("--execute", action="store_true", help="Approve and execute the rollback request after explicit confirmation.")
    rollback.add_argument("--yes", action="store_true", help="Skip terminal confirmation prompts.")
    rollback.set_defaults(handler=command_rollback_request)

    optimization = sub.add_parser("optimization")
    optimization_sub = optimization.add_subparsers(dest="optimization_command", required=True)
    optimization_plan = optimization_sub.add_parser("plan")
    optimization_plan.add_argument("--project", default="")
    optimization_plan.add_argument("--avatar", default="")
    optimization_plan.add_argument("--target-profile", default="pc_conservative")
    optimization_plan.add_argument("--no-quest", action="store_true")
    optimization_plan.set_defaults(handler=command_optimization_plan)

    return parser


def run(argv: list[str] | None = None, client: VRCForgeClient | None = None, stdout: TextIO | None = None, stdin: TextIO | None = None) -> int:
    stdout = stdout or sys.stdout
    stdin = stdin or sys.stdin
    args = build_parser().parse_args(argv)
    client = client or VRCForgeClient(args.endpoint, args.token or default_token(), args.timeout)
    handler = getattr(args, "handler")
    if handler in {command_apply_request, command_rollback_request}:
        payload = handler(client, args, stdin, stdout)
    else:
        payload = handler(client, args)
    if args.json:
        write_json(payload, stdout)
    else:
        write_summary(payload, stdout)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
