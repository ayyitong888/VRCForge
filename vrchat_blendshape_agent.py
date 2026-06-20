from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError


DEFAULT_SETTINGS_PATH = Path(".gemini/settings.json")
DEFAULT_MIN_CONFIDENCE = 0.65
DEFAULT_MVP_EXPORT_PATH = Path("examples/mvp_blendshapes_export.json")
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4.1-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-6"
DEFAULT_OLLAMA_MODEL = "llama3.2"
DEFAULT_VERTEX_AI_MODEL = "gemini-2.5-flash"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_VERTEX_AI_BASE_URL = ""
DEFAULT_CUSTOM_BASE_URL = ""
DEFAULT_LLM_PROVIDER = "gemini"
SUPPORTED_LLM_PROVIDERS = (
    "gemini",
    "deepseek",
    "openai",
    "openrouter",
    "anthropic",
    "ollama",
    "vertexai",
    "custom",
)
PROVIDER_ALIASES = {
    "google": "gemini",
    "google_ai": "gemini",
    "googleai": "gemini",
    "google-ai": "gemini",
    "google_ai_studio": "gemini",
    "google-ai-studio": "gemini",
    "ai_studio": "gemini",
    "aistudio": "gemini",
    "google_vertex": "vertexai",
    "google-vertex": "vertexai",
    "google_vertex_ai": "vertexai",
    "google-vertex-ai": "vertexai",
    "vertex": "vertexai",
    "vertex_ai": "vertexai",
    "vertex-ai": "vertexai",
}


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
    llm_provider: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_api_key_env: str
    gemini_thinking_level: str
    unity_mcp_command: list[str]
    unity_mcp_host: str
    unity_mcp_port: int
    unity_mcp_instance: str
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


@dataclass
class LlmPlanResponse:
    text: str
    reasoning: dict[str, Any]


class UnityMcpError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Use an LLM provider and Unity MCP to tune VRChat avatar blendshapes from natural language."
    )
    parser.add_argument(
        "instruction",
        nargs="?",
        help='Natural language expression tweak, e.g. "Open the eyes wider and raise the mouth corners".',
    )
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS_PATH, help="Path to settings.json")
    parser.add_argument(
        "--model",
        help="Optional provider model override, e.g. gemini-2.5-flash, deepseek-chat, or claude-opus-4-6.",
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
        help="Skip Unity execution and return a mock success result after generating the Unity apply payload.",
    )
    parser.add_argument(
        "--plan-json",
        type=Path,
        help="Optional local plan JSON file. If provided, live LLM generation is skipped and the plan is validated locally.",
    )
    parser.add_argument(
        "--avatar",
        help="Exact or partial avatar path/name from the export. Required when multiple avatars are present.",
    )
    parser.add_argument(
        "--list-avatars",
        action="store_true",
        help="Export the current scene and print the available avatar paths without running the LLM planner.",
    )
    parser.add_argument(
        "--unity-status",
        action="store_true",
        help="Print the current unity-mcp connection status and exit.",
    )
    parser.add_argument(
        "--list-unity-instances",
        action="store_true",
        help="List Unity instances visible to unity-mcp and exit.",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help="Reject low-confidence planner adjustments unless --allow-low-confidence is used.",
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
        "--save-apply-payload",
        type=Path,
        help="Optional path to save the generated Unity apply payload JSON.",
    )
    parser.add_argument(
        "--save-result",
        type=Path,
        help="Optional path to save the execution result JSON, including mock execution output in MVP mode.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Generate the plan and Unity apply payload without sending it to Unity.")
    parser.add_argument("--print-plan", action="store_true", help="Print the full validated JSON plan.")
    args = parser.parse_args()

    if not args.instruction and not args.list_avatars and not args.plan_json and not args.unity_status and not args.list_unity_instances:
        parser.error("instruction is required unless --list-avatars or --plan-json is used")

    try:
        settings = load_settings(args.settings, gemini_model_override=args.model)
        if args.unity_status:
            print(run_unity_mcp_passthrough(settings, ["status"]))
            return 0

        if args.list_unity_instances:
            print(run_unity_mcp_passthrough(settings, ["instances"]))
            return 0

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

        apply_payload = render_apply_payload_json(selected_avatar, plan)
        if args.save_apply_payload:
            save_text(args.save_apply_payload, apply_payload)

        if args.dry_run:
            print(apply_payload)
            return 0

        if using_mock_execute:
            result = mock_execute_payload(apply_payload, selected_avatar, export_source)
        else:
            result = apply_blendshape_plan_direct(settings, selected_avatar, plan)

        if args.save_result:
            save_result(args.save_result, result)

        print(render_summary(selected_avatar, plan, result, using_mock_execute))
        return 0
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return 1


def load_settings(
    settings_path: Path,
    gemini_model_override: str | None = None,
    llm_override: dict[str, Any] | None = None,
) -> Settings:
    if not settings_path.exists():
        raise SystemExit(
            f"Missing settings file: {settings_path}\n"
            "Create it from the provided template, configure your provider API key, and try again."
        )

    raw_settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    mcp_settings = raw_settings.get("unity_mcp", {})
    path_settings = raw_settings.get("paths", {})
    planning_settings = raw_settings.get("planning", {})
    command = mcp_settings.get("command", ["unity-mcp"])
    if isinstance(command, str):
        command = [command]

    export_path = Path(path_settings.get("blendshape_export", "Assets/VRCForge/blendshapes_export.json"))
    llm_settings = build_llm_settings(raw_settings, gemini_model_override, llm_override)

    return Settings(
        llm_provider=llm_settings["provider"],
        llm_api_key=llm_settings["api_key"],
        llm_base_url=llm_settings["base_url"],
        llm_model=llm_settings["model"],
        llm_api_key_env=llm_settings["api_key_env"],
        gemini_thinking_level=llm_settings["thinking_level"],
        unity_mcp_command=command,
        unity_mcp_host=str(mcp_settings.get("host", "127.0.0.1")).strip() or "127.0.0.1",
        unity_mcp_port=int(mcp_settings.get("port", 8080)),
        unity_mcp_instance=str(mcp_settings.get("instance", "")).strip(),
        unity_mcp_retries=int(mcp_settings.get("retries", 3)),
        unity_mcp_retry_backoff_seconds=float(mcp_settings.get("retry_backoff_seconds", 2.0)),
        unity_mcp_timeout_seconds=int(mcp_settings.get("timeout_seconds", 30)),
        export_tool_name=mcp_settings.get("export_tool_name", "vrc_export_blendshapes"),
        execute_tool_name=mcp_settings.get("execute_tool_name", "vrc_apply_blendshapes"),
        export_path=export_path,
        min_confidence=float(planning_settings.get("min_confidence", DEFAULT_MIN_CONFIDENCE)),
    )


def build_llm_settings(
    raw_settings: dict[str, Any],
    model_override: str | None,
    llm_override: dict[str, Any] | None,
) -> dict[str, str]:
    llm_settings = dict(raw_settings.get("llm") or {})
    legacy_gemini_settings = dict(raw_settings.get("gemini") or {})
    override = llm_override or {}

    provider = normalize_provider_name(
        override.get("provider")
        or llm_settings.get("provider")
        or legacy_gemini_settings.get("provider")
        or DEFAULT_LLM_PROVIDER
    )
    defaults = get_provider_defaults(provider)
    api_key_env = str(
        override.get("api_key_env")
        or llm_settings.get("api_key_env")
        or legacy_gemini_settings.get("api_key_env")
        or default_api_key_env_for_provider(provider)
    ).strip()
    api_key = str(
        override.get("api_key")
        or llm_settings.get("api_key")
        or os.environ.get(api_key_env, "")
    ).strip()
    base_url_value = (
        override.get("base_url")
        if "base_url" in override
        else llm_settings.get("base_url", legacy_gemini_settings.get("base_url"))
    )
    model_value = (
        model_override
        or override.get("model")
        or llm_settings.get("model")
        or legacy_gemini_settings.get("model")
        or defaults["model"]
    )
    thinking_level_value = (
        override.get("thinking_level")
        if "thinking_level" in override
        else llm_settings.get("thinking_level", legacy_gemini_settings.get("thinking_level", ""))
    )

    return {
        "provider": provider,
        "api_key_env": api_key_env,
        "api_key": api_key,
        "base_url": normalize_base_url(base_url_value, provider, defaults["base_url"]),
        "model": str(model_value).strip() or defaults["model"],
        "thinking_level": str(thinking_level_value or "").strip(),
    }


