from pathlib import Path
import hashlib

import json

import pytest

from asset_text_resources import publish_asset_text
from mcp_resource_registry import McpResourceRegistry
from jsonschema import Draft202012Validator
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS


def _project(tmp_path: Path, content: str) -> tuple[Path, dict]:
    root = tmp_path / "Project"
    asset = root / "Assets" / "lilToonSetting" / "lil_setting.hlsl"
    asset.parent.mkdir(parents=True)
    asset.write_text(content, encoding="utf-8")
    return root, {"assetPath": "Assets/lilToonSetting/lil_setting.hlsl", "guid": "a" * 32,
                  "assetType": "UnityEngine.TextAsset", "dependencyHash": "b" * 32}


def _embedded_shader_project(tmp_path: Path, *, manifest_name: str = "jp.lilxyzw.liltoon") -> tuple[Path, dict]:
    root = tmp_path / "Project"
    package = root / "Packages" / "jp.lilxyzw.liltoon"
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({"name": manifest_name, "version": "2.3.4"}), encoding="utf-8"
    )
    asset = package / "Shader" / "Includes" / "lil_common_frag_alpha.hlsl"
    asset.parent.mkdir(parents=True)
    asset.write_text("float alpha = 1.0;\n", encoding="utf-8")
    return root, {
        "assetPath": "Packages/jp.lilxyzw.liltoon/Shader/Includes/lil_common_frag_alpha.hlsl",
        "guid": "c" * 32,
        "assetType": "UnityEditor.ShaderInclude",
        "dependencyHash": "d" * 32,
    }


