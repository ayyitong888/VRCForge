from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import dashboard_server
from context_compaction import COMPACTION_SCHEMA, compact_context


def _structured_payload(*, goal: str = "Ship the release") -> dict[str, object]:
    return {
        "currentGoal": goal,
        "completed": ["Implementation is complete"],
        "decisions": ["Use structured compaction"],
        "constraints": ["Never expose secrets"],
        "todo": ["Run acceptance tests"],
        "references": ["context_compaction.py"],
        "recentContext": ["The final review is pending"],
    }


def _prompt_entries(prompt: str) -> list[dict[str, str]]:
    marker = "REDACTED_ENTRIES="
    return json.loads(prompt.split(marker, 1)[1])


class ContextCompactionTests(unittest.TestCase):
    def test_full_and_fitted_boundaries_preserve_goal_and_latest_pair(self) -> None:
        history = [
            {"role": "user", "text": "original goal " + "g" * 40},
            {"role": "assistant", "text": "first answer " + "a" * 40},
            {"role": "user", "text": "middle request " + "m" * 40},
            {"role": "assistant", "text": "middle answer " + "n" * 40},
            {"role": "user", "text": "latest request " + "r" * 40},
            {"role": "assistant", "text": "latest answer " + "z" * 40},
        ]
        prompts: list[str] = []

        full = compact_context(
            history,
            summarizer=lambda prompt: prompts.append(prompt) or {"summary": "full"},
            target_tokens=10_000,
        )
        full_cost = full["estimatedInputTokens"]
        exact = compact_context(
            history,
            summarizer=lambda _prompt: {"summary": "exact"},
            target_tokens=full_cost,
        )
        fitted = compact_context(
            history,
            summarizer=lambda prompt: prompts.append(prompt) or {"summary": "fitted"},
            target_tokens=full_cost - 1,
        )

        self.assertEqual(full["fidelity"], "full")
        self.assertEqual(exact["fidelity"], "full")
        self.assertEqual(fitted["fidelity"], "fitted")
        retained = _prompt_entries(prompts[-1])
        self.assertEqual(retained[0], {"role": "user", "text": history[0]["text"]})
        self.assertEqual(retained[-2:], history[-2:])
        self.assertLess(fitted["retainedEntryCount"], fitted["entryCount"])

    def test_redacts_paths_secrets_and_avatar_ids_before_provider(self) -> None:
        raw_secret = "sk-proj-supersecret123456"
        raw_avatar = "avtr_01234567-89ab-cdef-0123-456789abcdef"
        history = [
            {
                "role": "user",
                "text": (
                    r"Inspect C:\Users\alice\AvatarProject\Assets\Avatar.prefab "
                    "/Users/alice/Unity/Avatar/Packages/manifest.json "
                    f"Bearer {raw_secret}, token=short-secret, and {raw_avatar}"
                ),
            }
        ]
        prompts: list[str] = []

        result = compact_context(
            history,
            summarizer=lambda prompt: prompts.append(prompt) or "privacy-safe summary",
        )
        serialized = json.dumps(result, ensure_ascii=False)

        self.assertNotIn("alice", prompts[0])
        self.assertNotIn(raw_secret, prompts[0])
        self.assertNotIn(raw_avatar, prompts[0])
        self.assertIn("{{project:", prompts[0])
        self.assertIn("[REDACTED_SECRET]", prompts[0])
        self.assertIn("{{avatar:", prompts[0])
        self.assertNotIn(raw_secret, serialized)
        self.assertNotIn(raw_avatar, serialized)
        self.assertNotIn("history", result)
        self.assertGreaterEqual(result["redactions"]["paths"], 2)
        self.assertEqual(result["redactions"]["secrets"], 2)
        self.assertEqual(result["redactions"]["avatarBlueprintIds"], 1)

    def test_source_digest_is_stable_and_client_digest_is_never_trusted(self) -> None:
        history = [{"role": "user", "text": r"Open C:\Users\alice\Unity\Assets\A.prefab"}]
        first = compact_context(history)
        matching = compact_context(history, source_digest=first["sourceDigest"])
        mismatch = compact_context(history, source_digest="0" * 64)
        opaque_change_token = compact_context(history, source_digest="ctx1-browser-change-token")

        self.assertEqual(first["sourceDigest"], matching["sourceDigest"])
        self.assertTrue(matching["clientDigestMatched"])
        self.assertFalse(mismatch["clientDigestMatched"])
        self.assertIsNone(opaque_change_token["clientDigestMatched"])
        self.assertEqual(mismatch["sourceDigest"], first["sourceDigest"])

    def test_project_alias_is_stable_for_multiple_files_in_the_same_project(self) -> None:
        prompts: list[str] = []
        compact_context(
            [
                {
                    "role": "user",
                    "text": (
                        r"Compare C:\Users\alice\Avatar\Assets\A.prefab with "
                        r"C:\Users\alice\Avatar\Packages\manifest.json"
                    ),
                }
            ],
            summarizer=lambda prompt: prompts.append(prompt) or "safe",
        )

        entries = _prompt_entries(prompts[0])
        aliases = [part.split("}}", 1)[0] for part in entries[0]["text"].split("{{project:")[1:]]
        self.assertEqual(len(aliases), 2)
        self.assertEqual(aliases[0], aliases[1])

    def test_malformed_empty_and_provider_failure_use_bounded_fallback(self) -> None:
        history = [{"role": "user", "text": "Keep this goal"}]
        cases = (
            (lambda _prompt: "", "empty_response"),
            (lambda _prompt: '{"currentGoal":', "schema_error"),
            (lambda _prompt: {"currentGoal": "missing required fields"}, "schema_error"),
            (lambda _prompt: (_ for _ in ()).throw(RuntimeError("provider exploded")), "provider_error"),
        )
        for summarizer, reason in cases:
            with self.subTest(reason=reason):
                result = compact_context(history, summarizer=summarizer)
                self.assertEqual(result["fidelity"], "fallback")
                self.assertEqual(result["fallbackReason"], reason)
                self.assertEqual(result["providerAttempts"], 1)
                self.assertIn("Keep this goal", result["summary"])
                self.assertLessEqual(len(result["summary"]), 6_000)

    def test_transient_provider_failure_retries_at_most_twice(self) -> None:
        attempts = 0
        delays: list[float] = []

        def fail_transiently(_prompt: str) -> str:
            nonlocal attempts
            attempts += 1
            raise ValueError("HTTP 503 temporary provider failure")

        result = compact_context(
            [{"role": "user", "text": "retry safely"}],
            summarizer=fail_transiently,
            sleep=delays.append,
        )

        self.assertEqual(attempts, 3)
        self.assertEqual(result["providerAttempts"], 3)
        self.assertEqual(len(delays), 2)
        self.assertEqual(result["fallbackReason"], "provider_transient")

    def test_non_transient_provider_failures_do_not_retry(self) -> None:
        for message, reason in (
            ("API key is invalid", "provider_auth"),
            ("HTTP 402 billing credit exhausted", "provider_credit"),
            ("HTTP 413 payload too large", "provider_size"),
        ):
            with self.subTest(reason=reason):
                attempts = 0

                def fail_once(_prompt: str) -> str:
                    nonlocal attempts
                    attempts += 1
                    raise RuntimeError(message)

                result = compact_context(
                    [{"role": "user", "text": "manual compaction must still work"}],
                    summarizer=fail_once,
                    sleep=lambda _delay: self.fail("non-transient failures must not be retried"),
                )

                self.assertEqual(attempts, 1)
                self.assertEqual(result["fallbackReason"], reason)
                self.assertEqual(result["fidelity"], "fallback")

    def test_sensitive_provider_output_is_rejected_instead_of_redacted_in_place(self) -> None:
        leaked = r"Saved to C:\Users\alice\secret\result.txt with sk-proj-leaked123456"
        result = compact_context(
            [{"role": "user", "text": "Summarize safely"}],
            summarizer=lambda _prompt: {"summary": leaked},
        )

        self.assertEqual(result["fidelity"], "fallback")
        self.assertEqual(result["fallbackReason"], "sensitive_provider_output")
        self.assertNotIn("alice", result["summary"])
        self.assertNotIn("sk-proj", result["summary"])

    def test_structured_summary_uses_requested_language(self) -> None:
        result = compact_context(
            [{"role": "user", "text": "发布版本"}],
            summarizer=lambda _prompt: _structured_payload(goal="发布版本"),
            language="zh-CN",
        )

        self.assertEqual(result["fidelity"], "full")
        self.assertIn("当前目标", result["summary"])
        self.assertIn("已完成", result["summary"])
        self.assertIn("待办", result["summary"])
        self.assertEqual(result["language"], "zh-CN")

    def test_summary_only_response_is_accepted_and_digested(self) -> None:
        result = compact_context(
            [{"role": "user", "text": "Keep compatibility"}],
            summarizer=lambda _prompt: json.dumps({"summary": "Compatible summary"}),
            provider="test-provider",
            model="test-model",
        )

        self.assertEqual(result["schema"], COMPACTION_SCHEMA)
        self.assertEqual(result["summary"], "Compatible summary")
        self.assertEqual(result["provider"], "test-provider")
        self.assertEqual(result["model"], "test-model")
        self.assertEqual(len(result["summaryDigest"]), 64)

    def test_minimum_history_that_cannot_fit_never_reaches_provider(self) -> None:
        provider_called = False

        def should_not_run(_prompt: str) -> str:
            nonlocal provider_called
            provider_called = True
            return "unsafe"

        result = compact_context(
            [
                {"role": "user", "text": "目标" * 2_000},
                {"role": "assistant", "text": "结果" * 2_000},
            ],
            summarizer=should_not_run,
            target_tokens=64,
        )

        self.assertFalse(provider_called)
        self.assertEqual(result["fidelity"], "fallback")
        self.assertEqual(result["fallbackReason"], "input_oversize")
        self.assertLessEqual(len(result["summary"]), 1_000)

    def test_standalone_phase_is_supported_for_manual_command(self) -> None:
        result = compact_context(
            [{"role": "user", "text": "Keep the manual command"}],
            phase="standalone",
        )

        self.assertEqual(result["phase"], "standalone")

    def test_fitted_provider_input_keeps_goal_and_latest_stateful_block(self) -> None:
        history = [
            {"role": "user", "text": "Goal: preserve the approved release path."},
            {"role": "assistant", "text": "Older discussion can be fitted out."},
            {"role": "user", "text": "Continue from the saved state."},
            {"role": "assistant", "text": "Latest response keeps the next action explicit."},
            {"role": "assistant", "text": "Durable approval state; approvalId=approval-42; status=pending"},
            {"role": "assistant", "text": "Durable runtime references; checkpointId=checkpoint-42"},
            {"role": "assistant", "text": "Durable sub-agent ownership; taskId=task-42; status=completed"},
        ]
        prompts: list[str] = []
        full = compact_context(history, summarizer=lambda _prompt: "full", target_tokens=10_000)
        fitted = compact_context(
            history,
            summarizer=lambda prompt: prompts.append(prompt) or "fitted",
            target_tokens=int(full["estimatedInputTokens"]) - 1,
        )

        retained = _prompt_entries(prompts[0])
        self.assertEqual(fitted["fidelity"], "fitted")
        self.assertEqual(retained[0]["text"], history[0]["text"])
        self.assertEqual([entry["text"] for entry in retained[-5:]], [item["text"] for item in history[-5:]])
        self.assertIn("approval-42", prompts[0])
        self.assertIn("checkpoint-42", prompts[0])
        self.assertIn("task-42", prompts[0])
        self.assertLess(fitted["retainedEntryCount"], fitted["entryCount"])

    def test_oversized_tool_output_uses_digest_fallback_without_provider_or_raw_payload(self) -> None:
        tool_payload = "result-value-" * 4_000
        provider_called = False

        def should_not_run(_prompt: str) -> str:
            nonlocal provider_called
            provider_called = True
            return "unsafe"

        result = compact_context(
            [
                {"role": "user", "text": "Goal: validate the install safely."},
                {"role": "tool", "text": tool_payload},
            ],
            summarizer=should_not_run,
            target_tokens=128,
        )
        serialized = json.dumps(result, ensure_ascii=False)

        self.assertFalse(provider_called)
        self.assertEqual(result["fidelity"], "fallback")
        self.assertEqual(result["fallbackReason"], "input_oversize")
        self.assertIn("Goal: validate the install safely.", result["summary"])
        self.assertNotIn(tool_payload[:128], result["summary"])
        self.assertNotIn(tool_payload[:128], serialized)
        self.assertIn("entry omitted", result["summary"])

    def test_provider_retry_success_reports_stable_metadata_and_no_raw_input(self) -> None:
        raw_secret = "sk-proj-do-not-retain-123456"
        raw_avatar = "avtr_01234567-89ab-cdef-0123-456789abcdef"
        calls = 0
        prompts: list[str] = []

        def transient_then_success(prompt: str) -> dict[str, object]:
            nonlocal calls
            calls += 1
            prompts.append(prompt)
            if calls == 1:
                raise TimeoutError("temporary timeout")
            return _structured_payload(goal="Preserve durable state")

        result = compact_context(
            [
                {
                    "role": "user",
                    "text": (
                        r"Use C:\Users\sample\Unity\Assets\Avatar.prefab with "
                        f"token={raw_secret} and {raw_avatar}."
                    ),
                },
                {"role": "assistant", "text": "Approval approval-99 and checkpoint checkpoint-99 remain pending."},
            ],
            summarizer=transient_then_success,
            provider="provider-safe",
            model="model-safe",
            sleep=lambda _delay: None,
        )
        serialized = json.dumps(result, ensure_ascii=False)

        self.assertEqual(calls, 2)
        self.assertEqual(result["providerAttempts"], 2)
        self.assertEqual(result["fidelity"], "full")
        self.assertEqual(result["provider"], "provider-safe")
        self.assertEqual(result["model"], "model-safe")
        self.assertEqual(result["entryCount"], 2)
        self.assertEqual(result["retainedEntryCount"], 2)
        self.assertEqual(len(result["summaryDigest"]), 64)
        self.assertNotIn("sample", prompts[0])
        self.assertNotIn(raw_secret, prompts[0])
        self.assertNotIn(raw_avatar, prompts[0])
        self.assertNotIn(raw_secret, serialized)
        self.assertNotIn(raw_avatar, serialized)
        self.assertNotIn("history", result)

    def test_schema_failure_falls_closed_after_fitted_input_without_leaking_provider_metadata(self) -> None:
        history = [
            {"role": "user", "text": "Goal: keep the original plan."},
            {"role": "assistant", "text": "Older discussion " + "x" * 300},
            {"role": "user", "text": "Latest request " + "y" * 300},
        ]
        full = compact_context(history, target_tokens=10_000)
        result = compact_context(
            history,
            summarizer=lambda _prompt: {"currentGoal": "missing required lists"},
            provider="Bearer unsafe-provider-token",
            model="model-safe",
            target_tokens=int(full["estimatedInputTokens"]) - 1,
        )

        self.assertEqual(result["fidelity"], "fallback")
        self.assertEqual(result["fallbackReason"], "schema_error")
        self.assertLess(result["retainedEntryCount"], result["entryCount"])
        self.assertEqual(result["provider"], "")
        self.assertEqual(result["model"], "model-safe")
        self.assertIn("Goal: keep the original plan.", result["summary"])