def get_provider_defaults(provider: str) -> dict[str, str]:
    normalized = normalize_provider_name(provider)
    defaults = {
        "gemini": {"model": DEFAULT_GEMINI_MODEL, "base_url": DEFAULT_GEMINI_BASE_URL},
        "deepseek": {"model": DEFAULT_DEEPSEEK_MODEL, "base_url": DEFAULT_DEEPSEEK_BASE_URL},
        "openai": {"model": DEFAULT_OPENAI_MODEL, "base_url": DEFAULT_OPENAI_BASE_URL},
        "openrouter": {"model": DEFAULT_OPENROUTER_MODEL, "base_url": DEFAULT_OPENROUTER_BASE_URL},
        "anthropic": {"model": DEFAULT_ANTHROPIC_MODEL, "base_url": ""},
        "ollama": {"model": DEFAULT_OLLAMA_MODEL, "base_url": DEFAULT_OLLAMA_BASE_URL},
        "vertexai": {"model": DEFAULT_VERTEX_AI_MODEL, "base_url": DEFAULT_VERTEX_AI_BASE_URL},
        "custom": {"model": "", "base_url": DEFAULT_CUSTOM_BASE_URL},
    }
    return defaults[normalized]


def default_api_key_env_for_provider(provider: str) -> str:
    return {
        "gemini": "GEMINI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "ollama": "OLLAMA_API_KEY",
        "vertexai": "GOOGLE_APPLICATION_CREDENTIALS",
        "custom": "LLM_API_KEY",
    }[normalize_provider_name(provider)]


def normalize_provider_name(provider: str | None) -> str:
    normalized = str(provider or DEFAULT_LLM_PROVIDER).strip().lower().replace(" ", "_")
    normalized = PROVIDER_ALIASES.get(normalized, normalized)
    if normalized not in SUPPORTED_LLM_PROVIDERS:
        raise RuntimeError(
            f"Unsupported LLM provider '{provider}'. Supported values: {', '.join(SUPPORTED_LLM_PROVIDERS)}."
        )
    return normalized


def normalize_base_url(base_url: Any, provider: str, default_base_url: str) -> str:
    normalized = normalize_provider_name(provider)
    if normalized in {"anthropic", "gemini"}:
        return ""

    resolved = str(base_url if base_url is not None else default_base_url).strip()
    return resolved.rstrip("/")


def provider_requires_api_key(provider: str) -> bool:
    return normalize_provider_name(provider) not in {"ollama", "vertexai"}


def export_blendshapes(settings: Settings) -> dict[str, Any]:
    export_params = {"outputPath": settings.export_path.as_posix(), "refreshAssets": True}
    result = invoke_unity_mcp(settings, settings.export_tool_name, export_params)

    export_path = resolve_export_result_path(settings, result)
    if export_path is None:
        raise UnityMcpError(
            "Unity export tool reported success but the export file was not created. "
            f"Checked: {settings.export_path}"
        )

    return json.loads(export_path.read_text(encoding="utf-8"))


def resolve_export_result_path(settings: Settings, result: McpResult) -> Path | None:
    candidates: list[Path] = []
    payload_path = extract_export_path_from_payload(result.payload)
    stdout_path = extract_unity_mcp_output_field(result.stdout, "absoluteOutputPath")

    if payload_path:
        candidates.append(Path(payload_path))
    if stdout_path:
        candidates.append(Path(stdout_path))
    candidates.append(settings.export_path)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None


def extract_export_path_from_payload(payload: Any | None) -> str | None:
    if not isinstance(payload, dict):
        return None

    candidate: Any = payload
    visited = set()
    while isinstance(candidate, dict):
        marker = id(candidate)
        if marker in visited:
            break
        visited.add(marker)

        for key in ("absoluteOutputPath", "absolute_output_path", "outputPath", "output_path"):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        next_candidate = None
        for key in ("data", "result", "payload", "value"):
            if isinstance(candidate.get(key), dict):
                next_candidate = candidate[key]
                break
        if next_candidate is None:
            break
        candidate = next_candidate

    return None


def extract_unity_mcp_output_field(stdout: str, field_name: str) -> str | None:
    prefix = f"{field_name}:"
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped[len(prefix):].strip()
            return value or None
    return None


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


FACE_BLENDSHAPE_TERMS = (
    "eye",
    "eyelid",
    "blink",
    "pupil",
    "iris",
    "hitomi",
    "brow",
    "eyebrow",
    "mouth",
    "lip",
    "smile",
    "jaw",
    "chin",
    "cheek",
    "face",
    "morph",
    "nose",
    "tongue",
    "tooth",
    "teeth",
    "fang",
    "giza",
    "ear",
    "まばたき",
    "瞳",
    "目",
    "眉",
    "口",
    "唇",
    "顎",
    "頬",
    "顔",
    "鼻",
    "舌",
    "歯",
    "耳",
)

NON_FACE_BLENDSHAPE_TERMS = (
    "breast",
    "bust",
    "chest",
    "waist",
    "hip",
    "hips",
    "butt",
    "leg",
    "arm",
    "hand",
    "foot",
    "feet",
    "body size",
    "shoulder",
    "elbow",
    "knee",
    "thigh",
    "belly",
    "navel",
    "nipple",
    "panty",
    "pants",
    "orifice",
    "genital",
    "shrink",
    "back-",
    "adjust",
    "ribbon",
    "earring",
    "necklace",
    "accessory",
    "hair",
    "ahoge",
    "bang",
    "cloth",
    "clothing",
    "outfit",
    "dress",
    "skirt",
    "sleeve",
    "shoe",
    "sock",
    "tail",
    "wing",
    "胸",
    "腰",
    "尻",
    "肩",
    "膝",
    "腹",
    "臍",
    "脚",
    "腕",
    "手",
    "足",
    "髪",
    "髮",
    "毛",
    "服",
    "衣",
    "裙",
    "靴",
    "尾",
    "調整",
)

FACE_RENDERER_CONTEXT_TERMS = (
    "body",
    "face",
    "head",
    "atama",
    "avatar",
    "facial",
    "顔",
    "頭",
)

NON_FACE_RENDERER_CONTEXT_TERMS = (
    "costume",
    "cloth",
    "clothing",
    "outfit",
    "shirt",
    "shorts",
    "skirt",
    "shoe",
    "socks",
    "hair",
    "ahoge",
    "accessory",
    "bracelet",
    "ribbon",
    "earring",
    "tail",
    "wing",
    "服",
    "衣",
    "裙",
    "靴",
    "髪",
    "髮",
    "毛",
)

VISEME_BLENDSHAPE_NAMES = (
    "aa",
    "ih",
    "ou",
    "ee",
    "oh",
    "ch",
    "dd",
    "e",
    "ff",
    "kk",
    "nn",
    "pp",
    "rr",
    "sil",
    "ss",
    "th",
)

FACIAL_TRACKING_BLENDSHAPE_TERMS = (
    "vrc.",
    "vrc_",
    "viseme",
    "lipsync",
    "lip_sync",
    "face_tracking",
    "facetracking",
    "tracking",
    "arkit",
    "blink",
    "looking_",
    "look_up",
    "look_down",
    "look_left",
    "look_right",
)

