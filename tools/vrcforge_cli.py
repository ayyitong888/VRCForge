from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, TextIO


DEFAULT_ENDPOINT = "http://127.0.0.1:8757"
PLAN_SCHEMA = "vrcforge.cli_plan.v1"


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
    return client.request("POST", "/api/parameters/optimize", {"avatar_path": args.avatar or None})


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
    optimization_plan.add_argument("--avatar", default="")
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
