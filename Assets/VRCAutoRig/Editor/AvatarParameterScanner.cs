using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCAutoRig.Editor
{
    [McpForUnityTool(
        name: "vrc_scan_avatar_parameters",
        Description = "Read VRChat avatar expression parameters and build simple optimization suggestions without requiring Roslyn."
    )]
    public static class AvatarParameterScanner
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var descriptor = ResolveAvatarDescriptor(avatarPath);
                var parameters = ReadParameters(descriptor);
                var suggestions = BuildSuggestions(parameters);
                var boolCount = parameters.Count(item => string.Equals(item.valueType, "Bool", StringComparison.OrdinalIgnoreCase));
                var intCount = parameters.Count(item => string.Equals(item.valueType, "Int", StringComparison.OrdinalIgnoreCase));
                var floatCount = parameters.Count(item => string.Equals(item.valueType, "Float", StringComparison.OrdinalIgnoreCase));
                var totalCost = parameters.Sum(item =>
                    string.Equals(item.valueType, "Bool", StringComparison.OrdinalIgnoreCase) ? 1 : 8);
                var outputPath = (@params?["outputPath"]?.ToString() ?? string.Empty).Trim();
                var payload = new
                {
                    avatarPath = GetTransformPath(descriptor.transform),
                    avatarName = descriptor.name,
                    boolCount,
                    intCount,
                    floatCount,
                    totalParameters = parameters.Count,
                    totalEstimatedCost = totalCost,
                    parameterNames = parameters,
                    suggestionCount = suggestions.Count,
                    suggestions,
                    note = "Suggestions are heuristic only. Review animator conditions and menu bindings before changing parameter types."
                };
                var jsonPath = WriteJsonIfRequested(outputPath, payload);
                var response = JObject.FromObject(payload);
                if (!string.IsNullOrWhiteSpace(jsonPath))
                {
                    response["jsonPath"] = jsonPath;
                }

                return new SuccessResponse(
                    $"Scanned {parameters.Count} avatar parameter(s).",
                    response);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Avatar parameter scan failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static List<ParameterItem> ReadParameters(Component descriptor)
        {
            var asset = GetMemberValue(descriptor, "expressionParameters");
            var parameters = GetMemberValue(asset, "parameters") as IEnumerable;
            var result = new List<ParameterItem>();
            if (parameters == null)
            {
                return result;
            }

            foreach (var parameter in parameters)
            {
                var name = Convert.ToString(GetMemberValue(parameter, "name"), CultureInfo.InvariantCulture) ?? string.Empty;
                if (string.IsNullOrWhiteSpace(name))
                {
                    continue;
                }

                result.Add(new ParameterItem
                {
                    name = name,
                    valueType = Convert.ToString(GetMemberValue(parameter, "valueType"), CultureInfo.InvariantCulture) ?? "",
                    saved = ToBool(GetMemberValue(parameter, "saved")),
                    networkSynced = ToBool(GetMemberValue(parameter, "networkSynced")),
                    defaultValue = ToFloat(GetMemberValue(parameter, "defaultValue"))
                });
            }

            return result;
        }

        private static List<SuggestionItem> BuildSuggestions(List<ParameterItem> parameters)
        {
            return parameters
                .Where(parameter => string.Equals(parameter.valueType, "Int", StringComparison.OrdinalIgnoreCase))
                .Where(parameter =>
                    Math.Abs(parameter.defaultValue) <= 1.0f
                    || ContainsAny(parameter.name, "toggle", "enable", "show", "hide", "onoff", "switch")
                    || parameter.name.StartsWith("is", StringComparison.OrdinalIgnoreCase))
                .Select(parameter => new SuggestionItem
                {
                    name = parameter.name,
                    currentType = parameter.valueType,
                    suggestedType = "Bool",
                    reason = "Heuristic match: this Int parameter looks binary and may be reducible to Bool."
                })
                .ToList();
        }

        private static bool ContainsAny(string value, params string[] keywords)
        {
            return keywords.Any(keyword => value.IndexOf(keyword, StringComparison.OrdinalIgnoreCase) >= 0);
        }

        private static Component ResolveAvatarDescriptor(string avatarPath)
        {
            var descriptorType = FindType("VRC.SDK3.Avatars.Components.VRCAvatarDescriptor")
                ?? throw new InvalidOperationException("VRC SDK avatar descriptor type was not found.");
            var descriptors = Resources.FindObjectsOfTypeAll(descriptorType)
                .OfType<Component>()
                .Where(IsSceneComponent)
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

            var match = descriptors.FirstOrDefault(item => NormalizePath(GetTransformPath(item.transform)) == normalizedAvatarPath)
                ?? descriptors.FirstOrDefault(item => item.name.Equals(avatarPath, StringComparison.OrdinalIgnoreCase));
            if (match == null)
            {
                throw new InvalidOperationException($"Avatar descriptor not found: {avatarPath}");
            }

            return match;
        }

        private static bool IsSceneComponent(Component component)
        {
            return component != null
                && component.gameObject.scene.IsValid()
                && component.gameObject.scene.isLoaded
                && !EditorUtility.IsPersistent(component);
        }

        private static object GetMemberValue(object source, string name)
        {
            if (source == null)
            {
                return null;
            }

            var flags = System.Reflection.BindingFlags.Instance
                | System.Reflection.BindingFlags.Public
                | System.Reflection.BindingFlags.NonPublic;
            var type = source.GetType();
            var field = type.GetField(name, flags);
            if (field != null)
            {
                return field.GetValue(source);
            }

            var property = type.GetProperty(name, flags);
            return property != null ? property.GetValue(source) : null;
        }

        private static Type FindType(string fullName)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    var type = assembly.GetType(fullName, false);
                    if (type != null)
                    {
                        return type;
                    }
                }
                catch
                {
                    // Ignore transient reflection failures from editor reloads.
                }
            }

            return null;
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

        private static string WriteJsonIfRequested(string outputPath, object payload)
        {
            if (string.IsNullOrWhiteSpace(outputPath))
            {
                return "";
            }

            var absolutePath = ResolveToAbsolutePath(outputPath);
            var directory = Path.GetDirectoryName(absolutePath);
            if (!string.IsNullOrEmpty(directory))
            {
                Directory.CreateDirectory(directory);
            }

            File.WriteAllText(
                absolutePath,
                JsonConvert.SerializeObject(payload, Formatting.Indented),
                Encoding.UTF8);
            AssetDatabase.Refresh();
            return absolutePath.Replace("\\", "/");
        }

        private static string ResolveToAbsolutePath(string requestedPath)
        {
            if (Path.IsPathRooted(requestedPath))
            {
                return requestedPath.Replace("\\", "/");
            }

            var projectRoot = Directory.GetParent(Application.dataPath)?.FullName
                ?? throw new InvalidOperationException("Cannot determine Unity project root.");
            return Path.Combine(projectRoot, requestedPath).Replace("\\", "/");
        }

        private static float ToFloat(object value)
        {
            if (value == null)
            {
                return 0f;
            }

            try
            {
                return Convert.ToSingle(value, CultureInfo.InvariantCulture);
            }
            catch
            {
                return 0f;
            }
        }

        private static bool ToBool(object value)
        {
            if (value is bool boolValue)
            {
                return boolValue;
            }

            return string.Equals(Convert.ToString(value, CultureInfo.InvariantCulture), "true", StringComparison.OrdinalIgnoreCase);
        }

        private class ParameterItem
        {
            public string name;
            public string valueType;
            public bool saved;
            public bool networkSynced;
            public float defaultValue;
        }

        private class SuggestionItem
        {
            public string name;
            public string currentType;
            public string suggestedType;
            public string reason;
        }
    }
}
