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

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_scan_animation_bindings",
        Description = "Scan AnimationClip bindings for object toggles, blendshapes, material properties, and unsupported asset-reference writes."
    )]
    public static class AssetTools
    {
        public const string ScanAnimationBindingsToolName = "vrc_scan_animation_bindings";
        public const string DefaultOutputPath = "Assets/VRCForge/animation_bindings_inventory.json";

        public class ScanAnimationBindingsParameters
        {
            [ToolParameter("Optional avatar root hierarchy path used to discover FX clips.", Required = false)]
            public string avatarPath { get; set; } = "";

            [ToolParameter("Optional AnimatorController asset path used to discover clips.", Required = false)]
            public string controllerPath { get; set; } = "";

            [ToolParameter("Optional explicit AnimationClip asset paths.", Required = false)]
            public List<string> clipPaths { get; set; } = new List<string>();

            [ToolParameter("When true, scan all AnimationClip assets in the project.", Required = false)]
            public bool? includeAllProjectClips { get; set; } = false;

            [ToolParameter("Maximum number of clips to scan.", Required = false)]
            public int? maxClips { get; set; } = 300;

            [ToolParameter("Asset-relative or absolute output path. Leave empty to skip writing JSON.", Required = false)]
            public string outputPath { get; set; } = DefaultOutputPath;

            [ToolParameter("Refresh the Unity AssetDatabase after writing JSON.", Required = false)]
            public bool? refreshAssets { get; set; } = true;
        }

        [MenuItem("VRCForge/Scan Animation Bindings")]
        public static void ScanAnimationBindingsFromMenu()
        {
            var payload = BuildAnimationBindingsPayload("", "", new List<string>(), false, 300);
            var absolutePath = WriteJson(DefaultOutputPath, payload, true);
            Debug.Log($"[{ScanAnimationBindingsToolName}] Animation binding scan complete: {absolutePath}");
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<ScanAnimationBindingsParameters>()
                ?? new ScanAnimationBindingsParameters();

            try
            {
                var maxClips = Mathf.Clamp(parameters.maxClips ?? 300, 1, 2000);
                var payload = BuildAnimationBindingsPayload(
                    parameters.avatarPath ?? "",
                    parameters.controllerPath ?? "",
                    parameters.clipPaths ?? new List<string>(),
                    parameters.includeAllProjectClips ?? false,
                    maxClips);
                var requestedPath = parameters.outputPath ?? "";
                if (!string.IsNullOrWhiteSpace(requestedPath))
                {
                    var absolutePath = WriteJson(requestedPath, payload, parameters.refreshAssets ?? true);
                    payload.outputPath = ToAssetRelativePath(absolutePath);
                    payload.absoluteOutputPath = absolutePath.Replace("\\", "/");
                }

                return new SuccessResponse(
                    $"Scanned {payload.summary.clipCount} animation clip(s) with {payload.summary.bindingCount} binding(s).",
                    payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Animation binding scan failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static AnimationBindingsPayload BuildAnimationBindingsPayload(
            string avatarPath,
            string controllerPath,
            List<string> clipPaths,
            bool includeAllProjectClips,
            int maxClips)
        {
            var clips = ResolveClips(avatarPath, controllerPath, clipPaths, includeAllProjectClips)
                .GroupBy(clip => AssetDatabase.GetAssetPath(clip), StringComparer.OrdinalIgnoreCase)
                .Select(group => group.First())
                .Where(clip => clip != null)
                .OrderBy(clip => AssetDatabase.GetAssetPath(clip), StringComparer.OrdinalIgnoreCase)
                .Take(maxClips)
                .ToList();
            var clipItems = clips.Select(ScanClip).ToList();
            var warnings = clipItems
                .SelectMany(clip => clip.warnings.Select(warning => new WarningItem
                {
                    clip_path = clip.asset_path,
                    path = warning.path,
                    property_name = warning.property_name,
                    severity = warning.severity,
                    message = warning.message
                }))
                .ToList();

            return new AnimationBindingsPayload
            {
                type = "animation_bindings_snapshot",
                version = "0.1",
                id = $"bindings_{DateTime.UtcNow:yyyyMMdd_HHmmss}",
                created_at = DateTime.UtcNow.ToString("O"),
                unity_project = Directory.GetParent(Application.dataPath)?.Name ?? "UnknownProject",
                requested_avatar_path = NormalizePath(avatarPath),
                requested_controller_path = NormalizeAssetPath(controllerPath),
                include_all_project_clips = includeAllProjectClips,
                clips = clipItems,
                warnings = warnings,
                summary = new AnimationBindingsSummary
                {
                    clipCount = clipItems.Count,
                    bindingCount = clipItems.Sum(clip => clip.binding_count),
                    materialBindingCount = clipItems.Sum(clip => clip.material_binding_count),
                    objectToggleBindingCount = clipItems.Sum(clip => clip.object_toggle_binding_count),
                    blendshapeBindingCount = clipItems.Sum(clip => clip.blendshape_binding_count),
                    unsupportedWarningCount = warnings.Count
                }
            };
        }

        private static List<AnimationClip> ResolveClips(
            string avatarPath,
            string controllerPath,
            List<string> clipPaths,
            bool includeAllProjectClips)
        {
            var result = new List<AnimationClip>();

            foreach (var clipPath in clipPaths.Where(path => !string.IsNullOrWhiteSpace(path)))
            {
                var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(NormalizeAssetPath(clipPath));
                if (clip == null)
                {
                    throw new InvalidOperationException($"AnimationClip not found: {clipPath}");
                }

                result.Add(clip);
            }

            if (!string.IsNullOrWhiteSpace(controllerPath))
            {
                var controller = AssetDatabase.LoadAssetAtPath<AnimatorController>(NormalizeAssetPath(controllerPath));
                if (controller == null)
                {
                    throw new InvalidOperationException($"AnimatorController not found: {controllerPath}");
                }

                result.AddRange(ReadControllerClips(controller));
            }

            if (!string.IsNullOrWhiteSpace(avatarPath) || (result.Count == 0 && !includeAllProjectClips))
            {
                var descriptor = ResolveAvatarDescriptor(avatarPath);
                result.AddRange(ReadControllerClips(ResolveFxController(descriptor)));
            }

            if (includeAllProjectClips)
            {
                foreach (var guid in AssetDatabase.FindAssets("t:AnimationClip"))
                {
                    var path = AssetDatabase.GUIDToAssetPath(guid);
                    var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
                    if (clip != null)
                    {
                        result.Add(clip);
                    }
                }
            }

            return result;
        }

        private static ClipBindingItem ScanClip(AnimationClip clip)
        {
            var bindings = new List<BindingItem>();
            var warnings = new List<BindingWarningItem>();
            foreach (var binding in AnimationUtility.GetCurveBindings(clip))
            {
                AddBinding(bindings, warnings, binding, "float_curve");
            }

            foreach (var binding in AnimationUtility.GetObjectReferenceCurveBindings(clip))
            {
                AddBinding(bindings, warnings, binding, "object_reference_curve");
            }

            return new ClipBindingItem
            {
                name = clip.name,
                asset_path = AssetDatabase.GetAssetPath(clip),
                length = clip.length,
                frame_rate = clip.frameRate,
                binding_count = bindings.Count,
                material_binding_count = bindings.Count(binding => binding.binding_category == "material_property" || binding.binding_category == "material_reference"),
                object_toggle_binding_count = bindings.Count(binding => binding.binding_category == "object_active_toggle"),
                blendshape_binding_count = bindings.Count(binding => binding.binding_category == "blendshape"),
                bindings = bindings
                    .OrderBy(binding => binding.path, StringComparer.OrdinalIgnoreCase)
                    .ThenBy(binding => binding.property_name, StringComparer.OrdinalIgnoreCase)
                    .ToList(),
                warnings = warnings
            };
        }

        private static void AddBinding(
            List<BindingItem> bindings,
            List<BindingWarningItem> warnings,
            EditorCurveBinding binding,
            string bindingKind)
        {
            var category = ClassifyBinding(binding, bindingKind);
            var item = new BindingItem
            {
                path = binding.path,
                type_name = binding.type != null ? binding.type.Name : "",
                property_name = binding.propertyName,
                binding_kind = bindingKind,
                binding_category = category,
                safe_for_phase2_authoring = IsSafeForPhase2Authoring(category)
            };
            bindings.Add(item);

            var warning = BuildWarning(item);
            if (warning != null)
            {
                warnings.Add(warning);
            }
        }

        private static string ClassifyBinding(EditorCurveBinding binding, string bindingKind)
        {
            var propertyName = binding.propertyName ?? "";
            var typeName = binding.type != null ? binding.type.Name : "";
            if (propertyName.StartsWith("blendShape.", StringComparison.OrdinalIgnoreCase))
            {
                return "blendshape";
            }

            if (string.Equals(typeName, "GameObject", StringComparison.OrdinalIgnoreCase)
                && string.Equals(propertyName, "m_IsActive", StringComparison.OrdinalIgnoreCase))
            {
                return "object_active_toggle";
            }

            if (propertyName.StartsWith("material.", StringComparison.OrdinalIgnoreCase))
            {
                return "material_property";
            }

            if (propertyName.IndexOf("m_Materials", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return "material_reference";
            }

            if (propertyName.IndexOf("m_Mesh", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return "mesh_reference";
            }

            if (propertyName.IndexOf("m_Shader", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return "shader_reference";
            }

            if (string.Equals(propertyName, "m_Enabled", StringComparison.OrdinalIgnoreCase))
            {
                return string.Equals(typeName, "Renderer", StringComparison.OrdinalIgnoreCase)
                    ? "renderer_enabled"
                    : "component_enabled";
            }

            if (propertyName.StartsWith("m_Local", StringComparison.OrdinalIgnoreCase))
            {
                return "transform";
            }

            if (bindingKind == "object_reference_curve")
            {
                return "object_reference";
            }

            return "other";
        }

        private static bool IsSafeForPhase2Authoring(string category)
        {
            return category == "object_active_toggle" || category == "blendshape";
        }

        private static BindingWarningItem BuildWarning(BindingItem binding)
        {
            if (binding.binding_category == "material_reference"
                || binding.binding_category == "mesh_reference"
                || binding.binding_category == "shader_reference"
                || binding.binding_category == "object_reference")
            {
                return new BindingWarningItem
                {
                    path = binding.path,
                    property_name = binding.property_name,
                    severity = "warning",
                    message = "Reference-changing bindings can replace project assets at runtime and are not supported by VRCForge authoring tools."
                };
            }

            if (binding.binding_category == "material_property")
            {
                return new BindingWarningItem
                {
                    path = binding.path,
                    property_name = binding.property_name,
                    severity = "info",
                    message = "Material property animation is reported for review only; Phase 2 authoring does not write arbitrary shader properties."
                };
            }

            return null;
        }

        private static List<AnimationClip> ReadControllerClips(AnimatorController controller)
        {
            var result = new List<AnimationClip>();
            foreach (var layer in controller.layers ?? Array.Empty<AnimatorControllerLayer>())
            {
                ReadStateMachineClips(layer.stateMachine, result);
            }

            return result;
        }

        private static void ReadStateMachineClips(AnimatorStateMachine stateMachine, List<AnimationClip> clips)
        {
            if (stateMachine == null)
            {
                return;
            }

            foreach (var childState in stateMachine.states)
            {
                if (childState.state != null)
                {
                    clips.AddRange(ReadMotionClips(childState.state.motion));
                }
            }

            foreach (var childMachine in stateMachine.stateMachines)
            {
                ReadStateMachineClips(childMachine.stateMachine, clips);
            }
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

            return result;
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
                throw new InvalidOperationException($"Cannot resolve parent folder for animation binding scan path: {requestedPath}");
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
        private class AnimationBindingsPayload
        {
            public string type;
            public string version;
            public string id;
            public string created_at;
            public string unity_project;
            public string requested_avatar_path;
            public string requested_controller_path;
            public bool include_all_project_clips;
            public List<ClipBindingItem> clips;
            public List<WarningItem> warnings;
            public AnimationBindingsSummary summary;
            public string outputPath;
            public string absoluteOutputPath;
        }

        [Serializable]
        private class AnimationBindingsSummary
        {
            public int clipCount;
            public int bindingCount;
            public int materialBindingCount;
            public int objectToggleBindingCount;
            public int blendshapeBindingCount;
            public int unsupportedWarningCount;
        }

        [Serializable]
        private class ClipBindingItem
        {
            public string name;
            public string asset_path;
            public float length;
            public float frame_rate;
            public int binding_count;
            public int material_binding_count;
            public int object_toggle_binding_count;
            public int blendshape_binding_count;
            public List<BindingItem> bindings;
            public List<BindingWarningItem> warnings;
        }

        [Serializable]
        private class BindingItem
        {
            public string path;
            public string type_name;
            public string property_name;
            public string binding_kind;
            public string binding_category;
            public bool safe_for_phase2_authoring;
        }

        [Serializable]
        private class BindingWarningItem
        {
            public string path;
            public string property_name;
            public string severity;
            public string message;
        }

        [Serializable]
        private class WarningItem
        {
            public string clip_path;
            public string path;
            public string property_name;
            public string severity;
            public string message;
        }
    }
}
