using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;
using VRCForge.Editor;

public static class GeneratedAssetRelocationFixtureProbe
{
    private const string SourceRoot = "Assets/VRCForge/Generated/RelocationFixture";
    private const string DestinationRoot = "Assets/VRCForgeGenerated/RelocationFixture";
    private const string Prefix = "Assets/VRCForge/Generated/RelocationFixture/fixture_";
    private static bool failSecondMove;
    private static int moveCount;

    public static void Run()
    {
        try
        {
            Cleanup();
            EnsureFolder("Assets/VRCForge", "Generated");
            AssetDatabase.CreateFolder("Assets/VRCForge/Generated", "RelocationFixture");
            var first = CreateAsset("fixture_first.asset", "first");
            var second = CreateAsset("fixture_second.asset", "second");
            var firstBinding = Binding(first, "Assets/VRCForgeGenerated/RelocationFixture/Category/fixture_first.asset");
            var secondBinding = Binding(second, "Assets/VRCForgeGenerated/RelocationFixture/Category/fixture_second.asset");
            var project = Directory.GetParent(Application.dataPath).FullName;

            var invalid = Request(new[] { firstBinding }, project, true);
            invalid["entries"][0]["expectedAssetSha256"] = new string('0', 64);
            var dirty = AssetDatabase.LoadAssetAtPath<GeneratedAssetRelocationFixtureAsset>(first);
            var dirtyDiskHash = SceneObjectCopyCore.ReadStableAssetEvidence(first, "dirty fixture baseline").File.Digest;
            dirty.value = "deliberately dirty";
            EditorUtility.SetDirty(dirty);
            RequireFailure(RelocateGeneratedAssetsTool.HandleCommand(invalid), "invalid preview accepted");
            Require(EditorUtility.IsDirty(dirty), "rejected preview saved dirty asset");
            Require(SceneObjectCopyCore.ReadStableAssetEvidence(first, "dirty fixture readback").File.Digest == dirtyDiskHash, "rejected preview changed disk bytes");

            var dirtyApply = RelocateGeneratedAssetsTool.HandleCommand(Request(new[] { firstBinding, secondBinding }, project, false));
            RequireFailure(dirtyApply, "dirty source apply accepted");
            VerifyRestored(firstBinding);
            VerifyRestored(secondBinding);
            Require(EditorUtility.IsDirty(dirty), "dirty apply saved user changes");
            Require(!AssetDatabase.IsValidFolder(DestinationRoot), "dirty apply created a destination");
            dirty.value = "first";
            AssetDatabase.SaveAssetIfDirty(dirty);
            firstBinding = Binding(first, firstBinding.Value<string>("destinationAssetPath"));

            var success = RelocateGeneratedAssetsTool.HandleCommand(Request(new[] { firstBinding, secondBinding }, project, false));
            var successData = RequireSuccess(success, "two asset relocation");
            Require(successData.Value<bool>("ok") && successData.Value<bool>("verified"), "successful relocation verification");
            Require(successData.Value<string>("commitState") == "committed", "successful commit state");
            Require(successData.Value<string>("readbackState") == "verified", "successful readback state");
            VerifyMoved(firstBinding);
            VerifyMoved(secondBinding);

            // Recreate the first source and prepare a two-item batch. The processor
            // rejects only the second fixture move, forcing the tool's rollback.
            Cleanup();
            AssetDatabase.CreateFolder("Assets/VRCForge/Generated", "RelocationFixture");
            first = CreateAsset("fixture_first.asset", "first");
            second = CreateAsset("fixture_second.asset", "second");
            firstBinding = Binding(first, "Assets/VRCForgeGenerated/RelocationFixture/Category/fixture_first.asset");
            secondBinding = Binding(second, "Assets/VRCForgeGenerated/RelocationFixture/Category/fixture_second.asset");
            moveCount = 0;
            failSecondMove = true;
            var failed = RelocateGeneratedAssetsTool.HandleCommand(Request(new[] { firstBinding, secondBinding }, project, false));
            failSecondMove = false;
            Debug.Log("VRCFORGE_RELOCATION_ROLLBACK_RECEIPT " + ((VRCForgeToolResult)failed).ToStructuredContent().ToString(Newtonsoft.Json.Formatting.None));
            RequireFailure(failed, "injected middle-batch failure accepted");
            VerifyRestored(firstBinding);
            VerifyRestored(secondBinding);
            var firstDestination = firstBinding.Value<string>("destinationAssetPath");
            Require(!File.Exists(Absolute(firstDestination)) && !File.Exists(Absolute(firstDestination) + ".meta"), "first destination residue");
            Require(!AssetDatabase.IsValidFolder(DestinationRoot) && !File.Exists(Absolute(DestinationRoot) + ".meta"), "owned destination folder residue");
            Cleanup();
            Debug.Log("VRCFORGE_GENERATED_ASSET_RELOCATION_PROBE_OK");
            EditorApplication.Exit(0);
        }
        catch (Exception exception)
        {
            failSecondMove = false;
            Cleanup();
            Debug.LogException(exception);
            EditorApplication.Exit(1);
        }
    }

