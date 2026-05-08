using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;

namespace VRCAutoRig.Editor
{
    [McpForUnityTool(
        name: "vrc_apply_clothing_fx",
        Description = "Author simple clothing toggle FX assets without requiring Roslyn."
    )]
    public static class ClothingFxAuthor
    {
        private const string AssetDir = "Assets/VRCAutoRig/Generated/FX";

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var items = @params?["items"] as JArray;
                if (items == null || items.Count == 0)
                {
                    return new ErrorResponse("Missing required parameter: items");
                }

                var descriptor = ResolveAvatarDescriptor(avatarPath);
                var fxController = descriptor.baseAnimationLayers
                    .FirstOrDefault(layer => layer.type == VRCAvatarDescriptor.AnimLayerType.FX)
                    .animatorController as AnimatorController;
                if (fxController == null)
                {
                    return new ErrorResponse("No FX AnimatorController found on the avatar.");
                }

                var parametersAsset = descriptor.expressionParameters;
                if (parametersAsset == null)
                {
                    return new ErrorResponse("No VRCExpressionParameters found on the avatar.");
                }

                var menuAsset = descriptor.expressionsMenu;
                if (menuAsset == null)
                {
                    return new ErrorResponse("No VRCExpressionsMenu found on the avatar.");
                }

                Directory.CreateDirectory(AssetDir);
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

                    var clipOn = LoadOrCreateClip($"{AssetDir}/{clipName}_ON.anim", clipName + "_ON");
                    var clipOff = LoadOrCreateClip($"{AssetDir}/{clipName}_OFF.anim", clipName + "_OFF");
                    var binding = new EditorCurveBinding { path = objectPath, type = typeof(GameObject), propertyName = "m_IsActive" };
                    AnimationUtility.SetEditorCurve(clipOn, binding, AnimationCurve.Constant(0f, 0f, 1f));
                    AnimationUtility.SetEditorCurve(clipOff, binding, AnimationCurve.Constant(0f, 0f, 0f));

                    EnsureFxLayer(fxController, displayName, paramName, clipOn, clipOff);
                    EnsureExpressionParameter(parametersAsset, paramName);
                    EnsureMenuToggle(menuAsset, displayName, paramName);
                    created.Add(new { displayName, parameterName = paramName, sampleObjectPath = objectPath });
                }

                EditorUtility.SetDirty(fxController);
                EditorUtility.SetDirty(parametersAsset);
                EditorUtility.SetDirty(menuAsset);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();

                return new SuccessResponse(
                    $"Authored {created.Count} clothing FX item(s).",
                    new
                    {
                        ok = true,
                        createdCount = created.Count,
                        skippedCount = skipped.Count,
                        created,
                        skipped,
                        assetDir = AssetDir
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Clothing FX authoring failed: {ex.Message}\n{ex.StackTrace}");
            }
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
