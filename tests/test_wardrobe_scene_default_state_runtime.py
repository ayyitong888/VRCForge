"""Execute the production managed-wardrobe scene default planner with a small Unity model."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets/VRCForge/Editor/WardrobeOutfitWriter.cs"


def _source_text() -> str:
    source_dir = os.environ.get("VRCFORGE_WARDROBE_DEFAULT_STATE_SOURCE_DIR")
    source_path = Path(source_dir) / "WardrobeOutfitWriter.cs" if source_dir else SOURCE
    return source_path.read_text(encoding="utf-8-sig")


def _extract_method(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("Unbalanced production method")


def test_scene_default_state_is_scoped_to_managed_wardrobe_and_selected_default(tmp_path):
    source = _source_text()
    method = _extract_method(source, "private static Dictionary<string, bool> BuildSceneDefaultActiveStates(")
    program = r'''
using System;
using System.Collections.Generic;
using System.Linq;

sealed class AnimationClip { public string[] OnObjects; }
sealed class AnimatorState { public object motion; }
sealed class EqualsInfo { public int value; public string stateName; }
static class Probe {
    static IEnumerable<string> ReadClipOnObjects(AnimationClip clip) => clip?.OnObjects ?? Array.Empty<string>();
    METHOD
    static void Check(Dictionary<string,bool> actual, params string[] expectedActive) {
        var expected = new HashSet<string>(expectedActive, StringComparer.Ordinal);
        foreach (var item in actual)
            if (item.Value != expected.Contains(item.Key)) throw new Exception(item.Key + " active=" + item.Value);
    }
    static void Main() {
        var states = new Dictionary<string,AnimatorState> {
            ["Base"] = new AnimatorState { motion = new AnimationClip { OnObjects = new[] { "Original/Default" } } },
            ["CoatA"] = new AnimatorState { motion = new AnimationClip { OnObjects = new[] { "Custom/A" } } },
            ["CoatB"] = new AnimatorState { motion = new AnimationClip { OnObjects = new[] { "Custom/B" } } },
            ["CoatC"] = new AnimatorState { motion = new AnimationClip { OnObjects = new[] { "Custom/C" } } },
            ["CoatD"] = new AnimatorState { motion = new AnimationClip { OnObjects = new[] { "Custom/D" } } }
        };
        var siblings = new HashSet<string>(new[] { "Original/Default", "Custom/A", "Custom/B", "Custom/C" }, StringComparer.Ordinal);
        var existing = new List<EqualsInfo> {
            new EqualsInfo { value = 0, stateName = "Base" },
            new EqualsInfo { value = 1, stateName = "CoatA" },
            new EqualsInfo { value = 2, stateName = "CoatB" },
            new EqualsInfo { value = 3, stateName = "CoatC" }
        };
        var actual = BuildSceneDefaultActiveStates(0, 4, new[] { "Custom/D" }, siblings, existing, states);
        Check(actual, "Original/Default");
        if (actual.ContainsKey("Avatar/Unmanaged")) throw new Exception("unmanaged avatar object entered plan");
        var selectingNew = BuildSceneDefaultActiveStates(4, 4, new[] { "Custom/D" }, siblings, existing, states);
        Check(selectingNew, "Custom/D");
        var ambiguous = new List<EqualsInfo> {
            new EqualsInfo { value = 0, stateName = "Base" },
            new EqualsInfo { value = 0, stateName = "CoatA" }
        };
        var preserveAmbiguous = BuildSceneDefaultActiveStates(0, 4, new[] { "Custom/D" }, siblings, ambiguous, states);
        Check(preserveAmbiguous, "Original/Default", "Custom/A");
        Console.WriteLine("PASS: managed alternates OFF, selected default preserved, new default honored, ambiguity preserved, unmanaged objects excluded");
    }
}
'''.replace("METHOD", method)

    dotnet = shutil.which("dotnet")
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    packs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not packs:
        pytest.skip("Local .NET SDK and netcoreapp3.1 reference pack required")
    cs = tmp_path / "Probe.cs"
    cs.write_text(program, encoding="utf-8")
    assembly = tmp_path / "Probe.dll"
    built = subprocess.run(
        [dotnet, str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{assembly}"]
        + [f"-r:{item}" for item in packs[-1].glob("*.dll")]
        + [str(cs)], capture_output=True, text=True, timeout=60,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}))
    run = subprocess.run([dotnet, str(assembly)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
