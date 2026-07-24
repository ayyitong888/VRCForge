using System;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_save_scene_object_as_prefab",
        Description = "Preview or CreateNew-save one exact saved-scene hierarchy as a generated prefab asset."
    )]
    public static class SaveSceneObjectAsPrefabTool
    {
        public const string ToolName = "vrc_save_scene_object_as_prefab";

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var parameters = @params ?? new JObject();
                var sourceScenePath = parameters["sourceScenePath"]?.ToString() ?? string.Empty;
                var sourceObjectPath = parameters["sourceObjectPath"]?.ToString() ?? string.Empty;
                var prefabAssetPath = parameters["prefabAssetPath"]?.ToString() ?? string.Empty;
                var preview = parameters["preview"]?.Value<bool?>() ?? false;
                var saveAssets = parameters["saveAssets"]?.Value<bool?>() ?? false;
                var overwrite = parameters["overwrite"]?.Value<bool?>() ?? false;
                if (overwrite)
                {
                    throw new SceneObjectCopyException("Prefab overwrite is not supported.");
                }
                if (preview && saveAssets)
                {
                    throw new SceneObjectCopyException("Preview cannot save an asset.");
                }

                var snapshot = SceneObjectCopyCore.BuildPrefabPreview(
                    sourceScenePath,
                    sourceObjectPath,
                    prefabAssetPath);
                if (preview)
                {
                    return SceneObjectCopyCore.Success(
                    snapshot.ToPayload());
                }

                SceneObjectCopyCore.VerifyPrefabExpected(parameters, snapshot);
                return Apply(snapshot);
            }
            catch (Exception exception)
            {
                return SceneObjectCopyCore.Failure(exception);
            }
        }

        private static object Apply(PrefabPreviewSnapshot snapshot)
        {
            StagingFolderLease staging = null;
            var createdGuid = string.Empty;
            StableAssetEvidence stagedAsset = null;
            StableAssetEvidence ownedAsset = null;
            var mutationStarted = false;
            var finalEvidenceCaptured = false;
            try
            {
                SceneObjectCopyCore.VerifyFolderIdentity(
                    snapshot.Target.ParentFolderPath,
                    snapshot.Target.ParentFolderGuid,
                    snapshot.Target.ParentFolderIdentity,
                    "prefab destination folder");
                SceneObjectCopyCore.VerifyFolderIdentity(
                    snapshot.Target.StagingRootPath,
                    snapshot.Target.StagingRootGuid,
                    snapshot.Target.StagingRootIdentity,
                    "generated staging root");
                if (SceneObjectCopyCore.AssetOrMetaExists(snapshot.Target.AssetPath))
                {
                    throw new SceneObjectCopyException("The prefab destination appeared after preview.");
                }
                staging = SceneObjectCopyCore.CreateRandomStagingFolder(
                    snapshot.Target,
                    ref mutationStarted);
                SceneObjectCopyCore.VerifyOwnedStagingFolder(staging);

                bool saveSucceeded;
                var stagedPrefab = PrefabUtility.SaveAsPrefabAsset(
                    snapshot.Source.GameObject,
                    staging.PrefabPath,
                    out saveSucceeded);
                if (!saveSucceeded
                    || stagedPrefab == null
                    || !SceneObjectCopyCore.AssetPathExists(staging.PrefabPath))
                {
                    throw new SceneObjectCopyException("The prefab staging write did not complete.");
                }
                AssetDatabase.SaveAssets();
                AssetDatabase.ImportAsset(
                    staging.PrefabPath,
                    ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                createdGuid = SceneObjectCopyCore.ReadAssetGuid(
                    staging.PrefabPath,
                    "staged prefab");
                stagedAsset = SceneObjectCopyCore.ReadStableAssetEvidence(
                    staging.PrefabPath,
                    "staged prefab");
                ownedAsset = stagedAsset;
                var stagingReadback = AssetDatabase.LoadAssetAtPath<GameObject>(
                    staging.PrefabPath);
                if (stagingReadback == null)
                {
                    throw new SceneObjectCopyException("The staged prefab readback is unavailable.");
                }
                var stagingHierarchyDigest = SceneObjectCopyCore.ComputeHierarchyDigest(stagingReadback);
                SceneObjectCopyCore.VerifySourceUnchanged(snapshot.Source, true);
                SceneObjectCopyCore.VerifyOwnedStagingFolder(staging);

                if (SceneObjectCopyCore.AssetOrMetaExists(snapshot.Target.AssetPath))
                {
                    throw new SceneObjectCopyException("The prefab destination appeared after preview.");
                }
                SceneObjectCopyCore.VerifyFolderIdentity(
                    snapshot.Target.ParentFolderPath,
                    snapshot.Target.ParentFolderGuid,
                    snapshot.Target.ParentFolderIdentity,
                    "prefab destination folder");
                SceneObjectCopyCore.VerifyFolderIdentity(
                    snapshot.Target.StagingRootPath,
                    snapshot.Target.StagingRootGuid,
                    snapshot.Target.StagingRootIdentity,
                    "generated staging root");

                var moveError = AssetDatabase.MoveAsset(
                    staging.PrefabPath,
                    snapshot.Target.AssetPath);
                if (!string.IsNullOrEmpty(moveError))
                {
                    throw new SceneObjectCopyException("The prefab CreateNew move was rejected.");
                }
                AssetDatabase.SaveAssets();

                var finalAsset = SceneObjectCopyCore.ReadStableAssetEvidence(
                    snapshot.Target.AssetPath,
                    "created prefab");
                finalEvidenceCaptured = true;
                ownedAsset = finalAsset;
                var finalGuid = finalAsset.Guid;
                var finalReadback = AssetDatabase.LoadAssetAtPath<GameObject>(snapshot.Target.AssetPath);
                if (finalReadback == null)
                {
                    throw new SceneObjectCopyException("The created prefab readback is unavailable.");
                }
                SceneObjectCopyCore.VerifyMovedAssetEvidence(stagedAsset, finalAsset);
                if (finalGuid != createdGuid)
                {
                    throw new SceneObjectCopyException("The created prefab GUID changed during the move.");
                }
                var stagingFileDigest = stagedAsset.File.Digest;
                if (SceneObjectCopyCore.ComputeHierarchyDigest(finalReadback) != stagingHierarchyDigest)
                {
                    throw new SceneObjectCopyException("The created prefab hierarchy readback changed during the move.");
                }
                if (SceneObjectCopyCore.AssetOrMetaExists(staging.PrefabPath))
                {
                    throw new SceneObjectCopyException("The staged prefab remained after the final move.");
                }
                SceneObjectCopyCore.VerifyFolderIdentity(
                    snapshot.Target.ParentFolderPath,
                    snapshot.Target.ParentFolderGuid,
                    snapshot.Target.ParentFolderIdentity,
                    "prefab destination folder");
                SceneObjectCopyCore.VerifyFolderIdentity(
                    snapshot.Target.StagingRootPath,
                    snapshot.Target.StagingRootGuid,
                    snapshot.Target.StagingRootIdentity,
                    "generated staging root");
                SceneObjectCopyCore.VerifySourceUnchanged(snapshot.Source, true);
                if (!SceneObjectCopyCore.DeleteOwnedStagingFolder(staging))
                {
                    throw new SceneObjectCopyException("The prefab staging folder cleanup was not exact.");
                }
                staging = null;

                return SceneObjectCopyCore.Success(new
                {
                    schema = SceneObjectCopyCore.ResultSchema,
                    ok = true,
                    operation = SceneObjectCopyCore.PrefabOperation,
                    preview = false,
                    verified = true,
                    changed = true,
                    saved = true,
                    mutationCount = 1,
                    source = new
                    {
                        scenePath = snapshot.Source.Scene.Path,
                        sceneGuid = snapshot.Source.Scene.Guid,
                        sceneHandle = snapshot.Source.Scene.Handle,
                        objectPath = snapshot.Source.ObjectPath,
                        objectId = snapshot.Source.ObjectId,
                        hierarchyDigestBefore = snapshot.Source.HierarchyDigest,
                        hierarchyDigestAfter = snapshot.Source.HierarchyDigest,
                        sceneFileDigestBefore = snapshot.Source.Scene.FileDigest,
                        sceneFileDigestAfter = snapshot.Source.Scene.FileDigest,
                        sceneFileIdentityBefore = snapshot.Source.Scene.FileIdentity,
                        sceneFileIdentityAfter = snapshot.Source.Scene.FileIdentity,
                        sceneMetaDigest = snapshot.Source.Scene.MetaDigest,
                        sceneMetaIdentity = snapshot.Source.Scene.MetaIdentity,
                        unchanged = true
                    },
                    target = new
                    {
                        assetPath = snapshot.Target.AssetPath,
                        assetGuid = finalGuid,
                        stagingFileDigest = stagingFileDigest,
                        fileDigest = finalAsset.File.Digest,
                        fileIdentity = finalAsset.File.Identity,
                        metaDigest = finalAsset.Meta.Digest,
                        metaIdentity = finalAsset.Meta.Identity,
                        hierarchyDigest = stagingHierarchyDigest,
                        parentFolderPath = snapshot.Target.ParentFolderPath,
                        parentFolderGuid = snapshot.Target.ParentFolderGuid,
                        parentFolderIdentity = snapshot.Target.ParentFolderIdentity,
                        createNew = true,
                        readbackExact = true
                    },
                    previewDigest = snapshot.PreviewDigest,
                    cleanupRequired = false
                });
            }
            catch (Exception exception)
            {
                if (!mutationStarted)
                {
                    return SceneObjectCopyCore.Failure(exception);
                }
                var checkpointRequired = exception is SceneObjectCopyException controlled
                    && controlled.CheckpointRestoreRequired;
                var restored = !checkpointRequired
                    && CleanupFailedApply(
                        snapshot,
                        staging,
                        ownedAsset,
                        finalEvidenceCaptured);
                return SceneObjectCopyCore.BuildMutationFailure(
                    SceneObjectCopyCore.PrefabOperation,
                    restored);
            }
        }

        private static bool CleanupFailedApply(
            PrefabPreviewSnapshot snapshot,
            StagingFolderLease staging,
            StableAssetEvidence createdAsset,
            bool finalEvidenceCaptured)
        {
            try
            {
                if (SceneObjectCopyCore.AssetOrMetaExists(snapshot.Target.AssetPath))
                {
                    if (!finalEvidenceCaptured)
                    {
                        return false;
                    }
                    if (!SceneObjectCopyCore.DeleteOwnedAsset(
                        snapshot.Target.AssetPath,
                        createdAsset))
                    {
                        return false;
                    }
                }
                if (staging != null
                    && SceneObjectCopyCore.AssetOrMetaExists(staging.PrefabPath)
                    && !SceneObjectCopyCore.DeleteOwnedAsset(staging.PrefabPath, createdAsset))
                {
                    return false;
                }
                if (staging != null && !SceneObjectCopyCore.DeleteOwnedStagingFolder(staging))
                {
                    return false;
                }
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                if (SceneObjectCopyCore.AssetOrMetaExists(snapshot.Target.AssetPath))
                {
                    return false;
                }
                SceneObjectCopyCore.VerifySourceUnchanged(snapshot.Source, true);
                return true;
            }
            catch
            {
                return false;
            }
        }
    }
}
