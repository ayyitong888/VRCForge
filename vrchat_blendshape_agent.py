from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field, ValidationError


DEFAULT_SETTINGS_PATH = Path(".gemini/settings.json")
DEFAULT_MIN_CONFIDENCE = 0.65
DEFAULT_MVP_EXPORT_PATH = Path("examples/mvp_blendshapes_export.json")
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


class BlendshapeAdjustment(BaseModel):
    avatar_path: str = Field(description="Full avatar transform path from the Unity export.")
    renderer_path: str = Field(description="Full renderer transform path from the Unity export.")
    blendshape_name: str = Field(description="Blendshape name selected from the Unity export.")
    target_weight: float = Field(ge=0.0, le=100.0, description="Target blendshape weight in Unity's 0-100 range.")
    reason: str = Field(description="Short human-readable explanation for why this blendshape was chosen.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence that this semantic match is correct.")


class BlendshapePlan(BaseModel):
    summary: str = Field(description="One sentence summary of the expression change.")
    warnings: list[str] = Field(default_factory=list, description="Potential risks or ambiguous matches.")
    adjustments: list[BlendshapeAdjustment] = Field(default_factory=list, description="Blendshape edits to apply.")


@dataclass(frozen=True)
class SelectedAvatar:
    avatar_name: str
    avatar_path: str
    scene_name: str
    renderer_count: int
    blendshape_count: int


@dataclass
class Settings:
    gemini_api_key: str
    gemini_model: str
    gemini_thinking_level: str
    unity_mcp_command: list[str]
    unity_mcp_retries: int
    unity_mcp_retry_backoff_seconds: float
    unity_mcp_timeout_seconds: int
    export_tool_name: str
    execute_tool_name: str
    export_path: Path
    min_confidence: float


@dataclass
class McpResult:
    exit_code: int
    stdout: str
    stderr: str
    payload: Any | None