def _embedded_monoscript_project(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "Project"
    package = root / "Packages" / "jp.lilxyzw.liltoon"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": "jp.lilxyzw.liltoon", "version": "2.3.4"}), encoding="utf-8")
    asset = package / "Editor" / "lilShaderContainerImporter.cs"
    asset.parent.mkdir(parents=True)
    asset.write_text("namespace Lil { class Importer {} }\n// Shader Source\n", encoding="utf-8")
    return root, {"assetPath": "Packages/jp.lilxyzw.liltoon/Editor/lilShaderContainerImporter.cs",
                  "guid": "f" * 32, "assetType": "UnityEditor.MonoScript", "dependencyHash": "e" * 32}


def test_text_capture_reports_hash_lines_and_full_immutable_resource(tmp_path):
    root, info = _project(tmp_path, "#define LIL_FEATURE_DissolveNoiseMask\nfloat x;\n")
    registry = McpResourceRegistry(tmp_path / "resources")
    result = publish_asset_text(registry, {"projectPath": str(root), "includeText": True}, info)
    assert result["complete"] is True
    assert result["truncated"] is False
    assert result["sourceComplete"] is True
    assert result["sourceSha256"] == result["sha256"]
    assert result["textSha256"]
    assert result["totalLines"] == 2
    assert result["resourceUri"].startswith("vrcforge://unity-asset-text/")
    stored = registry.read(result["resourceUri"])["structuredContent"]
    expected = (root / "Assets" / "lilToonSetting" / "lil_setting.hlsl").read_bytes().decode("utf-8-sig")
    assert "".join(c["text"] for c in stored["data"]["chunks"]) == expected
    assert stored["data"]["sha256"] == result["sha256"]


def test_text_capture_is_bounded_and_line_selective_with_complete_resource(tmp_path):
    root, info = _project(tmp_path, "one\ntwo\nthree\n")
    registry = McpResourceRegistry(tmp_path / "resources")
    result = publish_asset_text(registry, {
        "projectPath": str(root), "includeText": True, "textStartLine": 2,
        "textEndLine": 3, "textMaxBytes": 3,
    }, info)
    assert result["text"] == "two"
    assert result["truncated"] is True
    assert result["rangeComplete"] is False
    assert result["sourceComplete"] is False
    assert result["resourceSourceComplete"] is True
    assert result["nextLine"] is None
    source_text = (root / info["assetPath"]).read_bytes().decode("utf-8-sig")
    assert result["nextByteOffset"] == len(source_text.splitlines(keepends=True)[0].encode("utf-8")) + 3
    stored = registry.read(result["resourceUri"])["structuredContent"]
    assert "".join(c["text"] for c in stored["data"]["chunks"]) == source_text
    assert stored["data"]["totalLines"] == 3


def test_text_capture_rejects_binary_and_missing_explicit_project(tmp_path):
    root, info = _project(tmp_path, "ok")
    (root / "Assets" / "binary.bin").write_bytes(b"a\x00b")
    binary = dict(info, assetPath="Assets/binary.bin")
    with pytest.raises(ValueError, match="Binary"):
        publish_asset_text(McpResourceRegistry(tmp_path / "r1"), {"projectPath": str(root)}, binary)
    with pytest.raises(ValueError, match="absolute projectPath"):
        publish_asset_text(McpResourceRegistry(tmp_path / "r2"), {}, info)


def test_embedded_upm_shader_include_reads_with_core_identity_and_full_resource(tmp_path):
    root, info = _embedded_shader_project(tmp_path)
    registry = McpResourceRegistry(tmp_path / "upm")
    result = publish_asset_text(registry, {"projectPath": str(root), "includeText": True}, info)
    assert result["complete"] is True
    assert result["text"] == "float alpha = 1.0;\r\n"
    assert result["resourceSourceComplete"] is True
    stored = registry.read(result["resourceUri"])["structuredContent"]["data"]
    assert "".join(chunk["text"] for chunk in stored["chunks"]) == result["text"]
    assert stored["sha256"] == result["sha256"]


def test_embedded_upm_monoscript_reads_hash_and_search_with_core_identity(tmp_path):
    root, info = _embedded_monoscript_project(tmp_path)
    result = publish_asset_text(McpResourceRegistry(tmp_path / "monoscript"), {
        "projectPath": str(root), "includeText": True,
        "textSearch": {"literal": "Shader Source", "maxMatches": 1},
    }, info)
    raw = (root / info["assetPath"]).read_bytes()
    assert result["sourceSha256"] == hashlib.sha256(raw).hexdigest()
    assert result["textSha256"] == hashlib.sha256(raw.decode("utf-8-sig").encode("utf-8")).hexdigest()
    assert result["textSearch"]["matchCount"] == 1


@pytest.mark.parametrize(
    ("asset_type", "asset_path", "message"),
    [("UnityEditor.MonoScript", "Packages/jp.lilxyzw.liltoon/Editor/bad.hlsl", "MonoScript"),
     ("UnityEditor.ShaderInclude", "Packages/jp.lilxyzw.liltoon/Editor/lilShaderContainerImporter.cs", "shader include"),
     ("UnityEngine.TextAsset", "Packages/jp.lilxyzw.liltoon/Editor/lilShaderContainerImporter.cs", "assetType")],
)
def test_embedded_upm_monoscript_suffix_and_type_cannot_be_confused(tmp_path, asset_type, asset_path, message):
    root, info = _embedded_monoscript_project(tmp_path)
    info.update(assetType=asset_type, assetPath=asset_path)
    with pytest.raises(ValueError, match=message):
        publish_asset_text(McpResourceRegistry(tmp_path / "reject"), {"projectPath": str(root)}, info)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda info: info.pop("guid"), "Core asset GUID"),
        (lambda info: info.update(assetType="UnityEngine.TextAsset"), "assetType"),
        (lambda info: info.update(assetPath="Packages/jp.lilxyzw.liltoon/Shader/Includes/missing.hlsl"), "unavailable"),
        (lambda info: info.update(assetPath="Library/PackageCache/jp.lilxyzw.liltoon/Shader/Includes/x.hlsl"), "Assets path"),
    ],
)
def test_embedded_upm_shader_include_rejects_missing_or_wrong_core_identity(tmp_path, mutator, message):
    root, info = _embedded_shader_project(tmp_path)
    mutator(info)
    with pytest.raises(ValueError, match=message):
        publish_asset_text(McpResourceRegistry(tmp_path / "reject"), {"projectPath": str(root)}, info)


def test_embedded_upm_shader_include_rejects_non_embedded_manifest_identity(tmp_path):
    root, info = _embedded_shader_project(tmp_path, manifest_name="com.other.shader")
    with pytest.raises(ValueError, match="does not match"):
        publish_asset_text(McpResourceRegistry(tmp_path / "reject"), {"projectPath": str(root)}, info)


def test_embedded_upm_shader_include_rejects_traversal_and_keeps_meta_assets_only(tmp_path):
    root, info = _embedded_shader_project(tmp_path)
    traversal = dict(info, assetPath="Packages/jp.lilxyzw.liltoon/../other/x.hlsl")
    with pytest.raises(ValueError, match="Assets path"):
        publish_asset_text(McpResourceRegistry(tmp_path / "traversal"), {"projectPath": str(root)}, traversal)
    with pytest.raises(ValueError, match="Assets path"):
        publish_asset_text(
            McpResourceRegistry(tmp_path / "meta"),
            {"projectPath": str(root), "includeMetaText": True},
            info,
        )


