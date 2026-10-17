using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
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
        Summary = "Inspect saved project state before VRCForge creates a disk rollback checkpoint. Never saves unsaved editor state. Internal safety tool."
    )]
    public static class CheckpointPrepareTool
    {
        public const bool SavesDirtyAssets = false;
        internal static HashSet<string> ExactFileScope(JToken token, bool allowEmpty = false)
        {
            if (token == null) return null;
            if (!(token is JArray rows) || (!allowEmpty && rows.Count == 0)) throw new InvalidOperationException("Exact checkpoint file scope is empty.");
            var paths = new HashSet<string>(StringComparer.Ordinal);
            foreach (var row in rows)
            {
                var path = row.Type == JTokenType.String ? row.Value<string>() : "";
                if (string.IsNullOrEmpty(path) || !path.StartsWith("Assets/", StringComparison.Ordinal) || path.Contains("\\") || path.Split('/').Any(part => part == ".." || part == "." || part.Length == 0))
                    throw new InvalidOperationException("Invalid exact checkpoint file scope.");
                paths.Add(path.EndsWith(".meta", StringComparison.Ordinal) ? path.Substring(0, path.Length - 5) : path);
            }
            return paths;
        }
        internal static object[] DirtyAssetEvidence()
        {
            return Resources.FindObjectsOfTypeAll<UnityEngine.Object>()
                .Where(asset => asset != null && EditorUtility.IsPersistent(asset) && EditorUtility.IsDirty(asset)
                    && AssetDatabase.GetAssetPath(asset).StartsWith("Assets/", StringComparison.Ordinal))
                .OrderBy(asset => AssetDatabase.GetAssetPath(asset), StringComparer.Ordinal).ThenBy(asset => asset.GetInstanceID())
                .Select(asset => (object)new { assetPath = AssetDatabase.GetAssetPath(asset), instanceId = asset.GetInstanceID(),
                    type = asset.GetType().FullName, dirty = EditorUtility.IsDirty(asset), dirtyCount = EditorUtility.GetDirtyCount(asset),
                    serializedState = EditorJsonUtility.ToJson(asset) }).ToArray();
        }
        private const string NdmfPreviewSceneGuid = "8cbd3f19cef3477439841053ced0661b";

        public class Parameters
        {
            [VRCForgeInput("Optional exact active Unity project root.", IsRequired = false)] public string projectPath { get; set; } = "";
            [VRCForgeInput("Exact existing assets resolved by the approved write preview.", IsRequired = false)] public string[] checkpointAssetPaths { get; set; }
            [VRCForgeInput("Exact disk files in the production checkpoint; absent means full project.", IsRequired = false)] public string[] checkpointFiles { get; set; }
            [VRCForgeInput("Capture a complete read-only before/after witness for a failed restore; requires a non-empty exact checkpoint file scope.", IsRequired = false)] public bool recoveryEvidenceOnly { get; set; }
            [VRCForgeInput("Capture only exact clean material assets without saving scenes or assets.", IsRequired = false)] public bool materialBaselineOnly { get; set; }
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
                if (@params?["recoveryEvidenceOnly"] != null
                    && @params["recoveryEvidenceOnly"].Type != JTokenType.Boolean)
                {
                    throw new InvalidOperationException("recoveryEvidenceOnly must be a boolean.");
                }
                if (@params?["recoveryEvidenceOnly"]?.Value<bool>() == true)
                {
                    return HandleRecoveryEvidenceOnly(@params);
                }
                if (@params?["materialBaselineOnly"]?.Value<bool>() == true)
                {
                    var baseline = CaptureReadOnlyMaterialBaseline(@params?["checkpointAssetPaths"]);
                    return VRCForgeToolResult.Completed("Captured material baseline without saving.", new
                    {
                        ok = true, projectPath = ProjectRoot(), phase = "prepare",
                        assetBaseline = baseline, mutationStarted = false, saved = false,
                        snapshotIncludesUnsavedState = false
                    });
                }

                var allLoadedScenes = LoadedScenes();
                var ignoredTransientScenes = allLoadedScenes.Where(IsKnownTransientPreviewScene)
                    .Select(scene => scene.path).ToArray();
                var loadedScenes = allLoadedScenes.Where(scene => !IsKnownTransientPreviewScene(scene)).ToList();
                var unsavedScenes = loadedScenes.Where(scene => string.IsNullOrWhiteSpace(scene.path))
                    .Select(scene => string.IsNullOrWhiteSpace(scene.name) ? "Untitled" : scene.name).ToArray();
                var unsupportedScenes = loadedScenes.Where(scene => !string.IsNullOrWhiteSpace(scene.path)
                    && !scene.path.StartsWith("Assets/", StringComparison.Ordinal)).Select(scene => scene.path).ToArray();
                var dirtyScenePaths = loadedScenes.Where(scene => scene.isDirty).Select(scene => scene.path).ToArray();
                var loadedScenePaths = new HashSet<string>(loadedScenes.Select(scene => scene.path), StringComparer.Ordinal);
                // Include persistent main assets, subassets and importers; IsDirty is state, not provenance.
                var dirtyAssetPaths = Resources.FindObjectsOfTypeAll<UnityEngine.Object>()
                    .Where(asset => asset != null && EditorUtility.IsPersistent(asset) && EditorUtility.IsDirty(asset))
                    .Select(AssetDatabase.GetAssetPath)
                    .Where(path => !string.IsNullOrWhiteSpace(path) && path.StartsWith("Assets/", StringComparison.Ordinal)
                        && !loadedScenePaths.Contains(path))
                    .Distinct(StringComparer.Ordinal).OrderBy(path => path, StringComparer.Ordinal).ToArray();
                var fileScope = ExactFileScope(@params?["checkpointFiles"]);
                var blockingDirtyAssetPaths = dirtyAssetPaths.Where(path => fileScope == null || fileScope.Contains(path)).ToArray();
                var code = unsavedScenes.Length > 0 ? "unsaved_open_scene"
                    : unsupportedScenes.Length > 0 ? "scene_outside_project_assets"
                    : dirtyScenePaths.Length > 0 || blockingDirtyAssetPaths.Length > 0 ? "dirty_editor_state" : "";
                if (code.Length > 0)
                {
                    return VRCForgeToolResult.Failed(code, new
                    {
                        message = "A disk checkpoint cannot preserve unsaved editor state. Save intended changes explicitly before this asset write; open scenes must be under Assets.",
                        blocking = true, recoverable = false, unsavedScenes, unsupportedScenes,
                        dirtyScenePaths, dirtyAssetPaths, blockingDirtyAssetPaths, ignoredTransientScenes,
                        snapshotIncludesUnsavedState = false, saved = false,
                        mutationStarted = false, committed = false, commitState = "not_started"
                    });
                }
                var assetBaseline = CaptureAssetBaseline(@params?["checkpointAssetPaths"]);
                var scenes = loadedScenes.Select(scene => scene.path).ToList();
                var activeScene = SceneManager.GetActiveScene();
                var activeScenePath = loadedScenes.Any(scene => scene == activeScene) ? activeScene.path : string.Empty;
                return VRCForgeToolResult.Completed("Inspected saved state without saving scenes or assets.", new
                {
                    ok = true, projectPath = ProjectRoot(), scenes, activeScenePath,
                    dirtyScenePaths, dirtyAssetPaths, blockingDirtyAssetPaths, ignoredTransientScenes, assetBaseline,
                    saved = false, mutationStarted = false, snapshotIncludesUnsavedState = false,
                    unityProcessId = identity?.ProcessId,
                    unityProcessStartedAtUtc = identity?.StartedAtUtc,
                    unityExecutableDigest = identity?.ExecutableDigest,
                    projectPathDigest = identity?.ProjectPathDigest
                });
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Checkpoint preparation failed: {ex.Message}", new
                {
                    blocking = true, mutationStarted = false, committed = false,
                    commitState = "not_started", saved = false, snapshotIncludesUnsavedState = false
                });
            }
        }

        private static object DescribeAssetState(string assetPath)
        {
            var loaded = AssetDatabase.LoadAllAssetsAtPath(assetPath);
            return new
            {
                dirty = loaded.Any(asset => asset != null && EditorUtility.IsDirty(asset)),
                file = DescribeProjectFile(assetPath)
            };
        }

        private static object ReadAssetAfter(string assetPath)
        {
            return DescribeAssetState(assetPath);
        }

        private static object DescribeProjectFile(string projectRelativePath)
        {
            var fullPath = Path.Combine(ProjectRoot(), projectRelativePath);
            var file = new FileInfo(fullPath);
            return new
            {
                exists = file.Exists,
                length = file.Exists ? file.Length : 0L,
                lastWriteTimeUtc = file.Exists ? file.LastWriteTimeUtc.ToString("O") : ""
            };
        }

        private static object BuildTransaction(List<TransactionReceipt> receipts, string transactionHandle)
        {
            var transactionItems = receipts.Select(item => new
            {
                asset = item.Asset,
                before = item.Before,
                after = item.After ?? item.Before,
                status = item.Status,
                error = item.Error,
                rolled_back = item.RolledBack
            }).ToList();
            return new
            {
                assets_touched = transactionItems.Count,
                items = transactionItems.Take(20).ToArray(),
                handle = transactionHandle
            };
        }

        private sealed class TransactionReceipt
        {
            public string Asset = "";
            public object Before;
            public object After;
            public string Status = "not_attempted";
            public string Error = "";
            public bool RolledBack = false;
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

        internal static JArray CaptureReadOnlyMaterialBaseline(JToken paths)
        {
            if (!(paths is JArray array) || array.Count == 0 || array.Count > 128
                || array.Any(path => path.Type != JTokenType.String
                    || !path.Value<string>().EndsWith(".mat", StringComparison.OrdinalIgnoreCase)))
                throw new InvalidOperationException("Material checkpoint requires 1..128 exact material assets.");
            return CaptureAssetBaseline(paths);
        }

        internal static string MaterialStateDigest(UnityEngine.Object asset)
        {
            using (var hash = System.Security.Cryptography.SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(System.Text.Encoding.UTF8.GetBytes(
                    EditorJsonUtility.ToJson(asset)))).Replace("-", "").ToLowerInvariant();
        }

        internal static JArray CaptureAssetBaseline(JToken paths)
        {
            if (paths == null) return null;
            if (!(paths is JArray array) || array.Count > 128
                || (array.Count > 32 && array.Any(entry => entry.Type != JTokenType.String
                    || !entry.Value<string>().EndsWith(".mat", StringComparison.OrdinalIgnoreCase))))
                throw new InvalidOperationException("Invalid checkpoint asset footprint.");
            var baseline = new JArray();
            foreach (var entry in array)
            {
                var path = entry.Type == JTokenType.String ? entry.Value<string>() : "";
                ValidateBaselinePath(path);
                var asset = AssetDatabase.LoadMainAssetAtPath(path);
                if (asset is Material && path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase))
                {
                    if (EditorUtility.IsDirty(asset))
                        throw new InvalidOperationException("Checkpoint material baseline requires a saved, clean material.");
                    baseline.Add(new JObject { ["assetPath"] = path,
                        ["assetGuid"] = AssetDatabase.AssetPathToGUID(path),
                        ["materialStateDigest"] = MaterialStateDigest(asset) });
                    continue;
                }
                if (!(asset is ScriptableObject) || !path.EndsWith(".asset", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Checkpoint baseline requires a native Material or ScriptableObject asset.");
                var json = EditorJsonUtility.ToJson(asset);
                if (json.Length > 262144) throw new InvalidOperationException("Checkpoint asset baseline is too large.");
                baseline.Add(new JObject { ["assetPath"] = path, ["assetGuid"] = AssetDatabase.AssetPathToGUID(path),
                    ["serializedState"] = JObject.Parse(json) });
            }
            return baseline;
        }

        internal static void ValidateBaselinePath(string path)
        {
            if (string.IsNullOrEmpty(path) || !path.StartsWith("Assets/", StringComparison.Ordinal)
                || !(path.EndsWith(".asset", StringComparison.OrdinalIgnoreCase) || path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase))
                || path.Contains("\\") || path.Split('/').Any(part => part == ".." || part == "." || part.Length == 0)
                || !File.Exists(Path.Combine(ProjectRoot(), path)))
                throw new InvalidOperationException("Checkpoint asset footprint must name an existing exact Assets .asset or .mat path.");
        }

        internal static JArray RestoreAssetBaseline(JToken token)
        {
            if (!(token is JArray baseline) || baseline.Count > 128
                || (baseline.Count > 32 && baseline.Any(item =>
                    !(item["assetPath"]?.Value<string>() ?? "").EndsWith(".mat", StringComparison.OrdinalIgnoreCase))))
                throw new InvalidOperationException("Checkpoint asset baseline is unavailable.");
            // Validate the entire footprint before importing any asset.
            foreach (var item in baseline)
            {
                var path = item["assetPath"]?.Value<string>();
                ValidateBaselinePath(path);
                var material = path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase);
                var digest = item["materialStateDigest"]?.Value<string>() ?? "";
                if ((material ? digest.Length != 64 || digest.Any(c => !Uri.IsHexDigit(c)) : !(item["serializedState"] is JObject))
                    || string.IsNullOrEmpty(item["assetGuid"]?.Value<string>())
                    || AssetDatabase.AssetPathToGUID(path) != item["assetGuid"].Value<string>())
                    throw new InvalidOperationException("Checkpoint asset identity or baseline is invalid.");
            }
            var verified = new JArray();
            foreach (var item in baseline)
            {
                var path = item["assetPath"].Value<string>();
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
                var asset = AssetDatabase.LoadMainAssetAtPath(path);
                var matches = path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase)
                    ? asset is Material && MaterialStateDigest(asset) == item["materialStateDigest"]?.Value<string>()
                    : asset is ScriptableObject && JToken.DeepEquals(item["serializedState"], JObject.Parse(EditorJsonUtility.ToJson(asset)));
                if (!matches)
                    throw new InvalidOperationException($"Checkpoint live asset readback differs from baseline: {path}");
                var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(path, "checkpoint asset readback");
                verified.Add(new JObject { ["assetPath"] = path, ["assetGuid"] = evidence.Guid,
                    ["fileDigest"] = evidence.File.Digest, ["verified"] = true });
            }
            return verified;
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

        private static VRCForgeToolResult HandleRecoveryEvidenceOnly(JObject @params)
        {
            // The witness is deliberately global: unrelated dirty assets are part of
            // the proof surface and cannot be hidden behind checkpointFiles.
            var fileScope = ExactFileScope(@params?["checkpointFiles"]);
            if (fileScope == null || fileScope.Count == 0)
            {
                throw new InvalidOperationException(
                    "recoveryEvidenceOnly requires a non-empty exact checkpoint file scope.");
            }

            using (var evidence = new CheckpointEvidenceFiles(
                ProjectRoot(),
                () => new[] { CaptureRecoveryWitness() },
                "recoveryEvidence",
                true))
            {
                evidence.RecordBefore(new[] { CaptureRecoveryWitness() });
                var result = VRCForgeToolResult.Completed(
                    "Captured a read-only recovery witness without changing Unity editor state.",
                    new
                    {
                        ok = true,
                        phase = "prepare",
                        recoveryEvidenceOnly = true,
                        checkpointFileCount = fileScope.Count,
                        saved = false,
                        imported = false,
                        refreshed = false,
                        mutationStarted = false,
                        committed = false,
                        snapshotIncludesUnsavedState = true,
                    });
                return evidence.Complete(result);
            }
        }

        internal static object CaptureRecoveryWitness()
        {
            var errors = new List<object>();
            var coreInstanceId = VRCForgeMcpCoreServer.CurrentInstanceId ?? "";
            var processId = 0;
            var processStartedAtUtc = "";
            try
            {
                using (var process = System.Diagnostics.Process.GetCurrentProcess())
                {
                    processId = process.Id;
                    processStartedAtUtc = process.StartTime.ToUniversalTime().ToString("O");
                }
            }
            catch (Exception ex)
            {
                errors.Add(new { kind = "core_process_identity", error = ex.GetType().Name });
            }
            var projectPathDigest = Fingerprint(ProjectRoot());
            var dirtyAssets = new List<object>();
            foreach (var asset in Resources.FindObjectsOfTypeAll<UnityEngine.Object>()
                .Where(item => item != null && EditorUtility.IsPersistent(item) && EditorUtility.IsDirty(item))
                .OrderBy(item => SafeAssetPath(item), StringComparer.Ordinal)
                .ThenBy(item => item.GetInstanceID()))
            {
                var assetPath = SafeAssetPath(asset);
                if (string.IsNullOrWhiteSpace(assetPath) || !assetPath.StartsWith("Assets/", StringComparison.Ordinal)) continue;
                var type = asset.GetType().FullName ?? asset.GetType().Name;
                var dirtyCount = EditorUtility.GetDirtyCount(asset);
                try
                {
                    var serialized = EditorJsonUtility.ToJson(asset);
                    dirtyAssets.Add(new
                    {
                        assetPath,
                        instanceId = asset.GetInstanceID(),
                        type,
                        dirty = EditorUtility.IsDirty(asset),
                        dirtyCount,
                        serializedState = serialized,
                        serializedFingerprint = Fingerprint(assetPath + "\n" + asset.GetInstanceID() + "\n" + type + "\n" + dirtyCount + "\n" + serialized),
                        serializedComplete = true,
                    });
                }
                catch (Exception ex)
                {
                    errors.Add(new { kind = "dirty_asset_serialization", assetPath, instanceId = asset.GetInstanceID(), type, error = ex.GetType().Name });
                    dirtyAssets.Add(new { assetPath, instanceId = asset.GetInstanceID(), type, dirty = EditorUtility.IsDirty(asset), dirtyCount, serializedState = "", serializedFingerprint = "", serializedComplete = false });
                }
            }

            var scenes = new List<object>();
            foreach (var scene in LoadedScenes())
            {
                try
                {
                    var topology = scene.isDirty ? CaptureDirtySceneTopology(scene, errors) : null;
                    var topologyJson = topology == null ? "" : topology.ToString(Newtonsoft.Json.Formatting.None);
                    scenes.Add(new
                    {
                        handle = scene.handle,
                        path = scene.path ?? "",
                        name = scene.name ?? "",
                        loaded = scene.isLoaded,
                        active = scene == SceneManager.GetActiveScene(),
                        dirty = scene.isDirty,
                        knownTransientPreview = IsKnownTransientPreviewScene(scene),
                        topology,
                        topologyFingerprint = Fingerprint(scene.handle + "\n" + (scene.path ?? "") + "\n" + (scene.name ?? "") + "\n" + scene.isLoaded + "\n" + (scene == SceneManager.GetActiveScene()) + "\n" + scene.isDirty + "\n" + topologyJson),
                        serializedComplete = !scene.isDirty || topology != null,
                    });
                }
                catch (Exception ex)
                {
                    errors.Add(new { kind = "scene_serialization", handle = scene.handle, path = scene.path ?? "", error = ex.GetType().Name });
                    scenes.Add(new { handle = scene.handle, path = scene.path ?? "", name = scene.name ?? "", loaded = scene.isLoaded, active = scene == SceneManager.GetActiveScene(), dirty = scene.isDirty, knownTransientPreview = IsKnownTransientPreviewScene(scene), topology = (JObject)null, topologyFingerprint = "", serializedComplete = false });
                }
            }

            var complete = !string.IsNullOrWhiteSpace(coreInstanceId)
                && processId > 0
                && !string.IsNullOrWhiteSpace(processStartedAtUtc)
                && !string.IsNullOrWhiteSpace(projectPathDigest)
                && errors.Count == 0
                && dirtyAssets.All(item => (bool)JObject.FromObject(item)["serializedComplete"])
                && scenes.All(item => (bool)JObject.FromObject(item)["serializedComplete"]);
            return new
            {
                schema = "vrcforge.recovery_witness.v1",
                coreIdentity = coreInstanceId,
                coreInstanceId,
                coreProductIdentity = VRCForgeMcpToolContract.CoreIdentity,
                coreVersion = VRCForgeMcpToolContract.ProductVersion,
                coreContractVersion = VRCForgeMcpToolContract.ToolContractVersion,
                unityProcessId = processId,
                unityProcessStartedAtUtc = processStartedAtUtc,
                processId,
                processStartTime = processStartedAtUtc,
                projectPathDigest,
                projectRootDigest = projectPathDigest,
                loadedSceneCount = scenes.Count,
                dirtySceneCount = scenes.Count(item => (bool)JObject.FromObject(item)["dirty"]),
                dirtyAssetCount = dirtyAssets.Count,
                loadedScenes = scenes,
                dirtyAssets,
                serializationErrors = errors,
                complete,
            };
        }

        private static JObject CaptureDirtySceneTopology(Scene scene, List<object> errors)
        {
            var objects = new JArray();
            foreach (var root in scene.GetRootGameObjects().OrderBy(item => item.name, StringComparer.Ordinal))
            {
                foreach (var transform in root.GetComponentsInChildren<Transform>(true))
                {
                    var gameObject = transform.gameObject;
                    var components = new JArray();
                    var componentIndex = 0;
                    foreach (var component in gameObject.GetComponents<Component>())
                    {
                        if (component == null)
                        {
                            errors.Add(new { kind = "scene_component_serialization", scene = scene.path ?? "", path = RelativePath(root.transform, transform), componentIndex, error = "missing_component" });
                            componentIndex++;
                            continue;
                        }
                        try
                        {
                            components.Add(new JObject
                            {
                                ["index"] = componentIndex,
                                ["type"] = component.GetType().FullName ?? component.GetType().Name,
                                ["serializedState"] = EditorJsonUtility.ToJson(component),
                            });
                        }
                        catch (Exception ex)
                        {
                            errors.Add(new { kind = "scene_component_serialization", scene = scene.path ?? "", path = RelativePath(root.transform, transform), componentIndex, type = component.GetType().FullName ?? component.GetType().Name, error = ex.GetType().Name });
                        }
                        componentIndex++;
                    }
                    try
                    {
                        objects.Add(new JObject
                        {
                            ["path"] = RelativePath(root.transform, transform),
                            ["gameObjectState"] = EditorJsonUtility.ToJson(gameObject),
                            ["components"] = components,
                        });
                    }
                    catch (Exception ex)
                    {
                        errors.Add(new { kind = "scene_game_object_serialization", scene = scene.path ?? "", path = RelativePath(root.transform, transform), error = ex.GetType().Name });
                    }
                }
            }
            return new JObject { ["objects"] = objects };
        }

        private static string RelativePath(Transform root, Transform current)
        {
            var parts = new List<string>();
            for (var item = current; item != null; item = item.parent)
            {
                parts.Add(item.name ?? "");
                if (item == root) break;
            }
            parts.Reverse();
            return string.Join("/", parts);
        }

        private static string SafeAssetPath(UnityEngine.Object asset)
        {
            try { return AssetDatabase.GetAssetPath(asset) ?? ""; }
            catch { return ""; }
        }

        private static string Fingerprint(string value)
        {
            using (var hash = SHA256.Create())
            {
                return BitConverter.ToString(hash.ComputeHash(new UTF8Encoding(false).GetBytes(value ?? ""))).Replace("-", "").ToLowerInvariant();
            }
        }
    }

    // Files are owned by this invocation until their references are returned. The
    // backend then owns their incident lifetime and removes exact hash matches.
    internal sealed class CheckpointEvidenceFiles : IDisposable
    {
        private readonly string projectRoot;
        private readonly Func<object[]> captureAfter;
        private readonly string evidenceName;
        private readonly bool requireComplete;
        private readonly List<string> createdFiles = new List<string>();
        private JObject before;
        private bool beforeComplete = true;
        private bool published;

        internal CheckpointEvidenceFiles(string projectRoot, Func<object[]> captureAfter)
            : this(projectRoot, captureAfter, "dirtyAssetEvidence", false)
        {
        }

        internal CheckpointEvidenceFiles(string projectRoot, Func<object[]> captureAfter, string evidenceName, bool requireComplete)
        {
            this.projectRoot = projectRoot;
            this.captureAfter = captureAfter;
            this.evidenceName = evidenceName;
            this.requireComplete = requireComplete;
        }


        internal void RecordBefore(object[] rows)
        {
            before = Write(rows);
            beforeComplete = RowsComplete(rows);
        }

        internal VRCForgeToolResult Complete(VRCForgeToolResult result)
        {
            if (before == null) return result;
            var payload = result.Payload == null ? new JObject() : JObject.FromObject(result.Payload);
            payload[evidenceName + "Before"] = before;
            try
            {
                var afterRows = captureAfter();
                payload[evidenceName + "After"] = Write(afterRows);
                var afterComplete = RowsComplete(afterRows);
                if (requireComplete)
                {
                    payload[evidenceName + "Complete"] = beforeComplete && afterComplete;
                    if (!beforeComplete || !afterComplete)
                    {
                        result = VRCForgeToolResult.FailedWithCode(
                            "checkpoint_recovery_evidence_incomplete",
                            "The read-only recovery witness could not serialize the complete editor state.",
                            payload);
                    }
                }
            }
            catch (Exception ex)
            {
                payload["evidenceError"] = ex.GetType().Name;
                payload["recoveryRequired"] = true;
                if (requireComplete) payload[evidenceName + "Complete"] = false;
                result = VRCForgeToolResult.FailedWithCode(
                    requireComplete ? "checkpoint_recovery_evidence_capture_failed" : "checkpoint_evidence_capture_failed",
                    requireComplete
                        ? "The read-only recovery witness could not be completed."
                        : "Checkpoint dirty-asset evidence could not be completed.",
                    payload);
            }
            var completed = result.IsSuccessful
                ? VRCForgeToolResult.Completed(result.Message, payload)
                : VRCForgeToolResult.FailedWithCode(result.ErrorCode, result.Message, payload);
            published = true;
            return completed;
        }

        private JObject Write(object[] rows)
        {
            var document = JArray.FromObject(rows);
            var bytes = new UTF8Encoding(false).GetBytes(document.ToString(Newtonsoft.Json.Formatting.None));
            string digest;
            using (var hash = SHA256.Create())
                digest = BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
            var items = new JArray(document.Take(16).Select(row => new JObject
            {
                ["assetPath"] = Bounded(row["assetPath"]?.ToString()),
                ["type"] = Bounded(row["type"]?.ToString()),
                ["dirtyCount"] = row["dirtyCount"]?.DeepClone()
            }));
            var summary = new JObject { ["total"] = rows.Length, ["items"] = items };
            var witness = document.FirstOrDefault() as JObject;
            if (witness != null && witness["schema"]?.Value<string>() == "vrcforge.recovery_witness.v1")
            {
                summary["schema"] = witness["schema"];
                summary["coreIdentity"] = Bounded(witness["coreIdentity"]?.ToString());
                summary["coreInstanceId"] = Bounded(witness["coreInstanceId"]?.ToString());
                summary["coreVersion"] = Bounded(witness["coreVersion"]?.ToString());
                summary["processId"] = witness["processId"];
                summary["processStartTime"] = Bounded(witness["processStartTime"]?.ToString());
                summary["projectPathDigest"] = Bounded(witness["projectPathDigest"]?.ToString());
                summary["loadedSceneCount"] = witness["loadedSceneCount"];
                summary["dirtySceneCount"] = witness["dirtySceneCount"];
                summary["dirtyAssetCount"] = witness["dirtyAssetCount"];
                summary["complete"] = witness["complete"];
            }
            else if (requireComplete && RowsComplete(rows)) summary["complete"] = true;
            do
            {
                summary["truncated"] = items.Count < rows.Length;
                if (Encoding.UTF8.GetByteCount(summary.ToString(Newtonsoft.Json.Formatting.None)) <= 4096) break;
                items.RemoveAt(items.Count - 1);
            } while (items.Count > 0);
            var reference = "Library/VRCForge/CheckpointEvidence/" + Guid.NewGuid().ToString("N") + ".json";
            var path = Path.Combine(projectRoot, reference);
            var temporary = path + ".tmp";
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            try
            {
                using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(true);
                }
                File.Move(temporary, path);
                createdFiles.Add(path);
            }
            finally
            {
                if (File.Exists(temporary)) File.Delete(temporary);
            }
            return new JObject { ["reference"] = reference, ["sha256"] = digest,
                ["byteCount"] = bytes.Length, ["objectCount"] = rows.Length, ["summary"] = summary };
        }

        private static bool RowsComplete(object[] rows)
        {
            if (rows == null) return false;
            foreach (var row in rows)
            {
                if (row == null) return false;
                var value = JToken.FromObject(row)?["complete"];
                if (value != null && value.Type == JTokenType.Boolean && !value.Value<bool>()) return false;
            }
            return true;
        }

        private static string Bounded(string value)
        {
            value = value ?? "";
            return value.Length <= 80 ? value : value.Substring(0, 80);
        }

        public void Dispose()
        {
            if (!published)
                foreach (var path in createdFiles) File.Delete(path);
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
            [VRCForgeInput("Refresh non-scene assets because checkpoint restore changed them.", IsRequired = false)] public bool refreshAssets { get; set; } = true;
            [VRCForgeInput("Exact approved checkpoint disk footprint.", IsRequired = false)] public string[] checkpointFiles { get; set; }
            [VRCForgeInput("Actual restored disk files for targeted asset import.", IsRequired = false)] public string[] restoredAssetPaths { get; set; }
            [VRCForgeInput("Exact asset state captured by checkpoint preparation.", IsRequired = false)] public JArray assetBaseline { get; set; }
            [VRCForgeInput("Live-run SHA-256 digest when a bound fixture request is used.", IsRequired = false)] public string expectedRunIdDigest { get; set; } = "";
            [VRCForgeInput("Expected Unity project-root SHA-256 digest.", IsRequired = false)] public string expectedProjectPathDigest { get; set; } = "";
            [VRCForgeInput("Expected Unity process id.", IsRequired = false)] public int? expectedUnityProcessId { get; set; }
            [VRCForgeInput("Expected Unity process start time in UTC.", IsRequired = false)] public string expectedUnityProcessStartedAtUtc { get; set; } = "";
            [VRCForgeInput("Expected Unity executable SHA-256 digest.", IsRequired = false)] public string expectedUnityExecutableDigest { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            using (var evidence = new CheckpointEvidenceFiles(CheckpointPrepareTool.ProjectRoot(), CheckpointPrepareTool.DirtyAssetEvidence))
                return evidence.Complete(HandleCommandWithEvidence(@params, evidence));
        }

        private static VRCForgeToolResult HandleCommandWithEvidence(JObject @params, CheckpointEvidenceFiles evidence)
        {
            var receipts = new List<SceneTransactionReceipt>();
            var transactionHandle = "";
            var recoveryPhase = "reload";
            var mutationStarted = false;
            try
            {
                var identity = PrimitiveBasisLiveGuard.RequireBoundRequest(@params);
                CheckpointPrepareTool.ValidateProject(@params);
                CheckpointPrepareTool.EnsureEditorReady();
                var phase = (@params?["phase"]?.ToString() ?? "reload").Trim();
                var dirtyAssetEvidenceBefore = CheckpointPrepareTool.DirtyAssetEvidence();
                var checkpointFiles = CheckpointPrepareTool.ExactFileScope(@params?["checkpointFiles"]);
                recoveryPhase = phase;
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
                    var dirty = loaded
                        .Where(scene => scene.isDirty)
                        .Select(scene => scene.path)
                        .ToList();
                    if (dirty.Count > 0)
                    {
                        return VRCForgeToolResult.Failed(
                            "Checkpoint restore cannot discard dirty scenes.",
                            new { phase, blocking = true, scenes = dirty });
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
                    transactionHandle = $"{CheckpointPrepareTool.ProjectRoot()}|{phase}";
                    receipts.AddRange(prepareScenes.Select(path => new SceneTransactionReceipt
                    {
                        Asset = path,
                        Before = DescribeScene(path)
                    }));
                    var closedScenes = new List<string>();
                    Scene scratch = default;
                    try
                    {
                        evidence.RecordBefore(dirtyAssetEvidenceBefore);
                        mutationStarted = true;
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
                                var receipt = receipts.First(item => item.Asset == path);
                                receipt.After = DescribeScene(path);
                                receipt.Status = "succeeded";
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
                        ReadSceneAfter(receipts);
                        foreach (var receipt in receipts.Where(item => closedScenes.Contains(item.Asset)))
                        {
                            var restored = SceneManager.GetSceneByPath(receipt.Asset);
                            if (restored.IsValid() && restored.isLoaded)
                            {
                                receipt.Status = "rolled_back";
                                receipt.RolledBack = true;
                            }
                            else
                            {
                                receipt.Status = "failed";
                                receipt.Error = closeError.Message;
                                receipt.RolledBack = false;
                            }
                        }
                        return VRCForgeToolResult.Failed(
                            "Checkpoint restore could not safely close all project scenes.",
                            new
                            {
                                phase,
                                blocking = true,
                                closedScenes,
                                reopenErrors,
                                error = closeError.Message,
                                transaction = BuildSceneTransaction(receipts, transactionHandle)
                            });
                    }
                    ReadSceneAfter(receipts);
                    return VRCForgeToolResult.Completed(
                        "Closed project scenes before checkpoint file recovery.",
                        new
                        {
                            ok = true,
                            phase,
                            projectPath = CheckpointPrepareTool.ProjectRoot(),
                            scenes = prepareScenes,
                            checkpointFiles,
                            activeScenePath = prepareActiveScenePath,
                            unityProcessId = identity?.ProcessId,
                            unityProcessStartedAtUtc = identity?.StartedAtUtc,
                            unityExecutableDigest = identity?.ExecutableDigest,
                            projectPathDigest = identity?.ProjectPathDigest,
                            transaction = BuildSceneTransaction(receipts, transactionHandle)
                        });
                }
                if (!string.Equals(phase, "reload", StringComparison.Ordinal))
                {
                    return VRCForgeToolResult.Failed($"Unknown checkpoint restore phase: {phase}");
                }
                var refreshAssets = @params?["refreshAssets"]?.Type != JTokenType.Boolean
                    || @params["refreshAssets"].Value<bool>();

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
                        || !path.EndsWith(".unity", StringComparison.OrdinalIgnoreCase)
                        || path.Split('/').Any(part => part == ".." || part == "." || part.Length == 0)
                        || !File.Exists(Path.Combine(CheckpointPrepareTool.ProjectRoot(), path)))
                    {
                        return VRCForgeToolResult.Failed(
                            "Checkpoint reload scene path is unavailable.",
                            new { phase, blocking = true, scene = path });
                    }
                }

                transactionHandle = $"{CheckpointPrepareTool.ProjectRoot()}|{phase}";
                receipts.AddRange(scenes.Select(path => new SceneTransactionReceipt
                {
                    Asset = path,
                    Before = DescribeScene(path)
                }));
                evidence.RecordBefore(dirtyAssetEvidenceBefore);
                mutationStarted = true;

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
                        var receipt = receipts.First(item => item.Asset == path);
                        receipt.After = DescribeScene(path);
                        receipt.Status = "succeeded";
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
                    ReadSceneAfter(receipts);
                    foreach (var receipt in receipts.Where(item => closedBeforeReload.Contains(item.Asset)))
                    {
                        var restored = SceneManager.GetSceneByPath(receipt.Asset);
                        if (restored.IsValid() && restored.isLoaded)
                        {
                            receipt.Status = "rolled_back";
                            receipt.RolledBack = true;
                        }
                        else
                        {
                            receipt.Status = "failed";
                            receipt.Error = closeError.Message;
                            receipt.RolledBack = false;
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
                            error = closeError.Message,
                            transaction = BuildSceneTransaction(receipts, transactionHandle)
                        });
                }

                var restoredScenes = new List<Scene>();
                try
                {
                    // File recovery happened outside Unity while these scenes were closed.
                    // Synchronize every scene asset before reopening any of them, including
                    // scene-only restores that intentionally skip the broader asset refresh.
                    foreach (var path in scenes)
                    {
                        AssetDatabase.ImportAsset(path,
                            ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
                    }
                    foreach (var path in scenes)
                    {
                        var restored = EditorSceneManager.OpenScene(path, OpenSceneMode.Additive);
                        restoredScenes.Add(restored);
                        var receipt = receipts.First(item => item.Asset == path);
                        receipt.After = DescribeScene(path);
                        receipt.Status = "succeeded";
                    }
                }
                catch (Exception reopenError)
                {
                    foreach (var restored in restoredScenes.Where(scene => scene.IsValid() && scene.isLoaded))
                    {
                        EditorSceneManager.CloseScene(restored, true);
                    }
                    ReadSceneAfter(receipts);
                    foreach (var restored in restoredScenes)
                    {
                        var receipt = receipts.First(item => item.Asset == restored.path);
                        receipt.Status = "rolled_back";
                        receipt.RolledBack = true;
                    }
                    var failed = receipts.FirstOrDefault(item => item.Status == "not_attempted");
                    if (failed != null)
                    {
                        failed.Status = "failed";
                        failed.Error = reopenError.Message;
                        failed.RolledBack = false;
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
                            error = reopenError.Message,
                            transaction = BuildSceneTransaction(receipts, transactionHandle)
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
                            new
                            {
                                phase,
                                blocking = true,
                                recoveryRequired = true,
                                scenes,
                                activeScenePath,
                                transaction = BuildFailedSceneTransaction(
                                    receipts,
                                    transactionHandle,
                                    "active_scene",
                                    "Could not reactivate the original scene.")
                            });
                    }
                    foreach (var scratch in scratchScenes.Where(scene => scene.IsValid() && scene.isLoaded))
                    {
                        EditorSceneManager.CloseScene(scratch, true);
                    }
                }
                if (refreshAssets)
                {
                    var restoredPaths = CheckpointPrepareTool.ExactFileScope(@params?["restoredAssetPaths"], allowEmpty: true);
                    if (restoredPaths == null) AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                    else foreach (var path in restoredPaths.Where(path => !path.EndsWith(".unity", StringComparison.OrdinalIgnoreCase)))
                        AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                }
                var assetReadback = @params?.Property("assetBaseline") != null
                    ? CheckpointPrepareTool.RestoreAssetBaseline(@params["assetBaseline"]) : null;
                VRCForgeMcpCoreServer.ScheduleInvocationPumpRegistration();
                ReadSceneAfter(receipts);
                return VRCForgeToolResult.Completed(
                    "Reloaded restored scenes and refreshed project assets.",
                    new
                    {
                        ok = true,
                        phase,
                        projectPath = CheckpointPrepareTool.ProjectRoot(),
                        scenes,
                        refreshAssets,
                        assetBaselineVerified = assetReadback != null,
                        assetReadback,
                        unityProcessId = identity?.ProcessId,
                        unityProcessStartedAtUtc = identity?.StartedAtUtc,
                        unityExecutableDigest = identity?.ExecutableDigest,
                        projectPathDigest = identity?.ProjectPathDigest,
                        transaction = BuildSceneTransaction(receipts, transactionHandle)
                    });
            }
            catch (Exception ex)
            {
                if (mutationStarted)
                {
                    ReadSceneAfter(receipts);
                    return VRCForgeToolResult.Failed($"Checkpoint reload failed: {ex.Message}", new
                    {
                        phase = recoveryPhase,
                        transaction = BuildFailedSceneTransaction(
                            receipts,
                            transactionHandle,
                            "transaction",
                            ex.Message)
                    });
                }
                return VRCForgeToolResult.Failed($"Checkpoint reload failed: {ex.Message}");
            }
        }

        private static object DescribeScene(string path)
        {
            var scene = SceneManager.GetSceneByPath(path);
            return new
            {
                path,
                loaded = scene.IsValid() && scene.isLoaded,
                active = scene.IsValid() && scene == SceneManager.GetActiveScene(),
                dirty = scene.IsValid() && scene.isDirty
            };
        }

        private static void ReadSceneAfter(IEnumerable<SceneTransactionReceipt> receipts)
        {
            foreach (var receipt in receipts)
            {
                receipt.After = DescribeScene(receipt.Asset);
            }
        }

        private static object BuildFailedSceneTransaction(
            List<SceneTransactionReceipt> receipts,
            string transactionHandle,
            string asset,
            string error)
        {
            receipts.Add(new SceneTransactionReceipt
            {
                Asset = asset,
                Before = new { started = true },
                After = new { completed = false },
                Status = "failed",
                Error = error,
                RolledBack = false
            });
            return BuildSceneTransaction(receipts, transactionHandle);
        }

        private static object BuildSceneTransaction(
            List<SceneTransactionReceipt> receipts,
            string transactionHandle)
        {
            var transactionItems = receipts.Select(item => new
            {
                asset = item.Asset,
                before = item.Before,
                after = item.After ?? item.Before,
                status = item.Status,
                error = item.Error,
                rolled_back = item.RolledBack
            }).ToList();
            return new
            {
                assets_touched = transactionItems.Count,
                items = transactionItems.Take(20).ToArray(),
                handle = transactionHandle
            };
        }

        private sealed class SceneTransactionReceipt
        {
            public string Asset = "";
            public object Before;
            public object After;
            public string Status = "not_attempted";
            public string Error = "";
            public bool RolledBack = false;
        }
    }
}