class UnityMcpError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Use Gemini and Unity MCP to tune VRChat avatar blendshapes from natural language."
    )
    parser.add_argument(
        "instruction",
        nargs="?",
        help='Natural language expression tweak, e.g. "Open the eyes wider and raise the mouth corners".',
    )
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS_PATH, help="Path to settings.json")
    parser.add_argument(
        "--model",
        help="Optional Gemini model override, e.g. gemini-2.5-flash or gemini-3.1-pro-preview.",
    )
    parser.add_argument(
        "--mvp",
        action="store_true",
        help="Run the MVP flow using a local export JSON and mock Unity execution.",
    )
    parser.add_argument(
        "--export-json",
        type=Path,
        help="Read blendshape export data from a local JSON file instead of calling Unity export.",
    )
    parser.add_argument(
        "--skip-export",
        action="store_true",
        help="Skip Unity export and read the configured export JSON path from disk.",
    )
    parser.add_argument(
        "--mock-execute",
        action="store_true",
        help="Skip Unity execution and return a mock success result after generating C#.",
    )
    parser.add_argument(
        "--plan-json",
        type=Path,
        help="Optional local plan JSON file. If provided, Gemini generation is skipped and the plan is validated locally.",
    )
    parser.add_argument(
        "--avatar",
        help="Exact or partial avatar path/name from the export. Required when multiple avatars are present.",
    )
    parser.add_argument(
        "--list-avatars",
        action="store_true",
        help="Export the current scene and print the available avatar paths without running Gemini.",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help="Reject Gemini adjustments below this confidence unless --allow-low-confidence is used.",
    )
    parser.add_argument(
        "--allow-low-confidence",
        action="store_true",
        help="Allow execution even when some adjustments fall below the confidence threshold.",
    )
    parser.add_argument(
        "--save-plan",
        type=Path,
        help="Optional path to save the validated adjustment plan JSON after local checks pass.",
    )
    parser.add_argument(
        "--save-csharp",
        type=Path,
        help="Optional path to save the generated Roslyn C# snippet.",
    )
    parser.add_argument(
        "--save-result",
        type=Path,
        help="Optional path to save the execution result JSON, including mock execution output in MVP mode.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Generate the plan and C# snippet without sending it to Unity.")
    parser.add_argument("--print-plan", action="store_true", help="Print the full validated JSON plan.")
    args = parser.parse_args()

    if not args.instruction and not args.list_avatars and not args.plan_json:
        parser.error("instruction is required unless --list-avatars or --plan-json is used")

    try:
        settings = load_settings(args.settings, gemini_model_override=args.model)
        export_payload, export_source, using_mock_execute = load_export_payload(
            settings=settings,
            export_json_path=args.export_json,
            skip_export=args.skip_export,
            mvp_mode=args.mvp,
            mock_execute=args.mock_execute,
        )

        if args.list_avatars:
            print(render_avatar_list(export_payload))
            return 0

        selected_avatar = resolve_avatar_selection(export_payload, args.avatar)
        planning_payload = build_planning_payload(export_payload, selected_avatar)
        plan = read_plan_json(args.plan_json) if args.plan_json else create_blendshape_plan(settings, planning_payload, args.instruction)
        plan = validate_plan(
            plan=plan,
            export_payload=planning_payload,
            selected_avatar=selected_avatar,
            min_confidence=args.min_confidence if args.min_confidence is not None else settings.min_confidence,
            allow_low_confidence=args.allow_low_confidence,
        )

        if args.save_plan:
            save_plan(args.save_plan, plan)

        if args.print_plan:
            print(json.dumps(plan.model_dump(), indent=2, ensure_ascii=False))

        print(render_preview(selected_avatar, plan, export_source, using_mock_execute))

        code = render_csharp(plan)
        if args.save_csharp:
            save_text(args.save_csharp, code)

        if args.dry_run:
            print(code)
            return 0

        if using_mock_execute:
            result = mock_execute_csharp(code, selected_avatar, export_source)
        else:
            result = execute_csharp(settings, code, [selected_avatar.avatar_path])

        if args.save_result:
            save_result(args.save_result, result)

        print(render_summary(selected_avatar, plan, result, using_mock_execute))
        return 0
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return 1


def load_settings(settings_path: Path, gemini_model_override: str | None = None) -> Settings:
    if not settings_path.exists():
        raise SystemExit(
            f"Missing settings file: {settings_path}\n"
            "Create it from the provided template, set GEMINI_API_KEY in your environment, and try again."
        )

    raw_settings = json.loads(settings_path.read_text(encoding="utf-8"))
    gemini_settings = raw_settings.get("gemini", {})
    mcp_settings = raw_settings.get("unity_mcp", {})
    path_settings = raw_settings.get("paths", {})
    planning_settings = raw_settings.get("planning", {})

    api_key_env = gemini_settings.get("api_key_env", "GEMINI_API_KEY")
    gemini_api_key = os.environ.get(api_key_env, "").strip()
    command = mcp_settings.get("command", ["unity-mcp"])
    if isinstance(command, str):
        command = [command]

    export_path = Path(path_settings.get("blendshape_export", "Assets/VRCAutoRig/blendshapes_export.json"))

    return Settings(
        gemini_api_key=gemini_api_key,
        gemini_model=(gemini_model_override or gemini_settings.get("model", DEFAULT_GEMINI_MODEL)).strip(),
        gemini_thinking_level=gemini_settings.get("thinking_level", "low"),
        unity_mcp_command=command,
        unity_mcp_retries=int(mcp_settings.get("retries", 3)),
        unity_mcp_retry_backoff_seconds=float(mcp_settings.get("retry_backoff_seconds", 2.0)),
        unity_mcp_timeout_seconds=int(mcp_settings.get("timeout_seconds", 30)),
        export_tool_name=mcp_settings.get("export_tool_name", "vrc_export_blendshapes"),
        execute_tool_name=mcp_settings.get("execute_tool_name", "vrc_execute_roslyn"),
        export_path=export_path,
        min_confidence=float(planning_settings.get("min_confidence", DEFAULT_MIN_CONFIDENCE)),
    )


