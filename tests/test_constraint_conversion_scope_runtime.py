"""Compiled production seams; this does not claim live Unity/SDK execution."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = "Assets/VRCForge/Editor/VrchatConstraintConversionTool.cs"


def source():
    revision = os.environ.get("VRCFORGE_CONVERSION_GIT_REF")
    return (subprocess.check_output(["git", "show", f"{revision}:{PATH}"], cwd=ROOT,
            text=True, encoding="utf-8") if revision else (ROOT / PATH).read_text(encoding="utf-8"))


def run(tmp_path, code):
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compiler = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))[-1]
    references = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))[-1]
    probe = tmp_path / "Probe.cs"
    probe.write_text(code, encoding="utf-8")
    assembly = tmp_path / "Probe.dll"
    result = subprocess.run([shutil.which("dotnet"), str(compiler), "-nologo", "-target:exe", "-nostdlib+",
        f"-out:{assembly}", *[f"-r:{p}" for p in references.glob("*.dll")], str(probe)],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}), encoding="utf-8")
    result = subprocess.run([shutil.which("dotnet"), str(assembly)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_sdk_suffix_alias_is_rejected_before_mutation(tmp_path):
    text = source()
    if "internal static bool IsExactSdkBinding(" in text:
        method = text.split("internal static bool IsExactSdkBinding(", 1)[1].split("private sealed class ClipEdit", 1)[0]
        method = "internal static bool IsExactSdkBinding(" + method
    else:
        # The old tool delegates directly to the SDK predicate without a guard.
        sdk = (ROOT / "UnityProjects/VRCForge_SapphyHead_ManukaBody_Dogfood/Packages/com.vrchat.avatars/Editor/VRCSDK/SDK3A/AvatarDynamicsSetup.cs").read_text(encoding="utf-8")
        assert "!hostGameObjectPath.EndsWith(curveBinding.path)" in sdk
        method = "static bool IsExactSdkBinding(string hostPath, string relativePath, string bindingPath) { return hostPath.EndsWith(bindingPath); }"
    run(tmp_path, "using System; class Probe {" + method + r'''
      static int Main() {
        foreach(var alias in new[]{"Hand", "", "and"}) {
          bool rejected = false;
          try { IsExactSdkBinding("Avatar/Left/Hand", "Left/Hand", alias); }
          catch(InvalidOperationException) { rejected = true; }
          if(!rejected) throw new Exception("SDK suffix alias accepted: " + alias);
        }
        if(!IsExactSdkBinding("Avatar/Left/Hand", "Left/Hand", "Left/Hand")) throw new Exception("exact binding rejected");
        if(IsExactSdkBinding("Avatar/Left/Hand", "Left/Hand", "Right/Foot")) throw new Exception("unrelated binding selected");
        if(!IsExactSdkBinding("Avatar", "", "")) throw new Exception("root binding rejected");
        return 0;
      }
    }''')


def test_conversion_saves_clips_and_flushes_before_scene(tmp_path):
    text = source()
    section = text.split("var issuesGenerated = AvatarDynamicsSetup.DoConvertUnityConstraints(", 1)[1].split("var readback = Readback(immediate);", 1)[0]
    section = "var issuesGenerated = AvatarDynamicsSetup.DoConvertUnityConstraints(" + section
    run(tmp_path, r'''
      using System;
      class Scene { public bool dirty; }
      class Saved { public Scene Scene = new Scene(); }
      class Snapshot { public object Constraint, Avatar; public bool[] Clips = {false}; public Saved Scene = new Saved(); }
      class AvatarDynamicsSetup { public static bool DoConvertUnityConstraints(object[] c, object a, bool rebind) {
        if(!rebind) throw new Exception("animation rebinding disabled"); Undo.pending = true; return false;
      }}
      class Undo { public static bool pending; public static void FlushUndoRecordObjects() {
        if(pending) Probe.immediate.Scene.Scene.dirty = true; pending = false;
      }}
      class EditorSceneManager { public static bool Fail;
        public static void MarkSceneDirty(Scene s) { s.dirty = true; }
        public static bool SaveScene(Scene s) { s.dirty = false; return !Fail; }
      }
      class Probe {
        public static Snapshot immediate = new Snapshot();
        static void SaveConvertedClips(bool[] clips) { if(Undo.pending) throw new Exception("unflushed Undo"); clips[0] = true; }
        static void Apply() {
    ''' + section + r'''
        }
        static int Main() {
          Apply(); Undo.FlushUndoRecordObjects();
          if(!immediate.Clips[0]) throw new Exception("converted animation was not saved");
          if(immediate.Scene.Scene.dirty) throw new Exception("deferred Undo dirtied saved scene");
          EditorSceneManager.Fail = true; bool rejected = false;
          try { Apply(); } catch(InvalidOperationException) { rejected = true; }
          if(!rejected) throw new Exception("save failure reported success");
          return 0;
        }
      }
    ''')


def test_clip_save_checks_persisted_curves_and_scopes_writes(tmp_path):
    text = source()
    if "private static void SaveConvertedClips(" not in text:
        pytest.skip("baseline has no scoped clip persistence method; red covered by conversion orchestration")
    method = "private static void SaveConvertedClips(" + text.split("private static void SaveConvertedClips(", 1)[1].split("private static string Required(", 1)[0]
    run(tmp_path, r'''
      using System; using System.Collections.Generic;
      class AnimationClip { public bool dirty; public string curves = "expected"; }
      class FileEvidence { public string Digest; }
      class Evidence { public string Guid = "guid"; public FileEvidence File = new FileEvidence { Digest = "before" }, Meta = new FileEvidence { Digest = "meta" }; }
      class ClipEdit { public AnimationClip Clip = new AnimationClip(); public string Path = "Assets/target.anim", ExpectedCurves = "expected", SavedDigest; public Evidence Before = new Evidence(); }
      class SceneObjectCopyCore {
        public static Evidence ReadStableAssetEvidence(string p, string label) { return AssetDatabase.disk; }
        public static bool StableAssetEvidenceMatches(Evidence a, Evidence b, bool exact) { return a.Guid == b.Guid && a.File.Digest == b.File.Digest && a.Meta.Digest == b.Meta.Digest; }
      }
      class EditorUtility { public static bool IsDirty(AnimationClip c) { return c.dirty; } public static void SetDirty(AnimationClip c) { c.dirty = true; } }
      [Flags] enum ImportAssetOptions { ForceSynchronousImport = 1, ForceUpdate = 2 }
      class AssetDatabase {
        public static Evidence disk; public static string mode; public static int saves, imports;
        public static AnimationClip unrelated = new AnimationClip { dirty = true };
        public static void SaveAssetIfDirty(AnimationClip c) {
          saves++; c.dirty = mode == "dirty";
          disk = new Evidence(); disk.File.Digest = mode == "unsaved" ? "before" : "after";
          if(mode == "identity") disk.Guid = "alien";
        }
        public static void ImportAsset(string p, ImportAssetOptions o) { imports++; }
        public static AnimationClip LoadAssetAtPath<T>(string p) { return new AnimationClip { curves = mode == "lostCurves" ? "old" : "expected" }; }
      }
      class Probe {
        static string CurveState(AnimationClip clip) { return clip.curves; }
    ''' + method + r'''
        static int Main() {
          foreach(var mode in new[]{"ok", "dirty", "unsaved", "identity", "lostCurves", "diskChanged", "wrongCurves"}) {
            AssetDatabase.mode = mode; AssetDatabase.saves = AssetDatabase.imports = 0;
            AssetDatabase.disk = new Evidence(); var edit = new ClipEdit();
            if(mode == "diskChanged") AssetDatabase.disk.File.Digest = "alien";
            if(mode == "wrongCurves") edit.Clip.curves = "alien";
            bool rejected = false;
            try { SaveConvertedClips(new List<ClipEdit>{edit}); } catch(InvalidOperationException) { rejected = true; }
            if(rejected != (mode != "ok")) throw new Exception("unexpected outcome: " + mode);
            if(mode == "ok" && (edit.SavedDigest != "after" || AssetDatabase.imports != 1 || AssetDatabase.saves != 1)) throw new Exception("persistence not read back");
            if((mode == "diskChanged" || mode == "wrongCurves") && AssetDatabase.saves != 0) throw new Exception("write before validation");
            if(!AssetDatabase.unrelated.dirty) throw new Exception("unrelated asset flushed");
          }
          return 0;
        }
      }
    ''')

