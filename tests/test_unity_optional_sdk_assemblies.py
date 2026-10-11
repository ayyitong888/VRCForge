"""Keep an importable setup path when Avatar SDK dependencies are absent.

The Unity import matrix additionally exercises the real compiler and menu.
These fast checks prevent a new source file or package edit from accidentally
putting SDK/JSON dependencies back into an unconditional assembly.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import dashboard_server


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "Assets" / "VRCForge"


def _assembly(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _owner(source: Path) -> Path:
    for directory in (source.parent, *source.parents):
        definitions = list(directory.glob("*.asmdef"))
        if definitions:
            assert len(definitions) == 1, source
            return definitions[0]
        if directory == ASSETS:
            break
    raise AssertionError(f"Default assembly escaped dependency isolation: {source}")


def test_sdk_and_json_sources_require_the_complete_avatar_dependency_set():
    definition = ASSETS / "VRCForge.Editor.asmdef"
    assembly = _assembly(definition)
    assert assembly["includePlatforms"] == ["Editor"]
    defines = {item["name"]: item["define"] for item in assembly["versionDefines"]}
    for package in (
        "com.vrchat.base",
        "com.vrchat.avatars",
        "com.unity.nuget.newtonsoft-json",
    ):
        assert defines[package] in assembly["defineConstraints"], package
    for source in ASSETS.rglob("*.cs"):
        code = source.read_text(encoding="utf-8-sig")
        code = re.sub(r"/\*.*?\*/|//[^\r\n]*", "", code, flags=re.DOTALL)
        if re.search(r"^\s*using\s+(?:Newtonsoft|VRC)\.", code, re.MULTILINE):
            assert _owner(source) == definition, source


def test_setup_compiles_without_the_core_or_external_packages():
    setup = ASSETS / "Setup" / "Editor"
    definitions = list(setup.glob("*.asmdef"))
    assert len(definitions) == 1
    assembly = _assembly(definitions[0])
    assert assembly["includePlatforms"] == ["Editor"]
    assert not assembly.get("references")
    assert not assembly.get("precompiledReferences")
    assert not assembly.get("defineConstraints")
    assert list(setup.glob("*.cs")), "The independently importable setup menu is missing"


def test_preflight_accepts_transitive_newtonsoft_from_packages_lock(tmp_path: Path):
    project = tmp_path / "Project"
    packages = project / "Packages"
    packages.mkdir(parents=True)
    (packages / "manifest.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "com.vrchat.avatars": "3.7.0",
                    "com.vrchat.base": "3.7.0",
                }
            }
        ),
        encoding="utf-8",
    )
    (packages / "packages-lock.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "com.unity.nuget.newtonsoft-json": {
                        "version": "3.2.1",
                        "depth": 1,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = dashboard_server.build_unity_dependency_preflight(project)

    assert result["status"] == "ok"
    assert result["ready"] is True
    assert result["checks"]["newtonsoft"]["source"] == "packages-lock"


def test_observation_frame_pump_remains_a_runtime_assembly():
    pump = ASSETS / "Runtime" / "RuntimeObservationFramePump.cs"
    assembly = _assembly(_owner(pump))
    assert not assembly.get("includePlatforms")
    assert not assembly.get("defineConstraints")
    editor = _assembly(ASSETS / "VRCForge.Editor.asmdef")
    assert assembly["name"] in editor["references"]
