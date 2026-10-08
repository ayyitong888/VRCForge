import ast
from copy import deepcopy
from pathlib import Path
import uuid

import pytest

import wardrobe_outfit_workflow_service as wardrobe


def composed_reader(scan):
    tree = ast.parse(Path(__file__).resolve().parents[1].joinpath("dashboard_server.py").read_text(encoding="utf-8"))
    root = next(node for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "WARDROBE_ARTIFACT_READ"
                        for target in node.targets))
    namespace = {**vars(wardrobe), "run_unity_artifact_scan_sync": scan}
    return eval(compile(ast.Expression(root.value), "dashboard-read-composition", "eval"), namespace)


@pytest.mark.parametrize("limit_key", ["maxItems", "max_items"])
def test_public_avatar_item_scan_forwards_paging_and_guard_without_write_options(limit_key):
    digest = uuid.uuid4().hex + uuid.uuid4().hex
    params = {"avatarPath": "Avatar-" + uuid.uuid4().hex, limit_key: 37,
              "offset": 73, "expectedSnapshotDigest": digest,
              "outputPath": "ignored.json", "refreshAssets": True}
    before = deepcopy(params)
    calls = []

    def scan(original, tool, prefix, arguments, label):
        calls.append((original, tool, prefix, arguments, label))
        return {"ok": True, "paging": {"offset": arguments.get("offset", 0)}}

    result = composed_reader(scan).scan_avatar_items(params)
    assert result["paging"]["offset"] == 73
    assert calls == [(params, "vrc_scan_avatar_items", "avatar_items",
                      {"maxItems": 37, "refreshAssets": False, "offset": 73,
                       "expectedSnapshotDigest": digest}, "avatar item scan")]
    assert params == before


def test_public_avatar_item_first_page_keeps_existing_defaults():
    calls = []
    reader = composed_reader(lambda *args: calls.append(args) or {"ok": True})
    assert reader.scan_avatar_items({}) == {"ok": True}
    assert calls[0][3] == {"maxItems": 2000, "refreshAssets": False}
