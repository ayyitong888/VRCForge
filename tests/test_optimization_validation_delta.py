from __future__ import annotations

from optimization_validation_delta import build_optimization_validation_delta


def report(
    *,
    errors: int = 0,
    warnings: int = 0,
    suggestions: int = 0,
    gate: str = "pass",
    triangle_count: int = 72000,
    parameter_cost: int = 240,
    findings: list[dict] | None = None,
) -> dict:
    rows = findings or []
    return {
        "schema": "vrcforge.validation.v1",
        "ok": errors == 0,
        "summary": {
            "severityCounts": {
                "Error": errors,
                "Warning": warnings,
                "Suggestion": suggestions,
                "Info": 0,
                "Ignored": 0,
            },
            "findingCount": len(rows),
            "gateStatus": gate,
        },
        "gate": {"status": gate},
        "findings": rows,
        "sources": {
            "performance_pc": {
                "ok": True,
                "payload": {
                    "rank": "Poor" if triangle_count >= 70000 else "Medium",
                    "triangleCount": triangle_count,
                },
            },
            "parameters": {
                "ok": True,
                "payload": {
                    "totalEstimatedCost": parameter_cost,
                    "totalParameters": 22,
                },
            },
        },
    }


def test_delta_reports_improvement_and_exact_rollback_profile() -> None:
    before = report(
        warnings=1,
        suggestions=1,
        findings=[
            {
                "section": "Materials",
                "severity": "Warning",
                "title": "Large texture",
                "source": "materials",
            }
        ],
    )
    after = report(
        suggestions=1,
        triangle_count=68000,
        parameter_cost=212,
    )

    delta = build_optimization_validation_delta(
        {
            "optimizerTool": "optimization.lac.apply-request",
            "approvalId": "approval-1",
            "checkpointId": "checkpoint-1",
            "beforeValidation": before,
            "afterValidation": after,
            "rollbackValidation": before,
        }
    )

    assert delta["schema"] == "vrcforge.optimization.validation_delta.v1"
    assert delta["readOnly"] is True
    assert delta["noProjectWrites"] is True
    assert delta["status"] == "improved"
    assert delta["severityDelta"]["Warning"] == -1
    assert delta["findingDelta"]["removedCount"] == 1
    assert delta["profileDiff"]["pc"]["metricsDelta"]["triangles"] == -4000
    assert delta["parameterBudgetDelta"]["syncedBitsDelta"] == -28
    assert delta["parameterBudgetDelta"]["rollbackMatchesBefore"] is True
    assert delta["rollbackProof"]["matchesBeforeSeverityAndGate"] is True
    assert delta["optimizerTool"] == "optimization.lac.apply-request"
    assert delta["approvalId"] == "approval-1"
    assert delta["checkpointId"] == "checkpoint-1"


def test_delta_blocks_a_new_error_without_mutating_inputs() -> None:
    before = report()
    after = report(
        errors=1,
        gate="blocked",
        findings=[
            {
                "section": "Unity compile",
                "severity": "Error",
                "title": "Compile error",
                "source": "compile",
            }
        ],
    )
    before_snapshot = repr(before)
    after_snapshot = repr(after)

    delta = build_optimization_validation_delta(
        {"before": before, "after": after}
    )

    assert delta["ok"] is False
    assert delta["status"] == "regressed"
    assert delta["severityDelta"]["Error"] == 1
    assert delta["findingDelta"]["addedCount"] == 1
    assert delta["rollbackProof"] == {
        "provided": False,
        "matchesBeforeSeverityAndGate": False,
        "remainingFindingCount": None,
    }
    assert repr(before) == before_snapshot
    assert repr(after) == after_snapshot


def test_delta_accepts_aliases_and_bounds_finding_projection() -> None:
    before = report()
    after = report(
        warnings=60,
        findings=[
            {
                "id": f"finding-{index}",
                "section": "Materials",
                "severity": "Warning",
                "title": f"Finding {index}",
                "source": "materials",
            }
            for index in range(60)
        ],
    )

    delta = build_optimization_validation_delta(
        {
            "optimizer_tool": "optimization.meshia.simplify-apply-request",
            "before_validation": before,
            "after_validation": after,
        }
    )

    assert delta["optimizerTool"] == "optimization.meshia.simplify-apply-request"
    assert delta["findingDelta"]["addedCount"] == 60
    assert len(delta["findingDelta"]["added"]) == 50