def export_blendshapes(settings: Settings) -> dict[str, Any]:
    export_params = {"outputPath": settings.export_path.as_posix(), "refreshAssets": True}
    invoke_unity_mcp(settings, settings.export_tool_name, export_params)

    if not settings.export_path.exists():
        raise UnityMcpError(
            f"Unity export tool reported success but the export file was not created: {settings.export_path}"
        )

    return json.loads(settings.export_path.read_text(encoding="utf-8"))


def load_export_payload(
    settings: Settings,
    export_json_path: Path | None,
    skip_export: bool,
    mvp_mode: bool,
    mock_execute: bool,
) -> tuple[dict[str, Any], str, bool]:
    resolved_export_json_path = export_json_path
    using_mock_execute = mock_execute or mvp_mode

    if mvp_mode and resolved_export_json_path is None:
        resolved_export_json_path = DEFAULT_MVP_EXPORT_PATH

    if resolved_export_json_path is not None:
        return read_export_json(resolved_export_json_path), str(resolved_export_json_path), using_mock_execute

    if skip_export:
        return read_export_json(settings.export_path), str(settings.export_path), using_mock_execute

    return export_blendshapes(settings), "unity-mcp export", using_mock_execute


def read_export_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"Export JSON file does not exist: {path}")

    return json.loads(path.read_text(encoding="utf-8"))


def render_avatar_list(export_payload: dict[str, Any]) -> str:
    avatars = export_payload.get("avatars") or []
    if not avatars:
        return "No avatars were found in the exported scene."

    lines = ["Available avatars:"]
    for avatar in avatars:
        renderer_count = len(avatar.get("renderers") or [])
        blendshape_count = sum(len(renderer.get("blendshapes") or []) for renderer in avatar.get("renderers") or [])
        lines.append(
            f"- {avatar.get('avatarPath', '<unknown path>')} "
            f"(name={avatar.get('avatarName', '<unknown>')}, scene={avatar.get('sceneName', '<unknown>')}, "
            f"renderers={renderer_count}, blendshapes={blendshape_count})"
        )
    return "\n".join(lines)


def resolve_avatar_selection(export_payload: dict[str, Any], requested_avatar: str | None) -> SelectedAvatar:
    avatars = export_payload.get("avatars") or []
    if not avatars:
        raise RuntimeError("The export JSON did not contain any avatars with blendshapes.")

    if requested_avatar:
        matches = find_avatar_matches(avatars, requested_avatar)
        if len(matches) == 1:
            return to_selected_avatar(matches[0])
        if len(matches) > 1:
            options = "\n".join(f"- {avatar.get('avatarPath', '<unknown path>')}" for avatar in matches)
            raise RuntimeError(
                f"Avatar selector '{requested_avatar}' matched multiple avatars. Be more specific:\n{options}"
            )

        available = "\n".join(f"- {avatar.get('avatarPath', '<unknown path>')}" for avatar in avatars)
        raise RuntimeError(
            f"Avatar selector '{requested_avatar}' did not match any exported avatars.\nAvailable avatars:\n{available}"
        )

    if len(avatars) == 1:
        return to_selected_avatar(avatars[0])

    raise RuntimeError(
        "Multiple avatars were exported from the scene. Re-run with --avatar or --list-avatars to choose one safely."
    )


def find_avatar_matches(avatars: list[dict[str, Any]], requested_avatar: str) -> list[dict[str, Any]]:
    requested = normalize_token(requested_avatar)
    exact_matches = [
        avatar
        for avatar in avatars
        if normalize_token(avatar.get("avatarPath", "")) == requested
        or normalize_token(avatar.get("avatarName", "")) == requested
    ]
    if exact_matches:
        return exact_matches

    return [
        avatar
        for avatar in avatars
        if requested in normalize_token(avatar.get("avatarPath", ""))
        or requested in normalize_token(avatar.get("avatarName", ""))
    ]


