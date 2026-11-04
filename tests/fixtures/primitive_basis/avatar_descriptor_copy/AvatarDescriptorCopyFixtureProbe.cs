using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;
using VRCForge.Core.MCP;
using VRCForge.Editor;

public static class AvatarDescriptorCopyFixtureProbe
{
    private static string FixtureFolder;
    private static string ScenePath;

    public static void Run()
    {
        try
        {
            PrepareFixture();
            VerifyCopyAndRelativeReferenceRemap();
            VerifyEyeLookOnlyCopyIgnoresUnrelatedVisemeReference();
            VerifyMissingReferencedTargetFailsBeforeMutation();
            VerifyScannerDescriptorConfigurationFacts();
            Cleanup();
            Debug.Log("VRCFORGE_AVATAR_DESCRIPTOR_COPY_PROBE_OK");
            EditorApplication.Exit(0);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            Cleanup();
            EditorApplication.Exit(1);
        }
    }

    private static void PrepareFixture()
    {
        var root = "Assets/VRCForgeGenerated/AvatarDescriptorCopyFixture_" + Guid.NewGuid().ToString("N");
        AssetDatabase.CreateFolder("Assets", "VRCForgeGenerated");
        FixtureFolder = root;
        AssetDatabase.CreateFolder("Assets/VRCForgeGenerated", Path.GetFileName(root));
        ScenePath = FixtureFolder + "/AvatarDescriptorCopyFixture.unity";
        var menu = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
        var parameters = ScriptableObject.CreateInstance<VRCExpressionParameters>();
        var controller = AnimatorController.CreateAnimatorControllerAtPath(FixtureFolder + "/Source.controller");
        AssetDatabase.CreateAsset(menu, FixtureFolder + "/Source.menu.asset");
        AssetDatabase.CreateAsset(parameters, FixtureFolder + "/Source.parameters.asset");

        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var source = NewAvatar("SourceAvatar", false);
        var target = NewAvatar("TargetAvatar", true);
        var missingTarget = NewAvatar("TargetMissing", false, omitFace: true);
        var eyeLookSource = NewAvatar("EyeLookSource", false, addEyeLookOnlyRenderer: true);
        NewAvatar("EmptyAvatar", false);
        var sourceDescriptor = source.GetComponent<VRCAvatarDescriptor>();
        var targetDescriptor = target.GetComponent<VRCAvatarDescriptor>();
        var missingDescriptor = missingTarget.GetComponent<VRCAvatarDescriptor>();
        sourceDescriptor.ViewPosition = new Vector3(0.1f, 1.7f, 0.2f);
        sourceDescriptor.expressionParameters = parameters;
        sourceDescriptor.expressionsMenu = menu;
        sourceDescriptor.baseAnimationLayers = new[]
        {
            new VRCAvatarDescriptor.CustomAnimLayer
            {
                type = VRCAvatarDescriptor.AnimLayerType.FX,
                isDefault = false,
                animatorController = controller
            }
        };
        sourceDescriptor.VisemeSkinnedMesh = source.transform.Find("Body/Face").GetComponent<SkinnedMeshRenderer>();
        sourceDescriptor.enableEyeLook = true;
        SetEyeLookMesh(sourceDescriptor, sourceDescriptor.VisemeSkinnedMesh);
        var eyeLookSourceDescriptor = eyeLookSource.GetComponent<VRCAvatarDescriptor>();
        eyeLookSourceDescriptor.VisemeSkinnedMesh = eyeLookSource.transform.Find("Body/EyeLookOnlyRenderer").GetComponent<SkinnedMeshRenderer>();
        eyeLookSourceDescriptor.enableEyeLook = true;
        SetEyeLookMesh(eyeLookSourceDescriptor, eyeLookSource.transform.Find("Body/Face").GetComponent<SkinnedMeshRenderer>());
        Require(EditorSceneManager.SaveScene(scene, ScenePath), "fixture scene save");
        AssetDatabase.SaveAssets();
    }

