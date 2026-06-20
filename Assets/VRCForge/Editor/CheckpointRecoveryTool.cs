using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_prepare_checkpoint",
        Description = "Save open project scenes and dirty assets immediately before VRCForge creates a rollback checkpoint. Internal safety tool."
    )]
    public static class CheckpointPrepareTool
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                ValidateProject(@params);
                EnsureEditorReady();
                AssetDatabase.SaveAssets();
                if (!EditorSceneManager.SaveOpenScenes())
                {
                    return new ErrorResponse("Could not save all open scenes before checkpointing.");
                }
                var scenes = OpenProjectScenePaths();
                return new SuccessResponse(
                    "Saved open scenes and dirty assets before checkpointing.",
                    new { ok = true, projectPath = ProjectRoot(), scenes });
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
            var scenes = new List<string>();
            for (var index = 0; index < SceneManager.sceneCount; index++)
            {
                var scene = SceneManager.GetSceneAt(index);
                if (scene.IsValid() && scene.isLoaded && !string.IsNullOrWhiteSpace(scene.path)
                    && scene.path.StartsWith("Assets/", StringComparison.Ordinal))
                {
                    scenes.Add(scene.path);
                }
            }
            return scenes;
        }
    }

    [McpForUnityTool(
        name: "vrc_reload_after_checkpoint_restore",
        Description = "Reload restored project scenes and refresh assets after VRCForge rollback. Internal safety tool."
    )]
    public static class CheckpointReloadTool
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
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
                    new { ok = true, projectPath = CheckpointPrepareTool.ProjectRoot(), scenes });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Checkpoint reload failed: {ex.Message}");
            }
        }
    }
}
