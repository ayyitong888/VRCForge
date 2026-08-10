from __future__ import annotations

from copy import deepcopy
import json
import unittest

from agent_harness_journey import (
    JOURNEY_SCHEMA,
    JOURNEY_RECEIPT_SCHEMA,
    JourneyReceiptError,
    RuntimeJourneyReceiptAuthority,
    project_runtime_journey,
)
from agent_task_loop import AgentTaskLoop


TASK_SCHEMA = "vrcforge.agent_task_loop.v2"


def _runtime_journey() -> dict:
    read_action = "action_read_verified"
    write_action = "action_write_verified"
    capture_action = "action_capture_verified"
    visual_action = "action_visual_verified"
    visual_evidence = {"ref": "visual_capture_123", "kind": "managed_visual_capture"}
    return {
        "ok": True,
        "sessionId": "sess_real_123",
        "turnId": "turn_real_456",
        "clientTurnId": "client_real_789",
        "message": "do not place raw prompts in receipts",
        "result": {
            "path": "D:/private/avatar.unity",
            "toolResult": "raw tool output must not be signed",
        },
        "steps": [
            {
                "index": 0,
                "kind": "skill",
                "tool": "fixture_read_state",
                "status": "completed",
                "actionId": read_action,
            },
            {
                "index": 1,
                "kind": "write",
                "tool": "fixture_unity_write",
                "status": "completed",
                "actionId": write_action,
            },
            {
                "index": 2,
                "kind": "write",
                "tool": "vrcforge_capture_multi_screenshot",
                "status": "completed",
                "actionId": capture_action,
            },
            {
                "index": 3,
                "kind": "skill",
                "tool": "vrcforge_vision_audit_multi",
                "status": "completed",
                "actionId": visual_action,
            },
        ],
        "task": {
            "schema": TASK_SCHEMA,
            "status": "completed",
            "actions": [
                {
                    "actionId": read_action,
                    "kind": "skill",
                    "tool": "fixture_read_state",
                    "status": "completed",
                    "outcome": {
                        "status": "ok",
                        "summary": "read completed",
                        "verification": {"state": "not_required", "checks": []},
                    },
                },
                {
                    "actionId": write_action,
                    "kind": "write",
                    "tool": "fixture_unity_write",
                    "status": "completed",
                    "outcome": {
                        "status": "ok",
                        "summary": "write completed",
                        "verification": {"state": "passed", "checks": []},
                    },
                },
                {
                    "actionId": capture_action,
                    "kind": "write",
                    "tool": "vrcforge_capture_multi_screenshot",
                    "status": "completed",
                    "outcome": {
                        "status": "ok",
                        "summary": "managed capture completed",
                        "evidence": [visual_evidence],
                        "verification": {"state": "not_required", "checks": []},
                    },
                },
                {
                    "actionId": visual_action,
                    "kind": "skill",
                    "tool": "vrcforge_vision_audit_multi",
                    "status": "completed",
                    "outcome": {
                        "status": "ok",
                        "summary": "visual audit completed",
                        "evidence": [visual_evidence],
                        "verification": {"state": "passed", "checks": []},
                    },
                },
            ],
            "requirements": [
                {
                    "requirementId": "requirement_read",
                    "actionId": read_action,
                    "kind": "skill",
                    "tool": "fixture_read_state",
                    "verificationProfile": "canonical_tool_result",
                },
                {
                    "requirementId": "requirement_write",
                    "actionId": write_action,
                    "kind": "write",
                    "tool": "fixture_unity_write",
                    "verificationProfile": "persisted_scene_write_console",
                },
                {
                    "requirementId": "requirement_capture",
                    "actionId": capture_action,
                    "kind": "write",
                    "tool": "vrcforge_capture_multi_screenshot",
                    "verificationProfile": "canonical_tool_result",
                },
                {
                    "requirementId": "requirement_visual",
                    "actionId": visual_action,
                    "kind": "skill",
                    "tool": "vrcforge_vision_audit_multi",
                    "verificationProfile": "multi_angle_visual",
                },
            ],
        },
        "plan": {
            "planner": "llm",
            "nextStep": "done",
            "taskCompletion": {
                "schema": TASK_SCHEMA,
                "status": "completed",
                "evidenceActionIds": [read_action, write_action, capture_action, visual_action],
            },
        },
        "contextUsage": {"requestCount": 5},
    }