    private static GameObject NewAvatar(
        string name,
        bool insertUnrelatedSibling,
        bool omitFace = false,
        bool addEyeLookOnlyRenderer = false)
    {
        var root = new GameObject(name);
        root.AddComponent<VRCAvatarDescriptor>();
        if (insertUnrelatedSibling)
        {
            var unrelated = new GameObject("UnrelatedSibling");
            unrelated.transform.SetParent(root.transform, false);
        }
        var body = new GameObject("Body");
        body.transform.SetParent(root.transform, false);
        if (!omitFace)
        {
            var face = new GameObject("Face");
            face.transform.SetParent(body.transform, false);
            face.AddComponent<SkinnedMeshRenderer>();
        }
        if (addEyeLookOnlyRenderer)
        {
            var extra = new GameObject("EyeLookOnlyRenderer");
            extra.transform.SetParent(body.transform, false);
            extra.AddComponent<SkinnedMeshRenderer>();
        }
        return root;
    }

    private static void VerifyCopyAndRelativeReferenceRemap()
    {
        var source = GameObject.Find("SourceAvatar").GetComponent<VRCAvatarDescriptor>();
        var target = GameObject.Find("TargetAvatar").GetComponent<VRCAvatarDescriptor>();
        var sourceJson = EditorJsonUtility.ToJson(source);
        var sourceRenderer = source.VisemeSkinnedMesh;
        var targetRenderer = target.transform.Find("Body/Face").GetComponent<SkinnedMeshRenderer>();
        var result = WriteAvatarDescriptorTool.HandleCommand(new JObject
        {
            ["avatarPath"] = "TargetAvatar",
            ["sourceDescriptorAvatarPath"] = "SourceAvatar"
        }) as VRCForgeToolResult;
        Require(result != null && result.IsSuccessful, "descriptor copy tool failed: " + result?.Message);
        Require(target.gameObject.name == "TargetAvatar", "CopySerialized changed the target component host");
        Require(target.ViewPosition == source.ViewPosition, "viewpoint was not copied");
        Require(target.expressionParameters == source.expressionParameters, "expression parameters asset reference was not retained");
        Require(target.expressionsMenu == source.expressionsMenu, "expression menu asset reference was not retained");
        Require(target.baseAnimationLayers.Length == 1
            && target.baseAnimationLayers[0].animatorController == source.baseAnimationLayers[0].animatorController,
            "playable layer asset reference was not retained");
        Require(target.VisemeSkinnedMesh == targetRenderer, "viseme renderer was not remapped by relative hierarchy path");
        Require(target.VisemeSkinnedMesh != sourceRenderer, "viseme renderer still references the source avatar");
        Require(target.enableEyeLook, "eye-look enable state was not copied");
        Require(GetEyeLookMesh(target) == targetRenderer, "eye-look renderer was not remapped");
        Require(EditorJsonUtility.ToJson(source) == sourceJson, "source descriptor changed during copy");
    }

    private static void VerifyEyeLookOnlyCopyIgnoresUnrelatedVisemeReference()
    {
        var target = GameObject.Find("TargetAvatar").GetComponent<VRCAvatarDescriptor>();
        var source = GameObject.Find("EyeLookSource").GetComponent<VRCAvatarDescriptor>();
        var targetRenderer = target.transform.Find("Body/Face").GetComponent<SkinnedMeshRenderer>();
        var preservedViseme = target.VisemeSkinnedMesh;
        var preservedViewPosition = target.ViewPosition;
        var result = WriteAvatarDescriptorTool.HandleCommand(new JObject
        {
            ["avatarPath"] = "TargetAvatar",
            ["eyeLookSettingsSourceAvatarPath"] = "EyeLookSource"
        }) as VRCForgeToolResult;
        Require(result != null && result.IsSuccessful, "eye-look-only copy failed: " + result?.Message);
        Require(target.VisemeSkinnedMesh == preservedViseme, "eye-look-only copy changed the target viseme renderer");
        Require(target.ViewPosition == preservedViewPosition, "eye-look-only copy changed unrelated descriptor settings");
        Require(source.VisemeSkinnedMesh.transform.name == "EyeLookOnlyRenderer", "eye-look-only source fixture precondition");
        Require(GetEyeLookMesh(target) == targetRenderer, "eye-look-only copy did not remap the eye renderer");
    }

