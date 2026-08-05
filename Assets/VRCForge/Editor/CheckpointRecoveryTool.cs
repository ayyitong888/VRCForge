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
    [VRCForgeCommand(
        toolId: "vrc_prepare_checkpoint",
        Summary = "Save open project scenes and dirty assets immediately before VRCForge creates a rollback checkpoint. Internal safety tool."
    )]
    public static class CheckpointPrepareTool
    {
        private const string NdmfPreviewSceneGuid = "8cbd3f19cef3477439841053ced0661b";

        public class Parameters
        {
            [VRCForgeInput("Optional exact active Unity project root.", IsRequired = false)] public string projectPath { get; set; } = "";
            [VRCForgeInput("Live-run SHA-256 digest when a bound fixture request is used.", IsRequired = false)] public string expectedRunIdDigest { get; set; } = "";
            [VRCForgeInput("Expected Unity project-root SHA-256 digest.", IsRequired = false)] public string expectedProjectPathDigest { get; set; } = "";
            [VRCForgeInput("Expected Unity process id.", IsRequired = false)] public int? expectedUnityProcessId { get; set; }
            [VRCForgeInput("Expected Unity process start time in UTC.", IsRequired = false)] public string expectedUnityProcessStartedAtUtc { get; set; } = "";
            [VRCForgeInput("Expected Unity executable SHA-256 digest.", IsRequired = false)] public string expectedUnityExecutableDigest { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var identity = PrimitiveBasisLiveGuard.RequireBoundRequest(@params);
                ValidateProject(@params);
                EnsureEditorReady();

                var allLoadedScenes = LoadedScenes();
                var ignoredTransientScenes = allLoadedScenes
                    .Where(IsKnownTransientPreviewScene)
                    .Select(scene => scene.path)
                    .ToList();
                var loadedScenes = allLoadedScenes
                    .Where(scene => !IsKnownTransientPreviewScene(scene))
                    .ToList();
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
                    return VRCForgeToolResult.Failed(
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
                    return VRCForgeToolResult.Failed(
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
                        return VRCForgeToolResult.Failed(
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
                return VRCForgeToolResult.Completed(
                    "Saved open scenes and dirty assets before checkpointing.",
                    new
                    {
                        ok = true,
                        projectPath = ProjectRoot(),
                        scenes,
                        ignoredTransientScenes,
                        unityProcessId = identity?.ProcessId,
                        unityProcessStartedAtUtc = identity?.StartedAtUtc,
                        unityExecutableDigest = identity?.ExecutableDigest,
                        projectPathDigest = identity?.ProjectPathDigest
                    });
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Checkpoint preparation failed: {ex.Message}");
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

        internal static bool IsKnownTransientPreviewScene(Scene scene)
        {
            if (!scene.IsValid() || !scene.isLoaded || scene.isDirty || !scene.isSubScene
                || scene == SceneManager.GetActiveScene()
                || !string.Equals(scene.name, "___NDMF Preview___", StringComparison.Ordinal))
            {
                return false;
            }
            var expectedPath = AssetDatabase.GUIDToAssetPath(NdmfPreviewSceneGuid);
            return !string.IsNullOrWhiteSpace(expectedPath)
                && string.Equals(scene.path, expectedPath, StringComparison.Ordinal);
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

    [VRCForgeCommand(
        toolId: "vrc_reload_after_checkpoint_restore",
        Summary = "Reload restored project scenes and refresh assets after VRCForge rollback. Internal safety tool."
    )]
    public static class CheckpointReloadTool
    {
        public class Parameters
        {
            [VRCForgeInput("Optional exact active Unity project root.", IsRequired = false)] public string projectPath { get; set; } = "";
            [VRCForgeInput("Internal restore phase: prepare_restore or reload.", IsRequired = false)] public string phase { get; set; } = "reload";
            [VRCForgeInput("Exact project scene paths captured before restore.", IsRequired = false)] public List<string> scenePaths { get; set; } = new List<string>();
            [VRCForgeInput("Exact active project scene path captured before restore.", IsRequired = false)] public string activeScenePath { get; set; } = "";
            [VRCForgeInput("Live-run SHA-256 digest when a bound fixture request is used.", IsRequired = false)] public string expectedRunIdDigest { get; set; } = "";
            [VRCForgeInput("Expected Unity project-root SHA-256 digest.", IsRequired = false)] public string expectedProjectPathDigest { get; set; } = "";
            [VRCForgeInput("Expected Unity process id.", IsRequired = false)] public int? expectedUnityProcessId { get; set; }
            [VRCForgeInput("Expected Unity process start time in UTC.", IsRequired = false)] public string expectedUnityProcessStartedAtUtc { get; set; } = "";
            [VRCForgeInput("Expected Unity executable SHA-256 digest.", IsRequired = false)] public string expectedUnityExecutableDigest { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var identity = PrimitiveBasisLiveGuard.RequireBoundRequest(@params);
                CheckpointPrepareTool.ValidateProject(@params);
                CheckpointPrepareTool.EnsureEditorReady();
                var phase = (@params?["phase"]?.ToString() ?? "reload").Trim();
                if (string.Equals(phase, "prepare_restore", StringComparison.Ordinal))
                {
                    var loaded = CheckpointPrepareTool.LoadedScenes()
                        .Where(scene => !CheckpointPrepareTool.IsKnownTransientPreviewScene(scene))
                        .ToList();
                    var unsaved = loaded
                        .Where(scene => string.IsNullOrWhiteSpace(scene.path))
                        .Select(scene => string.IsNullOrWhiteSpace(scene.name) ? "Untitled" : scene.name)
                        .ToList();
                    if (unsaved.Count > 0)
                    {
                        return VRCForgeToolResult.Failed(
                            "Checkpoint restore cannot close unsaved scenes.",
                            new { phase, blocking = true, scenes = unsaved });
                    }
                    var unsupported = loaded
                        .Where(scene => !scene.path.StartsWith("Assets/", StringComparison.Ordinal))
                        .Select(scene => scene.path)
                        .ToList();
                    if (unsupported.Count > 0)
                    {
                        return VRCForgeToolResult.Failed(
                            "Checkpoint restore cannot close scenes outside project Assets.",
                            new { phase, blocking = true, scenes = unsupported });
                    }
                    var activeScene = SceneManager.GetActiveScene();
                    var prepareActiveScenePath = loaded.Any(scene => scene == activeScene)
                        ? activeScene.path
                        : string.Empty;
                    if (loaded.Count > 0 && string.IsNullOrWhiteSpace(prepareActiveScenePath))
                    {
                        return VRCForgeToolResult.Failed(
                            "Checkpoint restore could not bind the active project scene.",
                            new { phase, blocking = true });
                    }
                    var prepareScenes = loaded.Select(scene => scene.path).Distinct().ToList();
                    var closedScenes = new List<string>();
                    Scene scratch = default;
                    try
                    {
                        if (prepareScenes.Count > 0)
                        {
                            scratch = EditorSceneManager.NewScene(
                                NewSceneSetup.EmptyScene,
                                NewSceneMode.Additive);
                            SceneManager.SetActiveScene(scratch);
                            foreach (var scene in loaded)
                            {
                                var path = scene.path;
                                if (!EditorSceneManager.CloseScene(scene, true))
                                {
                                    throw new InvalidOperationException(
                                        $"Could not close scene without saving: {path}");
                                }
                                closedScenes.Add(path);
                            }
                        }
                    }
                    catch (Exception closeError)
                    {
                        var reopenErrors = new List<string>();
                        foreach (var path in closedScenes)
                        {
                            try
                            {
                                EditorSceneManager.OpenScene(path, OpenSceneMode.Additive);
                            }
                            catch (Exception reopenError)
                            {
                                reopenErrors.Add($"{path}: {reopenError.Message}");
                            }
                        }
                        var restoredActive = SceneManager.GetSceneByPath(prepareActiveScenePath);
                        if (restoredActive.IsValid() && restoredActive.isLoaded)
                        {
                            SceneManager.SetActiveScene(restoredActive);
                        }
                        if (scratch.IsValid() && scratch.isLoaded
                            && CheckpointPrepareTool.LoadedScenes().Any(scene => scene != scratch))
                        {
                            EditorSceneManager.CloseScene(scratch, true);
                        }
                        return VRCForgeToolResult.Failed(
                            "Checkpoint restore could not safely close all project scenes.",
                            new
                            {
                                phase,
                                blocking = true,
                                closedScenes,
                                reopenErrors,
                                error = closeError.Message
                            });
                    }
                    return VRCForgeToolResult.Completed(
                        "Closed project scenes before checkpoint file recovery.",
                        new
                        {
                            ok = true,
                            phase,
                            projectPath = CheckpointPrepareTool.ProjectRoot(),
                            scenes = prepareScenes,
                            activeScenePath = prepareActiveScenePath,
                            unityProcessId = identity?.ProcessId,
                            unityProcessStartedAtUtc = identity?.StartedAtUtc,
                            unityExecutableDigest = identity?.ExecutableDigest,
                            projectPathDigest = identity?.ProjectPathDigest
                        });
                }
                if (!string.Equals(phase, "reload", StringComparison.Ordinal))
                {
                    return VRCForgeToolResult.Failed($"Unknown checkpoint restore phase: {phase}");
                }

                var requested = @params?["scenePaths"] as JArray;
                var scenes = requested == null
                    ? new List<string>()
                    : requested.Values<string>()
                        .Where(path => !string.IsNullOrWhiteSpace(path))
                        .Select(path => path.Replace('\\', '/').Trim())
                        .Distinct()
                        .ToList();
                if (requested == null)
                {
                    scenes = CheckpointPrepareTool.OpenProjectScenePaths();
                }
                var activeScenePath = (@params?["activeScenePath"]?.ToString() ?? string.Empty)
                    .Replace('\\', '/')
                    .Trim();
                if (requested != null && scenes.Count > 0
                    && (string.IsNullOrWhiteSpace(activeScenePath) || !scenes.Contains(activeScenePath)))
                {
                    return VRCForgeToolResult.Failed(
                        "Checkpoint reload active scene does not match the prepared scene set.",
                        new { phase, blocking = true, activeScenePath, scenes });
                }
                if (string.IsNullOrWhiteSpace(activeScenePath) && scenes.Count > 0)
                {
                    activeScenePath = scenes[0];
                }
                foreach (var path in scenes)
                {
                    if (!path.StartsWith("Assets/", StringComparison.Ordinal)
                        || !File.Exists(Path.Combine(CheckpointPrepareTool.ProjectRoot(), path)))
                    {
                        return VRCForgeToolResult.Failed(
                            "Checkpoint reload scene path is unavailable.",
                            new { phase, blocking = true, scene = path });
                    }
                }

                var scratchScenes = CheckpointPrepareTool.LoadedScenes()
                    .Where(scene => string.IsNullOrWhiteSpace(scene.path))
                    .ToList();
                if (scratchScenes.Count == 0 && scenes.Count > 0)
                {
                    var scratch = EditorSceneManager.NewScene(
                        NewSceneSetup.EmptyScene,
                        NewSceneMode.Additive);
                    SceneManager.SetActiveScene(scratch);
                    scratchScenes.Add(scratch);
                }
                var closedBeforeReload = new List<string>();
                try
                {
                    foreach (var scene in CheckpointPrepareTool.LoadedScenes()
                        .Where(scene => scenes.Contains(scene.path)).ToList())
                    {
                        var path = scene.path;
                        if (!EditorSceneManager.CloseScene(scene, true))
                        {
                            throw new InvalidOperationException(
                                $"Could not close scene before restored reload: {path}");
                        }
                        closedBeforeReload.Add(path);
                    }
                }
                catch (Exception closeError)
                {
                    var reopenErrors = new List<string>();
                    foreach (var path in closedBeforeReload)
                    {
                        try
                        {
                            EditorSceneManager.OpenScene(path, OpenSceneMode.Additive);
                        }
                        catch (Exception reopenError)
                        {
                            reopenErrors.Add($"{path}: {reopenError.Message}");
                        }
                    }
                    return VRCForgeToolResult.Failed(
                        "Checkpoint files were restored, but Unity could not close stale scene state before reload.",
                        new
                        {
                            phase,
                            blocking = true,
                            recoveryRequired = true,
                            scenes,
                            activeScenePath,
                            closedScenes = closedBeforeReload,
                            reopenErrors,
                            error = closeError.Message
                        });
                }

                var restoredScenes = new List<Scene>();
                try
                {
                    foreach (var path in scenes)
                    {
                        restoredScenes.Add(EditorSceneManager.OpenScene(path, OpenSceneMode.Additive));
                    }
                }
                catch (Exception reopenError)
                {
                    foreach (var restored in restoredScenes.Where(scene => scene.IsValid() && scene.isLoaded))
                    {
                        EditorSceneManager.CloseScene(restored, true);
                    }
                    return VRCForgeToolResult.Failed(
                        "Checkpoint files were restored, but Unity could not reopen every scene.",
                        new
                        {
                            phase,
                            blocking = true,
                            recoveryRequired = true,
                            scenes,
                            activeScenePath,
                            error = reopenError.Message
                        });
                }
                if (restoredScenes.Count > 0)
                {
                    var restoredActive = restoredScenes.FirstOrDefault(
                        scene => string.Equals(scene.path, activeScenePath, StringComparison.Ordinal));
                    if (!restoredActive.IsValid() || !SceneManager.SetActiveScene(restoredActive))
                    {
                        var fallback = restoredScenes.FirstOrDefault(scene => scene.IsValid() && scene.isLoaded);
                        if (fallback.IsValid() && SceneManager.SetActiveScene(fallback))
                        {
                            foreach (var scratch in scratchScenes.Where(scene => scene.IsValid() && scene.isLoaded))
                            {
                                EditorSceneManager.CloseScene(scratch, true);
                            }
                        }
                        return VRCForgeToolResult.Failed(
                            "Checkpoint files were restored, but Unity could not reactivate the original scene.",
                            new { phase, blocking = true, recoveryRequired = true, scenes, activeScenePath });
                    }
                    foreach (var scratch in scratchScenes.Where(scene => scene.IsValid() && scene.isLoaded))
                    {
                        EditorSceneManager.CloseScene(scratch, true);
                    }
                }
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                return VRCForgeToolResult.Completed(
                    "Reloaded restored scenes and refreshed project assets.",
                    new
                    {
                        ok = true,
                        phase,
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
                return VRCForgeToolResult.Failed($"Checkpoint reload failed: {ex.Message}");
            }
        }
    }
}
