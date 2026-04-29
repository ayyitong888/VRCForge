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
from google.genai import types
from pydantic import BaseModel, Field, ValidationError


DEFAULT_SETTINGS_PATH = Path(".gemini/settings.json")


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
        description="Use Gemini and Unity MCP to tune VRChat avatar blendshapes from natural language.")
    parser.add_argument(
        "instruction",
        help='Natural language expression tweak, e.g. "Open the eyes wider and raise the mouth corners".',
    )
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS_PATH, help="Path to settings.json")
    parser.add_argument("--dry-run", action="store_true", help="Generate the plan and C# snippet without sending it to Unity.")
    parser.add_argument("--print-plan", action="store_true", help="Print the full JSON plan produced by Gemini.")
    args = parser.parse_args()

    settings = load_settings(args.settings)
    export_payload = export_blendshapes(settings)
    plan = create_blendshape_plan(settings, export_payload, args.instruction)

    if args.print_plan:
        print(json.dumps(plan.model_dump(), indent=2, ensure_ascii=False))

    code = render_csharp(plan)
    if args.dry_run:
        print(code)
        return 0

    result = execute_csharp(settings, code)
    print(render_summary(plan, result))
    return 0


def load_settings(settings_path: Path) -> Settings:
    if not settings_path.exists():
        raise SystemExit(
            f"Missing settings file: {settings_path}\n"
            "Create it from the provided template, set GEMINI_API_KEY in your environment, and try again."
        )

    raw_settings = json.loads(settings_path.read_text(encoding="utf-8"))
    gemini_settings = raw_settings.get("gemini", {})
    mcp_settings = raw_settings.get("unity_mcp", {})
    path_settings = raw_settings.get("paths", {})

    api_key_env = gemini_settings.get("api_key_env", "GEMINI_API_KEY")
    gemini_api_key = os.environ.get(api_key_env, "").strip()
    if not gemini_api_key:
        raise SystemExit(f"Environment variable {api_key_env} is empty. Set your Gemini API key before running.")

    command = mcp_settings.get("command", ["unity-mcp"])
    if isinstance(command, str):
        command = [command]

    export_path = Path(path_settings.get("blendshape_export", "Assets/VRCAutoRig/blendshapes_export.json"))

    return Settings(
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_settings.get("model", "gemini-3.1-pro-preview"),
        gemini_thinking_level=gemini_settings.get("thinking_level", "low"),
        unity_mcp_command=command,
        unity_mcp_retries=int(mcp_settings.get("retries", 3)),
        unity_mcp_retry_backoff_seconds=float(mcp_settings.get("retry_backoff_seconds", 2.0)),
        unity_mcp_timeout_seconds=int(mcp_settings.get("timeout_seconds", 30)),
        export_tool_name=mcp_settings.get("export_tool_name", "vrc_export_blendshapes"),
        execute_tool_name=mcp_settings.get("execute_tool_name", "vrc_execute_roslyn"),
        export_path=export_path,
    )


def export_blendshapes(settings: Settings) -> dict[str, Any]:
    export_params = {"outputPath": settings.export_path.as_posix(), "refreshAssets": True}
    invoke_unity_mcp(settings, settings.export_tool_name, export_params)

    if not settings.export_path.exists():
        raise UnityMcpError(
            f"Unity export tool reported success but the export file was not created: {settings.export_path}"
        )

    return json.loads(settings.export_path.read_text(encoding="utf-8"))


def create_blendshape_plan(settings: Settings, export_payload: dict[str, Any], instruction: str) -> BlendshapePlan:
    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = build_planner_prompt(export_payload, instruction)

    # Keep the LLM adapter isolated so swapping Gemini for DeepSeek later only changes this block.
    config = types.GenerateContentConfig(response_mime_type="application/json")

    if settings.gemini_thinking_level:
        try:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_level=settings.gemini_thinking_level),
            )
        except TypeError:
            # Older SDK versions may not expose thinking config yet.
            pass

    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=config,
    )

    raw_json = extract_json_block(response.text or "")
    if not raw_json:
        raise RuntimeError("Gemini returned an empty response while generating the blendshape plan.")

    try:
        return BlendshapePlan.model_validate(json.loads(raw_json))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError(f"Gemini returned invalid blendshape JSON:\n{response.text}") from exc


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


def render_csharp(plan: BlendshapePlan) -> str:
    if not plan.adjustments:
        warning_text = "; ".join(plan.warnings) if plan.warnings else "Gemini did not find a safe match."
        raise RuntimeError(f"No blendshape adjustments were generated. {warning_text}")

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


def execute_csharp(settings: Settings, code: str) -> McpResult:
    return invoke_unity_mcp(
        settings,
        settings.execute_tool_name,
        {"code": code, "enforceWriteDefaultsOn": True},
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

            if completed.returncode == 0:
                return result

            error_text = result.stderr or result.stdout or f"unity-mcp exited with code {completed.returncode}"
            raise UnityMcpError(error_text)
        except Exception as exc:  # noqa: BLE001 - We want to retry any transport/runtime failure here.
            last_error = exc
            if attempt >= settings.unity_mcp_retries:
                break
            time.sleep(settings.unity_mcp_retry_backoff_seconds * attempt)

    raise UnityMcpError(f"Failed to call unity-mcp tool '{tool_name}' after retries.") from last_error


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


def render_summary(plan: BlendshapePlan, result: McpResult) -> str:
    lines = [f"Applied plan: {plan.summary}"]

    if plan.warnings:
        lines.append("Warnings: " + " | ".join(plan.warnings))

    lines.append(f"Adjusted blendshapes: {len(plan.adjustments)}")

    if result.stdout:
        lines.append("Unity MCP output:")
        lines.append(result.stdout)

    return "\n".join(lines)


def to_csharp_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f"\"{escaped}\""


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
