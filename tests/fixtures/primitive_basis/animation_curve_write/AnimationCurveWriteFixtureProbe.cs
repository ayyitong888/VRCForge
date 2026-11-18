using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Editor;

public static class AnimationCurveWriteFixtureProbe
{
    private const string ChangedAfterPreview = "该文件在预览后被修改";
    private static readonly ImportAssetOptions Reload =
        ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate;

    public static void Run()
    {
        Debug.Log("VRCFORGE_COMPATIBILITY_UNITY " + Application.unityVersion);
        foreach (var package in UnityEditor.PackageManager.PackageInfo.GetAllRegisteredPackages())
        {
            if (package.name == "com.vrchat.avatars" || package.name == "com.vrchat.base")
                Debug.Log("VRCFORGE_COMPATIBILITY_SDK " + package.name + " " + package.version);
        }
        var first = AssetDatabase.GenerateUniqueAssetPath("Assets/CurveCompatibilityA.anim");
        var second = AssetDatabase.GenerateUniqueAssetPath("Assets/CurveCompatibilityB.anim");
        try
        {
            var retained = EditorCurveBinding.FloatCurve("Untouched", typeof(GameObject), "m_IsActive");
            foreach (var path in new[] { first, second })
            {
                var clip = new AnimationClip();
                AnimationUtility.SetEditorCurve(clip, retained, AnimationCurve.Constant(0, 1, 1));
                AssetDatabase.CreateAsset(clip, path);
            }
            var binding = EditorCurveBinding.FloatCurve("", typeof(GameObject), "m_IsActive");
            var batch = new JObject
            {
                ["assetOnly"] = true,
                ["clips"] = new JArray(ClipRequest(first), ClipRequest(second)),
            };
            BindPreview(batch);
            RequireSuccess(WriteAnimationCurveTool.HandleCommand(batch));
            foreach (var path in new[] { first, second })
            {
                AssetDatabase.ImportAsset(path, Reload);
                var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
                if (AnimationUtility.GetEditorCurve(clip, binding)?.Evaluate(0) != 1
                    || AnimationUtility.GetEditorCurve(clip, retained)?.Evaluate(0) != 1)
                    throw new InvalidOperationException("Changed or untouched curve differed after reload.");
            }

            var flat = CurveRequest();
            flat["assetOnly"] = true;
            flat["clipPath"] = first;
            VerifyDrift("flat", flat, first);
            var single = ClipRequest(first);
            single["assetOnly"] = true;
            VerifyDrift("single", single, first);
            VerifyDrift("batch", batch, second);
            Debug.Log("VRCFORGE_ANIMATION_CURVE_WRITE_PROBE_OK");
        }
        finally
        {
            AssetDatabase.DeleteAsset(first);
            AssetDatabase.DeleteAsset(second);
        }
    }

    private static JObject CurveRequest() => new JObject
    {
        ["propertyName"] = "m_IsActive",
        ["constantFloat"] = 1,
        ["overwriteExisting"] = true,
    };

    private static JObject ClipRequest(string path) => new JObject
    {
        ["clipPath"] = path,
        ["curves"] = new JArray(CurveRequest()),
    };

    private static void BindPreview(JObject request)
    {
        request.Remove("expectedClipFiles");
        request["preview"] = true;
        var result = RequireSuccess(WriteAnimationCurveTool.HandleCommand(request));
        var clipFiles = result["Payload"]?["clipFiles"];
        if (clipFiles == null)
            throw new InvalidOperationException("Preview omitted clip file evidence.");
        request["expectedClipFiles"] = clipFiles.DeepClone();
        request["preview"] = false;
    }

    private static void VerifyDrift(string label, JObject request, string path)
    {
        BindPreview(request);
        File.SetLastWriteTimeUtc(path, File.GetLastWriteTimeUtc(path).AddSeconds(3));
        RequireSuccess(WriteAnimationCurveTool.HandleCommand(request));

        BindPreview(request);
        var before = File.ReadAllBytes(path);
        var changed = (byte[])before.Clone();
        changed[changed.Length - 1] ^= 1;
        var timestamp = File.GetLastWriteTimeUtc(path);
        File.WriteAllBytes(path, changed);
        File.SetLastWriteTimeUtc(path, timestamp);
        try
        {
            RequireDriftRejection(request);
            if (!changed.SequenceEqual(File.ReadAllBytes(path)))
                throw new InvalidOperationException("The stale apply wrote to the clip.");
        }
        finally
        {
            File.WriteAllBytes(path, before);
        }

        BindPreview(request);
        var dirtyClip = AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
        EditorUtility.SetDirty(dirtyClip);
        RequireDriftRejection(request);
        AssetDatabase.SaveAssetIfDirty(dirtyClip);
        Debug.Log("VRCFORGE_ANIMATION_CURVE_ENTRY_OK " + label);
    }

    private static void RequireDriftRejection(JObject request)
    {
        var result = JObject.FromObject(WriteAnimationCurveTool.HandleCommand(request));
        if (result["IsSuccessful"]?.Value<bool>() != false
            || (string)result["Message"] != ChangedAfterPreview
            || result["Payload"]?["mutationStarted"]?.Value<bool>() != false
            || (string)result["Payload"]?["commitState"] != "not_started")
            throw new InvalidOperationException("Curve write did not report the expected pre-mutation rejection: " + result);
    }

    private static JObject RequireSuccess(object raw)
    {
        var result = JObject.FromObject(raw);
        if (result["IsSuccessful"]?.Value<bool>() != true)
            throw new InvalidOperationException("Curve write failed: " + result);
        return result;
    }
}
