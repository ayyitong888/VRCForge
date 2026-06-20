from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


DEFAULT_BASE_URL = "http://127.0.0.1:8757"
DEFAULT_SERVER_NAME = "VRCForge Agent Bridge"
HIDDEN_EXTERNAL_TOOLS = {"vrcforge_apply_approved", "vrcforge_execute_approved_shell"}


def main() -> int:
    args = parse_args()
    bridge = VRCForgeBridge(
        base_url=args.base_url.rstrip("/"),
        config_path=Path(args.config).expanduser().resolve() if args.config else None,
        timeout_seconds=args.timeout,
        start_runtime=not args.no_start,
    )
    if args.preflight:
        print(json.dumps(bridge.preflight(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    run_stdio_server(bridge)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the VRCForge external-agent stdio MCP bridge.")
    parser.add_argument("--base-url", default=os.environ.get("VRCFORGE_AGENT_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--config", default=os.environ.get("VRCFORGE_AGENT_GATEWAY_CONFIG", ""))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("VRCFORGE_AGENT_TIMEOUT", "30")))
    parser.add_argument("--no-start", action="store_true", help="Do not launch VRCForge if the runtime is offline.")
    parser.add_argument("--preflight", action="store_true", help="Print a JSON preflight report and exit.")
    parser.add_argument("--json", action="store_true", help="Compatibility flag; preflight already prints JSON.")
    return parser.parse_args()


class VRCForgeBridge:
    def __init__(
        self,
        *,
        base_url: str,
        config_path: Path | None,
        timeout_seconds: float,
        start_runtime: bool,
    ) -> None:
        self.base_url = base_url
        self.config_path = config_path
        self.timeout_seconds = timeout_seconds
        self.start_runtime = start_runtime

    def preflight(self) -> dict[str, Any]:
        config_path = self.resolve_config_path()
        config = read_json_file(config_path)
        token = str(os.environ.get("VRCFORGE_AGENT_TOKEN") or config.get("token") or "")
        report: dict[str, Any] = {
            "ok": False,
            "schema": "vrcforge.external_agent_bridge.preflight.v1",
            "baseUrl": self.base_url,
            "configPath": str(config_path) if config_path else "",
            "configFound": bool(config_path and config_path.is_file()),
            "tokenSource": "env" if os.environ.get("VRCFORGE_AGENT_TOKEN") else "config" if token else "missing",
            "tokenConfigured": bool(token),
            "gatewayEnabled": bool(config.get("enabled")),
            "allowWriteRequests": bool(config.get("allow_write_requests", True)),
            "runtimeOnline": False,
            "manifestToolCount": 0,
            "advertisesRequestApply": False,
            "advertisesDirectApply": False,
            "error": "",
        }
        if not token:
            report["error"] = "VRCForge Agent Gateway token was not found."
            return report

        if self.start_runtime and not self.runtime_port_open():
            launch = self.try_launch_runtime()
            report["launch"] = launch

        try:
            manifest = self.request_json("GET", "/api/agent/manifest", token=token, allow_http_error=False)
            tools = manifest.get("tools") if isinstance(manifest, dict) else []
            tool_names = {str(tool.get("name") or "") for tool in tools if isinstance(tool, dict)}
            report["runtimeOnline"] = True
            report["gatewayEnabled"] = bool(manifest.get("enabled"))
            report["allowWriteRequests"] = bool(manifest.get("allowWriteRequests"))
            report["manifestToolCount"] = len(tool_names)
            report["advertisesRequestApply"] = "vrcforge_request_apply" in tool_names
            report["advertisesDirectApply"] = bool(HIDDEN_EXTERNAL_TOOLS & tool_names)
            report["ok"] = (
                bool(manifest.get("enabled"))
                and bool(manifest.get("allowWriteRequests"))
                and "vrcforge_request_apply" in tool_names
                and not bool(HIDDEN_EXTERNAL_TOOLS & tool_names)
            )
            if not report["ok"]:
                report["error"] = "Gateway is reachable, but external-agent write-request contract is not ready."
        except Exception as exc:  # noqa: BLE001 - preflight should report actionable failure instead of crashing.
            report["error"] = str(exc)
        return report

    def call_tool(self, tool_name: str, params: dict[str, Any] | None = None, agent_name: str = "external-stdio-agent") -> dict[str, Any]:
        if tool_name in HIDDEN_EXTERNAL_TOOLS:
            return {"ok": False, "error": f"{tool_name} is internal to VRCForge approval execution."}
        token = self.require_token()
        return self.request_json(
            "POST",
            f"/api/agent/tool/{tool_name}",
            token=token,
            payload={"agent_name": agent_name, "params": params or {}},
        )

    def manifest(self) -> dict[str, Any]:
        token = self.require_token()
        return self.request_json("GET", "/api/agent/manifest", token=token)

    def require_token(self) -> str:
        config_path = self.resolve_config_path()
        config = read_json_file(config_path)
        token = str(os.environ.get("VRCFORGE_AGENT_TOKEN") or config.get("token") or "")
        if not token:
            raise RuntimeError("VRCForge Agent Gateway token was not found.")
        return token

    def resolve_config_path(self) -> Path | None:
        if self.config_path is not None:
            return self.config_path
        candidates: list[Path] = []
        user_data = os.environ.get("VRCFORGE_USER_DATA_DIR", "").strip()
        if user_data:
            candidates.append(Path(user_data) / "config" / "agent_gateway.json")
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            candidates.append(Path(local_app_data) / "VRCForge" / "agentic-app" / "config" / "agent_gateway.json")
        candidates.append(Path.cwd() / "agent_gateway.json")
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        return candidates[0].resolve() if candidates else None

    def runtime_port_open(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", url_port(self.base_url)), timeout=0.35):
                return True
        except OSError:
            return False

    def try_launch_runtime(self) -> dict[str, Any]:
        exe = find_vrcforge_executable()
        if not exe:
            return {"ok": False, "error": "VRCForge.exe was not found. Start VRCForge Desktop, then retry."}
        try:
            subprocess.Popen(
                [str(exe)],
                cwd=str(exe.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        except OSError as exc:
            return {"ok": False, "path": str(exe), "error": str(exc)}
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if self.runtime_port_open():
                return {"ok": True, "path": str(exe)}
            time.sleep(0.25)
        return {"ok": False, "path": str(exe), "error": "VRCForge runtime did not open its loopback port in time."}

    def request_json(
        self,
        method: str,
        path: str,
        *,
        token: str,
        payload: dict[str, Any] | None = None,
        allow_http_error: bool = True,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - loopback-only URL.
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            if not allow_http_error:
                raise RuntimeError(f"HTTP {exc.code} from {path}: {text}") from exc
            return {"ok": False, "status": exc.code, "error": text}
        return json.loads(text or "{}")


def run_stdio_server(bridge: VRCForgeBridge) -> None:
    preflight = bridge.preflight()
    mcp = FastMCP(
        DEFAULT_SERVER_NAME,
        instructions=(
            "Use VRCForge for supervised VRChat avatar work. Read and preview tools run through "
            "the local VRCForge gateway. Writes must be requested with vrcforge_request_apply; "
            "VRCForge Desktop owns approval, checkpoint, apply, validation, and rollback."
        ),
    )

    @mcp.tool(name="vrcforge_bridge_preflight")
    async def bridge_preflight() -> dict[str, Any]:
        return bridge.preflight()

    if preflight.get("runtimeOnline"):
        try:
            manifest = bridge.manifest()
        except Exception:
            manifest = {}
        tools = manifest.get("tools") if isinstance(manifest, dict) else []
        for item in tools:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name in HIDDEN_EXTERNAL_TOOLS:
                continue
            register_proxy_tool(mcp, bridge, name, str(item.get("description") or name))

    mcp.run("stdio")


def register_proxy_tool(mcp: FastMCP, bridge: VRCForgeBridge, tool_name: str, description: str) -> None:
    async def proxy(params: dict[str, Any] | None = None, agent_name: str = "external-stdio-agent") -> dict[str, Any]:
        return bridge.call_tool(tool_name, params or {}, agent_name=agent_name)

    proxy.__name__ = f"proxy_{tool_name}"
    mcp.tool(name=tool_name, description=description)(proxy)


def read_json_file(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def url_port(base_url: str) -> int:
    marker = "://"
    host_part = base_url.split(marker, 1)[1] if marker in base_url else base_url
    host_part = host_part.split("/", 1)[0]
    if ":" in host_part:
        return int(host_part.rsplit(":", 1)[1])
    return 80


def find_vrcforge_executable() -> Path | None:
    env_path = os.environ.get("VRCFORGE_EXE", "").strip()
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path))
    program_files = [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")]
    for base in program_files:
        if base:
            candidates.append(Path(base) / "VRCForge" / "VRCForge.exe")
    root = Path(__file__).resolve().parents[1]
    candidates.extend(
        [
            root / "VRCForge.exe",
            root / "dist" / "VRCForge_Windows_x64" / "VRCForge.exe",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


if __name__ == "__main__":
    raise SystemExit(main())
