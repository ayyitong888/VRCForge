using System;
using System.IO;
using System.Runtime.InteropServices;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRCForge.Editor;

public static class SceneObjectCopyFixtureProbe
{
    private const string SceneFolder = "Assets/VRCForge/Generated/SceneObjectCopyProbe";
    private const string ScenePath = SceneFolder + "/SceneObjectCopyProbe.unity";
    private const string PrefabPath = SceneFolder + "/AccessoryProbe.prefab";

    public static void Run()
    {
        try
        {
            VerifyStructuredMutationFailureSignals();
            PrepareFolder();
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var avatarA = new GameObject("AvatarA");
            var source = new GameObject("Accessory");
            source.transform.SetParent(avatarA.transform, false);
            source.AddComponent<BoxCollider>();
            var avatarB = new GameObject("AvatarB");
            avatarB.transform.localPosition = new Vector3(1f, 2f, 3f);
            Require(EditorSceneManager.SaveScene(scene, ScenePath), "fixture scene save failed");

            var baseline = SceneObjectCopyCore.BuildSourceSnapshot(ScenePath, "AvatarA/Accessory");
            var baselineSceneDigest = baseline.Scene.FileDigest;
            RunMetaHardlinkRejection();
            RunDuplicateLifecycle(baselineSceneDigest);
            RunPrefabLifecycle(baselineSceneDigest);

            Require(
                SceneObjectCopyCore.BuildSourceSnapshot(ScenePath, "AvatarA/Accessory").HierarchyDigest
                    == baseline.HierarchyDigest,
                "source hierarchy did not return to baseline");
            Require(
                SceneObjectCopyCore.ResolveSavedScene(ScenePath, "fixture scene").FileDigest
                    == baselineSceneDigest,
                "fixture scene did not return to baseline");
            Require(!SceneObjectCopyCore.AssetPathExists(PrefabPath), "prefab residue remained");
            Require(
                Directory.GetDirectories(
                    SceneObjectCopyCore.ToAbsoluteAssetPath(SceneObjectCopyCore.GeneratedRoot),
                    "stage-*",
                    SearchOption.TopDirectoryOnly).Length == 0,
                "staging folder residue remained");
            Debug.Log("VRCFORGE_SCENE_OBJECT_COPY_PROBE_OK");
            EditorApplication.Exit(0);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            EditorApplication.Exit(1);
        }
    }

    private static void VerifyStructuredMutationFailureSignals()
    {
        var restoreRequired = JObject.FromObject(SceneObjectCopyCore.BuildMutationFailure(
            SceneObjectCopyCore.DuplicateOperation,
            false));
        Require(!restoreRequired.Value<bool>("success"), "restore-required success flag");
        var restoreRequiredData = (JObject)restoreRequired["data"];
        Require(restoreRequiredData.Value<bool>("mutationStarted"), "restore-required mutation flag");
        Require(!restoreRequiredData.Value<bool>("restored"), "restore-required restored flag");
        Require(!restoreRequiredData.Value<bool>("cleanupVerified"), "restore-required cleanup proof");
        Require(restoreRequiredData.Value<bool>("cleanupRequired"), "restore-required cleanup flag");
        Require(
            restoreRequiredData.Value<bool>("checkpointRestoreRequired"),
            "restore-required checkpoint flag");
        Require(
            restoreRequiredData.Value<string>("operationState") == "checkpoint_restore_required",
            "restore-required operation state");

        var restored = JObject.FromObject(SceneObjectCopyCore.BuildMutationFailure(
            SceneObjectCopyCore.PrefabOperation,
            true));
        Require(!restored.Value<bool>("success"), "restored success flag");
        var restoredData = (JObject)restored["data"];
        Require(restoredData.Value<bool>("mutationStarted"), "restored mutation flag");
        Require(restoredData.Value<bool>("restored"), "restored state flag");
        Require(restoredData.Value<bool>("cleanupVerified"), "restored cleanup proof");
        Require(!restoredData.Value<bool>("cleanupRequired"), "restored cleanup flag");
        Require(!restoredData.Value<bool>("checkpointRestoreRequired"), "restored checkpoint flag");
        Require(restoredData.Value<string>("operationState") == "restored", "restored operation state");

        var preMutation = JObject.FromObject(SceneObjectCopyCore.Failure(
            new SceneObjectCopyException("pre-mutation rejection")));
        var preMutationData = preMutation["data"] as JObject;
        Require(
            preMutationData == null || preMutationData["mutationStarted"] == null,
            "pre-mutation failure claimed a mutation");
    }

