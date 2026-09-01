using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_duplicate_scene_asset",
        Summary = "when-to-use: preview and create-new duplicate one exact saved Assets scene, optionally opening the copy as the only active scene. when-NOT-to-use: do not overwrite, move, rename, merge, save dirty scenes, or copy arbitrary files. Negative example: do not call it with an existing destination scene."
    )]
    public static class SceneAssetDuplicateTool
    {
        internal const string ToolName = "vrc_duplicate_scene_asset";
        internal const string ResultSchema = "vrcforge.scene_asset_duplicate.v1";
        internal const string Operation = "duplicate_scene_asset";
        private const string PreviewDigestSchema = "vrcforge.scene_asset_duplicate_preview.v1";
        private const int StableReadAttempts = 3;
        private const int StableReadRetryDelayMilliseconds = 75;

        public class Parameters
        {
            [VRCForgeInput("Exact existing source scene path below Assets.", IsRequired = true)] public string sourceScenePath { get; set; } = "";
            [VRCForgeInput("Exact absent destination scene path below Assets.", IsRequired = true)] public string destinationScenePath { get; set; } = "";
            [VRCForgeInput("After copying, open the copy as the only active scene.", IsRequired = false)] public bool? openAsOnlyActiveScene { get; set; } = false;
            [VRCForgeInput("Return a non-mutating create-new preview.", IsRequired = false)] public bool? preview { get; set; } = false;
            [VRCForgeInput("Must remain false; overwrite is unsupported.", IsRequired = false)] public bool? overwrite { get; set; } = false;
            [VRCForgeInput("Expected active Unity project root from preview.", IsRequired = false)] public string expectedProjectPath { get; set; } = "";
            [VRCForgeInput("Expected source scene GUID.", IsRequired = false)] public string expectedSourceGuid { get; set; } = "";
            [VRCForgeInput("Expected source scene file SHA-256.", IsRequired = false)] public string expectedSourceFileDigest { get; set; } = "";
            [VRCForgeInput("Expected source scene file identity.", IsRequired = false)] public string expectedSourceFileIdentity { get; set; } = "";
            [VRCForgeInput("Expected source scene metadata SHA-256.", IsRequired = false)] public string expectedSourceMetaDigest { get; set; } = "";
            [VRCForgeInput("Expected source scene metadata identity.", IsRequired = false)] public string expectedSourceMetaIdentity { get; set; } = "";
            [VRCForgeInput("Expected destination parent folder path.", IsRequired = false)] public string expectedDestinationParentPath { get; set; } = "";
            [VRCForgeInput("Expected destination-absent assertion from preview.", IsRequired = false)] public bool? expectedDestinationAbsent { get; set; }
            [VRCForgeInput("Expected current restorable scene-setup digest.", IsRequired = false)] public string expectedSceneSetupDigest { get; set; } = "";
            [VRCForgeInput("Expected current loaded-scene state digest.", IsRequired = false)] public string expectedOpenSceneStateDigest { get; set; } = "";
            [VRCForgeInput("Expected requested open behavior.", IsRequired = false)] public bool? expectedOpenAsOnlyActiveScene { get; set; }
            [VRCForgeInput("Expected authoritative preview digest.", IsRequired = false)] public string expectedPreviewDigest { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            var mutationStarted = false;
            var sceneSetupChanged = false;
            StableAssetEvidence createdEvidence = null;
            SceneDuplicateSnapshot snapshot = null;
            try
            {
                CheckpointPrepareTool.EnsureEditorReady();
                var parameters = @params ?? new JObject();
                if (parameters["overwrite"]?.Value<bool?>() ?? false)
                {
                    throw new SceneObjectCopyException("Scene overwrite is not supported.");
                }

                var openAsOnlyActiveScene = parameters["openAsOnlyActiveScene"]?.Value<bool?>() ?? false;
                snapshot = BuildSnapshot(
                    parameters["sourceScenePath"]?.ToString() ?? string.Empty,
                    parameters["destinationScenePath"]?.ToString() ?? string.Empty,
                    openAsOnlyActiveScene);
                if (parameters["preview"]?.Value<bool?>() ?? false)
                {
                    return VRCForgeToolResult.Completed(
                        "Validated one create-new scene asset duplicate without changing the project.",
                        snapshot.ToPreviewPayload());
                }

                VerifyExpected(parameters, snapshot);
                VerifySnapshotCurrent(snapshot);
                if (!AssetDatabase.CopyAsset(snapshot.SourcePath, snapshot.DestinationPath))
                {
                    throw new SceneObjectCopyException("Unity AssetDatabase refused the create-new scene copy.");
                }
                mutationStarted = true;
                AssetDatabase.SaveAssets();
                AssetDatabase.ImportAsset(
                    snapshot.DestinationPath,
                    ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

                createdEvidence = ReadCreatedEvidenceWithRetry(snapshot.DestinationPath);
                VerifyCreatedScene(snapshot, createdEvidence);

                Scene openedScene = default(Scene);
                if (snapshot.OpenAsOnlyActiveScene)
                {
                    sceneSetupChanged = true;
                    openedScene = EditorSceneManager.OpenScene(
                        snapshot.DestinationPath,
                        OpenSceneMode.Single);
                    if (!openedScene.IsValid()
                        || !openedScene.isLoaded
                        || openedScene.isDirty
                        || !string.Equals(
                            (openedScene.path ?? string.Empty).Replace('\\', '/'),
                            snapshot.DestinationPath,
                            StringComparison.Ordinal)
                        || SceneManager.sceneCount != 1
                        || SceneManager.GetActiveScene().handle != openedScene.handle)
                    {
                        throw new SceneObjectCopyException(
                            "The copied scene did not become the only loaded active scene.");
                    }
                }

                VerifySourceUnchanged(snapshot);
                var target = TargetPayload(snapshot, createdEvidence, openedScene);
                return VRCForgeToolResult.Completed(
                    snapshot.OpenAsOnlyActiveScene
                        ? "Created, verified, and opened one independent scene asset copy."
                        : "Created and verified one independent scene asset copy.",
                    new
                    {
                        schema = ResultSchema,
                        ok = true,
                        operation = Operation,
                        preview = false,
                        verified = true,
                        changed = true,
                        saved = true,
                        mutationCount = snapshot.OpenAsOnlyActiveScene ? 2 : 1,
                        mutationStarted = true,
                        commitState = "committed",
                        checkpointRestoreRequired = false,
                        manualRecoveryRequired = false,
                        source = SourcePayload(snapshot, true),
                        target,
                        before = new
                        {
                            source = SourcePayload(snapshot, true),
                            target = new { assetPath = snapshot.DestinationPath, exists = false },
                            sceneSetupDigest = snapshot.SceneSetupDigest,
                            openSceneStateDigest = snapshot.OpenSceneStateDigest
                        },
                        after = target,
                        affected = new
                        {
                            count = 1,
                            items = new[] { snapshot.DestinationPath },
                            handle = createdEvidence.Guid
                        },
                        previewDigest = snapshot.PreviewDigest,
                        cleanupRequired = false
                    });
            }
            catch (Exception exception)
            {
                if (!mutationStarted || snapshot == null)
                {
                    return Failure(exception, false, false, "validation");
                }
                var restored = Rollback(snapshot, createdEvidence, sceneSetupChanged);
                return Failure(exception, true, !restored, "apply");
            }
        }

        private static SceneDuplicateSnapshot BuildSnapshot(
            string rawSourcePath,
            string rawDestinationPath,
            bool openAsOnlyActiveScene)
        {
            var sourcePath = SceneObjectCopyCore.NormalizeSceneAssetPath(rawSourcePath, "sourceScenePath");
            var destinationPath = SceneObjectCopyCore.NormalizeSceneAssetPath(rawDestinationPath, "destinationScenePath");
            if (string.Equals(sourcePath, destinationPath, StringComparison.Ordinal))
            {
                throw new SceneObjectCopyException("The source and destination scene paths are identical.");
            }
            if (SceneObjectCopyCore.AssetOrMetaExists(destinationPath))
            {
                throw new SceneObjectCopyException(
                    "The destination scene or its metadata already exists; overwrite is unsupported.");
            }
            var parentPath = (Path.GetDirectoryName(destinationPath) ?? string.Empty).Replace('\\', '/');
            if (string.IsNullOrWhiteSpace(parentPath) || !AssetDatabase.IsValidFolder(parentPath))
            {
                throw new SceneObjectCopyException("The destination scene folder must already exist.");
            }

            var sourceAsset = AssetDatabase.LoadAssetAtPath<SceneAsset>(sourcePath);
            var sourceType = AssetDatabase.GetMainAssetTypeAtPath(sourcePath);
            if (sourceAsset == null || sourceType != typeof(SceneAsset))
            {
                throw new SceneObjectCopyException("The source Unity scene asset is unavailable.");
            }
            var sourceEvidence = SceneObjectCopyCore.ReadStableAssetEvidence(
                sourcePath,
                "source scene asset");
            if (sourceEvidence.File.LinkCount != 1 || sourceEvidence.Meta.LinkCount != 1)
            {
                throw new SceneObjectCopyException("The source scene must be backed by single-link files.");
            }

            var loaded = CheckpointPrepareTool.LoadedScenes();
            var sourceLoaded = loaded.FirstOrDefault(scene => string.Equals(
                (scene.path ?? string.Empty).Replace('\\', '/'),
                sourcePath,
                StringComparison.Ordinal));
            if (sourceLoaded.IsValid() && sourceLoaded.isDirty)
            {
                throw new SceneObjectCopyException(
                    "The loaded source scene is dirty; save or revert it before duplicating the persisted asset.");
            }
            if (openAsOnlyActiveScene && loaded.Any(scene => scene.isDirty || string.IsNullOrWhiteSpace(scene.path)))
            {
                throw new SceneObjectCopyException(
                    "Opening the copy as the only scene requires every loaded scene to be saved and clean.");
            }

            var setup = EditorSceneManager.GetSceneManagerSetup();
            var snapshot = new SceneDuplicateSnapshot
            {
                ProjectPath = ProjectRoot(),
                SourcePath = sourcePath,
                DestinationPath = destinationPath,
                DestinationParentPath = parentPath,
                SourceEvidence = sourceEvidence,
                SourceLoaded = sourceLoaded.IsValid() && sourceLoaded.isLoaded,
                OpenAsOnlyActiveScene = openAsOnlyActiveScene,
                OriginalSceneSetup = setup,
                SceneSetupDigest = ComputeSceneSetupDigest(setup),
                OpenSceneStateDigest = ComputeOpenSceneStateDigest()
            };
            snapshot.PreviewDigest = ComputePreviewDigest(snapshot);
            return snapshot;
        }

        private static void VerifyExpected(JObject parameters, SceneDuplicateSnapshot snapshot)
        {
            if (!SceneObjectCopyCore.MatchesCurrentProject(RequiredString(parameters, "expectedProjectPath"))
                || RequiredString(parameters, "expectedSourceGuid") != snapshot.SourceEvidence.Guid
                || RequiredString(parameters, "expectedSourceFileDigest") != snapshot.SourceEvidence.File.Digest
                || RequiredString(parameters, "expectedSourceFileIdentity") != snapshot.SourceEvidence.File.Identity
                || RequiredString(parameters, "expectedSourceMetaDigest") != snapshot.SourceEvidence.Meta.Digest
                || RequiredString(parameters, "expectedSourceMetaIdentity") != snapshot.SourceEvidence.Meta.Identity
                || RequiredString(parameters, "expectedDestinationParentPath") != snapshot.DestinationParentPath
                || !RequiredBool(parameters, "expectedDestinationAbsent")
                || RequiredString(parameters, "expectedSceneSetupDigest") != snapshot.SceneSetupDigest
                || RequiredString(parameters, "expectedOpenSceneStateDigest") != snapshot.OpenSceneStateDigest
                || RequiredBool(parameters, "expectedOpenAsOnlyActiveScene") != snapshot.OpenAsOnlyActiveScene
                || RequiredString(parameters, "expectedPreviewDigest") != snapshot.PreviewDigest)
            {
                throw new SceneObjectCopyException(
                    "The scene duplicate preview evidence changed before apply.");
            }
        }

        private static void VerifySnapshotCurrent(SceneDuplicateSnapshot snapshot)
        {
            var current = BuildSnapshot(
                snapshot.SourcePath,
                snapshot.DestinationPath,
                snapshot.OpenAsOnlyActiveScene);
            if (current.PreviewDigest != snapshot.PreviewDigest)
            {
                throw new SceneObjectCopyException("The scene duplicate state changed before mutation.");
            }
        }

        private static void VerifyCreatedScene(
            SceneDuplicateSnapshot snapshot,
            StableAssetEvidence createdEvidence)
        {
            var destinationAsset = AssetDatabase.LoadAssetAtPath<SceneAsset>(snapshot.DestinationPath);
            var destinationType = AssetDatabase.GetMainAssetTypeAtPath(snapshot.DestinationPath);
            if (createdEvidence == null
                || destinationAsset == null
                || destinationType != typeof(SceneAsset)
                || createdEvidence.Guid == snapshot.SourceEvidence.Guid
                || createdEvidence.File.LinkCount != 1
                || createdEvidence.Meta.LinkCount != 1
                || createdEvidence.File.Digest != snapshot.SourceEvidence.File.Digest)
            {
                throw new SceneObjectCopyException(
                    "The created scene copy failed independent persisted readback verification.");
            }
        }

        private static void VerifySourceUnchanged(SceneDuplicateSnapshot snapshot)
        {
            var current = SceneObjectCopyCore.ReadStableAssetEvidence(
                snapshot.SourcePath,
                "source scene asset readback");
            if (!SceneObjectCopyCore.StableAssetEvidenceMatches(
                snapshot.SourceEvidence,
                current,
                true))
            {
                throw new SceneObjectCopyException("The source scene changed during the copy.");
            }
        }

        private static StableAssetEvidence ReadCreatedEvidenceWithRetry(string assetPath)
        {
            Exception lastError = null;
            for (var attempt = 1; attempt <= StableReadAttempts; attempt++)
            {
                try
                {
                    return SceneObjectCopyCore.ReadStableAssetEvidence(
                        assetPath,
                        "created scene asset copy");
                }
                catch (Exception exception)
                {
                    lastError = exception;
                    if (attempt < StableReadAttempts)
                    {
                        Thread.Sleep(StableReadRetryDelayMilliseconds);
                    }
                }
            }
            throw new SceneObjectCopyException(
                "The created scene copy could not be read stably after "
                + StableReadAttempts.ToString(CultureInfo.InvariantCulture)
                + " attempts. Last reason: "
                + (lastError?.Message ?? "unknown"));
        }

        private static bool Rollback(
            SceneDuplicateSnapshot snapshot,
            StableAssetEvidence createdEvidence,
            bool sceneSetupChanged)
        {
            try
            {
                if (sceneSetupChanged)
                {
                    EditorSceneManager.RestoreSceneManagerSetup(snapshot.OriginalSceneSetup);
                    if (ComputeSceneSetupDigest(EditorSceneManager.GetSceneManagerSetup())
                        != snapshot.SceneSetupDigest)
                    {
                        return false;
                    }
                }
                if (!SceneObjectCopyCore.AssetOrMetaExists(snapshot.DestinationPath))
                {
                    return true;
                }
                if (createdEvidence == null)
                {
                    var candidateEvidence = ReadCreatedEvidenceWithRetry(snapshot.DestinationPath);
                    VerifyCreatedScene(snapshot, candidateEvidence);
                    createdEvidence = candidateEvidence;
                }
                return createdEvidence != null
                    && SceneObjectCopyCore.DeleteOwnedAsset(
                        snapshot.DestinationPath,
                        createdEvidence);
            }
            catch
            {
                return false;
            }
        }

        private static object SourcePayload(SceneDuplicateSnapshot snapshot, bool unchanged)
        {
            return new
            {
                assetPath = snapshot.SourcePath,
                guid = snapshot.SourceEvidence.Guid,
                fileDigest = snapshot.SourceEvidence.File.Digest,
                fileIdentity = snapshot.SourceEvidence.File.Identity,
                metaDigest = snapshot.SourceEvidence.Meta.Digest,
                metaIdentity = snapshot.SourceEvidence.Meta.Identity,
                mainAssetType = typeof(SceneAsset).FullName,
                loaded = snapshot.SourceLoaded,
                unchanged
            };
        }

        private static object TargetPayload(
            SceneDuplicateSnapshot snapshot,
            StableAssetEvidence evidence,
            Scene openedScene)
        {
            var opened = openedScene.IsValid() && openedScene.isLoaded;
            return new
            {
                assetPath = snapshot.DestinationPath,
                guid = evidence.Guid,
                fileDigest = evidence.File.Digest,
                fileIdentity = evidence.File.Identity,
                metaDigest = evidence.Meta.Digest,
                metaIdentity = evidence.Meta.Identity,
                mainAssetType = typeof(SceneAsset).FullName,
                bytesIdenticalToSource = evidence.File.Digest == snapshot.SourceEvidence.File.Digest,
                createNew = true,
                readbackVerified = true,
                openAsOnlyActiveScene = snapshot.OpenAsOnlyActiveScene,
                opened,
                active = opened && SceneManager.GetActiveScene().handle == openedScene.handle,
                openSceneCount = SceneManager.sceneCount,
                sceneHandle = opened ? openedScene.handle : 0,
                sceneName = opened ? (openedScene.name ?? string.Empty) : string.Empty
            };
        }

        private static string ComputeSceneSetupDigest(SceneSetup[] setup)
        {
            var value = new StringBuilder();
            AppendDigestField(value, "vrcforge.scene_setup.v1");
            var items = setup ?? new SceneSetup[0];
            AppendDigestField(value, items.Length.ToString(CultureInfo.InvariantCulture));
            foreach (var item in items)
            {
                AppendDigestField(value, (item.path ?? string.Empty).Replace('\\', '/'));
                AppendDigestField(value, item.isLoaded ? "true" : "false");
                AppendDigestField(value, item.isActive ? "true" : "false");
            }
            return Sha256(value.ToString());
        }

        private static string ComputeOpenSceneStateDigest()
        {
            var value = new StringBuilder();
            AppendDigestField(value, "vrcforge.open_scene_state.v1");
            var scenes = CheckpointPrepareTool.LoadedScenes();
            AppendDigestField(value, scenes.Count.ToString(CultureInfo.InvariantCulture));
            foreach (var scene in scenes)
            {
                AppendDigestField(value, (scene.path ?? string.Empty).Replace('\\', '/'));
                AppendDigestField(value, scene.name ?? string.Empty);
                AppendDigestField(value, scene.handle.ToString(CultureInfo.InvariantCulture));
                AppendDigestField(value, scene.isDirty ? "true" : "false");
                AppendDigestField(value, scene.rootCount.ToString(CultureInfo.InvariantCulture));
                AppendDigestField(value,
                    SceneManager.GetActiveScene().handle == scene.handle ? "true" : "false");
            }
            return Sha256(value.ToString());
        }

        private static string ComputePreviewDigest(SceneDuplicateSnapshot snapshot)
        {
            var value = new StringBuilder();
            foreach (var field in new[]
            {
                PreviewDigestSchema,
                ResultSchema,
                Operation,
                snapshot.ProjectPath,
                snapshot.SourcePath,
                snapshot.SourceEvidence.Guid,
                snapshot.SourceEvidence.File.Digest,
                snapshot.SourceEvidence.File.Identity,
                snapshot.SourceEvidence.Meta.Digest,
                snapshot.SourceEvidence.Meta.Identity,
                snapshot.SourceLoaded ? "true" : "false",
                snapshot.DestinationPath,
                snapshot.DestinationParentPath,
                "destination_absent",
                snapshot.OpenAsOnlyActiveScene ? "true" : "false",
                snapshot.SceneSetupDigest,
                snapshot.OpenSceneStateDigest
            })
            {
                AppendDigestField(value, field);
            }
            return Sha256(value.ToString());
        }

        private static void AppendDigestField(StringBuilder target, string value)
        {
            var text = value ?? string.Empty;
            target.Append(Encoding.UTF8.GetByteCount(text).ToString(CultureInfo.InvariantCulture));
            target.Append(':');
            target.Append(text);
        }

        private static string Sha256(string value)
        {
            using (var sha = SHA256.Create())
            {
                return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(value ?? string.Empty))
                    .Select(item => item.ToString("x2", CultureInfo.InvariantCulture)));
            }
        }

        private static string RequiredString(JObject parameters, string name)
        {
            var token = parameters[name];
            if (token == null || token.Type != JTokenType.String)
            {
                throw new SceneObjectCopyException(name + " is required from preview.");
            }
            return token.Value<string>() ?? string.Empty;
        }

        private static bool RequiredBool(JObject parameters, string name)
        {
            var token = parameters[name];
            if (token == null || token.Type != JTokenType.Boolean)
            {
                throw new SceneObjectCopyException(name + " is required from preview.");
            }
            return token.Value<bool>();
        }

        private static string ProjectRoot()
        {
            return Path.GetFullPath(Path.Combine(Application.dataPath, ".."))
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                .Replace('\\', '/');
        }

        private static object Failure(
            Exception exception,
            bool mutationStarted,
            bool cleanupRequired,
            string failurePhase)
        {
            return VRCForgeToolResult.FailedWithCode(
                cleanupRequired
                    ? "scene_duplicate_cleanup_unverified"
                    : "scene_duplicate_failed",
                exception.Message,
                new
                {
                    schema = ResultSchema,
                    ok = false,
                    operation = Operation,
                    failureLayer = mutationStarted ? "unity_mutation" : "unity_validation",
                    failurePhase,
                    mutationStarted,
                    writeOccurred = mutationStarted,
                    committed = false,
                    commitState = cleanupRequired ? "unknown" : "not_committed",
                    requestMayHaveCommitted = cleanupRequired,
                    cleanupRequired,
                    checkpointRestoreRequired = cleanupRequired,
                    manualRecoveryRequired = cleanupRequired
                });
        }

        private sealed class SceneDuplicateSnapshot
        {
            internal string ProjectPath = string.Empty;
            internal string SourcePath = string.Empty;
            internal string DestinationPath = string.Empty;
            internal string DestinationParentPath = string.Empty;
            internal StableAssetEvidence SourceEvidence;
            internal bool SourceLoaded;
            internal bool OpenAsOnlyActiveScene;
            internal SceneSetup[] OriginalSceneSetup = new SceneSetup[0];
            internal string SceneSetupDigest = string.Empty;
            internal string OpenSceneStateDigest = string.Empty;
            internal string PreviewDigest = string.Empty;

            internal object ToPreviewPayload()
            {
                return new
                {
                    schema = ResultSchema,
                    ok = true,
                    operation = Operation,
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
                    source = SourcePayload(this, true),
                    target = new
                    {
                        assetPath = DestinationPath,
                        parentPath = DestinationParentPath,
                        assetExists = false,
                        metaExists = false,
                        createNew = true,
                        openAsOnlyActiveScene = OpenAsOnlyActiveScene,
                        willBecomeOnlyActiveScene = OpenAsOnlyActiveScene
                    },
                    sceneSetupDigest = SceneSetupDigest,
                    openSceneStateDigest = OpenSceneStateDigest,
                    previewDigest = PreviewDigest,
                    cleanupRequired = false
                };
            }
        }
    }
}