def test_embedded_upm_shader_include_rejects_reparse_packages_parent_when_supported(tmp_path):
    outside = tmp_path / "outside"
    package = outside / "jp.lilxyzw.liltoon"
    (package / "Shader" / "Includes").mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({"name": "jp.lilxyzw.liltoon", "version": "2.3.4"}), encoding="utf-8"
    )
    (package / "Shader" / "Includes" / "x.hlsl").write_text("float x;\n", encoding="utf-8")
    root = tmp_path / "Project"
    root.mkdir()
    try:
        (root / "Packages").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    info = {
        "assetPath": "Packages/jp.lilxyzw.liltoon/Shader/Includes/x.hlsl",
        "guid": "e" * 32, "assetType": "UnityEditor.ShaderInclude",
    }
    with pytest.raises(ValueError, match="symlink or reparse"):
        publish_asset_text(McpResourceRegistry(tmp_path / "reparse-parent"), {"projectPath": str(root)}, info)


def test_embedded_upm_shader_include_rejects_reparse_manifest_when_supported(tmp_path):
    root, info = _embedded_shader_project(tmp_path)
    manifest = root / "Packages" / "jp.lilxyzw.liltoon" / "package.json"
    outside = tmp_path / "manifest.json"
    outside.write_text(json.dumps({"name": "jp.lilxyzw.liltoon", "version": "2.3.4"}), encoding="utf-8")
    manifest.unlink()
    try:
        manifest.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"file symlink unavailable: {exc}")
    with pytest.raises(ValueError, match="symlink or reparse"):
        publish_asset_text(McpResourceRegistry(tmp_path / "reparse-manifest"), {"projectPath": str(root)}, info)


def test_public_schema_requires_project_for_text_and_bounds_line_options():
    schema = UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_get_asset_info"]
    assert list(Draft202012Validator(schema).iter_errors({"assetPath": "Assets/a.hlsl", "includeText": True}))
    Draft202012Validator(schema).validate({
        "projectPath": "D:/Project", "assetPath": "Assets/a.hlsl", "includeText": True,
        "textMaxBytes": 65536, "textStartLine": 1, "textEndLine": 2,
    })
    assert list(Draft202012Validator(schema).iter_errors({
        "projectPath": "D:/Project", "assetPath": "Assets/a.hlsl", "includeText": True,
        "textMaxBytes": 65537,
    }))


def test_backend_projects_core_identity_into_text_resource(monkeypatch, tmp_path):
    import dashboard_server as server

    root, info = _project(tmp_path, "#define LIL_FEATURE_DissolveNoiseMask\n")
    registry = McpResourceRegistry(tmp_path / "resources")
    calls = []
    monkeypatch.setattr(server, "load_dashboard_settings", lambda _: {})
    monkeypatch.setattr(server, "invoke_unity_mcp", lambda settings, tool, args: calls.append((tool, args)) or {})
    monkeypatch.setattr(server, "extract_tool_result_payload", lambda result: dict(info))
    monkeypatch.setattr(server.AGENT_GATEWAY, "_mcp_resources", registry)
    result = server.get_asset_info_sync({"projectPath": str(root), "assetPath": info["assetPath"], "includeText": True})
    assert calls == [("vrc_get_asset_info", {"assetPath": info["assetPath"], "guid": ""})]
    assert result["text"]["resourceUri"].startswith("vrcforge://unity-asset-text/")
    assert "GPU" in result["text"]["interpretation"]


