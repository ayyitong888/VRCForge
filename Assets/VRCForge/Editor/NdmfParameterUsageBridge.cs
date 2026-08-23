using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using UnityEngine;

namespace VRCForge.Editor
{
    /// <summary>
    /// Optional, reflection-only adapter over NDMF's public parameter
    /// introspection API. VRCForge remains self-contained when NDMF/Modular
    /// Avatar is not installed, while using the same calculation as MA
    /// Information when it is available.
    /// </summary>
    internal static class NdmfParameterUsageBridge
    {
        private const string ParameterInfoTypeName = "nadena.dev.ndmf.ParameterInfo";

        internal static NdmfParameterUsageSnapshot Inspect(GameObject avatarRoot)
        {
            var snapshot = new NdmfParameterUsageSnapshot
            {
                available = false,
                inspectionStage = "ndmf_parameter_introspection",
                parameterNames = new List<NdmfParameterUsageItem>(),
                pluginBreakdown = new List<NdmfParameterPluginUsage>()
            };

            if (avatarRoot == null)
            {
                snapshot.unavailableReason = "Avatar root is required.";
                return snapshot;
            }

            try
            {
                var parameterInfoType = FindType(ParameterInfoTypeName);
                if (parameterInfoType == null)
                {
                    snapshot.unavailableReason = "NDMF ParameterInfo is not installed or has not loaded.";
                    return snapshot;
                }

                // Equivalent public entrypoint used by MA Information:
                // ParameterInfo.ForUI.GetParametersForObject(avatarRoot)
                var forUiField = parameterInfoType.GetField(
                    "ForUI",
                    BindingFlags.Public | BindingFlags.Static);
                var parameterInfo = forUiField != null ? forUiField.GetValue(null) : null;
                if (parameterInfo == null)
                {
                    snapshot.unavailableReason = "NDMF ParameterInfo.ForUI is unavailable.";
                    return snapshot;
                }

                var getParameters = parameterInfoType
                    .GetMethods(BindingFlags.Public | BindingFlags.Instance)
                    .Where(method => string.Equals(method.Name, "GetParametersForObject", StringComparison.Ordinal))
                    .FirstOrDefault(method =>
                    {
                        var arguments = method.GetParameters();
                        return arguments.Length == 2 && arguments[0].ParameterType == typeof(GameObject);
                    });
                if (getParameters == null)
                {
                    snapshot.unavailableReason = "NDMF GetParametersForObject API was not found.";
                    return snapshot;
                }

                var enumerable = getParameters.Invoke(parameterInfo, new object[] { avatarRoot, null }) as IEnumerable;
                if (enumerable == null)
                {
                    snapshot.unavailableReason = "NDMF parameter introspection returned no enumerable result.";
                    return snapshot;
                }

                foreach (var parameter in enumerable)
                {
                    if (parameter == null)
                    {
                        continue;
                    }

                    var source = GetMemberValue(parameter, "Source") as Component;
                    var plugin = GetMemberValue(parameter, "Plugin");
                    var item = new NdmfParameterUsageItem
                    {
                        name = ToText(GetMemberValue(parameter, "EffectiveName")),
                        originalName = ToText(GetMemberValue(parameter, "OriginalName")),
                        parameterNamespace = ToText(GetMemberValue(parameter, "Namespace")),
                        parameterType = ToText(GetMemberValue(parameter, "ParameterType")),
                        bitUsage = ToInt(GetMemberValue(parameter, "BitUsage")),
                        wantSynced = ToBool(GetMemberValue(parameter, "WantSynced")),
                        animatorOnly = ToBool(GetMemberValue(parameter, "IsAnimatorOnly")),
                        hidden = ToBool(GetMemberValue(parameter, "IsHidden")),
                        plugin = ReadPluginName(plugin),
                        sourceComponentType = source != null ? source.GetType().FullName : string.Empty,
                        sourcePath = source != null ? GetTransformPath(source.transform) : string.Empty
                    };
                    snapshot.parameterNames.Add(item);
                }

                snapshot.available = true;
                snapshot.totalParameters = snapshot.parameterNames.Count;
                snapshot.totalBitUsage = snapshot.parameterNames.Sum(item => item.bitUsage);
                snapshot.syncedParameterCount = snapshot.parameterNames.Count(item => item.bitUsage > 0);
                snapshot.animatorOnlyParameterCount = snapshot.parameterNames.Count(item => item.animatorOnly);
                snapshot.hiddenParameterCount = snapshot.parameterNames.Count(item => item.hidden);
                snapshot.pluginBreakdown = snapshot.parameterNames
                    .GroupBy(item => string.IsNullOrWhiteSpace(item.plugin) ? "Unknown provider" : item.plugin)
                    .Select(group => new NdmfParameterPluginUsage
                    {
                        plugin = group.Key,
                        parameterCount = group.Count(),
                        syncedParameterCount = group.Count(item => item.bitUsage > 0),
                        bitUsage = group.Sum(item => item.bitUsage)
                    })
                    .OrderBy(item => item.plugin, StringComparer.Ordinal)
                    .ToList();
                return snapshot;
            }
            catch (TargetInvocationException ex)
            {
                var cause = ex.InnerException ?? ex;
                snapshot.unavailableReason = cause.GetType().Name + ": " + cause.Message;
                return snapshot;
            }
            catch (Exception ex)
            {
                snapshot.unavailableReason = ex.GetType().Name + ": " + ex.Message;
                return snapshot;
            }
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
                    // A package assembly can be transiently unavailable during reload.
                }
            }

