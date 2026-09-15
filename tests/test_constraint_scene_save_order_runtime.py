"""Execute real constraint save sections with deferred Undo; not live Unity proof."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("method,next_marker", [
    ("private static object Apply(", "var readback = CaptureSnapshot("),
    ("private static bool TryRestoreBeforeSources(", "var restored = CaptureSnapshot("),
])
def test_constraint_save_flushes_deferred_undo(tmp_path, method, next_marker):
    path = "Assets/VRCForge/Editor/ConstraintSourceTool.cs"
    revision = os.environ.get("VRCFORGE_CONSTRAINT_SAVE_GIT_REF")
    source = (subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT,
              text=True, encoding="utf-8") if revision else (ROOT / path).read_text(encoding="utf-8"))
    start = source.index("EditorUtility.SetDirty(snapshot.Component);", source.index(method))
    section = source[start:source.index(next_marker, start)]
    code = r'''
using System;
class Scene { public bool dirty; }
class Saved { public Scene Scene = new Scene(); }
class Snapshot { public object Component = new object(); public Saved Scene = new Saved(); }
class EditorUtility { public static void SetDirty(object value) {} }
class Undo {
 public static bool pending;
 public static void FlushUndoRecordObjects() {
  if(pending) Probe.snapshot.Scene.Scene.dirty = true;
  pending = false;
 }
}
class EditorSceneManager {
 public static bool Fail;
 public static void MarkSceneDirty(Scene scene) { scene.dirty = true; }
 public static bool SaveScene(Scene scene) {
  if(Fail) return false;
  scene.dirty = false; return true;
 }
}
class ConstraintSourceToolException : Exception {
 public ConstraintSourceToolException(string message) : base(message) {}
}
class Probe {
 public static Snapshot snapshot = new Snapshot();
 static bool Save() {
''' + section + r'''
 return true;
 }
 static int Main() {
  Undo.pending = true;
  if(!Save()) throw new Exception("valid save rejected");
  Undo.FlushUndoRecordObjects();
  if(snapshot.Scene.Scene.dirty) throw new Exception("saved scene became dirty after deferred Undo");
  EditorSceneManager.Fail = true;
  bool rejected = false;
  try { rejected = !Save(); } catch(ConstraintSourceToolException) { rejected = true; }
  if(!rejected) throw new Exception("save failure accepted");
  return 0;
 }
}
'''
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compiler = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))[-1]
    references = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))[-1]
    dotnet = shutil.which("dotnet")
    probe = tmp_path / "Probe.cs"
    probe.write_text(code, encoding="utf-8")
    assembly = tmp_path / "Probe.dll"
    result = subprocess.run([dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+",
        f"-out:{assembly}", *[f"-r:{p}" for p in references.glob("*.dll")], str(probe)],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}), encoding="utf-8")
    result = subprocess.run([dotnet, str(assembly)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
