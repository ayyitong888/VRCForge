"""Run the production save method through deferred-Undo persistence seams."""
from pathlib import Path
import os
import subprocess

from test_constraint_conversion_scope_runtime import run
from test_setup_outfit_dispatch_runtime import extract_method


def test_setup_save_finishes_undo_and_verifies_clean_disk_state(tmp_path):
    root = Path(__file__).resolve().parents[1]
    relative = "Assets/VRCForge/Editor/SetupOutfitTool.cs"
    revision = os.environ.get("VRCFORGE_SETUP_SAVE_GIT_REF")
    source = (subprocess.check_output(["git", "show", f"{revision}:{relative}"], cwd=root,
                                     text=True, encoding="utf-8") if revision else
              (root / relative).read_text(encoding="utf-8-sig"))
    method = extract_method(source, "private static bool SaveTargetScene")
    run(tmp_path, r'''
using System;
class Scene {
    public string path = "Assets/AvatarScene.unity";
    public int handle = 7;
    public bool dirty;
    public bool IsValid() { return true; }
}
static class Undo {
    public static bool pending;
    public static Scene target;
    public static void FlushUndoRecordObjects() {
        if (pending) target.dirty = true;
        pending = false;
    }
}
static class EditorSceneManager {
    public static bool fail, leaveDirty;
    public static bool SaveScene(Scene scene) {
        if (fail) return false;
        scene.dirty = leaveDirty;
        return true;
    }
}
static class SceneObjectCopyCore {
    public sealed class Saved { public Scene Scene; }
    public static bool missingDisk;
    public static Saved ResolveSavedScene(string path, string label) {
        if (missingDisk || Undo.target.dirty) throw new InvalidOperationException("saved scene evidence unavailable");
        return new Saved { Scene = Undo.target };
    }
}
class Probe {
''' + method + r'''
    static void RequireRejected(Action action, string message) {
        bool rejected = false;
        try { action(); } catch (InvalidOperationException) { rejected = true; }
        if (!rejected) throw new Exception(message);
    }
    static int Main() {
        var scene = new Scene { dirty = true };
        Undo.target = scene; Undo.pending = true;
        if (!SaveTargetScene(scene)) throw new Exception("save not reported");
        Undo.FlushUndoRecordObjects(); // Unity finalizes recorded edits at end of frame.
        if (scene.dirty) throw new Exception("deferred Undo dirtied scene after reported save");
        EditorSceneManager.fail = true;
        RequireRejected(() => SaveTargetScene(scene), "failed save reported success");
        EditorSceneManager.fail = false; EditorSceneManager.leaveDirty = true;
        RequireRejected(() => SaveTargetScene(scene), "dirty post-save scene reported success");
        EditorSceneManager.leaveDirty = false; SceneObjectCopyCore.missingDisk = true;
        RequireRejected(() => SaveTargetScene(scene), "missing disk scene reported success");
        return 0;
    }
}
''')
