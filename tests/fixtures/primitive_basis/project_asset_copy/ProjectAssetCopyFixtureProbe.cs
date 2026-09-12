using System;
using System.IO;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;
using VRCForge.Editor;

// Run only in a fresh disposable project before any other generated-output fixture.
public static class ProjectAssetCopyFixtureProbe
{
    private const string Root = "Assets/VRCForgeGenerated";
    private const string SourceFolder = "Assets/ProjectAssetCopyProbe";
    private const string SourcePath = SourceFolder + "/Source.anim";
    private const string RootCopy = Root + "/RootCopy.anim";
    private const string ClassifiedFolder = Root + "/Animations";
    private const string ClassifiedCopy = ClassifiedFolder + "/ClassifiedCopy.anim";

    public static void Run()
    {
        try
        {
            Require(!SceneObjectCopyCore.AssetOrMetaExists(Root), "fresh generated root must be absent");
            Require(!SceneObjectCopyCore.AssetOrMetaExists(SourceFolder), "fixture source folder must be absent");
            Require(SceneObjectCopyCore.ReadAssetGuid("Assets", "anchor")
                == "00000000000000001000000000000000", "Assets anchor GUID");
            Require(SceneObjectCopyCore.ReadDirectoryIdentity("Assets", "anchor").Length == 64,
                "Assets anchor directory identity");
            ExpectRejected(() => SceneObjectCopyCore.ToAbsoluteAssetPath("AssetsSibling"),
                "Assets sibling escaped the anchor boundary");

            CreateFolder("Assets", "ProjectAssetCopyProbe");
            AssetDatabase.CreateAsset(new AnimationClip { name = "Source" }, SourcePath);
            AssetDatabase.SaveAssets();
            var sourceBefore = SceneObjectCopyCore.ReadStableAssetEvidence(SourcePath, "fixture source");
            Require(!Invoke(Request("Assets/VRCForge/Generated/Rejected.anim", true)).Value<bool>("success"),
                "plugin output was accepted");
            Require(!Invoke(Request(ClassifiedCopy, true)).Value<bool>("success"),
                "missing classified parent was accepted");
            Require(!SceneObjectCopyCore.AssetOrMetaExists(Root), "preview created the generated root");

            var absentSnapshot = Snapshot(RootCopy);
            var preview = Preview(RootCopy);
            Require(!(bool)preview["target"]["generatedRootExists"], "absent root preview");
            Require((string)preview["target"]["anchorFolderPath"] == "Assets", "anchor path binding");
            var applied = Success(Invoke(Bind(preview)));
            Require((bool)applied["target"]["generatedRootCreated"], "first apply did not create the root");
            Require((int)applied["mutationCount"] == 2, "first apply mutation count");
            Require((string)applied["target"]["assetPath"] == RootCopy, "root copy path changed");
            Require((string)applied["target"]["guid"] != sourceBefore.Guid, "copy reused the source GUID");
            Require(!Invoke(Request(RootCopy, true)).Value<bool>("success"), "existing output was accepted");
            DeleteOwnedAsset(RootCopy);
            Require(Cleanup(absentSnapshot, RootLease()), "operation-created empty root cleanup failed");
            Require(!SceneObjectCopyCore.AssetOrMetaExists(Root), "root or meta remained after cleanup");

            var cleanupSnapshot = Snapshot(RootCopy);
            CreateFolder("Assets", "VRCForgeGenerated");
            var ownedRoot = RootLease();
            CreateFolder(Root, "Animations");
            Require(!Cleanup(cleanupSnapshot, ownedRoot), "cleanup removed a nonempty generated root");
            Require(AssetDatabase.IsValidFolder(ClassifiedFolder), "cleanup removed unrelated folder content");

            var classifiedPreview = Preview(ClassifiedCopy);
            Require((string)classifiedPreview["target"]["parentFolderPath"] == ClassifiedFolder,
                "classified parent path was not bound");
            var args = Bind(classifiedPreview);
            var savedGuid = (string)classifiedPreview["target"]["parentFolderGuid"];
            Require(string.IsNullOrEmpty(AssetDatabase.MoveAsset(ClassifiedFolder, Root + "/OriginalAnimations")),
                "fixture parent move failed");
            CreateFolder(Root, "Animations");
            Require(AssetDatabase.AssetPathToGUID(ClassifiedFolder) != savedGuid, "fixture parent GUID did not change");
            Require(!Invoke(args).Value<bool>("success"), "replaced classified parent was accepted");
            Require(!SceneObjectCopyCore.AssetOrMetaExists(ClassifiedCopy), "stale apply wrote an asset");

            var fresh = Preview(ClassifiedCopy);
            var classifiedApply = Success(Invoke(Bind(fresh)));
            Require((int)classifiedApply["mutationCount"] == 1, "classified apply created extra folders");
            Require((string)classifiedApply["target"]["assetPath"] == ClassifiedCopy,
                "classified copy did not preserve the exact path");
            Require(!(bool)classifiedApply["target"]["generatedRootCreated"], "classified apply claimed root creation");
            Require(SceneObjectCopyCore.StableAssetEvidenceMatches(sourceBefore,
                SceneObjectCopyCore.ReadStableAssetEvidence(SourcePath, "source after copy"), true),
                "source bytes, metadata, GUID or file identity changed");
            DeleteOwnedAsset(ClassifiedCopy);
            Require(AssetDatabase.DeleteAsset(ClassifiedFolder), "fixture classified folder cleanup failed");
            Require(AssetDatabase.DeleteAsset(Root + "/OriginalAnimations"), "fixture old parent cleanup failed");
            Require(Cleanup(cleanupSnapshot, ownedRoot), "verified owned root cleanup failed");

            // The actual failure-cleanup helper must preserve a replacement root.
            CreateFolder("Assets", "VRCForgeGenerated");
            var replacedLease = RootLease();
            Require(AssetDatabase.DeleteAsset(Root), "fixture root replacement delete failed");
            CreateFolder("Assets", "VRCForgeGenerated");
            Require(!Cleanup(cleanupSnapshot, replacedLease), "cleanup removed a replacement root");
            Require(SceneObjectCopyCore.DeleteOwnedStagingFolder(RootLease()), "fixture final root cleanup failed");
            DeleteOwnedAsset(SourcePath);
            Require(AssetDatabase.DeleteAsset(SourceFolder), "fixture source folder cleanup failed");
            Require(!SceneObjectCopyCore.AssetOrMetaExists(Root), "final generated root residue");
            Debug.Log("VRCFORGE_PROJECT_ASSET_COPY_PROBE_OK");
            EditorApplication.Exit(0);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            EditorApplication.Exit(1);
        }
    }

