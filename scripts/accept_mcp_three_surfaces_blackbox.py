"""Black-box acceptance for native MCP Tools, Resources and Prompts.

The probe launches the frozen backend's stdio entrypoint and learns state only
through protocol responses.  It never imports Gateway registries or Skill
definitions.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MCP_1X_VERSION = "2025-11-25"
MCP_2026_VERSION = "2026-07-28"
EXPECTED_INSTALLED_PROMPTS = {
    "vrcforge-avatar-accessory-switch",
    "vrcforge-avatar-animation",
    "vrcforge-avatar-audit",
    "vrcforge-avatar-breast-physics-audit",
    "vrcforge-avatar-expression-menu",
    "vrcforge-avatar-hairstyle",
    "vrcforge-avatar-head-transplant",
    "vrcforge-avatar-part-transplant",
    "vrcforge-avatar-wardrobe",
}


class Client:
    def __init__(self, executable: Path, profile: str, timeout: float) -> None:
        self.timeout = timeout
        self.process = subprocess.Popen(
            [
                str(executable),
                "--agent-mcp-stdio",
                "--protocol-profile",
                profile,
                "--exposure-layer",
                "planning",
            ],
            cwd=executable.parent.parent,
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
        self.lines: queue.Queue[str] = queue.Queue()
        self.errors: queue.Queue[str] = queue.Queue()
        self.notifications: list[dict[str, Any]] = []
        self.next_id = 1
        threading.Thread(target=self._pump, args=(self.process.stdout, self.lines), daemon=True).start()
        threading.Thread(target=self._pump, args=(self.process.stderr, self.errors), daemon=True).start()

    @staticmethod
    def _pump(stream: Any, output: queue.Queue[str]) -> None:
        if stream is None:
            return
        for line in stream:
            output.put(line.rstrip("\r\n"))

    def request(self, method: str, params: dict[str, Any], *, v2: bool = False) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        if v2:
            params = {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": MCP_2026_VERSION,
                    "io.modelcontextprotocol/clientCapabilities": {},
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "vrcforge-three-surface-blackbox",
                        "version": "1",
                    },
                },
                **params,
            }
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None and self.lines.empty():
                raise RuntimeError(f"stdio server exited {self.process.returncode}: {self.stderr_tail()}")
            try:
                line = self.lines.get(timeout=0.1)
            except queue.Empty:
                continue
            response = json.loads(line)
            if "id" not in response:
                self.notifications.append(response)
                continue
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise RuntimeError(f"{method} failed: {response['error']}")
            return dict(response.get("result") or {})
        raise TimeoutError(f"{method} timed out")

    def drain_notifications(self, wait_seconds: float = 0.75) -> list[dict[str, Any]]:
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            try:
                response = json.loads(self.lines.get(timeout=0.05))
            except queue.Empty:
                continue
            if "id" not in response:
                self.notifications.append(response)
        result = list(self.notifications)
        self.notifications.clear()
        return result

    def stderr_tail(self) -> list[str]:
        result: list[str] = []
        while True:
            try:
                result.append(self.errors.get_nowait())
            except queue.Empty:
                return result[-20:]

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def standard_acceptance(executable: Path, unity_project: Path, timeout: float) -> dict[str, Any]:
    client = Client(executable, "mcp-1x", timeout)
    try:
        initialized = client.request(
            "initialize",
            {
                "protocolVersion": MCP_1X_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "three-surface-blackbox", "version": "1"},
            },
        )
        capabilities = initialized.get("capabilities") or {}
        require(all(name in capabilities for name in ("tools", "resources", "prompts")), "initialize omitted a native MCP surface")
        tools_before = client.request("tools/list", {})
        planning_tools = list(tools_before.get("tools") or [])
        require(planning_tools, "planning tools/list was empty")
        require(
            all(
                not bool(tool.get("write"))
                and str((tool.get("_meta") or {}).get("permission") or "").casefold() != "write"
                for tool in planning_tools
            ),
            "planning tools/list exposed a write Tool",
        )

        resources_before = client.request("resources/list", {})
        templates = client.request("resources/templates/list", {})
        resource_types = {str((item.get("_meta") or {}).get("resourceType") or "") for item in resources_before.get("resources") or []}
        require("tool_catalog" in resource_types and "session_identity_lock" in resource_types, "required seed Resources were absent")
        require(len(templates.get("resourceTemplates") or []) >= 7, "Resource templates were incomplete")
        catalog_uri = next(
            item["uri"]
            for item in resources_before["resources"]
            if (item.get("_meta") or {}).get("resourceType") == "tool_catalog"
        )
        identity_uri = next(
            item["uri"]
            for item in resources_before["resources"]
            if (item.get("_meta") or {}).get("resourceType") == "session_identity_lock"
        )
        catalog = client.request("resources/read", {"uri": catalog_uri})
        require(catalog.get("structuredContent", {}).get("data", {}).get("sameDefinitionSource") is True, "Tool catalog Resource was not shared-source")

        prompts = client.request("prompts/list", {})
        prompt_names = {str(item.get("name") or "") for item in prompts.get("prompts") or []}
        require(EXPECTED_INSTALLED_PROMPTS <= prompt_names, "not all nine installed Skills were native Prompts")
        awaiting = client.request(
            "prompts/get",
            {"name": "vrcforge-avatar-audit", "arguments": {}},
        )
        require(awaiting.get("structuredContent", {}).get("context", {}).get("status") == "awaiting_resources", "Prompt guessed missing Resources")
        binding = client.request(
            "tools/call",
            {"name": "vrcforge_bind_execution_target", "arguments": {"projectPath": str(unity_project), "scope": "project"}},
        ).get("structuredContent", {})
        binding_resources = binding.get("resources", {})
        require(bool(binding_resources.get("identityLockUri")), "Live identity binding did not publish an identity Resource")
        ready = client.request(
            "prompts/get",
            {
                "name": "vrcforge-avatar-audit",
                "arguments": {"identityLockUri": binding_resources["identityLockUri"], "sessionContextUri": binding_resources["operationReceiptUri"]},
            },
        )
        context = ready.get("structuredContent", {}).get("context", {})
        require(context.get("status") == "ready_for_planning", "Prompt did not accept explicit Resource context")
        require(context.get("awaitingUserHardStop") is True and context.get("gameOnlyAcceptance"), "Prompt omitted safety/acceptance context")
        prompt_meta = ready.get("_meta") or {}
        prompt_provenance = {
            key: prompt_meta.get(key)
            for key in ("skillId", "version", "contentHash", "supportContentHash", "hashScope", "source", "packageId")
        }

        loaded = client.request(
            "tools/call",
            {"name": "vrcforge_load_tool_block", "arguments": {"block": "materials"}},
        )
        notifications = client.drain_notifications()
        methods = {str(item.get("method") or "") for item in notifications}
        require("notifications/tools/list_changed" in methods, "Tool block load omitted tools/list_changed")
        tools_after = client.request("tools/list", {})
        require(tools_after.get("catalogGeneration") != tools_before.get("catalogGeneration"), "Tool catalog generation did not advance")
        require("vrcforge_scan_materials" in {item.get("name") for item in tools_after.get("tools") or []}, "materials Tool did not appear")
        read_result = client.request(
            "tools/call",
            {
                "name": "vrcforge_get_compile_errors",
                "arguments": {
                    "projectPath": str(unity_project),
                    "promptSkillProvenance": prompt_provenance,
                },
            },
        )
        resource_notifications = client.drain_notifications()
        resource_methods = {str(item.get("method") or "") for item in resource_notifications}
        require("notifications/resources/list_changed" in resource_methods, "Gateway Tool result omitted resources/list_changed")
        receipt_uri = read_result.get("structuredContent", {}).get("resources", {}).get("operationReceiptUri")
        require(
            read_result.get("structuredContent", {}).get("promptSkillProvenance", {}).get("status") == "verified",
            "Tool result did not preserve verified Prompt/Skill provenance",
        )
        require(isinstance(receipt_uri, str) and receipt_uri.startswith("vrcforge://"), "Tool result omitted Resource receipt handle")
        receipt = client.request("resources/read", {"uri": receipt_uri})
        require(receipt.get("structuredContent", {}).get("resourceType") == "operation_receipt", "operation Resource readback failed")
        return {
            "protocol": MCP_1X_VERSION,
            "capabilities": capabilities,
            "planningToolCount": len(planning_tools),
            "promptCount": len(prompt_names),
            "installedPromptCount": len(EXPECTED_INSTALLED_PROMPTS & prompt_names),
            "resourceTemplateCount": len(templates.get("resourceTemplates") or []),
            "catalogGenerationBefore": tools_before.get("catalogGeneration"),
            "catalogGenerationAfter": tools_after.get("catalogGeneration"),
            "notifications": sorted(methods | resource_methods),
            "receiptUri": receipt_uri,
        }
    finally:
        client.close()


def v2_acceptance(executable: Path, timeout: float) -> dict[str, Any]:
    client = Client(executable, "vrcforge-2026", timeout)
    try:
        discover = client.request("server/discover", {}, v2=True)
        capabilities = discover.get("capabilities") or {}
        require(all(name in capabilities for name in ("tools", "resources", "prompts")), "MCP 2026 discovery omitted a surface")
        resources = client.request("resources/list", {}, v2=True)
        prompts = client.request("prompts/list", {}, v2=True)
        prompt_names = {str(item.get("name") or "") for item in prompts.get("prompts") or []}
        require(EXPECTED_INSTALLED_PROMPTS <= prompt_names, "MCP 2026 omitted installed Skill Prompts")
        resource_uri = next(item["uri"] for item in resources.get("resources") or [])
        read = client.request("resources/read", {"uri": resource_uri}, v2=True)
        require(read.get("structuredContent", {}).get("schema") == "vrcforge.resource.v1", "MCP 2026 Resource envelope mismatch")
        return {
            "protocol": MCP_2026_VERSION,
            "capabilities": capabilities,
            "resourceCount": len(resources.get("resources") or []),
            "promptCount": len(prompt_names),
            "installedPromptCount": len(EXPECTED_INSTALLED_PROMPTS & prompt_names),
            "resourceRead": resource_uri,
        }
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--unity-project", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    executable = args.backend.expanduser().resolve(strict=True)
    unity_project = args.unity_project.expanduser().resolve(strict=True)
    output = args.output or (
        ROOT
        / "artifacts"
        / "mcp-three-surfaces"
        / f"mcp-three-surfaces-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    )
    report: dict[str, Any] = {
        "schema": "vrcforge.mcp_three_surfaces_blackbox.v1",
        "ok": False,
        "backend": str(executable),
        "standard": {},
        "vrcforge2026": {},
        "error": "",
    }
    try:
        report["standard"] = standard_acceptance(executable, unity_project, args.timeout)
        report["vrcforge2026"] = v2_acceptance(executable, args.timeout)
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001 - artifact must preserve the black-box failure.
        report["error"] = f"{type(exc).__name__}: {exc}"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "output": str(output), "error": report["error"]}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