def to_selected_avatar(avatar: dict[str, Any]) -> SelectedAvatar:
    renderers = avatar.get("renderers") or []
    blendshape_count = sum(len(renderer.get("blendshapes") or []) for renderer in renderers)
    return SelectedAvatar(
        avatar_name=avatar.get("avatarName", "<unknown>"),
        avatar_path=avatar.get("avatarPath", "<unknown path>"),
        scene_name=avatar.get("sceneName", "<unknown scene>"),
        renderer_count=len(renderers),
        blendshape_count=blendshape_count,
    )


def build_planning_payload(export_payload: dict[str, Any], selected_avatar: SelectedAvatar) -> dict[str, Any]:
    selected_avatar_payload = next(
        avatar
        for avatar in export_payload.get("avatars") or []
        if avatar.get("avatarPath") == selected_avatar.avatar_path
    )
    renderers = selected_avatar_payload.get("renderers") or []
    blendshape_count = sum(len(renderer.get("blendshapes") or []) for renderer in renderers)

    return {
        "generatedAtUtc": export_payload.get("generatedAtUtc"),
        "unityProject": export_payload.get("unityProject"),
        "scenes": [selected_avatar.scene_name],
        "summary": {
            "avatarCount": 1,
            "rendererCount": len(renderers),
            "blendshapeCount": blendshape_count,
        },
        "avatars": [selected_avatar_payload],
    }


def create_blendshape_plan(settings: Settings, export_payload: dict[str, Any], instruction: str) -> BlendshapePlan:
    if not settings.gemini_api_key:
        raise RuntimeError("Gemini API key is empty. Set GEMINI_API_KEY or use --plan-json for a local MVP run.")

    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = build_planner_prompt(export_payload, instruction)

    # Keep the LLM adapter isolated so swapping Gemini for DeepSeek later only changes this block.
    config = build_generate_content_config(settings.gemini_thinking_level)
    response = request_gemini_plan(client, settings.gemini_model, prompt, config)

    raw_json = extract_json_block(response.text or "")
    if not raw_json:
        raise RuntimeError("Gemini returned an empty response while generating the blendshape plan.")

    try:
        return BlendshapePlan.model_validate(json.loads(raw_json))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError(f"Gemini returned invalid blendshape JSON:\n{response.text}") from exc


def format_gemini_client_error(exc: genai_errors.ClientError, model: str) -> str:
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None) or "unknown"
    message = str(exc).strip()

    if status_code == 429:
        return (
            f"Gemini quota was exhausted for model '{model}'.\n"
            "Try a lighter model with --model, enable billing for this project, or retry after the quota window resets.\n"
            f"Original error: {message}"
        )

    if status_code == 400 and "API_KEY_INVALID" in message:
        return (
            f"Gemini rejected the API key while calling model '{model}'.\n"
            "Make sure GEMINI_API_KEY comes from Google AI Studio / Gemini Developer API and has no extra whitespace.\n"
            f"Original error: {message}"
        )

    return f"Gemini request failed for model '{model}' with HTTP {status_code}.\nOriginal error: {message}"


def build_generate_content_config(thinking_level: str) -> types.GenerateContentConfig:
    if thinking_level:
        try:
            return types.GenerateContentConfig(
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_level=thinking_level),
            )
        except TypeError:
            # Older SDK versions may not expose thinking config yet.
            pass

    return types.GenerateContentConfig(response_mime_type="application/json")


