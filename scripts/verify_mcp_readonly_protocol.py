"""Inspect a named frozen MCP endpoint through its public STDIO protocol only.

This records protocol evidence, not real Agent selection or Unity write proof.
Each child owns only its pipes and session; authentication stays in the explicit
Gateway config, and closing a client reaps its process and closes every pipe.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Any


class WireClient:
    def __init__(self, backend: Path, base_url: str, config: Path, profile: str, timeout: float):
        self.timeout = timeout
        self.profile = profile
        self.sequence = 0
        self.trace: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.lines: queue.Queue[str] = queue.Queue()
        self.errors: list[str] = []
        environment = os.environ.copy()
        environment.update(VRCFORGE_AGENT_BASE_URL=base_url,
                           VRCFORGE_AGENT_GATEWAY_CONFIG=str(config),
                           VRCFORGE_AGENT_TIMEOUT=str(timeout), VRCFORGE_AGENT_NO_START="1")
        self.process = subprocess.Popen(
            [str(backend), "--agent-mcp-stdio", "--protocol-profile", profile,
             "--exposure-layer", "planning"], env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=backend.parent, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.readers = [
            threading.Thread(target=self._pump, args=(self.process.stdout, False), daemon=True),
            threading.Thread(target=self._pump, args=(self.process.stderr, True), daemon=True),
        ]
        for reader in self.readers:
            reader.start()

    def _pump(self, stream, stderr: bool):
        for line in stream:
            if stderr:
                self.errors.append(line.rstrip())
                self.errors[:] = self.errors[-20:]
            else:
                self.lines.put(line)

    def request(self, method: str, params: dict | None = None) -> dict:
        self.sequence += 1
        values = dict(params or {})
        if self.profile == "vrcforge-2026":
            values["_meta"] = {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {"name": "readonly-protocol-verifier", "version": "1"},
            }
        request = {"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": values}
        self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                response = json.loads(self.lines.get(timeout=0.1))
            except queue.Empty:
                if self.process.poll() is not None:
                    raise RuntimeError(f"STDIO process exited {self.process.returncode}")
                continue
            if "id" not in response:
                self.notifications.append(response)
            elif response["id"] == self.sequence:
                self.trace.append({"request": request, "response": response})
                return response
        raise TimeoutError(f"{method} exceeded {self.timeout} seconds")

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        for reader in self.readers:
            reader.join(timeout=2)
        self.process.stdout.close()
        self.process.stderr.close()


def result(response: dict) -> dict:
    if "error" in response:
        raise ValueError(json.dumps(response["error"], ensure_ascii=False))
    return response.get("result") or {}


def content(response: dict) -> dict:
    value = result(response)
    return value.get("structuredContent") or value


def probe(client: WireClient, expected_prompts: set[str]) -> dict:
    checks: dict[str, bool] = {}
    report: dict[str, Any] = {"checks": checks, "limitations": ["No Unity write or real Agent selection was attempted."]}
    if client.profile == "mcp-1x":
        greeting = result(client.request("initialize", {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "readonly-protocol-verifier", "version": "1"},
        }))
    else:
        greeting = result(client.request("server/discover"))
    checks["threeCapabilities"] = all(name in greeting.get("capabilities", {}) for name in ("tools", "resources", "prompts"))
    before = result(client.request("tools/list"))
    tools = before.get("tools", [])
    checks["uniqueToolNames"] = len({t["name"] for t in tools}) == len(tools)
    checks["planningNoWriteExposure"] = bool(tools) and all(
        not t.get("write") and str(t.get("_meta", {}).get("permission", "")).lower() != "write" for t in tools
    )
    resources = result(client.request("resources/list"))
    templates = result(client.request("resources/templates/list"))
    checks["resourceTemplatesPresent"] = bool(templates.get("resourceTemplates"))
    envelopes = []
    for item in resources.get("resources", []):
        envelope = content(client.request("resources/read", {"uri": item["uri"]}))
        envelopes.append(envelope)
        checks[f"resourceHash:{item['name']}"] = envelope.get("contentHash") == hashlib.sha256(json.dumps(
            {"identity": envelope.get("identity"), "data": envelope.get("data")},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()
    missing = client.request("resources/read", {"uri": "vrcforge://operation/readonly-probe-never-created/receipt?revision=1"})
    checks["unknownResourceRejected"] = "error" in missing
    prompts = result(client.request("prompts/list"))
    names = {item["name"] for item in prompts.get("prompts", [])}
    checks["expectedInstalledPrompts"] = bool(expected_prompts) and expected_prompts <= names
    report["promptCount"] = len(names)
    for name in sorted(names):
        payload = content(client.request("prompts/get", {"name": name}))
        checks[f"promptFullInstructions:{name}"] = bool(payload.get("skill", {}).get("instructions"))
        checks[f"promptMissingContextWaits:{name}"] = payload.get("context", {}).get("status") == "awaiting_resources"
        files = payload.get("skill", {}).get("supportFiles", [])
        declared = payload.get("provenance", {}).get("supportFilePaths", [])
        checks[f"supportContents:{name}"] = [item.get("path") for item in files] == declared and all(
            item.get("sha256") == hashlib.sha256(item.get("content", "").encode("utf-8")).hexdigest() for item in files
        )
    if names:
        invalid = client.request("prompts/get", {"name": sorted(names)[0], "arguments": {
            "identityLockUri": "not-a-resource", "sessionContextUri": "not-a-resource",
        }})
        checks["fabricatedContextNotReady"] = "error" in invalid or content(invalid).get("context", {}).get("status") != "ready_for_planning"
        identity = next((v for v in envelopes if v.get("resourceType") == "session_identity_lock"), None)
        catalog = next((v for v in envelopes if v.get("resourceType") == "tool_catalog"), None)
        if identity and catalog:
            wrong_type = client.request("prompts/get", {"name": sorted(names)[0], "arguments": {
                "identityLockUri": catalog["uri"], "sessionContextUri": identity["uri"],
            }})
            checks["wrongResourceTypeNotReady"] = "error" in wrong_type or content(wrong_type).get("context", {}).get("status") != "ready_for_planning"
    available = {t["name"] for t in tools}
    if "vrcforge_health" in available:
        health = content(client.request("tools/call", {"name": "vrcforge_health", "arguments": {}}))
        health_schema = next(t for t in tools if t["name"] == "vrcforge_health").get("outputSchema", {})
        checks["healthRequiredOutputFields"] = all(key in health for key in health_schema.get("required", []))
        uri = health.get("resources", {}).get("operationReceiptUri")
        checks["healthReceiptPresent"] = bool(uri)
        if uri:
            receipt = content(client.request("resources/read", {"uri": uri}))
            operation = health.get("operationId") or health.get("requestId")
            checks["healthReceiptIndependentReadback"] = bool(operation) and receipt.get("data", {}).get("operationId") == operation
        report["healthUnityReadiness"] = health
    if {"vrcforge_load_tool_block", "vrcforge_unload_tool_block"} <= available:
        notification_start = len(client.notifications)
        loaded = content(client.request("tools/call", {"name": "vrcforge_load_tool_block", "arguments": {"block": "materials"}}))
        after = result(client.request("tools/list"))
        after_names = {t["name"] for t in after.get("tools", [])}
        checks["loadChangesGeneration"] = after.get("catalogGeneration") is not None and after.get("catalogGeneration") != before.get("catalogGeneration")
        checks["hostRelistAddsTools"] = bool(after_names - available)
        checks["loadNotification"] = any(n.get("method") == "notifications/tools/list_changed" for n in client.notifications[notification_start:])
        notification_start = len(client.notifications)
        unloaded = content(client.request("tools/call", {"name": "vrcforge_unload_tool_block", "arguments": {"block": "materials"}}))
        restored = result(client.request("tools/list"))
        checks["hostRelistRemovesTools"] = {t["name"] for t in restored.get("tools", [])} == available
        checks["unloadChangesGeneration"] = restored.get("catalogGeneration") != after.get("catalogGeneration")
        checks["unloadNotification"] = any(n.get("method") == "notifications/tools/list_changed" for n in client.notifications[notification_start:])
        report["toolBlockResults"] = {"load": loaded, "unload": unloaded}
    else:
        checks["stdioBlockControlsPresent"] = False
    report["ok"] = all(checks.values())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--expected-prompt", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    backend = args.backend.resolve(strict=True)
    config = args.config.resolve(strict=True)
    if args.output.exists():
        parser.error("output already exists; preserve prior evidence")
    report = {"schema": "vrcforge.readonly_mcp_protocol.v1", "capturedAt": datetime.now(timezone.utc).isoformat(),
              "backend": str(backend), "backendSha256": hashlib.sha256(backend.read_bytes()).hexdigest(),
              "baseUrl": args.base_url, "profiles": {}, "ok": False}
    for profile in ("mcp-1x", "vrcforge-2026"):
        client = WireClient(backend, args.base_url, config, profile, args.timeout)
        try:
            report["profiles"][profile] = probe(client, set(args.expected_prompt))
        except Exception as exc:
            report["profiles"][profile] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            client.close()
            report["profiles"][profile].update(trace=client.trace, notifications=client.notifications,
                                               childReaped=client.process.poll() is not None)
    report["ok"] = all(p["ok"] for p in report["profiles"].values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "output": str(args.output)}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