ARKIT_TRACKING_COMPACT_NAMES = (
    "eyeblinkleft",
    "eyeblinkright",
    "eyelookdownleft",
    "eyelookdownright",
    "eyelookinleft",
    "eyelookinright",
    "eyelookoutleft",
    "eyelookoutright",
    "eyelookupleft",
    "eyelookupright",
    "jawforward",
    "jawleft",
    "jawright",
    "jawopen",
    "mouthclose",
    "mouthfunnel",
    "mouthpucker",
    "mouthleft",
    "mouthright",
    "mouthsmileleft",
    "mouthsmileright",
    "mouthfrownleft",
    "mouthfrownright",
    "mouthdimpleleft",
    "mouthdimpleright",
    "mouthstretchleft",
    "mouthstretchright",
    "mouthrolllower",
    "mouthrollupper",
    "mouthshruglower",
    "mouthshrugupper",
    "mouthpressleft",
    "mouthpressright",
    "mouthlowerdownleft",
    "mouthlowerdownright",
    "mouthupperupleft",
    "mouthupperupright",
    "browdownleft",
    "browdownright",
    "browinnerup",
    "browouterupleft",
    "browouterupright",
    "cheekpuff",
    "cheeksquintleft",
    "cheeksquintright",
    "nosesneerleft",
    "nosesneerright",
    "tongueout",
)


def filter_planning_payload_to_face_blendshapes(export_payload: dict[str, Any]) -> dict[str, Any]:
    filtered_avatars: list[dict[str, Any]] = []
    renderer_count = 0
    blendshape_count = 0

    for avatar in export_payload.get("avatars") or []:
        filtered_renderers: list[dict[str, Any]] = []
        for renderer in avatar.get("renderers") or []:
            filtered_blendshapes = [
                blendshape
                for blendshape in renderer.get("blendshapes") or []
                if is_face_related_blendshape(renderer, blendshape)
            ]
            if not filtered_blendshapes:
                continue

            renderer_copy = dict(renderer)
            renderer_copy["blendshapes"] = filtered_blendshapes
            renderer_copy["blendshapeCount"] = len(filtered_blendshapes)
            filtered_renderers.append(renderer_copy)
            blendshape_count += len(filtered_blendshapes)

        if filtered_renderers:
            avatar_copy = dict(avatar)
            avatar_copy["renderers"] = filtered_renderers
            filtered_avatars.append(avatar_copy)
            renderer_count += len(filtered_renderers)

    payload = dict(export_payload)
    payload["avatars"] = filtered_avatars
    summary = dict(payload.get("summary") or {})
    summary["avatarCount"] = len(filtered_avatars)
    summary["rendererCount"] = renderer_count
    summary["blendshapeCount"] = blendshape_count
    payload["summary"] = summary
    payload["planningFilter"] = {
        "scope": "face",
        "note": "Only face-related blendshapes are exposed to the natural-language face editor.",
    }
    return payload


def is_face_related_blendshape(renderer: dict[str, Any], blendshape: dict[str, Any]) -> bool:
    name_text = str(blendshape.get("name") or "").lower()
    context_text = " ".join(
        str(value or "")
        for value in (
            renderer.get("rendererName"),
            renderer.get("rendererPath"),
            renderer.get("relativeRendererPath"),
            renderer.get("meshName"),
        )
    ).lower()
    renderer_name = str(renderer.get("rendererName") or "").lower()
    renderer_leaf = context_text.replace("\\", "/").split("/")[-1].strip()

    if is_facial_tracking_blendshape_name(name_text):
        return False

    if any(term in name_text for term in NON_FACE_BLENDSHAPE_TERMS):
        return False

    renderer_is_face_candidate = (
        not any(term in context_text for term in NON_FACE_RENDERER_CONTEXT_TERMS)
        and (
            renderer_name in {"body", "face", "head", "headmesh", "facemesh", "atama"}
            or renderer_leaf in {"body", "face", "head", "headmesh", "facemesh", "atama"}
            or any(term in context_text for term in ("face", "headmesh", "facemesh", "facial", "顔"))
        )
    )

    if renderer_is_face_candidate and any(term in name_text for term in FACE_BLENDSHAPE_TERMS):
        return True

    if any(term in context_text for term in FACE_RENDERER_CONTEXT_TERMS):
        return renderer_is_face_candidate and not any(term in context_text for term in NON_FACE_BLENDSHAPE_TERMS)

    return False


def is_facial_tracking_blendshape_name(name_text: str) -> bool:
    normalized = name_text.replace("\\", "/").replace("-", "_").replace(".", "_")
    compact = "".join(ch for ch in normalized if ch.isalnum())
    tokens = [token for token in normalized.split("_") if token]
    if any(term in normalized for term in FACIAL_TRACKING_BLENDSHAPE_TERMS):
        return True
    if "vrc" in tokens and "v" in tokens:
        return True
    if normalized.startswith("vrc_v_"):
        return True
    return (
        normalized in VISEME_BLENDSHAPE_NAMES
        or normalized.startswith("vrc.v_")
        or compact in ARKIT_TRACKING_COMPACT_NAMES
    )


def create_blendshape_plan(
    settings: Settings,
    export_payload: dict[str, Any],
    instruction: str,
    reference_image_path: str | Path | None = None,
    reference_image_paths: Sequence[str | Path] | None = None,
    reference_image_labels: Sequence[str] | None = None,
) -> BlendshapePlan:
    if provider_requires_api_key(settings.llm_provider) and not settings.llm_api_key:
        provider_name = provider_display_name(settings.llm_provider)
        raise RuntimeError(
            f"{provider_name} API key is empty. Set {settings.llm_api_key_env} or use --plan-json for a local MVP run."
        )

    image_paths = normalize_reference_image_paths(reference_image_path, reference_image_paths)
    prompt = build_planner_prompt(
        export_payload,
        instruction,
        has_reference_image=bool(image_paths),
        reference_image_count=len(image_paths),
        reference_image_labels=reference_image_labels,
    )

    raw_response_text = request_llm_plan(settings, prompt, reference_image_paths=image_paths)
    raw_json = extract_json_block(raw_response_text)
    if not raw_json:
        raise RuntimeError(
            f"{provider_display_name(settings.llm_provider)} returned an empty response while generating the blendshape plan."
        )

    try:
        return filter_plan_by_instruction_relevance(
            BlendshapePlan.model_validate(json.loads(raw_json)),
            instruction,
        )
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError(
            f"{provider_display_name(settings.llm_provider)} returned invalid blendshape JSON:\n{raw_response_text}"
        ) from exc


def create_material_tuning_plan(
    settings: Settings,
    material_inventory: dict[str, Any],
    instruction: str,
    reference_image_path: str | Path | None = None,
    reference_image_paths: Sequence[str | Path] | None = None,
    reference_image_labels: Sequence[str] | None = None,
) -> dict[str, Any]:
    if provider_requires_api_key(settings.llm_provider) and not settings.llm_api_key:
        provider_name = provider_display_name(settings.llm_provider)
        raise RuntimeError(
            f"{provider_name} API key is empty. Save a provider config before generating a shader tuning plan."
        )

    image_paths = normalize_reference_image_paths(reference_image_path, reference_image_paths)
    prompt = build_material_tuning_prompt(
        material_inventory=material_inventory,
        instruction=instruction,
        has_reference_image=bool(image_paths),
        reference_image_count=len(image_paths),
        reference_image_labels=reference_image_labels,
    )
    raw_response_text = request_llm_plan(settings, prompt, reference_image_paths=image_paths)
    raw_json = extract_json_block(raw_response_text)
    if not raw_json:
        raise RuntimeError(
            f"{provider_display_name(settings.llm_provider)} returned an empty response while generating the material tuning plan."
        )

    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{provider_display_name(settings.llm_provider)} returned invalid material tuning JSON:\n{raw_response_text}"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Material tuning response must be a JSON object.")

    payload.setdefault("type", "vision_assisted_material_tuning_plan" if image_paths else "material_tuning_plan")
    payload.setdefault("version", "0.2")
    payload.setdefault("summary", "")
    payload.setdefault("warnings", [])
    payload.setdefault("changes", [])
    return payload


