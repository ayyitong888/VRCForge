from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs"


def _product_source() -> str:
    revision = os.environ.get("VRCFORGE_ENSURE_ANIMATOR_GIT_REF", "").strip()
    if revision:
        return subprocess.check_output(
            ["git", "show", f"{revision}:Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs"],
            text=True,
        )
    return SOURCE.read_text(encoding="utf-8-sig")


def _method(source: str, marker: str) -> str:
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    end = brace
    while end < len(source):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
            if depth == 0:
                return source[start : end + 1].replace("private static", "public static", 1)
        end += 1
    raise AssertionError(f"unterminated method: {marker}")


def _program(source: str) -> str:
    parameter = _method(source, "private static bool EnsureControllerParameter")
    layer = _method(source, "private static AnimatorControllerLayer EnsureLayer")
    state = _method(source, "private static AnimatorState FindState")
    has_validate = "private static void ValidateTargetIdentity" in source
    validate = _method(source, "private static void ValidateTargetIdentity") if has_validate else ""
    preflight = "" if not has_validate else '''
        var preflightParameters = new AnimatorController {{ parameters = new[] {{
            new AnimatorControllerParameter {{ name = "Clothes", type = AnimatorControllerParameterType.Int }},
            new AnimatorControllerParameter {{ name = "Clothes", type = AnimatorControllerParameterType.Int }} }} }};
        var parameterCount = preflightParameters.parameters.Length;
        Check(Reject(() => ValidateTargetIdentity(preflightParameters, "FX", "State", "Clothes", AnimatorControllerParameterType.Int))
            && preflightParameters.parameters.Length == parameterCount, "preflight rejects duplicate parameter without mutation");
        var typeConflict = new AnimatorController {{ parameters = new[] {{
            new AnimatorControllerParameter {{ name = "Clothes", type = AnimatorControllerParameterType.Int }} }} }};
        var typeCount = typeConflict.parameters.Length;
        Check(Reject(() => ValidateTargetIdentity(typeConflict, "FX", "State", "Clothes", AnimatorControllerParameterType.Bool))
            && typeConflict.parameters.Length == typeCount, "preflight rejects parameter type conflict without mutation");
        var preflightLayers = new AnimatorController {{ layers = new[] {{ Layer("FX", Machine()), Layer("FX", Machine()) }} }};
        var layerCount = preflightLayers.layers.Length;
        Check(Reject(() => ValidateTargetIdentity(preflightLayers, "FX", "State", "Clothes", AnimatorControllerParameterType.Int))
            && preflightLayers.layers.Length == layerCount, "preflight rejects duplicate layer without mutation");
        var preflightStates = new AnimatorController {{ layers = new[] {{ Layer("FX", root) }} }};
        Check(Reject(() => ValidateTargetIdentity(preflightStates, "FX", "Idle", "Clothes", AnimatorControllerParameterType.Int)), "preflight rejects duplicate state without mutation");
        var uniquePreflight = new AnimatorController {{ layers = new[] {{ Layer("FX", Machine("State")) }} }};
        ValidateTargetIdentity(uniquePreflight, "FX", "State", "Clothes", AnimatorControllerParameterType.Int);
        Check(uniquePreflight.layers.Length == 1 && uniquePreflight.parameters.Length == 0, "unique preflight leaves controller unchanged");
'''
    preflight = preflight.replace("{{", "{").replace("}}", "}")
    return f'''
using System;
using System.Linq;
using System.Collections.Generic;
using UnityEditor.Animations;
using UnityEngine;

namespace UnityEngine {{ public static class Mathf {{ public static bool Approximately(float a,float b)=>Math.Abs(a-b)<0.0001f; }} }}
namespace UnityEditor.Animations {{
    public enum AnimatorControllerParameterType {{ Float, Int, Bool, Trigger }}
    public class AnimatorControllerParameter {{ public string name; public AnimatorControllerParameterType type; }}
    public class AnimatorState {{ public string name; }}
    public class ChildAnimatorState {{ public AnimatorState state; }}
    public class ChildAnimatorStateMachine {{ public AnimatorStateMachine stateMachine; }}
    public class AnimatorStateMachine {{
        public ChildAnimatorState[] states = Array.Empty<ChildAnimatorState>();
        public ChildAnimatorStateMachine[] stateMachines = Array.Empty<ChildAnimatorStateMachine>();
    }}
    public class AnimatorControllerLayer {{ public string name; public AnimatorStateMachine stateMachine = new AnimatorStateMachine(); public float defaultWeight; }}
    public class AnimatorController {{
        public AnimatorControllerParameter[] parameters = Array.Empty<AnimatorControllerParameter>();
        public AnimatorControllerLayer[] layers = Array.Empty<AnimatorControllerLayer>();
        public void AddParameter(string name, AnimatorControllerParameterType type) {{
            parameters = parameters.Concat(new[] {{ new AnimatorControllerParameter {{ name = name, type = type }} }}).ToArray();
        }}
        public void AddLayer(string name) {{ layers = layers.Concat(new[] {{ new AnimatorControllerLayer {{ name = name }} }}).ToArray(); }}
    }}
}}
public static class Probe {{
    {parameter}
    {layer}
    {state}
    {validate}
    static int failures;
    static void Check(bool value, string label) {{ if (!value) {{ Console.WriteLine("FAIL " + label); failures++; }} }}
    static bool Reject(Action action) {{ try {{ action(); return false; }} catch (InvalidOperationException) {{ return true; }} }}
    static AnimatorStateMachine Machine(params string[] names) => new AnimatorStateMachine {{ states = names.Select(name => new ChildAnimatorState {{ state = new AnimatorState {{ name = name }} }}).ToArray() }};
    static AnimatorControllerLayer Layer(string name, AnimatorStateMachine machine) => new AnimatorControllerLayer {{ name = name, stateMachine = machine }};
    public static int Main() {{
        var duplicateParameters = new AnimatorController {{ parameters = new[] {{
            new AnimatorControllerParameter {{ name = "Clothes", type = AnimatorControllerParameterType.Int }},
            new AnimatorControllerParameter {{ name = "Clothes", type = AnimatorControllerParameterType.Int }} }} }};
        Check(Reject(() => EnsureControllerParameter(duplicateParameters, "Clothes", AnimatorControllerParameterType.Int)), "duplicate parameter rejected");
        var uniqueParameters = new AnimatorController {{ parameters = new[] {{ new AnimatorControllerParameter {{ name = "Clothes", type = AnimatorControllerParameterType.Int }} }} }};
        Check(!EnsureControllerParameter(uniqueParameters, "Clothes", AnimatorControllerParameterType.Int) && uniqueParameters.parameters.Length == 1, "unique parameter reused");
        Check(EnsureControllerParameter(uniqueParameters, "New", AnimatorControllerParameterType.Bool) && uniqueParameters.parameters.Length == 2, "new parameter added");
        Check(Reject(() => EnsureControllerParameter(uniqueParameters, "Clothes", AnimatorControllerParameterType.Bool)), "parameter type conflict rejected");

        var duplicateLayers = new AnimatorController {{ layers = new[] {{ Layer("FX", Machine()), Layer("FX", Machine()) }} }};
        Check(Reject(() => EnsureLayer(duplicateLayers, "FX")), "duplicate layer rejected");
        var uniqueLayers = new AnimatorController {{ layers = new[] {{ Layer("FX", Machine()) }} }};
        Check(ReferenceEquals(EnsureLayer(uniqueLayers, "FX"), uniqueLayers.layers[0]) && uniqueLayers.layers.Length == 1, "unique layer reused");
        Check(EnsureLayer(uniqueLayers, "New").name == "New" && uniqueLayers.layers.Length == 2, "new layer added");

        var root = Machine("Idle");
        root.stateMachines = new[] {{ new ChildAnimatorStateMachine {{ stateMachine = Machine("Nested") }} }};
        Check(FindState(root, "Idle") != null && FindState(root, "Nested") != null && FindState(root, "Missing") == null, "unique and missing states unchanged");
        root.stateMachines = new[] {{ new ChildAnimatorStateMachine {{ stateMachine = Machine("Idle") }} }};
        Check(Reject(() => FindState(root, "Idle")), "root and nested duplicate state rejected");

        {preflight}
        return failures == 0 ? 0 : 1;
    }}
}}
'''


def _run(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    project = tmp_path / "probe.csproj"
    project.write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
        '<TargetFramework>netcoreapp3.1</TargetFramework></PropertyGroup></Project>',
        encoding="utf-8",
    )
    (tmp_path / "Program.cs").write_text(_program(_product_source()), encoding="utf-8")
    return subprocess.run(
        ["dotnet", "run", "--project", str(project), "--nologo"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_ensure_animator_identity_methods_reject_ambiguous_targets(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
