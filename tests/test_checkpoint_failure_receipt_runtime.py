"""Exercise the production checkpoint normalizer with Core failure receipts."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


def normalize(data, code="scene_save_failed"):
    source = Path(__file__).resolve().parents[1] / "dashboard_server.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "normalize_unity_checkpoint_result")
    env = {"McpResult": SimpleNamespace, "Path": Path, "Any": object,
           "serialize_result": lambda r: r.payload,
           "_checkpoint_result_cause": lambda *args: "core"}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), env)
    result = SimpleNamespace(exit_code=1, payload={"isError": True,
        "structuredContent": {"success": False, "code": code, "data": data}})
    return env[function.name](result, Path("ExampleProject"))


def test_legacy_partial_save_failure_is_not_reported_as_no_mutation():
    receipt = normalize({"transaction": {"items": [{"status": "succeeded"},
                                                  {"status": "failed"}]}})
    assert receipt["commitState"] == "unknown"
    assert receipt["mutationStarted"] is None
    assert receipt["committed"] is None
    assert receipt["blocking"] is True


@pytest.mark.parametrize("data", [
    {"mutationStarted": True, "committed": None, "commitState": "unknown"},
    {"mutationStarted": False, "committed": False, "commitState": "not_started"},
])
def test_explicit_core_evidence_is_preserved(data):
    result = normalize(data)
    for key, value in data.items():
        assert result[key] == value