@pytest.mark.parametrize("derived_params", [
    {"includeText": True},
    {"textSearch": {"literal": "missing"}},
    {"includeMetaText": True},
    {"includePreview": True},
])
def test_backend_preserves_core_asset_failure_before_derived_read(monkeypatch, tmp_path, derived_params):
    import dashboard_server as server

    root, info = _project(tmp_path, "unused")
    missing_asset = "Assets/VRCForgeGenerated/FinalAvatar/Wardrobe/Shaders/IndependentDiagnostic/DiagnosticCutout.lilcontainer"
    core_failure = {
        "success": False,
        "code": f"No asset found at '{missing_asset}'.",
        "error": f"No asset found at '{missing_asset}'.",
        "operationId": "asset-info-failure-1121",
        "executionTargetDigest": "e" * 64,
        "ok": False,
    }
    calls = []
    monkeypatch.setattr(server, "load_dashboard_settings", lambda _: {})
    monkeypatch.setattr(server, "invoke_unity_mcp", lambda settings, tool, args: calls.append((tool, args)) or {})
    monkeypatch.setattr(server, "extract_tool_result_payload", lambda result: dict(core_failure))

    def fail_if_derived_read(*_args, **_kwargs):
        raise AssertionError("includeText must not run after Core asset failure")

    import asset_text_resources
    monkeypatch.setattr(asset_text_resources, "publish_asset_text", fail_if_derived_read)
    monkeypatch.setattr(asset_text_resources, "publish_asset_meta", fail_if_derived_read)
    import texture_preview_resources
    monkeypatch.setattr(texture_preview_resources, "publish_texture_preview", fail_if_derived_read)
    params = {"projectPath": str(root), "assetPath": missing_asset, **derived_params}
    result = server.get_asset_info_sync(params)
    assert result == core_failure
    assert calls == [("vrc_get_asset_info", {"assetPath": missing_asset, "guid": ""})]


def test_backend_routes_literal_search_without_forwarding_search_to_core(monkeypatch, tmp_path):
    import dashboard_server as server

    root, info = _project(tmp_path, "prefix\nTARGET\n")
    registry = McpResourceRegistry(tmp_path / "resources")
    calls = []
    monkeypatch.setattr(server, "load_dashboard_settings", lambda _: {})
    monkeypatch.setattr(server, "invoke_unity_mcp", lambda settings, tool, args: calls.append((tool, args)) or {})
    monkeypatch.setattr(server, "extract_tool_result_payload", lambda result: dict(info))
    monkeypatch.setattr(server.AGENT_GATEWAY, "_mcp_resources", registry)
    result = server.get_asset_info_sync({
        "projectPath": str(root), "assetPath": info["assetPath"],
        "textSearch": {"literal": "TARGET", "contextBeforeBytes": 0, "contextAfterBytes": 0},
    })
    assert calls == [("vrc_get_asset_info", {"assetPath": info["assetPath"], "guid": ""})]
    assert result["text"]["textSearch"]["matches"][0]["matchText"] == "TARGET"


def test_selected_range_complete_is_distinct_from_entire_source(tmp_path):
    root, info = _project(tmp_path, "a\nb\nc\n")
    result = publish_asset_text(McpResourceRegistry(tmp_path / "r"), {
        "projectPath": str(root), "textStartLine": 2, "textEndLine": 2,
    }, info)
    assert result["rangeComplete"] is True
    assert result["complete"] is False
    assert result["sourceComplete"] is False
    assert result["nextLine"] == 3


def test_newline_byte_cut_continues_after_returned_line(tmp_path):
    root, info = _project(tmp_path, "a\nb\nc\n")
    asset = root / info["assetPath"]
    asset.write_bytes(b"a\nb\nc\n")
    result = publish_asset_text(McpResourceRegistry(tmp_path / "r"), {
        "projectPath": str(root), "textMaxBytes": 2,
    }, info)
    assert result["text"] == "a\n"
    assert result["nextLine"] == 2
    assert result["nextByteOffset"] == 2


def test_multibyte_cut_offset_counts_only_returned_utf8(tmp_path):
    root, info = _project(tmp_path, "界a")
    result = publish_asset_text(McpResourceRegistry(tmp_path / "r"), {
        "projectPath": str(root), "textMaxBytes": 2,
    }, info)
    assert result["text"] == ""
    assert result["nextByteOffset"] == 0
    assert result["continuation"]["method"] == "resources/read"


def test_literal_text_search_finds_match_after_preview_and_preserves_resource(tmp_path):
    content = ("prefix\n" * 12000) + "界TARGET\n" + ("tail\n" * 1000)
    root, info = _project(tmp_path, content)
    registry = McpResourceRegistry(tmp_path / "search")
    result = publish_asset_text(registry, {
        "projectPath": str(root), "includeText": True, "textMaxBytes": 32,
        "textSearch": {"literal": "TARGET", "contextBeforeBytes": 3, "contextAfterBytes": 3},
    }, info)
    search = result["textSearch"]
    assert search["matchCount"] == 1
    assert search["returnedMatchCount"] == 1
    assert search["matches"][0]["lineStart"] > 65536 // 6
    assert search["matches"][0]["matchText"] == "TARGET"
    assert search["matches"][0]["contextText"].startswith("界TARGET\r\n")
    assert search["matches"][0]["matchStartByte"] > 65536
    assert search["sourceContentHash"] == result["textSha256"]
    stored = registry.read(result["resourceUri"])["structuredContent"]["data"]
    assert "".join(c["text"] for c in stored["chunks"]) == (root / info["assetPath"]).read_bytes().decode("utf-8-sig")