def build_material_tuning_prompt(
    material_inventory: dict[str, Any],
    instruction: str,
    has_reference_image: bool = False,
    reference_image_count: int = 0,
    reference_image_labels: Sequence[str] | None = None,
) -> str:
    schema = {
        "type": "material_tuning_plan",
        "version": "0.2",
        "summary": "Make skin softer and eyes glossier.",
        "visual_analysis": {
            "summary": "Only include in Vision-assisted mode.",
            "detected_issues": ["skin shadow is too harsh"],
        },
        "changes": [
            {
                "material_id": "mat_001",
                "material_name": "Face_Skin",
                "shader_family": "lilToon",
                "category": "skin",
                "semantic_property": "shade_color",
                "before": "#D18A7AFF",
                "after": "#E2A295FF",
                "reason": "Soften skin shadow.",
                "confidence": 0.9,
            }
        ],
        "warnings": ["Only whitelisted semantic material properties will be applied."],
    }

    image_note = ""
    if has_reference_image:
        labels = [str(label) for label in (reference_image_labels or []) if str(label).strip()]
        label_text = "\n".join(f"- Image {index + 1}: {label}" for index, label in enumerate(labels))
        if not label_text:
            label_text = f"- {reference_image_count or 1} reference image(s) attached."
        image_note = (
            f"{reference_image_count or 1} image(s) are attached in this same request.\n"
            f"{label_text}\n"
            "Use source/current images as the current look and target/reference images as the desired style. "
            "Only translate visible material style cues into editable semantic material parameters.\n\n"
        )

    return (
        "You are a VRChat avatar material tuning assistant.\n"
        "Task: use the safe material inventory snapshot and the user's instruction to return a JSON-only material tuning plan.\n"
        "Rules:\n"
        "1. Output JSON only. Do not output Markdown.\n"
        "2. Only target material_id values that exist in the inventory.\n"
        "3. Only target shader_family values lilToon or Poiyomi. Unsupported shaders must not be edited.\n"
        "4. Only use semantic_property values listed inside each material's supported_properties object.\n"
        "5. Do not invent real shader property names such as _Color or _Smoothness. Use semantic properties only.\n"
        "6. Do not propose texture edits, shader replacement, render queue changes, stencil changes, culling changes, blend mode changes, or mesh edits.\n"
        "7. Prefer small conservative changes. Usually 2 to 10 edits is enough.\n"
        "8. For color values, use #RRGGBB or #RRGGBBAA strings. For numeric values, use floats in the supported semantic range.\n"
        "9. If no safe edit is available, return an empty changes array and explain why in warnings.\n\n"
        f"{image_note}"
        f"Output JSON shape example: {json.dumps(schema, ensure_ascii=False)}\n\n"
        f"User instruction, authoritative: {instruction}\n\n"
        f"Safe material inventory snapshot:\n{json.dumps(material_inventory, ensure_ascii=False, indent=2)}"
    )


def create_shader_visual_review(
    settings: Settings,
    goal: str,
    before_image_paths: Sequence[str | Path],
    after_image_paths: Sequence[str | Path],
) -> dict[str, Any]:
    image_paths = [*normalize_reference_image_paths(reference_image_paths=before_image_paths), *normalize_reference_image_paths(reference_image_paths=after_image_paths)]
    if not image_paths:
        raise RuntimeError("Shader visual review requires at least one before or after screenshot.")

    labels = [
        *[f"Before screenshot {index + 1}" for index, _ in enumerate(before_image_paths or [])],
        *[f"After screenshot {index + 1}" for index, _ in enumerate(after_image_paths or [])],
    ]
    prompt = build_shader_visual_review_prompt(goal, len(before_image_paths or []), len(after_image_paths or []), labels)
    raw_response_text = request_llm_plan(settings, prompt, reference_image_paths=image_paths)
    raw_json = extract_json_block(raw_response_text)
    if not raw_json:
        raise RuntimeError(
            f"{provider_display_name(settings.llm_provider)} returned an empty response while reviewing shader tuning."
        )

    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{provider_display_name(settings.llm_provider)} returned invalid shader review JSON:\n{raw_response_text}"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Shader visual review response must be a JSON object.")

    payload.setdefault("type", "shader_visual_review")
    payload.setdefault("version", "0.2")
    payload.setdefault("goal", goal)
    payload.setdefault("improved", False)
    payload.setdefault("remaining_issues", [])
    payload.setdefault("suggested_next_steps", [])
    return payload


def build_shader_visual_review_prompt(goal: str, before_count: int, after_count: int, labels: Sequence[str]) -> str:
    schema = {
        "type": "shader_visual_review",
        "version": "0.2",
        "goal": goal,
        "result_summary": "The skin appears softer and the eyes are slightly brighter.",
        "improved": True,
        "remaining_issues": ["eye highlight could still be stronger"],
        "suggested_next_steps": ["increase eye highlight slightly if desired"],
    }
    label_text = "\n".join(f"- Image {index + 1}: {label}" for index, label in enumerate(labels))
    return (
        "You are reviewing a user-controlled VRChat avatar shader/material tuning result.\n"
        "Compare the before screenshot(s) and after screenshot(s) against the user's goal.\n"
        "Return advisory JSON only. Do not suggest automatic execution and do not output Markdown.\n"
        "Focus on visible material appearance such as skin softness, shadows, shine, eye gloss, hair highlights, and outfit material feel.\n"
        f"Before image count: {before_count}. After image count: {after_count}.\n"
        f"{label_text}\n\n"
        f"Output JSON shape example: {json.dumps(schema, ensure_ascii=False)}\n\n"
        f"User goal: {goal}"
    )


def filter_plan_by_instruction_relevance(plan: BlendshapePlan, instruction: str) -> BlendshapePlan:
    allowed_categories = infer_instruction_categories(instruction)
    if not allowed_categories:
        return plan

    kept: list[BlendshapeAdjustment] = []
    dropped: list[BlendshapeAdjustment] = []
    for adjustment in plan.adjustments:
        adjustment_categories = infer_adjustment_categories(adjustment)
        if adjustment_categories and adjustment_categories.isdisjoint(allowed_categories):
            dropped.append(adjustment)
        else:
            kept.append(adjustment)

    if not dropped:
        return plan

    warnings = list(plan.warnings)
    dropped_names = ", ".join(f"{item.renderer_path}::{item.blendshape_name}" for item in dropped[:8])
    warnings.append(
        "Dropped unrelated planner adjustments before Unity execution because they did not match the user instruction: "
        + dropped_names
    )
    if not kept:
        warnings.append("No instruction-relevant blendshape adjustments remained after safety filtering.")

    return BlendshapePlan(summary=plan.summary, warnings=warnings, adjustments=kept)


def infer_instruction_categories(instruction: str) -> set[str]:
    text = str(instruction or "").lower()
    categories: set[str] = set()

    def has_any(terms: tuple[str, ...]) -> bool:
        return any(term in text for term in terms)

    if has_any(("eye", "眼", "眯", "睁", "瞳", "目", "blink")):
        categories.add("eye")
    if has_any(("brow", "眉")):
        categories.add("brow")
    if has_any(("mouth", "lip", "smile", "嘴", "口", "唇", "笑")):
        categories.add("mouth")
    if has_any(("face", "jaw", "chin", "cheek", "脸", "下巴", "下颚", "脸颊", "圆", "瘦", "捏脸")):
        categories.add("face")
    if has_any(("tooth", "teeth", "fang", "giza", "牙", "齿")):
        categories.add("teeth")
    if has_any(("hair", "ahoge", "头发", "呆毛", "刘海", "发型", "髪", "髮")):
        categories.add("hair")
    if has_any(("breast", "bust", "chest", "body", "胸", "身材", "身体", "体型")):
        categories.add("body")
    if has_any(("cloth", "clothing", "outfit", "衣", "裙", "鞋", "帽", "配饰")):
        categories.add("clothing")
    if has_any(("expression", "表情", "温柔", "可爱", "柔和", "自然")):
        categories.update({"eye", "brow", "mouth", "face"})

    return categories


