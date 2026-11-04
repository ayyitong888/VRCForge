using System;
using System.IO;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;
using VRCForge.Core.MCP;
using VRCForge.Editor;

public static class AvatarReferenceDiagnosticsFixtureProbe
{
    private static string FixtureFolder;
    private static string ScenePath;
    private static string AvatarName = "ReferenceDiagnosticsAvatar";

    public static void Run()
    {
        try
        {
            PrepareFixture();
            VerifyDiagnosticsFindBrokenRefsAndMissingScripts();
            Cleanup();
            Debug.Log("VRCFORGE_AVATAR_REFERENCE_DIAGNOSTICS_PROBE_OK");
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
        if (!AssetDatabase.IsValidFolder("Assets/VRCForgeGenerated"))
            AssetDatabase.CreateFolder("Assets", "VRCForgeGenerated");
        FixtureFolder = "Assets/VRCForgeGenerated/AvatarReferenceDiagnosticsFixture_" + Guid.NewGuid().ToString("N");
        AssetDatabase.CreateFolder("Assets/VRCForgeGenerated", Path.GetFileName(FixtureFolder));
        ScenePath = FixtureFolder + "/AvatarReferenceDiagnosticsFixture.unity";

        var referencedAsset = new AnimationClip();
        AssetDatabase.CreateAsset(referencedAsset, FixtureFolder + "/Referenced.asset");
        var parameters = ScriptableObject.CreateInstance<VRCExpressionParameters>();
        parameters.parameters = new[]
        {
            new VRCExpressionParameters.Parameter
            {
                name = "DeclaredMenuParameter",
                valueType = VRCExpressionParameters.ValueType.Bool,
                defaultValue = 0f,
                saved = false,
                networkSynced = false
            }
        };
        AssetDatabase.CreateAsset(parameters, FixtureFolder + "/ExpressionParameters.asset");
        var menu = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
        menu.controls = new List<VRCExpressionsMenu.Control>
        {
            new VRCExpressionsMenu.Control
            {
                name = "Declared Control",
                type = VRCExpressionsMenu.Control.ControlType.Toggle,
                parameter = new VRCExpressionsMenu.Control.Parameter { name = "DeclaredMenuParameter" }
            },
            new VRCExpressionsMenu.Control
            {
                name = "Undeclared Control",
                type = VRCExpressionsMenu.Control.ControlType.Toggle,
                parameter = new VRCExpressionsMenu.Control.Parameter { name = "NotInParametersAsset" }
            }
        };
        AssetDatabase.CreateAsset(menu, FixtureFolder + "/ExpressionsMenu.asset");
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var avatar = new GameObject(AvatarName);
        var descriptor = avatar.AddComponent<VRCAvatarDescriptor>();
        var unconfigured = UnityAvatarReferenceDiagnostics.HandleCommand(new JObject { ["avatarPath"] = AvatarName }) as VRCForgeToolResult;
        var emptyMenu = JObject.FromObject(unconfigured.Payload)["menuParameterEvidence"];
        Require(emptyMenu.Value<bool?>("expressionsMenuAssigned") == false, "unassigned expression menu fact");
        Require(emptyMenu.Value<bool?>("expressionParametersAssigned") == false, "unassigned expression parameters fact");
        descriptor.expressionParameters = parameters;
        descriptor.expressionsMenu = menu;
        var inactiveChild = new GameObject("InactiveChild");
        inactiveChild.transform.SetParent(avatar.transform, false);
        inactiveChild.SetActive(false);
        var component = inactiveChild.AddComponent<AvatarReferenceDiagnosticsFixtureComponent>();
        var missingScriptComponent = inactiveChild.AddComponent<AvatarReferenceDiagnosticsMissingScriptFixtureComponent>();
        component.missingAssetReference = referencedAsset;
        Require(EditorSceneManager.SaveScene(scene, ScenePath), "fixture scene save");
        AssetDatabase.SaveAssets();

        var script = MonoScript.FromMonoBehaviour(missingScriptComponent);
        var scriptPath = AssetDatabase.GetAssetPath(script);
        var scriptGuid = AssetDatabase.AssetPathToGUID(scriptPath);
        var assetGuid = AssetDatabase.AssetPathToGUID(FixtureFolder + "/Referenced.asset");
        Require(!string.IsNullOrEmpty(scriptGuid) && !string.IsNullOrEmpty(assetGuid), "fixture serialized identities");

        var sceneYaml = File.ReadAllText(ScenePath);
        Require(sceneYaml.Contains("guid: " + scriptGuid), "fixture script identity is present in scene yaml");
        Require(sceneYaml.Contains("guid: " + assetGuid), "fixture asset identity is present in scene yaml");
        sceneYaml = sceneYaml.Replace("guid: " + scriptGuid, "guid: ffffffffffffffffffffffffffffffff");
        sceneYaml = sceneYaml.Replace("guid: " + assetGuid, "guid: eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee");
        File.WriteAllText(ScenePath, sceneYaml);
        EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        AssetDatabase.ImportAsset(ScenePath, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
        Require(EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single).IsValid(), "fixture scene reopen");
    }

    private static void VerifyDiagnosticsFindBrokenRefsAndMissingScripts()
    {
        var result = UnityAvatarReferenceDiagnostics.HandleCommand(new JObject { ["avatarPath"] = AvatarName }) as VRCForgeToolResult;
        Require(result != null && result.IsSuccessful, "diagnostic tool failed: " + result?.Message);
        var payload = JObject.FromObject(result.Payload);
        Require(payload.Value<string>("schema") == "vrcforge.avatar_reference_diagnostics.v1", "diagnostic schema");
        Require(payload.Value<bool>("readOnly") && payload.Value<bool>("mutationStarted") == false, "read-only receipt facts");
        Require(payload.Value<string>("proofStatus") == "complete", "hierarchy scan completeness");
        Require(payload.Value<int>("scannedGameObjectCount") == 2, "inactive child included in scan");
        Require(payload.Value<int>("missingScriptCount") == 1, "Unity missing-script counter was not reported");
        Require(payload.Value<int>("missingObjectReferenceCount") == 1, "serialized missing-reference signal was not reported");

        var findings = payload["findings"] as JArray;
        Require(findings != null, "findings array");
        var missingScript = false;
        var missingReference = false;
        foreach (var finding in findings)
        {
            var row = (JObject)finding;
            if (row.Value<string>("kind") == "missing_script_component")
                missingScript = row.Value<string>("gameObjectPath").EndsWith("InactiveChild", StringComparison.Ordinal);
            if (row.Value<string>("kind") == "serialized_missing_object_reference")
                missingReference = row.Value<string>("serializedPropertyPath") == "missingAssetReference"
                    && row.Value<string>("gameObjectPath").EndsWith("InactiveChild", StringComparison.Ordinal);
            Require(row.Value<string>("serializedPropertyPath") != "intentionallyUnassignedReference", "ordinary null field was falsely reported");
        }
        Require(missingScript, "missing script was not located at the inactive child");
        Require(missingReference, "broken serialized asset reference was not located at its field");

        var menu = payload["menuParameterEvidence"] as JObject;
        Require(menu != null && menu.Value<string>("status") == "available", "reused menu/parameter scanner evidence");
        Require(menu.Value<bool?>("expressionsMenuAssigned") == true, "assigned expression menu fact");
        Require(menu.Value<bool?>("expressionParametersAssigned") == true, "assigned expression parameters fact");
        var menuControls = menu["controls"] as JArray;
        Require(menuControls != null && menuControls.Count == 2, "both menu parameter cases were returned");
        var declared = FindControl(menuControls, "DeclaredMenuParameter");
        var undeclared = FindControl(menuControls, "NotInParametersAsset");
        Require(declared != null && declared.Value<bool>("explicitlyDeclaredInExpressionParameters"),
            "declared menu parameter was not recognized from the full parameter asset");
        Require(undeclared != null && !undeclared.Value<bool>("explicitlyDeclaredInExpressionParameters"),
            "undeclared menu name was not represented as a distinct fact");
        Require(undeclared.Value<string>("interpretation").Contains("may be an SDK built-in"),
            "undeclared menu name was treated as invalid instead of uncertain evidence");
        Require(menu.Value<int>("unmatchedCount") == 1, "only the undeclared menu name should count as unmatched evidence");
    }

    private static JObject FindControl(JArray controls, string parameterName)
    {
        foreach (var token in controls)
        {
            var control = token as JObject;
            if (control != null && control.Value<string>("parameterName") == parameterName) return control;
        }
        return null;
    }

    private static void Cleanup()
    {
        if (EditorSceneManager.GetActiveScene().IsValid())
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
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