def test_literal_text_search_handles_multibyte_offsets_and_match_limit(tmp_path):
    root, info = _project(tmp_path, "界A界A界A\n")
    result = publish_asset_text(McpResourceRegistry(tmp_path / "search"), {
        "projectPath": str(root), "textSearch": {
            "literal": "界A", "contextBeforeBytes": 0, "contextAfterBytes": 2, "maxMatches": 2,
        },
    }, info)
    search = result["textSearch"]
    assert search["matchCount"] == 3
    assert search["returnedMatchCount"] == 2
    assert search["matchTruncated"] is True
    assert search["nextMatchStartByte"] == len("界A界A".encode("utf-8"))
    assert [m["matchStartByte"] for m in search["matches"]] == [0, 4]
    assert [m["lineStart"] for m in search["matches"]] == [1, 1]
    assert [m["contextText"] for m in search["matches"]] == ["界A", "界A"]


def test_literal_text_search_reports_no_match_and_validates_literal(tmp_path):
    root, info = _project(tmp_path, "shader\n")
    result = publish_asset_text(McpResourceRegistry(tmp_path / "search"), {
        "projectPath": str(root), "textSearch": {"literal": "missing"},
    }, info)
    assert result["textSearch"]["matchCount"] == 0
    assert result["textSearch"]["searchComplete"] is True
    with pytest.raises(ValueError, match="literal"):
        publish_asset_text(McpResourceRegistry(tmp_path / "invalid"), {
            "projectPath": str(root), "textSearch": {"literal": ""},
        }, info)


def test_literal_text_search_schema_is_optional_and_bounded():
    schema = UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_get_asset_info"]
    assert list(Draft202012Validator(schema).iter_errors({
        "assetPath": "Assets/a.hlsl", "textSearch": {"literal": "x"},
    }))
    Draft202012Validator(schema).validate({
        "projectPath": "D:/Project", "assetPath": "Assets/a.hlsl", "includeText": True,
        "textSearch": {"literal": "#include", "contextBeforeBytes": 96, "contextAfterBytes": 160, "maxMatches": 32},
        })
    assert list(Draft202012Validator(schema).iter_errors({
        "projectPath": "D:/Project", "assetPath": "Assets/a.hlsl",
        "textSearch": {"literal": "x", "maxMatches": 129},
    }))


def test_literal_text_search_caps_aggregate_context_result(tmp_path):
    root, info = _project(tmp_path, ("a" * 4096 + "TARGET" + "b" * 4096 + "\n") * 32)
    result = publish_asset_text(McpResourceRegistry(tmp_path / "cap"), {
        "projectPath": str(root), "textSearch": {
            "literal": "TARGET", "contextBeforeBytes": 4096, "contextAfterBytes": 4096, "maxMatches": 128,
        },
    }, info)
    search = result["textSearch"]
    assert search["matchCount"] == 32
    assert search["resultTruncated"] is True
    assert search["resultBytes"] <= 65536
    assert search["nextMatchStartByte"] == search["matches"][-1]["matchEndByte"] + 4096 + 4096 + 2


def test_long_single_line_can_be_read_as_bounded_resource_chunks(tmp_path):
    content = "界" * 30000
    root, info = _project(tmp_path, content)
    registry = McpResourceRegistry(tmp_path / "r")
    result = publish_asset_text(registry, {"projectPath": str(root), "textMaxBytes": 5}, info)
    stored = registry.read(result["resourceUri"])["structuredContent"]["data"]
    assert "text" not in stored  # No duplicate full text alongside chunks.
    chunks = stored["chunks"]
    assert "".join(c["text"] for c in chunks) == content
    assert all(len(c["text"].encode("utf-8")) <= 8192 for c in chunks)
    assert chunks[0]["startByte"] == 0
    assert chunks[-1]["endByte"] == len(content.encode("utf-8"))
    assert all(a["endByte"] == b["startByte"] for a, b in zip(chunks, chunks[1:]))


