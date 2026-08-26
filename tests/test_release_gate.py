from __future__ import annotations

import os
import stat
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts import release_gate


def test_frozen_known_failure_whitelist_has_exactly_thirteen_reasoned_entries() -> None:
    failures = release_gate.read_known_failures()

    assert len(failures) == 13
    assert all(nodeid.startswith("tests/test_agent_loop_p0.py::AgentLoopP0Tests::") for nodeid in failures)


def test_known_failure_whitelist_rejects_entry_without_own_reason(tmp_path: Path) -> None:
    whitelist = tmp_path / "known_failures.txt"
    whitelist.write_text("tests/test_example.py::test_failure\n", encoding="utf-8")

    try:
        release_gate.read_known_failures(whitelist)
    except ValueError as exc:
        assert "Allow reason" in str(exc)
    else:
        raise AssertionError("an unreasoned known failure was accepted")


def test_junit_failure_reader_preserves_exact_pytest_nodeids(tmp_path: Path) -> None:
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    failed = ET.SubElement(
        suite,
        "testcase",
        classname="tests.test_agent_loop_p0.AgentLoopP0Tests",
        name="test_failure[param]",
    )
    ET.SubElement(failed, "failure", message="AssertionError: 0 != 1")
    passed = ET.SubElement(
        suite,
        "testcase",
        classname="tests.test_module",
        name="test_passed",
    )
    xml_path = tmp_path / "results.xml"
    ET.ElementTree(root).write(xml_path, encoding="utf-8", xml_declaration=True)

    assert release_gate.read_pytest_failures(xml_path) == {
        "tests/test_agent_loop_p0.py::AgentLoopP0Tests::test_failure[param]"
    }
    assert passed.find("failure") is None


def test_failure_set_comparison_fails_for_both_additions_and_removals() -> None:
    expected = {"tests/test_a.py::test_a", "tests/test_b.py::test_b"}

    added = release_gate.compare_failure_sets(expected | {"tests/test_c.py::test_c"}, expected)
    removed = release_gate.compare_failure_sets({"tests/test_a.py::test_a"}, expected)
    exact = release_gate.compare_failure_sets(expected, expected)

    assert added.ok is False
    assert "tests/test_c.py::test_c" in added.output
    assert "多出來的" in added.output
    assert removed.ok is False
    assert "tests/test_b.py::test_b" in removed.output
    assert "少掉的" in removed.output
    assert exact.ok is True


def test_release_gate_stops_immediately_after_first_failed_item(capsys) -> None:
    calls: list[str] = []

    def fail() -> release_gate.StepResult:
        calls.append("fail")
        return release_gate.StepResult(False, "raw assertion output\n")

    def must_not_run() -> release_gate.StepResult:
        calls.append("late")
        return release_gate.StepResult(True, "")

    exit_code = release_gate.execute_steps((('first', fail), ('late', must_not_run)))

    assert exit_code == 1
    assert calls == ["fail"]
    output = capsys.readouterr().out
    assert "raw assertion output" in output
    assert "FAIL: first" in output
    assert "late" not in calls


def test_forbidden_scan_scope_excludes_gate_and_test_infrastructure() -> None:
    assert release_gate._is_production_python_module("runtime_queue_port.py") is True
    assert release_gate._is_production_python_module("vrcforge/domain/port.py") is True
    assert release_gate._is_production_python_module("scripts/release_gate.py") is False
    assert release_gate._is_production_python_module("tests/test_release_gate.py") is False


def test_smoke_environment_redirects_every_writable_runtime_root(tmp_path: Path) -> None:
    environment = release_gate._smoke_environment(tmp_path)

    assert Path(environment["LOCALAPPDATA"]).is_relative_to(tmp_path)
    assert Path(environment["VRCFORGE_USER_DATA_DIR"]).is_relative_to(tmp_path)
    assert Path(environment["VRCFORGE_CONFIG_DIR"]).is_relative_to(tmp_path)
    assert Path(environment["VRCFORGE_CONFIG_PATH"]).is_relative_to(tmp_path)
    assert Path(environment["VRCFORGE_LOG_DIR"]).is_relative_to(tmp_path)
    assert Path(environment["VRCFORGE_ARTIFACTS_DIR"]).is_relative_to(tmp_path)
    assert Path(environment["VRCFORGE_SETTINGS_PATH"]).is_relative_to(tmp_path)


def test_tier_two_is_tier_one_followed_by_three_release_checks() -> None:
    tier_one_names = [name for name, _action in release_gate.tier_one_steps()]
    tier_two_names = [name for name, _action in release_gate.tier_two_steps()]

    assert tier_two_names[: len(tier_one_names)] == tier_one_names
    assert tier_two_names[len(tier_one_names) :] == [
        "Unity full compile (isolated project)",
        "Tauri full build (isolated source and target)",
        "isolated backend health and route-count smoke",
    ]


def test_cleanup_directory_retries_windows_readonly_files(tmp_path: Path) -> None:
    root = tmp_path / "cleanup"
    root.mkdir()
    locked = root / "readonly-object"
    locked.write_bytes(b"fixture")
    os.chmod(locked, stat.S_IREAD)

    assert release_gate.cleanup_directory(root) == ""
    assert not root.exists()
