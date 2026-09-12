from __future__ import annotations

import json

from runtime_planner_service import (
    PlannerCatalogSnapshot,
    RuntimePlannerService,
    sanitize_planner_observation_text,
)


class _Catalog:
    def read(self, exposure_layer: str, *, project_context_active: bool = True) -> PlannerCatalogSnapshot:
        _ = exposure_layer, project_context_active
        return PlannerCatalogSnapshot()


def _service() -> RuntimePlannerService:
    # The observation formatter is intentionally tested without a model or any
    # provider call; the production service's lightweight default ports suffice.
    return RuntimePlannerService(catalog=_Catalog(), desktop=object())


def _evidence(observation: str) -> dict:
    marker = "logReadEvidence="
    assert marker in observation
    encoded = observation.split(marker, 1)[1]
    return json.JSONDecoder().raw_decode(encoded)[0]


def test_disk_log_page_keeps_file_and_paging_facts_for_next_plan() -> None:
    observation = _service()._llm_loop_step_observation(
        {
            "tool": "vrcforge_read_recent_logs",
            "status": "completed",
            "result": {
                "ok": True,
                "source": "disk",
                "file": "vrcforge_2026-09-12_22-43-35_0.log",
                "logs": [{"message": "startup"}] * 5,
                "offset": 0,
                "nextOffset": 5,
                "availableEntryCount": 10,
                "truncated": True,
            },
        }
    )
    evidence = _evidence(observation)
    assert evidence["source"] == "disk"
    assert evidence["file"] == "vrcforge_2026-09-12_22-43-35_0.log"
    assert evidence["offset"] == 0
    assert evidence["nextOffset"] == 5
    assert evidence["availableEntryCount"] == 10
    assert evidence["truncated"] is True


def test_disk_log_page_exposes_bounded_next_read_without_claiming_complete_history() -> None:
    observation = _service()._llm_loop_step_observation(
        {
            "tool": "vrcforge_read_recent_logs",
            "status": "completed",
            "result": {
                "source": "disk",
                "file": "vrcforge_2026-09-12_22-43-35_0.log",
                "logs": [{"message": "x"}] * 500,
                "offset": 100,
                "nextOffset": 600,
                "availableEntryCount": 601,
                "truncated": True,
            },
        }
    )
    evidence = _evidence(observation)
    assert evidence["observationTruncated"] is True
    assert 0 < len(evidence["logs"]) < 500
    assert evidence["nextRead"]["source"] == "disk"
    assert evidence["nextRead"]["file"] == "vrcforge_2026-09-12_22-43-35_0.log"
    assert evidence["nextRead"]["offset"] == 100 + len(evidence["logs"])
    assert isinstance(evidence["nextRead"].get("limit"), int)
    assert evidence["truncated"] is True


def test_disk_log_listing_preserves_retained_basenames_and_does_not_guess_a_file() -> None:
    observation = _service()._llm_loop_step_observation(
        {
            "tool": "vrcforge_read_recent_logs",
            "status": "completed",
            "result": {
                "source": "disk",
                "files": [
                    "vrcforge_2026-09-12_22-43-35_0.log",
                    "vrcforge_2026-09-12_22-43-36_0.log",
                ],
                "truncated": False,
            },
        }
    )
    evidence = _evidence(observation)
    assert evidence["source"] == "disk"
    assert evidence["files"] == [
        "vrcforge_2026-09-12_22-43-35_0.log",
        "vrcforge_2026-09-12_22-43-36_0.log",
    ]
    assert "nextRead" not in evidence or evidence["nextRead"] is None


def test_log_evidence_redacts_tokens_and_absolute_paths() -> None:
    observation = _service()._llm_loop_step_observation(
        {
            "tool": "vrcforge_read_recent_logs",
            "status": "completed",
            "result": {
                "source": "disk",
                "file": "vrcforge_2026-09-12_22-43-35_0.log",
                "logs": [
                    {
                        "message": "safe-startup-marker Authorization=Bearer secret-token",
                        "path": r"C:\Users\ExampleUser\AppData\Local\VRCForge\logs\backend.jsonl",
                    }
                ],
                "offset": 0,
                "nextOffset": None,
                "availableEntryCount": 1,
                "truncated": False,
            },
        }
    )
    evidence = _evidence(observation)
    assert evidence["source"] == "disk"
    assert evidence["logs"]
    assert "safe-startup-marker" in json.dumps(evidence, ensure_ascii=False)
    assert "secret-token" not in observation
    assert r"C:\Users\ExampleUser" not in observation
    assert "AppData" not in observation


def test_other_tools_do_not_receive_log_read_evidence_escape_hatch() -> None:
    observation = _service()._llm_loop_step_observation(
        {
            "tool": "vrcforge_health",
            "status": "completed",
            "result": {
                "source": "disk",
                "file": "backend.jsonl",
                "offset": 0,
                "nextOffset": 1,
            },
        }
    )
    assert "logReadEvidence=" not in observation


def test_failed_disk_log_read_requires_explicit_filename_before_retry() -> None:
    observation = _service()._llm_loop_step_observation(
        {
            "tool": "vrcforge_read_recent_logs",
            "status": "failed",
            "result": {
                "source": "disk",
                "files": ["vrcforge_2026-09-12_22-43-35_0.log"],
            },
            "outcome": {
                "status": "failed",
                "summary": "A disk log filename must be selected from the retained listing before reading.",
            },
        }
    )
    assert "source=disk" in observation
    assert "filename" in observation.lower()
    assert "logReadEvidence=" not in observation


def test_prompt_carries_log_read_evidence_to_the_next_model_request() -> None:
    prompt = _service()._build_llm_plan_prompt(
        "diagnose startup",
        [],
        [
            {
                "tool": "vrcforge_read_recent_logs",
                "status": "completed",
                "result": {
                    "source": "disk",
                    "file": "vrcforge_2026-09-12_22-43-35_0.log",
                    "logs": [{"message": "backend startup complete"}],
                    "offset": 0,
                    "nextOffset": 1,
                    "availableEntryCount": 2,
                    "truncated": True,
                },
            }
        ],
    )
    assert "logReadEvidence=" in prompt
    assert "vrcforge_2026-09-12_22-43-35_0.log" in prompt
    assert "backend startup complete" in prompt


def test_bearer_value_is_removed_without_leaking_the_suffix() -> None:
    sanitized = sanitize_planner_observation_text(
        "Authorization=Bearer secret-token; safe-startup-marker"
    )
    assert "secret-token" not in sanitized
    assert "safe-startup-marker" in sanitized
