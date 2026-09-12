"""Compile and execute the actual asset-copy path validators; no Unity imitation."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets/VRCForge/Editor/Generic/DuplicateProjectAssetTool.cs"


def extract(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def run_actual_validators(output: Path) -> subprocess.CompletedProcess:
    dotnet = shutil.which("dotnet")
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    references = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not references:
        pytest.skip("Local .NET SDK/reference pack required")
    source = SOURCE.read_text(encoding="utf-8-sig")
    declarations = []
    for name in ("GeneratedRoot", "LegacyGeneratedRoot"):
        declarations.append(next(line.strip() for line in source.splitlines() if "const string " + name + " =" in line).replace("internal ", "private "))
    start = source.index("private static readonly string[] AllowedExtensions")
    declarations.append(source[start:source.index(";", start)+1])
    methods = [extract(source, visibility + " static " + signature) for visibility, signature in (
        ("internal", "string NormalizeSourcePath"),
        ("internal", "string NormalizeDestinationPath"),
        ("private", "string NormalizeAssetPath"),
        ("private", "void ValidateExtension"),
        ("private", "string GeneratedSourceType"),
        ("private", "void ValidateGeneratedSourceType"),
    )]
    runner = r'''
    static int failures;
    static void Expect(string path, bool allowed) {
        bool actual;
        try { actual = NormalizeSourcePath(path) == path; }
        catch (InvalidOperationException) { actual = false; }
        Console.WriteLine((actual == allowed ? "PASS " : "FAIL ") + path);
        if (actual != allowed) failures++;
    }
    static void CheckType(string path, string type, bool allowed) {
        bool actual = true;
        try { ValidateGeneratedSourceType(path, type); }
        catch (InvalidOperationException) { actual = false; }
        Console.WriteLine((actual == allowed ? "PASS " : "FAIL ") + path + " type=" + type);
        if (actual != allowed) failures++;
    }
    public static int Main() {
        Expect("Assets/VRCForgeGenerated/FinalAvatar/Shared/Controllers/FinalAvatar_BaseFX.controller", true);
        Expect("Assets/VRCForgeGenerated/FinalAvatar/Wardrobe/Animations/Existing.anim", true);
        Expect("Assets/VRCForgeGenerated/FinalAvatar/Materials/Existing.mat", true);
        Expect("Assets/User/Existing.controller", true);
        Expect("Assets/User/Existing.anim", true);
        Expect("Assets/VRCForgeGenerated/FinalAvatar/Unknown.asset", false);
        Expect("Assets/VRCForge/Generated/Legacy.anim", false);
        Expect("Assets/VRCForgeGenerated/FinalAvatar/Script.cs", false);
        Expect("Assets/VRCForgeGenerated/../Existing.anim", false);
        Expect("Assets/VRCForgeGenerated/FinalAvatar/", false);
        Expect("Packages/ThirdParty/Existing.anim", false);
        Expect("Assets/VRCForge/Editor/Plugin.controller", true);
        Expect("Assets/Plugins/Other/Plugin.anim", true);
        Expect("Assets/VRCForgeGenerated/Avatar/Overrides/A.overridecontroller", true);
        CheckType("Assets/VRCForgeGenerated/Avatar/A.anim", "UnityEngine.AnimationClip", true);
        CheckType("Assets/VRCForgeGenerated/Avatar/A.controller", "UnityEditor.Animations.AnimatorController", true);
        CheckType("Assets/VRCForgeGenerated/Avatar/A.anim", "Untrusted.ScriptableAsset", false);
        CheckType("Assets/VRCForgeGenerated/Avatar/A.controller", "UnityEngine.AnimationClip", false);
        return failures == 0 ? 0 : 1;
    }
    '''
    output.mkdir(parents=True, exist_ok=True)
    program = "using System; using System.IO; using System.Linq; public class Probe { class ProjectAssetCopyException : InvalidOperationException { public ProjectAssetCopyException(string message):base(message){} }\n" + "\n".join(declarations + methods) + runner + "}"
    (output / "Probe.cs").write_text(program, encoding="utf-8")
    dll = output / "Probe.dll"
    command = [dotnet, str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{p}" for p in references[-1].glob("*.dll")]
    command.append(str(output / "Probe.cs"))
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    (output / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}), encoding="utf-8")
    return subprocess.run([dotnet, str(dll)], capture_output=True, text=True, timeout=30)


def test_generated_animation_copy_actual_source_validator(tmp_path):
    result = run_actual_validators(tmp_path / "compiled")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS ") == 18