class ContextCompactionRouteTests(unittest.TestCase):
    @patch("dashboard_server.load_dashboard_settings")
    def test_manual_route_falls_back_without_api_key(self, mock_load_settings) -> None:
        mock_load_settings.return_value = SimpleNamespace(
            llm_provider="deepseek",
            llm_api_key="",
            llm_model="deepseek-chat",
        )
        with patch(
            "dashboard_server.build_unity_status_snapshot",
            return_value={"connected": False, "error": "mocked"},
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/compact",
                    json={
                        "history": [{"role": "user", "text": "Preserve this goal"}],
                        "trigger": "manual",
                        "phase": "standalone",
                        "language": "en",
                        "provider": "untrusted-client-provider",
                        "model": "untrusted-client-model",
                        "targetTokens": 512,
                        "realContextLimit": 8_192,
                        "sourceDigest": "bad-client-digest",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema"], COMPACTION_SCHEMA)
        self.assertEqual(payload["fidelity"], "fallback")
        self.assertEqual(payload["fallbackReason"], "provider_unavailable")
        self.assertFalse(payload["clientDigestMatched"])
        self.assertEqual(payload["provider"], "deepseek")
        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertIn("Preserve this goal", payload["summary"])


if __name__ == "__main__":
    unittest.main()
