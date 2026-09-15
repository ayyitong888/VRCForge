"""Execute the production FX-controller selectors against small Unity stubs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
AUTHORING = "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs"
PRIMITIVE = "Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs"
WARDROBE_MANAGER = "Assets/VRCForge/Editor/WardrobeManagerWriter.cs"
WARDROBE_PART = "Assets/VRCForge/Editor/WardrobeOutfitPartWriter.cs"
WARDROBE_OUTFIT = "Assets/VRCForge/Editor/WardrobeOutfitWriter.cs"


def _source(relative: str) -> str:
    ref = os.environ.get("VRCFORGE_FX_CONTROLLER_GIT_REF", "").strip()
    if not ref:
        return (ROOT / relative).read_text(encoding="utf-8-sig")
    result = subprocess.run(
        ["git", "show", f"{ref}:{relative}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def _method(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    quote = None
    escaped = False
    line_comment = block_comment = False
    i = brace
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if line_comment:
            if ch == "\n":
                line_comment = False
        elif block_comment:
            if ch == "*" and nxt == "/":
                block_comment = False
                i += 1
        elif quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
        elif ch == "/" and nxt == "/":
            line_comment = True
            i += 1
        elif ch == "/" and nxt == "*":
            block_comment = True
            i += 1
        elif ch in ('"', "'"):
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
        i += 1
    raise AssertionError(f"unterminated method: {signature}")


def _run_probe(tmp_path: Path, authoring: str, primitive: str, manager: str, part: str, outfit: str) -> subprocess.CompletedProcess[str]:
    program = r'''
using System;
using System.Linq;
using System.Collections.Generic;

class RuntimeAnimatorController { }
class AnimatorOverrideController : RuntimeAnimatorController { }
class AnimatorController : RuntimeAnimatorController { public readonly string Id; public AnimatorController(string id) { Id=id; } }
class VRCAvatarDescriptor {
  public enum AnimLayerType { Base=0, Additive=1, Gesture=2, Action=3, FX=4 }
  public struct CustomAnimLayer { public AnimLayerType type; public bool isDefault; public RuntimeAnimatorController animatorController; }
  public CustomAnimLayer[] baseAnimationLayers;
}
static class AvatarAuthoringCrudCore {
  public static AnimatorController GetFxController(VRCAvatarDescriptor descriptor) {
    var slots=(descriptor.baseAnimationLayers??Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>()).Where(layer=>layer.type==VRCAvatarDescriptor.AnimLayerType.FX).ToArray();
    if(slots.Length>1) throw new InvalidOperationException("Avatar FX layer is ambiguous");
    if(slots.Length==0||slots[0].animatorController==null)return null;
    if(!(slots[0].animatorController is AnimatorController controller))throw new InvalidOperationException("unsupported runtime controller");
    return controller;
  }
}
static class Authoring { LOOKUP }
static class Primitive { LOOKUP }
static class Manager { LOOKUP }
static class Part { LOOKUP }
static class Outfit { LOOKUP }
class Probe {
  static int failures;
  static void Check(bool value, string label) { Console.WriteLine((value ? "PASS " : "FAIL ") + label); if (!value) failures++; }
  static bool Reject(Func<AnimatorController> call) { try { call(); return false; } catch (InvalidOperationException) { return true; } }
  static VRCAvatarDescriptor.CustomAnimLayer Layer(VRCAvatarDescriptor.AnimLayerType type, RuntimeAnimatorController controller) => new VRCAvatarDescriptor.CustomAnimLayer { type=type, animatorController=controller };
  static VRCAvatarDescriptor Avatar(params VRCAvatarDescriptor.CustomAnimLayer[] layers) => new VRCAvatarDescriptor { baseAnimationLayers=layers };
  static void CheckHelper(string name, Func<VRCAvatarDescriptor, AnimatorController> get) {
    var ordinary = new AnimatorController(name);
    Check(object.ReferenceEquals(get(Avatar(Layer(VRCAvatarDescriptor.AnimLayerType.FX, ordinary))), ordinary), name + " returns unique ordinary FX controller");
    Check(get(Avatar()) == null, name + " returns null for no FX");
    Check(get(Avatar(Layer(VRCAvatarDescriptor.AnimLayerType.Base, ordinary))) == null, name + " ignores non-FX controller");
    Check(get(Avatar(Layer(VRCAvatarDescriptor.AnimLayerType.FX, null))) == null, name + " returns null for empty FX slot");
    var duplicate = Avatar(
      Layer(VRCAvatarDescriptor.AnimLayerType.FX, new AnimatorController("a")),
      Layer(VRCAvatarDescriptor.AnimLayerType.FX, new AnimatorController("b")));
    Check(Reject(() => get(duplicate)), name + " rejects multiple FX slots");
    var overrideSlot = Avatar(Layer(VRCAvatarDescriptor.AnimLayerType.FX, new AnimatorOverrideController()));
    Check(Reject(() => get(overrideSlot)), name + " rejects FX override controller");
  }
  public static int Main() {
    CheckHelper("authoring", Authoring.GetFxController);
    CheckHelper("primitive", Primitive.GetFxController);
    CheckHelper("manager", Manager.GetFxController);
    CheckHelper("part", Part.GetFxController);
    CheckHelper("outfit", Outfit.GetFxController);
    return failures == 0 ? 0 : 1;
  }
}
'''
    program = program.replace("LOOKUP", _method(authoring, "internal static AnimatorController GetFxController("), 1)
    program = program.replace("LOOKUP", _method(primitive, "internal static AnimatorController GetFxController("), 1)
    program = program.replace("LOOKUP", _method(manager, "private static AnimatorController GetFxController(").replace("private static", "public static", 1), 1)
    program = program.replace("LOOKUP", _method(part, "private static AnimatorController GetFxController(").replace("private static", "public static", 1), 1)
    program = program.replace("LOOKUP", _method(outfit, "private static AnimatorController GetFxController(").replace("private static", "public static", 1), 1)
    base = Path(os.environ.get("DOTNET_ROOT", str(Path.home() / "AppData/Local/Microsoft/dotnet")))
    compilers = sorted((base / "sdk").glob("8.*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("8.*/ref/net8.0"))
    dotnet = base / ("dotnet.exe" if os.name == "nt" else "dotnet")
    if not dotnet.is_file() or not compilers or not refs:
        pytest.skip("Local .NET 8 SDK/reference pack required")
    cs = tmp_path / "FxControllerSelectionProbe.cs"
    dll = tmp_path / "FxControllerSelectionProbe.dll"
    cs.write_text(program, encoding="utf-8")
    command = [str(dotnet), str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{p}" for p in refs[-1].glob("*.dll")]
    compiled = subprocess.run(command + [str(cs)], capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    (tmp_path / "FxControllerSelectionProbe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}), encoding="utf-8")
    return subprocess.run([str(dotnet), str(dll)], capture_output=True, text=True, timeout=30)


def test_both_production_fx_controller_selectors(tmp_path: Path) -> None:
    result = _run_probe(tmp_path, _source(AUTHORING), _source(PRIMITIVE), _source(WARDROBE_MANAGER), _source(WARDROBE_PART), _source(WARDROBE_OUTFIT))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS ") == 30
