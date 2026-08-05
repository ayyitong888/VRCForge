from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke_mcp_tool_trigger_matrix.py"
MATRIX = ROOT / "tests" / "fixtures" / "mcp_tool_trigger_matrix.json"


def load_smoke():
    spec = importlib.util.spec_from_file_location("mcp_tool_trigger_matrix", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def deterministic_planner(prompt: str, _tools: list[dict[str, object]]) -> dict[str, object]:
    matrix = load_smoke().load_matrix(MATRIX)
    for case in matrix["cases"]:
        if case["prompt"] == prompt:
            expected = case.get("expectedTools") or [case.get("expectedTool")]
            return {"toolCalls": [item for item in expected if item]}
    raise AssertionError("unknown fixture prompt")


def test_matrix_has_required_positive_negative_counts_and_mixed_language_cases() -> None:
    smoke = load_smoke()
    matrix = smoke.load_matrix(MATRIX)
    cases = matrix["cases"]
    assert sum(case["kind"] == "positive" for case in cases) >= 20
    assert sum(case["kind"] == "negative" for case in cases) >= 20
    assert any(any("\u4e00" <= char <= "\u9fff" for char in case["prompt"]) for case in cases)
    assert any(case["kind"] == "negative" and "vrc_" in case["prompt"] for case in cases)
    execution_cases = [case for case in cases if case["exposureLayer"] == "execution"]
    assert {case["expectedTool"] for case in execution_cases} == {
        "vrc_create_gameobject",
        "vrc_prepare_checkpoint",
    }
    assert all(
        case["expectedTool"] not in {"vrc_create_gameobject", "vrc_prepare_checkpoint"}
        for case in cases
        if case["kind"] == "positive" and case["exposureLayer"] == "planning"
    )


def test_matrix_prompts_are_utf8_chinese_not_mojibake() -> None:
    cases = load_smoke().load_matrix(MATRIX)["cases"]
    prompts = [case["prompt"] for case in cases]
    assert sum(any("\u4e00" <= char <= "\u9fff" for char in prompt) for prompt in prompts) >= 10
    assert not any(any(marker in prompt for marker in ("浣", "锛", "銆", "€")) for prompt in prompts)


def test_deterministic_planner_proves_matrix_statistics_but_is_not_real_provider_accepted() -> None:
    smoke = load_smoke()
    report = smoke.run_matrix(smoke.load_matrix(MATRIX), deterministic_planner)
    assert report["passed"] is True
    assert report["accepted"] is False
    assert report["notAcceptedReason"]
    assert report["positiveCorrectRate"] == 1.0
    assert report["negativeZeroCalls"] is True
    assert report["visibleToolsHash"]
    assert all(item["actualTools"] == item["expectedTools"] for item in report["cases"])


def test_negative_calls_and_positive_attached_call_fail_the_gate() -> None:
    smoke = load_smoke()
    matrix = smoke.load_matrix(MATRIX)

    def noisy_planner(prompt: str, _tools: list[dict[str, object]]) -> dict[str, object]:
        if "你好" in prompt:
            return {"toolCalls": ["vrc_get_gameobject"]}
        if "创建一个名为 HatAnchor" in prompt:
            return {"toolCalls": ["vrc_create_gameobject", "vrc_get_gameobject"]}
        return deterministic_planner(prompt, _tools)

    report = smoke.run_matrix(matrix, noisy_planner, planner_source="test-noisy")
    assert report["passed"] is False
    assert report["accepted"] is False
    assert report["negativeZeroCalls"] is False
    assert report["positiveCorrectRate"] < 1.0


def test_self_asserted_provider_fields_cannot_accept_the_matrix() -> None:
    smoke = load_smoke()
    matrix = smoke.load_matrix(MATRIX)
    tools = smoke.normalize_visible_tools(None, matrix)
    tools_hash = smoke.visible_tools_hash(tools)

    def receipt_planner(prompt: str, _tools: list[dict[str, object]]) -> dict[str, object]:
        result = deterministic_planner(prompt, _tools)
        result["providerEvidence"] = {
            "source": "dashboard-llm-plan",
            "provider": "acceptance-provider",
            "model": "acceptance-model",
            "selectionOnly": True,
            "toolsExecuted": False,
            "visibleToolsHash": tools_hash,
        }
        return result

    report = smoke.run_matrix(matrix, receipt_planner, visible_tools=tools)
    assert report["providerEvidenceValid"] is False
    assert report["accepted"] is False


def test_process_local_receipt_authority_validates_but_is_not_release_accepted() -> None:
    from mcp_trigger_selection import SelectionReceiptAuthority

    smoke = load_smoke()
    matrix = smoke.load_matrix(MATRIX)
    tools = smoke.normalize_visible_tools(None, matrix)
    authority = SelectionReceiptAuthority()

    def receipt_planner(prompt: str, visible_tools: list[dict[str, object]]) -> dict[str, object]:
        result = deterministic_planner(prompt, visible_tools)
        result["providerEvidence"] = authority.issue(
            prompt,
            visible_tools,
            result,
            provider="configured-provider",
            model="configured-model",
            config_digest="a" * 64,
            resolved_api_type="responses",
        )
        return result

    report = smoke.run_matrix(
        matrix,
        receipt_planner,
        visible_tools=tools,
        planner_source="dashboard-test",
        receipt_verifier=lambda prompt, visible_tools, result: authority.verify_and_consume(
            prompt,
            visible_tools,
            result,
            provider="configured-provider",
            model="configured-model",
            config_digest="a" * 64,
            resolved_api_type="responses",
        ),
        trusted_receipt_source=False,
    )
    assert report["providerEvidenceValid"] is True
    assert report["trustedReceiptSource"] is False
    assert report["accepted"] is False


@pytest.mark.parametrize(
    ("url", "token"),
    [
        ("https://127.0.0.1:8757", "token"),
        ("http://example.com:8757", "token"),
        ("http://127.0.0.1:8757/path", "token"),
        ("http://127.0.0.1:8757", ""),
    ],
)
def test_app_backend_provider_rejects_non_loopback_or_unscoped_inputs(url: str, token: str) -> None:
    smoke = load_smoke()
    with pytest.raises(ValueError):
        smoke.app_backend_provider(url, token)


def test_matrix_validation_rejects_insufficient_or_malformed_cases(tmp_path: Path) -> None:
    smoke = load_smoke()
    path = tmp_path / "bad.json"
    path.write_text('{"schema":"vrcforge.mcp_tool_trigger_matrix.v1","cases":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="at least 20"):
        smoke.load_matrix(path)