def infer_adjustment_categories(adjustment: BlendshapeAdjustment) -> set[str]:
    text = " ".join(
        (
            adjustment.renderer_path,
            adjustment.blendshape_name,
            adjustment.reason,
        )
    ).lower()
    categories: set[str] = set()

    def has_any(terms: tuple[str, ...]) -> bool:
        return any(term in text for term in terms)

    if has_any(("eye", "pupil", "iris", "blink", "tare", "tsuri", "まばたき", "瞳", "目")):
        categories.add("eye")
    if has_any(("brow", "eyebrow", "眉")):
        categories.add("brow")
    if has_any(("mouth", "lip", "smile", "口", "唇")):
        categories.add("mouth")
    if has_any(("jaw", "chin", "cheek", "face", "morph", "round", "narrow", "顎")):
        categories.add("face")
    if has_any(("tooth", "teeth", "fang", "giza", "牙", "齿")):
        categories.add("teeth")
        categories.discard("mouth")
    if has_any(("hair", "ahoge", "髪", "髮", "head")):
        categories.add("hair")
    if has_any(("breast", "bust", "chest", "body size", "胸")):
        categories.add("body")
    if has_any(("cloth", "clothing", "outfit", "dress", "skirt", "衣", "服", "裙", "靴", "shoe")):
        categories.add("clothing")

    return categories


def request_llm_plan(
    settings: Settings,
    prompt: str,
    reference_image_path: str | Path | None = None,
    reference_image_paths: Sequence[str | Path] | None = None,
) -> str:
    return request_llm_plan_with_metadata(
        settings,
        prompt,
        reference_image_path=reference_image_path,
        reference_image_paths=reference_image_paths,
    ).text


def request_llm_plan_with_metadata(
    settings: Settings,
    prompt: str,
    reference_image_path: str | Path | None = None,
    reference_image_paths: Sequence[str | Path] | None = None,
) -> LlmPlanResponse:
    image_paths = normalize_reference_image_paths(reference_image_path, reference_image_paths)
    provider = normalize_provider_name(settings.llm_provider)
    if provider == "gemini":
        return request_gemini_plan_with_metadata(settings, prompt, reference_image_paths=image_paths)
    if provider == "vertexai":
        return request_vertex_ai_plan_with_metadata(settings, prompt, reference_image_paths=image_paths)
    if provider == "anthropic":
        return request_anthropic_plan_with_metadata(settings, prompt, reference_image_paths=image_paths)

    return request_openai_compatible_plan_with_metadata(settings, prompt, reference_image_paths=image_paths)


def request_gemini_plan(
    settings: Settings,
    prompt: str,
    reference_image_path: str | Path | None = None,
    reference_image_paths: Sequence[str | Path] | None = None,
) -> str:
    return request_gemini_plan_with_metadata(
        settings,
        prompt,
        reference_image_path=reference_image_path,
        reference_image_paths=reference_image_paths,
    ).text


def request_gemini_plan_with_metadata(
    settings: Settings,
    prompt: str,
    reference_image_path: str | Path | None = None,
    reference_image_paths: Sequence[str | Path] | None = None,
) -> LlmPlanResponse:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "The 'google-genai' package is not installed. Run pip install -r requirements.txt and try again."
        ) from exc

    image_paths = normalize_reference_image_paths(reference_image_path, reference_image_paths)
    contents: Any = prompt
    if image_paths:
        from google.genai import types

        image_parts = [build_google_genai_image_part(types, path) for path in image_paths]
        contents = [prompt, *image_parts]

    client = genai.Client(api_key=settings.llm_api_key)
    try:
        response = client.models.generate_content(
            model=settings.llm_model,
            contents=contents,
        )
        return LlmPlanResponse(
            text=getattr(response, "text", "") or "",
            reasoning=extract_llm_reasoning_trace(response, settings, source="gemini"),
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(format_multimodal_error(exc, settings, bool(image_paths), "Gemini")) from exc


def request_vertex_ai_plan(
    settings: Settings,
    prompt: str,
    reference_image_path: str | Path | None = None,
    reference_image_paths: Sequence[str | Path] | None = None,
) -> str:
    return request_vertex_ai_plan_with_metadata(
        settings,
        prompt,
        reference_image_path=reference_image_path,
        reference_image_paths=reference_image_paths,
    ).text


def request_vertex_ai_plan_with_metadata(
    settings: Settings,
    prompt: str,
    reference_image_path: str | Path | None = None,
    reference_image_paths: Sequence[str | Path] | None = None,
) -> LlmPlanResponse:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "The 'google-genai' package is not installed. Run pip install -r requirements.txt and try again."
        ) from exc

    project, location = resolve_vertex_ai_project_location(settings.llm_base_url)
    image_paths = normalize_reference_image_paths(reference_image_path, reference_image_paths)
    contents: Any = prompt
    if image_paths:
        from google.genai import types

        image_parts = [build_google_genai_image_part(types, path) for path in image_paths]
        contents = [prompt, *image_parts]

    try:
        client = genai.Client(vertexai=True, project=project, location=location)
        response = client.models.generate_content(
            model=settings.llm_model,
            contents=contents,
        )
        return LlmPlanResponse(
            text=getattr(response, "text", "") or "",
            reasoning=extract_llm_reasoning_trace(response, settings, source="vertexai"),
        )
    except Exception as exc:  # noqa: BLE001
        detail = (
            f"Google Vertex AI request failed for model {settings.llm_model} "
            f"in project '{project}' / location '{location}': {exc}"
        )
        if image_paths:
            detail = (
                f"Google Vertex AI model '{settings.llm_model}' does not appear to support image input, "
                f"or the selected Vertex endpoint rejected multimodal content.\nOriginal error: {exc}"
            )
        raise RuntimeError(detail) from exc


def request_openai_compatible_plan(
    settings: Settings,
    prompt: str,
    reference_image_path: str | Path | None = None,
    reference_image_paths: Sequence[str | Path] | None = None,
) -> str:
    return request_openai_compatible_plan_with_metadata(
        settings,
        prompt,
        reference_image_path=reference_image_path,
        reference_image_paths=reference_image_paths,
    ).text


def request_openai_compatible_plan_with_metadata(
    settings: Settings,
    prompt: str,
    reference_image_path: str | Path | None = None,
    reference_image_paths: Sequence[str | Path] | None = None,
) -> LlmPlanResponse:
    if not settings.llm_base_url:
        provider_name = provider_display_name(settings.llm_provider)
        raise RuntimeError(f"{provider_name} requires a Base URL for OpenAI-compatible requests.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "The 'openai' package is not installed. Run pip install -r requirements.txt and try again."
        ) from exc

    image_paths = normalize_reference_image_paths(reference_image_path, reference_image_paths)
    user_content: Any = prompt
    if image_paths:
        user_content = [
            {"type": "text", "text": prompt},
            *[
                {"type": "image_url", "image_url": {"url": image_path_to_data_url(path)}}
                for path in image_paths
            ],
        ]

    client = OpenAI(api_key=settings.llm_api_key or "ollama", base_url=settings.llm_base_url)
    try:
        response = client.chat.completions.create(
            model=settings.llm_model,
            temperature=0.1,
            messages=[
                {
                    "role": "system",
                    "content": "You are a VRChat blendshape planning assistant. Reply with JSON only and no Markdown.",
                },
                {"role": "user", "content": user_content},
            ],
        )
        return LlmPlanResponse(
            text=extract_openai_message_text(response),
            reasoning=extract_llm_reasoning_trace(response, settings, source="openai-compatible"),
        )
    except Exception as exc:  # noqa: BLE001
        if image_paths:
            raise RuntimeError(format_multimodal_error(exc, settings, True, provider_display_name(settings.llm_provider))) from exc
        raise RuntimeError(format_openai_compatible_error(exc, settings)) from exc


