using System;
using System.Collections.Generic;
using System.Linq;
using VRCForge.Core.MCP;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_apply_parameter_optimization",
        Summary = "Apply selected VRCExpressionParameters type optimizations via a predefined VRCForge tool."
    )]
    public static class AvatarParameterOptimizationApplier
    {
        public class Parameters
        {
            [VRCForgeInput("Optional avatar root hierarchy path; empty is allowed only when selection is unambiguous.", IsRequired = false)]
            public string avatarPath { get; set; } = "";
            [VRCForgeInput("One or more parameter suggestions; each entry requires the existing parameter name.", IsRequired = true)]
            public JArray suggestions { get; set; } = new JArray();
        }

        public static object HandleCommand(JObject @params)
        {
            WriteAnimationCurveTool.AssetEditRecovery recovery = null;
            bool mutationStarted = false;
            try
            {
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var suggestions = @params?["suggestions"] as JArray;
                if (suggestions == null || suggestions.Count == 0)
                {
                    return VRCForgeToolResult.Failed("Missing required parameter: suggestions");
                }

                var descriptor = ResolveAvatarDescriptor(avatarPath);
                var parametersAsset = descriptor.expressionParameters;
                if (parametersAsset == null || parametersAsset.parameters == null)
                {
                    return VRCForgeToolResult.Failed("Avatar has no VRCExpressionParameters asset.");
                }

                if (EditorUtility.IsDirty(parametersAsset))
                    throw new InvalidOperationException("Save or discard pending edits to the parameter asset before optimization.");
                var persistedBefore = ExpressionWritePersistence.Capture(parametersAsset);
                var parameters = parametersAsset.parameters;
                var requestedNames = ValidateRequestedParameterNames(
                    suggestions
                    .OfType<JObject>()
                    .Select(item => (item["name"]?.ToString() ?? string.Empty).Trim())
                    .ToList(),
                    parameters
                        .Where(parameter => parameter != null)
                        .Select(parameter => parameter.name));
                if (requestedNames.Count != suggestions.Count)
                {
                    throw new InvalidOperationException("Every optimization suggestion must be an object with an exact parameter name.");
                }
                var applied = new List<object>();
                var before = new List<object>();

                recovery = new WriteAnimationCurveTool.AssetEditRecovery();
                recovery.Capture(AssetDatabase.GetAssetPath(parametersAsset));
                recovery.Begin();
                Undo.RegisterCompleteObjectUndo(parametersAsset, "VRCForge optimize parameters");
                mutationStarted = true;
                for (var i = 0; i < parameters.Length; i++)
                {
                    var parameter = parameters[i];
                    if (parameter == null || !requestedNames.Contains(parameter.name, StringComparer.Ordinal))
                    {
                        continue;
                    }

                    var previousType = parameter.valueType.ToString();
                    before.Add(DescribeParameter(parameter));
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
                var persistence = ExpressionWritePersistence.SaveAndVerify(
                    persistedBefore, parametersAsset, descriptor, false, false);
                var assetPath = AssetDatabase.GetAssetPath(parametersAsset);
                var readbackAsset = AssetDatabase.LoadAssetAtPath<VRCExpressionParameters>(assetPath);
                if (readbackAsset == null || readbackAsset.parameters == null)
                {
                    throw new InvalidOperationException("Parameter optimization asset readback failed.");
                }
                var after = readbackAsset.parameters
                    .Where(parameter => parameter != null && requestedNames.Contains(parameter.name, StringComparer.Ordinal))
                    .Select(DescribeParameter)
                    .ToList();

                recovery.Complete();
                mutationStarted = false;
                return VRCForgeToolResult.Completed(
                    $"Applied {applied.Count} parameter optimization(s).",
                    new
                    {
                        ok = true,
                        appliedCount = applied.Count,
                        applied,
                        assetPath,
                        persistence,
                        before,
                        after,
                        affected = new
                        {
                            count = after.Count,
                            items = after.Take(20).ToArray(),
                            handle = AssetDatabase.AssetPathToGUID(assetPath)
                        }
                    });
            }
            catch (Exception ex)
            {
                var restored = !mutationStarted || (recovery != null && recovery.Restore());
                return VRCForgeToolResult.Failed($"Parameter optimization apply failed: {ex.Message}\nFailure compensation: {(restored ? "restored" : "incomplete; preserve current state for recovery")}\n{ex.StackTrace}");
            }
        }

        private static object DescribeParameter(VRCExpressionParameters.Parameter parameter)
        {
            return new
            {
                name = parameter.name,
                valueType = parameter.valueType.ToString(),
                parameter.defaultValue,
                parameter.saved,
                parameter.networkSynced
            };
        }

        internal static List<string> ValidateRequestedParameterNames(
            IEnumerable<string> requestedNames,
            IEnumerable<string> parameterNames)
        {
            var requested = requestedNames?.ToList() ?? new List<string>();
            var available = parameterNames?.Where(name => name != null).ToList() ?? new List<string>();
            if (requested.Any(string.IsNullOrWhiteSpace))
            {
                throw new InvalidOperationException("Every optimization suggestion requires an exact parameter name.");
            }
            if (requested.Count != requested.Distinct(StringComparer.Ordinal).Count())
            {
                throw new InvalidOperationException("Optimization suggestions contain a duplicate parameter identity.");
            }
            foreach (var requestedName in requested)
            {
                var exactMatches = available.Count(name => name == requestedName);
                if (exactMatches == 1)
                {
                    continue;
                }
                var caseInsensitiveMatches = available.Count(name => string.Equals(name, requestedName, StringComparison.OrdinalIgnoreCase));
                throw new InvalidOperationException(exactMatches > 1 || caseInsensitiveMatches > 1
                    ? $"Parameter identity is ambiguous: {requestedName}"
                    : $"Exact parameter not found: {requestedName}");
            }
            return requested;
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
            var matches = string.IsNullOrEmpty(normalizedAvatarPath)
                ? descriptors
                : descriptors.Where(item => NormalizePath(GetTransformPath(item.transform)) == normalizedAvatarPath).ToList();
            if (matches.Count == 0 && !normalizedAvatarPath.Contains("/"))
                matches = descriptors.Where(item => item.name.Equals(avatarPath, StringComparison.OrdinalIgnoreCase)).ToList();
            if (matches.Count != 1)
                throw new InvalidOperationException(matches.Count > 1
                    ? $"Avatar descriptor is ambiguous: {avatarPath}. Provide an exact unique hierarchy path."
                    : $"Avatar descriptor not found: {avatarPath}");
            return matches[0];
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

    [VRCForgeCommand(
        toolId: "vrc_rollback_avatar_parameters",
        Summary = "Restore VRCExpressionParameters from a dashboard snapshot via a predefined VRCForge tool."
    )]
    public static class AvatarParameterRollbackTool
    {
        public class Parameters
        {
            [VRCForgeInput("Optional avatar root hierarchy path; empty is allowed only when selection is unambiguous.", IsRequired = false)]
            public string avatarPath { get; set; } = "";
            [VRCForgeInput("Snapshot parameter entries. Each entry supports name, valueType, defaultValue, saved, and networkSynced.", IsRequired = true)]
            public JArray parameterNames { get; set; } = new JArray();
        }

        public static object HandleCommand(JObject @params)
        {
            WriteAnimationCurveTool.AssetEditRecovery recovery = null;
            bool mutationStarted = false;
            try
            {
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var parameterItems = @params?["parameterNames"] as JArray;
                if (parameterItems == null)
                {
                    return VRCForgeToolResult.Failed("Missing required parameter: parameterNames");
                }

                var descriptor = ResolveAvatarDescriptor(avatarPath);
                var parametersAsset = descriptor.expressionParameters;
                if (parametersAsset == null)
                {
                    return VRCForgeToolResult.Failed("Avatar has no VRCExpressionParameters asset.");
                }
                if (EditorUtility.IsDirty(parametersAsset))
                    throw new InvalidOperationException("Save or discard pending edits to the parameter asset before restoration.");
                var persistedBefore = ExpressionWritePersistence.Capture(parametersAsset);
                var beforeParameters = (parametersAsset.parameters ?? Array.Empty<VRCExpressionParameters.Parameter>())
                    .Where(parameter => parameter != null)
                    .ToArray();
                var before = beforeParameters.Select(DescribeParameter).ToList();

                var restored = new List<VRCExpressionParameters.Parameter>();
                var names = new HashSet<string>(StringComparer.Ordinal);
                foreach (var rawItem in parameterItems)
                {
                    var item = rawItem as JObject
                        ?? throw new InvalidOperationException("Every snapshot parameter must be an object.");
                    var name = (item["name"]?.ToString() ?? string.Empty).Trim();
                    if (string.IsNullOrWhiteSpace(name) || !names.Add(name))
                        throw new InvalidOperationException("Snapshot parameter name is missing or duplicated.");

                    restored.Add(new VRCExpressionParameters.Parameter
                    {
                        name = name,
                        valueType = ParseValueType(item["valueType"]?.ToString()),
                        defaultValue = item["defaultValue"]?.Value<float?>() ?? 0f,
                        saved = item["saved"]?.Value<bool?>() ?? true,
                        networkSynced = item["networkSynced"]?.Value<bool?>() ?? true
                    });
                }

                recovery = new WriteAnimationCurveTool.AssetEditRecovery();
                recovery.Capture(AssetDatabase.GetAssetPath(parametersAsset));
                recovery.Begin();
                Undo.RegisterCompleteObjectUndo(parametersAsset, "VRCForge restore parameters");
                mutationStarted = true;
                parametersAsset.parameters = restored.ToArray();
                var persistence = ExpressionWritePersistence.SaveAndVerify(
                    persistedBefore, parametersAsset, descriptor, false, false);
                var assetPath = AssetDatabase.GetAssetPath(parametersAsset);
                var readbackAsset = AssetDatabase.LoadAssetAtPath<VRCExpressionParameters>(assetPath);
                if (readbackAsset == null || readbackAsset.parameters == null)
                {
                    throw new InvalidOperationException("Parameter rollback asset readback failed.");
                }
                var after = readbackAsset.parameters
                    .Where(parameter => parameter != null)
                    .Select(DescribeParameter)
                    .ToList();
                var affectedNames = beforeParameters
                    .Concat(readbackAsset.parameters.Where(parameter => parameter != null))
                    .Select(parameter => parameter.name)
                    .Where(name => !string.IsNullOrWhiteSpace(name))
                    .Distinct(StringComparer.Ordinal)
                    .OrderBy(name => name, StringComparer.Ordinal)
                    .ToArray();

                recovery.Complete();
                mutationStarted = false;
                return VRCForgeToolResult.Completed(
                    $"Restored {restored.Count} avatar parameter(s).",
                    new
                    {
                        ok = true,
                        restoredCount = restored.Count,
                        assetPath,
                        persistence,
                        before,
                        after,
                        affected = new
                        {
                            count = affectedNames.Length,
                            items = affectedNames.Take(20).ToArray(),
                            handle = AssetDatabase.AssetPathToGUID(assetPath)
                        }
                    });
            }
            catch (Exception ex)
            {
                var restored = !mutationStarted || (recovery != null && recovery.Restore());
                return VRCForgeToolResult.Failed($"Parameter rollback failed: {ex.Message}\nFailure compensation: {(restored ? "restored" : "incomplete; preserve current state for recovery")}\n{ex.StackTrace}");
            }
        }

        private static object DescribeParameter(VRCExpressionParameters.Parameter parameter)
        {
            return new
            {
                name = parameter.name,
                valueType = parameter.valueType.ToString(),
                parameter.defaultValue,
                parameter.saved,
                parameter.networkSynced
            };
        }

        private static VRCExpressionParameters.ValueType ParseValueType(string value)
        {
            if (Enum.TryParse(value, true, out VRCExpressionParameters.ValueType parsed)
                && Enum.IsDefined(typeof(VRCExpressionParameters.ValueType), parsed))
            {
                return parsed;
            }

            throw new InvalidOperationException("Snapshot parameter valueType is invalid.");
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
            var matches = string.IsNullOrEmpty(normalizedAvatarPath)
                ? descriptors
                : descriptors.Where(item => NormalizePath(GetTransformPath(item.transform)) == normalizedAvatarPath).ToList();
            if (matches.Count == 0 && !normalizedAvatarPath.Contains("/"))
                matches = descriptors.Where(item => item.name.Equals(avatarPath, StringComparison.OrdinalIgnoreCase)).ToList();
            if (matches.Count != 1)
                throw new InvalidOperationException(matches.Count > 1
                    ? $"Avatar descriptor is ambiguous: {avatarPath}. Provide an exact unique hierarchy path."
                    : $"Avatar descriptor not found: {avatarPath}");
            return matches[0];
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
