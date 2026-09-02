"""Real-Agent black-box acceptance for lazy VRCForge MCP Tool discovery.

This host deliberately knows only standard MCP JSON-RPC. It launches the
repository stdio server, feeds each fresh tools/list snapshot to a separate
Codex Agent, follows list_changed by re-listing, and verifies positive and
negative Tool selection without reading VRCForge source metadata directly.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_VERSION = "2025-11-25"


class StdioMcpClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "tools" / "vrcforge_agent_mcp_stdio.py"),
                "--no-start",
                "--protocol-profile",
                "mcp-1x",
                "--exposure-layer",
                "execution",
            ],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
        self.output: queue.Queue[str] = queue.Queue()
        self.errors: queue.Queue[str] = queue.Queue()
        self.notifications: list[dict[str, Any]] = []
        threading.Thread(target=self._pump, args=(self.process.stdout, self.output), daemon=True).start()
        threading.Thread(target=self._pump, args=(self.process.stderr, self.errors), daemon=True).start()
        self.next_id = 1

    @staticmethod
    def _pump(stream: Any, target: queue.Queue[str]) -> None:
        if stream is None:
            return
        for line in stream:
            target.put(line.rstrip("\r\n"))

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        if self.process.stdin is None:
            raise RuntimeError("MCP stdio input is unavailable")
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None and self.output.empty():
                raise RuntimeError(f"MCP server exited with {self.process.returncode}: {self.stderr_tail()}")
            try:
                line = self.output.get(timeout=0.1)
            except queue.Empty:
                continue
            message = json.loads(line)
            if "id" not in message:
                self.notifications.append(message)
                continue
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"MCP {method} failed: {message['error']}")
                return message["result"]
        raise TimeoutError(f"MCP {method} timed out after {self.timeout}s")

    def drain_notifications(self, *, wait_seconds: float = 0.5) -> list[dict[str, Any]]:
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            try:
                line = self.output.get(timeout=0.05)
            except queue.Empty:
                continue
            message = json.loads(line)
            if "id" not in message:
                self.notifications.append(message)
        result = list(self.notifications)
        self.notifications.clear()
        return result

    def stderr_tail(self) -> list[str]:
        lines: list[str] = []
        while True:
            try:
                lines.append(self.errors.get_nowait())
            except queue.Empty:
                break
        return lines[-12:]

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)


def run_codex_selector(message: str, tools: list[dict[str, Any]], *, timeout: float) -> dict[str, Any]:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["toolCalls", "reason"],
        "properties": {
            "toolCalls": {
                "type": "array",
                "maxItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "block", "projectPath", "avatarPath"],
                    "properties": {
                        "name": {"type": "string"},
                        "block": {"type": ["string", "null"]},
                        "projectPath": {"type": ["string", "null"]},
                        "avatarPath": {"type": ["string", "null"]},
                    },
                },
            },
            "reason": {"type": "string"},
        },
    }
    compact_tools = [
        {
            "name": tool.get("name"),
            "description": tool.get("description"),
            "inputSchema": tool.get("inputSchema"),
            "annotations": tool.get("annotations"),
            "_meta": {
                key: value
                for key, value in dict(tool.get("_meta") or {}).items()
                if key in {"permission", "toolBlock", "whenToUse", "whenNotToUse"}
            },
        }
        for tool in tools
    ]
    prompt = (
        "You are an external black-box MCP Agent. You cannot read server source. "
        "Using only this exact tools/list snapshot, select at most one Tool for the user. "
        "If the needed domain Tool is not visible but a lazy block loader is visible, select the loader "
        "with the correct block argument. If the user asks only for an explanation or forbids project "
        "inspection/change, return no calls. Explain briefly what the selected Tool does and why it fits.\n\n"
        f"tools/list snapshot:\n{json.dumps(compact_tools, ensure_ascii=False, sort_keys=True)}\n\n"
        f"User request:\n{message}"
    )
    with tempfile.TemporaryDirectory(prefix="vrcforge-mcp-agent-") as temp_value:
        temp = Path(temp_value)
        schema_path = temp / "selection.schema.json"
        output_path = temp / "selection.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        completed = subprocess.run(
            [
                "codex",
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-",
            ],
            cwd=temp,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
        if completed.returncode != 0 or not output_path.is_file():
            raise RuntimeError(
                "Codex selector failed: "
                + (completed.stderr or completed.stdout)[-2000:]
            )
        result = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise RuntimeError("Codex selector returned a non-object")
        return result


def call_names(selection: dict[str, Any]) -> list[str]:
    calls = selection.get("toolCalls")
    if not isinstance(calls, list):
        return []
    return [str(item.get("name") or "") for item in calls if isinstance(item, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real Codex Agent against a fresh lazy MCP tools/list session.")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or (
        ROOT
        / "artifacts"
        / "mcp-agent-blackbox"
        / f"mcp-agent-blackbox-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    )
    client = StdioMcpClient(timeout=min(args.timeout, 30.0))
    report: dict[str, Any] = {
        "schema": "vrcforge.mcp_agent_blackbox.v1",
        "ok": False,
        "agent": "codex exec --ephemeral --ignore-user-config",
        "protocol": PROTOCOL_VERSION,
        "steps": [],
    }
    try:
        initialized = client.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "VRCForge Agent acceptance host", "version": "1"},
            },
        )
        before = client.request("tools/list", {})
        before_tools = list(before.get("tools") or [])
        discover_selection = run_codex_selector(
            "扫描当前 Unity Avatar 使用的全部材质；如果材质工具尚未加载，先加载正确的工具块。",
            before_tools,
            timeout=args.timeout,
        )
        discover_calls = discover_selection.get("toolCalls") or []
        discover_call = discover_calls[0] if len(discover_calls) == 1 else {}
        discover_ok = (
            discover_call.get("name") == "vrcforge_load_tool_block"
            and str(discover_call.get("block") or "").casefold() in {"materials", "5"}
        )
        report["steps"].append({
            "name": "agent_selects_lazy_materials_block",
            "ok": discover_ok,
            "visibleToolCount": len(before_tools),
            "catalogGeneration": before.get("catalogGeneration"),
            "selection": discover_selection,
        })
        if not discover_ok:
            raise RuntimeError("Agent did not select the materials block loader")

        loaded = client.request(
            "tools/call",
            {"name": discover_call["name"], "arguments": {"block": discover_call["block"]}},
        )
        notifications = client.drain_notifications()
        changed = any(item.get("method") == "notifications/tools/list_changed" for item in notifications)
        after = client.request("tools/list", {})
        after_tools = list(after.get("tools") or [])
        target_visible = any(tool.get("name") == "vrcforge_scan_materials" for tool in after_tools)
        generation_advanced = int(after.get("catalogGeneration") or 0) > int(before.get("catalogGeneration") or 0)
        loaded_structured = dict(loaded.get("structuredContent") or {})
        refresh_ok = changed and target_visible and generation_advanced
        report["steps"].append({
            "name": "host_consumes_list_changed_and_relists",
            "ok": refresh_ok,
            "notificationObserved": changed,
            "beforeToolCount": len(before_tools),
            "afterToolCount": len(after_tools),
            "beforeGeneration": before.get("catalogGeneration"),
            "afterGeneration": after.get("catalogGeneration"),
            "targetVisible": target_visible,
            "loadOperationId": loaded_structured.get("operationId"),
            "loadOperationStatus": loaded_structured.get("operationStatus"),
        })
        if not refresh_ok:
            raise RuntimeError("MCP host did not complete the lazy refresh loop")

        target_selection = run_codex_selector(
            "只读取并扫描当前 Unity Avatar 使用的材质、Shader 和纹理引用，不要修改任何内容。",
            after_tools,
            timeout=args.timeout,
        )
        target_ok = call_names(target_selection) == ["vrcforge_scan_materials"]
        report["steps"].append({
            "name": "agent_selects_exact_material_scan_tool",
            "ok": target_ok,
            "selection": target_selection,
        })
        if target_ok:
            selected_call = target_selection["toolCalls"][0]
            read_arguments = {
                key: selected_call[key]
                for key in ("projectPath", "avatarPath")
                if selected_call.get(key)
            }
            read_result = client.request(
                "tools/call",
                {"name": selected_call["name"], "arguments": read_arguments},
            )
            read_structured = dict(read_result.get("structuredContent") or {})
            read_contract_ok = (
                str(read_structured.get("operationId") or "").startswith("mcpread_")
                and read_structured.get("mutationStarted") is False
                and read_structured.get("resources", {}).get("status") == "unavailable"
                and read_structured.get("promptSkillProvenance", {}).get("status") == "unavailable"
            )
            report["steps"].append({
                "name": "selected_read_tool_returns_stage1_contract",
                "ok": read_contract_ok,
                "tool": selected_call["name"],
                "domainStatus": read_structured.get("status"),
                "operationStatus": read_structured.get("operationStatus"),
                "operationId": read_structured.get("operationId"),
                "mutationStarted": read_structured.get("mutationStarted"),
            })

        write_name = "vrcforge_set_material_texture"
        write_visible = any(tool.get("name") == write_name for tool in after_tools)
        write_result = client.request(
            "tools/call",
            {"name": write_name, "arguments": {}},
        ) if write_visible else {}
        write_structured = dict(write_result.get("structuredContent") or {})
        write_contract_ok = (
            write_visible
            and str(write_structured.get("operationId") or "").startswith(("mcpreject_", "mcpwrite_"))
            and write_structured.get("operationStatus") == "failed"
            and write_structured.get("mutationStarted") is False
            and write_structured.get("resources", {}).get("status") == "unavailable"
            and write_structured.get("promptSkillProvenance", {}).get("status") == "unavailable"
        )
        report["steps"].append({
            "name": "supervised_write_fails_closed_with_stage1_contract",
            "ok": write_contract_ok,
            "tool": write_name,
            "visible": write_visible,
            "domainStatus": write_structured.get("status"),
            "operationStatus": write_structured.get("operationStatus"),
            "operationId": write_structured.get("operationId"),
            "mutationStarted": write_structured.get("mutationStarted"),
            "errorCode": write_structured.get("errorDetails", {}).get("errorCode"),
        })

        negative_selection = run_codex_selector(
            "只解释 Unity 的 Material 是什么，不要读取、检查或修改当前项目。",
            after_tools,
            timeout=args.timeout,
        )
        negative_ok = call_names(negative_selection) == []
        report["steps"].append({
            "name": "agent_makes_zero_call_for_explanation",
            "ok": negative_ok,
            "selection": negative_selection,
        })

        descriptors_ok = all(
            isinstance(tool.get("inputSchema"), dict)
            and isinstance(tool.get("outputSchema"), dict)
            and all(
                marker in str(tool.get("description") or "")
                for marker in ("When to use:", "When NOT to use:", "Negative example:")
            )
            for tool in after_tools
        )
        report["steps"].append({
            "name": "fresh_tools_list_is_self_describing",
            "ok": descriptors_ok,
            "checkedToolCount": len(after_tools),
        })
        report["initialize"] = {
            "serverInfo": initialized.get("serverInfo"),
            "capabilities": initialized.get("capabilities"),
        }
        report["ok"] = all(bool(step.get("ok")) for step in report["steps"])
    except Exception as exc:  # noqa: BLE001 - acceptance always emits evidence.
        report["error"] = str(exc)
    finally:
        report["stderrTail"] = client.stderr_tail()
        client.close()
        report["finishedAt"] = datetime.now(timezone.utc).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "reportPath": str(output), "steps": report["steps"]}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