def request_gemini_plan(
    client: genai.Client,
    model: str,
    prompt: str,
    config: types.GenerateContentConfig,
) -> Any:
    try:
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
    except genai_errors.ClientError as exc:
        if uses_thinking_config(config) and is_unsupported_thinking_error(exc):
            fallback_config = types.GenerateContentConfig(response_mime_type="application/json")
            return client.models.generate_content(
                model=model,
                contents=prompt,
                config=fallback_config,
            )
        raise RuntimeError(format_gemini_client_error(exc, model)) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Gemini request failed for model '{model}'.\n"
            f"Original error: {exc}"
        ) from exc


def uses_thinking_config(config: types.GenerateContentConfig) -> bool:
    return getattr(config, "thinking_config", None) is not None


def is_unsupported_thinking_error(exc: genai_errors.ClientError) -> bool:
    return "thinking level is not supported for this model" in str(exc).lower()


def read_plan_json(path: Path) -> BlendshapePlan:
    if not path.exists():
        raise RuntimeError(f"Plan JSON file does not exist: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Plan JSON is not valid JSON: {path}") from exc

    try:
        return BlendshapePlan.model_validate(payload)
    except ValidationError as exc:
        raise RuntimeError(f"Plan JSON does not match the expected schema: {path}") from exc


def build_planner_prompt(export_payload: dict[str, Any], instruction: str) -> str:
    schema = {
        "summary": "string",
        "warnings": ["string"],
        "adjustments": [
            {
                "avatar_path": "string",
                "renderer_path": "string",
                "blendshape_name": "string",
                "target_weight": "0-100 float",
                "reason": "string",
                "confidence": "0-1 float",
            }
        ],
    }

    return (
        "You are a VRChat avatar blendshape planning assistant.\n"
        "Task: read the exported blendshape list, semantically match the user's intent, and return a JSON-only plan.\n"
        "Rules:\n"
        "1. Only use avatar_path, renderer_path, and blendshape_name values that already exist in the export JSON.\n"
        "2. target_weight must be a float between 0 and 100.\n"
        "3. Do not output Markdown. Output JSON only.\n"
        "4. If naming is inconsistent, do your best semantic match and explain ambiguities in warnings.\n"
        "5. Prefer a small, high-quality set of edits. Usually 3 to 12 adjustments is enough.\n"
        "6. If there is no safe match, return an empty adjustments array and explain why in warnings.\n\n"
        f"Output JSON shape example: {json.dumps(schema, ensure_ascii=False)}\n\n"
        f"User instruction: {instruction}\n\n"
        f"Exported Unity / VRChat blendshape data:\n{json.dumps(export_payload, ensure_ascii=False, indent=2)}"
    )


def validate_plan(
    plan: BlendshapePlan,
    export_payload: dict[str, Any],
    selected_avatar: SelectedAvatar,
    min_confidence: float,
    allow_low_confidence: bool,
) -> BlendshapePlan:
    allowed_targets = build_allowed_target_set(export_payload)
    warnings = list(plan.warnings)
    invalid_targets: list[str] = []
    low_confidence_adjustments: list[str] = []
    dedupe_index: dict[tuple[str, str, str], int] = {}
    deduped_adjustments: list[BlendshapeAdjustment] = []

    for adjustment in plan.adjustments:
        key = (adjustment.avatar_path, adjustment.renderer_path, adjustment.blendshape_name)

        if key not in allowed_targets:
            invalid_targets.append(
                f"- avatar={adjustment.avatar_path}, renderer={adjustment.renderer_path}, "
                f"blendshape={adjustment.blendshape_name}"
            )
            continue

        if adjustment.confidence < min_confidence:
            low_confidence_adjustments.append(
                f"- {adjustment.blendshape_name} on {adjustment.renderer_path}: "
                f"{adjustment.confidence:.2f} < {min_confidence:.2f}"
            )

        if key in dedupe_index:
            warnings.append(
                f"Gemini returned duplicate edits for {adjustment.blendshape_name} on {adjustment.renderer_path}; "
                "the later target weight was kept."
            )
            deduped_adjustments[dedupe_index[key]] = adjustment
            continue

        dedupe_index[key] = len(deduped_adjustments)
        deduped_adjustments.append(adjustment)

    if invalid_targets:
        detail = "\n".join(invalid_targets)
        raise RuntimeError(
            "Gemini returned blendshape targets that do not exist in the selected avatar export.\n"
            f"Selected avatar: {selected_avatar.avatar_path}\n{detail}"
        )

    if not deduped_adjustments:
        warning_text = "; ".join(warnings) if warnings else "Gemini did not find a safe match."
        raise RuntimeError(f"No blendshape adjustments were generated. {warning_text}")

    if low_confidence_adjustments and not allow_low_confidence:
        detail = "\n".join(low_confidence_adjustments)
        raise RuntimeError(
            "Gemini returned low-confidence adjustments. Re-run with a more specific prompt, lower "
            "--min-confidence, or use --allow-low-confidence if you want to accept the risk.\n"
            f"{detail}"
        )

    if low_confidence_adjustments:
        warnings.append("Low-confidence adjustments were allowed by CLI override.")

    return BlendshapePlan(summary=plan.summary, warnings=warnings, adjustments=deduped_adjustments)


