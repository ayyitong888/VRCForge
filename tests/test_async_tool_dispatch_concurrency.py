from __future__ import annotations

import asyncio
import threading
import time

import dashboard_server
from dashboard_server import AgentToolRequest


def test_agent_tool_http_dispatch_overlaps_slow_callbacks(monkeypatch) -> None:
    entered = threading.Barrier(2)
    observed: list[str] = []

    monkeypatch.setattr(
        dashboard_server,
        "authenticate_agent_request",
        lambda *_args, **_kwargs: None,
    )

    def slow_call(name: str, params: dict, agent_name: str = "external-agent") -> dict:
        observed.append(f"start:{name}:{params['projectPath']}:{agent_name}")
        entered.wait(timeout=2)
        time.sleep(0.02)
        return {"ok": True, "projectPath": params["projectPath"]}

    monkeypatch.setattr(dashboard_server.AGENT_GATEWAY, "call_tool", slow_call)

    async def run() -> list[dict]:
        requests = [
            AgentToolRequest(
                agent_name="agent-a",
                params={"projectPath": "E:/unity/Projects/manuka FT2"},
            ),
            AgentToolRequest(
                agent_name="agent-b",
                params={
                    "projectPath": "D:/Codex/VRCForge/UnityProjects/VRCForge_SapphyHead_ManukaBody_Dogfood"
                },
            ),
        ]
        return await asyncio.gather(
            dashboard_server.call_agent_tool("slow_test", object(), requests[0]),
            dashboard_server.call_agent_tool("slow_test", object(), requests[1]),
        )

    result = asyncio.run(run())
    assert [item["projectPath"] for item in result] == [
        "E:/unity/Projects/manuka FT2",
        "D:/Codex/VRCForge/UnityProjects/VRCForge_SapphyHead_ManukaBody_Dogfood",
    ]
    assert len(observed) == 2