def _runtime_journey_with_resolved_correction() -> dict:
    response = _runtime_journey()
    failed_action = "action_write_failed"
    corrected_action = "action_write_verified"
    response["steps"].insert(
        1,
        {
            "index": 1,
            "kind": "write",
            "tool": "fixture_unity_write",
            "status": "failed",
            "actionId": failed_action,
        },
    )
    for index, step in enumerate(response["steps"]):
        step["index"] = index
    response["task"]["actions"].insert(
        1,
        {
            "actionId": failed_action,
            "kind": "write",
            "tool": "fixture_unity_write",
            "status": "superseded",
            "supersededBy": corrected_action,
            "outcome": {
                "status": "failed",
                "summary": "the first write arguments were rejected",
                "verification": {"state": "failed", "checks": []},
            },
        },
    )
    response["contextUsage"]["requestCount"] = 6
    return response


class _Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class RuntimeJourneyProjectionTests(unittest.TestCase):
    def test_projects_exact_ordered_journey_and_all_verifiers(self) -> None:
        projected = project_runtime_journey(_runtime_journey())

        self.assertEqual(projected["schema"], JOURNEY_SCHEMA)
        self.assertEqual(projected["id"], "turn_real_456")
        self.assertEqual(projected["actualToolExecutionCount"], 4)
        self.assertEqual(projected["toolExecutions"], 4)
        self.assertEqual(projected["providerRequestCount"], 5)
        self.assertEqual(projected["resultRefeedCount"], 4)
        self.assertEqual(projected["managedVisualEvidenceCount"], 1)
        self.assertEqual(projected["taskStatus"], "completed")
        self.assertEqual(projected["nextStep"], "done")
        self.assertEqual(
            projected["completedActionIds"],
            [
                "action_read_verified",
                "action_write_verified",
                "action_capture_verified",
                "action_visual_verified",
            ],
        )
        self.assertEqual(projected["evidenceActionIds"], projected["completedActionIds"])
        self.assertEqual(
            projected["verificationProfiles"],
            [
                "canonical_tool_result",
                "persisted_scene_write_console",
                "canonical_tool_result",
                "multi_angle_visual",
            ],
        )
        self.assertEqual(
            projected["verificationStates"],
            ["passed", "passed", "passed", "passed"],
        )
        self.assertEqual(
            projected["completedActions"][1]["verificationProfiles"],
            ["persisted_scene_write_console"],
        )

    def test_pre_provider_desktop_bootstrap_has_exact_count_exception(self) -> None:
        response = _runtime_journey()
        response["steps"][0]["tool"] = "vrcforge_agent_desktop_action"
        response["steps"][0]["preProvider"] = True
        response["task"]["actions"][0]["tool"] = "vrcforge_agent_desktop_action"
        response["task"]["requirements"][0]["tool"] = "vrcforge_agent_desktop_action"
        response["contextUsage"]["requestCount"] = 4

        projected = project_runtime_journey(response)

        self.assertEqual(projected["preProviderBootstrapCount"], 1)
        self.assertEqual(projected["providerRequestCount"], 4)
        invalid = deepcopy(response)
        invalid["steps"][1]["preProvider"] = True
        with self.assertRaises(JourneyReceiptError) as raised:
            project_runtime_journey(invalid)
        self.assertEqual(raised.exception.code, "journey_provider_order_invalid")

    def test_visual_verifier_requires_prior_matching_managed_capture(self) -> None:
        response = _runtime_journey()
        response["task"]["actions"][-1]["outcome"]["evidence"] = [
            {"ref": "visual_capture_from_other_task", "kind": "managed_visual_capture"}
        ]

        with self.assertRaises(JourneyReceiptError) as raised:
            project_runtime_journey(response)

        self.assertEqual(raised.exception.code, "journey_visual_evidence_mismatch")

    def test_visual_profile_requires_the_first_party_visual_audit_tool(self) -> None:
        response = _runtime_journey()
        response["steps"][-1]["tool"] = "fixture_fake_visual_verifier"
        response["task"]["actions"][-1]["tool"] = "fixture_fake_visual_verifier"
        response["task"]["requirements"][-1]["tool"] = "fixture_fake_visual_verifier"

        with self.assertRaises(JourneyReceiptError) as raised:
            project_runtime_journey(response)

        self.assertEqual(raised.exception.code, "journey_visual_verifier_invalid")

    def test_accepts_resolved_superseded_action_without_using_it_as_evidence(self) -> None:
        projected = project_runtime_journey(_runtime_journey_with_resolved_correction())

        self.assertEqual(projected["actualToolExecutionCount"], 5)
        self.assertEqual(projected["resultRefeedCount"], 5)
        self.assertEqual(projected["supersededActionIds"], ["action_write_failed"])
        self.assertNotIn("action_write_failed", projected["completedActionIds"])
        self.assertNotIn("action_write_failed", projected["evidenceActionIds"])
        self.assertEqual(
            projected["verificationProfiles"],
            [
                "canonical_tool_result",
                "persisted_scene_write_console",
                "canonical_tool_result",
                "multi_angle_visual",
            ],
        )

    def test_rejects_unresolved_or_cross_tool_superseded_action(self) -> None:
        cases = []
        missing_target = _runtime_journey_with_resolved_correction()
        missing_target["task"]["actions"][1]["supersededBy"] = "action_missing"
        cases.append(missing_target)
        wrong_tool = _runtime_journey_with_resolved_correction()
        wrong_tool["task"]["actions"][1]["tool"] = "fixture_other_write"
        wrong_tool["steps"][1]["tool"] = "fixture_other_write"
        cases.append(wrong_tool)
        false_supersession = _runtime_journey_with_resolved_correction()
        false_supersession["task"]["actions"][1]["outcome"]["status"] = "ok"
        cases.append(false_supersession)

        for response in cases:
            with self.subTest(response=response["task"]["actions"][1]):
                with self.assertRaises(JourneyReceiptError) as raised:
                    project_runtime_journey(response)
                self.assertEqual(raised.exception.code, "journey_superseded_invalid")

    def test_rejects_superseded_action_as_completion_evidence(self) -> None:
        response = _runtime_journey_with_resolved_correction()
        response["plan"]["taskCompletion"]["evidenceActionIds"].insert(
            1,
            "action_write_failed",
        )

        with self.assertRaises(JourneyReceiptError) as raised:
            project_runtime_journey(response)

        self.assertEqual(raised.exception.code, "journey_evidence_mismatch")

    def test_rejects_missing_real_runtime_identity(self) -> None:
        for field in ("sessionId", "turnId", "clientTurnId"):
            with self.subTest(field=field):
                response = _runtime_journey()
                response.pop(field)
                with self.assertRaisesRegex(JourneyReceiptError, field):
                    project_runtime_journey(response)

    def test_rejects_zero_later_provider_sample(self) -> None:
        response = _runtime_journey()
        response["contextUsage"]["requestCount"] = 3

        with self.assertRaises(JourneyReceiptError) as raised:
            project_runtime_journey(response)

        self.assertEqual(raised.exception.code, "journey_provider_resample_missing")

    def test_rejects_missing_action_identity(self) -> None:
        response = _runtime_journey()
        response["steps"][0]["actionId"] = ""

        with self.assertRaises(JourneyReceiptError) as raised:
            project_runtime_journey(response)

        self.assertEqual(raised.exception.code, "journey_identity_missing")

    def test_rejects_out_of_order_actions(self) -> None:
        response = _runtime_journey()
        response["steps"].reverse()
        for index, step in enumerate(response["steps"]):
            step["index"] = index

        with self.assertRaises(JourneyReceiptError) as raised:
            project_runtime_journey(response)

        self.assertEqual(raised.exception.code, "journey_action_identity_mismatch")

    def test_rejects_evidence_mismatch(self) -> None:
        response = _runtime_journey()
        response["plan"]["taskCompletion"]["evidenceActionIds"] = [
            "action_visual_verified",
            "action_write_verified",
        ]

        with self.assertRaises(JourneyReceiptError) as raised:
            project_runtime_journey(response)

        self.assertEqual(raised.exception.code, "journey_evidence_mismatch")

    def test_rejects_all_canonical_verification(self) -> None:
        response = _runtime_journey()
        for requirement in response["task"]["requirements"]:
            requirement["verificationProfile"] = "canonical_tool_result"

        with self.assertRaises(JourneyReceiptError) as raised:
            project_runtime_journey(response)

        self.assertEqual(raised.exception.code, "journey_verification_canonical_only")

    def test_rejects_ambiguous_multi_profile_action(self) -> None:
        response = _runtime_journey()
        extra = deepcopy(response["task"]["requirements"][0])
        extra["requirementId"] = "requirement_write_visual"
        extra["verificationProfile"] = "multi_angle_visual"
        response["task"]["requirements"].append(extra)

        with self.assertRaises(JourneyReceiptError) as raised:
            project_runtime_journey(response)

        self.assertEqual(raised.exception.code, "journey_verification_ambiguous")

    def test_rejects_unbound_declared_verifier(self) -> None:
        response = _runtime_journey()
        response["task"]["requirements"].append(
            {
                "requirementId": "requirement_unbound",
                "actionId": "action_missing",
                "kind": "write",
                "tool": "fixture_missing",
                "verificationProfile": "persisted_scene_write_console",
            }
        )

        with self.assertRaises(JourneyReceiptError) as raised:
            project_runtime_journey(response)

        self.assertEqual(raised.exception.code, "journey_verification_undeclared")

    def test_rejects_failed_and_needs_user_action(self) -> None:
        for status in ("failed", "needs_user_action"):
            with self.subTest(status=status):
                response = _runtime_journey()
                response["task"]["status"] = status
                response["task"]["actions"][0]["status"] = status
                response["task"]["actions"][0]["outcome"]["status"] = status
                with self.assertRaises(JourneyReceiptError) as raised:
                    project_runtime_journey(response)
                self.assertIn(
                    raised.exception.code,
                    {"journey_not_completed", "journey_action_failed"},
                )

    def test_rejects_failed_declared_verifier(self) -> None:
        response = _runtime_journey()
        response["task"]["actions"][1]["outcome"]["verification"]["state"] = "failed"

        with self.assertRaises(JourneyReceiptError) as raised:
            project_runtime_journey(response)

        self.assertEqual(raised.exception.code, "journey_verification_failed")

    def test_real_task_loop_projects_capture_and_visual_verifier_profiles(self) -> None:
        loop = AgentTaskLoop(
            "capture and visually verify the avatar",
            session_id="session-real-visual",
            turn_id="turn-real-visual",
            client_turn_id="client-real-visual",
        )
        evidence = [{"ref": "visual-real-1", "kind": "managed_visual_capture"}]
        capture_arguments = {"angles": ["front", "back"]}
        capture_requirement = loop.require_action(
            kind="write",
            tool="vrcforge_capture_multi_screenshot",
            arguments=capture_arguments,
        )
        loop.record_action(
            kind="write",
            tool="vrcforge_capture_multi_screenshot",
            arguments=capture_arguments,
            raw_result={"ok": True},
            outcome={"status": "ok", "summary": "captured", "evidence": evidence},
            action_id=capture_requirement["actionId"],
        )
        visual_arguments = {"captureReceipt": "opaque"}
        visual_requirement = loop.require_action(
            kind="skill",
            tool="vrcforge_vision_audit_multi",
            arguments=visual_arguments,
        )
        loop.record_action(
            kind="skill",
            tool="vrcforge_vision_audit_multi",
            arguments=visual_arguments,
            raw_result={
                "ok": True,
                "visualVerified": True,
                "coverageComplete": True,
                "captureEvidenceVerified": True,
            },
            outcome={"status": "ok", "summary": "visually verified", "evidence": evidence},
            action_id=visual_requirement["actionId"],
        )
        completed_action_ids = loop.completed_action_ids()
        plan = loop.gate_terminal(
            {
                "planner": "llm",
                "nextStep": "done",
                "reply": "verified",
                "completionClaim": {
                    "satisfied": True,
                    "evidenceActionIds": completed_action_ids,
                },
            }
        )
        response = {
            "ok": True,
            "sessionId": loop.session_id,
            "turnId": loop.turn_id,
            "clientTurnId": loop.client_turn_id,
            "steps": loop.historical_steps(),
            "task": plan["task"],
            "plan": plan,
            "contextUsage": {"requestCount": 3},
        }

        journey = project_runtime_journey(response)

        self.assertEqual(
            journey["verificationProfiles"],
            ["canonical_tool_result", "multi_angle_visual"],
        )
        self.assertEqual(journey["managedVisualEvidenceCount"], 1)


class RuntimeJourneyReceiptAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.secret = b"journey-test-secret-that-is-at-least-32-bytes"
        self.authority = RuntimeJourneyReceiptAuthority(
            secret=self.secret,
            clock=self.clock,
            default_ttl_seconds=10,
        )

    def test_issue_verify_once_and_omit_raw_runtime_material(self) -> None:
        receipt = self.authority.issue(_runtime_journey())

        self.assertEqual(receipt["schema"], JOURNEY_RECEIPT_SCHEMA)
        serialized = json.dumps(receipt, sort_keys=True)
        self.assertNotIn("do not place raw prompts", serialized)
        self.assertNotIn("D:/private/avatar.unity", serialized)
        self.assertNotIn("raw tool output", serialized)
        self.assertNotIn(self.secret.decode("ascii"), serialized)

        verified = self.authority.verify(receipt)
        self.assertEqual(verified, receipt["journey"])
        with self.assertRaises(JourneyReceiptError) as raised:
            self.authority.verify(receipt)
        self.assertEqual(raised.exception.code, "receipt_replayed")

    def test_rejects_tampered_receipt(self) -> None:
        receipt = self.authority.issue(_runtime_journey())
        receipt["journey"]["providerRequestCount"] = 99

        with self.assertRaises(JourneyReceiptError) as raised:
            self.authority.verify(receipt)

        self.assertEqual(raised.exception.code, "receipt_invalid")

    def test_rejects_expired_receipt(self) -> None:
        receipt = self.authority.issue(_runtime_journey(), ttl_seconds=2)
        self.clock.now += 3

        with self.assertRaises(JourneyReceiptError) as raised:
            self.authority.verify(receipt)

        self.assertEqual(raised.exception.code, "receipt_expired")

    def test_rejects_receipt_from_another_authority(self) -> None:
        receipt = self.authority.issue(_runtime_journey())
        other = RuntimeJourneyReceiptAuthority(secret=self.secret, clock=self.clock)

        with self.assertRaises(JourneyReceiptError) as raised:
            other.verify(receipt)

        self.assertEqual(raised.exception.code, "receipt_wrong_authority")


if __name__ == "__main__":
    unittest.main()
