"""Scope measurement must fit the same bounded Core read deadline as its consumer."""
import uuid

import agent_scan_scope_client as scope


def test_bounded_scope_measurement_can_finish_after_short_bootstrap_deadline(monkeypatch, tmp_path):
    observed_deadlines = []
    editor = str(uuid.uuid4())

    class TimedOut(Exception):
        cause_code = "unity_core_timeout"

    class Core:
        def __init__(self, project, *, timeout_seconds):
            self.deadline = timeout_seconds
            observed_deadlines.append(timeout_seconds)

        def core_info(self):
            return {"scanScope": {"schema": scope.SCAN_SCOPE_SCHEMA,
                    "implementation": "scan-scope-v1", "tools": list(scope.SCAN_SCOPE_TOOLS)}}

        def call_tool(self, name, arguments):
            # Replay the measured handler duration without making a test sleep.
            if self.deadline < 7.298:
                raise TimedOut()
            data = {"schema": scope.SCAN_SCOPE_SCHEMA, "editorSession": editor,
                    "reason": "", "valid": True, "changedAssetPaths": []}
            if name == scope.CAPTURE_SCAN_SCOPE_TOOL:
                data.update(verified=True, token="scope_" + "a" * 32,
                            assetPaths=["Assets/Body.mat"], capturedAt="2026-10-18T16:20:00Z",
                            dependencyCoverage="complete")
            return {"structuredContent": {"success": True, "data": data}}

    monkeypatch.setattr(scope, "UnityMcpCoreClient", Core)
    client = scope.AgentScanScopeClient(tmp_path)
    proof = client.capture("vrcforge_scan_fx_animator", {"controllerPath": "Assets/Body.controller"})
    assert proof["verified"] is True
    assert client.validate(proof["token"])["valid"] is True
    assert observed_deadlines == [30.0, 30.0]