def build_allowed_target_set(export_payload: dict[str, Any]) -> set[tuple[str, str, str]]:
    allowed: set[tuple[str, str, str]] = set()
    for avatar in export_payload.get("avatars") or []:
        avatar_path = avatar.get("avatarPath", "")
        for renderer in avatar.get("renderers") or []:
            renderer_path = renderer.get("rendererPath", "")
            for blendshape in renderer.get("blendshapes") or []:
                allowed.add((avatar_path, renderer_path, blendshape.get("name", "")))
    return allowed


def save_plan(output_path: Path, plan: BlendshapePlan) -> None:
    save_json(output_path, plan.model_dump())


def save_result(output_path: Path, result: McpResult) -> None:
    save_json(
        output_path,
        {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "payload": result.payload,
        },
    )


def save_json(output_path: Path, payload: Any) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def save_text(output_path: Path, text: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")


def render_preview(
    selected_avatar: SelectedAvatar,
    plan: BlendshapePlan,
    export_source: str,
    using_mock_execute: bool,
) -> str:
    lines = [
        f"Export source: {export_source}",
        f"Execution mode: {'mock' if using_mock_execute else 'live-unity'}",
        f"Target avatar: {selected_avatar.avatar_path}",
        f"Scene: {selected_avatar.scene_name}",
        f"Available renderers: {selected_avatar.renderer_count}",
        f"Available blendshapes: {selected_avatar.blendshape_count}",
        f"Plan summary: {plan.summary}",
        f"Planned adjustments: {len(plan.adjustments)}",
    ]

    for adjustment in plan.adjustments:
        lines.append(
            f"- {adjustment.renderer_path} :: {adjustment.blendshape_name} -> {adjustment.target_weight:.2f} "
            f"(confidence={adjustment.confidence:.2f})"
        )

    if plan.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in plan.warnings)

    return "\n".join(lines)


def render_csharp(plan: BlendshapePlan) -> str:
    lines = [
        "// Generated by vrchat_blendshape_agent.py",
        f"RoslynExecutor.Log({to_csharp_string(plan.summary)});",
    ]

    for adjustment in plan.adjustments:
        lines.append(f"// {adjustment.reason} (confidence={adjustment.confidence:.2f})")
        lines.append(
            "RoslynExecutor.SetBlendshapeWeight("
            f"{to_csharp_string(adjustment.avatar_path)}, "
            f"{to_csharp_string(adjustment.renderer_path)}, "
            f"{to_csharp_string(adjustment.blendshape_name)}, "
            f"{adjustment.target_weight:.2f}f);"
        )

    lines.append("RoslynExecutor.SaveProjectAssets();")
    return "\n".join(lines)


