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
using UnityEditor.Animations;
using UnityEngine;

namespace VRCAutoRig.Editor
{
    [McpForUnityTool(
        name: "vrc_scan_fx_animator",
        Description = "Scan a VRChat avatar FX AnimatorController into a read-only layer/state/transition inventory."
    )]
    public static class ComponentTools
    {
        public const string ScanFxAnimatorToolName = "vrc_scan_fx_animator";
        public const string DefaultOutputPath = "Assets/VRCAutoRig/fx_animator_inventory.json";

        public class ScanFxAnimatorParameters
        {
            [ToolParameter("Optional avatar root hierarchy path. If empty, the first scene avatar descriptor is used.", Required = false)]
            public string avatarPath { get; set; } = "";

            [ToolParameter("Optional AnimatorController asset path. If set, this overrides avatar FX lookup.", Required = false)]
            public string controllerPath { get; set; } = "";

            [ToolParameter("Asset-relative or absolute output path. Leave empty to skip writing JSON.", Required = false)]
            public string outputPath { get; set; } = DefaultOutputPath;

            [ToolParameter("Refresh the Unity AssetDatabase after writing JSON.", Required = false)]
            public bool? refreshAssets { get; set; } = true;
        }

        [MenuItem("VRCAutoRig/Scan FX Animator")]
        public static void ScanFxAnimatorFromMenu()
        {
            var payload = BuildFxAnimatorPayload("", "");
            var absolutePath = WriteJson(DefaultOutputPath, payload, true);
            Debug.Log($"[{ScanFxAnimatorToolName}] FX animator scan complete: {absolutePath}");
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<ScanFxAnimatorParameters>()
                ?? new ScanFxAnimatorParameters();

            try
            {
                var payload = BuildFxAnimatorPayload(parameters.avatarPath ?? "", parameters.controllerPath ?? "");
                var requestedPath = parameters.outputPath ?? "";
                if (!string.IsNullOrWhiteSpace(requestedPath))
                {
                    var absolutePath = WriteJson(requestedPath, payload, parameters.refreshAssets ?? true);
                    payload.outputPath = ToAssetRelativePath(absolutePath);
                    payload.absoluteOutputPath = absolutePath.Replace("\\", "/");
                }

                return new SuccessResponse(
                    $"Scanned FX animator with {payload.summary.layerCount} layer(s), {payload.summary.stateCount} state(s), and {payload.summary.transitionCount} transition(s).",
                    payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"FX animator scan failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static FxAnimatorPayload BuildFxAnimatorPayload(string avatarPath, string controllerPath)
        {
            var descriptor = string.IsNullOrWhiteSpace(controllerPath) ? ResolveAvatarDescriptor(avatarPath) : null;
            var controller = !string.IsNullOrWhiteSpace(controllerPath)
                ? LoadController(controllerPath)
                : ResolveFxController(descriptor);
            var controllerAssetPath = AssetDatabase.GetAssetPath(controller);
            var layers = new List<LayerItem>();
            var allTransitions = new List<TransitionItem>();
            var clipMap = new Dictionary<string, ClipItem>(StringComparer.OrdinalIgnoreCase);
            var usedParameters = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            foreach (var layer in controller.layers ?? Array.Empty<AnimatorControllerLayer>())
            {
                var layerStates = new List<StateItem>();
                var layerTransitions = new List<TransitionItem>();
                ScanStateMachine(
                    layer.stateMachine,
                    layer.name,
                    "",
                    layerStates,
                    layerTransitions,
                    clipMap,
                    usedParameters);

                layers.Add(new LayerItem
                {
                    name = layer.name,
                    default_weight = layer.defaultWeight,
                    blending_mode = layer.blendingMode.ToString(),
                    synced_layer_index = layer.syncedLayerIndex,
                    state_count = layerStates.Count,
                    transition_count = layerTransitions.Count,
                    states = layerStates,
                    transitions = layerTransitions
                });

                allTransitions.AddRange(layerTransitions);
            }

            var controllerParameters = (controller.parameters ?? Array.Empty<AnimatorControllerParameter>())
                .Select(parameter => new ControllerParameterItem
                {
                    name = parameter.name,
                    type = parameter.type.ToString(),
                    default_bool = parameter.defaultBool,
                    default_float = parameter.defaultFloat,
                    default_int = parameter.defaultInt,
                    used_by_condition = usedParameters.Contains(parameter.name)
                })
                .OrderBy(parameter => parameter.name, StringComparer.OrdinalIgnoreCase)
                .ToList();

            var toggleGroups = BuildToggleGroups(layers, allTransitions, controllerParameters, clipMap.Values.ToList());

            return new FxAnimatorPayload
            {
                type = "fx_animator_snapshot",
                version = "0.1",
                id = $"fx_{DateTime.UtcNow:yyyyMMdd_HHmmss}",
                created_at = DateTime.UtcNow.ToString("O"),
                unity_project = Directory.GetParent(Application.dataPath)?.Name ?? "UnknownProject",
                avatar_name = descriptor != null ? descriptor.name : "",
                avatar_path = descriptor != null ? GetTransformPath(descriptor.transform) : "",
                controller_name = controller.name,
                controller_path = controllerAssetPath,
                layers = layers,
                parameters = controllerParameters,
                animation_clips = clipMap.Values.OrderBy(clip => clip.asset_path, StringComparer.OrdinalIgnoreCase).ToList(),
                likely_toggle_groups = toggleGroups,
                summary = new FxAnimatorSummary
                {
                    layerCount = layers.Count,
                    stateCount = layers.Sum(layer => layer.state_count),
                    transitionCount = layers.Sum(layer => layer.transition_count),
                    controllerParameterCount = controllerParameters.Count,
                    usedParameterCount = usedParameters.Count,
                    clipCount = clipMap.Count,
                    likelyToggleGroupCount = toggleGroups.Count
                }
            };
        }

        private static void ScanStateMachine(
            AnimatorStateMachine stateMachine,
            string layerName,
            string pathPrefix,
            List<StateItem> states,
            List<TransitionItem> transitions,
            Dictionary<string, ClipItem> clipMap,
            HashSet<string> usedParameters)
        {
            if (stateMachine == null)
            {
                return;
            }

            foreach (var childState in stateMachine.states)
            {
                var state = childState.state;
                if (state == null)
                {
                    continue;
                }

                var statePath = string.IsNullOrWhiteSpace(pathPrefix)
                    ? state.name
                    : $"{pathPrefix}/{state.name}";
                var clips = ReadMotionClips(state.motion);
                foreach (var clip in clips)
                {
                    var clipPath = AssetDatabase.GetAssetPath(clip);
                    if (string.IsNullOrWhiteSpace(clipPath))
                    {
                        clipPath = clip.name;
                    }

                    if (!clipMap.ContainsKey(clipPath))
                    {
                        clipMap.Add(clipPath, new ClipItem
                        {
                            name = clip.name,
                            asset_path = clipPath,
                            length = clip.length,
                            frame_rate = clip.frameRate,
                            used_by_states = new List<string>()
                        });
                    }

                    clipMap[clipPath].used_by_states.Add($"{layerName}/{statePath}");
                }

                states.Add(new StateItem
                {
                    name = state.name,
                    state_path = statePath,
                    motion_name = state.motion != null ? state.motion.name : "",
                    motion_type = state.motion != null ? state.motion.GetType().Name : "",
                    speed = state.speed,
                    write_default_values = state.writeDefaultValues,
                    clip_paths = clips
                        .Select(clip => AssetDatabase.GetAssetPath(clip))
                        .Where(value => !string.IsNullOrWhiteSpace(value))
                        .Distinct(StringComparer.OrdinalIgnoreCase)
                        .OrderBy(value => value)
                        .ToList()
                });

                foreach (var transition in state.transitions ?? Array.Empty<AnimatorStateTransition>())
                {
                    transitions.Add(BuildTransitionItem(layerName, statePath, transition, usedParameters));
                }
            }

            foreach (var transition in stateMachine.anyStateTransitions ?? Array.Empty<AnimatorStateTransition>())
            {
                transitions.Add(BuildTransitionItem(layerName, "AnyState", transition, usedParameters));
            }

            foreach (var childMachine in stateMachine.stateMachines)
            {
                var childPath = string.IsNullOrWhiteSpace(pathPrefix)
                    ? childMachine.stateMachine.name
                    : $"{pathPrefix}/{childMachine.stateMachine.name}";
                ScanStateMachine(childMachine.stateMachine, layerName, childPath, states, transitions, clipMap, usedParameters);
            }
        }

        private static TransitionItem BuildTransitionItem(
            string layerName,
            string fromState,
            AnimatorStateTransition transition,
            HashSet<string> usedParameters)
        {
            var conditions = new List<ConditionItem>();
            foreach (var condition in transition.conditions ?? Array.Empty<AnimatorCondition>())
            {
                if (!string.IsNullOrWhiteSpace(condition.parameter))
                {
                    usedParameters.Add(condition.parameter);
                }

                conditions.Add(new ConditionItem
                {
                    parameter = condition.parameter,
                    mode = condition.mode.ToString(),
                    threshold = condition.threshold
                });
            }

            return new TransitionItem
            {
                layer = layerName,
                from_state = fromState,
                to_state = transition.destinationState != null ? transition.destinationState.name : "",
                to_state_machine = transition.destinationStateMachine != null ? transition.destinationStateMachine.name : "",
                has_exit_time = transition.hasExitTime,
                exit_time = transition.exitTime,
                duration = transition.duration,
                can_transition_to_self = transition.canTransitionToSelf,
                conditions = conditions
            };
        }

        private static List<AnimationClip> ReadMotionClips(Motion motion)
        {
            var result = new List<AnimationClip>();
            if (motion == null)
            {
                return result;
            }

            if (motion is AnimationClip clip)
            {
                result.Add(clip);
                return result;
            }

            if (motion is BlendTree blendTree)
            {
                foreach (var child in blendTree.children)
                {
                    result.AddRange(ReadMotionClips(child.motion));
                }
            }

            return result
                .Where(item => item != null)
                .GroupBy(item => AssetDatabase.GetAssetPath(item))
                .Select(group => group.First())
                .ToList();
        }

        private static List<ToggleGroupItem> BuildToggleGroups(
            List<LayerItem> layers,
            List<TransitionItem> transitions,
            List<ControllerParameterItem> parameters,
            List<ClipItem> clips)
        {
            var boolParameters = new HashSet<string>(
                parameters
                    .Where(parameter => string.Equals(parameter.type, "Bool", StringComparison.OrdinalIgnoreCase))
                    .Select(parameter => parameter.name),
                StringComparer.OrdinalIgnoreCase);

            return transitions
                .SelectMany(transition => transition.conditions.Select(condition => new { transition, condition }))
                .Where(item => !string.IsNullOrWhiteSpace(item.condition.parameter))
                .GroupBy(item => item.condition.parameter, StringComparer.OrdinalIgnoreCase)
                .Where(group =>
                    boolParameters.Contains(group.Key)
                    || ContainsAny(group.Key, "toggle", "show", "hide", "enable", "cloth", "outfit", "wardrobe"))
                .Select(group =>
                {
                    var layerNames = group.Select(item => item.transition.layer)
                        .Distinct(StringComparer.OrdinalIgnoreCase)
                        .OrderBy(value => value)
                        .ToList();
                    var referencedStates = group
                        .Select(item => item.transition.to_state)
                        .Where(value => !string.IsNullOrWhiteSpace(value))
                        .Distinct(StringComparer.OrdinalIgnoreCase)
                        .OrderBy(value => value)
                        .ToList();
                    return new ToggleGroupItem
                    {
                        parameter = group.Key,
                        likely_reason = boolParameters.Contains(group.Key)
                            ? "Bool parameter used by FX transition conditions."
                            : "Parameter name and transition use look like a toggle.",
                        layers = layerNames,
                        referenced_states = referencedStates,
                        referenced_clip_paths = clips
                            .Where(clip => clip.used_by_states.Any(state => layerNames.Any(layer => state.StartsWith(layer + "/", StringComparison.OrdinalIgnoreCase))))
                            .Select(clip => clip.asset_path)
                            .Distinct(StringComparer.OrdinalIgnoreCase)
                            .OrderBy(value => value)
                            .Take(12)
                            .ToList()
                    };
                })
                .OrderBy(item => item.parameter, StringComparer.OrdinalIgnoreCase)
                .ToList();
        }

        private static AnimatorController LoadController(string controllerPath)
        {
            var controller = AssetDatabase.LoadAssetAtPath<AnimatorController>(NormalizeAssetPath(controllerPath));
            if (controller == null)
            {
                throw new InvalidOperationException($"AnimatorController not found: {controllerPath}");
            }

            return controller;
        }

        private static Component ResolveAvatarDescriptor(string avatarPath)
        {
            var descriptorType = FindType("VRC.SDK3.Avatars.Components.VRCAvatarDescriptor")
                ?? throw new InvalidOperationException("VRC SDK avatar descriptor type was not found.");
            var descriptors = Resources.FindObjectsOfTypeAll(descriptorType)
                .OfType<Component>()
                .Where(IsSceneObject)
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

        private static AnimatorController ResolveFxController(Component descriptor)
        {
            var layers = GetMemberValue(descriptor, "baseAnimationLayers") as IEnumerable;
            if (layers == null)
            {
                throw new InvalidOperationException("Avatar descriptor has no baseAnimationLayers field.");
            }

            foreach (var layer in layers)
            {
                var layerType = Convert.ToString(GetMemberValue(layer, "type"), CultureInfo.InvariantCulture) ?? "";
                if (!string.Equals(layerType, "FX", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                var controller = GetMemberValue(layer, "animatorController") as AnimatorController;
                if (controller != null)
                {
                    return controller;
                }
            }

            throw new InvalidOperationException("No FX AnimatorController found on the avatar.");
        }

        private static bool ContainsAny(string value, params string[] keywords)
        {
            return keywords.Any(keyword => value.IndexOf(keyword, StringComparison.OrdinalIgnoreCase) >= 0);
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

        private static bool IsSceneObject(Component component)
        {
            return component != null
                && component.gameObject.scene.IsValid()
                && component.gameObject.scene.isLoaded
                && !EditorUtility.IsPersistent(component);
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

        private static string NormalizeAssetPath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim();
        }

        private static string WriteJson(string requestedPath, object payload, bool refreshAssets)
        {
            var absolutePath = ResolveToAbsolutePath(requestedPath);
            var directory = Path.GetDirectoryName(absolutePath);
            if (string.IsNullOrEmpty(directory))
            {
                throw new InvalidOperationException($"Cannot resolve parent folder for FX animator scan path: {requestedPath}");
            }

            Directory.CreateDirectory(directory);
            File.WriteAllText(absolutePath, JsonConvert.SerializeObject(payload, Formatting.Indented), Encoding.UTF8);

            if (refreshAssets)
            {
                AssetDatabase.Refresh();
            }

            return absolutePath;
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

        private static string ToAssetRelativePath(string absolutePath)
        {
            var dataPath = Application.dataPath.Replace("\\", "/");
            if (absolutePath.StartsWith(dataPath, StringComparison.OrdinalIgnoreCase))
            {
                return "Assets" + absolutePath.Substring(dataPath.Length);
            }

            return absolutePath.Replace("\\", "/");
        }

        [Serializable]
        private class FxAnimatorPayload
        {
            public string type;
            public string version;
            public string id;
            public string created_at;
            public string unity_project;
            public string avatar_name;
            public string avatar_path;
            public string controller_name;
            public string controller_path;
            public List<LayerItem> layers;
            public List<ControllerParameterItem> parameters;
            public List<ClipItem> animation_clips;
            public List<ToggleGroupItem> likely_toggle_groups;
            public FxAnimatorSummary summary;
            public string outputPath;
            public string absoluteOutputPath;
        }

        [Serializable]
        private class FxAnimatorSummary
        {
            public int layerCount;
            public int stateCount;
            public int transitionCount;
            public int controllerParameterCount;
            public int usedParameterCount;
            public int clipCount;
            public int likelyToggleGroupCount;
        }

        [Serializable]
        private class LayerItem
        {
            public string name;
            public float default_weight;
            public string blending_mode;
            public int synced_layer_index;
            public int state_count;
            public int transition_count;
            public List<StateItem> states;
            public List<TransitionItem> transitions;
        }

        [Serializable]
        private class StateItem
        {
            public string name;
            public string state_path;
            public string motion_name;
            public string motion_type;
            public float speed;
            public bool write_default_values;
            public List<string> clip_paths;
        }

        [Serializable]
        private class TransitionItem
        {
            public string layer;
            public string from_state;
            public string to_state;
            public string to_state_machine;
            public bool has_exit_time;
            public float exit_time;
            public float duration;
            public bool can_transition_to_self;
            public List<ConditionItem> conditions;
        }

        [Serializable]
        private class ConditionItem
        {
            public string parameter;
            public string mode;
            public float threshold;
        }

        [Serializable]
        private class ControllerParameterItem
        {
            public string name;
            public string type;
            public bool default_bool;
            public float default_float;
            public int default_int;
            public bool used_by_condition;
        }

        [Serializable]
        private class ClipItem
        {
            public string name;
            public string asset_path;
            public float length;
            public float frame_rate;
            public List<string> used_by_states;
        }

        [Serializable]
        private class ToggleGroupItem
        {
            public string parameter;
            public string likely_reason;
            public List<string> layers;
            public List<string> referenced_states;
            public List<string> referenced_clip_paths;
        }
    }
}
