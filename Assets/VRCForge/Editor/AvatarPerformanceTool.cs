using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_scan_avatar_performance",
        Description = "Calculate VRChat SDK avatar performance statistics and ranking for a scene avatar (read-only)."
    )]
    public static class AvatarPerformanceTool
    {
        public const string ToolName = "vrc_scan_avatar_performance";

        public class AvatarPerformanceParameters
        {
            [ToolParameter("Avatar root hierarchy path or avatar name.", Required = false)]
            public string avatarPath { get; set; } = "";

            [ToolParameter("Calculate ratings against mobile (Quest/Android) limits instead of PC limits.", Required = false)]
            public bool? isMobile { get; set; } = false;

            [ToolParameter("Optional JSON output path for the full report.", Required = false)]
            public string outputPath { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<AvatarPerformanceParameters>()
                ?? new AvatarPerformanceParameters();

            try
            {
                var payload = CalculatePerformance(parameters);
                var jsonPath = WriteJsonIfRequested(parameters.outputPath, payload);
                if (!string.IsNullOrWhiteSpace(jsonPath))
                {
                    payload["jsonPath"] = jsonPath;
                }

                return new SuccessResponse(
                    $"Avatar performance: {payload["overallRating"]} ({payload["avatarName"]}).",
                    payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Avatar performance scan failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static JObject CalculatePerformance(AvatarPerformanceParameters parameters)
        {
            var statsType = FindType("VRC.SDKBase.Validation.Performance.AvatarPerformanceStats")
                ?? throw new InvalidOperationException("VRC SDK AvatarPerformanceStats type was not found.");
            var perfType = FindType("VRC.SDKBase.Validation.Performance.AvatarPerformance")
                ?? throw new InvalidOperationException("VRC SDK AvatarPerformance type was not found.");
            var categoryType = FindType("VRC.SDKBase.Validation.Performance.AvatarPerformanceCategory");

            var descriptor = ResolveAvatarDescriptor(parameters.avatarPath ?? "");
            var avatarObject = descriptor.gameObject;
            var isMobile = parameters.isMobile == true;

            var stats = CreateStats(statsType, isMobile);
            InvokeCalculate(perfType, statsType, descriptor.name, avatarObject, stats, isMobile);

            var ratings = ReadCategoryRatings(stats, statsType, categoryType);
            var rawStats = ReadStatFields(stats, statsType);

            var overall = ratings.TryGetValue("Overall", out var overallRating)
                ? overallRating
                : ratings.Values.FirstOrDefault() ?? "Unknown";

            return new JObject
            {
                ["avatarPath"] = GetTransformPath(descriptor.transform),
                ["avatarName"] = descriptor.name,
                ["isMobile"] = isMobile,
                ["overallRating"] = overall,
                ["ratings"] = JObject.FromObject(ratings),
                ["stats"] = JObject.FromObject(rawStats),
            };
        }

        private static object CreateStats(Type statsType, bool isMobile)
        {
            var boolCtor = statsType.GetConstructor(new[] { typeof(bool) });
            if (boolCtor != null)
            {
                return boolCtor.Invoke(new object[] { isMobile });
            }

            return Activator.CreateInstance(statsType);
        }

        private static void InvokeCalculate(Type perfType, Type statsType, string avatarName, GameObject avatarObject, object stats, bool isMobile)
        {
            var candidates = perfType
                .GetMethods(BindingFlags.Public | BindingFlags.Static)
                .Where(method => method.Name == "CalculatePerformanceStats")
                .OrderByDescending(method => method.GetParameters().Length)
                .ToList();
            if (candidates.Count == 0)
            {
                throw new InvalidOperationException("AvatarPerformance.CalculatePerformanceStats was not found.");
            }

            foreach (var method in candidates)
            {
                var methodParams = method.GetParameters();
                var args = new object[methodParams.Length];
                var supported = true;
                for (var index = 0; index < methodParams.Length; index++)
                {
                    var paramType = methodParams[index].ParameterType;
                    if (paramType == typeof(string))
                    {
                        args[index] = avatarName;
                    }
                    else if (paramType == typeof(GameObject))
                    {
                        args[index] = avatarObject;
                    }
                    else if (paramType.IsAssignableFrom(statsType))
                    {
                        args[index] = stats;
                    }
                    else if (paramType == typeof(bool))
                    {
                        args[index] = isMobile;
                    }
                    else
                    {
                        supported = false;
                        break;
                    }
                }

                if (!supported)
                {
                    continue;
                }

                method.Invoke(null, args);
                return;
            }

            throw new InvalidOperationException(
                "No compatible CalculatePerformanceStats overload was found for this VRChat SDK version.");
        }

        private static Dictionary<string, string> ReadCategoryRatings(object stats, Type statsType, Type categoryType)
        {
            var ratings = new Dictionary<string, string>();
            if (categoryType == null)
            {
                return ratings;
            }

            var ratingMethod = statsType.GetMethod("GetPerformanceRatingForCategory", new[] { categoryType });
            if (ratingMethod == null)
            {
                return ratings;
            }

            foreach (var category in Enum.GetValues(categoryType))
            {
                var name = category.ToString();
                if (name == "None" || name == "AvatarPerformanceCategoryCount")
                {
                    continue;
                }

                try
                {
                    var rating = ratingMethod.Invoke(stats, new[] { category });
                    ratings[name] = rating?.ToString() ?? "Unknown";
                }
                catch
                {
                    // Skip categories the current SDK cannot rate.
                }
            }

            return ratings;
        }

        private static Dictionary<string, object> ReadStatFields(object stats, Type statsType)
        {
            var values = new Dictionary<string, object>();
            foreach (var fieldInfo in statsType.GetFields(BindingFlags.Public | BindingFlags.Instance))
            {
                try
                {
                    var value = fieldInfo.GetValue(stats);
                    if (value == null)
                    {
                        continue;
                    }

                    var valueType = Nullable.GetUnderlyingType(value.GetType()) ?? value.GetType();
                    if (valueType.IsPrimitive || valueType == typeof(string) || valueType.IsEnum || valueType == typeof(decimal))
                    {
                        values[fieldInfo.Name] = valueType.IsEnum ? value.ToString() : value;
                    }
                }
                catch
                {
                    // Skip unreadable fields.
                }
            }

            return values;
        }

        private static Component ResolveAvatarDescriptor(string avatarPath)
        {
            var descriptorType = FindType("VRC.SDK3.Avatars.Components.VRCAvatarDescriptor")
                ?? throw new InvalidOperationException("VRC SDK avatar descriptor type was not found.");
            var descriptors = Resources.FindObjectsOfTypeAll(descriptorType)
                .OfType<Component>()
                .Where(component => component != null
                    && component.gameObject != null
                    && component.gameObject.scene.IsValid()
                    && !EditorUtility.IsPersistent(component.gameObject))
                .OrderBy(item => item.name)
                .ToList();
            if (descriptors.Count == 0)
            {
                throw new InvalidOperationException("No scene VRChat avatar descriptor was found.");
            }

            var normalized = NormalizePath(avatarPath);
            if (string.IsNullOrEmpty(normalized))
            {
                return descriptors[0];
            }

            var match = descriptors.FirstOrDefault(item => NormalizePath(GetTransformPath(item.transform)) == normalized)
                ?? descriptors.FirstOrDefault(item => item.name.Equals(avatarPath, StringComparison.OrdinalIgnoreCase));
            if (match == null)
            {
                throw new InvalidOperationException($"Avatar descriptor not found: {avatarPath}");
            }

            return match;
        }

        private static string WriteJsonIfRequested(string outputPath, JObject payload)
        {
            var trimmed = (outputPath ?? string.Empty).Trim();
            if (string.IsNullOrEmpty(trimmed))
            {
                return string.Empty;
            }

            var fullPath = Path.IsPathRooted(trimmed)
                ? trimmed
                : Path.GetFullPath(Path.Combine(Application.dataPath, "..", trimmed));
            Directory.CreateDirectory(Path.GetDirectoryName(fullPath) ?? ".");
            File.WriteAllText(fullPath, payload.ToString(Formatting.Indented), new UTF8Encoding(false));
            return fullPath.Replace("\\", "/");
        }

        private static string GetTransformPath(Transform transform)
        {
            if (transform == null)
            {
                return string.Empty;
            }

            var segments = new List<string>();
            var current = transform;
            while (current != null)
            {
                segments.Insert(0, current.name);
                current = current.parent;
            }

            return string.Join("/", segments);
        }

        private static string NormalizePath(string path)
        {
            return (path ?? string.Empty).Trim().Trim('/').Replace("\\", "/");
        }

        private static Type FindType(string fullName)
        {
            return AppDomain.CurrentDomain.GetAssemblies()
                .Select(assembly =>
                {
                    try
                    {
                        return assembly.GetType(fullName, false);
                    }
                    catch
                    {
                        return null;
                    }
                })
                .FirstOrDefault(type => type != null);
        }
    }
}
