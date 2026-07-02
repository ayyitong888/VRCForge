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
using UnityEditor.SceneManagement;
using UnityEngine;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_scan_avatar_controls",
        Description = "Scan a VRChat avatar expression menu, expression parameters, and scene clothing-like objects via a predefined VRCForge tool."
    )]
    public static class AvatarControlScanner
    {
        private static readonly string[] WardrobeKeywords =
        {
            "cloth", "clothes", "clothing", "outfit", "wear", "wardrobe", "dress", "shirt", "skirt",
            "pants", "jacket", "coat", "hood", "hat", "shoe", "socks", "accessory", "acc", "top", "bottom",
            "costume", "toggle", "衣装", "服", "洋服", "着替", "靴", "帽子", "上着", "スカート", "パンツ",
            "コート", "ジャケット", "アクセ", "饰品", "衣服", "衣柜", "换装", "外套", "裙", "鞋", "帽"
        };

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var descriptor = ResolveAvatarDescriptor(avatarPath);
                var resolvedAvatarPath = GetTransformPath(descriptor.transform);
                var parameterMap = ReadExpressionParameters(descriptor);
                var menuItems = ReadExpressionMenuItems(descriptor, parameterMap);
                var parameterItems = ReadParameterOnlyItems(parameterMap, menuItems);
                var sceneItems = ReadSceneObjectCandidates(descriptor.transform);

                var items = menuItems
                    .Concat(parameterItems)
                    .Concat(sceneItems)
                    .GroupBy(item => $"{item.source}:{item.parameterName}:{item.objectPath}:{item.menuPath}")
                    .Select(group => group.First())
                    .OrderBy(item => SourceRank(item.source))
                    .ThenBy(item => item.displayName, StringComparer.OrdinalIgnoreCase)
                    .Take(120)
                    .ToList();
                var outputPath = (@params?["outputPath"]?.ToString() ?? string.Empty).Trim();
                var payload = new
                {
                    avatarPath = resolvedAvatarPath,
                    avatarName = descriptor.name,
                    itemCount = items.Count,
                    items
                };
                var jsonPath = WriteJsonIfRequested(outputPath, payload);
                var response = JObject.FromObject(payload);
                if (!string.IsNullOrWhiteSpace(jsonPath))
                {
                    response["jsonPath"] = jsonPath;
                }

                return new SuccessResponse(
                    $"Scanned {items.Count} avatar control item(s).",
                    response);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Avatar control scan failed: {ex.Message}\n{ex.StackTrace}");
            }
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

        private static Dictionary<string, ParameterInfo> ReadExpressionParameters(Component descriptor)
        {
            var result = new Dictionary<string, ParameterInfo>(StringComparer.OrdinalIgnoreCase);
            var asset = GetMemberValue(descriptor, "expressionParameters");
            var parameters = GetMemberValue(asset, "parameters") as IEnumerable;
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

                result[name] = new ParameterInfo
                {
                    name = name,
                    valueType = Convert.ToString(GetMemberValue(parameter, "valueType"), CultureInfo.InvariantCulture) ?? "",
                    defaultValue = ToFloat(GetMemberValue(parameter, "defaultValue")),
                    saved = ToBool(GetMemberValue(parameter, "saved")),
                    networkSynced = ToBool(GetMemberValue(parameter, "networkSynced"))
                };
            }

            return result;
        }

        private static List<ControlItem> ReadExpressionMenuItems(Component descriptor, Dictionary<string, ParameterInfo> parameterMap)
        {
            var allControls = new List<ControlItem>();
            var filtered = new List<ControlItem>();
            var rootMenu = GetMemberValue(descriptor, "expressionsMenu");
            TraverseMenu(rootMenu, "", parameterMap, allControls, new HashSet<int>(), 0);

            foreach (var item in allControls)
            {
                if (IsWardrobeCandidate(item.displayName, item.parameterName, item.menuPath))
                {
                    filtered.Add(item);
                }
            }

            return filtered.Count > 0
                ? filtered
                : allControls.Where(item => !string.IsNullOrWhiteSpace(item.parameterName)).Take(60).ToList();
        }

        private static void TraverseMenu(
            object menu,
            string parentPath,
            Dictionary<string, ParameterInfo> parameterMap,
            List<ControlItem> items,
            HashSet<int> visited,
            int depth)
        {
            if (menu == null || depth > 8)
            {
                return;
            }

            var unityObject = menu as UnityEngine.Object;
            if (unityObject != null && !visited.Add(unityObject.GetInstanceID()))
            {
                return;
            }

            var controls = GetMemberValue(menu, "controls") as IEnumerable;
            if (controls == null)
            {
                return;
            }

            foreach (var control in controls)
            {
                var name = Convert.ToString(GetMemberValue(control, "name"), CultureInfo.InvariantCulture) ?? "";
                var type = Convert.ToString(GetMemberValue(control, "type"), CultureInfo.InvariantCulture) ?? "";
                var parameterName = ReadControlParameterName(control);
                var menuPath = string.IsNullOrWhiteSpace(parentPath) ? name : $"{parentPath}/{name}";
                var parameter = !string.IsNullOrWhiteSpace(parameterName) && parameterMap.TryGetValue(parameterName, out var info)
                    ? info
                    : null;

                if (!string.IsNullOrWhiteSpace(name) || !string.IsNullOrWhiteSpace(parameterName))
                {
                    items.Add(new ControlItem
                    {
                        name = string.IsNullOrWhiteSpace(name) ? parameterName : name,
                        displayName = string.IsNullOrWhiteSpace(name) ? parameterName : name,
                        source = "menu_control",
                        menuPath = menuPath,
                        objectPath = "",
                        active = parameter != null && parameter.defaultValue >= 0.5f,
                        canToggleSceneObject = false,
                        parameterName = parameterName,
                        controlType = type,
                        valueType = parameter?.valueType ?? "",
                        defaultValue = parameter?.defaultValue ?? 0f,
                        saved = parameter?.saved ?? false,
                        networkSynced = parameter?.networkSynced ?? false
                    });
                }

                var subMenu = GetMemberValue(control, "subMenu");
                if (subMenu != null)
                {
                    TraverseMenu(subMenu, menuPath, parameterMap, items, visited, depth + 1);
                }
            }
        }

        private static List<ControlItem> ReadParameterOnlyItems(
            Dictionary<string, ParameterInfo> parameterMap,
            List<ControlItem> menuItems)
        {
            var menuParameterNames = new HashSet<string>(
                menuItems.Select(item => item.parameterName).Where(value => !string.IsNullOrWhiteSpace(value)),
                StringComparer.OrdinalIgnoreCase);

            return parameterMap.Values
                .Where(parameter => !menuParameterNames.Contains(parameter.name))
                .Where(parameter => IsWardrobeCandidate(parameter.name, parameter.name, ""))
                .Select(parameter => new ControlItem
                {
                    name = parameter.name,
                    displayName = parameter.name,
                    source = "parameter",
                    menuPath = "",
                    objectPath = "",
                    active = parameter.defaultValue >= 0.5f,
                    canToggleSceneObject = false,
                    parameterName = parameter.name,
                    controlType = "",
                    valueType = parameter.valueType,
                    defaultValue = parameter.defaultValue,
                    saved = parameter.saved,
                    networkSynced = parameter.networkSynced
                })
                .ToList();
        }

        private static List<ControlItem> ReadSceneObjectCandidates(Transform avatarRoot)
        {
            return avatarRoot
                .GetComponentsInChildren<Transform>(true)
                .Where(item => item != null && item != avatarRoot)
                .Where(item => item.GetComponent<Renderer>() != null || item.GetComponentInChildren<Renderer>(true) != null)
                .Where(item => IsWardrobeCandidate(item.name, "", GetTransformPath(item)))
                .Select(item => new ControlItem
                {
                    name = item.name,
                    displayName = item.name,
                    source = "scene_object",
                    menuPath = "",
                    objectPath = GetTransformPath(item),
                    active = item.gameObject.activeSelf,
                    canToggleSceneObject = true,
                    parameterName = "",
                    controlType = "SceneObject",
                    valueType = "",
                    defaultValue = item.gameObject.activeSelf ? 1f : 0f,
                    saved = false,
                    networkSynced = false
                })
                .ToList();
        }

        private static string ReadControlParameterName(object control)
        {
            var parameter = GetMemberValue(control, "parameter");
            if (parameter == null)
            {
                return "";
            }

            return Convert.ToString(GetMemberValue(parameter, "name"), CultureInfo.InvariantCulture) ?? "";
        }

        private static bool IsWardrobeCandidate(params string[] values)
        {
            foreach (var value in values)
            {
                if (string.IsNullOrWhiteSpace(value))
                {
                    continue;
                }

                foreach (var keyword in WardrobeKeywords)
                {
                    if (value.IndexOf(keyword, StringComparison.OrdinalIgnoreCase) >= 0)
                    {
                        return true;
                    }
                }
            }

            return false;
        }

        private static int SourceRank(string source)
        {
            if (source == "menu_control")
            {
                return 0;
            }
            if (source == "parameter")
            {
                return 1;
            }
            return 2;
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
            return VRCForgeOutputPathGuard.ResolveManagedProjectOutputPath(requestedPath, "Avatar control scan");
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

        private class ParameterInfo
        {
            public string name;
            public string valueType;
            public float defaultValue;
            public bool saved;
            public bool networkSynced;
        }

        private class ControlItem
        {
            public string name;
            public string displayName;
            public string source;
            public string menuPath;
            public string objectPath;
            public bool active;
            public bool canToggleSceneObject;
            public string parameterName;
            public string controlType;
            public string valueType;
            public float defaultValue;
            public bool saved;
            public bool networkSynced;
        }
    }

    [McpForUnityTool(
        name: "vrc_toggle_scene_object",
        Description = "Toggle a scene GameObject active state by transform path via a predefined VRCForge tool."
    )]
    public static class SceneObjectToggler
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                var objectPath = (@params?["objectPath"]?.ToString() ?? string.Empty).Trim();
                var active = @params?["active"]?.Value<bool?>() ?? false;
                var saveAssets = @params?["saveAssets"]?.Value<bool?>() ?? true;
                if (string.IsNullOrWhiteSpace(objectPath))
                {
                    return new ErrorResponse("Missing required parameter: objectPath");
                }

                var normalized = NormalizePath(objectPath);
                var target = Resources.FindObjectsOfTypeAll<Transform>()
                    .Where(item => item != null && item.gameObject.scene.IsValid() && item.gameObject.scene.isLoaded && !EditorUtility.IsPersistent(item))
                    .FirstOrDefault(item => NormalizePath(GetTransformPath(item)) == normalized);
                if (target == null)
                {
                    return new ErrorResponse($"Scene object not found: {objectPath}");
                }

                target.gameObject.SetActive(active);
                EditorUtility.SetDirty(target.gameObject);
                EditorSceneManager.MarkSceneDirty(target.gameObject.scene);
                if (saveAssets)
                {
                    AssetDatabase.SaveAssets();
                    EditorSceneManager.SaveOpenScenes();
                }

                return new SuccessResponse(
                    $"Set {objectPath} active={active}.",
                    new
                    {
                        objectPath,
                        active = target.gameObject.activeSelf,
                        saved = saveAssets
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Scene object toggle failed: {ex.Message}\n{ex.StackTrace}");
            }
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