    private static JObject Request(string destination, bool preview) => new JObject
    {
        ["sourceAssetPath"] = SourcePath,
        ["destinationAssetPath"] = destination,
        ["preview"] = preview,
        ["overwrite"] = false,
    };

    private static JObject Invoke(JObject request) =>
        ((VRCForgeToolResult)DuplicateProjectAssetTool.HandleCommand(request)).ToStructuredContent();

    private static JObject Success(JObject response)
    {
        Require(response.Value<bool>("success"), "copy failed: " + response.ToString());
        var data = response["data"] as JObject;
        Require(data != null && data.Value<bool>("ok") && data.Value<bool>("verified"), "verified payload missing");
        return data;
    }

    private static JObject Preview(string destination) => Success(Invoke(Request(destination, true)));

    private static JObject Bind(JObject preview)
    {
        var source = (JObject)preview["source"];
        var target = (JObject)preview["target"];
        var request = Request((string)target["assetPath"], false);
        request["expectedProjectPath"] = Directory.GetParent(Application.dataPath).FullName;
        foreach (var field in new[] { "Guid", "FileDigest", "FileIdentity", "MetaDigest", "MetaIdentity", "MainAssetType", "ObjectLayoutDigest" })
        {
            request["expectedSource" + field] = source[char.ToLowerInvariant(field[0]) + field.Substring(1)].DeepClone();
        }
        foreach (var field in new[] { "GeneratedRootExists", "GeneratedRootGuid", "GeneratedRootIdentity", "AnchorFolderGuid", "AnchorFolderIdentity" })
        {
            request["expected" + field] = target[char.ToLowerInvariant(field[0]) + field.Substring(1)].DeepClone();
        }
        request["expectedDestinationParentFolderGuid"] = target["parentFolderGuid"].DeepClone();
        request["expectedDestinationParentFolderIdentity"] = target["parentFolderIdentity"].DeepClone();
        request["expectedDestinationAbsent"] = true;
        request["expectedPreviewDigest"] = preview["previewDigest"].DeepClone();
        return request;
    }

    private static object Snapshot(string destination) => typeof(DuplicateProjectAssetTool)
        .GetMethod("BuildSnapshot", BindingFlags.NonPublic | BindingFlags.Static)
        .Invoke(null, new object[] { SourcePath, destination });

    private static bool Cleanup(object snapshot, StagingFolderLease lease) => (bool)typeof(DuplicateProjectAssetTool)
        .GetMethod("CleanupFailedApply", BindingFlags.NonPublic | BindingFlags.Static)
        .Invoke(null, new object[] { snapshot, null, true, lease });

    private static StagingFolderLease RootLease() => new StagingFolderLease
    {
        RootPath = "Assets",
        FolderPath = Root,
        FolderGuid = SceneObjectCopyCore.ReadAssetGuid(Root, "owned root"),
        FolderIdentity = SceneObjectCopyCore.ReadDirectoryIdentity(Root, "owned root"),
    };

    private static void CreateFolder(string parent, string name)
    {
        Require(!string.IsNullOrEmpty(AssetDatabase.CreateFolder(parent, name)), "fixture folder creation failed");
        Require(AssetDatabase.IsValidFolder(parent + "/" + name), "fixture folder path changed");
    }

    private static void DeleteOwnedAsset(string path) => Require(SceneObjectCopyCore.DeleteOwnedAsset(
        path, SceneObjectCopyCore.ReadStableAssetEvidence(path, "fixture cleanup")), "owned asset cleanup failed");

    private static void ExpectRejected(Action action, string message)
    {
        try { action(); }
        catch (SceneObjectCopyException) { return; }
        throw new InvalidOperationException(message);
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }
}