    private static string CreateAsset(string name, string value)
    {
        var path = SourceRoot + "/" + name;
        var asset = ScriptableObject.CreateInstance<GeneratedAssetRelocationFixtureAsset>();
        asset.value = value;
        AssetDatabase.CreateAsset(asset, path);
        AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
        return path;
    }

    private static JObject Binding(string source, string destination)
    {
        var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(source, "fixture binding");
        return new JObject
        {
            ["sourceAssetPath"] = source,
            ["destinationAssetPath"] = destination,
            ["expectedGuid"] = evidence.Guid,
            ["expectedAssetSha256"] = evidence.File.Digest,
            ["expectedMetaSha256"] = evidence.Meta.Digest
        };
    }

    private static JObject Request(JObject[] entries, string project, bool preview)
    {
        return new JObject
        {
            ["entries"] = new JArray(entries.Select(entry => entry.DeepClone())),
            ["expectedProjectPath"] = project,
            ["preview"] = preview
        };
    }

    private static void VerifyMoved(JObject binding)
    {
        var destination = binding.Value<string>("destinationAssetPath");
        var source = binding.Value<string>("sourceAssetPath");
        Require(!File.Exists(Absolute(source)) && !File.Exists(Absolute(source) + ".meta"), "source remains after success");
        var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(destination, "fixture moved readback");
        Require(evidence.Guid == binding.Value<string>("expectedGuid"), "moved GUID");
        Require(evidence.File.Digest == binding.Value<string>("expectedAssetSha256"), "moved asset hash");
        Require(evidence.Meta.Digest == binding.Value<string>("expectedMetaSha256"), "moved metadata hash");
    }

    private static void VerifyRestored(JObject binding)
    {
        var source = binding.Value<string>("sourceAssetPath");
        var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(source, "fixture restored readback");
        Require(evidence.Guid == binding.Value<string>("expectedGuid"), "restored GUID");
        Require(evidence.File.Digest == binding.Value<string>("expectedAssetSha256"), "restored asset hash");
        Require(evidence.Meta.Digest == binding.Value<string>("expectedMetaSha256"), "restored metadata hash");
    }

    private static void EnsureFolder(string parent, string child)
    {
        if (!AssetDatabase.IsValidFolder(parent + "/" + child)) AssetDatabase.CreateFolder(parent, child);
    }

    private static void Cleanup()
    {
        if (AssetDatabase.IsValidFolder(DestinationRoot)) AssetDatabase.DeleteAsset(DestinationRoot);
        if (AssetDatabase.IsValidFolder(SourceRoot)) AssetDatabase.DeleteAsset(SourceRoot);
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
    }

    private static string Absolute(string path) => Path.Combine(Directory.GetParent(Application.dataPath).FullName, path.Replace('/', Path.DirectorySeparatorChar));
    private static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException("Probe failed: " + message); }
    private static void RequireFailure(object value, string message)
    {
        var result = value as VRCForgeToolResult;
        Require(result != null && !result.IsSuccessful, message);
        var wire = result.ToStructuredContent();
        var data = wire["data"] as JObject;
        Require(data != null && !data.Value<bool>("ok") && !data.Value<bool>("verified"), message + " failure state");
    }

    private static JObject RequireSuccess(object value, string message)
    {
        var result = value as VRCForgeToolResult;
        Require(result != null && result.IsSuccessful, message + ": " + (result == null ? "missing result" : result.ToStructuredContent().ToString()));
        var wire = result.ToStructuredContent();
        var data = wire["data"] as JObject;
        Require(data != null, message + " payload");
        return data;
    }

    internal static bool ShouldFailMove(string source, string destination)
    {
        if (!failSecondMove || !source.StartsWith(Prefix, StringComparison.OrdinalIgnoreCase)) return false;
        moveCount++;
        return moveCount == 2;
    }
}

public sealed class GeneratedAssetRelocationFixtureMoveGuard : AssetModificationProcessor
{
    private static AssetMoveResult OnWillMoveAsset(string sourceAssetPath, string destinationAssetPath)
    {
        return GeneratedAssetRelocationFixtureProbe.ShouldFailMove(sourceAssetPath, destinationAssetPath)
            ? AssetMoveResult.FailedMove
            : AssetMoveResult.DidNotMove;
    }
}