def request_anthropic_plan(
    settings: Settings,
    prompt: str,
    reference_image_path: str | Path | None = None,
    reference_image_paths: Sequence[str | Path] | None = None,
) -> str:
    return request_anthropic_plan_with_metadata(
        settings,
        prompt,
        reference_image_path=reference_image_path,
        reference_image_paths=reference_image_paths,
    ).text


def request_anthropic_plan_with_metadata(
    settings: Settings,
    prompt: str,
    reference_image_path: str | Path | None = None,
    reference_image_paths: Sequence[str | Path] | None = None,
) -> LlmPlanResponse:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "The 'anthropic' package is not installed. Run pip install -r requirements.txt and try again."
        ) from exc

    image_paths = normalize_reference_image_paths(reference_image_path, reference_image_paths)
    user_content: Any = prompt
    if image_paths:
        image_blocks = []
        for image_path_value in image_paths:
            image_path = resolve_existing_image_path(image_path_value)
            mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
            image_blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                    },
                }
            )
        user_content = [
            {"type": "text", "text": prompt},
            *image_blocks,
        ]

    client = anthropic.Anthropic(api_key=settings.llm_api_key)
    try:
        response = client.messages.create(
            model=settings.llm_model or DEFAULT_ANTHROPIC_MODEL,
            max_tokens=1400,
            system="You are a VRChat blendshape planning assistant. Reply with JSON only and no Markdown.",
            messages=[{"role": "user", "content": user_content}],
        )
        return LlmPlanResponse(
            text=extract_anthropic_message_text(response),
            reasoning=extract_llm_reasoning_trace(response, settings, source="anthropic"),
        )
    except Exception as exc:  # noqa: BLE001
        if image_paths:
            raise RuntimeError(format_multimodal_error(exc, settings, True, "Anthropic")) from exc
        raise RuntimeError(format_anthropic_error(exc, settings.llm_model or DEFAULT_ANTHROPIC_MODEL)) from exc