def test_search_only_omits_unrequested_preview_but_retains_full_resource(tmp_path):
    import json
    root, info = _project(tmp_path, "irrelevant\n" * 12000 + "TARGET")
    registry = McpResourceRegistry(tmp_path / "search_only")
    result = publish_asset_text(registry, {"projectPath": str(root), "textSearch": {"literal": "TARGET"}}, info)
    assert result["text"] == "" and result["returnedBytes"] == 0
    assert result["complete"] is False
    assert len(json.dumps(result).encode()) < 4096
    assert result["textSearch"]["matchCount"] == 1
    stored = registry.read(result["resourceUri"])["structuredContent"]["data"]
    assert "".join(c["text"] for c in stored["chunks"]) == (root / info["assetPath"]).read_bytes().decode("utf-8")


def test_search_budget_metadata_and_actual_size_agree(tmp_path):
    import json
    root, info = _project(tmp_path, ("a" * 4096 + "TARGET" + "b" * 4096 + "\n") * 32)
    result = publish_asset_text(McpResourceRegistry(tmp_path / "budget"), {
        "projectPath": str(root), "textSearch": {"literal": "TARGET", "contextBeforeBytes": 4096,
        "contextAfterBytes": 4096, "maxMatches": 128}}, info)["textSearch"]
    assert result["matchTruncated"] and not result["searchComplete"]
    assert result["resultBytes"] == len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode())
    assert result["resultBytes"] <= 65536


def test_search_continuation_preserves_total_and_advances(tmp_path):
    root, info = _project(tmp_path, "界A界A界A")
    registry = McpResourceRegistry(tmp_path / "pages")
    first = publish_asset_text(registry, {"projectPath": str(root), "textSearch": {"literal": "界A", "maxMatches": 2}}, info)["textSearch"]
    second = publish_asset_text(registry, {"projectPath": str(root), "textSearch": first["continuation"]["textSearch"]}, info)["textSearch"]
    assert first["matchCount"] == second["matchCount"] == 3
    assert second["returnedMatchCount"] == 1 and second["searchComplete"]
    assert second["matches"][0]["matchStartByte"] == 8


def test_search_multiline_match_line_end_is_inclusive(tmp_path):
    root, info = _project(tmp_path, "first\nsecond\nthird")
    raw = (root / info["assetPath"]).read_bytes()
    literal = raw.split(b"second")[0].decode()
    result = publish_asset_text(McpResourceRegistry(tmp_path / "multiline"), {
        "projectPath": str(root), "textSearch": {"literal": literal}}, info)["textSearch"]["matches"][0]
    assert result["lineStart"] == result["lineEnd"] == 1


def test_bom_source_hash_and_decoded_text_hash_are_distinct(tmp_path):
    import hashlib
    root, info = _project(tmp_path, "unused")
    raw = b"\xef\xbb\xbfhello\n"
    (root / info["assetPath"]).write_bytes(raw)
    result = publish_asset_text(McpResourceRegistry(tmp_path / "r"), {"projectPath": str(root)}, info)
    assert result["sourceSha256"] == hashlib.sha256(raw).hexdigest()
    assert result["textSha256"] == hashlib.sha256(b"hello\n").hexdigest()
    assert result["text"] == "hello\n"


def test_include_meta_text_reads_only_matching_sidecar_and_preserves_raw_hash(tmp_path):
    import hashlib
    root, info = _project(tmp_path, "shader\n")
    meta_raw = b"fileFormatVersion: 2\nguid: " + info["guid"].encode() + b"\n" \
        b"TextScriptImporter:\n  userData: keep\n"
    (root / (info["assetPath"] + ".meta")).write_bytes(b"\xef\xbb\xbf" + meta_raw)
    result = publish_asset_text(McpResourceRegistry(tmp_path / "r"), {
        "projectPath": str(root), "includeMetaText": True,
    }, info)
    meta = result["meta"]
    assert meta["assetGuid"] == info["guid"]
    assert meta["rawSha256"] == hashlib.sha256(b"\xef\xbb\xbf" + meta_raw).hexdigest()
    assert meta["textSha256"] == hashlib.sha256(meta_raw).hexdigest()
    assert meta["text"].startswith("fileFormatVersion: 2")
    assert meta["resourceUri"].startswith("vrcforge://unity-asset-meta/")
    assert meta["reconstructedRawSha256"] == meta["rawSha256"]


