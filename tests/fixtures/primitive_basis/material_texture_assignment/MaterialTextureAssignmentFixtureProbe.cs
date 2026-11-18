using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Editor;

public static class MaterialTextureAssignmentFixtureProbe
{
    private const string ChangedAfterPreview = "该文件在预览后被修改";
    private const ImportAssetOptions Reload = ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate;

    public static void Run()
    {
        var folder = AssetDatabase.GenerateUniqueAssetPath("Assets/TextureAssignmentProbe");
        AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder));
        var source = folder + "/Source.png";
        try
        {
            var pixels = new Texture2D(32, 32);
            File.WriteAllBytes(source, pixels.EncodeToPNG());
            UnityEngine.Object.DestroyImmediate(pixels);
            AssetDatabase.ImportAsset(source, Reload);
            foreach (var test in new[] { "restored-middle", "restored-first", "timestamp-and-restored" })
                Verify(folder, source, test);
            Debug.Log("VRCFORGE_MATERIAL_TEXTURE_ASSIGNMENT_PROBE_OK");
        }
        finally
        {
            AssetDatabase.DeleteAsset(folder);
        }
    }

    private static void Verify(string folder, string source, string test)
    {
        var materialPaths = Enumerable.Range(0, 3).Select(i => folder + "/" + test + "-" + i + ".mat").ToArray();
        var shader = Shader.Find("Standard");
        foreach (var path in materialPaths)
            AssetDatabase.CreateAsset(new Material(shader), path);
        var rows = new JArray(materialPaths.Select(path => Assignment(path, "_MainTex", source)));
        var normal = Assignment(materialPaths[0], "_BumpMap", source);
        normal["textureScale"] = new JObject { ["x"] = 2, ["y"] = 3 };
        normal["textureOffset"] = new JObject { ["x"] = 0.25, ["y"] = -0.5 };
        rows.Add(normal);
        var request = new JObject
        {
            ["expectedProjectPath"] = Path.GetFullPath(Path.Combine(Application.dataPath, "..")),
            ["preview"] = true,
            ["assignments"] = rows,
        };
        var preview = Execute(request);
        Require((bool)preview["IsSuccessful"], "Preview failed", preview);
        request["expectedBatchPlan"] = new JObject { ["assignments"] = preview["Payload"]["assignments"].DeepClone() };
        request["preview"] = false;

        var files = materialPaths.Select(File.ReadAllBytes).ToArray();
        var touched = test == "restored-first" ? 0 : 1;
        var pathToRestore = materialPaths[touched];
        var timestamp = File.GetLastWriteTimeUtc(pathToRestore);
        var modified = files[touched].Concat(new byte[] { (byte)'\n' }).ToArray();
        File.WriteAllBytes(pathToRestore, modified);
        File.SetLastWriteTimeUtc(pathToRestore, timestamp);
        var drift = Execute(request);
        Require(!(bool)drift["IsSuccessful"] && (string)drift["Payload"]["commitState"] == "not_started", "Disk drift started a write", drift);
        for (var i = 0; i < materialPaths.Length; i++)
            Require(File.ReadAllBytes(materialPaths[i]).SequenceEqual(i == touched ? modified : files[i]), "Disk drift modified the batch", drift);
        File.WriteAllBytes(pathToRestore, files[touched]);
        File.SetLastWriteTimeUtc(pathToRestore, timestamp.AddSeconds(-5));
        if (test == "timestamp-and-restored")
            File.SetLastWriteTimeUtc(materialPaths[0], File.GetLastWriteTimeUtc(materialPaths[0]).AddSeconds(-5));

        var applied = Execute(request);
        Require((bool)applied["IsSuccessful"] && (string)applied["Payload"]["commitState"] == "committed"
            && (bool)applied["Payload"]["persistedReadback"], "Restored files failed to commit", applied);
        Require((string)drift["Message"] == ChangedAfterPreview, "Wrong disk drift message", drift);
        foreach (var path in materialPaths)
            AssetDatabase.ImportAsset(path, Reload);
        foreach (var row in rows)
        {
            var material = AssetDatabase.LoadAssetAtPath<Material>((string)row["materialAssetPath"]);
            var texture = material.GetTexture((string)row["propertyName"]);
            Require(texture != null && AssetDatabase.GetAssetPath(texture) == source, "Texture differs after reimport", row);
        }
        var withNormal = AssetDatabase.LoadAssetAtPath<Material>(materialPaths[0]);
        Require(withNormal.GetTextureScale("_BumpMap") == new Vector2(2, 3)
            && withNormal.GetTextureOffset("_BumpMap") == new Vector2(0.25f, -0.5f)
            && withNormal.shader == shader, "Normal map transform or shader differs after reimport", applied);
        if (test == "restored-middle")
        {
            request.Remove("expectedBatchPlan");
            request["preview"] = true;
            preview = Execute(request);
            Require((bool)preview["IsSuccessful"], "Preview failed", preview);
            request["expectedBatchPlan"] = new JObject { ["assignments"] = preview["Payload"]["assignments"].DeepClone() };
            request["preview"] = false;
            var dirty = AssetDatabase.LoadAssetAtPath<Material>(materialPaths[0]);
            var saved = File.ReadAllBytes(materialPaths[0]);
            dirty.SetTextureScale("_MainTex", new Vector2(2, 3));
            EditorUtility.SetDirty(dirty);
            var rejected = Execute(request);
            Require(!(bool)rejected["IsSuccessful"] && (string)rejected["Payload"]["commitState"] == "not_started"
                && EditorUtility.IsDirty(dirty) && dirty.GetTextureScale("_MainTex") == new Vector2(2, 3)
                && File.ReadAllBytes(materialPaths[0]).SequenceEqual(saved), "Unsaved edit must stay in memory", rejected);
        }
        Debug.Log("VRCFORGE_MATERIAL_TEXTURE_ASSIGNMENT_CASE_OK " + test);
    }

    private static JObject Assignment(string material, string property, string source) => new JObject
    {
        ["materialAssetPath"] = material,
        ["propertyName"] = property,
        ["textureAssetPath"] = source,
    };

    private static JObject Execute(JObject request) => JObject.FromObject(MaterialTextureTool.HandleCommand(request));

    private static void Require(bool condition, string description, JToken result)
    {
        if (!condition) throw new InvalidOperationException(description + ". " + result);
    }
}
