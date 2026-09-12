using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using VRCForge.Core.MCP;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_scan_animation_bindings",
        Summary = "Scan AnimationClip bindings for object toggles, blendshapes, material properties, and unsupported asset-reference writes."
    )]
    public static class AssetTools
    {
        public const string ScanAnimationBindingsToolName = "vrc_scan_animation_bindings";
        public const string DefaultOutputPath = "Assets/VRCForge/animation_bindings_inventory.json";

        public class ScanAnimationBindingsParameters
        {
            [VRCForgeInput("Optional avatar root hierarchy path used to discover FX clips.", IsRequired = false)]
            public string avatarPath { get; set; } = "";

            [VRCForgeInput("Optional AnimatorController asset path used to discover clips.", IsRequired = false)]
            public string controllerPath { get; set; } = "";

            [VRCForgeInput("Optional explicit AnimationClip asset paths.", IsRequired = false)]
            public List<string> clipPaths { get; set; } = new List<string>();

            [VRCForgeInput("When true, scan all AnimationClip assets in the project.", IsRequired = false)]
            public bool? includeAllProjectClips { get; set; } = false;

            [VRCForgeInput("Maximum number of clips to scan.", IsRequired = false)]
            public int? maxClips { get; set; } = 300;

            [VRCForgeInput("Maximum keyframes returned per binding. Values above this limit are explicitly marked as truncated.", IsRequired = false)]
            public int? maxKeysPerBinding { get; set; } = 256;

            [VRCForgeInput("Include full binding arrays and warning details. Defaults to true for direct Unity callers; external wrappers may request compact summaries.", IsRequired = false)]
            public bool? includeBindingDetails { get; set; } = true;

            [VRCForgeInput("Optional summary/index/details read mode; omit for legacy output.", IsRequired = false)]
            public string bindingView { get; set; }

            [VRCForgeInput("At most 32 exact path/propertyName/componentType selectors, OR between rows.", IsRequired = false)]
            public JArray bindingSelectors { get; set; }

            [VRCForgeInput("Global binding offset within the selected clip page.", IsRequired = false)]
            public int? bindingOffset { get; set; }

            [VRCForgeInput("Binding page size 1..256; selected reads default 16.", IsRequired = false)]
            public int? bindingLimit { get; set; }

            [VRCForgeInput("Sorted discovered clip offset.", IsRequired = false)]
            public int? clipOffset { get; set; }

            [VRCForgeInput("Key offset for exact binding continuation.", IsRequired = false)]
            public int? keyOffset { get; set; }

            [VRCForgeInput("Selected-read total key budget 1..4096, default 4096.", IsRequired = false)]
            public int? maxTotalKeys { get; set; }

            [VRCForgeInput("SHA256 from the preceding selected read; rejects stale continuation.", IsRequired = false)]
            public string expectedSnapshotDigest { get; set; }

            [VRCForgeInput("Asset-relative or absolute output path. Leave empty to skip writing JSON.", IsRequired = false)]
            public string outputPath { get; set; } = DefaultOutputPath;

            [VRCForgeInput("Refresh the Unity AssetDatabase after writing JSON.", IsRequired = false)]
            public bool? refreshAssets { get; set; } = true;
        }

        [MenuItem("VRCForge/Scan Animation Bindings")]
        public static void ScanAnimationBindingsFromMenu()
        {
            var payload = BuildAnimationBindingsPayload("", "", new List<string>(), false, 300, 256, true);
            var absolutePath = WriteJson(DefaultOutputPath, payload, true, out _, out _);
            Debug.Log($"[{ScanAnimationBindingsToolName}] Animation binding scan complete: {absolutePath}");
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<ScanAnimationBindingsParameters>()
                ?? new ScanAnimationBindingsParameters();

            try
            {
                if (AnimationBindingReadSelection.Requested(@params))
                {
                    var selected = AnimationBindingReadSelection.Build(@params, () => ResolveClips(
                        parameters.avatarPath ?? "", parameters.controllerPath ?? "",
                        parameters.clipPaths ?? new List<string>(), parameters.includeAllProjectClips ?? false));
                    return VRCForgeToolResult.Completed("Animation binding selection: " + selected["selection"]["matchStatus"], selected);
                }
                var maxClips = Mathf.Clamp(parameters.maxClips ?? 300, 1, 2000);
                var maxKeysPerBinding = Mathf.Clamp(parameters.maxKeysPerBinding ?? 256, 1, 2000);
                var payload = BuildAnimationBindingsPayload(
                    parameters.avatarPath ?? "",
                    parameters.controllerPath ?? "",
                    parameters.clipPaths ?? new List<string>(),
                    parameters.includeAllProjectClips ?? false,
                    maxClips,
                    maxKeysPerBinding,
                    parameters.includeBindingDetails ?? true);
                var requestedPath = parameters.outputPath ?? "";
                if (!string.IsNullOrWhiteSpace(requestedPath))
                {
                    var absolutePath = WriteJson(
                        requestedPath,
                        payload,
                        parameters.refreshAssets ?? true,
                        out var beforeSnapshot,
                        out var afterSnapshot);
                    payload.outputPath = ToAssetRelativePath(absolutePath);
                    payload.absoluteOutputPath = absolutePath.Replace("\\", "/");
                    payload.before = beforeSnapshot;
                    payload.after = afterSnapshot;
                    payload.affected = new
                    {
                        count = 1,
                        items = new[] { payload.outputPath },
                        handle = payload.outputPath
                    };
                }

                return VRCForgeToolResult.Completed(
                    $"Scanned {payload.summary.clipCount} animation clip(s) with {payload.summary.bindingCount} binding(s).",
                    payload);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Animation binding scan failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static AnimationBindingsPayload BuildAnimationBindingsPayload(
            string avatarPath,
            string controllerPath,
            List<string> clipPaths,
            bool includeAllProjectClips,
            int maxClips,
            int maxKeysPerBinding,
            bool includeBindingDetails)
        {
            var clips = ResolveClips(avatarPath, controllerPath, clipPaths, includeAllProjectClips)
                .GroupBy(clip => AssetDatabase.GetAssetPath(clip), StringComparer.OrdinalIgnoreCase)
                .Select(group => group.First())
                .Where(clip => clip != null)
                .OrderBy(clip => AssetDatabase.GetAssetPath(clip), StringComparer.OrdinalIgnoreCase)
                .Take(maxClips)
                .ToList();
            var clipItems = clips.Select(clip => ScanClip(clip, maxKeysPerBinding, includeBindingDetails)).ToList();
            var warnings = includeBindingDetails ? clipItems
                .SelectMany(clip => clip.warnings.Select(warning => new WarningItem
                {
                    clip_path = clip.asset_path,
                    path = warning.path,
                    property_name = warning.property_name,
                    severity = warning.severity,
                    message = warning.message
                }))
                .ToList() : new List<WarningItem>();

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
                max_keys_per_binding = maxKeysPerBinding,
                include_binding_details = includeBindingDetails,
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

            // An explicit clip list is an exact selector. Do not silently widen it
            // with controller/avatar discovery or the all-project fallback.
            if (result.Count > 0)
            {
                return result;
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

        private static ClipBindingItem ScanClip(AnimationClip clip, int maxKeysPerBinding, bool includeBindingDetails)
        {
            var bindings = new List<BindingItem>();
            var warnings = new List<BindingWarningItem>();
            foreach (var binding in AnimationUtility.GetCurveBindings(clip))
            {
                AddBinding(bindings, warnings, clip, binding, "float_curve", maxKeysPerBinding, includeBindingDetails);
            }

            foreach (var binding in AnimationUtility.GetObjectReferenceCurveBindings(clip))
            {
                AddBinding(bindings, warnings, clip, binding, "object_reference_curve", maxKeysPerBinding, includeBindingDetails);
            }

            var settings = AnimationUtility.GetAnimationClipSettings(clip);

            return new ClipBindingItem
            {
                name = clip.name,
                asset_path = AssetDatabase.GetAssetPath(clip),
                length = clip.length,
                frame_rate = clip.frameRate,
                loop_time = settings.loopTime,
                loop_blend = settings.loopBlend,
                binding_count = bindings.Count,
                material_binding_count = bindings.Count(binding => binding.binding_category == "material_property" || binding.binding_category == "material_reference"),
                object_toggle_binding_count = bindings.Count(binding => binding.binding_category == "object_active_toggle"),
                blendshape_binding_count = bindings.Count(binding => binding.binding_category == "blendshape"),
                bindings = includeBindingDetails ? bindings
                    .OrderBy(binding => binding.path, StringComparer.OrdinalIgnoreCase)
                    .ThenBy(binding => binding.property_name, StringComparer.OrdinalIgnoreCase)
                    .ToList() : null,
                warnings = includeBindingDetails ? warnings : null,
                warning_count = warnings.Count
            };
        }

        private static void AddBinding(
            List<BindingItem> bindings,
            List<BindingWarningItem> warnings,
            AnimationClip clip,
            EditorCurveBinding binding,
            string bindingKind,
            int maxKeysPerBinding,
            bool includeBindingDetails)
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
            PopulateCurveData(item, clip, binding, bindingKind, maxKeysPerBinding, includeBindingDetails);
            bindings.Add(item);

            var warning = BuildWarning(item);
            if (warning != null)
            {
                warnings.Add(warning);
            }
        }

        private static void PopulateCurveData(
            BindingItem item,
            AnimationClip clip,
            EditorCurveBinding binding,
            string bindingKind,
            int maxKeysPerBinding,
            bool includeBindingDetails)
        {
            if (bindingKind == "float_curve")
            {
                var curve = AnimationUtility.GetEditorCurve(clip, binding);
                var keys = curve != null ? curve.keys : Array.Empty<Keyframe>();
                item.keyframe_count = keys.Length;
                item.keys_truncated = keys.Length > maxKeysPerBinding;
                if (!includeBindingDetails)
                {
                    return;
                }

                item.curve_pre_wrap_mode = curve != null ? curve.preWrapMode.ToString() : null;
                item.curve_post_wrap_mode = curve != null ? curve.postWrapMode.ToString() : null;
                item.keys = keys.Take(maxKeysPerBinding).Select(key => new CurveKeyItem
                {
                    time = key.time,
                    value = key.value,
                    inTangent = key.inTangent,
                    outTangent = key.outTangent,
                    inWeight = key.inWeight,
                    outWeight = key.outWeight,
                    weightedMode = key.weightedMode.ToString()
                }).ToList();
                return;
            }

            var references = AnimationUtility.GetObjectReferenceCurve(clip, binding)
                ?? Array.Empty<ObjectReferenceKeyframe>();
            item.object_reference_key_count = references.Length;
            item.object_reference_keys_truncated = references.Length > maxKeysPerBinding;
            if (!includeBindingDetails)
            {
                return;
            }

            item.object_reference_keys = references.Take(maxKeysPerBinding).Select(key => new ObjectReferenceKeyItem
            {
                time = key.time,
                asset_path = key.value != null ? AssetDatabase.GetAssetPath(key.value) : "",
                name = key.value != null ? key.value.name : "",
                type_name = key.value != null ? key.value.GetType().Name : ""
            }).ToList();
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

        private static string WriteJson(
            string requestedPath,
            object payload,
            bool refreshAssets,
            out JObject beforeSnapshot,
            out JObject afterSnapshot)
        {
            var absolutePath = ResolveToAbsolutePath(requestedPath);
            var directory = Path.GetDirectoryName(absolutePath);
            if (string.IsNullOrEmpty(directory))
            {
                throw new InvalidOperationException($"Cannot resolve parent folder for animation binding scan path: {requestedPath}");
            }

            Directory.CreateDirectory(directory);
            var before = ReadFileSnapshot(absolutePath);
            File.WriteAllText(absolutePath, JsonConvert.SerializeObject(payload, Formatting.Indented), Encoding.UTF8);
            AssetDatabase.SaveAssets();
            var after = ReadFileSnapshot(absolutePath);
            beforeSnapshot = before;
            afterSnapshot = after;

            if (refreshAssets)
            {
                AssetDatabase.Refresh();
            }

            return absolutePath;
        }

        private static JObject ReadFileSnapshot(string absolutePath)
        {
            if (!File.Exists(absolutePath))
            {
                return new JObject
                {
                    ["exists"] = false,
                    ["absolutePath"] = absolutePath.Replace("\\", "/")
                };
            }
            var text = File.ReadAllText(absolutePath, Encoding.UTF8);
            JToken content;
            try
            {
                content = JToken.Parse(text);
            }
            catch (JsonReaderException)
            {
                content = new JValue(text);
            }
            return new JObject
            {
                ["exists"] = true,
                ["absolutePath"] = absolutePath.Replace("\\", "/"),
                ["length"] = new FileInfo(absolutePath).Length,
                ["content"] = content
            };
        }

        private static string ResolveToAbsolutePath(string requestedPath)
        {
            return VRCForgeOutputPathGuard.ResolveManagedProjectOutputPath(requestedPath, "Animation binding scan");
        }

        private static string ToAssetRelativePath(string absolutePath)
        {
            return VRCForgeOutputPathGuard.ToAssetRelativePath(absolutePath);
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
            public bool include_binding_details;
            public int max_keys_per_binding;
            public List<ClipBindingItem> clips;
            public List<WarningItem> warnings;
            public AnimationBindingsSummary summary;
            public string outputPath;
            public string absoluteOutputPath;
            public object before;
            public object after;
            public object affected;
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
            public bool loop_time;
            public bool loop_blend;
            public int binding_count;
            public int material_binding_count;
            public int object_toggle_binding_count;
            public int blendshape_binding_count;
            public int warning_count;
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
            public int keyframe_count;
            public bool keys_truncated;
            public string curve_pre_wrap_mode;
            public string curve_post_wrap_mode;
            public List<CurveKeyItem> keys;
            public int object_reference_key_count;
            public bool object_reference_keys_truncated;
            public List<ObjectReferenceKeyItem> object_reference_keys;
        }

        [Serializable]
        private class CurveKeyItem
        {
            public float time;
            public float value;
            public float inTangent;
            public float outTangent;
            public float inWeight;
            public float outWeight;
            public string weightedMode;
        }

        [Serializable]
        private class ObjectReferenceKeyItem
        {
            public float time;
            public string asset_path;
            public string name;
            public string type_name;
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