    private static void VerifyMissingReferencedTargetFailsBeforeMutation()
    {
        var source = GameObject.Find("SourceAvatar").GetComponent<VRCAvatarDescriptor>();
        var target = GameObject.Find("TargetMissing").GetComponent<VRCAvatarDescriptor>();
        var before = EditorJsonUtility.ToJson(target);
        var result = WriteAvatarDescriptorTool.HandleCommand(new JObject
        {
            ["avatarPath"] = "TargetMissing",
            ["sourceDescriptorAvatarPath"] = "SourceAvatar"
        }) as VRCForgeToolResult;
        Require(result != null && !result.IsSuccessful, "missing referenced target was accepted");
        Require(result.Message.IndexOf("descriptor reference", StringComparison.OrdinalIgnoreCase) >= 0,
            "missing-reference failure did not identify the descriptor reference");
        Require(EditorJsonUtility.ToJson(target) == before, "missing-reference rejection mutated target descriptor");
        Require(source.VisemeSkinnedMesh != null, "source reference was unexpectedly lost");
    }

    private static void VerifyScannerDescriptorConfigurationFacts()
    {
        var empty = AvatarControlScanner.HandleCommand(new JObject { ["avatarPath"] = "EmptyAvatar" }) as VRCForgeToolResult;
        var configured = AvatarControlScanner.HandleCommand(new JObject { ["avatarPath"] = "TargetAvatar" }) as VRCForgeToolResult;
        Require(empty != null && empty.IsSuccessful, "empty-avatar scanner failed: " + empty?.Message);
        Require(configured != null && configured.IsSuccessful, "configured-avatar scanner failed: " + configured?.Message);
        var emptyConfiguration = JObject.FromObject(empty.Payload)["descriptorConfiguration"] as JObject;
        var configuredConfiguration = JObject.FromObject(configured.Payload)["descriptorConfiguration"] as JObject;
        Require(emptyConfiguration?.Value<bool>("descriptorPresent") == true, "empty avatar descriptor presence was not reported");
        Require(emptyConfiguration?.Value<bool?>("expressionsMenuAssigned") == false, "empty menu assignment was not reported");
        Require(emptyConfiguration?.Value<bool?>("expressionParametersAssigned") == false, "empty parameters assignment was not reported");
        Require(configuredConfiguration?.Value<bool>("descriptorPresent") == true, "configured avatar descriptor presence was not reported");
        Require(configuredConfiguration?.Value<bool?>("expressionsMenuAssigned") == true, "configured menu assignment was not reported");
        Require(configuredConfiguration?.Value<bool?>("expressionParametersAssigned") == true, "configured parameters assignment was not reported");
    }

    private static void SetEyeLookMesh(VRCAvatarDescriptor descriptor, SkinnedMeshRenderer renderer)
    {
        var serialized = new SerializedObject(descriptor);
        var property = serialized.FindProperty("customEyeLookSettings.eyelidsSkinnedMesh");
        Require(property != null, "SDK eye-look renderer property was not found");
        property.objectReferenceValue = renderer;
        serialized.ApplyModifiedProperties();
    }

    private static SkinnedMeshRenderer GetEyeLookMesh(VRCAvatarDescriptor descriptor)
    {
        var serialized = new SerializedObject(descriptor);
        return serialized.FindProperty("customEyeLookSettings.eyelidsSkinnedMesh")?.objectReferenceValue as SkinnedMeshRenderer;
    }

    private static void Cleanup()
    {
        if (EditorSceneManager.GetActiveScene().IsValid())
        {
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        }
        if (!string.IsNullOrWhiteSpace(FixtureFolder) && AssetDatabase.IsValidFolder(FixtureFolder))
        {
            AssetDatabase.DeleteAsset(FixtureFolder);
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }
}