def normalize_reference_image_paths(
    reference_image_path: str | Path | None = None,
    reference_image_paths: Sequence[str | Path] | str | Path | None = None,
) -> list[str | Path]:
    candidates: list[str | Path] = []
    if reference_image_path:
        candidates.append(reference_image_path)
    if isinstance(reference_image_paths, (str, Path)):
        candidates.append(reference_image_paths)
    elif reference_image_paths:
        candidates.extend(path for path in reference_image_paths if path)

    image_paths: list[str | Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        image_paths.append(path)
    return image_paths


def resolve_existing_image_path(image_path: str | Path) -> Path:
    resolved = Path(image_path).expanduser()
    if not resolved.is_absolute():
        resolved = Path.cwd() / resolved
    resolved = resolved.resolve()
    if not resolved.exists() or not resolved.is_file():
        raise RuntimeError(f"Reference image file does not exist: {resolved}")
    return resolved


def image_path_to_data_url(image_path: str | Path) -> str:
    resolved = resolve_existing_image_path(image_path)
    mime_type = mimetypes.guess_type(str(resolved))[0] or "image/png"
    encoded = base64.b64encode(resolved.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def build_google_genai_image_part(types_module: Any, image_path: str | Path) -> Any:
    resolved = resolve_existing_image_path(image_path)
    mime_type = mimetypes.guess_type(str(resolved))[0] or "image/png"
    return types_module.Part.from_bytes(data=resolved.read_bytes(), mime_type=mime_type)


def resolve_vertex_ai_project_location(base_url: str) -> tuple[str, str]:
    """Resolve Vertex AI project/location from env or a compact dashboard field.

    The dashboard Base URL field is reused for Vertex metadata because Vertex
    does not use an OpenAI-style endpoint. Supported values include
    ``project=my-project;location=us-central1`` and ``my-project/us-central1``.
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GOOGLE_GENAI_PROJECT") or ""
    location = os.environ.get("GOOGLE_CLOUD_LOCATION") or os.environ.get("GOOGLE_GENAI_LOCATION") or "us-central1"
    value = (base_url or "").strip()

    if value:
        parsed: dict[str, str] = {}
        for part in value.replace(",", ";").split(";"):
            if "=" not in part:
                continue
            key, raw_value = part.split("=", 1)
            parsed[key.strip().lower()] = raw_value.strip()
        if parsed:
            project = parsed.get("project") or parsed.get("project_id") or project
            location = parsed.get("location") or parsed.get("region") or location
        elif "/" in value:
            left, right = value.split("/", 1)
            project = left.strip() or project
            location = right.strip() or location
        else:
            project = value

    if not project:
        raise RuntimeError(
            "Google Vertex AI requires a project id. Set GOOGLE_CLOUD_PROJECT or enter "
            "project=<id>;location=<region> in the dashboard Vertex config field."
        )
    return project, location or "us-central1"


def extract_openai_message_text(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""

    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", "") if message is not None else ""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
            elif isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
        return "\n".join(parts)

    return str(content or "")


def extract_anthropic_message_text(response: Any) -> str:
    content = getattr(response, "content", None) or []
    if not content:
        return ""

    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
        elif isinstance(block, dict) and block.get("text"):
            parts.append(str(block["text"]))
    return "\n".join(parts)


def extract_llm_reasoning_trace(response: Any, settings: Settings, source: str = "") -> dict[str, Any]:
    """Extract provider-returned visible reasoning/thinking fields.

    This intentionally does not invent chain-of-thought. It only surfaces fields
    the provider response explicitly returned, such as DeepSeek
    ``reasoning_content``, Anthropic ``thinking`` blocks, Gemini thought
    summaries, OpenRouter ``reasoning_details``, or opaque OpenAI reasoning
    items.
    """
    items: list[dict[str, Any]] = []
    redacted = False

    def add_item(title: str, value: Any, kind: str = "reasoning", opaque: bool = False) -> None:
        nonlocal redacted
        if value is None:
            return
        text = stringify_reasoning_value(value)
        if not text and not opaque:
            return
        if len(items) >= 8:
            return
        entry: dict[str, Any] = {
            "title": title,
            "kind": kind,
            "text": text,
        }
        if opaque:
            entry["opaque"] = True
            redacted = True
        items.append(entry)

    choices = as_list(get_value(response, "choices"))
    if choices:
        message = get_value(choices[0], "message")
        if message is not None:
            add_item("reasoning_content", get_value(message, "reasoning_content"), "reasoning")
            add_item("reasoning", get_value(message, "reasoning"), "reasoning")
            add_item("thinking", get_value(message, "thinking"), "thinking")
            add_item("reasoning_details", get_value(message, "reasoning_details"), "reasoning_details")

    for block in as_list(get_value(response, "content")):
        block_type = str(get_value(block, "type") or "").strip().lower()
        if block_type == "thinking":
            add_item("thinking", get_value(block, "thinking") or get_value(block, "text"), "thinking")
        elif block_type == "redacted_thinking":
            add_item("redacted thinking", "Provider returned an opaque thinking block.", "thinking", opaque=True)

    for item in as_list(get_value(response, "output")):
        item_type = str(get_value(item, "type") or "").strip().lower()
        if item_type != "reasoning":
            continue
        summary = get_value(item, "summary")
        if summary:
            add_item("reasoning summary", summary, "summary")
        encrypted = get_value(item, "encrypted_content")
        if encrypted:
            add_item("encrypted reasoning item", "Provider returned encrypted reasoning for continuity.", "encrypted", opaque=True)

    for candidate in as_list(get_value(response, "candidates")):
        content = get_value(candidate, "content")
        for part in as_list(get_value(content, "parts")):
            if bool(get_value(part, "thought")):
                add_item("thought summary", get_value(part, "text"), "summary")

    return {
        "schema": "vrcforge.llm_reasoning.v1",
        "provider": normalize_provider_name(settings.llm_provider),
        "providerLabel": provider_display_name(settings.llm_provider),
        "model": settings.llm_model,
        "source": source,
        "collapsedDefault": True,
        "redacted": redacted,
        "itemCount": len(items),
        "items": items,
    }


def get_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    if value is None:
        return None
    return getattr(value, key, None)


def as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    return [value]


def stringify_reasoning_value(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, list):
        parts = [stringify_reasoning_value(item, limit=limit) for item in value]
        text = "\n".join(part for part in parts if part)
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    else:
        model_dump = getattr(value, "model_dump", None)
        to_dict = getattr(value, "to_dict", None)
        if callable(model_dump):
            text = json.dumps(model_dump(), ensure_ascii=False, indent=2, default=str)
        elif callable(to_dict):
            text = json.dumps(to_dict(), ensure_ascii=False, indent=2, default=str)
        else:
            text = str(value).strip()
    if len(text) > limit:
        return text[: limit - 14].rstrip() + "\n[truncated]"
    return text


def format_openai_compatible_error(exc: Exception, settings: Settings) -> str:
    status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None) or "unknown"
    message = str(exc).strip()
    provider_name = provider_display_name(settings.llm_provider)

    if status_code == 429:
        return (
            f"{provider_name} quota was exhausted for model '{settings.llm_model}'.\n"
            "Try a lighter model, check provider billing/quota, or retry after the quota window resets.\n"
            f"Original error: {message}"
        )

    if status_code in {401, 403}:
        return (
            f"{provider_name} rejected the API key while calling model '{settings.llm_model}'.\n"
            f"Check {settings.llm_api_key_env} or the saved dashboard API config.\n"
            f"Original error: {message}"
        )

    return (
        f"{provider_name} request failed for model '{settings.llm_model}' via OpenAI-compatible endpoint "
        f"'{settings.llm_base_url}' with HTTP {status_code}.\nOriginal error: {message}"
    )


def format_anthropic_error(exc: Exception, model: str) -> str:
    status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None) or "unknown"
    message = str(exc).strip()

    if status_code == 429:
        return (
            f"Anthropic quota was exhausted for model '{model}'.\n"
            "Check Anthropic billing/quota or retry after the quota window resets.\n"
            f"Original error: {message}"
        )

    if status_code in {401, 403}:
        return (
            f"Anthropic rejected the x-api-key while calling model '{model}'.\n"
            "Check ANTHROPIC_API_KEY or the saved dashboard API config.\n"
            f"Original error: {message}"
        )

    return f"Anthropic request failed for model '{model}' with HTTP {status_code}.\nOriginal error: {message}"


def format_multimodal_error(exc: Exception, settings: Settings, has_image: bool, provider_name: str) -> str:
    if not has_image:
        status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None) or "unknown"
        return (
            f"{provider_name} request failed for model '{settings.llm_model}' with HTTP {status_code}.\n"
            f"Original error: {str(exc).strip()}"
        )

    status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None) or "unknown"
    message = str(exc).strip()
    return (
        f"{provider_name} model '{settings.llm_model}' does not support image input, "
        "or this provider endpoint rejected the text+image request.\n"
        "Use a vision-capable model for reference-image face editing, or remove the reference image.\n"
        f"HTTP {status_code}. Original error: {message}"
    )


def provider_display_name(provider: str) -> str:
    return {
        "gemini": "Google AI Studio",
        "deepseek": "DeepSeek",
        "openai": "OpenAI",
        "openrouter": "OpenRouter",
        "anthropic": "Anthropic",
        "ollama": "Ollama",
        "vertexai": "Google Vertex AI",
        "custom": "Custom",
    }[normalize_provider_name(provider)]


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


def build_planner_prompt(
    export_payload: dict[str, Any],
    instruction: str,
    has_reference_image: bool = False,
    reference_image_count: int = 0,
    reference_image_labels: Sequence[str] | None = None,
) -> str:
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

    image_note = ""
    if has_reference_image:
        labels = [str(label) for label in (reference_image_labels or []) if str(label).strip()]
        label_text = "\n".join(f"- Image {index + 1}: {label}" for index, label in enumerate(labels))
        if not label_text:
            label_text = f"- {reference_image_count or 1} reference image(s) attached."
        image_note = (
            f"{reference_image_count or 1} image(s) are attached in this same request.\n"
            f"{label_text}\n"
            "Use original/current-face images only as the before/current baseline. Use target/reference images as the desired face or expression. "
            "If only target/reference images are provided, use them as the desired result. If only original/current images are provided, use them as visual context for the text instruction. "
            "Only translate visible facial style/expression cues into available face blendshapes; do not identify people or infer private traits.\n\n"
        )

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
        "7. Every adjustment must be directly supported by the user's instruction. Do not alter chest, body size, hair, teeth, "
        "clothing, or accessories unless the instruction explicitly asks for those areas.\n"
        "8. For face-expression requests, prefer eye, brow, cheek, jaw/face, mouth, and lip blendshapes. Do not use teeth/giza "
        "blendshapes unless teeth are explicitly requested.\n\n"
        f"{image_note}"
        f"Output JSON shape example: {json.dumps(schema, ensure_ascii=False)}\n\n"
        f"User instruction, authoritative: {instruction}\n\n"
        f"Exported Unity / VRChat blendshape data:\n{json.dumps(export_payload, ensure_ascii=False, indent=2)}\n\n"
        f"Before returning JSON, re-check that every adjustment directly answers this exact instruction: {instruction}"
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
                f"The planner returned duplicate edits for {adjustment.blendshape_name} on {adjustment.renderer_path}; "
                "the later target weight was kept."
            )
            deduped_adjustments[dedupe_index[key]] = adjustment
            continue

        dedupe_index[key] = len(deduped_adjustments)
        deduped_adjustments.append(adjustment)

    if invalid_targets:
        detail = "\n".join(invalid_targets)
        raise RuntimeError(
            "The planner returned blendshape targets that do not exist in the selected avatar export.\n"
            f"Selected avatar: {selected_avatar.avatar_path}\n{detail}"
        )

    if not deduped_adjustments:
        warning_text = "; ".join(warnings) if warnings else "The planner did not find a safe match."
        raise RuntimeError(f"No blendshape adjustments were generated. {warning_text}")

    if low_confidence_adjustments and not allow_low_confidence:
        detail = "\n".join(low_confidence_adjustments)
        raise RuntimeError(
            "The planner returned low-confidence adjustments. Re-run with a more specific prompt, lower "
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


def render_apply_payload_json(selected_avatar: SelectedAvatar, plan: BlendshapePlan) -> str:
    payload = {
        "tool": "vrc_apply_blendshapes",
        "params": {
            "avatarPath": selected_avatar.avatar_path,
            "adjustments": [
                {
                    "rendererPath": adjustment.renderer_path,
                    "blendshapeName": adjustment.blendshape_name,
                    "targetWeight": adjustment.target_weight,
                    "reason": adjustment.reason,
                    "confidence": adjustment.confidence,
                }
                for adjustment in plan.adjustments
            ],
            "saveAssets": True,
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def apply_blendshape_plan_direct(settings: Settings, selected_avatar: SelectedAvatar, plan: BlendshapePlan) -> McpResult:
    adjustments = [
        {
            "rendererPath": adjustment.renderer_path,
            "blendshapeName": adjustment.blendshape_name,
            "targetWeight": adjustment.target_weight,
        }
        for adjustment in plan.adjustments
    ]
    if not adjustments:
        payload = {"ok": True, "appliedCount": 0, "applied": []}
        return McpResult(exit_code=0, stdout=json.dumps(payload, ensure_ascii=False), stderr="", payload=payload)

    return invoke_unity_mcp(
        settings,
        "vrc_apply_blendshapes",
        {
            "avatarPath": selected_avatar.avatar_path,
            "adjustments": adjustments,
            "saveAssets": True,
        },
    )


def mock_execute_payload(apply_payload: str, selected_avatar: SelectedAvatar, export_source: str) -> McpResult:
    payload = {
        "mode": "mock",
        "message": "Skipped Unity execution and returned a mock success result.",
        "avatarPath": selected_avatar.avatar_path,
        "exportSource": export_source,
        "generatedPayloadLineCount": len(apply_payload.splitlines()),
        "generatedPayloadPreview": apply_payload.splitlines()[:12],
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
            completed = run_unity_mcp_process(
                settings,
                build_custom_tool_cli_args(settings, tool_name, params),
            )
            payload = try_parse_json(completed.stdout)
            result = McpResult(
                exit_code=completed.returncode,
                stdout=completed.stdout.strip(),
                stderr=completed.stderr.strip(),
                payload=payload,
            )

            payload_error = extract_mcp_error(result.payload)
            stdout_error = extract_unity_mcp_stdout_error(result.stdout)
            if completed.returncode == 0 and not payload_error and not stdout_error:
                return result

            error_text = payload_error or stdout_error or result.stderr or result.stdout or f"unity-mcp exited with code {completed.returncode}"
            raise UnityMcpError(humanize_unity_mcp_error(error_text))
        except Exception as exc:  # noqa: BLE001 - We want to retry any transport/runtime failure here.
            last_error = exc
            if attempt >= settings.unity_mcp_retries:
                break
            time.sleep(settings.unity_mcp_retry_backoff_seconds * attempt)

    detail = f": {last_error}" if last_error else "."
    raise UnityMcpError(f"Failed to call unity-mcp tool '{tool_name}' after retries{detail}") from last_error


def build_custom_tool_cli_args(settings: Settings, tool_name: str, params: dict[str, Any]) -> list[str]:
    params_json = json.dumps(params, ensure_ascii=False)
    if uses_unity_mcp_powershell_wrapper(settings.unity_mcp_command):
        params_b64 = base64.b64encode(params_json.encode("utf-8")).decode("ascii")
        return ["editor", "custom-tool", tool_name, "--params-b64", params_b64]

    return ["editor", "custom-tool", tool_name, "--params", params_json]


def uses_unity_mcp_powershell_wrapper(command: list[str]) -> bool:
    return any(str(part).lower().endswith("unity-mcp-cli.ps1") for part in command)


def extract_unity_mcp_stdout_error(stdout: str) -> str | None:
    stripped = stdout.strip()
    if not stripped:
        return None

    for line in stripped.splitlines():
        clean_line = line.strip()
        if clean_line.startswith("❌ Error:"):
            return clean_line.replace("❌ Error:", "", 1).strip() or clean_line

    return None


def run_unity_mcp_passthrough(settings: Settings, cli_args: list[str]) -> str:
    completed = run_unity_mcp_process(settings, cli_args)
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        detail = stderr or stdout or f"unity-mcp exited with code {completed.returncode}"
        raise UnityMcpError(humanize_unity_mcp_error(detail))

    return completed.stdout.strip() or completed.stderr.strip() or "unity-mcp returned no output."


def run_unity_mcp_process(settings: Settings, cli_args: list[str]) -> subprocess.CompletedProcess[str]:
    command = build_unity_mcp_command(settings, cli_args)
    command = resolve_unity_mcp_wrapper_command(command)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=settings.unity_mcp_timeout_seconds,
        )
    except FileNotFoundError as exc:
        joined_command = " ".join(command)
        raise UnityMcpError(
            "Could not find the unity-mcp CLI command.\n"
            f"Tried command: {joined_command}\n"
            "Install mcpforunityserver, or use the provided tools/unity-mcp-cli.ps1 wrapper in settings."
        ) from exc


def build_unity_mcp_command(settings: Settings, cli_args: list[str]) -> list[str]:
    command = list(settings.unity_mcp_command)
    command.extend(["--host", settings.unity_mcp_host, "--port", str(settings.unity_mcp_port)])
    if settings.unity_mcp_instance:
        command.extend(["--instance", settings.unity_mcp_instance])
    command.extend(cli_args)
    return command


def resolve_unity_mcp_wrapper_command(command: list[str]) -> list[str]:
    wrapper_index = next(
        (index for index, part in enumerate(command) if str(part).lower().endswith("unity-mcp-cli.ps1")),
        None,
    )
    if wrapper_index is None:
        return command

    resolved_prefix = find_unity_mcp_executable_prefix()
    if not resolved_prefix:
        return command

    cli_args = decode_params_base64_args(command[wrapper_index + 1:])
    return resolved_prefix + cli_args


def find_unity_mcp_executable_prefix() -> list[str] | None:
    unity_mcp_path = shutil.which("unity-mcp.exe") or shutil.which("unity-mcp")
    if unity_mcp_path:
        return [unity_mcp_path]

    candidates: list[Path] = []
    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        candidates.append(Path(virtual_env) / "Scripts" / "unity-mcp.exe")
    candidates.append(Path(sys.executable).parent / "Scripts" / "unity-mcp.exe")

    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.extend(
            [
                Path(appdata) / "Python" / "Python314" / "Scripts" / "unity-mcp.exe",
                Path(appdata) / "Python" / "Scripts" / "unity-mcp.exe",
            ]
        )

    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        candidates.append(Path(localappdata) / "Microsoft" / "WinGet" / "Links" / "unity-mcp.exe")

    for candidate in candidates:
        if candidate.exists():
            return [str(candidate)]

    uvx_path = shutil.which("uvx.exe") or shutil.which("uvx")
    uvx_candidates: list[Path] = []
    if virtual_env:
        uvx_candidates.append(Path(virtual_env) / "Scripts" / "uvx.exe")
    uvx_candidates.append(Path(sys.executable).parent / "Scripts" / "uvx.exe")
    if appdata:
        uvx_candidates.extend(
            [
                Path(appdata) / "Python" / "Python314" / "Scripts" / "uvx.exe",
                Path(appdata) / "Python" / "Scripts" / "uvx.exe",
            ]
        )
    if localappdata:
        uvx_candidates.append(Path(localappdata) / "Microsoft" / "WinGet" / "Links" / "uvx.exe")

    for candidate in uvx_candidates:
        if candidate.exists():
            uvx_path = str(candidate)
            break

    if uvx_path:
        return [uvx_path, "--from", "mcpforunityserver", "unity-mcp"]

    return None


def decode_params_base64_args(args: list[str]) -> list[str]:
    converted: list[str] = []
    index = 0
    while index < len(args):
        argument = args[index]
        if argument not in ("--params-b64", "--params-base64"):
            converted.append(argument)
            index += 1
            continue

        if index + 1 >= len(args):
            raise UnityMcpError(f"Missing value after {argument}.")
        decoded = base64.b64decode(args[index + 1]).decode("utf-8")
        converted.extend(["--params", decoded])
        index += 2

    return converted


def humanize_unity_mcp_error(detail: str) -> str:
    normalized = detail.strip()
    lowered = normalized.lower()

    if "http error from server: 503" in lowered or "cannot connect to unity mcp server" in lowered:
        return (
            f"{normalized}\n"
            "Unity MCP server is not ready yet. Open the target Unity project, wait for package import, "
            "then start MCP for Unity inside the editor before retrying."
        )

    return normalized


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


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