    private static void RunMetaHardlinkRejection()
    {
        if (Application.platform != RuntimePlatform.WindowsEditor)
        {
            return;
        }

        var source = SceneObjectCopyCore.BuildSourceSnapshot(ScenePath, "AvatarA/Accessory");
        var preview = SceneObjectCopyCore.BuildPrefabPreview(
            ScenePath,
            "AvatarA/Accessory",
            PrefabPath);
        var staging = SceneObjectCopyCore.CreateRandomStagingFolder(preview.Target);
        bool staged;
        Require(
            PrefabUtility.SaveAsPrefabAsset(source.GameObject, staging.PrefabPath, out staged) != null
                && staged,
            "hardlink fixture staging write failed");
        AssetDatabase.SaveAssets();
        AssetDatabase.ImportAsset(
            staging.PrefabPath,
            ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
        var stagedEvidence = SceneObjectCopyCore.ReadStableAssetEvidence(
            staging.PrefabPath,
            "hardlink staging control",
            VerifyAtomicSnapshotWriteDenied);
        var stagedMetaPath = SceneObjectCopyCore.ToAbsoluteAssetPath(staging.PrefabPath) + ".meta";
        var stagedAlias = stagedMetaPath + ".hardlink-probe";
        Require(
            CreateHardLinkW(stagedAlias, stagedMetaPath, IntPtr.Zero),
            "staging metadata hardlink injection failed");
        ExpectHardlinkRejected(staging.PrefabPath, "staging metadata hardlink was accepted");
        File.Delete(stagedAlias);
        var stagedAfter = SceneObjectCopyCore.ReadStableAssetEvidence(
            staging.PrefabPath,
            "hardlink staging cleanup");
        Require(
            SceneObjectCopyCore.StableAssetEvidenceMatches(stagedEvidence, stagedAfter, true),
            "staging metadata identity changed after hardlink cleanup");
        Require(
            SceneObjectCopyCore.DeleteOwnedAsset(staging.PrefabPath, stagedAfter),
            "hardlink staging asset cleanup failed");
        Require(
            SceneObjectCopyCore.DeleteOwnedStagingFolder(staging),
            "hardlink staging folder cleanup failed");

        bool finalSaved;
        Require(
            PrefabUtility.SaveAsPrefabAsset(source.GameObject, PrefabPath, out finalSaved) != null
                && finalSaved,
            "hardlink fixture final write failed");
        AssetDatabase.SaveAssets();
        AssetDatabase.ImportAsset(
            PrefabPath,
            ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
        var finalEvidence = SceneObjectCopyCore.ReadStableAssetEvidence(
            PrefabPath,
            "hardlink final control",
            VerifyAtomicSnapshotWriteDenied);
        var finalMetaPath = SceneObjectCopyCore.ToAbsoluteAssetPath(PrefabPath) + ".meta";
        var finalAlias = finalMetaPath + ".hardlink-probe";
        Require(
            CreateHardLinkW(finalAlias, finalMetaPath, IntPtr.Zero),
            "final metadata hardlink injection failed");
        ExpectHardlinkRejected(PrefabPath, "final metadata hardlink was accepted");
        File.Delete(finalAlias);
        var finalAfter = SceneObjectCopyCore.ReadStableAssetEvidence(
            PrefabPath,
            "hardlink final cleanup");
        Require(
            SceneObjectCopyCore.StableAssetEvidenceMatches(finalEvidence, finalAfter, true),
            "final metadata identity changed after hardlink cleanup");
        finalAfter = ReplaceAssetFilesWithExactBytes(PrefabPath, finalAfter);
        Require(
            SceneObjectCopyCore.DeleteOwnedAsset(PrefabPath, finalAfter),
            "hardlink final asset cleanup failed");
    }

    private static void VerifyAtomicSnapshotWriteDenied(
        string assetFile,
        string metaFile)
    {
        Require(WriteOpenDenied(assetFile), "snapshot allowed a prefab write handle");
        Require(WriteOpenDenied(metaFile), "snapshot allowed a metadata write handle");
    }

    private static bool WriteOpenDenied(string path)
    {
        try
        {
            using (new FileStream(
                path,
                FileMode.Open,
                FileAccess.Write,
                FileShare.ReadWrite))
            {
                return false;
            }
        }
        catch (IOException)
        {
            return true;
        }
        catch (UnauthorizedAccessException)
        {
            return true;
        }
    }

    private static StableAssetEvidence ReplaceAssetFilesWithExactBytes(
        string assetPath,
        StableAssetEvidence before)
    {
        var assetFile = SceneObjectCopyCore.ToAbsoluteAssetPath(assetPath);
        var metaFile = assetFile + ".meta";
        var assetReplacement = assetFile + ".replacement-probe";
        var metaReplacement = metaFile + ".replacement-probe";
        File.WriteAllBytes(assetReplacement, File.ReadAllBytes(assetFile));
        File.WriteAllBytes(metaReplacement, File.ReadAllBytes(metaFile));
        File.Delete(assetFile);
        File.Delete(metaFile);
        File.Move(assetReplacement, assetFile);
        File.Move(metaReplacement, metaFile);
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        var after = SceneObjectCopyCore.ReadStableAssetEvidence(
            assetPath,
            "identity replacement control");
        Require(before.File.Identity != after.File.Identity, "prefab identity was not replaced");
        Require(before.Meta.Identity != after.Meta.Identity, "prefab metadata identity was not replaced");
        SceneObjectCopyCore.VerifyMovedAssetEvidence(before, after);
        return after;
    }

    private static void ExpectHardlinkRejected(string assetPath, string message)
    {
        var rejected = false;
        try
        {
            SceneObjectCopyCore.ReadStableAssetEvidence(assetPath, "hardlink rejection probe");
        }
        catch (SceneObjectCopyException)
        {
            rejected = true;
        }
        Require(rejected, message);
    }

    private static void RunDuplicateLifecycle(string baselineSceneDigest)
    {
        var preview = RequireSuccess(DuplicateSceneObjectTool.HandleCommand(new JObject
        {
            ["sourceScenePath"] = ScenePath,
            ["sourceObjectPath"] = "AvatarA/Accessory",
            ["targetParentScenePath"] = ScenePath,
            ["targetParentPath"] = "AvatarB",
            ["targetName"] = "AccessoryCopy",
            ["preserveWorldTransform"] = false,
            ["preview"] = true,
            ["saveScene"] = false,
            ["overwrite"] = false
        }));
        Require(preview.Value<bool>("preview"), "duplicate preview flag missing");
        Require(preview.Value<int>("mutationCount") == 0, "duplicate preview reported a mutation");

        var source = (JObject)preview["source"];
        var target = (JObject)preview["target"];
        var apply = RequireSuccess(DuplicateSceneObjectTool.HandleCommand(new JObject
        {
            ["sourceScenePath"] = source.Value<string>("scenePath"),
            ["sourceObjectPath"] = source.Value<string>("objectPath"),
            ["targetParentScenePath"] = target.Value<string>("scenePath"),
            ["targetParentPath"] = target.Value<string>("parentPath"),
            ["targetName"] = target.Value<string>("name"),
            ["preserveWorldTransform"] = preview.Value<bool>("preserveWorldTransform"),
            ["preview"] = false,
            ["saveScene"] = true,
            ["overwrite"] = false,
            ["expectedProjectPath"] = ProjectRoot(),
            ["expectedSourceSceneGuid"] = source.Value<string>("sceneGuid"),
            ["expectedSourceSceneHandle"] = source.Value<int>("sceneHandle"),
            ["expectedSourceObjectId"] = source.Value<string>("objectId"),
            ["expectedSourceHierarchyDigest"] = source.Value<string>("hierarchyDigest"),
            ["expectedSourceSceneFileDigest"] = source.Value<string>("sceneFileDigest"),
            ["expectedSourceSceneFileIdentity"] = source.Value<string>("sceneFileIdentity"),
            ["expectedSourceSceneMetaDigest"] = source.Value<string>("sceneMetaDigest"),
            ["expectedSourceSceneMetaIdentity"] = source.Value<string>("sceneMetaIdentity"),
            ["expectedTargetSceneGuid"] = target.Value<string>("sceneGuid"),
            ["expectedTargetSceneHandle"] = target.Value<int>("sceneHandle"),
            ["expectedTargetParentObjectId"] = target.Value<string>("parentObjectId"),
            ["expectedTargetParentHierarchyDigest"] = target.Value<string>("parentHierarchyDigest"),
            ["expectedTargetSceneFileDigest"] = target.Value<string>("sceneFileDigest"),
            ["expectedTargetSceneFileIdentity"] = target.Value<string>("sceneFileIdentity"),
            ["expectedTargetSceneMetaDigest"] = target.Value<string>("sceneMetaDigest"),
            ["expectedTargetSceneMetaIdentity"] = target.Value<string>("sceneMetaIdentity"),
            ["expectedDestinationPath"] = target.Value<string>("objectPath"),
            ["expectedPreviewDigest"] = preview.Value<string>("previewDigest")
        }));
        Require(!apply.Value<bool>("preview"), "duplicate apply stayed in preview mode");
        Require(apply.Value<bool>("verified"), "duplicate apply was not verified");
        Require(
            SceneObjectCopyCore.ResolveUniqueGameObject(
                SceneManager.GetActiveScene(),
                "AvatarB/AccessoryCopy",
                "duplicate readback") != null,
            "duplicate target was missing");

        var duplicate = SceneObjectCopyCore.ResolveUniqueGameObject(
            SceneManager.GetActiveScene(),
            "AvatarB/AccessoryCopy",
            "duplicate cleanup");
        UnityEngine.Object.DestroyImmediate(duplicate);
        EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
        Require(EditorSceneManager.SaveScene(SceneManager.GetActiveScene()), "duplicate cleanup save failed");
        Require(
            SceneObjectCopyCore.ResolveSavedScene(ScenePath, "duplicate cleanup scene").FileDigest
                == baselineSceneDigest,
            "duplicate cleanup did not restore the scene file");
    }

    private static void RunPrefabLifecycle(string baselineSceneDigest)
    {
        var preview = RequireSuccess(SaveSceneObjectAsPrefabTool.HandleCommand(new JObject
        {
            ["sourceScenePath"] = ScenePath,
            ["sourceObjectPath"] = "AvatarA/Accessory",
            ["prefabAssetPath"] = PrefabPath,
            ["preview"] = true,
            ["saveAssets"] = false,
            ["overwrite"] = false
        }));
        Require(preview.Value<bool>("preview"), "prefab preview flag missing");
        Require(preview.Value<int>("mutationCount") == 0, "prefab preview reported a mutation");

        var source = (JObject)preview["source"];
        var target = (JObject)preview["target"];
        var apply = RequireSuccess(SaveSceneObjectAsPrefabTool.HandleCommand(new JObject
        {
            ["sourceScenePath"] = source.Value<string>("scenePath"),
            ["sourceObjectPath"] = source.Value<string>("objectPath"),
            ["prefabAssetPath"] = target.Value<string>("assetPath"),
            ["preview"] = false,
            ["saveAssets"] = true,
            ["overwrite"] = false,
            ["expectedProjectPath"] = ProjectRoot(),
            ["expectedSourceSceneGuid"] = source.Value<string>("sceneGuid"),
            ["expectedSourceSceneHandle"] = source.Value<int>("sceneHandle"),
            ["expectedSourceObjectId"] = source.Value<string>("objectId"),
            ["expectedSourceHierarchyDigest"] = source.Value<string>("hierarchyDigest"),
            ["expectedSourceSceneFileDigest"] = source.Value<string>("sceneFileDigest"),
            ["expectedSourceSceneFileIdentity"] = source.Value<string>("sceneFileIdentity"),
            ["expectedSourceSceneMetaDigest"] = source.Value<string>("sceneMetaDigest"),
            ["expectedSourceSceneMetaIdentity"] = source.Value<string>("sceneMetaIdentity"),
            ["expectedPrefabParentFolderGuid"] = target.Value<string>("parentFolderGuid"),
            ["expectedPrefabParentFolderIdentity"] = target.Value<string>("parentFolderIdentity"),
            ["expectedStagingRootGuid"] = target.Value<string>("stagingRootGuid"),
            ["expectedStagingRootIdentity"] = target.Value<string>("stagingRootIdentity"),
            ["expectedStagingPolicy"] = target.Value<string>("stagingPolicy"),
            ["expectedPreviewDigest"] = preview.Value<string>("previewDigest")
        }));
        Require(!apply.Value<bool>("preview"), "prefab apply stayed in preview mode");
        Require(apply.Value<bool>("verified"), "prefab apply was not verified");
        Require(SceneObjectCopyCore.AssetPathExists(PrefabPath), "prefab asset was missing");
        Require(
            SceneObjectCopyCore.ResolveSavedScene(ScenePath, "prefab source scene").FileDigest
                == baselineSceneDigest,
            "prefab save changed the source scene");
        Require(AssetDatabase.DeleteAsset(PrefabPath), "prefab cleanup failed");
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        Require(!SceneObjectCopyCore.AssetPathExists(PrefabPath), "prefab cleanup left residue");
    }

    private static JObject RequireSuccess(object response)
    {
        var success = response as SuccessResponse;
        if (success == null)
        {
            var failure = response as ErrorResponse;
            throw new InvalidOperationException(
                failure == null ? "unexpected tool response" : "tool failed: " + failure.Error);
        }
        return JObject.FromObject(success.Data);
    }

    private static string ProjectRoot()
    {
        return Directory.GetParent(Application.dataPath)?.FullName
            ?? throw new InvalidOperationException("project root unavailable");
    }

    private static void PrepareFolder()
    {
        if (!AssetDatabase.IsValidFolder("Assets/VRCForge/Generated"))
        {
            AssetDatabase.CreateFolder("Assets/VRCForge", "Generated");
        }
        if (AssetDatabase.IsValidFolder(SceneFolder))
        {
            AssetDatabase.DeleteAsset(SceneFolder);
        }
        foreach (var childFolder in AssetDatabase.GetSubFolders("Assets/VRCForge/Generated"))
        {
            var folderName = childFolder.Substring(childFolder.LastIndexOf('/') + 1);
            if (folderName.StartsWith("stage-", StringComparison.Ordinal))
            {
                AssetDatabase.DeleteAsset(childFolder);
            }
        }
        AssetDatabase.CreateFolder("Assets/VRCForge/Generated", "SceneObjectCopyProbe");
        AssetDatabase.SaveAssets();
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
        {
            throw new InvalidOperationException(message);
        }
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateHardLinkW(
        string fileName,
        string existingFileName,
        IntPtr securityAttributes);
}
