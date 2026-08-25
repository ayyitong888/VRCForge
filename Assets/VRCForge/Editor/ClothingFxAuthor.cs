using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using VRCForge.Core.MCP;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_apply_clothing_fx",
        Summary = "Author simple clothing toggle FX assets via a predefined VRCForge tool."
    )]
    public static class ClothingFxAuthor
    {
        private const string AssetDir = "Assets/VRCForge/Generated/FX";

        public class Parameters
        {
            [VRCForgeInput("Optional avatar root hierarchy path; empty is allowed only when selection is unambiguous.", IsRequired = false)]
            public string avatarPath { get; set; } = "";
            [VRCForgeInput("One or more clothing items. Each item may specify displayName/name, parameterName, animationClipName, and sampleObjectPath/objectPath.", IsRequired = true)]
            public JArray items { get; set; } = new JArray();
        }

        public static object HandleCommand(JObject @params)
        {
            var receipts = new List<TransactionReceipt>();
            try
            {
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var items = @params?["items"] as JArray;
                if (items == null || items.Count == 0)
                {
                    return VRCForgeToolResult.Failed("Missing required parameter: items");
                }

                var descriptor = ResolveAvatarDescriptor(avatarPath);
                var fxController = descriptor.baseAnimationLayers
                    .FirstOrDefault(layer => layer.type == VRCAvatarDescriptor.AnimLayerType.FX)
                    .animatorController as AnimatorController;
                if (fxController == null)
                {
                    return VRCForgeToolResult.Failed("No FX AnimatorController found on the avatar.");
                }

                var parametersAsset = descriptor.expressionParameters;
                if (parametersAsset == null)
                {
                    return VRCForgeToolResult.Failed("No VRCExpressionParameters found on the avatar.");
                }

                var menuAsset = descriptor.expressionsMenu;
                if (menuAsset == null)
                {
                    return VRCForgeToolResult.Failed("No VRCExpressionsMenu found on the avatar.");
                }
                var fxControllerPath = AssetDatabase.GetAssetPath(fxController);
                var parametersPath = AssetDatabase.GetAssetPath(parametersAsset);
                var menuPath = AssetDatabase.GetAssetPath(menuAsset);
                var controllerReceipt = new TransactionReceipt { Asset = fxControllerPath, Before = DescribeAsset(fxController) };
                var parametersReceipt = new TransactionReceipt { Asset = parametersPath, Before = DescribeAsset(parametersAsset) };
                var menuReceipt = new TransactionReceipt { Asset = menuPath, Before = DescribeAsset(menuAsset) };
                receipts.Add(controllerReceipt);
                receipts.Add(parametersReceipt);
                receipts.Add(menuReceipt);

                EnsureAssetFolder(AssetDir);
                var created = new List<object>();
                var skipped = new List<object>();
                foreach (var item in items.OfType<JObject>())
                {
                    var displayName = FirstNonEmpty(item, "displayName", "name");
                    var paramName = FirstNonEmpty(item, "parameterName");
                    var clipName = FirstNonEmpty(item, "animationClipName");
                    var objectPath = FirstNonEmpty(item, "sampleObjectPath", "objectPath");
                    if (string.IsNullOrWhiteSpace(displayName))
                    {
                        displayName = "Clothing";
                    }
                    if (string.IsNullOrWhiteSpace(paramName))
                    {
                        paramName = "Cloth_" + SanitizeName(displayName);
                    }
                    if (string.IsNullOrWhiteSpace(clipName))
                    {
                        clipName = "FX_" + SanitizeName(displayName) + "_Toggle";
                    }
                    if (string.IsNullOrWhiteSpace(objectPath))
                    {
                        skipped.Add(new { displayName, parameterName = paramName, reason = "No scene object path; existing menu/parameter controls do not need new active-state clips." });
                        continue;
                    }

                    var clipOnPath = $"{AssetDir}/{clipName}_ON.anim";
                    var clipOffPath = $"{AssetDir}/{clipName}_OFF.anim";
                    var clipOnReceipt = new TransactionReceipt { Asset = clipOnPath, Before = DescribeAsset(AssetDatabase.LoadAssetAtPath<AnimationClip>(clipOnPath)) };
                    var clipOffReceipt = new TransactionReceipt { Asset = clipOffPath, Before = DescribeAsset(AssetDatabase.LoadAssetAtPath<AnimationClip>(clipOffPath)) };
                    receipts.Add(clipOnReceipt);
                    receipts.Add(clipOffReceipt);
                    var clipOn = LoadOrCreateClip(clipOnPath, clipName + "_ON");
                    var clipOff = LoadOrCreateClip(clipOffPath, clipName + "_OFF");
                    var binding = new EditorCurveBinding { path = objectPath, type = typeof(GameObject), propertyName = "m_IsActive" };
                    AnimationUtility.SetEditorCurve(clipOn, binding, AnimationCurve.Constant(0f, 0f, 1f));
                    AnimationUtility.SetEditorCurve(clipOff, binding, AnimationCurve.Constant(0f, 0f, 0f));
                    clipOnReceipt.After = DescribeAsset(clipOn);
                    clipOnReceipt.Status = "succeeded";
                    clipOffReceipt.After = DescribeAsset(clipOff);
                    clipOffReceipt.Status = "succeeded";

                    EnsureFxLayer(fxController, displayName, paramName, clipOn, clipOff);
                    EnsureExpressionParameter(parametersAsset, paramName);
                    EnsureMenuToggle(menuAsset, displayName, paramName);
                    controllerReceipt.After = DescribeAsset(fxController);
                    controllerReceipt.Status = "succeeded";
                    parametersReceipt.After = DescribeAsset(parametersAsset);
                    parametersReceipt.Status = "succeeded";
                    menuReceipt.After = DescribeAsset(menuAsset);
                    menuReceipt.Status = "succeeded";
                    created.Add(new { displayName, parameterName = paramName, sampleObjectPath = objectPath });
                }

                EditorUtility.SetDirty(fxController);
                EditorUtility.SetDirty(parametersAsset);
                EditorUtility.SetDirty(menuAsset);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();
                controllerReceipt.After = DescribeAsset(AssetDatabase.LoadAssetAtPath<AnimatorController>(fxControllerPath));
                parametersReceipt.After = DescribeAsset(AssetDatabase.LoadAssetAtPath<VRCExpressionParameters>(parametersPath));
                menuReceipt.After = DescribeAsset(AssetDatabase.LoadAssetAtPath<VRCExpressionsMenu>(menuPath));

                return VRCForgeToolResult.Completed(
                    $"Authored {created.Count} clothing FX item(s).",
                    new
                    {
                        ok = true,
                        createdCount = created.Count,
                        skippedCount = skipped.Count,
                        created,
                        skipped,
                        assetDir = AssetDir,
                        transaction = BuildTransaction(receipts)
                    });
            }
            catch (Exception ex)
            {
                var failed = receipts.FirstOrDefault(item => item.Status == "not_attempted");
                if (failed != null)
                {
                    failed.Status = "failed";
                    failed.Error = ex.Message;
                }
                return VRCForgeToolResult.Failed(
                    $"Clothing FX authoring failed: {ex.Message}\n{ex.StackTrace}",
                    new { transaction = BuildTransaction(receipts) });
            }
        }

        private static object DescribeAsset(UnityEngine.Object asset)
        {
            return asset == null
                ? (object)new { exists = false }
                : new
                {
                    exists = true,
                    assetPath = AssetDatabase.GetAssetPath(asset),
                    value = JToken.Parse(EditorJsonUtility.ToJson(asset))
                };
        }

        private static object BuildTransaction(List<TransactionReceipt> receipts)
        {
            var transactionItems = receipts
                .Where(item => item.Status != "not_attempted")
                .Select(item => new
                {
                    asset = item.Asset,
                    before = item.Before,
                    after = item.After,
                    status = item.Status,
                    error = item.Error,
                    rolled_back = item.RolledBack
                })
                .ToList();
            return new
            {
                assets_touched = transactionItems.Count,
                items = transactionItems.Take(20).ToArray(),
                handle = AssetDir
            };
        }

        private sealed class TransactionReceipt
        {
            public string Asset = "";
            public object Before;
            public object After;
            public string Status = "not_attempted";
            public string Error = "";
            public bool RolledBack = false;
        }

        private static AnimationClip LoadOrCreateClip(string path, string clipName)
        {
            var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
            if (clip == null)
            {
                clip = new AnimationClip { name = clipName };
                AssetDatabase.CreateAsset(clip, path);
            }
            else
            {
                clip.name = clipName;
            }

            return clip;
        }

        private static void EnsureAssetFolder(string assetPath)
        {
            var normalized = assetPath.Replace("\\", "/").Trim('/');
            var parts = normalized.Split('/');
            if (parts.Length == 0 || parts[0] != "Assets")
            {
                throw new InvalidOperationException($"Generated asset folder must be under Assets: {assetPath}");
            }

            var current = "Assets";
            for (var index = 1; index < parts.Length; index++)
            {
                var next = $"{current}/{parts[index]}";
                if (!AssetDatabase.IsValidFolder(next))
                {
                    AssetDatabase.CreateFolder(current, parts[index]);
                }
                current = next;
            }
        }

        private static void EnsureFxLayer(AnimatorController controller, string displayName, string paramName, AnimationClip clipOn, AnimationClip clipOff)
        {
            if (!controller.parameters.Any(parameter => parameter.name == paramName))
            {
                controller.AddParameter(paramName, AnimatorControllerParameterType.Bool);
            }
            if (controller.layers.Any(layer => layer.name == paramName))
            {
                return;
            }

            controller.AddLayer(paramName);
            var layers = controller.layers;
            var layer = layers[layers.Length - 1];
            layer.defaultWeight = 1f;
            var stateMachine = layer.stateMachine;
            var stateOn = stateMachine.AddState(displayName + "_ON");
            var stateOff = stateMachine.AddState(displayName + "_OFF");
            stateOn.motion = clipOn;
            stateOff.motion = clipOff;

            var transitionOn = stateMachine.AddAnyStateTransition(stateOn);
            transitionOn.hasExitTime = false;
            transitionOn.duration = 0f;
            transitionOn.AddCondition(AnimatorConditionMode.If, 0f, paramName);

            var transitionOff = stateMachine.AddAnyStateTransition(stateOff);
            transitionOff.hasExitTime = false;
            transitionOff.duration = 0f;
            transitionOff.AddCondition(AnimatorConditionMode.IfNot, 0f, paramName);
            controller.layers = layers;
        }

        private static void EnsureExpressionParameter(VRCExpressionParameters asset, string paramName)
        {
            var list = asset.parameters?.ToList() ?? new List<VRCExpressionParameters.Parameter>();
            if (list.Any(parameter => parameter.name == paramName))
            {
                return;
            }

            list.Add(new VRCExpressionParameters.Parameter
            {
                name = paramName,
                valueType = VRCExpressionParameters.ValueType.Bool,
                defaultValue = 1f,
                saved = true,
                networkSynced = true
            });
            asset.parameters = list.ToArray();
        }

        private static void EnsureMenuToggle(VRCExpressionsMenu menu, string displayName, string paramName)
        {
            if (menu.controls != null && menu.controls.Any(control => control.parameter != null && control.parameter.name == paramName))
            {
                return;
            }
            if (menu.controls == null || menu.controls.Count >= VRCExpressionsMenu.MAX_CONTROLS)
            {
                return;
            }

            menu.controls.Add(new VRCExpressionsMenu.Control
            {
                name = displayName,
                type = VRCExpressionsMenu.Control.ControlType.Toggle,
                parameter = new VRCExpressionsMenu.Control.Parameter { name = paramName },
                value = 1f
            });
        }

        private static VRCAvatarDescriptor ResolveAvatarDescriptor(string avatarPath)
        {
            var descriptors = Resources.FindObjectsOfTypeAll<VRCAvatarDescriptor>()
                .Where(item => item != null && item.gameObject.scene.IsValid() && item.gameObject.scene.isLoaded && !EditorUtility.IsPersistent(item))
                .OrderBy(item => item.name)
                .ToList();
            if (descriptors.Count == 0)
            {
                throw new InvalidOperationException("No scene VRChat avatar descriptor was found.");
            }

            var normalizedAvatarPath = NormalizePath(avatarPath);
            if (string.IsNullOrEmpty(normalizedAvatarPath))
            {
                return descriptors[0];
            }

            return descriptors.FirstOrDefault(item => NormalizePath(GetTransformPath(item.transform)) == normalizedAvatarPath)
                ?? descriptors.FirstOrDefault(item => item.name.Equals(avatarPath, StringComparison.OrdinalIgnoreCase))
                ?? throw new InvalidOperationException($"Avatar descriptor not found: {avatarPath}");
        }

        private static string FirstNonEmpty(JObject item, params string[] keys)
        {
            foreach (var key in keys)
            {
                var value = (item[key]?.ToString() ?? string.Empty).Trim();
                if (!string.IsNullOrWhiteSpace(value))
                {
                    return value;
                }
            }

            return string.Empty;
        }

        private static string SanitizeName(string value)
        {
            return new string((value ?? string.Empty).Where(char.IsLetterOrDigit).ToArray());
        }

        private static string GetTransformPath(Transform transform)
        {
            var segments = new Stack<string>();
            var current = transform;
            while (current != null)
            {
                segments.Push(current.name);
                current = current.parent;
            }

            return string.Join("/", segments);
        }

        private static string NormalizePath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim().Trim('/');
        }
    }
}
