using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using VRCForge.Core.MCP;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace VRCForge.Editor
{
    [VRCForgeTool(
        name: "vrc_prepare_checkpoint",
        Description = "Save open project scenes and dirty assets immediately before VRCForge creates a rollback checkpoint. Internal safety tool."
    )]
    public static class CheckpointPrepareTool
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                var identity = PrimitiveBasisLiveGuard.RequireBoundRequest(@params);
                ValidateProject(@params);
                EnsureEditorReady();

                var loadedScenes = LoadedScenes();
                var unsavedScenes = loadedScenes
                    .Where(scene => string.IsNullOrWhiteSpace(scene.path))
                    .Select((scene, index) => new
                    {
                        index,
                        name = string.IsNullOrWhiteSpace(scene.name) ? "Untitled" : scene.name
                    })
                    .ToList();
                if (unsavedScenes.Count > 0)
                {
                    return new ErrorResponse(
                        "unsaved_open_scene",
                        new
                        {
                            message = "Save every open scene before an App-approved write so VRCForge can create a recoverable checkpoint.",
                            blocking = true,
                            recoverable = false,
                            scenes = unsavedScenes
                        });
                }

                var unsupportedScenes = loadedScenes
                    .Where(scene => !scene.path.StartsWith("Assets/", StringComparison.Ordinal))
                    .Select(scene => scene.path)
                    .ToList();
                if (unsupportedScenes.Count > 0)
                {
                    return new ErrorResponse(
                        "scene_outside_project_assets",
                        new
                        {
                            message = "Every open scene must be saved under this project's Assets folder before an App-approved write.",
                            blocking = true,
                            recoverable = false,
                            scenes = unsupportedScenes
                        });
                }

                foreach (var scene in loadedScenes)
                {
                    if (!EditorSceneManager.SaveScene(scene))
                    {
                        return new ErrorResponse(
                            "scene_save_failed",
                            new
                            {
                                message = $"Unity could not save the open scene '{scene.path}' before checkpointing.",
                                blocking = true,
                                recoverable = false,
                                scene = scene.path
                            });
                    }
                }
                AssetDatabase.SaveAssets();
                var scenes = loadedScenes.Select(scene => scene.path).ToList();
                return new SuccessResponse(
                    "Saved open scenes and dirty assets before checkpointing.",
                    new
                    {
                        ok = true,
                        projectPath = ProjectRoot(),
                        scenes,
                        unityProcessId = identity?.ProcessId,
                        unityProcessStartedAtUtc = identity?.StartedAtUtc,
                        unityExecutableDigest = identity?.ExecutableDigest,
                        projectPathDigest = identity?.ProjectPathDigest
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Checkpoint preparation failed: {ex.Message}");
            }
        }

        internal static void ValidateProject(JObject @params)
        {
            var expected = (@params?["projectPath"]?.ToString() ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(expected)) { return; }
            var actual = Path.GetFullPath(ProjectRoot()).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            var requested = Path.GetFullPath(expected).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            if (!string.Equals(actual, requested, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException($"Active Unity project '{actual}' does not match checkpoint project '{requested}'.");
            }
        }

        internal static void EnsureEditorReady()
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode)
            {
                throw new InvalidOperationException("Checkpoint operations are unavailable while entering or running Play Mode.");
            }
            if (EditorApplication.isCompiling)
            {
                throw new InvalidOperationException("Checkpoint operations are unavailable while Unity is compiling.");
            }
        }

        internal static string ProjectRoot()
        {
            return Path.GetFullPath(Path.Combine(Application.dataPath, "..")).Replace("\\", "/");
        }

        internal static List<string> OpenProjectScenePaths()
        {
            return LoadedScenes()
                .Where(scene => !string.IsNullOrWhiteSpace(scene.path)
                    && scene.path.StartsWith("Assets/", StringComparison.Ordinal))
                .Select(scene => scene.path)
                .ToList();
        }

        internal static List<Scene> LoadedScenes()
        {
            var scenes = new List<Scene>();
            for (var index = 0; index < SceneManager.sceneCount; index++)
            {
                var scene = SceneManager.GetSceneAt(index);
                if (scene.IsValid() && scene.isLoaded)
                {
                    scenes.Add(scene);
                }
            }
            return scenes;
        }
    }

    [VRCForgeTool(
        name: "vrc_reload_after_checkpoint_restore",
        Description = "Reload restored project scenes and refresh assets after VRCForge rollback. Internal safety tool."
    )]
    public static class CheckpointReloadTool
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                var identity = PrimitiveBasisLiveGuard.RequireBoundRequest(@params);
                CheckpointPrepareTool.ValidateProject(@params);
                CheckpointPrepareTool.EnsureEditorReady();
                var scenes = CheckpointPrepareTool.OpenProjectScenePaths()
                    .Where(path => File.Exists(Path.Combine(CheckpointPrepareTool.ProjectRoot(), path)))
                    .ToList();

                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                if (scenes.Count > 0)
                {
                    // Keep a scratch scene loaded while the dirty project scenes
                    // are closed without saving, then open the restored files.
                    var scratch = EditorSceneManager.NewScene(
                        NewSceneSetup.EmptyScene,
                        NewSceneMode.Additive);
                    SceneManager.SetActiveScene(scratch);

                    var loadedProjectScenes = new List<Scene>();
                    for (var index = 0; index < SceneManager.sceneCount; index++)
                    {
                        var scene = SceneManager.GetSceneAt(index);
                        if (scene.IsValid() && scene.isLoaded && scenes.Contains(scene.path))
                        {
                            loadedProjectScenes.Add(scene);
                        }
                    }
                    foreach (var scene in loadedProjectScenes)
                    {
                        if (!EditorSceneManager.CloseScene(scene, true))
                        {
                            throw new InvalidOperationException(
                                $"Could not close dirty scene without saving: {scene.path}");
                        }
                    }

                    Scene firstRestored = default;
                    foreach (var path in scenes)
                    {
                        var restored = EditorSceneManager.OpenScene(path, OpenSceneMode.Additive);
                        if (!firstRestored.IsValid())
                        {
                            firstRestored = restored;
                        }
                    }
                    if (firstRestored.IsValid())
                    {
                        SceneManager.SetActiveScene(firstRestored);
                    }
                    EditorSceneManager.CloseScene(scratch, true);
                }
                return new SuccessResponse(
                    "Reloaded restored scenes and refreshed project assets.",
                    new
                    {
                        ok = true,
                        projectPath = CheckpointPrepareTool.ProjectRoot(),
                        scenes,
                        unityProcessId = identity?.ProcessId,
                        unityProcessStartedAtUtc = identity?.StartedAtUtc,
                        unityExecutableDigest = identity?.ExecutableDigest,
                        projectPathDigest = identity?.ProjectPathDigest
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Checkpoint reload failed: {ex.Message}");
            }
        }
    }
}