            return null;
        }

        private static object GetMemberValue(object source, string name)
        {
            if (source == null)
            {
                return null;
            }

            var flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
            var type = source.GetType();
            var field = type.GetField(name, flags);
            if (field != null)
            {
                return field.GetValue(source);
            }

            var property = type.GetProperty(name, flags);
            return property != null ? property.GetValue(source, null) : null;
        }

        private static string ReadPluginName(object plugin)
        {
            if (plugin == null)
            {
                return "Unknown provider";
            }

            var displayName = ToText(GetMemberValue(plugin, "DisplayName"));
            return string.IsNullOrWhiteSpace(displayName) ? plugin.GetType().FullName : displayName;
        }

        private static string ToText(object value)
        {
            return Convert.ToString(value, CultureInfo.InvariantCulture) ?? string.Empty;
        }

        private static int ToInt(object value)
        {
            try
            {
                return value == null ? 0 : Convert.ToInt32(value, CultureInfo.InvariantCulture);
            }
            catch
            {
                return 0;
            }
        }

        private static bool ToBool(object value)
        {
            if (value is bool)
            {
                return (bool)value;
            }

            return string.Equals(ToText(value), "true", StringComparison.OrdinalIgnoreCase);
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

            return string.Join("/", segments.ToArray());
        }
    }

    [Serializable]
    internal sealed class NdmfParameterUsageSnapshot
    {
        public bool available;
        public string inspectionStage;
        public string unavailableReason;
        public int totalParameters;
        public int totalBitUsage;
        public int syncedParameterCount;
        public int animatorOnlyParameterCount;
        public int hiddenParameterCount;
        public List<NdmfParameterUsageItem> parameterNames;
        public List<NdmfParameterPluginUsage> pluginBreakdown;
    }

    [Serializable]
    internal sealed class NdmfParameterUsageItem
    {
        public string name;
        public string originalName;
        public string parameterNamespace;
        public string parameterType;
        public int bitUsage;
        public bool wantSynced;
        public bool animatorOnly;
        public bool hidden;
        public string plugin;
        public string sourceComponentType;
        public string sourcePath;
    }

    [Serializable]
    internal sealed class NdmfParameterPluginUsage
    {
        public string plugin;
        public int parameterCount;
        public int syncedParameterCount;
        public int bitUsage;
    }
}
