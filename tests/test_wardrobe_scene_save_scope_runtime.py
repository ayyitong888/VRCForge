"""Compile the real scene scope; model only its existing ComponentCrudCore ports.

This is new-scope coverage, not a claimed red run against a nonexistent old class.
Writer integration and Unity's actual scene serialization are separate evidence.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / "Assets/VRCForge/Editor/WardrobeSceneSaveScope.cs"
WRITERS = ("WardrobeManagerWriter", "WardrobeOutfitWriter", "WardrobeOutfitPartWriter")


def _compile_and_run(tmp_path, source):
    dotnet = shutil.which("dotnet")
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    references = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not references:
        pytest.skip("Local .NET SDK and netcoreapp3.1 reference pack required")
    probe = tmp_path / "Boundary.cs"
    probe.write_text(source, encoding="utf-8")
    assembly = tmp_path / "Boundary.dll"
    command = [dotnet, str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+",
               "-langversion:8.0", f"-out:{assembly}"]
    command += [f"-r:{reference}" for reference in references[-1].glob("*.dll")]
    command += [str(SCOPE), str(probe)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    (tmp_path / "Boundary.runtimeconfig.json").write_text(json.dumps({
        "runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {
            "name": "Microsoft.NETCore.App", "version": "3.1.0"}}
    }), encoding="utf-8")
    result = subprocess.run([dotnet, str(assembly)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _writer_source(name):
    path = f"Assets/VRCForge/Editor/{name}.cs"
    revision = os.environ.get("VRCFORGE_WARDROBE_SCENE_GIT_REF")
    if revision:
        return subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT,
                                       text=True, encoding="utf-8")
    return (ROOT / path).read_text(encoding="utf-8-sig")


def test_writer_actual_save_segments_persist_scene_only_when_scope_enabled(tmp_path):
    """Execute exact asset-save-through-refresh segments, not whole handlers.

    Set VRCFORGE_WARDROBE_SCENE_GIT_REF=d454f5d to execute real old segments.
    Scope construction and the scene change here are seam setup; source placement
    is checked separately and does not stand in for handler runtime evidence.
    """
    methods = []
    for name in WRITERS:
        source = _writer_source(name)
        start = source.index("saveScope.Save(")
        end = source.index("AssetDatabase.Refresh();", start) + len("AssetDatabase.Refresh();")
        segment = source[start:end]
        methods.append("static void " + name + "(bool enabled) { Setup(enabled); "
                       "var saveScope = new AssetScope(); "
                       "var sceneSaveScope = new WardrobeSceneSaveScope(enabled ? Target : null); "
                       "Target.Scene.Dirty = enabled; Target.Scene.Active = !enabled; "
                       + segment + ' Check(enabled, "' + name + '"); }')
    source = r'''
using System;
namespace UnityEngine { public class GameObject { internal VRCForge.Editor.SavedSceneSnapshot Scene; } }
namespace VRCForge.Editor {
    internal sealed class SavedSceneSnapshot { internal bool Dirty, Active = true, PersistedActive = true; }
    internal static class ComponentCrudCore {
        internal static SavedSceneSnapshot ResolveSavedSceneFor(UnityEngine.GameObject target) {
            if (target.Scene.Dirty) throw new Exception("dirty scene reached mutation boundary");
            return target.Scene;
        }
        internal static SavedSceneSnapshot SaveAndResolveScene(SavedSceneSnapshot scene) {
            Probe.SceneSaves++;
            scene.PersistedActive = scene.Active; scene.Dirty = false; return scene;
        }
    }
    internal static class AssetDatabase { internal static void Refresh() {} }
    internal sealed class AssetScope { internal void Save(params object[] roots) { Probe.AssetSaves++; } }
    internal static class Probe {
        internal static int SceneSaves, AssetSaves;
        internal static UnityEngine.GameObject Target;
        static SavedSceneSnapshot Unrelated;
        static object fxController = new object(), parametersAsset = new object(), clip = new object(),
            onClip = new object(), offClip = new object();
        static object[] saveRoots = new object[] { fxController };
        static bool addMenuToggle = true, boolParamExists = false;
        static MenuOwner descriptor = new MenuOwner();
        class MenuOwner { internal object expressionsMenu = new object(); }
        static int failures;
        static void Setup(bool enabled) {
            SceneSaves = AssetSaves = 0;
            Target = new UnityEngine.GameObject { Scene = new SavedSceneSnapshot() };
            Unrelated = new SavedSceneSnapshot { Dirty = true };
        }
        static void Check(bool enabled, string writer) {
            bool pass = AssetSaves == 1 && SceneSaves == (enabled ? 1 : 0)
                && !Target.Scene.Dirty && Target.Scene.PersistedActive == !enabled && Unrelated.Dirty;
            Console.WriteLine((pass ? "PASS " : "FAIL ") + writer + " enabled=" + enabled);
            if (!pass) failures++;
        }
        // METHODS
        public static int Main() {
            WardrobeManagerWriter(true); WardrobeManagerWriter(false);
            WardrobeOutfitWriter(true); WardrobeOutfitWriter(false);
            WardrobeOutfitPartWriter(true); WardrobeOutfitPartWriter(false);
            return failures == 0 ? 0 : 1;
        }
    }
}
'''.replace("// METHODS", "\n".join(methods))
    assert _compile_and_run(tmp_path, source).count("PASS ") == 6


@pytest.mark.parametrize("name", WRITERS)
def test_writer_scene_scope_source_placement(name):
    """Auxiliary source assertions; these do not execute a complete handler."""
    source = _writer_source(name)
    construct = source.index("new WardrobeSceneSaveScope(")
    save = source.index("sceneSaveScope.Save();")
    asset_save = source.index("saveScope.Save(")
    first_write = source.index("ApplyAction(action,") if name == "WardrobeManagerWriter" else source.index(
        "AvatarAuthoringCrudCore.EnsureAssetFolder(clipDir)")
    assert construct < first_write < asset_save < save < source.index("AssetDatabase.Refresh();", asset_save)


def test_wardrobe_scene_save_scope_uses_existing_scoped_persistence(tmp_path):
    dotnet = shutil.which("dotnet")
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    references = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not references:
        pytest.skip("Local .NET SDK and netcoreapp3.1 reference pack required")

    seam = r'''
using System;
namespace UnityEngine {
    public class GameObject { internal VRCForge.Editor.SavedSceneSnapshot Scene; }
}
namespace VRCForge.Editor {
    internal sealed class SavedSceneSnapshot {
        internal bool Dirty;
        internal bool Active = true;
        internal bool PersistedActive = true;
    }
    internal static class ComponentCrudCore {
        internal static int Preflights, Saves;
        internal static bool FailSave;
        internal static SavedSceneSnapshot LastSaved;
        internal static SavedSceneSnapshot ResolveSavedSceneFor(UnityEngine.GameObject target) {
            Preflights++;
            if (target.Scene == null || target.Scene.Dirty)
                throw new InvalidOperationException("saved clean scene required");
            return target.Scene;
        }
        internal static SavedSceneSnapshot SaveAndResolveScene(SavedSceneSnapshot scene) {
            Saves++;
            LastSaved = scene;
            if (FailSave) throw new InvalidOperationException("scene save failed");
            scene.PersistedActive = scene.Active;
            scene.Dirty = false;
            return scene;
        }
    }
    internal static class Probe {
        static void Check(bool condition, string label) {
            if (!condition) throw new Exception(label);
            Console.WriteLine("PASS " + label);
        }
        static bool Throws(Action action, string message) {
            try { action(); return false; }
            catch (InvalidOperationException exception) { return exception.Message == message; }
        }
        public static int Main() {
            var untouched = new SavedSceneSnapshot { Dirty = true };
            new WardrobeSceneSaveScope(null).Save();
            Check(ComponentCrudCore.Preflights == 0 && ComponentCrudCore.Saves == 0,
                "null target does not preflight or save");

            Check(Throws(() => new WardrobeSceneSaveScope(new UnityEngine.GameObject { Scene = untouched }),
                "saved clean scene required") && ComponentCrudCore.Saves == 0 && untouched.Dirty,
                "dirty target rejects before saving or changing scene");

            Check(Throws(() => new WardrobeSceneSaveScope(new UnityEngine.GameObject()),
                "saved clean scene required") && ComponentCrudCore.Saves == 0,
                "missing saved scene preflight propagates");

            var target = new SavedSceneSnapshot();
            var scope = new WardrobeSceneSaveScope(new UnityEngine.GameObject { Scene = target });
            Check(ComponentCrudCore.Preflights == 3 && ComponentCrudCore.Saves == 0,
                "construction preflights without saving");
            target.Active = false;
            target.Dirty = true;
            scope.Save();
            Check(ComponentCrudCore.Saves == 1 && ReferenceEquals(ComponentCrudCore.LastSaved, target)
                && !target.PersistedActive && !target.Dirty && untouched.Dirty && untouched.PersistedActive,
                "save reaches captured target only and preserves unrelated dirty scene");

            target.Dirty = true;
            ComponentCrudCore.FailSave = true;
            Check(Throws(() => scope.Save(), "scene save failed") && ComponentCrudCore.Saves == 2
                && target.Dirty && untouched.Dirty,
                "save failure reaches caller without false success or compensation");
            new WardrobeSceneSaveScope(null).Save();
            Check(ComponentCrudCore.Preflights == 3 && ComponentCrudCore.Saves == 2,
                "null scope remains no-op even when persistence would fail");
            return 0;
        }
    }
}
'''
    probe = tmp_path / "Probe.cs"
    probe.write_text(seam, encoding="utf-8")
    assembly = tmp_path / "Probe.dll"
    command = [dotnet, str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+",
               "-langversion:8.0", f"-out:{assembly}"]
    command += [f"-r:{reference}" for reference in references[-1].glob("*.dll")]
    command += [str(SCOPE), str(probe)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({
        "runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {
            "name": "Microsoft.NETCore.App", "version": "3.1.0"}}
    }), encoding="utf-8")
    result = subprocess.run([dotnet, str(assembly)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS ") == 7, result.stdout
