using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32.SafeHandles;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace VRCForge.Editor
{
    internal static class SceneObjectCopyCore
    {
        internal const string ResultSchema = "vrcforge.scene_object_copy.v1";
        internal const string ApprovalSchema = "vrcforge.scene_object_copy_approval.v1";
        internal const string DuplicateOperation = "duplicate_scene_object";
        internal const string PrefabOperation = "save_scene_object_as_prefab";
        internal const string GeneratedRoot = "Assets/VRCForge/Generated";
        internal const string GeneratedPrefix = "Assets/VRCForge/Generated/";
        internal const string RandomStagingPolicy = "random_create_new_folder_v1";

        private const uint FileShareRead = 0x00000001;
        private const uint FileShareWrite = 0x00000002;
        private const uint OpenExisting = 3;
        private const uint FileFlagBackupSemantics = 0x02000000;
        private const uint FileFlagOpenReparsePoint = 0x00200000;

        internal static object Success(object payload)
        {
            return new SuccessResponse("Scene object copy operation completed.", payload);
        }

        internal static object Failure(Exception exception)
        {
            if (exception is SceneObjectCopyException controlled)
            {
                return new ErrorResponse(controlled.Message);
            }
            return new ErrorResponse("Scene object copy operation failed closed.");
        }

        internal static object BuildMutationFailure(string operation, bool restored)
        {
            if (operation != DuplicateOperation && operation != PrefabOperation)
            {
                operation = "scene_object_copy";
            }
            var message = restored
                ? "Scene object copy failed after restoring the verified pre-state."
                : "Scene object copy failed; checkpoint restore is required.";
            return new ErrorResponse(
                message,
                new
                {
                    schema = ResultSchema,
                    operation,
                    mutationStarted = true,
                    restored,
                    cleanupVerified = restored,
                    cleanupRequired = !restored,
                    checkpointRestoreRequired = !restored,
                    operationState = restored ? "restored" : "checkpoint_restore_required"
                });
        }

        internal static DuplicatePreviewSnapshot BuildDuplicatePreview(
            string sourceScenePath,
            string sourceObjectPath,
            string targetScenePath,
            string targetParentPath,
            string targetName,
            bool preserveWorldTransform)
        {
            var sourceSnapshot = BuildSourceSnapshot(sourceScenePath, sourceObjectPath);
            var targetScene = ResolveSavedScene(targetScenePath, "target scene");
            if (sourceSnapshot.Scene.Path == targetScene.Path
                && (sourceSnapshot.Scene.Guid != targetScene.Guid
                    || sourceSnapshot.Scene.Handle != targetScene.Handle
                    || sourceSnapshot.Scene.FileDigest != targetScene.FileDigest
                    || sourceSnapshot.Scene.FileIdentity != targetScene.FileIdentity
                    || sourceSnapshot.Scene.MetaDigest != targetScene.MetaDigest
                    || sourceSnapshot.Scene.MetaIdentity != targetScene.MetaIdentity))
            {
                throw new SceneObjectCopyException("The selected scene changed while it was verified.");
            }
            var targetParent = ResolveUniqueGameObject(targetScene.Scene, targetParentPath, "target parent");
            var source = sourceSnapshot.GameObject;
            var normalizedName = NormalizeObjectName(targetName, "targetName");
            var destinationPath = targetParentPath + "/" + normalizedName;
            var nameCollision = targetParent.transform.Cast<Transform>().Any(child =>
                string.Equals(child.name, normalizedName, StringComparison.OrdinalIgnoreCase));
            var sameDestination = sourceSnapshot.Scene.Path == targetScene.Path
                && sourceSnapshot.ObjectPath == destinationPath;
            var targetWithinSource = sourceSnapshot.Scene.Path == targetScene.Path
                && targetParent.transform.IsChildOf(source.transform);

            if (nameCollision)
            {
                throw new SceneObjectCopyException("The duplicate destination name already exists.");
            }
            if (sameDestination)
            {
                throw new SceneObjectCopyException("The duplicate destination is the source path.");
            }
            if (targetWithinSource)
            {
                throw new SceneObjectCopyException("The duplicate target parent is inside the source hierarchy.");
            }

            var target = new DuplicateTargetSnapshot
            {
                Scene = targetScene,
                Parent = targetParent,
                ParentPath = NormalizeHierarchyPath(targetParentPath, "targetParentPath"),
                ParentObjectId = ComputeSceneObjectId(targetScene, targetParent),
                ParentHierarchyDigest = ComputeHierarchyDigest(targetParent),
                ObjectPath = destinationPath,
                Name = normalizedName,
                NameCollision = false,
                SameDestination = false,
                TargetWithinSource = false
            };
            var snapshot = new DuplicatePreviewSnapshot
            {
                Source = sourceSnapshot,
                Target = target,
                PreserveWorldTransform = preserveWorldTransform
            };
            snapshot.PreviewDigest = ComputePreviewDigest(snapshot);
            return snapshot;
        }

        internal static PrefabPreviewSnapshot BuildPrefabPreview(
            string sourceScenePath,
            string sourceObjectPath,
            string prefabAssetPath)
        {
            var source = BuildSourceSnapshot(sourceScenePath, sourceObjectPath);
            var assetPath = NormalizeGeneratedPrefabPath(prefabAssetPath, "prefabAssetPath", false);
            var parentFolder = assetPath.Substring(0, assetPath.LastIndexOf('/'));
            if (!AssetDatabase.IsValidFolder(parentFolder))
            {
                throw new SceneObjectCopyException("The prefab destination folder does not exist.");
            }
            var parentFolderGuid = NormalizeGuid(
                AssetDatabase.AssetPathToGUID(
                    parentFolder,
                    AssetPathToGUIDOptions.OnlyExistingAssets),
                "prefab destination folder");
            var parentFolderIdentity = ReadDirectoryIdentity(
                parentFolder,
                "prefab destination folder");
            if (!AssetDatabase.IsValidFolder(GeneratedRoot))
            {
                throw new SceneObjectCopyException("The generated staging root does not exist.");
            }
            var stagingRootGuid = NormalizeGuid(
                AssetDatabase.AssetPathToGUID(
                    GeneratedRoot,
                    AssetPathToGUIDOptions.OnlyExistingAssets),
                "generated staging root");
            var stagingRootIdentity = ReadDirectoryIdentity(
                GeneratedRoot,
                "generated staging root");
            var target = new PrefabTargetSnapshot
            {
                AssetPath = assetPath,
                ParentFolderPath = parentFolder,
                ParentFolderGuid = parentFolderGuid,
                ParentFolderIdentity = parentFolderIdentity,
                StagingRootPath = GeneratedRoot,
                StagingRootGuid = stagingRootGuid,
                StagingRootIdentity = stagingRootIdentity,
                StagingPolicy = RandomStagingPolicy,
                AssetExists = AssetPathExists(assetPath),
                MetaExists = SafeSiblingFileExists(assetPath, ".meta")
            };
            if (target.AssetExists || target.MetaExists)
            {
                throw new SceneObjectCopyException("The prefab destination already exists.");
            }
            var snapshot = new PrefabPreviewSnapshot
            {
                Source = source,
                Target = target
            };
            snapshot.PreviewDigest = ComputePreviewDigest(snapshot);
            return snapshot;
        }

        internal static void VerifyDuplicateExpected(
            JObject parameters,
            DuplicatePreviewSnapshot snapshot)
        {
            VerifyProject(parameters);
            RequireFalse(parameters, "overwrite");
            RequireTrue(parameters, "saveScene");
            RequireExpected(parameters, "expectedSourceSceneGuid", snapshot.Source.Scene.Guid);
            RequireExpected(parameters, "expectedSourceSceneHandle", snapshot.Source.Scene.Handle);
            RequireExpected(parameters, "expectedSourceObjectId", snapshot.Source.ObjectId);
            RequireExpected(parameters, "expectedSourceHierarchyDigest", snapshot.Source.HierarchyDigest);
            RequireExpected(parameters, "expectedSourceSceneFileDigest", snapshot.Source.Scene.FileDigest);
            RequireExpected(parameters, "expectedSourceSceneFileIdentity", snapshot.Source.Scene.FileIdentity);
            RequireExpected(parameters, "expectedSourceSceneMetaDigest", snapshot.Source.Scene.MetaDigest);
            RequireExpected(parameters, "expectedSourceSceneMetaIdentity", snapshot.Source.Scene.MetaIdentity);
            RequireExpected(parameters, "expectedTargetSceneGuid", snapshot.Target.Scene.Guid);
            RequireExpected(parameters, "expectedTargetSceneHandle", snapshot.Target.Scene.Handle);
            RequireExpected(parameters, "expectedTargetParentObjectId", snapshot.Target.ParentObjectId);
            RequireExpected(
                parameters,
                "expectedTargetParentHierarchyDigest",
                snapshot.Target.ParentHierarchyDigest);
            RequireExpected(parameters, "expectedTargetSceneFileDigest", snapshot.Target.Scene.FileDigest);
            RequireExpected(parameters, "expectedTargetSceneFileIdentity", snapshot.Target.Scene.FileIdentity);
            RequireExpected(parameters, "expectedTargetSceneMetaDigest", snapshot.Target.Scene.MetaDigest);
            RequireExpected(parameters, "expectedTargetSceneMetaIdentity", snapshot.Target.Scene.MetaIdentity);
            RequireExpected(parameters, "expectedDestinationPath", snapshot.Target.ObjectPath);
            RequireExpected(parameters, "expectedPreviewDigest", snapshot.PreviewDigest);
        }

        internal static void VerifyPrefabExpected(
            JObject parameters,
            PrefabPreviewSnapshot snapshot)
        {
            VerifyProject(parameters);
            RequireFalse(parameters, "overwrite");
            RequireTrue(parameters, "saveAssets");
            RequireExpected(parameters, "expectedSourceSceneGuid", snapshot.Source.Scene.Guid);
            RequireExpected(parameters, "expectedSourceSceneHandle", snapshot.Source.Scene.Handle);
            RequireExpected(parameters, "expectedSourceObjectId", snapshot.Source.ObjectId);
            RequireExpected(parameters, "expectedSourceHierarchyDigest", snapshot.Source.HierarchyDigest);
            RequireExpected(parameters, "expectedSourceSceneFileDigest", snapshot.Source.Scene.FileDigest);
            RequireExpected(parameters, "expectedSourceSceneFileIdentity", snapshot.Source.Scene.FileIdentity);
            RequireExpected(parameters, "expectedSourceSceneMetaDigest", snapshot.Source.Scene.MetaDigest);
            RequireExpected(parameters, "expectedSourceSceneMetaIdentity", snapshot.Source.Scene.MetaIdentity);
            RequireExpected(
                parameters,
                "expectedPrefabParentFolderGuid",
                snapshot.Target.ParentFolderGuid);
            RequireExpected(
                parameters,
                "expectedPrefabParentFolderIdentity",
                snapshot.Target.ParentFolderIdentity);
            RequireExpected(parameters, "expectedStagingRootGuid", snapshot.Target.StagingRootGuid);
            RequireExpected(
                parameters,
                "expectedStagingRootIdentity",
                snapshot.Target.StagingRootIdentity);
            RequireExpected(parameters, "expectedStagingPolicy", snapshot.Target.StagingPolicy);
            RequireExpected(parameters, "expectedPreviewDigest", snapshot.PreviewDigest);
        }

        internal static void VerifySourceUnchanged(
            SourceObjectSnapshot before,
            bool requireSceneFileUnchanged)
        {
            var after = BuildSourceSnapshot(before.Scene.Path, before.ObjectPath);
            if (after.Scene.Guid != before.Scene.Guid
                || after.Scene.Handle != before.Scene.Handle
                || after.ObjectId != before.ObjectId
                || after.HierarchyDigest != before.HierarchyDigest
                || (requireSceneFileUnchanged
                    && (after.Scene.FileDigest != before.Scene.FileDigest
                        || after.Scene.FileIdentity != before.Scene.FileIdentity
                        || after.Scene.MetaDigest != before.Scene.MetaDigest
                        || after.Scene.MetaIdentity != before.Scene.MetaIdentity)))
            {
                throw new SceneObjectCopyException("The source object changed during the operation.");
            }
        }

        internal static SourceObjectSnapshot BuildSourceSnapshot(
            string scenePath,
            string objectPath)
        {
            var scene = ResolveSavedScene(scenePath, "source scene");
            var normalizedPath = NormalizeHierarchyPath(objectPath, "sourceObjectPath");
            var source = ResolveUniqueGameObject(scene.Scene, normalizedPath, "source object");
            return new SourceObjectSnapshot
            {
                Scene = scene,
                GameObject = source,
                ObjectPath = normalizedPath,
                ObjectId = ComputeSceneObjectId(scene, source),
                HierarchyDigest = ComputeHierarchyDigest(source)
            };
        }

        internal static SavedSceneSnapshot ResolveSavedScene(string rawPath, string label)
        {
            var path = NormalizeSceneAssetPath(rawPath, label);
            var matches = new List<Scene>();
            for (var index = 0; index < SceneManager.sceneCount; index++)
            {
                var scene = SceneManager.GetSceneAt(index);
                if (scene.IsValid()
                    && scene.isLoaded
                    && string.Equals(NormalizeSlashes(scene.path), path, StringComparison.Ordinal))
                {
                    matches.Add(scene);
                }
            }
            if (matches.Count != 1)
            {
                throw new SceneObjectCopyException("The saved scene selector is missing or ambiguous.");
            }
            var sceneMatch = matches[0];
            if (sceneMatch.isDirty)
            {
                throw new SceneObjectCopyException("The selected scene has unsaved changes.");
            }
            var evidence = ReadStableAssetEvidence(path, label);
            return new SavedSceneSnapshot
            {
                Scene = sceneMatch,
                Path = path,
                Guid = evidence.Guid,
                Handle = sceneMatch.handle,
                FileDigest = evidence.File.Digest,
                FileIdentity = evidence.File.Identity,
                MetaDigest = evidence.Meta.Digest,
                MetaIdentity = evidence.Meta.Identity
            };
        }

        internal static GameObject ResolveUniqueGameObject(Scene scene, string rawPath, string label)
        {
            var path = NormalizeHierarchyPath(rawPath, label);
            var segments = path.Split('/');
            var roots = scene.GetRootGameObjects()
                .Where(item => item != null && string.Equals(item.name, segments[0], StringComparison.Ordinal))
                .ToList();
            if (roots.Count != 1)
            {
                throw new SceneObjectCopyException("The hierarchy path is missing or ambiguous.");
            }
            var current = roots[0].transform;
            for (var index = 1; index < segments.Length; index++)
            {
                var matches = current.Cast<Transform>()
                    .Where(item => string.Equals(item.name, segments[index], StringComparison.Ordinal))
                    .ToList();
                if (matches.Count != 1)
                {
                    throw new SceneObjectCopyException("The hierarchy path is missing or ambiguous.");
                }
                current = matches[0];
            }
            if (current.gameObject.scene.handle != scene.handle)
            {
                throw new SceneObjectCopyException("The hierarchy path crossed the selected scene.");
            }
            return current.gameObject;
        }

        internal static string ComputeHierarchyDigest(GameObject root)
        {
            if (root == null)
            {
                throw new SceneObjectCopyException("The hierarchy root is unavailable.");
            }
            var value = new StringBuilder();
            AppendDigestField(value, "vrcforge.scene_object_hierarchy.v1");
            AppendHierarchy(value, root.transform, root.transform, 0);
            return Sha256(value.ToString());
        }

        internal static StableAssetEvidence ReadStableAssetEvidence(
            string assetPath,
            string label,
            Action<string, string> whileHandlesHeldProbe = null)
        {
            var absolutePath = ToAbsoluteAssetPath(assetPath);
            var metaPath = absolutePath + ".meta";
            RejectProjectPathReparsePoints(metaPath);
            if (!File.Exists(absolutePath) || !File.Exists(metaPath))
            {
                throw new SceneObjectCopyException("A required project asset is incomplete.");
            }
            try
            {
                using (var fileStream = new FileStream(
                    absolutePath,
                    FileMode.Open,
                    FileAccess.Read,
                    FileShare.Read))
                using (var metaStream = new FileStream(
                    metaPath,
                    FileMode.Open,
                    FileAccess.Read,
                    FileShare.Read))
                {
                    RejectProjectPathReparsePoints(absolutePath);
                    RejectProjectPathReparsePoints(metaPath);
                    var fileBefore = ReadFileHandleEvidence(fileStream, absolutePath, label);
                    var metaBefore = ReadFileHandleEvidence(metaStream, metaPath, label + " metadata");
                    whileHandlesHeldProbe?.Invoke(absolutePath, metaPath);

                    fileStream.Position = 0;
                    string fileDigest;
                    using (var sha256 = SHA256.Create())
                    {
                        fileDigest = Hex(sha256.ComputeHash(fileStream));
                    }
                    metaStream.Position = 0;
                    var metaBytes = ReadBoundedStream(metaStream, 1024 * 1024, label + " metadata");
                    string metaDigest;
                    using (var sha256 = SHA256.Create())
                    {
                        metaDigest = Hex(sha256.ComputeHash(metaBytes));
                    }
                    var metaText = Encoding.UTF8.GetString(metaBytes);
                    var metaGuid = ParseMetaGuid(metaText, label);
                    var databaseGuid = ReadAssetGuid(assetPath, label);
                    if (metaGuid != databaseGuid)
                    {
                        throw new SceneObjectCopyException("A required project asset GUID is inconsistent.");
                    }

                    var fileAfter = ReadFileHandleEvidence(fileStream, absolutePath, label);
                    var metaAfter = ReadFileHandleEvidence(metaStream, metaPath, label + " metadata");
                    RejectProjectPathReparsePoints(absolutePath);
                    RejectProjectPathReparsePoints(metaPath);
                    if (!FileHandleEvidenceMatches(fileBefore, fileAfter)
                        || !FileHandleEvidenceMatches(metaBefore, metaAfter))
                    {
                        throw new SceneObjectCopyException(
                            "A required project asset changed while its evidence was read.");
                    }
                    return new StableAssetEvidence
                    {
                        Guid = databaseGuid,
                        File = BuildStableFileEvidence(fileAfter, fileDigest),
                        Meta = BuildStableFileEvidence(metaAfter, metaDigest)
                    };
                }
            }
            catch (SceneObjectCopyException)
            {
                throw;
            }
            catch
            {
                throw new SceneObjectCopyException(
                    "A required project asset could not be locked for a stable read.");
            }
        }

        internal static bool StableAssetEvidenceMatches(
            StableAssetEvidence left,
            StableAssetEvidence right,
            bool requireFileDigest)
        {
            return left != null
                && right != null
                && left.Guid == right.Guid
                && left.File != null
                && right.File != null
                && left.File.Identity == right.File.Identity
                && left.File.LinkCount == right.File.LinkCount
                && (!requireFileDigest || left.File.Digest == right.File.Digest)
                && left.Meta != null
                && right.Meta != null
                && StableFileEvidenceMatches(left.Meta, right.Meta);
        }

        internal static void VerifyMovedAssetEvidence(
            StableAssetEvidence staged,
            StableAssetEvidence final)
        {
            if (staged == null
                || final == null
                || staged.File == null
                || final.File == null
                || staged.Meta == null
                || final.Meta == null)
            {
                throw new SceneObjectCopyException("The moved project asset evidence is incomplete.");
            }
            if (staged.Guid != final.Guid)
            {
                throw new SceneObjectCopyException("The moved project asset GUID changed.");
            }
            if (staged.File.Digest != final.File.Digest)
            {
                throw new SceneObjectCopyException("The moved project asset bytes changed.");
            }
            if (staged.Meta.Digest != final.Meta.Digest)
            {
                throw new SceneObjectCopyException("The moved project asset metadata changed.");
            }
            if (staged.File.LinkCount != 1
                || final.File.LinkCount != 1
                || staged.Meta.LinkCount != 1
                || final.Meta.LinkCount != 1)
            {
                throw new SceneObjectCopyException("The moved project asset is not single-link.");
            }
        }

        internal static bool DeleteOwnedAsset(
            string assetPath,
            StableAssetEvidence expected)
        {
            try
            {
                if (expected == null)
                {
                    return false;
                }
                var current = ReadStableAssetEvidence(assetPath, "owned project asset cleanup");
                if (!StableAssetEvidenceMatches(expected, current, true))
                {
                    return false;
                }
                if (!AssetDatabase.DeleteAsset(assetPath))
                {
                    return false;
                }
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                return !AssetOrMetaExists(assetPath);
            }
            catch
            {
                return false;
            }
        }

        internal static string ToAbsoluteAssetPath(string assetPath)
        {
            var normalized = NormalizeAssetPath(assetPath, "asset path");
            var projectRoot = Directory.GetParent(Application.dataPath)?.FullName;
            if (string.IsNullOrWhiteSpace(projectRoot))
            {
                throw new SceneObjectCopyException("The Unity project root is unavailable.");
            }
            var absolute = Path.GetFullPath(Path.Combine(
                projectRoot,
                normalized.Replace('/', Path.DirectorySeparatorChar)));
            var assetsRoot = Path.GetFullPath(Application.dataPath)
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            var comparison = Application.platform == RuntimePlatform.WindowsEditor
                ? StringComparison.OrdinalIgnoreCase
                : StringComparison.Ordinal;
            if (!absolute.StartsWith(assetsRoot + Path.DirectorySeparatorChar, comparison))
            {
                throw new SceneObjectCopyException("The asset path escaped the project Assets root.");
            }
            RejectProjectPathReparsePoints(absolute);
            return absolute;
        }

        internal static bool AssetPathExists(string assetPath)
        {
            return AssetDatabase.LoadMainAssetAtPath(assetPath) != null
                || File.Exists(ToAbsoluteAssetPath(assetPath));
        }

        internal static bool AssetOrMetaExists(string assetPath)
        {
            return AssetPathExists(assetPath) || SafeSiblingFileExists(assetPath, ".meta");
        }

        internal static bool SafeSiblingFileExists(string assetPath, string suffix)
        {
            if (suffix != ".meta")
            {
                throw new SceneObjectCopyException("The requested project sidecar is not allowed.");
            }
            var absolutePath = ToAbsoluteAssetPath(assetPath) + suffix;
            RejectProjectPathReparsePoints(absolutePath);
            return File.Exists(absolutePath);
        }

        internal static string ReadAssetGuid(string assetPath, string label)
        {
            ToAbsoluteAssetPath(assetPath);
            return NormalizeGuid(
                AssetDatabase.AssetPathToGUID(
                    assetPath,
                    AssetPathToGUIDOptions.OnlyExistingAssets),
                label);
        }

        internal static string ReadDirectoryIdentity(string assetPath, string label)
        {
            var normalized = NormalizeAssetPath(assetPath, label);
            if (!AssetDatabase.IsValidFolder(normalized))
            {
                throw new SceneObjectCopyException("A required project folder is unavailable.");
            }
            var absolutePath = ToAbsoluteAssetPath(normalized);
            if (!Directory.Exists(absolutePath))
            {
                throw new SceneObjectCopyException("A required project folder is unavailable.");
            }
            RejectProjectPathReparsePoints(absolutePath);
            if (Application.platform == RuntimePlatform.WindowsEditor)
            {
                using (var handle = CreateFileW(
                    absolutePath,
                    0,
                    FileShareRead | FileShareWrite,
                    IntPtr.Zero,
                    OpenExisting,
                    FileFlagBackupSemantics | FileFlagOpenReparsePoint,
                    IntPtr.Zero))
                {
                    if (handle == null || handle.IsInvalid)
                    {
                        throw new SceneObjectCopyException("A required project folder identity is unavailable.");
                    }
                    var information = ReadNativeHandleInformation(handle, label);
                    RejectProjectPathReparsePoints(absolutePath);
                    return NativeIdentity(information);
                }
            }
            var directory = new DirectoryInfo(absolutePath);
            return Sha256(string.Join(
                "|",
                "vrcforge.directory_identity.portable.v1",
                directory.CreationTimeUtc.Ticks.ToString(CultureInfo.InvariantCulture)));
        }

        internal static void VerifyFolderIdentity(
            string assetPath,
            string expectedGuid,
            string expectedIdentity,
            string label)
        {
            if (ReadAssetGuid(assetPath, label) != expectedGuid
                || ReadDirectoryIdentity(assetPath, label) != expectedIdentity)
            {
                throw new SceneObjectCopyException("A required project folder identity changed.");
            }
        }

        internal static StagingFolderLease CreateRandomStagingFolder(
            PrefabTargetSnapshot target)
        {
            var mutationStarted = false;
            return CreateRandomStagingFolder(target, ref mutationStarted);
        }

        internal static StagingFolderLease CreateRandomStagingFolder(
            PrefabTargetSnapshot target,
            ref bool mutationStarted)
        {
            if (target.StagingPolicy != RandomStagingPolicy)
            {
                throw new SceneObjectCopyException("The prefab staging policy is invalid.");
            }
            VerifyFolderIdentity(
                target.StagingRootPath,
                target.StagingRootGuid,
                target.StagingRootIdentity,
                "generated staging root");
            byte[] randomBytes = new byte[16];
            using (var random = RandomNumberGenerator.Create())
            {
                random.GetBytes(randomBytes);
            }
            var folderName = "stage-" + Hex(randomBytes);
            var folderPath = target.StagingRootPath + "/" + folderName;
            var absolutePath = ToAbsoluteAssetPath(folderPath);
            if (Directory.Exists(absolutePath)
                || File.Exists(absolutePath)
                || SafeSiblingFileExists(folderPath, ".meta")
                || !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(
                    folderPath,
                    AssetPathToGUIDOptions.OnlyExistingAssets)))
            {
                throw new SceneObjectCopyException("The random prefab staging folder already exists.");
            }

            StagingFolderLease lease = null;
            try
            {
                var createdGuid = AssetDatabase.CreateFolder(target.StagingRootPath, folderName);
                mutationStarted = true;
                var normalizedGuid = NormalizeGuid(createdGuid, "created staging folder");
                var actualPath = NormalizeSlashes(AssetDatabase.GUIDToAssetPath(normalizedGuid));
                if (!string.Equals(actualPath, folderPath, StringComparison.Ordinal))
                {
                    throw new SceneObjectCopyException(
                        "The random prefab staging folder was not created exactly.");
                }
                var identity = ReadDirectoryIdentity(folderPath, "created staging folder");
                var finalFileName = target.AssetPath.Substring(target.AssetPath.LastIndexOf('/') + 1);
                lease = new StagingFolderLease
                {
                    RootPath = target.StagingRootPath,
                    FolderPath = folderPath,
                    FolderGuid = normalizedGuid,
                    FolderIdentity = identity,
                    PrefabPath = folderPath + "/" + finalFileName
                };
                VerifyFolderIdentity(
                    target.StagingRootPath,
                    target.StagingRootGuid,
                    target.StagingRootIdentity,
                    "generated staging root");
                if (AssetOrMetaExists(lease.PrefabPath))
                {
                    throw new SceneObjectCopyException("The random prefab staging folder is not empty.");
                }
                return lease;
            }
            catch (Exception exception)
            {
                if (lease != null && DeleteOwnedStagingFolder(lease))
                {
                    throw;
                }
                var reason = exception is SceneObjectCopyException
                    ? exception.Message
                    : "an internal verification error occurred";
                throw new SceneObjectCopyException(
                    "The prefab staging folder creation outcome could not be cleaned exactly ("
                    + reason
                    + "); checkpoint restore is required.",
                    true);
            }
        }

        internal static void VerifyOwnedStagingFolder(StagingFolderLease lease)
        {
            if (lease == null)
            {
                throw new SceneObjectCopyException("The prefab staging lease is unavailable.");
            }
            VerifyFolderIdentity(
                lease.FolderPath,
                lease.FolderGuid,
                lease.FolderIdentity,
                "created staging folder");
        }

        internal static bool DeleteOwnedStagingFolder(StagingFolderLease lease)
        {
            try
            {
                if (lease == null)
                {
                    return false;
                }
                var initialAbsoluteFolder = ToAbsoluteAssetPath(lease.FolderPath);
                if (!Directory.Exists(initialAbsoluteFolder)
                    && !SafeSiblingFileExists(lease.FolderPath, ".meta")
                    && string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(
                        lease.FolderPath,
                        AssetPathToGUIDOptions.OnlyExistingAssets)))
                {
                    return true;
                }
                VerifyOwnedStagingFolder(lease);
                var absoluteFolder = initialAbsoluteFolder;
                foreach (var entry in Directory.EnumerateFileSystemEntries(absoluteFolder))
                {
                    var fullEntry = Path.GetFullPath(entry);
                    RejectProjectPathReparsePoints(fullEntry);
                    return false;
                }
                if (!AssetDatabase.DeleteAsset(lease.FolderPath))
                {
                    return false;
                }
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                var cleanupResult = !Directory.Exists(ToAbsoluteAssetPath(lease.FolderPath))
                    && !SafeSiblingFileExists(lease.FolderPath, ".meta")
                    && string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(
                        lease.FolderPath,
                        AssetPathToGUIDOptions.OnlyExistingAssets));
                return cleanupResult;
            }
            catch
            {
                return false;
            }
        }

        internal static string NormalizeSceneAssetPath(string value, string label)
        {
            var path = NormalizeAssetPath(value, label);
            if (!path.StartsWith("Assets/", StringComparison.Ordinal)
                || !path.EndsWith(".unity", StringComparison.OrdinalIgnoreCase))
            {
                throw new SceneObjectCopyException("The scene path must be a saved Assets scene.");
            }
            return path;
        }

        internal static string NormalizeGeneratedPrefabPath(
            string value,
            string label,
            bool requireStagingName)
        {
            var path = NormalizeAssetPath(value, label);
            if (!path.StartsWith(GeneratedPrefix, StringComparison.Ordinal)
                || !path.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase))
            {
                throw new SceneObjectCopyException("The prefab path must be below the generated asset root.");
            }
            var fileName = path.Substring(path.LastIndexOf('/') + 1);
            var isStaging = fileName.StartsWith(".", StringComparison.Ordinal)
                && fileName.Contains(".vrcforge-stage-");
            if (requireStagingName != isStaging)
            {
                throw new SceneObjectCopyException("The prefab path uses a reserved filename.");
            }
            return path;
        }

        internal static string NormalizeHierarchyPath(string value, string label)
        {
            var path = RequireCanonicalText(value, label, 2048);
            if (path.StartsWith("/", StringComparison.Ordinal)
                || path.EndsWith("/", StringComparison.Ordinal)
                || path.Contains("\\"))
            {
                throw new SceneObjectCopyException("The hierarchy path is not canonical.");
            }
            var parts = path.Split('/');
            if (parts.Any(part => string.IsNullOrEmpty(part)
                || part == "."
                || part == ".."
                || part.Any(character => char.IsControl(character))))
            {
                throw new SceneObjectCopyException("The hierarchy path contains an unsafe segment.");
            }
            return path;
        }

        internal static string NormalizeObjectName(string value, string label)
        {
            var name = RequireCanonicalText(value, label, 256);
            if (name != name.Trim()
                || name == "."
                || name == ".."
                || name.Contains("/")
                || name.Contains("\\")
                || name.Any(character => char.IsControl(character)))
            {
                throw new SceneObjectCopyException("The target object name is not canonical.");
            }
            return name;
        }

        internal static bool MatchesCurrentProject(string expectedProjectPath)
        {
            if (string.IsNullOrWhiteSpace(expectedProjectPath) || !Path.IsPathRooted(expectedProjectPath))
            {
                return false;
            }
            var expected = Path.GetFullPath(expectedProjectPath)
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            var current = Path.GetFullPath(Directory.GetParent(Application.dataPath)?.FullName ?? string.Empty)
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            var comparison = Application.platform == RuntimePlatform.WindowsEditor
                ? StringComparison.OrdinalIgnoreCase
                : StringComparison.Ordinal;
            return string.Equals(expected, current, comparison);
        }

        internal static string ComputePreviewDigest(DuplicatePreviewSnapshot snapshot)
        {
            var value = PreviewDigestPrefix(DuplicateOperation, snapshot.Source);
            AppendDigestField(value, snapshot.Target.Scene.Path);
            AppendDigestField(value, snapshot.Target.Scene.Guid);
            AppendDigestField(value, snapshot.Target.Scene.Handle.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(value, snapshot.Target.ParentPath);
            AppendDigestField(value, snapshot.Target.ParentObjectId);
            AppendDigestField(value, snapshot.Target.ParentHierarchyDigest);
            AppendDigestField(value, snapshot.Target.Scene.FileDigest);
            AppendDigestField(value, snapshot.Target.Scene.FileIdentity);
            AppendDigestField(value, snapshot.Target.Scene.MetaDigest);
            AppendDigestField(value, snapshot.Target.Scene.MetaIdentity);
            AppendDigestField(value, snapshot.Target.ObjectPath);
            AppendDigestField(value, snapshot.Target.Name);
            AppendDigestField(value, "true");
            AppendDigestField(value, "false");
            AppendDigestField(value, "false");
            AppendDigestField(value, "false");
            AppendDigestField(value, snapshot.PreserveWorldTransform ? "true" : "false");
            return Sha256(value.ToString());
        }

        internal static string ComputePreviewDigest(PrefabPreviewSnapshot snapshot)
        {
            var value = PreviewDigestPrefix(PrefabOperation, snapshot.Source);
            AppendDigestField(value, snapshot.Target.AssetPath);
            AppendDigestField(value, snapshot.Target.ParentFolderPath);
            AppendDigestField(value, snapshot.Target.ParentFolderGuid);
            AppendDigestField(value, snapshot.Target.ParentFolderIdentity);
            AppendDigestField(value, snapshot.Target.StagingRootPath);
            AppendDigestField(value, snapshot.Target.StagingRootGuid);
            AppendDigestField(value, snapshot.Target.StagingRootIdentity);
            AppendDigestField(value, snapshot.Target.StagingPolicy);
            AppendDigestField(value, "false");
            AppendDigestField(value, "false");
            AppendDigestField(value, "true");
            return Sha256(value.ToString());
        }

        internal static string GetHierarchyPath(Transform transform)
        {
            if (transform == null)
            {
                throw new SceneObjectCopyException("The hierarchy transform is unavailable.");
            }
            var segments = new Stack<string>();
            var current = transform;
            while (current != null)
            {
                segments.Push(current.name);
                current = current.parent;
            }
            return string.Join("/", segments);
        }

        private static StringBuilder PreviewDigestPrefix(
            string operation,
            SourceObjectSnapshot source)
        {
            var value = new StringBuilder();
            AppendDigestField(value, ResultSchema);
            AppendDigestField(value, operation);
            AppendDigestField(value, "true");
            AppendDigestField(value, "true");
            AppendDigestField(value, "true");
            AppendDigestField(value, "false");
            AppendDigestField(value, "false");
            AppendDigestField(value, "0");
            AppendDigestField(value, source.Scene.Path);
            AppendDigestField(value, source.Scene.Guid);
            AppendDigestField(value, source.Scene.Handle.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(value, source.ObjectPath);
            AppendDigestField(value, source.ObjectId);
            AppendDigestField(value, source.HierarchyDigest);
            AppendDigestField(value, source.Scene.FileDigest);
            AppendDigestField(value, source.Scene.FileIdentity);
            AppendDigestField(value, source.Scene.MetaDigest);
            AppendDigestField(value, source.Scene.MetaIdentity);
            AppendDigestField(value, "true");
            return value;
        }

        private static string ComputeSceneObjectId(SavedSceneSnapshot scene, GameObject gameObject)
        {
            var globalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(gameObject);
            if (globalObjectId.identifierType == 0)
            {
                throw new SceneObjectCopyException("A stable scene object identity is unavailable.");
            }
            var value = new StringBuilder();
            AppendDigestField(value, "vrcforge.scene_object_identity.v1");
            AppendDigestField(value, scene.Guid);
            AppendDigestField(value, globalObjectId.ToString());
            AppendDigestField(value, GetHierarchyPath(gameObject.transform));
            return Sha256(value.ToString());
        }

        private static void AppendHierarchy(
            StringBuilder target,
            Transform root,
            Transform current,
            int depth)
        {
            AppendDigestField(target, depth.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(target, GetRelativePath(root, current));
            AppendDigestField(target, current.GetSiblingIndex().ToString(CultureInfo.InvariantCulture));
            AppendDigestField(target, current.gameObject.activeSelf ? "true" : "false");
            AppendDigestField(target, current.gameObject.layer.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(target, current.gameObject.tag ?? string.Empty);
            AppendDigestField(target, current.gameObject.isStatic ? "true" : "false");
            AppendDigestField(target, ((int)current.gameObject.hideFlags).ToString(CultureInfo.InvariantCulture));
            AppendVector3(target, current.localPosition);
            AppendQuaternion(target, current.localRotation);
            AppendVector3(target, current.localScale);

            var components = current.GetComponents<Component>();
            AppendDigestField(target, components.Length.ToString(CultureInfo.InvariantCulture));
            for (var index = 0; index < components.Length; index++)
            {
                var component = components[index];
                if (component == null)
                {
                    throw new SceneObjectCopyException("The source hierarchy contains a missing component.");
                }
                AppendDigestField(target, index.ToString(CultureInfo.InvariantCulture));
                AppendDigestField(target, component.GetType().AssemblyQualifiedName ?? component.GetType().FullName);
                string json;
                try
                {
                    json = EditorJsonUtility.ToJson(component, false)
                        .Replace("\r\n", "\n")
                        .Replace("\r", "\n");
                }
                catch
                {
                    throw new SceneObjectCopyException("A hierarchy component could not be read deterministically.");
                }
                AppendDigestField(target, json);
            }

            AppendDigestField(target, current.childCount.ToString(CultureInfo.InvariantCulture));
            for (var index = 0; index < current.childCount; index++)
            {
                AppendHierarchy(target, root, current.GetChild(index), depth + 1);
            }
        }

        private static string GetRelativePath(Transform root, Transform current)
        {
            if (ReferenceEquals(root, current))
            {
                return root.name;
            }
            var segments = new Stack<string>();
            var cursor = current;
            while (cursor != null && !ReferenceEquals(cursor, root))
            {
                segments.Push(cursor.name);
                cursor = cursor.parent;
            }
            if (!ReferenceEquals(cursor, root))
            {
                throw new SceneObjectCopyException("A hierarchy child escaped its source root.");
            }
            return root.name + "/" + string.Join("/", segments);
        }

        private static void AppendVector3(StringBuilder target, Vector3 value)
        {
            AppendDigestField(target, value.x.ToString("R", CultureInfo.InvariantCulture));
            AppendDigestField(target, value.y.ToString("R", CultureInfo.InvariantCulture));
            AppendDigestField(target, value.z.ToString("R", CultureInfo.InvariantCulture));
        }

        private static void AppendQuaternion(StringBuilder target, Quaternion value)
        {
            AppendDigestField(target, value.x.ToString("R", CultureInfo.InvariantCulture));
            AppendDigestField(target, value.y.ToString("R", CultureInfo.InvariantCulture));
            AppendDigestField(target, value.z.ToString("R", CultureInfo.InvariantCulture));
            AppendDigestField(target, value.w.ToString("R", CultureInfo.InvariantCulture));
        }

        private static string NormalizeAssetPath(string value, string label)
        {
            var path = RequireCanonicalText(value, label, 1024);
            if (path.StartsWith("/", StringComparison.Ordinal)
                || path.EndsWith("/", StringComparison.Ordinal)
                || path.Contains("\\"))
            {
                throw new SceneObjectCopyException("The project asset path is not canonical.");
            }
            var parts = path.Split('/');
            if (parts.Any(part => string.IsNullOrEmpty(part)
                || part == "."
                || part == ".."
                || part.Any(character => char.IsControl(character))))
            {
                throw new SceneObjectCopyException("The project asset path contains an unsafe segment.");
            }
            return path;
        }

        private static string RequireCanonicalText(string value, string label, int maxLength)
        {
            if (string.IsNullOrEmpty(value)
                || value.Length > maxLength
                || value.IndexOf('\0') >= 0)
            {
                throw new SceneObjectCopyException(label + " is invalid.");
            }
            return value;
        }

        private static string NormalizeGuid(string value, string label)
        {
            var normalized = NormalizeHex(value, 32, label);
            if (normalized.All(character => character == '0'))
            {
                throw new SceneObjectCopyException("A required project GUID is unavailable.");
            }
            return normalized;
        }

        private static string NormalizeHex(string value, int length, string label)
        {
            var normalized = (value ?? string.Empty).Trim();
            if (normalized.Length != length
                || normalized.Any(character => !Uri.IsHexDigit(character))
                || !string.Equals(normalized, normalized.ToLowerInvariant(), StringComparison.Ordinal))
            {
                throw new SceneObjectCopyException(label + " is invalid.");
            }
            return normalized;
        }

        private static void VerifyProject(JObject parameters)
        {
            var expectedProjectPath = parameters?["expectedProjectPath"]?.ToString() ?? string.Empty;
            if (!MatchesCurrentProject(expectedProjectPath))
            {
                throw new SceneObjectCopyException("The Unity project no longer matches the verified preview.");
            }
        }

        private static void RequireTrue(JObject parameters, string key)
        {
            if (parameters?[key]?.Type != JTokenType.Boolean || parameters[key].Value<bool>() != true)
            {
                throw new SceneObjectCopyException(key + " must be true for apply.");
            }
        }

        private static void RequireFalse(JObject parameters, string key)
        {
            if (parameters?[key]?.Type != JTokenType.Boolean || parameters[key].Value<bool>() != false)
            {
                throw new SceneObjectCopyException(key + " must be false.");
            }
        }

        private static void RequireExpected(JObject parameters, string key, string expected)
        {
            var value = parameters?[key]?.ToString();
            if (string.IsNullOrEmpty(value) || !string.Equals(value, expected, StringComparison.Ordinal))
            {
                throw new SceneObjectCopyException("The current state no longer matches the verified preview.");
            }
        }

        private static void RequireExpected(JObject parameters, string key, int expected)
        {
            if (parameters?[key]?.Type != JTokenType.Integer || parameters[key].Value<int>() != expected)
            {
                throw new SceneObjectCopyException("The current state no longer matches the verified preview.");
            }
        }

        private static void AppendDigestField(StringBuilder target, string value)
        {
            var safeValue = value ?? string.Empty;
            target.Append(safeValue.Length).Append(':').Append(safeValue);
        }

        private static string Sha256(string value)
        {
            using (var sha256 = SHA256.Create())
            {
                return Hex(sha256.ComputeHash(Encoding.UTF8.GetBytes(value ?? string.Empty)));
            }
        }

        private static string Hex(byte[] bytes)
        {
            return BitConverter.ToString(bytes).Replace("-", string.Empty).ToLowerInvariant();
        }

        private static FileHandleEvidence ReadFileHandleEvidence(
            FileStream stream,
            string absolutePath,
            string label)
        {
            if (Application.platform == RuntimePlatform.WindowsEditor)
            {
                var information = ReadNativeHandleInformation(stream.SafeFileHandle, label);
                if (information.NumberOfLinks != 1)
                {
                    throw new SceneObjectCopyException(
                        "A required project file is not a single-link file.");
                }
                return new FileHandleEvidence
                {
                    Identity = NativeIdentity(information),
                    Length = CombineHighLow(information.FileSizeHigh, information.FileSizeLow),
                    LinkCount = information.NumberOfLinks
                };
            }

            var file = new FileInfo(absolutePath);
            return new FileHandleEvidence
            {
                Identity = Sha256(string.Join(
                    "|",
                    "vrcforge.file_identity.portable.v1",
                    file.CreationTimeUtc.Ticks.ToString(CultureInfo.InvariantCulture),
                    stream.Length.ToString(CultureInfo.InvariantCulture))),
                Length = (ulong)stream.Length,
                LinkCount = 1
            };
        }

        private static ByHandleFileInformation ReadNativeHandleInformation(
            SafeFileHandle handle,
            string label)
        {
            ByHandleFileInformation information;
            if (handle == null
                || handle.IsInvalid
                || !GetFileInformationByHandle(handle, out information))
            {
                throw new SceneObjectCopyException(label + " identity is unavailable.");
            }
            return information;
        }

        private static string NativeIdentity(ByHandleFileInformation information)
        {
            var value = new StringBuilder();
            AppendDigestField(value, "vrcforge.windows_file_identity.v1");
            AppendDigestField(
                value,
                information.VolumeSerialNumber.ToString("x8", CultureInfo.InvariantCulture));
            AppendDigestField(
                value,
                information.FileIndexHigh.ToString("x8", CultureInfo.InvariantCulture));
            AppendDigestField(
                value,
                information.FileIndexLow.ToString("x8", CultureInfo.InvariantCulture));
            return Sha256(value.ToString());
        }

        private static bool StableFileEvidenceMatches(
            StableFileEvidence left,
            StableFileEvidence right)
        {
            return left != null
                && right != null
                && left.Digest == right.Digest
                && left.Identity == right.Identity
                && left.LinkCount == right.LinkCount
                && left.Length == right.Length;
        }

        private static bool FileHandleEvidenceMatches(
            FileHandleEvidence left,
            FileHandleEvidence right)
        {
            return left != null
                && right != null
                && left.Identity == right.Identity
                && left.LinkCount == right.LinkCount
                && left.Length == right.Length;
        }

        private static StableFileEvidence BuildStableFileEvidence(
            FileHandleEvidence handle,
            string digest)
        {
            return new StableFileEvidence
            {
                Digest = digest,
                Identity = handle.Identity,
                LinkCount = handle.LinkCount,
                Length = handle.Length
            };
        }

        private static byte[] ReadBoundedStream(
            FileStream stream,
            int maxBytes,
            string label)
        {
            if (stream.Length < 0 || stream.Length > maxBytes)
            {
                throw new SceneObjectCopyException(label + " is outside its allowed size.");
            }
            var length = checked((int)stream.Length);
            var bytes = new byte[length];
            var offset = 0;
            while (offset < length)
            {
                var read = stream.Read(bytes, offset, length - offset);
                if (read <= 0)
                {
                    throw new SceneObjectCopyException(label + " could not be read completely.");
                }
                offset += read;
            }
            return bytes;
        }

        private static string ParseMetaGuid(string metaText, string label)
        {
            var matches = (metaText ?? string.Empty)
                .Replace("\r\n", "\n")
                .Replace("\r", "\n")
                .Split('\n')
                .Select(line => line.Trim())
                .Where(line => line.StartsWith("guid:", StringComparison.Ordinal))
                .Select(line => line.Substring("guid:".Length).Trim())
                .ToList();
            if (matches.Count != 1)
            {
                throw new SceneObjectCopyException(label + " GUID metadata is invalid.");
            }
            return NormalizeGuid(matches[0], label + " GUID metadata");
        }

        private static ulong CombineHighLow(uint high, uint low)
        {
            return ((ulong)high << 32) | low;
        }

        private static void RejectProjectPathReparsePoints(string absolutePath)
        {
            var projectRoot = Directory.GetParent(Application.dataPath)?.FullName;
            if (string.IsNullOrWhiteSpace(projectRoot))
            {
                throw new SceneObjectCopyException("The Unity project root is unavailable.");
            }
            var root = Path.GetFullPath(projectRoot)
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            var target = Path.GetFullPath(absolutePath)
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            var comparison = Application.platform == RuntimePlatform.WindowsEditor
                ? StringComparison.OrdinalIgnoreCase
                : StringComparison.Ordinal;
            if (!string.Equals(target, root, comparison)
                && !target.StartsWith(root + Path.DirectorySeparatorChar, comparison))
            {
                throw new SceneObjectCopyException("The project path escaped its allowed root.");
            }

            RejectReparsePointIfPresent(root);
            if (string.Equals(target, root, comparison))
            {
                return;
            }
            var relative = target.Substring(root.Length)
                .TrimStart(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            var current = root;
            foreach (var segment in relative.Split(new[]
            {
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar
            }, StringSplitOptions.RemoveEmptyEntries))
            {
                current = Path.Combine(current, segment);
                if (!Directory.Exists(current) && !File.Exists(current))
                {
                    break;
                }
                RejectReparsePointIfPresent(current);
            }
        }

        private static void RejectReparsePointIfPresent(string path)
        {
            try
            {
                if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                {
                    throw new SceneObjectCopyException(
                        "A project path contains a reparse point and cannot be written safely.");
                }
            }
            catch (SceneObjectCopyException)
            {
                throw;
            }
            catch
            {
                throw new SceneObjectCopyException("A project path could not be verified.");
            }
        }

        private static string NormalizeSlashes(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/");
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFileW(
            string fileName,
            uint desiredAccess,
            uint shareMode,
            IntPtr securityAttributes,
            uint creationDisposition,
            uint flagsAndAttributes,
            IntPtr templateFile);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetFileInformationByHandle(
            SafeFileHandle file,
            out ByHandleFileInformation information);

        [StructLayout(LayoutKind.Sequential)]
        private struct ByHandleFileInformation
        {
            internal uint FileAttributes;
            internal NativeFileTime CreationTime;
            internal NativeFileTime LastAccessTime;
            internal NativeFileTime LastWriteTime;
            internal uint VolumeSerialNumber;
            internal uint FileSizeHigh;
            internal uint FileSizeLow;
            internal uint NumberOfLinks;
            internal uint FileIndexHigh;
            internal uint FileIndexLow;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct NativeFileTime
        {
            internal uint LowDateTime;
            internal uint HighDateTime;
        }
    }

    internal sealed class SavedSceneSnapshot
    {
        internal Scene Scene;
        internal string Path = string.Empty;
        internal string Guid = string.Empty;
        internal int Handle;
        internal string FileDigest = string.Empty;
        internal string FileIdentity = string.Empty;
        internal string MetaDigest = string.Empty;
        internal string MetaIdentity = string.Empty;
    }

    internal sealed class SourceObjectSnapshot
    {
        internal SavedSceneSnapshot Scene;
        internal GameObject GameObject;
        internal string ObjectPath = string.Empty;
        internal string ObjectId = string.Empty;
        internal string HierarchyDigest = string.Empty;

        internal object ToPayload()
        {
            return new
            {
                scenePath = Scene.Path,
                sceneGuid = Scene.Guid,
                sceneHandle = Scene.Handle,
                objectPath = ObjectPath,
                objectId = ObjectId,
                hierarchyDigest = HierarchyDigest,
                sceneFileDigest = Scene.FileDigest,
                sceneFileIdentity = Scene.FileIdentity,
                sceneMetaDigest = Scene.MetaDigest,
                sceneMetaIdentity = Scene.MetaIdentity,
                pathUnique = true
            };
        }
    }

    internal sealed class DuplicateTargetSnapshot
    {
        internal SavedSceneSnapshot Scene;
        internal GameObject Parent;
        internal string ParentPath = string.Empty;
        internal string ParentObjectId = string.Empty;
        internal string ParentHierarchyDigest = string.Empty;
        internal string ObjectPath = string.Empty;
        internal string Name = string.Empty;
        internal bool NameCollision;
        internal bool SameDestination;
        internal bool TargetWithinSource;

        internal object ToPayload()
        {
            return new
            {
                scenePath = Scene.Path,
                sceneGuid = Scene.Guid,
                sceneHandle = Scene.Handle,
                parentPath = ParentPath,
                parentObjectId = ParentObjectId,
                parentHierarchyDigest = ParentHierarchyDigest,
                sceneFileDigest = Scene.FileDigest,
                sceneFileIdentity = Scene.FileIdentity,
                sceneMetaDigest = Scene.MetaDigest,
                sceneMetaIdentity = Scene.MetaIdentity,
                objectPath = ObjectPath,
                name = Name,
                parentPathUnique = true,
                nameCollision = NameCollision,
                sameDestination = SameDestination,
                targetWithinSource = TargetWithinSource
            };
        }
    }

    internal sealed class PrefabTargetSnapshot
    {
        internal string AssetPath = string.Empty;
        internal string ParentFolderPath = string.Empty;
        internal string ParentFolderGuid = string.Empty;
        internal string ParentFolderIdentity = string.Empty;
        internal string StagingRootPath = string.Empty;
        internal string StagingRootGuid = string.Empty;
        internal string StagingRootIdentity = string.Empty;
        internal string StagingPolicy = string.Empty;
        internal bool AssetExists;
        internal bool MetaExists;

        internal object ToPayload()
        {
            return new
            {
                assetPath = AssetPath,
                parentFolderPath = ParentFolderPath,
                parentFolderGuid = ParentFolderGuid,
                parentFolderIdentity = ParentFolderIdentity,
                stagingRootPath = StagingRootPath,
                stagingRootGuid = StagingRootGuid,
                stagingRootIdentity = StagingRootIdentity,
                stagingPolicy = StagingPolicy,
                assetExists = AssetExists,
                metaExists = MetaExists,
                createNew = true
            };
        }
    }

    internal sealed class DuplicatePreviewSnapshot
    {
        internal SourceObjectSnapshot Source;
        internal DuplicateTargetSnapshot Target;
        internal bool PreserveWorldTransform;
        internal string PreviewDigest = string.Empty;

        internal object ToPayload()
        {
            return new
            {
                schema = SceneObjectCopyCore.ResultSchema,
                ok = true,
                operation = SceneObjectCopyCore.DuplicateOperation,
                preview = true,
                verified = true,
                changed = false,
                saved = false,
                mutationCount = 0,
                source = Source.ToPayload(),
                target = Target.ToPayload(),
                preserveWorldTransform = PreserveWorldTransform,
                previewDigest = PreviewDigest
            };
        }
    }

    internal sealed class PrefabPreviewSnapshot
    {
        internal SourceObjectSnapshot Source;
        internal PrefabTargetSnapshot Target;
        internal string PreviewDigest = string.Empty;

        internal object ToPayload()
        {
            return new
            {
                schema = SceneObjectCopyCore.ResultSchema,
                ok = true,
                operation = SceneObjectCopyCore.PrefabOperation,
                preview = true,
                verified = true,
                changed = false,
                saved = false,
                mutationCount = 0,
                source = Source.ToPayload(),
                target = Target.ToPayload(),
                previewDigest = PreviewDigest
            };
        }
    }

    internal sealed class SceneObjectCopyException : InvalidOperationException
    {
        internal bool CheckpointRestoreRequired { get; }

        internal SceneObjectCopyException(string message)
            : this(message, false)
        {
        }

        internal SceneObjectCopyException(string message, bool checkpointRestoreRequired)
            : base(message)
        {
            CheckpointRestoreRequired = checkpointRestoreRequired;
        }
    }

    internal sealed class StableFileEvidence
    {
        internal string Digest = string.Empty;
        internal string Identity = string.Empty;
        internal uint LinkCount;
        internal ulong Length;
    }

    internal sealed class StableAssetEvidence
    {
        internal string Guid = string.Empty;
        internal StableFileEvidence File;
        internal StableFileEvidence Meta;
    }

    internal sealed class FileHandleEvidence
    {
        internal string Identity = string.Empty;
        internal uint LinkCount;
        internal ulong Length;
    }

    internal sealed class StagingFolderLease
    {
        internal string RootPath = string.Empty;
        internal string FolderPath = string.Empty;
        internal string FolderGuid = string.Empty;
        internal string FolderIdentity = string.Empty;
        internal string PrefabPath = string.Empty;
    }
}
