using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    internal static class Stage1SceneToolCore
    {
        internal const string SaveSchema = "vrcforge.scene_save.v1";
        internal const string TransitionSchema = "vrcforge.scene_transition.v1";

        internal static string ProjectPath()
        {
            return CheckpointPrepareTool.ProjectRoot().TrimEnd('/');
        }

        internal static string ScenePath(JObject p, string name, bool required)
        {
            var value = p[name]?.ToString() ?? string.Empty;
            if (!required && string.IsNullOrWhiteSpace(value)) return string.Empty;
            return SceneObjectCopyCore.NormalizeSceneAssetPath(value, name);
        }

        internal static bool IsHex(string value, int length)
        {
            return !string.IsNullOrEmpty(value) && value.Length == length
                && value.All(c => (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
        }

        internal static void RequireExpected(JObject p, string name, string value, int length = -1)
        {
            var actual = p["expected" + name];
            var text = actual?.ToString() ?? string.Empty;
            if (actual == null || actual.Type != JTokenType.String
                || (length > 0 && !IsHex(text, length))
                || !string.Equals(text, value ?? string.Empty, StringComparison.Ordinal))
                throw new SceneObjectCopyException("The approved scene preview evidence changed before apply.");
        }

        internal static void RequireExpectedBool(JObject p, string name, bool value)
        {
            var actual = p["expected" + name];
            if (actual == null || actual.Type != JTokenType.Boolean || actual.Value<bool>() != value)
                throw new SceneObjectCopyException("The approved scene dirty-state evidence changed before apply.");
        }

        internal static void RequireExpectedInt(JObject p, string name, int value)
        {
            var actual = p["expected" + name];
            if (actual == null || actual.Type != JTokenType.Integer || actual.Value<int>() != value)
                throw new SceneObjectCopyException("The approved scene identity changed before apply.");
        }

        internal static string SetupDigest()
        {
            var setup = EditorSceneManager.GetSceneManagerSetup() ?? new SceneSetup[0];
            var fields = new List<string> { "vrcforge.stage1.scene_setup.v1", setup.Length.ToString(CultureInfo.InvariantCulture) };
            foreach (var item in setup)
            {
                fields.Add((item.path ?? string.Empty).Replace('\\', '/'));
                fields.Add(item.isLoaded ? "true" : "false");
                fields.Add(item.isActive ? "true" : "false");
            }
            return Digest(fields);
        }

        internal static string OpenStateDigest()
        {
            var scenes = CheckpointPrepareTool.LoadedScenes();
            var fields = new List<string> { "vrcforge.stage1.open_scene_state.v1", scenes.Count.ToString(CultureInfo.InvariantCulture) };
            foreach (var scene in scenes)
            {
                fields.Add((scene.path ?? string.Empty).Replace('\\', '/'));
                fields.Add(scene.name ?? string.Empty);
                fields.Add(scene.handle.ToString(CultureInfo.InvariantCulture));
                fields.Add(scene.isDirty ? "true" : "false");
                fields.Add(scene.rootCount.ToString(CultureInfo.InvariantCulture));
                fields.Add(SceneManager.GetActiveScene().handle == scene.handle ? "true" : "false");
            }
            return Digest(fields);
        }

        internal static string HierarchyDigest(Scene scene)
        {
            var fields = new List<string> { "vrcforge.stage1.scene_hierarchy.v1" };
            var roots = scene.GetRootGameObjects();
            fields.Add(roots.Length.ToString(CultureInfo.InvariantCulture));
            for (var i = 0; i < roots.Length; i++)
            {
                fields.Add(i.ToString(CultureInfo.InvariantCulture));
                fields.Add(SceneObjectCopyCore.ComputeHierarchyDigest(roots[i]));
            }
            return Digest(fields);
        }

        internal static string Digest(IEnumerable<string> fields)
        {
            var value = new StringBuilder();
            foreach (var field in fields)
            {
                var text = field ?? string.Empty;
                value.Append(Encoding.UTF8.GetByteCount(text).ToString(CultureInfo.InvariantCulture));
                value.Append(':').Append(text);
            }
            using (var sha = SHA256.Create())
                return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(value.ToString())).Select(b => b.ToString("x2", CultureInfo.InvariantCulture)));
        }

        internal static object Binding(Dictionary<string, object> values)
        {
            return values.ToDictionary(item => item.Key, item => item.Value);
        }

        internal static object Readback(Scene scene)
        {
            return new
            {
                path = (scene.path ?? string.Empty).Replace('\\', '/'),
                name = scene.name ?? string.Empty,
                handle = scene.handle,
                loaded = scene.IsValid() && scene.isLoaded,
                active = scene.IsValid() && scene.isLoaded && SceneManager.GetActiveScene().handle == scene.handle,
                dirty = scene.IsValid() && scene.isLoaded && scene.isDirty,
                rootObjectCount = scene.IsValid() && scene.isLoaded ? scene.rootCount : 0,
                hierarchyDigest = scene.IsValid() && scene.isLoaded ? HierarchyDigest(scene) : string.Empty
            };
        }

        internal static object NoMutation(string schema, string operation, string projectPath, string digest, string message, bool ok = false)
        {
            return new
            {
                schema, operation, ok, preview = false, verified = false, changed = false,
                mutationStarted = false, commitState = "not_started", projectPath,
                previewDigest = digest ?? string.Empty, applyBinding = new { }, readback = new { },
                message
            };
        }

        internal static object UnknownAfterMutation(string schema, string operation, string projectPath, string message)
        {
            return new
            {
                schema, operation, ok = false, preview = false, verified = false, changed = true,
                mutationStarted = true, commitState = "unknown", projectPath,
                previewDigest = string.Empty, applyBinding = new { }, readback = new { },
                checkpointRecoveryRequired = true, message
            };
        }
    }

    [VRCForgeCommand(toolId: "vrc_scene_save", Summary = "when-to-use: preview or explicitly approved save/save-as of Unity scenes with atomic receipt binding. when-NOT-to-use: never overwrite an existing destination or save a dirty scene implicitly.")]
    public static class SceneSaveTool
    {
        public class Parameters
        {
            [VRCForgeInput("save or save_as.", IsRequired = true)] public string action { get; set; } = "save";
            [VRCForgeInput("Exact active saved scene path for save, or source scene path for save_as.", IsRequired = true)] public string scenePath { get; set; } = "";
            [VRCForgeInput("Absent destination scene path for save_as.", IsRequired = false)] public string destinationScenePath { get; set; } = "";
            [VRCForgeInput("Return a non-mutating preview.", IsRequired = false)] public bool? preview { get; set; } = false;
            [VRCForgeInput("Expected active Unity project root from preview.", IsRequired = false)] public string expectedProjectPath { get; set; } = "";
            [VRCForgeInput("Expected preview digest.", IsRequired = false)] public string expectedPreviewDigest { get; set; } = "";
            [VRCForgeInput("Expected scene handle.", IsRequired = false)] public int? expectedSceneHandle { get; set; }
            [VRCForgeInput("Expected scene GUID.", IsRequired = false)] public string expectedSceneGuid { get; set; } = "";
            [VRCForgeInput("Expected scene file digest.", IsRequired = false)] public string expectedSceneFileDigest { get; set; } = "";
            [VRCForgeInput("Expected scene file identity.", IsRequired = false)] public string expectedSceneFileIdentity { get; set; } = "";
            [VRCForgeInput("Expected scene metadata digest.", IsRequired = false)] public string expectedSceneMetaDigest { get; set; } = "";
            [VRCForgeInput("Expected scene metadata identity.", IsRequired = false)] public string expectedSceneMetaIdentity { get; set; } = "";
            [VRCForgeInput("Expected scene manager setup digest.", IsRequired = false)] public string expectedSceneSetupDigest { get; set; } = "";
            [VRCForgeInput("Expected open-scene state digest.", IsRequired = false)] public string expectedOpenSceneStateDigest { get; set; } = "";
            [VRCForgeInput("Expected dirty flag.", IsRequired = false)] public bool? expectedSceneDirty { get; set; }
        }

        public static object HandleCommand(JObject p)
        {
            var parameters = p ?? new JObject();
            var preview = parameters["preview"]?.Value<bool?>() ?? false;
            var action = (parameters["action"]?.ToString() ?? "").Trim().ToLowerInvariant();
            var project = Stage1SceneToolCore.ProjectPath();
            var mutationStarted = false;
            try
            {
                CheckpointPrepareTool.EnsureEditorReady();
                if (action != "save" && action != "save_as") throw new SceneObjectCopyException("action must be save or save_as.");
                var scenePath = Stage1SceneToolCore.ScenePath(parameters, "scenePath", true);
                var destination = action == "save_as" ? Stage1SceneToolCore.ScenePath(parameters, "destinationScenePath", true) : string.Empty;
                var scene = SceneManager.GetActiveScene();
                if (!scene.IsValid() || !scene.isLoaded || string.IsNullOrWhiteSpace(scene.path)
                    || !string.Equals(scene.path.Replace('\\', '/'), scenePath, StringComparison.Ordinal))
                    throw new SceneObjectCopyException("scenePath must identify the active saved scene.");
                if (action == "save_as" && SceneObjectCopyCore.AssetOrMetaExists(destination))
                    throw new SceneObjectCopyException("The save_as destination or its metadata exists; overwrite is unsupported.");
                var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(scenePath, "scene asset");
                var hierarchy = Stage1SceneToolCore.HierarchyDigest(scene);
                var setup = Stage1SceneToolCore.SetupDigest();
                var open = Stage1SceneToolCore.OpenStateDigest();
                var digest = Stage1SceneToolCore.Digest(new[] { Stage1SceneToolCore.SaveSchema, action, project, scenePath, destination, scene.handle.ToString(CultureInfo.InvariantCulture), scene.isDirty ? "true" : "false", evidence.Guid, evidence.File.Digest, evidence.File.Identity, evidence.Meta.Digest, evidence.Meta.Identity, hierarchy, setup, open });
                var binding = new Dictionary<string, object>
                {
                    ["expectedSceneHandle"] = scene.handle, ["expectedSceneGuid"] = evidence.Guid,
                    ["expectedSceneFileDigest"] = evidence.File.Digest, ["expectedSceneFileIdentity"] = evidence.File.Identity,
                    ["expectedSceneMetaDigest"] = evidence.Meta.Digest, ["expectedSceneMetaIdentity"] = evidence.Meta.Identity,
                    ["expectedSceneDirty"] = scene.isDirty,
                    ["expectedSceneSetupDigest"] = setup,
                    ["expectedOpenSceneStateDigest"] = open
                };
                if (preview)
                    return VRCForgeToolResult.Completed("Scene save preview completed.", SavePayload(action, project, scenePath, destination, digest, binding, scene, evidence, hierarchy, setup, open));

                Stage1SceneToolCore.RequireExpected(parameters, "ProjectPath", project);
                Stage1SceneToolCore.RequireExpected(parameters, "PreviewDigest", digest, 64);
                Stage1SceneToolCore.RequireExpectedInt(parameters, "SceneHandle", scene.handle);
                Stage1SceneToolCore.RequireExpected(parameters, "SceneGuid", evidence.Guid, 32);
                Stage1SceneToolCore.RequireExpected(parameters, "SceneFileDigest", evidence.File.Digest, 64);
                Stage1SceneToolCore.RequireExpected(parameters, "SceneFileIdentity", evidence.File.Identity, 64);
                Stage1SceneToolCore.RequireExpected(parameters, "SceneMetaDigest", evidence.Meta.Digest, 64);
                Stage1SceneToolCore.RequireExpected(parameters, "SceneMetaIdentity", evidence.Meta.Identity, 64);
                Stage1SceneToolCore.RequireExpected(parameters, "SceneSetupDigest", setup);
                Stage1SceneToolCore.RequireExpected(parameters, "OpenSceneStateDigest", open);
                Stage1SceneToolCore.RequireExpectedBool(parameters, "SceneDirty", scene.isDirty);
                if (action == "save" && !scene.isDirty)
                    return VRCForgeToolResult.Completed("Scene is already clean; no save was needed.", SavePayload(action, project, scenePath, destination, digest, binding, scene, evidence, hierarchy, setup, open, false, true));
                if (action == "save_as" && SceneObjectCopyCore.AssetOrMetaExists(destination)) throw new SceneObjectCopyException("The save_as destination appeared after preview; overwrite is unsupported.");
                mutationStarted = true;
                if (!EditorSceneManager.SaveScene(scene, action == "save_as" ? destination : scenePath, action == "save_as" ? false : false))
                    throw new SceneObjectCopyException("Unity did not confirm the scene save.");
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                var loaded = SceneManager.GetSceneByPath(action == "save_as" ? destination : scenePath);
                var afterEvidence = SceneObjectCopyCore.ReadStableAssetEvidence(action == "save_as" ? destination : scenePath, "saved scene asset");
                if (!loaded.IsValid() || !loaded.isLoaded || loaded.isDirty || Stage1SceneToolCore.HierarchyDigest(loaded) != hierarchy)
                    throw new SceneObjectCopyException("Scene save readback did not match the approved scene.");
                return VRCForgeToolResult.Completed("Scene save committed and verified.", SavePayload(action, project, scenePath, destination, digest, binding, loaded, afterEvidence, hierarchy, Stage1SceneToolCore.SetupDigest(), Stage1SceneToolCore.OpenStateDigest(), true, false));
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.FailedWithCode(
                    mutationStarted ? "scene_save_failed_after_mutation" : "scene_save_rejected",
                    ex.Message,
                    mutationStarted
                        ? Stage1SceneToolCore.UnknownAfterMutation(Stage1SceneToolCore.SaveSchema, "scene_save_" + action, project, ex.Message)
                        : Stage1SceneToolCore.NoMutation(Stage1SceneToolCore.SaveSchema, action, project, string.Empty, ex.Message));
            }
        }

        private static object SavePayload(string action, string project, string scenePath, string destination, string digest, Dictionary<string, object> binding, Scene scene, StableAssetEvidence evidence, string hierarchy, string setup, string open, bool changed = false, bool noChange = false)
        {
            var actual = action == "save_as" ? destination : scenePath;
            return new { schema = Stage1SceneToolCore.SaveSchema, operation = "scene_save_" + action, ok = true, preview = !changed && !noChange, verified = true, changed, mutationStarted = changed, commitState = changed ? "committed" : noChange ? "no_change" : "not_started", projectPath = project, previewDigest = digest, applyBinding = Stage1SceneToolCore.Binding(binding), readback = new { path = actual, guid = evidence.Guid, fileDigest = evidence.File.Digest, fileIdentity = evidence.File.Identity, metaDigest = evidence.Meta.Digest, metaIdentity = evidence.Meta.Identity, scene = Stage1SceneToolCore.Readback(scene), hierarchyDigest = hierarchy, sceneSetupDigest = setup, openSceneStateDigest = open }, scenePath, destinationScenePath = destination, cleanNoChange = noChange, message = noChange ? "no change" : "verified" };
        }
    }

    [VRCForgeCommand(toolId: "vrc_scene_transition", Summary = "when-to-use: preview or explicitly approved open/additive/new/set-active/unload/reload saved-scene transitions with dirty-scene protection. when-NOT-to-use: never discard or implicitly save dirty changes, and never overwrite a new scene destination.")]
    public static class SceneTransitionTool
    {
        public class Parameters
        {
            [VRCForgeInput("open_single, open_additive, new_saved, set_active, unload, or reload_saved.", IsRequired = true)] public string action { get; set; } = "open_single";
            [VRCForgeInput("Exact scene path below Assets.", IsRequired = false)] public string scenePath { get; set; } = "";
            [VRCForgeInput("Absent destination for new_saved.", IsRequired = false)] public string destinationScenePath { get; set; } = "";
            [VRCForgeInput("Return a non-mutating preview.", IsRequired = false)] public bool? preview { get; set; } = false;
            [VRCForgeInput("Expected active Unity project root from preview.", IsRequired = false)] public string expectedProjectPath { get; set; } = "";
            [VRCForgeInput("Expected preview digest.", IsRequired = false)] public string expectedPreviewDigest { get; set; } = "";
            [VRCForgeInput("Expected scene manager setup digest.", IsRequired = false)] public string expectedSceneSetupDigest { get; set; } = "";
            [VRCForgeInput("Expected open-scene state digest.", IsRequired = false)] public string expectedOpenSceneStateDigest { get; set; } = "";
            [VRCForgeInput("Expected destination absent assertion.", IsRequired = false)] public bool? expectedDestinationAbsent { get; set; }
            [VRCForgeInput("Expected target Scene GUID.", IsRequired = false)] public string expectedSceneGuid { get; set; } = "";
            [VRCForgeInput("Expected target Scene file digest.", IsRequired = false)] public string expectedSceneFileDigest { get; set; } = "";
            [VRCForgeInput("Expected target Scene file identity.", IsRequired = false)] public string expectedSceneFileIdentity { get; set; } = "";
            [VRCForgeInput("Expected target Scene metadata digest.", IsRequired = false)] public string expectedSceneMetaDigest { get; set; } = "";
            [VRCForgeInput("Expected target Scene metadata identity.", IsRequired = false)] public string expectedSceneMetaIdentity { get; set; } = "";
        }

        public static object HandleCommand(JObject p)
        {
            var parameters = p ?? new JObject();
            var action = (parameters["action"]?.ToString() ?? "").Trim().ToLowerInvariant();
            var project = Stage1SceneToolCore.ProjectPath();
            var mutationStarted = false;
            try
            {
                CheckpointPrepareTool.EnsureEditorReady();
                var allowed = new[] { "open_single", "open_additive", "new_saved", "set_active", "unload", "reload_saved" };
                if (!allowed.Contains(action)) throw new SceneObjectCopyException("Unsupported scene transition action.");
                var path = Stage1SceneToolCore.ScenePath(parameters, "scenePath", action != "new_saved");
                var destination = action == "new_saved" ? Stage1SceneToolCore.ScenePath(parameters, "destinationScenePath", true) : string.Empty;
                if ((action == "open_single" || action == "new_saved" || action == "reload_saved") && CheckpointPrepareTool.LoadedScenes().Any(s => s.isDirty))
                    throw new SceneObjectCopyException("The transition would drop dirty scene changes; save them first.");
                if (action == "new_saved" && SceneObjectCopyCore.AssetOrMetaExists(destination))
                    throw new SceneObjectCopyException("The new_saved destination or its metadata exists; overwrite is unsupported.");
                if (action == "new_saved")
                {
                    var parent = Path.GetDirectoryName(SceneObjectCopyCore.ToAbsoluteAssetPath(destination));
                    if (string.IsNullOrWhiteSpace(parent) || !Directory.Exists(parent))
                        throw new SceneObjectCopyException("The new_saved destination folder must already exist.");
                }
                StableAssetEvidence targetEvidence = null;
                if (action != "new_saved")
                    targetEvidence = SceneObjectCopyCore.ReadStableAssetEvidence(path, "transition scene asset");
                var setup = Stage1SceneToolCore.SetupDigest();
                var open = Stage1SceneToolCore.OpenStateDigest();
                var absent = action == "new_saved" && !SceneObjectCopyCore.AssetOrMetaExists(destination);
                var digest = Stage1SceneToolCore.Digest(new[] { Stage1SceneToolCore.TransitionSchema, action, project, path, destination, setup, open, absent ? "true" : "false", targetEvidence == null ? "" : targetEvidence.Guid, targetEvidence == null ? "" : targetEvidence.File.Digest, targetEvidence == null ? "" : targetEvidence.File.Identity, targetEvidence == null ? "" : targetEvidence.Meta.Digest, targetEvidence == null ? "" : targetEvidence.Meta.Identity });
                var binding = new Dictionary<string, object> { ["expectedSceneSetupDigest"] = setup, ["expectedOpenSceneStateDigest"] = open };
                if (action == "new_saved") binding["expectedDestinationAbsent"] = true;
                if (targetEvidence != null)
                {
                    binding["expectedSceneGuid"] = targetEvidence.Guid;
                    binding["expectedSceneFileDigest"] = targetEvidence.File.Digest;
                    binding["expectedSceneFileIdentity"] = targetEvidence.File.Identity;
                    binding["expectedSceneMetaDigest"] = targetEvidence.Meta.Digest;
                    binding["expectedSceneMetaIdentity"] = targetEvidence.Meta.Identity;
                }
                if (parameters["preview"]?.Value<bool?>() ?? false)
                    return VRCForgeToolResult.Completed("Scene transition preview completed.", TransitionPayload(action, project, path, destination, digest, binding, setup, open, false, new { }));
                Stage1SceneToolCore.RequireExpected(parameters, "ProjectPath", project);
                Stage1SceneToolCore.RequireExpected(parameters, "PreviewDigest", digest, 64);
                Stage1SceneToolCore.RequireExpected(parameters, "SceneSetupDigest", setup, 64);
                Stage1SceneToolCore.RequireExpected(parameters, "OpenSceneStateDigest", open, 64);
                if (targetEvidence != null)
                {
                    Stage1SceneToolCore.RequireExpected(parameters, "SceneGuid", targetEvidence.Guid, 32);
                    Stage1SceneToolCore.RequireExpected(parameters, "SceneFileDigest", targetEvidence.File.Digest, 64);
                    Stage1SceneToolCore.RequireExpected(parameters, "SceneFileIdentity", targetEvidence.File.Identity, 64);
                    Stage1SceneToolCore.RequireExpected(parameters, "SceneMetaDigest", targetEvidence.Meta.Digest, 64);
                    Stage1SceneToolCore.RequireExpected(parameters, "SceneMetaIdentity", targetEvidence.Meta.Identity, 64);
                }
                if (action == "new_saved")
                {
                    Stage1SceneToolCore.RequireExpectedBool(parameters, "DestinationAbsent", true);
                    mutationStarted = true;
                    var created = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                    if (!EditorSceneManager.SaveScene(created, destination, false)) throw new SceneObjectCopyException("Unity did not confirm new_saved.");
                }
                else
                {
                    var target = SceneManager.GetSceneByPath(path);
                    if (action == "open_single")
                    {
                        mutationStarted = true;
                        target = EditorSceneManager.OpenScene(path, OpenSceneMode.Single);
                    }
                    else if (action == "open_additive")
                    {
                        if (target.IsValid() && target.isLoaded) throw new SceneObjectCopyException("The requested additive scene is already loaded.");
                        mutationStarted = true;
                        target = EditorSceneManager.OpenScene(path, OpenSceneMode.Additive);
                    }
                    else if (action == "set_active")
                    {
                        if (!target.IsValid() || !target.isLoaded) throw new SceneObjectCopyException("The requested scene is not loaded.");
                        if (SceneManager.GetActiveScene().handle == target.handle)
                            return VRCForgeToolResult.Completed("The requested scene is already active.", TransitionPayload(action, project, path, destination, digest, binding, setup, open, false, Stage1SceneToolCore.Readback(target), true));
                        mutationStarted = true;
                        if (!SceneManager.SetActiveScene(target)) throw new SceneObjectCopyException("Could not set the requested scene active.");
                    }
                    else if (action == "unload")
                    {
                        if (!target.IsValid() || !target.isLoaded || target.isDirty || SceneManager.sceneCount <= 1)
                            throw new SceneObjectCopyException("The requested scene cannot be safely unloaded as the last or a dirty scene.");
                        mutationStarted = true;
                        if (!EditorSceneManager.CloseScene(target, true)) throw new SceneObjectCopyException("Could not safely unload the requested scene.");
                    }
                    else if (action == "reload_saved")
                    {
                        if (!target.IsValid() || !target.isLoaded || target.isDirty)
                            throw new SceneObjectCopyException("The requested scene is not loaded and clean; reload was refused.");
                        var wasActive = SceneManager.GetActiveScene().handle == target.handle;
                        var mode = SceneManager.sceneCount == 1 ? OpenSceneMode.Single : OpenSceneMode.Additive;
                        mutationStarted = true;
                        if (mode == OpenSceneMode.Additive && !EditorSceneManager.CloseScene(target, true))
                            throw new SceneObjectCopyException("Could not close the exact clean scene for reload.");
                        target = EditorSceneManager.OpenScene(path, mode);
                        if (wasActive && !SceneManager.SetActiveScene(target))
                            throw new SceneObjectCopyException("Reloaded the scene but could not restore its active identity.");
                    }
                }
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                var readbackPath = action == "new_saved" ? destination : path;
                var readbackScene = SceneManager.GetSceneByPath(readbackPath);
                var readbackEvidence = action == "unload" ? null : SceneObjectCopyCore.ReadStableAssetEvidence(readbackPath, "transition readback scene");
                if (action == "unload" && readbackScene.IsValid() && readbackScene.isLoaded)
                    throw new SceneObjectCopyException("The unloaded scene is still loaded in fresh readback.");
                if (action != "unload" && (!readbackScene.IsValid() || !readbackScene.isLoaded || readbackEvidence == null))
                    throw new SceneObjectCopyException("The transitioned scene is missing from fresh readback.");
                return VRCForgeToolResult.Completed("Scene transition committed and verified.", TransitionPayload(
                    action, project, path, destination, digest, binding,
                    Stage1SceneToolCore.SetupDigest(), Stage1SceneToolCore.OpenStateDigest(), true,
                    new
                    {
                        scene = action == "unload" ? null : Stage1SceneToolCore.Readback(readbackScene),
                        path = readbackPath,
                        guid = readbackEvidence == null ? "" : readbackEvidence.Guid,
                        fileDigest = readbackEvidence == null ? "" : readbackEvidence.File.Digest,
                        active = SceneManager.GetActiveScene().path,
                        openSceneCount = SceneManager.sceneCount,
                        unloaded = action == "unload"
                    }));
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.FailedWithCode(
                    mutationStarted ? "scene_transition_failed_after_mutation" : "scene_transition_rejected",
                    ex.Message,
                    mutationStarted
                        ? Stage1SceneToolCore.UnknownAfterMutation(Stage1SceneToolCore.TransitionSchema, "scene_transition_" + action, project, ex.Message)
                        : Stage1SceneToolCore.NoMutation(Stage1SceneToolCore.TransitionSchema, action, project, string.Empty, ex.Message));
            }
        }

        private static object TransitionPayload(string action, string project, string path, string destination, string digest, Dictionary<string, object> binding, string setup, string open, bool changed, object readback, bool noChange = false)
        {
            return new { schema = Stage1SceneToolCore.TransitionSchema, operation = "scene_transition_" + action, ok = true, preview = !changed && !noChange, verified = true, changed, mutationStarted = changed, commitState = changed ? "committed" : noChange ? "no_change" : "not_started", projectPath = project, previewDigest = digest, applyBinding = Stage1SceneToolCore.Binding(binding), readback, scenePath = path, destinationScenePath = destination, sceneSetupDigest = setup, openSceneStateDigest = open };
        }
    }
}