def execute_csharp(settings: Settings, code: str, target_avatar_paths: list[str]) -> McpResult:
    return invoke_unity_mcp(
        settings,
        settings.execute_tool_name,
        {
            "code": code,
            "enforceWriteDefaultsOn": True,
            "targetAvatarPaths": target_avatar_paths,
        },
    )


def mock_execute_csharp(code: str, selected_avatar: SelectedAvatar, export_source: str) -> McpResult:
    payload = {
        "mode": "mock",
        "message": "Skipped Unity execution and returned a mock success result.",
        "avatarPath": selected_avatar.avatar_path,
        "exportSource": export_source,
        "generatedCodeLineCount": len(code.splitlines()),
        "generatedCodePreview": code.splitlines()[:8],
    }
    return McpResult(
        exit_code=0,
        stdout=json.dumps(payload, ensure_ascii=False, indent=2),
        stderr="",
        payload=payload,
    )


def invoke_unity_mcp(settings: Settings, tool_name: str, params: dict[str, Any]) -> McpResult:
    last_error: Exception | None = None

    for attempt in range(1, settings.unity_mcp_retries + 1):
        try:
            command = [
                *settings.unity_mcp_command,
                "editor",
                "custom-tool",
                tool_name,
                "--params",
                json.dumps(params, ensure_ascii=False),
            ]
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=settings.unity_mcp_timeout_seconds,
            )
            payload = try_parse_json(completed.stdout)
            result = McpResult(
                exit_code=completed.returncode,
                stdout=completed.stdout.strip(),
                stderr=completed.stderr.strip(),
                payload=payload,
            )

            payload_error = extract_mcp_error(result.payload)
            if completed.returncode == 0 and not payload_error:
                return result

            error_text = payload_error or result.stderr or result.stdout or f"unity-mcp exited with code {completed.returncode}"
            raise UnityMcpError(error_text)
        except Exception as exc:  # noqa: BLE001 - We want to retry any transport/runtime failure here.
            last_error = exc
            if attempt >= settings.unity_mcp_retries:
                break
            time.sleep(settings.unity_mcp_retry_backoff_seconds * attempt)

    raise UnityMcpError(f"Failed to call unity-mcp tool '{tool_name}' after retries.") from last_error


def extract_mcp_error(payload: Any | None) -> str | None:
    if not isinstance(payload, dict):
        return None

    if payload.get("_mcp_status") == "error":
        return str(payload.get("error") or payload.get("message") or json.dumps(payload, ensure_ascii=False))

    if payload.get("isError") is True or payload.get("success") is False or payload.get("ok") is False:
        return str(payload.get("error") or payload.get("message") or json.dumps(payload, ensure_ascii=False))

    return None


def try_parse_json(text: str) -> Any | None:
    candidate = extract_json_block(text)
    if not candidate:
        return None

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def extract_json_block(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""

    for start in range(len(stripped)):
        if stripped[start] not in "{[":
            continue
        try:
            _, end = json.JSONDecoder().raw_decode(stripped[start:])
            return stripped[start:start + end]
        except json.JSONDecodeError:
            continue

    return ""


def render_summary(
    selected_avatar: SelectedAvatar,
    plan: BlendshapePlan,
    result: McpResult,
    using_mock_execute: bool,
) -> str:
    lines = [
        f"Applied plan to avatar: {selected_avatar.avatar_path}",
        f"Execution mode: {'mock' if using_mock_execute else 'live-unity'}",
        f"Plan summary: {plan.summary}",
        f"Adjusted blendshapes: {len(plan.adjustments)}",
    ]

    if plan.warnings:
        lines.append("Warnings: " + " | ".join(plan.warnings))

    if result.stdout:
        lines.append("Unity MCP output:")
        lines.append(result.stdout)

    return "\n".join(lines)


def normalize_token(value: str) -> str:
    return value.strip().casefold()


def to_csharp_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f"\"{escaped}\""


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
