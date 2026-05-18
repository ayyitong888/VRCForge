using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_apply_parameter_optimization",
        Description = "Apply selected VRCExpressionParameters type optimizations via a predefined VRCForge tool."
    )]
    public static class AvatarParameterOptimizationApplier
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var suggestions = @params?["suggestions"] as JArray;
                if (suggestions == null || suggestions.Count == 0)
                {
                    return new ErrorResponse("Missing required parameter: suggestions");
                }

                var descriptor = ResolveAvatarDescriptor(avatarPath);
                var parametersAsset = descriptor.expressionParameters;
                if (parametersAsset == null || parametersAsset.parameters == null)
                {
                    return new ErrorResponse("Avatar has no VRCExpressionParameters asset.");
                }

                var requestedNames = suggestions
                    .OfType<JObject>()
                    .Select(item => (item["name"]?.ToString() ?? string.Empty).Trim())
                    .Where(name => !string.IsNullOrWhiteSpace(name))
                    .ToHashSet(StringComparer.OrdinalIgnoreCase);
                var parameters = parametersAsset.parameters;
                var applied = new List<object>();

                for (var i = 0; i < parameters.Length; i++)
                {
                    var parameter = parameters[i];
                    if (parameter == null || !requestedNames.Contains(parameter.name))
                    {
                        continue;
                    }

                    var previousType = parameter.valueType.ToString();
                    parameter.valueType = VRCExpressionParameters.ValueType.Bool;
                    parameters[i] = parameter;
                    applied.Add(new
                    {
                        name = parameter.name,
                        from = previousType,
                        to = parameter.valueType.ToString()
                    });
                }

                parametersAsset.parameters = parameters;
                EditorUtility.SetDirty(parametersAsset);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();

                return new SuccessResponse(
                    $"Applied {applied.Count} parameter optimization(s).",
                    new
                    {
                        ok = true,
                        appliedCount = applied.Count,
                        applied,
                        assetPath = AssetDatabase.GetAssetPath(parametersAsset)
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Parameter optimization apply failed: {ex.Message}\n{ex.StackTrace}");
            }
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

    [McpForUnityTool(
        name: "vrc_rollback_avatar_parameters",
        Description = "Restore VRCExpressionParameters from a dashboard snapshot via a predefined VRCForge tool."
    )]
    public static class AvatarParameterRollbackTool
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var parameterItems = @params?["parameterNames"] as JArray;
                if (parameterItems == null)
                {
                    return new ErrorResponse("Missing required parameter: parameterNames");
                }

                var descriptor = ResolveAvatarDescriptor(avatarPath);
                var parametersAsset = descriptor.expressionParameters;
                if (parametersAsset == null)
                {
                    return new ErrorResponse("Avatar has no VRCExpressionParameters asset.");
                }

                var restored = new List<VRCExpressionParameters.Parameter>();
                foreach (var item in parameterItems.OfType<JObject>())
                {
                    var name = (item["name"]?.ToString() ?? string.Empty).Trim();
                    if (string.IsNullOrWhiteSpace(name))
                    {
                        continue;
                    }

                    restored.Add(new VRCExpressionParameters.Parameter
                    {
                        name = name,
                        valueType = ParseValueType(item["valueType"]?.ToString()),
                        defaultValue = item["defaultValue"]?.Value<float?>() ?? 0f,
                        saved = item["saved"]?.Value<bool?>() ?? true,
                        networkSynced = item["networkSynced"]?.Value<bool?>() ?? true
                    });
                }

                parametersAsset.parameters = restored.ToArray();
                EditorUtility.SetDirty(parametersAsset);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();

                return new SuccessResponse(
                    $"Restored {restored.Count} avatar parameter(s).",
                    new
                    {
                        ok = true,
                        restoredCount = restored.Count,
                        assetPath = AssetDatabase.GetAssetPath(parametersAsset)
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Parameter rollback failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static VRCExpressionParameters.ValueType ParseValueType(string value)
        {
            if (Enum.TryParse(value, true, out VRCExpressionParameters.ValueType parsed))
            {
                return parsed;
            }

            return VRCExpressionParameters.ValueType.Bool;
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