def test_include_meta_text_rejects_sidecar_guid_mismatch(tmp_path):
    root, info = _project(tmp_path, "shader\n")
    (root / (info["assetPath"] + ".meta")).write_text(
        "fileFormatVersion: 2\nguid: " + "b" * 32 + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="meta GUID"):
        publish_asset_text(McpResourceRegistry(tmp_path / "r"), {
            "projectPath": str(root), "includeMetaText": True,
        }, info)


def test_public_schema_exposes_include_meta_text_with_explicit_project_requirement():
    schema = UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_get_asset_info"]
    assert list(Draft202012Validator(schema).iter_errors({
        "assetPath": "Assets/a.hlsl", "includeMetaText": True,
    }))
    Draft202012Validator(schema).validate({
        "projectPath": "D:/Project", "assetPath": "Assets/a.hlsl",
        "includeMetaText": True,
    })


def test_backend_routes_meta_only_without_forwarding_flag_to_core(monkeypatch, tmp_path):
    import dashboard_server as server

    root, info = _project(tmp_path, "shader\n")
    (root / (info["assetPath"] + ".meta")).write_text(
        "fileFormatVersion: 2\nguid: " + info["guid"] + "\n", encoding="utf-8"
    )
    registry = McpResourceRegistry(tmp_path / "r")
    calls = []
    monkeypatch.setattr(server, "load_dashboard_settings", lambda _: {})
    monkeypatch.setattr(server, "invoke_unity_mcp", lambda settings, tool, args: calls.append((tool, args)) or {})
    monkeypatch.setattr(server, "extract_tool_result_payload", lambda result: dict(info))
    monkeypatch.setattr(server.AGENT_GATEWAY, "_mcp_resources", registry)
    result = server.get_asset_info_sync({
        "projectPath": str(root), "assetPath": info["assetPath"],
        "includeMetaText": True, "includeText": False, "textMaxBytes": 8,
    })
    assert calls == [("vrc_get_asset_info", {"assetPath": info["assetPath"], "guid": ""})]
    assert result["metaText"]["meta"]["text"] == "fileForm"
    assert result["metaText"]["meta"]["truncated"] is True
    assert result["metaText"]["interpretation"].endswith("main asset content was not read.")


def test_meta_resource_alone_reconstructs_bom_crlf_and_pairs_source_hash(tmp_path):
    import hashlib
    root, info = _project(tmp_path, "shader\n")
    text = "界\r\nfileFormatVersion: 2\r\nguid: " + info["guid"] + "\r\nuserData: " + "界" * 4000
    raw = b"\xef\xbb\xbf" + text.encode("utf-8")
    (root / (info["assetPath"] + ".meta")).write_bytes(raw)
    registry = McpResourceRegistry(tmp_path / "r")
    result = publish_asset_text(registry, {"projectPath": str(root), "includeMetaText": True, "textMaxBytes": 2}, info)
    meta = result["meta"]
    assert meta["text"] == "" and meta["returnedBytes"] == 0
    assert meta["continuation"]["resumeTextByteOffset"] == 0
    stored = registry.read(meta["resourceUri"])["structuredContent"]["data"]
    rebuilt = (b"\xef\xbb\xbf" if stored["bomPresent"] else b"") + "".join(c["text"] for c in stored["chunks"]).encode("utf-8")
    assert rebuilt == raw
    assert hashlib.sha256(rebuilt).hexdigest() == stored["rawSha256"] == meta["rawSha256"]
    assert stored["sourceAssetSha256"] == result["sourceSha256"]
    assert stored["dependencyHash"] == info["dependencyHash"]


def test_meta_only_handles_binary_main_asset_and_rejects_duplicate_guid(tmp_path):
    from asset_text_resources import publish_asset_meta
    root, info = _project(tmp_path, "unused")
    (root / info["assetPath"]).write_bytes(b"\x00\xff")
    sidecar = root / (info["assetPath"] + ".meta")
    one_guid = "guid: " + info["guid"] + "\n"
    sidecar.write_text(one_guid, encoding="utf-8")
    assert publish_asset_meta(McpResourceRegistry(tmp_path / "r1"), {"projectPath": str(root)}, info)["meta"]["complete"]
    sidecar.write_text(one_guid * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="meta GUID"):
        publish_asset_meta(McpResourceRegistry(tmp_path / "r2"), {"projectPath": str(root)}, info)
