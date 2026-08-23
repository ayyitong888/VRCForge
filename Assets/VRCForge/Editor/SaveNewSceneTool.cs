using System;
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
    [VRCForgeCommand(
        toolId: "vrc_save_new_scene",
        Summary = "Preview or CreateNew-save the one active unsaved scene without replacing its contents or overwriting an asset."
    )]
    public static class SaveNewSceneTool
    {
        private const string ResultSchema = "vrcforge.scene_asset_save.v1";
        private const string Operation = "save_new_scene";

        public class Parameters
        {
            [VRCForgeInput("Exact new scene asset path below Assets.", IsRequired = true)] public string scenePath { get; set; } = "";
            [VRCForgeInput("Return a non-mutating CreateNew preview.", IsRequired = false)] public bool? preview { get; set; } = false;
            [VRCForgeInput("Expected active Unity project root from preview.", IsRequired = false)] public string expectedProjectPath { get; set; } = "";
            [VRCForgeInput("Expected unsaved scene handle.", IsRequired = false)] public int? expectedSceneHandle { get; set; }
            [VRCForgeInput("Expected unsaved scene name.", IsRequired = false)] public string expectedSceneName { get; set; } = "";
            [VRCForgeInput("Expected unsaved-scene dirty flag.", IsRequired = false)] public bool? expectedSceneWasDirty { get; set; }
            [VRCForgeInput("Expected root object count.", IsRequired = false)] public int? expectedRootObjectCount { get; set; }
            [VRCForgeInput("Expected complete scene hierarchy digest.", IsRequired = false)] public string expectedSceneHierarchyDigest { get; set; } = "";
            [VRCForgeInput("Expected authoritative preview digest.", IsRequired = false)] public string expectedPreviewDigest { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            var mutationStarted = false;
            try
            {
                CheckpointPrepareTool.EnsureEditorReady();
                var parameters = @params ?? new JObject();
                var snapshot = BuildSnapshot(parameters["scenePath"]?.ToString() ?? string.Empty);
                var preview = parameters["preview"]?.Value<bool?>() ?? false;
                if (preview)
                {
                    return VRCForgeToolResult.Completed(
                        "Validated a new scene asset save without changing the project.",
                        snapshot.ToPayload(true));
                }

                VerifyExpected(parameters, snapshot);
                if (!EditorSceneManager.SaveScene(snapshot.Scene, snapshot.ScenePath, false))
                {
                    mutationStarted = SceneObjectCopyCore.AssetOrMetaExists(snapshot.ScenePath)
                        || !string.IsNullOrWhiteSpace(snapshot.Scene.path);
                    return MutationFailure(
                        "scene_save_failed",
                        "Unity did not confirm the new scene asset save.",
                        mutationStarted);
                }
                mutationStarted = true;
                AssetDatabase.SaveAssets();
                AssetDatabase.ImportAsset(
                    snapshot.ScenePath,
                    ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(
                    snapshot.ScenePath,
                    "new scene asset");
                var loaded = SceneManager.GetSceneByPath(snapshot.ScenePath);
                if (!loaded.IsValid()
                    || !loaded.isLoaded
                    || loaded.handle != snapshot.SceneHandle
                    || loaded.isDirty
                    || ComputeSceneHierarchyDigest(loaded) != snapshot.SceneHierarchyDigest)
                {
                    return MutationFailure(
                        "scene_readback_failed",
                        "The new scene asset was saved, but its loaded readback did not match the approved scene.",
                        true);
                }

                return VRCForgeToolResult.Completed(
                    "Saved the active unsaved scene as a new scene asset.",
                    new
                    {
                        schema = ResultSchema,
                        operation = Operation,
                        ok = true,
                        preview = false,
                        verified = true,
                        changed = true,
                        saved = true,
                        mutationCount = 1,
                        mutationStarted = true,
                        commitState = "committed",
                        checkpointRestoreRequired = false,
                        projectPath = ProjectRoot(),
                        scenePath = snapshot.ScenePath,
                        sceneGuid = evidence.Guid,
                        sceneHandle = loaded.handle,
                        sceneName = loaded.name ?? string.Empty,
                        sceneWasDirty = snapshot.SceneWasDirty,
                        rootObjectCount = loaded.rootCount,
                        sceneHierarchyDigest = snapshot.SceneHierarchyDigest,
                        sceneFileDigest = evidence.File.Digest,
                        sceneFileIdentity = evidence.File.Identity,
                        sceneMetaDigest = evidence.Meta.Digest,
                        sceneMetaIdentity = evidence.Meta.Identity,
                        previewDigest = snapshot.PreviewDigest
                    });
            }
            catch (SceneObjectCopyException exception)
            {
                return VRCForgeToolResult.Failed(
                    "scene_save_rejected",
                    new
                    {
                        schema = ResultSchema,
                        operation = Operation,
                        message = exception.Message,
                        mutationStarted,
                        commitState = mutationStarted ? "unknown" : "not_started",
                        checkpointRestoreRequired = mutationStarted
                    });
            }
            catch (Exception)
            {
                return MutationFailure(
                    "scene_save_failed",
                    mutationStarted
                        ? "The new scene save failed after mutation; use the returned checkpoint before retrying."
                        : "The new scene save failed before changing the project.",
                    mutationStarted);
            }
        }

        private static SceneSaveSnapshot BuildSnapshot(string rawScenePath)
        {
            var scenePath = SceneObjectCopyCore.NormalizeSceneAssetPath(rawScenePath, "scenePath");
            var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(scenePath);
            var parent = Path.GetDirectoryName(absolute);
            if (string.IsNullOrWhiteSpace(parent) || !Directory.Exists(parent))
            {
                throw new SceneObjectCopyException("The scene destination folder must already exist.");
            }
            if (SceneObjectCopyCore.AssetOrMetaExists(scenePath))
            {
                throw new SceneObjectCopyException("The scene destination or its metadata already exists; overwrite is unsupported.");
            }

            var loaded = CheckpointPrepareTool.LoadedScenes()
                .Where(scene => !CheckpointPrepareTool.IsKnownTransientPreviewScene(scene))
                .ToList();
            var active = SceneManager.GetActiveScene();
            if (loaded.Count != 1 || !active.IsValid() || !active.isLoaded || active.handle != loaded[0].handle)
            {
                throw new SceneObjectCopyException("Exactly one active project scene is required for the initial scene save.");
            }
            if (!string.IsNullOrWhiteSpace(active.path))
            {
                throw new SceneObjectCopyException("The active scene is already saved; this create-new tool only accepts an unsaved scene.");
            }

            var snapshot = new SceneSaveSnapshot
            {
                Scene = active,
                ScenePath = scenePath,
                SceneHandle = active.handle,
                SceneName = active.name ?? string.Empty,
                SceneWasDirty = active.isDirty,
                RootObjectCount = active.rootCount,
                SceneHierarchyDigest = ComputeSceneHierarchyDigest(active),
                ProjectPath = ProjectRoot()
            };
            snapshot.PreviewDigest = ComputePreviewDigest(snapshot);
            return snapshot;
        }

        private static void VerifyExpected(JObject parameters, SceneSaveSnapshot snapshot)
        {
            if (!SceneObjectCopyCore.MatchesCurrentProject(parameters["expectedProjectPath"]?.ToString() ?? string.Empty)
                || parameters["expectedSceneHandle"]?.Type != JTokenType.Integer
                || parameters["expectedSceneHandle"].Value<int>() != snapshot.SceneHandle
                || parameters["expectedSceneName"]?.Type != JTokenType.String
                || parameters["expectedSceneName"].ToString() != snapshot.SceneName
                || parameters["expectedSceneWasDirty"]?.Type != JTokenType.Boolean
                || parameters["expectedSceneWasDirty"].Value<bool>() != snapshot.SceneWasDirty
                || parameters["expectedRootObjectCount"]?.Type != JTokenType.Integer
                || parameters["expectedRootObjectCount"].Value<int>() != snapshot.RootObjectCount
                || !IsLowerHex(parameters["expectedSceneHierarchyDigest"]?.ToString(), 64)
                || parameters["expectedSceneHierarchyDigest"].ToString() != snapshot.SceneHierarchyDigest
                || !IsLowerHex(parameters["expectedPreviewDigest"]?.ToString(), 64)
                || parameters["expectedPreviewDigest"].ToString() != snapshot.PreviewDigest)
            {
                throw new SceneObjectCopyException("The unsaved scene or destination changed after preview.");
            }
        }

        private static object MutationFailure(string code, string message, bool mutationStarted)
        {
            return VRCForgeToolResult.Failed(
                code,
                new
                {
                    schema = ResultSchema,
                    operation = Operation,
                    message,
                    mutationStarted,
                    commitState = mutationStarted ? "unknown" : "not_started",
                    checkpointRestoreRequired = mutationStarted
                });
        }

        private static string ComputeSceneHierarchyDigest(Scene scene)
        {
            var value = new StringBuilder();
            AppendDigestField(value, "vrcforge.unsaved_scene_hierarchy.v1");
            var roots = scene.GetRootGameObjects();
            AppendDigestField(value, roots.Length.ToString(CultureInfo.InvariantCulture));
            for (var index = 0; index < roots.Length; index++)
            {
                AppendDigestField(value, index.ToString(CultureInfo.InvariantCulture));
                AppendDigestField(value, SceneObjectCopyCore.ComputeHierarchyDigest(roots[index]));
            }
            return Sha256(value.ToString());
        }

        private static string ComputePreviewDigest(SceneSaveSnapshot snapshot)
        {
            var value = new StringBuilder();
            AppendDigestField(value, ResultSchema);
            AppendDigestField(value, Operation);
            AppendDigestField(value, "true");
            AppendDigestField(value, "true");
            AppendDigestField(value, "true");
            AppendDigestField(value, "false");
            AppendDigestField(value, "false");
            AppendDigestField(value, "0");
            AppendDigestField(value, snapshot.ProjectPath);
            AppendDigestField(value, snapshot.ScenePath);
            AppendDigestField(value, snapshot.SceneHandle.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(value, snapshot.SceneName);
            AppendDigestField(value, snapshot.SceneWasDirty ? "true" : "false");
            AppendDigestField(value, snapshot.RootObjectCount.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(value, snapshot.SceneHierarchyDigest);
            AppendDigestField(value, "false");
            AppendDigestField(value, "false");
            return Sha256(value.ToString());
        }

        private static void AppendDigestField(StringBuilder target, string value)
        {
            var bytes = Encoding.UTF8.GetBytes(value ?? string.Empty);
            target.Append(bytes.Length.ToString(CultureInfo.InvariantCulture));
            target.Append(':');
            target.Append(value ?? string.Empty);
        }

        private static string Sha256(string value)
        {
            using (var sha = SHA256.Create())
            {
                return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(value ?? string.Empty))
                    .Select(item => item.ToString("x2", CultureInfo.InvariantCulture)));
            }
        }

        private static bool IsLowerHex(string value, int length)
        {
            return !string.IsNullOrEmpty(value)
                && value.Length == length
                && value.All(character => (character >= '0' && character <= '9')
                    || (character >= 'a' && character <= 'f'));
        }

        private static string ProjectRoot()
        {
            return Path.GetFullPath(Path.Combine(Application.dataPath, ".."))
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                .Replace('\\', '/');
        }

        private sealed class SceneSaveSnapshot
        {
            internal Scene Scene;
            internal string ProjectPath = string.Empty;
            internal string ScenePath = string.Empty;
            internal int SceneHandle;
            internal string SceneName = string.Empty;
            internal bool SceneWasDirty;
            internal int RootObjectCount;
            internal string SceneHierarchyDigest = string.Empty;
            internal string PreviewDigest = string.Empty;

            internal object ToPayload(bool preview)
            {
                return new
                {
                    schema = ResultSchema,
                    operation = Operation,
                    ok = true,
                    preview,
                    verified = true,
                    changed = false,
                    saved = false,
                    mutationCount = 0,
                    mutationStarted = false,
                    commitState = "not_started",
                    checkpointRestoreRequired = false,
                    projectPath = ProjectPath,
                    scenePath = ScenePath,
                    sceneHandle = SceneHandle,
                    sceneName = SceneName,
                    sceneWasDirty = SceneWasDirty,
                    rootObjectCount = RootObjectCount,
                    sceneHierarchyDigest = SceneHierarchyDigest,
                    targetExists = false,
                    targetMetaExists = false,
                    previewDigest = PreviewDigest
                };
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_save_current_scene",
        Summary = "when-to-use: preview or save the one active dirty saved scene in place after explicit user approval. when-NOT-to-use: do not use for clean, unsaved, multiple, or non-active scenes. Negative example: do not call merely because another tool reports a dirty scene."
    )]
    public static class SaveCurrentSceneTool
    {
        private const string ResultSchema = "vrcforge.current_scene_save.v1";
        private const string Operation = "save_current_scene";

        public class Parameters
        {
            [VRCForgeInput("Exact existing active scene path below Assets.", IsRequired = true)] public string scenePath { get; set; } = "";
            [VRCForgeInput("Return a non-mutating current-scene save preview.", IsRequired = false)] public bool? preview { get; set; } = false;
            [VRCForgeInput("Expected active Unity project root from preview.", IsRequired = false)] public string expectedProjectPath { get; set; } = "";
            [VRCForgeInput("Expected active scene handle.", IsRequired = false)] public int? expectedSceneHandle { get; set; }
            [VRCForgeInput("Expected active scene name.", IsRequired = false)] public string expectedSceneName { get; set; } = "";
            [VRCForgeInput("Expected dirty flag before saving.", IsRequired = false)] public bool? expectedSceneWasDirty { get; set; }
            [VRCForgeInput("Expected open project scene count.", IsRequired = false)] public int? expectedOpenSceneCount { get; set; }
            [VRCForgeInput("Expected root object count.", IsRequired = false)] public int? expectedRootObjectCount { get; set; }
            [VRCForgeInput("Expected complete in-memory hierarchy digest.", IsRequired = false)] public string expectedSceneHierarchyDigest { get; set; } = "";
            [VRCForgeInput("Expected existing scene GUID.", IsRequired = false)] public string expectedSceneGuid { get; set; } = "";
            [VRCForgeInput("Expected on-disk scene digest before saving.", IsRequired = false)] public string expectedSceneFileDigestBefore { get; set; } = "";
            [VRCForgeInput("Expected on-disk scene identity before saving.", IsRequired = false)] public string expectedSceneFileIdentityBefore { get; set; } = "";
            [VRCForgeInput("Expected scene metadata digest before saving.", IsRequired = false)] public string expectedSceneMetaDigestBefore { get; set; } = "";
            [VRCForgeInput("Expected scene metadata identity before saving.", IsRequired = false)] public string expectedSceneMetaIdentityBefore { get; set; } = "";
            [VRCForgeInput("Expected authoritative preview digest.", IsRequired = false)] public string expectedPreviewDigest { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            var mutationStarted = false;
            try
            {
                CheckpointPrepareTool.EnsureEditorReady();
                var parameters = @params ?? new JObject();
                var snapshot = BuildSnapshot(parameters["scenePath"]?.ToString() ?? string.Empty);
                if (parameters["preview"]?.Value<bool?>() ?? false)
                {
                    return VRCForgeToolResult.Completed(
                        "Validated the current dirty scene save without changing the project.",
                        snapshot.ToPreviewPayload());
                }

                VerifyExpected(parameters, snapshot);
                mutationStarted = true;
                if (!EditorSceneManager.SaveScene(snapshot.Scene, snapshot.ScenePath, false))
                {
                    return MutationFailure(
                        "current_scene_save_failed",
                        "Unity did not confirm the current scene save.",
                        true);
                }
                AssetDatabase.SaveAssets();
                AssetDatabase.ImportAsset(
                    snapshot.ScenePath,
                    ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);

                var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(
                    snapshot.ScenePath,
                    "saved current scene");
                var loaded = SceneManager.GetSceneByPath(snapshot.ScenePath);
                if (!loaded.IsValid()
                    || !loaded.isLoaded
                    || loaded.handle != snapshot.SceneHandle
                    || loaded.isDirty
                    || loaded.rootCount != snapshot.RootObjectCount
                    || ComputeSceneHierarchyDigest(loaded) != snapshot.SceneHierarchyDigest
                    || evidence.Guid != snapshot.SceneGuid
                    || evidence.Meta.Digest != snapshot.SceneMetaDigestBefore
                    || evidence.Meta.Identity != snapshot.SceneMetaIdentityBefore)
                {
                    return MutationFailure(
                        "current_scene_readback_failed",
                        "The current scene save completed, but persisted readback did not match the approved in-memory scene.",
                        true);
                }

                return VRCForgeToolResult.Completed(
                    "Saved the active dirty scene in place.",
                    new
                    {
                        schema = ResultSchema,
                        operation = Operation,
                        ok = true,
                        preview = false,
                        verified = true,
                        changed = true,
                        saved = true,
                        mutationCount = 1,
                        mutationStarted = true,
                        commitState = "committed",
                        checkpointRestoreRequired = false,
                        manualRecoveryRequired = false,
                        projectPath = ProjectRoot(),
                        scenePath = snapshot.ScenePath,
                        sceneGuid = evidence.Guid,
                        sceneHandle = loaded.handle,
                        sceneName = loaded.name ?? string.Empty,
                        sceneWasDirty = snapshot.SceneWasDirty,
                        sceneIsDirty = loaded.isDirty,
                        openSceneCount = snapshot.OpenSceneCount,
                        rootObjectCount = loaded.rootCount,
                        sceneHierarchyDigest = snapshot.SceneHierarchyDigest,
                        sceneFileDigestAfter = evidence.File.Digest,
                        sceneFileIdentityAfter = evidence.File.Identity,
                        sceneMetaDigestAfter = evidence.Meta.Digest,
                        sceneMetaIdentityAfter = evidence.Meta.Identity,
                        previewDigest = snapshot.PreviewDigest
                    });
            }
            catch (SceneObjectCopyException exception)
            {
                return VRCForgeToolResult.Failed(
                    "current_scene_save_rejected",
                    new
                    {
                        schema = ResultSchema,
                        operation = Operation,
                        message = exception.Message,
                        mutationStarted,
                        commitState = mutationStarted ? "unknown" : "not_started",
                        checkpointRestoreRequired = false,
                        manualRecoveryRequired = mutationStarted
                    });
            }
            catch (Exception)
            {
                return MutationFailure(
                    "current_scene_save_failed",
                    mutationStarted
                        ? "The current scene save failed after persistence began; inspect the scene and Console before retrying."
                        : "The current scene save failed before changing the project.",
                    mutationStarted);
            }
        }

        private static CurrentSceneSaveSnapshot BuildSnapshot(string rawScenePath)
        {
            var scenePath = SceneObjectCopyCore.NormalizeSceneAssetPath(rawScenePath, "scenePath");
            var loaded = CheckpointPrepareTool.LoadedScenes()
                .Where(scene => !CheckpointPrepareTool.IsKnownTransientPreviewScene(scene))
                .ToList();
            var active = SceneManager.GetActiveScene();
            if (loaded.Count != 1 || !active.IsValid() || !active.isLoaded || active.handle != loaded[0].handle)
            {
                throw new SceneObjectCopyException("Exactly one active project scene is required for the current scene save.");
            }
            if (string.IsNullOrWhiteSpace(active.path)
                || !string.Equals(active.path.Replace('\\', '/'), scenePath, StringComparison.Ordinal))
            {
                throw new SceneObjectCopyException("The requested scene is not the active saved scene.");
            }
            if (!active.isDirty)
            {
                throw new SceneObjectCopyException("The active scene has no unsaved changes to persist.");
            }

            var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(scenePath, "current scene asset");
            var snapshot = new CurrentSceneSaveSnapshot
            {
                Scene = active,
                ProjectPath = ProjectRoot(),
                ScenePath = scenePath,
                SceneGuid = evidence.Guid,
                SceneHandle = active.handle,
                SceneName = active.name ?? string.Empty,
                SceneWasDirty = active.isDirty,
                OpenSceneCount = loaded.Count,
                RootObjectCount = active.rootCount,
                SceneHierarchyDigest = ComputeSceneHierarchyDigest(active),
                SceneFileDigestBefore = evidence.File.Digest,
                SceneFileIdentityBefore = evidence.File.Identity,
                SceneMetaDigestBefore = evidence.Meta.Digest,
                SceneMetaIdentityBefore = evidence.Meta.Identity
            };
            snapshot.PreviewDigest = ComputePreviewDigest(snapshot);
            return snapshot;
        }

        private static void VerifyExpected(JObject parameters, CurrentSceneSaveSnapshot snapshot)
        {
            if (!SceneObjectCopyCore.MatchesCurrentProject(parameters["expectedProjectPath"]?.ToString() ?? string.Empty)
                || parameters["expectedSceneHandle"]?.Type != JTokenType.Integer
                || parameters["expectedSceneHandle"].Value<int>() != snapshot.SceneHandle
                || parameters["expectedSceneName"]?.Type != JTokenType.String
                || parameters["expectedSceneName"].ToString() != snapshot.SceneName
                || parameters["expectedSceneWasDirty"]?.Type != JTokenType.Boolean
                || !parameters["expectedSceneWasDirty"].Value<bool>()
                || parameters["expectedOpenSceneCount"]?.Type != JTokenType.Integer
                || parameters["expectedOpenSceneCount"].Value<int>() != snapshot.OpenSceneCount
                || parameters["expectedRootObjectCount"]?.Type != JTokenType.Integer
                || parameters["expectedRootObjectCount"].Value<int>() != snapshot.RootObjectCount
                || !ExpectedHex(parameters, "expectedSceneHierarchyDigest", snapshot.SceneHierarchyDigest, 64)
                || !ExpectedHex(parameters, "expectedSceneGuid", snapshot.SceneGuid, 32)
                || !ExpectedHex(parameters, "expectedSceneFileDigestBefore", snapshot.SceneFileDigestBefore, 64)
                || !ExpectedHex(parameters, "expectedSceneFileIdentityBefore", snapshot.SceneFileIdentityBefore, 64)
                || !ExpectedHex(parameters, "expectedSceneMetaDigestBefore", snapshot.SceneMetaDigestBefore, 64)
                || !ExpectedHex(parameters, "expectedSceneMetaIdentityBefore", snapshot.SceneMetaIdentityBefore, 64)
                || !ExpectedHex(parameters, "expectedPreviewDigest", snapshot.PreviewDigest, 64))
            {
                throw new SceneObjectCopyException("The current dirty scene changed after preview.");
            }
        }

        private static bool ExpectedHex(JObject parameters, string name, string expected, int length)
        {
            var value = parameters[name]?.ToString() ?? string.Empty;
            return IsLowerHex(value, length) && value == expected;
        }

        private static object MutationFailure(string code, string message, bool mutationStarted)
        {
            return VRCForgeToolResult.Failed(
                code,
                new
                {
                    schema = ResultSchema,
                    operation = Operation,
                    message,
                    mutationStarted,
                    commitState = mutationStarted ? "unknown" : "not_started",
                    checkpointRestoreRequired = false,
                    manualRecoveryRequired = mutationStarted
                });
        }

        private static string ComputeSceneHierarchyDigest(Scene scene)
        {
            var value = new StringBuilder();
            AppendDigestField(value, "vrcforge.current_scene_hierarchy.v1");
            var roots = scene.GetRootGameObjects();
            AppendDigestField(value, roots.Length.ToString(CultureInfo.InvariantCulture));
            for (var index = 0; index < roots.Length; index++)
            {
                AppendDigestField(value, index.ToString(CultureInfo.InvariantCulture));
                AppendDigestField(value, SceneObjectCopyCore.ComputeHierarchyDigest(roots[index]));
            }
            return Sha256(value.ToString());
        }

        private static string ComputePreviewDigest(CurrentSceneSaveSnapshot snapshot)
        {
            var value = new StringBuilder();
            foreach (var field in new[]
            {
                ResultSchema, Operation, "true", "true", "true", "false", "false", "0",
                snapshot.ProjectPath, snapshot.ScenePath, snapshot.SceneGuid,
                snapshot.SceneHandle.ToString(CultureInfo.InvariantCulture), snapshot.SceneName,
                snapshot.SceneWasDirty ? "true" : "false",
                snapshot.OpenSceneCount.ToString(CultureInfo.InvariantCulture),
                snapshot.RootObjectCount.ToString(CultureInfo.InvariantCulture),
                snapshot.SceneHierarchyDigest, snapshot.SceneFileDigestBefore,
                snapshot.SceneFileIdentityBefore, snapshot.SceneMetaDigestBefore,
                snapshot.SceneMetaIdentityBefore
            })
            {
                AppendDigestField(value, field);
            }
            return Sha256(value.ToString());
        }

        private static void AppendDigestField(StringBuilder target, string value)
        {
            var bytes = Encoding.UTF8.GetBytes(value ?? string.Empty);
            target.Append(bytes.Length.ToString(CultureInfo.InvariantCulture));
            target.Append(':');
            target.Append(value ?? string.Empty);
        }

        private static string Sha256(string value)
        {
            using (var sha = SHA256.Create())
            {
                return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(value ?? string.Empty))
                    .Select(item => item.ToString("x2", CultureInfo.InvariantCulture)));
            }
        }

        private static bool IsLowerHex(string value, int length)
        {
            return !string.IsNullOrEmpty(value)
                && value.Length == length
                && value.All(character => (character >= '0' && character <= '9')
                    || (character >= 'a' && character <= 'f'));
        }

        private static string ProjectRoot()
        {
            return Path.GetFullPath(Path.Combine(Application.dataPath, ".."))
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                .Replace('\\', '/');
        }

        private sealed class CurrentSceneSaveSnapshot
        {
            internal Scene Scene;
            internal string ProjectPath = string.Empty;
            internal string ScenePath = string.Empty;
            internal string SceneGuid = string.Empty;
            internal int SceneHandle;
            internal string SceneName = string.Empty;
            internal bool SceneWasDirty;
            internal int OpenSceneCount;
            internal int RootObjectCount;
            internal string SceneHierarchyDigest = string.Empty;
            internal string SceneFileDigestBefore = string.Empty;
            internal string SceneFileIdentityBefore = string.Empty;
            internal string SceneMetaDigestBefore = string.Empty;
            internal string SceneMetaIdentityBefore = string.Empty;
            internal string PreviewDigest = string.Empty;

            internal object ToPreviewPayload()
            {
                return new
                {
                    schema = ResultSchema,
                    operation = Operation,
                    ok = true,
                    preview = true,
                    verified = true,
                    changed = false,
                    saved = false,
                    mutationCount = 0,
                    mutationStarted = false,
                    commitState = "not_started",
                    checkpointRestoreRequired = false,
                    manualRecoveryRequired = false,
                    projectPath = ProjectPath,
                    scenePath = ScenePath,
                    sceneGuid = SceneGuid,
                    sceneHandle = SceneHandle,
                    sceneName = SceneName,
                    sceneWasDirty = SceneWasDirty,
                    openSceneCount = OpenSceneCount,
                    rootObjectCount = RootObjectCount,
                    sceneHierarchyDigest = SceneHierarchyDigest,
                    sceneFileDigestBefore = SceneFileDigestBefore,
                    sceneFileIdentityBefore = SceneFileIdentityBefore,
                    sceneMetaDigestBefore = SceneMetaDigestBefore,
                    sceneMetaIdentityBefore = SceneMetaIdentityBefore,
                    previewDigest = PreviewDigest
                };
            }
        }
    }
}
