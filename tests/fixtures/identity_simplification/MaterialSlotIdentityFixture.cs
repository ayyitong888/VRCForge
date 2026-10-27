// Run only in a disposable Unity project with the full Core imported.
// Exercises production scene/material persistence; does not test MCP admission.
using System;
using System.IO;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using VRCForge.Core.MCP;
using VRCForge.Editor;

public static class MaterialSlotIdentityFixture
{
    const BindingFlags Static = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
    const BindingFlags Instance = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    static void Require(bool value, string message) { if (!value) throw new InvalidOperationException(message); }
    static object Call(string method, params object[] args)
        => typeof(UnityRendererMaterialSlotsBatch).GetMethod(method, Static).Invoke(null, args);
    static JObject Evidence(object plan) => (JObject)plan.GetType().GetField("Evidence", Instance).GetValue(plan);
    static JObject Success(object value)
    {
        var result = (VRCForgeToolResult)value;
        Require(result.IsSuccessful, result.Message);
        return JObject.FromObject(result.Payload);
    }
    static Renderer RendererUnder(Transform root, string name, Material material, int count)
    {
        var go = new GameObject(name);
        go.transform.SetParent(root, false);
        var renderer = go.AddComponent<MeshRenderer>();
        var materials = new Material[count];
        for (var i = 0; i < count; i++) materials[i] = material;
        renderer.sharedMaterials = materials;
        return renderer;
    }

    public static JObject Run()
    {
        Require(!EditorApplication.isPlaying, "material fixture requires edit mode");
        var root = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
        var folder = "Assets/MaterialSlotIdentityFixture_" + Guid.NewGuid().ToString("N");
        Directory.CreateDirectory(Path.Combine(root, folder));
        AssetDatabase.Refresh();
        var shader = Shader.Find("Unlit/Color") ?? Shader.Find("Standard");
        Require(shader != null, "fixture shader unavailable");
        var before = new Material(shader);
        var after = new Material(shader);
        AssetDatabase.CreateAsset(before, folder + "/Before.mat");
        AssetDatabase.CreateAsset(after, folder + "/After.mat");
        AssetDatabase.SaveAssets();
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var avatar = new GameObject("Avatar");
        var a = RendererUnder(avatar.transform, "A", before, 2);
        var b = RendererUnder(avatar.transform, "B", before, 1);
        var scenePath = folder + "/Fixture.unity";
        Require(EditorSceneManager.SaveScene(scene, scenePath), "fixture initial save failed");
        var aId = RendererComponentIdentity.Create(a).componentId;
        var bId = RendererComponentIdentity.Create(b).componentId;
        var rows = new JArray();
        for (var i = 0; i < 3; i++) rows.Add(new JObject {
            ["rendererPath"] = i < 2 ? "Avatar/A" : "Avatar/B",
            ["rendererComponentId"] = i < 2 ? aId : bId,
            ["slotIndex"] = i == 1 ? 1 : 0, ["newMaterialAssetPath"] = folder + "/After.mat"
        });
        RendererMaterialSlotTool.Target resolved;
        var observed = RendererMaterialSlotTool.ResolveTarget("Avatar/A", aId, out resolved);
        Require(resolved.Renderer == a, "exact renderer resolution changed");
        var first = Call("Build", rows);
        var second = Call("Build", rows);
        Require(JToken.DeepEquals(Evidence(first), Evidence(second)), "fresh observations disagree without a write");
        var request = new JObject { ["assignments"] = rows, ["expectedProjectPath"] = root, ["preview"] = true };
        var preview = Success(RendererMaterialSlotTool.HandleCommand(request));
        Require(JToken.DeepEquals(preview["plan"], Evidence(first)), "preview differs from direct Build");
        request["preview"] = false;
        request["expectedBatchPlan"] = preview["plan"].DeepClone();
        var applied = Success(RendererMaterialSlotTool.HandleCommand(request));
        Require(applied.Value<bool>("committed") && applied.Value<bool>("persistedReadback"), "apply did not commit and read back");
        Require(a.sharedMaterials[0] == after && a.sharedMaterials[1] == after && b.sharedMaterials[0] == after,
            "three assignments were not applied");
        var saved = SceneObjectCopyCore.ResolveSavedScene(scenePath, "fixture persisted scene");
        Require(saved.FileDigest != observed.FileDigest, "scene bytes did not change");
        var readback = (JArray)Call("ReadRenderers", first, saved);
        Require(JToken.DeepEquals(Evidence(first)["afterRenderers"], readback), "independent readback differs from expected slots");
        EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);
        var reopened = SceneObjectCopyCore.ResolveSavedScene(scenePath, "fixture reopened scene");
        Require(JToken.DeepEquals(readback, (JArray)Call("ReadRenderers", first, reopened)), "reopened scene differs from readback");
        var report = new JObject { ["ok"] = true, ["scenePath"] = scenePath, ["assignments"] = 3,
            ["renderers"] = 2, ["previewStable"] = true, ["committed"] = true,
            ["persistedReadback"] = true, ["reopenedReadback"] = true, ["result"] = applied };
        File.WriteAllText(Path.Combine(root, "Library", "MaterialSlotIdentityFixture.json"), report.ToString());
        return report;
    }
}
