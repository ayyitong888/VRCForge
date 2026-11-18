using System;
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;
using VRCForge.Core.MCP;
using VRCForge.Editor;

// In a disposable Unity project with the source Core and VRChat SDK, copy to Assets/VRCForge/Editor.
// Run Unity in batch mode with -executeMethod WardrobeScopeAudit.Run; set
// VRCFORGE_WARDROBE_AUDIT_CASE to "dirty" (reuse) or "new" (create).
internal static class WardrobeScopeAudit
{
    public static void Run()
    {
        var scenario = Environment.GetEnvironmentVariable("VRCFORGE_WARDROBE_AUDIT_CASE");
        Require(Application.isBatchMode && (scenario == "dirty" || scenario == "new"), "Select a batch fixture case.");
        var reply = (JObject)typeof(VRCForgeMcpCoreServer)
            .GetMethod("HandleMessage", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] {
                new JObject { ["jsonrpc"] = "2.0", ["id"] = 1, ["method"] = "server/core-info" }, null, null
            });
        Debug.Log("WARDROBE_AUDIT_CORE_INFO " + reply.ToString(Newtonsoft.Json.Formatting.None));
        var info = reply["result"];
        Require((string)info["coreIdentity"] == VRCForgeMcpToolContract.CoreIdentity
            && (string)info["coreVersion"] == VRCForgeMcpToolContract.ProductVersion
            && (int)info["compileSnapshot"]["errorCount"] == 0, "Isolated Core identity or compile result is wrong.");

        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var avatar = new GameObject("Avatar");
        var descriptor = avatar.AddComponent<VRCAvatarDescriptor>();
        var part = new GameObject("Hat");
        part.transform.SetParent(avatar.transform);
        var controller = AnimatorController.CreateAnimatorControllerAtPath(Unique("Assets/WardrobeAuditFX.controller"));
        controller.AddParameter("Wardrobe", AnimatorControllerParameterType.Int);
        var machine = controller.layers[0].stateMachine;
        machine.AddAnyStateTransition(machine.AddState("Idle")).AddCondition(AnimatorConditionMode.Equals, 1, "Wardrobe");
        descriptor.baseAnimationLayers = new[] {
            new VRCAvatarDescriptor.CustomAnimLayer { type = VRCAvatarDescriptor.AnimLayerType.FX, animatorController = controller }
        };
        var expressions = ScriptableObject.CreateInstance<VRCExpressionParameters>();
        expressions.parameters = new[] {
            new VRCExpressionParameters.Parameter { name = "Wardrobe", valueType = VRCExpressionParameters.ValueType.Int },
            new VRCExpressionParameters.Parameter { name = "Hat", valueType = VRCExpressionParameters.ValueType.Bool }
        };
        var parametersPath = Unique("Assets/WardrobeAuditParameters.asset");
        AssetDatabase.CreateAsset(expressions, parametersPath);
        descriptor.expressionParameters = expressions;
        AssetDatabase.SaveAssets();
        Require(EditorSceneManager.SaveScene(scene, Unique("Assets/WardrobeScopeAudit.unity")), "Fixture save failed.");
        var originalParameters = File.ReadAllBytes(parametersPath);
        var reuse = scenario == "dirty";
        if (reuse)
        {
            expressions.parameters[1].defaultValue = 1;
            EditorUtility.SetDirty(expressions);
        }

        var partParameter = reuse ? "Hat" : "FreshHat";
        var result = (VRCForgeToolResult)WardrobeOutfitPartWriter.HandleCommand(new JObject {
            ["avatarPath"] = "Avatar", ["parameterName"] = "Wardrobe", ["partName"] = "Hat",
            ["partParameterName"] = partParameter, ["value"] = 1, ["objectPaths"] = new JArray("Hat"),
            ["writeDefaults"] = false, ["addMenuToggle"] = false, ["setObjectsDefaultOff"] = false
        });
        Require(result.IsSuccessful, result.Message);
        var readback = JObject.FromObject(result.Payload)["readback"];
        if (reuse)
        {
            Require(EditorUtility.IsDirty(expressions) && expressions.parameters[1].defaultValue == 1
                && File.ReadAllBytes(parametersPath).SequenceEqual(originalParameters)
                && !readback["parameters"].Any(), "Reused expression asset was saved, reloaded or claimed in the receipt.");
        }
        else
        {
            Require((string)readback["parameters"].Single() == parametersPath
                && AssetDatabase.LoadAssetAtPath<VRCExpressionParameters>(parametersPath).parameters.Any(p =>
                    p.name == partParameter && p.valueType == VRCExpressionParameters.ValueType.Bool),
                "New expression parameter lacked persisted evidence.");
        }
        Debug.Log("WARDROBE_AUDIT_PASS " + scenario);
    }

    private static string Unique(string path) => AssetDatabase.GenerateUniqueAssetPath(path);

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }
}
