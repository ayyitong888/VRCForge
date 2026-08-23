using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    /// <summary>
    /// Reflection-only adapter for Gesture Manager. The VRCForge package keeps
    /// no compile-time dependency on third-party assemblies and exposes only
    /// bounded Play Mode parameter reads/writes.
    /// </summary>
    internal static class GestureManagerRuntimeBridge
    {
        private const string ManagerTypeName = "BlackStartX.GestureManager.GestureManager";
        private const string ManagerEditorTypeName = "BlackStartX.GestureManager.Editor.GestureManagerEditor";
        private const string ModuleHelperTypeName = "BlackStartX.GestureManager.Editor.Modules.ModuleHelper";
        private const int MaxMenuDepth = 8;
        private const int MaxMenuControlsPerNode = 8;

        internal sealed class Status
        {
            public bool isPlayMode;
            public bool packageDetected;
            public string packageVersion = string.Empty;
            public string prefabAssetPath = string.Empty;
            public bool detected;
            public int managerCount;
            public object[] managers = Array.Empty<object>();
            public bool enterPlayModePending;
            public string enterPlayModeErrorCode = string.Empty;
            public string enterPlayModeError = string.Empty;
        }

        internal sealed class ManagerBinding
        {
            public MonoBehaviour Behaviour;
            public object Module;
            public string ManagerPath;
            public string AvatarPath;
        }

        internal static Status ReadStatus(
            string requestedAvatarPath = "",
            bool includeAllParameters = true,
            IEnumerable<string> requestedParameterNames = null,
            string parameterPrefix = "")
        {
            var prefabAssetPath = FindPrefabAssetPath();
            var packageInfo = string.IsNullOrEmpty(prefabAssetPath)
                ? null
                : UnityEditor.PackageManager.PackageInfo.FindForAssetPath(prefabAssetPath);
            var bindings = Discover(requestedAvatarPath);
            return new Status
            {
                isPlayMode = EditorApplication.isPlaying,
                packageDetected = !string.IsNullOrEmpty(prefabAssetPath),
                packageVersion = packageInfo?.version ?? string.Empty,
                prefabAssetPath = prefabAssetPath,
                detected = bindings.Count > 0,
                managerCount = bindings.Count,
                enterPlayModePending = GestureManagerPlayModeCoordinator.Pending,
                enterPlayModeErrorCode = GestureManagerPlayModeCoordinator.LastErrorCode,
                enterPlayModeError = GestureManagerPlayModeCoordinator.LastError,
                managers = bindings.Select(binding => Describe(
                    binding,
                    includeAllParameters,
                    requestedParameterNames,
                    parameterPrefix)).ToArray()
            };
        }

        internal static bool IsRunning()
        {
            return EditorApplication.isPlaying && Discover(string.Empty).Count > 0;
        }

        internal static IReadOnlyList<ManagerBinding> Discover(string requestedAvatarPath)
        {
            var requested = NormalizePath(requestedAvatarPath);
            var bindings = new List<ManagerBinding>();
            foreach (var behaviour in Resources.FindObjectsOfTypeAll<MonoBehaviour>())
            {
                if (!behaviour || !behaviour.gameObject.scene.IsValid())
                {
                    continue;
                }

                var type = behaviour.GetType();
                if (!string.Equals(type.FullName, ManagerTypeName, StringComparison.Ordinal))
                {
                    continue;
                }

                var module = ReadMember(behaviour, "Module");
                var avatar = ReadMember(module, "Avatar") as GameObject;
                var avatarPath = avatar ? HierarchyPath(avatar.transform) : string.Empty;
                if (!string.IsNullOrEmpty(requested)
                    && !string.Equals(NormalizePath(avatarPath), requested, StringComparison.Ordinal))
                {
                    continue;
                }

                bindings.Add(new ManagerBinding
                {
                    Behaviour = behaviour,
                    Module = module,
                    ManagerPath = HierarchyPath(behaviour.transform),
                    AvatarPath = avatarPath
                });
            }
            return bindings.OrderBy(item => item.ManagerPath, StringComparer.Ordinal).ToArray();
        }

        internal static IReadOnlyList<MonoBehaviour> DiscoverActiveManagers()
        {
            return Resources.FindObjectsOfTypeAll<MonoBehaviour>()
                .Where(behaviour => behaviour
                    && behaviour.gameObject.scene.IsValid()
                    && behaviour.gameObject.activeInHierarchy
                    && behaviour.enabled
                    && string.Equals(behaviour.GetType().FullName, ManagerTypeName, StringComparison.Ordinal))
                .OrderBy(behaviour => HierarchyPath(behaviour.transform), StringComparer.Ordinal)
                .ToArray();
        }

        internal static IReadOnlyList<MonoBehaviour> DiscoverActiveAvatars(string requestedAvatarPath)
        {
            var factory = FindModuleFactory();
            if (factory == null)
            {
                return Array.Empty<MonoBehaviour>();
            }

            var descriptorType = factory.GetParameters()[0].ParameterType;
            var requested = NormalizePath(requestedAvatarPath);
            return Resources.FindObjectsOfTypeAll<MonoBehaviour>()
                .Where(behaviour => behaviour
                    && behaviour.gameObject.scene.IsValid()
                    && behaviour.gameObject.activeInHierarchy
                    && behaviour.enabled
                    && descriptorType.IsInstanceOfType(behaviour)
                    && (string.IsNullOrEmpty(requested)
                        || string.Equals(NormalizePath(HierarchyPath(behaviour.transform)), requested, StringComparison.Ordinal)))
                .OrderBy(behaviour => HierarchyPath(behaviour.transform), StringComparer.Ordinal)
                .ToArray();
        }

        internal static bool TryInvokeEditorEntry(MonoBehaviour manager, out string entryPoint, out string error)
        {
            entryPoint = string.Empty;
            error = string.Empty;
            try
            {
                var editorType = FindType(ManagerEditorTypeName);
                var method = editorType?.GetMethods(BindingFlags.Static | BindingFlags.Public)
                    .Where(candidate => string.Equals(candidate.Name, "CreateAndPing", StringComparison.Ordinal))
                    .FirstOrDefault(candidate =>
                    {
                        var arguments = candidate.GetParameters();
                        return arguments.Length == 1
                            && arguments[0].ParameterType.IsAssignableFrom(manager.GetType());
                    });
                if (method == null)
                {
                    error = "Gesture Manager public editor entry CreateAndPing(manager) is unavailable.";
                    return false;
                }

                method.Invoke(null, new object[] { manager });
                entryPoint = $"{ManagerEditorTypeName}.CreateAndPing";
                return true;
            }
            catch (Exception exception)
            {
                error = exception.GetBaseException().Message;
                return false;
            }
        }

        internal static bool TryConnect(
            string managerPath,
            string avatarPath,
            out ManagerBinding binding,
            out string error)
        {
            binding = null;
            error = string.Empty;
            try
            {
                var normalizedManager = NormalizePath(managerPath);
                var normalizedAvatar = NormalizePath(avatarPath);
                var managers = DiscoverActiveManagers()
                    .Where(item => string.Equals(
                        NormalizePath(HierarchyPath(item.transform)),
                        normalizedManager,
                        StringComparison.Ordinal))
                    .ToArray();
                var avatars = DiscoverActiveAvatars(normalizedAvatar).ToArray();
                if (managers.Length != 1 || avatars.Length != 1)
                {
                    error = $"Gesture Manager connection target changed during Play Mode entry: managers={managers.Length}, avatars={avatars.Length}.";
                    return false;
                }

                var manager = managers[0];
                var existingModule = ReadMember(manager, "Module");
                var existingAvatar = ReadMember(existingModule, "Avatar") as GameObject;
                var existingAvatarPath = existingAvatar ? HierarchyPath(existingAvatar.transform) : string.Empty;
                if (existingModule != null
                    && string.Equals(NormalizePath(existingAvatarPath), normalizedAvatar, StringComparison.Ordinal))
                {
                    binding = new ManagerBinding
                    {
                        Behaviour = manager,
                        Module = existingModule,
                        ManagerPath = HierarchyPath(manager.transform),
                        AvatarPath = existingAvatarPath
                    };
                    return true;
                }

                var factory = FindModuleFactory();
                var module = factory?.Invoke(null, new object[] { avatars[0] });
                if (module == null)
                {
                    error = $"Gesture Manager could not create a compatible module for avatar: {avatarPath}";
                    return false;
                }

                var setModule = manager.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public)
                    .FirstOrDefault(candidate =>
                    {
                        if (!string.Equals(candidate.Name, "SetModule", StringComparison.Ordinal))
                        {
                            return false;
                        }
                        var arguments = candidate.GetParameters();
                        return arguments.Length == 1 && arguments[0].ParameterType.IsInstanceOfType(module);
                    });
                if (setModule == null)
                {
                    error = "Gesture Manager public runtime entry SetModule(module) is unavailable.";
                    return false;
                }

                setModule.Invoke(manager, new[] { module });
                var connectedModule = ReadMember(manager, "Module");
                var connectedAvatar = ReadMember(connectedModule, "Avatar") as GameObject;
                var connectedAvatarPath = connectedAvatar ? HierarchyPath(connectedAvatar.transform) : string.Empty;
                if (connectedModule == null
                    || !string.Equals(NormalizePath(connectedAvatarPath), normalizedAvatar, StringComparison.Ordinal))
                {
                    error = $"Gesture Manager did not retain the requested avatar connection: {avatarPath}";
                    return false;
                }

                binding = new ManagerBinding
                {
                    Behaviour = manager,
                    Module = connectedModule,
                    ManagerPath = HierarchyPath(manager.transform),
                    AvatarPath = connectedAvatarPath
                };
                return true;
            }
            catch (Exception exception)
            {
                error = exception.GetBaseException().Message;
                return false;
            }
        }

        internal static bool TryReadParameter(
            ManagerBinding binding,
            string parameterName,
            out object parameter,
            out float value,
            out string typeName)
        {
            parameter = null;
            value = 0f;
            typeName = string.Empty;
            if (binding == null || binding.Module == null || string.IsNullOrWhiteSpace(parameterName))
            {
                return false;
            }

            var getParam = binding.Module.GetType().GetMethod(
                "GetParam",
                BindingFlags.Instance | BindingFlags.Public,
                null,
                new[] { typeof(string) },
                null);
            parameter = getParam?.Invoke(binding.Module, new object[] { parameterName });
            if (parameter == null)
            {
                return false;
            }

            var floatValue = parameter.GetType().GetMethod(
                "FloatValue",
                BindingFlags.Instance | BindingFlags.Public,
                null,
                Type.EmptyTypes,
                null);
            var raw = floatValue?.Invoke(parameter, null);
            if (raw == null)
            {
                return false;
            }

            value = Convert.ToSingle(raw);
            typeName = Convert.ToString(ReadMember(parameter, "Type")) ?? string.Empty;
            return true;
        }

        internal static void SetParameter(ManagerBinding binding, object parameter, float value)
        {
            var method = parameter.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public)
                .FirstOrDefault(candidate =>
                {
                    if (!string.Equals(candidate.Name, "Set", StringComparison.Ordinal))
                    {
                        return false;
                    }
                    var arguments = candidate.GetParameters();
                    return arguments.Length == 3
                        && arguments[0].ParameterType.IsInstanceOfType(binding.Module)
                        && arguments[1].ParameterType == typeof(float)
                        && arguments[2].ParameterType == typeof(object);
                });
            if (method == null)
            {
                throw new MissingMethodException(parameter.GetType().FullName, "Set(module, float, object)");
            }
            method.Invoke(parameter, new[] { binding.Module, (object)value, null });
        }

        private static object Describe(
            ManagerBinding binding,
            bool includeAllParameters,
            IEnumerable<string> requestedParameterNames,
            string parameterPrefix)
        {
            var descriptor = ReadMember(binding.Module, "AvatarDescriptor");
            var menu = ReadMember(descriptor, "expressionsMenu") as UnityEngine.Object;
            var parameters = ReadMember(descriptor, "expressionParameters") as UnityEngine.Object;
            var userParameterNames = DictionaryKeys(ReadMember(binding.Module, "UserFilteredParams"));
            var defaultParameterNames = DictionaryKeys(ReadMember(binding.Module, "VrcFilteredParams"));
            var allRuntimeParameters = DescribeParameters(
                ReadMember(binding.Module, "Params"),
                userParameterNames,
                defaultParameterNames);
            var normalizedRequestedNames = new HashSet<string>(
                (requestedParameterNames ?? Array.Empty<string>())
                    .Where(item => !string.IsNullOrWhiteSpace(item))
                    .Select(item => item.Trim()),
                StringComparer.Ordinal);
            var normalizedPrefix = (parameterPrefix ?? string.Empty).Trim();
            var runtimeParameters = FilterParameters(
                allRuntimeParameters,
                includeAllParameters,
                normalizedRequestedNames,
                normalizedPrefix);

            return new
            {
                managerPath = binding.ManagerPath,
                avatarPath = binding.AvatarPath,
                managerActive = binding.Behaviour && binding.Behaviour.isActiveAndEnabled,
                moduleConnected = binding.Module != null,
                moduleType = binding.Module?.GetType().FullName ?? string.Empty,
                menuAsset = menu ? menu.name : string.Empty,
                menuControlCount = CollectionCount(ReadMember(menu, "controls")),
                menuTree = DescribeMenu(menu),
                expressionParametersAsset = parameters ? parameters.name : string.Empty,
                expressionParameterCount = CollectionCount(ReadMember(parameters, "parameters")),
                runtimeParameterCount = allRuntimeParameters.Length,
                userRuntimeParameterCount = allRuntimeParameters.Count(item =>
                    string.Equals(Convert.ToString(ReadMember(item, "category")), "user", StringComparison.Ordinal)),
                defaultRuntimeParameterCount = allRuntimeParameters.Count(item =>
                    string.Equals(Convert.ToString(ReadMember(item, "category")), "vrc_default", StringComparison.Ordinal)),
                returnedRuntimeParameterCount = runtimeParameters.Length,
                runtimeParameterSelection = new
                {
                    includeAll = includeAllParameters,
                    exactNames = normalizedRequestedNames.OrderBy(item => item, StringComparer.Ordinal).ToArray(),
                    prefix = normalizedPrefix
                },
                runtimeParameters
            };
        }

        private static object[] FilterParameters(
            object[] parameters,
            bool includeAll,
            ISet<string> exactNames,
            string prefix)
        {
            if (includeAll)
            {
                return parameters;
            }
            if ((exactNames == null || exactNames.Count == 0) && string.IsNullOrEmpty(prefix))
            {
                return Array.Empty<object>();
            }
            return parameters.Where(item =>
            {
                var name = Convert.ToString(ReadMember(item, "name")) ?? string.Empty;
                return (exactNames != null && exactNames.Contains(name))
                    || (!string.IsNullOrEmpty(prefix) && name.StartsWith(prefix, StringComparison.Ordinal));
            }).ToArray();
        }

        private static object DescribeMenu(UnityEngine.Object menu)
        {
            return DescribeMenu(menu, 0, new HashSet<int>());
        }

        private static object DescribeMenu(UnityEngine.Object menu, int depth, ISet<int> activeMenuIds)
        {
            if (!menu)
            {
                return null;
            }

            var instanceId = menu.GetInstanceID();
            if (!activeMenuIds.Add(instanceId))
            {
                return new
                {
                    name = menu.name ?? string.Empty,
                    controlCount = CollectionCount(ReadMember(menu, "controls")),
                    returnedControlCount = 0,
                    controlsTruncated = false,
                    cycleDetected = true,
                    controls = Array.Empty<object>()
                };
            }

            try
            {
                var controlsObject = ReadMember(menu, "controls");
                var controlCount = CollectionCount(controlsObject);
                var controls = EnumerateObjects(controlsObject)
                    .Take(MaxMenuControlsPerNode)
                    .Select(control => DescribeMenuControl(control, depth, activeMenuIds))
                    .ToArray();
                return new
                {
                    name = menu.name ?? string.Empty,
                    controlCount,
                    returnedControlCount = controls.Length,
                    controlsTruncated = controlCount > controls.Length,
                    cycleDetected = false,
                    controls
                };
            }
            finally
            {
                activeMenuIds.Remove(instanceId);
            }
        }

        private static object DescribeMenuControl(object control, int depth, ISet<int> activeMenuIds)
        {
            var parameter = ReadMember(control, "parameter");
            var subMenu = ReadMember(control, "subMenu") as UnityEngine.Object;
            var depthTruncated = subMenu && depth >= MaxMenuDepth;
            return new
            {
                name = Convert.ToString(ReadMember(control, "name")) ?? string.Empty,
                type = Convert.ToString(ReadMember(control, "type")) ?? string.Empty,
                parameter = ReadParameterName(parameter),
                value = ReadMember(control, "value"),
                subParameters = EnumerateObjects(ReadMember(control, "subParameters"))
                    .Select(ReadParameterName)
                    .Where(item => !string.IsNullOrEmpty(item))
                    .ToArray(),
                subMenuAsset = subMenu ? subMenu.name : string.Empty,
                depthTruncated,
                subMenu = subMenu && !depthTruncated
                    ? DescribeMenu(subMenu, depth + 1, activeMenuIds)
                    : null
            };
        }

        private static string ReadParameterName(object parameter)
        {
            return Convert.ToString(ReadMember(parameter, "name")) ?? string.Empty;
        }

        private static IEnumerable<object> EnumerateObjects(object value)
        {
            if (!(value is IEnumerable enumerable))
            {
                yield break;
            }
            foreach (var item in enumerable)
            {
                if (item != null)
                {
                    yield return item;
                }
            }
        }

        private static object[] DescribeParameters(
            object dictionaryObject,
            ISet<string> userParameterNames,
            ISet<string> defaultParameterNames)
        {
            if (!(dictionaryObject is IDictionary dictionary))
            {
                return Array.Empty<object>();
            }

            var parameters = new List<object>();
            foreach (DictionaryEntry entry in dictionary)
            {
                var name = Convert.ToString(entry.Key) ?? Convert.ToString(ReadMember(entry.Value, "Name")) ?? string.Empty;
                var parameter = entry.Value;
                if (parameter == null || string.IsNullOrEmpty(name))
                {
                    continue;
                }

                var floatValueMethod = parameter.GetType().GetMethod(
                    "FloatValue",
                    BindingFlags.Instance | BindingFlags.Public,
                    null,
                    Type.EmptyTypes,
                    null);
                var rawValue = floatValueMethod?.Invoke(parameter, null);
                if (rawValue == null)
                {
                    continue;
                }

                var value = Convert.ToSingle(rawValue);
                var category = userParameterNames.Contains(name)
                    ? "user"
                    : defaultParameterNames.Contains(name)
                        ? "vrc_default"
                        : "runtime_internal";
                parameters.Add(new
                {
                    name,
                    category,
                    type = Convert.ToString(ReadMember(parameter, "Type")) ?? string.Empty,
                    sync = Convert.ToString(ReadMember(parameter, "Sync")) ?? string.Empty,
                    value,
                    intValue = Convert.ToInt32(value),
                    boolValue = Math.Abs(value) > float.Epsilon,
                    lastUpdate = ReadMember(parameter, "LastUpdate")
                });
            }

            return parameters
                .OrderBy(item => Convert.ToString(ReadMember(item, "category")), StringComparer.Ordinal)
                .ThenBy(item => Convert.ToString(ReadMember(item, "name")), StringComparer.Ordinal)
                .ToArray();
        }

        private static ISet<string> DictionaryKeys(object dictionaryObject)
        {
            var keys = new HashSet<string>(StringComparer.Ordinal);
            if (!(dictionaryObject is IDictionary dictionary))
            {
                return keys;
            }
            foreach (DictionaryEntry entry in dictionary)
            {
                var key = Convert.ToString(entry.Key);
                if (!string.IsNullOrEmpty(key))
                {
                    keys.Add(key);
                }
            }
            return keys;
        }

        private static string FindPrefabAssetPath()
        {
            const string packagedPrefab = "Packages/vrchat.blackstartx.gesture-manager/GestureManager.prefab";
            if (IsGestureManagerPrefab(packagedPrefab))
            {
                return packagedPrefab;
            }

            foreach (var guid in AssetDatabase.FindAssets("t:Prefab GestureManager"))
            {
                var path = AssetDatabase.GUIDToAssetPath(guid);
                if (!path.EndsWith("/GestureManager.prefab", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                if (IsGestureManagerPrefab(path))
                {
                    return path;
                }
            }
            return string.Empty;
        }

        private static MethodInfo FindModuleFactory()
        {
            return FindType(ModuleHelperTypeName)?.GetMethods(BindingFlags.Static | BindingFlags.Public)
                .FirstOrDefault(candidate =>
                    string.Equals(candidate.Name, "GetModuleFor", StringComparison.Ordinal)
                    && candidate.GetParameters().Length == 1);
        }

        private static Type FindType(string fullName)
        {
            return AppDomain.CurrentDomain.GetAssemblies()
                .Select(assembly => assembly.GetType(fullName, throwOnError: false))
                .FirstOrDefault(type => type != null);
        }

        private static bool IsGestureManagerPrefab(string path)
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            return prefab
                && prefab.GetComponents<MonoBehaviour>().Any(component =>
                    component && string.Equals(component.GetType().FullName, ManagerTypeName, StringComparison.Ordinal));
        }

        private static int CollectionCount(object value)
        {
            if (value is ICollection collection)
            {
                return collection.Count;
            }
            return -1;
        }

        private static object ReadMember(object source, string name)
        {
            if (source == null)
            {
                return null;
            }
            var type = source.GetType();
            var property = type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (property != null && property.GetIndexParameters().Length == 0)
            {
                return property.GetValue(source, null);
            }
            var field = type.GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            return field?.GetValue(source);
        }

        private static string HierarchyPath(Transform transform)
        {
            if (!transform)
            {
                return string.Empty;
            }
            var names = new Stack<string>();
            for (var current = transform; current != null; current = current.parent)
            {
                names.Push(current.name);
            }
            return string.Join("/", names.ToArray());
        }

        private static string NormalizePath(string value)
        {
            return (value ?? string.Empty).Trim().Trim('/').Replace('\\', '/');
        }
    }

    [InitializeOnLoad]
    internal static class GestureManagerPlayModeCoordinator
    {
        private const string PendingKey = "VRCForge.GestureManager.EnterPlayMode.Pending";
        private const string ManagerPathKey = "VRCForge.GestureManager.EnterPlayMode.ManagerPath";
        private const string AvatarPathKey = "VRCForge.GestureManager.EnterPlayMode.AvatarPath";
        private const string LastErrorCodeKey = "VRCForge.GestureManager.EnterPlayMode.LastErrorCode";
        private const string LastErrorKey = "VRCForge.GestureManager.EnterPlayMode.LastError";

        static GestureManagerPlayModeCoordinator()
        {
            EditorApplication.playModeStateChanged -= OnPlayModeStateChanged;
            EditorApplication.playModeStateChanged += OnPlayModeStateChanged;
            if (EditorApplication.isPlaying && Pending)
            {
                EditorApplication.delayCall += CompletePendingConnection;
            }
        }

        internal static bool Pending => SessionState.GetBool(PendingKey, false);
        internal static string LastErrorCode => SessionState.GetString(LastErrorCodeKey, string.Empty);
        internal static string LastError => SessionState.GetString(LastErrorKey, string.Empty);

        internal static void Prepare(string managerPath, string avatarPath)
        {
            SessionState.SetString(ManagerPathKey, managerPath ?? string.Empty);
            SessionState.SetString(AvatarPathKey, avatarPath ?? string.Empty);
            SessionState.SetString(LastErrorCodeKey, string.Empty);
            SessionState.SetString(LastErrorKey, string.Empty);
            SessionState.SetBool(PendingKey, true);
        }

        private static void OnPlayModeStateChanged(PlayModeStateChange state)
        {
            if (state == PlayModeStateChange.EnteredPlayMode && Pending)
            {
                EditorApplication.delayCall += CompletePendingConnection;
            }
            else if (state == PlayModeStateChange.EnteredEditMode)
            {
                SessionState.SetBool(PendingKey, false);
            }
        }

        private static void CompletePendingConnection()
        {
            if (!EditorApplication.isPlaying || !Pending)
            {
                return;
            }

            var managerPath = SessionState.GetString(ManagerPathKey, string.Empty);
            var avatarPath = SessionState.GetString(AvatarPathKey, string.Empty);
            if (GestureManagerRuntimeBridge.TryConnect(managerPath, avatarPath, out _, out var error))
            {
                SessionState.SetString(LastErrorCodeKey, string.Empty);
                SessionState.SetString(LastErrorKey, string.Empty);
            }
            else
            {
                SessionState.SetString(LastErrorCodeKey, "gesture_manager_module_connection_failed");
                SessionState.SetString(LastErrorKey, error ?? string.Empty);
            }
            SessionState.SetBool(PendingKey, false);
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_gesture_manager_enter_play_mode",
        Summary = "Enter Play Mode through Gesture Manager's public editor/runtime path and bind one exact active manager to one exact avatar."
    )]
    public static class GestureManagerEnterPlayModeTool
    {
        public class Parameters
        {
            [VRCForgeInput("Optional exact avatar hierarchy path; required when multiple active avatars exist.", IsRequired = false)] public string avatarPath { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            var requestedAvatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
            var packageStatus = GestureManagerRuntimeBridge.ReadStatus(
                requestedAvatarPath,
                includeAllParameters: false);
            if (!packageStatus.packageDetected)
            {
                return VRCForgeToolResult.FailedWithCode(
                    "gesture_manager_package_not_detected",
                    "Gesture Manager package/prefab was not detected in the current Unity project.",
                    NoMutation(requestedAvatarPath));
            }

            var managers = GestureManagerRuntimeBridge.DiscoverActiveManagers();
            if (managers.Count != 1)
            {
                return VRCForgeToolResult.FailedWithCode(
                    managers.Count == 0
                        ? "gesture_manager_active_instance_required"
                        : "gesture_manager_active_instance_ambiguous",
                    managers.Count == 0
                        ? "Exactly one active scene Gesture Manager is required; none was found."
                        : "Exactly one active scene Gesture Manager is required; multiple were found.",
                    new
                    {
                        avatarPath = requestedAvatarPath,
                        activeManagerCount = managers.Count,
                        managerPaths = managers.Select(item => GestureManagerPath(item)).ToArray(),
                        mutationStarted = false,
                        committed = false,
                        commitState = "not_started"
                    });
            }

            var avatars = GestureManagerRuntimeBridge.DiscoverActiveAvatars(requestedAvatarPath);
            if (avatars.Count != 1)
            {
                return VRCForgeToolResult.FailedWithCode(
                    avatars.Count == 0
                        ? "gesture_manager_avatar_not_found"
                        : "gesture_manager_avatar_ambiguous",
                    avatars.Count == 0
                        ? (string.IsNullOrEmpty(requestedAvatarPath)
                            ? "No active compatible avatar descriptor was found."
                            : $"No active compatible avatar descriptor was found at: {requestedAvatarPath}")
                        : "Multiple active compatible avatars were found; provide one exact avatarPath.",
                    new
                    {
                        avatarPath = requestedAvatarPath,
                        activeAvatarCount = avatars.Count,
                        candidates = avatars.Select(item => GestureManagerPath(item)).ToArray(),
                        mutationStarted = false,
                        committed = false,
                        commitState = "not_started"
                    });
            }

            var managerPath = GestureManagerPath(managers[0]);
            var avatarPath = GestureManagerPath(avatars[0]);
            if (EditorApplication.isPlaying)
            {
                if (!GestureManagerRuntimeBridge.TryConnect(
                        managerPath,
                        avatarPath,
                        out var binding,
                        out var connectError))
                {
                    return VRCForgeToolResult.FailedWithCode(
                        "gesture_manager_module_connection_failed",
                        connectError,
                        new
                        {
                            managerPath,
                            avatarPath,
                            isPlayMode = true,
                            moduleConnected = false,
                            mutationStarted = true,
                            committed = false,
                            commitState = "editor_state_partial"
                        });
                }

                return Connected(binding, packageStatus.packageVersion, "already_playing");
            }

            if (!GestureManagerRuntimeBridge.TryInvokeEditorEntry(
                    managers[0],
                    out var editorEntryPoint,
                    out var editorEntryError))
            {
                return VRCForgeToolResult.FailedWithCode(
                    "gesture_manager_editor_entry_unavailable",
                    editorEntryError,
                    NoMutation(avatarPath));
            }

            GestureManagerPlayModeCoordinator.Prepare(managerPath, avatarPath);
            EditorApplication.EnterPlaymode();
            return VRCForgeToolResult.Completed(
                $"Gesture Manager Play Mode entry requested for avatar: {avatarPath}",
                new
                {
                    managerPath,
                    avatarPath,
                    packageVersion = packageStatus.packageVersion,
                    editorEntryPoint,
                    isPlayMode = false,
                    moduleConnected = false,
                    enterPlayModePending = true,
                    persistent = false,
                    sceneDirty = false,
                    mutationStarted = true,
                    committed = true,
                    commitState = "enter_play_mode_requested"
                });
        }

        private static object Connected(
            GestureManagerRuntimeBridge.ManagerBinding binding,
            string packageVersion,
            string entryState)
        {
            return VRCForgeToolResult.Completed(
                $"Gesture Manager connected to avatar: {binding.AvatarPath}",
                new
                {
                    managerPath = binding.ManagerPath,
                    avatarPath = binding.AvatarPath,
                    packageVersion,
                    entryState,
                    isPlayMode = true,
                    moduleConnected = true,
                    moduleType = binding.Module?.GetType().FullName ?? string.Empty,
                    persistent = false,
                    sceneDirty = false,
                    mutationStarted = true,
                    committed = true,
                    commitState = "runtime_connected"
                });
        }

        private static object NoMutation(string avatarPath)
        {
            return new
            {
                avatarPath = avatarPath ?? string.Empty,
                mutationStarted = false,
                committed = false,
                commitState = "not_started"
            };
        }

        private static string GestureManagerPath(MonoBehaviour behaviour)
        {
            var names = new Stack<string>();
            for (var current = behaviour.transform; current != null; current = current.parent)
            {
                names.Push(current.name);
            }
            return string.Join("/", names.ToArray());
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_gesture_manager_set_parameter",
        Summary = "Set one existing Gesture Manager parameter on one connected Play Mode avatar without persisting scene or asset changes."
    )]
    public static class GestureManagerRuntimeParameterTool
    {
        public class Parameters
        {
            [VRCForgeInput("Optional exact avatar hierarchy path; required when multiple managers are connected.", IsRequired = false)] public string avatarPath { get; set; } = "";
            [VRCForgeInput("Exact existing Gesture Manager parameter name.")] public string parameterName { get; set; } = "";
            [VRCForgeInput("Runtime parameter value.")] public float? value { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            if (!EditorApplication.isPlaying)
            {
                return VRCForgeToolResult.FailedWithCode(
                    "gesture_manager_play_mode_required",
                    "Gesture Manager parameters can only be changed while Unity is in Play Mode.",
                    new { mutationStarted = false, committed = false, commitState = "not_started" });
            }

            var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
            var parameterName = (@params?["parameterName"]?.ToString() ?? string.Empty).Trim();
            if (string.IsNullOrEmpty(parameterName))
            {
                return VRCForgeToolResult.FailedWithCode(
                    "gesture_manager_parameter_name_required",
                    "parameterName is required.",
                    new { mutationStarted = false, committed = false, commitState = "not_started" });
            }
            var valueToken = @params?["value"];
            if (valueToken == null || valueToken.Type == JTokenType.Null)
            {
                return VRCForgeToolResult.FailedWithCode(
                    "gesture_manager_parameter_value_required",
                    "value is required.",
                    new { mutationStarted = false, committed = false, commitState = "not_started" });
            }

            float value;
            try
            {
                value = valueToken.Value<float>();
            }
            catch (Exception)
            {
                return VRCForgeToolResult.FailedWithCode(
                    "gesture_manager_parameter_value_invalid",
                    "value must be a finite number.",
                    new { mutationStarted = false, committed = false, commitState = "not_started" });
            }
            if (float.IsNaN(value) || float.IsInfinity(value))
            {
                return VRCForgeToolResult.FailedWithCode(
                    "gesture_manager_parameter_value_invalid",
                    "value must be a finite number.",
                    new { mutationStarted = false, committed = false, commitState = "not_started" });
            }

            var managers = GestureManagerRuntimeBridge.Discover(avatarPath);
            if (managers.Count == 0)
            {
                return VRCForgeToolResult.FailedWithCode(
                    "gesture_manager_not_running",
                    string.IsNullOrEmpty(avatarPath)
                        ? "No connected Gesture Manager runtime was found in the active Play Mode scene."
                        : $"No connected Gesture Manager runtime was found for avatar: {avatarPath}",
                    new
                    {
                        avatarPath,
                        managerCount = 0,
                        mutationStarted = false,
                        committed = false,
                        commitState = "not_started"
                    });
            }
            if (managers.Count != 1)
            {
                return VRCForgeToolResult.FailedWithCode(
                    "gesture_manager_avatar_ambiguous",
                    "Multiple connected Gesture Manager runtimes were found; provide one exact avatarPath.",
                    new
                    {
                        avatarPath,
                        managerCount = managers.Count,
                        candidates = managers.Select(item => item.AvatarPath).ToArray(),
                        mutationStarted = false,
                        committed = false,
                        commitState = "not_started"
                    });
            }

            var manager = managers[0];
            if (!GestureManagerRuntimeBridge.TryReadParameter(
                    manager,
                    parameterName,
                    out var parameter,
                    out var before,
                    out var parameterType))
            {
                return VRCForgeToolResult.FailedWithCode(
                    "gesture_manager_parameter_not_found",
                    $"Gesture Manager parameter was not found: {parameterName}",
                    new
                    {
                        managerPath = manager.ManagerPath,
                        avatarPath = manager.AvatarPath,
                        parameterName,
                        mutationStarted = false,
                        committed = false,
                        commitState = "not_started"
                    });
            }

            try
            {
                GestureManagerRuntimeBridge.SetParameter(manager, parameter, value);
                if (!GestureManagerRuntimeBridge.TryReadParameter(
                        manager,
                        parameterName,
                        out _,
                        out var after,
                        out _))
                {
                    return VRCForgeToolResult.FailedWithCode(
                        "gesture_manager_parameter_readback_failed",
                        $"Gesture Manager accepted the parameter write but readback failed: {parameterName}",
                        new
                        {
                            managerPath = manager.ManagerPath,
                            avatarPath = manager.AvatarPath,
                            parameterName,
                            beforeValue = before,
                            requestedValue = value,
                            mutationStarted = true,
                            committed = false,
                            commitState = "unknown"
                        });
                }

                return VRCForgeToolResult.Completed(
                    $"Gesture Manager parameter set: {parameterName} = {after}",
                    new
                    {
                        managerPath = manager.ManagerPath,
                        avatarPath = manager.AvatarPath,
                        parameterName,
                        parameterType,
                        beforeValue = before,
                        requestedValue = value,
                        afterValue = after,
                        persistent = false,
                        sceneDirty = false,
                        mutationStarted = true,
                        committed = true,
                        commitState = "runtime_applied"
                    });
            }
            catch (TargetInvocationException exception)
            {
                var detail = exception.InnerException?.Message ?? exception.Message;
                return VRCForgeToolResult.FailedWithCode(
                    "gesture_manager_parameter_write_failed",
                    $"Gesture Manager parameter write failed: {detail}",
                    new
                    {
                        managerPath = manager.ManagerPath,
                        avatarPath = manager.AvatarPath,
                        parameterName,
                        beforeValue = before,
                        requestedValue = value,
                        mutationStarted = true,
                        committed = false,
                        commitState = "unknown"
                    });
            }
        }
    }
}
