using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using VRCForge.Core.MCP;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;

namespace VRCForge.Editor
{
    internal static class AvatarPrimitiveCrudCore
    {
        internal const string DefaultAssetDir = "Assets/VRCForge/Generated/AvatarPrimitives";

        internal static VRCAvatarDescriptor ResolveAvatarDescriptor(string avatarPath)
        {
            var descriptors = Resources.FindObjectsOfTypeAll<VRCAvatarDescriptor>()
                .Where(item => item != null && item.gameObject.scene.IsValid() && item.gameObject.scene.isLoaded && !EditorUtility.IsPersistent(item))
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

            return descriptors.FirstOrDefault(item => NormalizePath(GetTransformPath(item.transform)) == normalized)
                ?? descriptors.FirstOrDefault(item => item.name.Equals(avatarPath, StringComparison.OrdinalIgnoreCase))
                ?? throw new InvalidOperationException($"Avatar descriptor not found: {avatarPath}");
        }

        internal static string GetTransformPath(Transform transform)
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

        internal static string NormalizePath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim().Trim('/');
        }

        internal static string NormalizeAssetPath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim();
        }

        internal static string NormalizeAssetDir(string value)
        {
            var normalized = NormalizePath(value);
            return string.IsNullOrWhiteSpace(normalized) ? DefaultAssetDir : normalized;
        }

        internal static void EnsureAssetFolder(string assetPath)
        {
            var normalized = NormalizePath(assetPath);
            var parts = normalized.Split('/');
            if (parts.Length == 0 || parts[0] != "Assets")
            {
                throw new InvalidOperationException($"Generated asset folder must be under Assets: {assetPath}");
            }

            var current = "Assets";
            for (var index = 1; index < parts.Length; index++)
            {
                var next = $"{current}/{parts[index]}";
                if (!AssetDatabase.IsValidFolder(next))
                {
                    AssetDatabase.CreateFolder(current, parts[index]);
                }
                current = next;
            }
        }

        internal static string Sanitize(string value, string fallback)
        {
            var cleaned = new string((value ?? string.Empty).Select(c => char.IsLetterOrDigit(c) || c == '_' ? c : '_').ToArray()).Trim('_');
            return string.IsNullOrWhiteSpace(cleaned) ? fallback : cleaned;
        }

        internal static Type FindType(string fullNameOrShortName)
        {
            var requested = (fullNameOrShortName ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(requested))
            {
                return null;
            }
            if (requested.Equals("GameObject", StringComparison.OrdinalIgnoreCase)
                || requested.Equals("UnityEngine.GameObject", StringComparison.OrdinalIgnoreCase))
            {
                return typeof(GameObject);
            }
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type direct = null;
                try { direct = assembly.GetType(requested, false); } catch { direct = null; }
                if (direct != null)
                {
                    return direct;
                }
            }
            if (!requested.Contains("."))
            {
                var unityType = FindType("UnityEngine." + requested);
                if (unityType != null)
                {
                    return unityType;
                }
            }
            Type match = null;
            var count = 0;
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type[] types;
                try { types = assembly.GetTypes(); }
                catch (ReflectionTypeLoadException ex) { types = ex.Types.Where(t => t != null).ToArray(); }
                catch { continue; }
                foreach (var type in types)
                {
                    if (type == null || !string.Equals(type.Name, requested, StringComparison.Ordinal))
                    {
                        continue;
                    }
                    match = type;
                    count++;
                }
            }
            if (count == 1)
            {
                return match;
            }
            if (count > 1)
            {
                throw new InvalidOperationException($"Type '{requested}' is ambiguous; use a fully-qualified name.");
            }
            return null;
        }

        internal static AnimatorController GetFxController(VRCAvatarDescriptor descriptor)
        {
            if (descriptor.baseAnimationLayers == null)
            {
                return null;
            }
            foreach (var layer in descriptor.baseAnimationLayers)
            {
                if (layer.type == VRCAvatarDescriptor.AnimLayerType.FX && !layer.isDefault && layer.animatorController is AnimatorController controller)
                {
                    return controller;
                }
            }
            foreach (var layer in descriptor.baseAnimationLayers)
            {
                if (layer.type == VRCAvatarDescriptor.AnimLayerType.FX && layer.animatorController is AnimatorController controller)
                {
                    return controller;
                }
            }
            return null;
        }

        internal static AnimatorController ResolveAnimatorController(VRCAvatarDescriptor descriptor, JObject @params, string assetDir)
        {
            var explicitPath = NormalizeAssetPath(@params?["controllerPath"]?.ToString() ?? @params?["fxControllerPath"]?.ToString() ?? "");
            if (!string.IsNullOrWhiteSpace(explicitPath))
            {
                var explicitController = AssetDatabase.LoadAssetAtPath<AnimatorController>(explicitPath);
                if (explicitController == null)
                {
                    throw new InvalidOperationException($"AnimatorController not found: {explicitPath}");
                }
                return explicitController;
            }

            var existing = GetFxController(descriptor);
            if (existing != null)
            {
                return existing;
            }

            AvatarAuthoringCrudCore.EnsureAssetFolder(assetDir);
            return AvatarAuthoringCrudCore.EnsureFxController(descriptor, assetDir);
        }

        internal static Vector3 ReadVector3(JToken token, Vector3 fallback)
        {
            if (token == null)
            {
                return fallback;
            }
            if (token.Type == JTokenType.Array)
            {
                var array = (JArray)token;
                return new Vector3(
                    array.Count > 0 ? array[0].Value<float>() : fallback.x,
                    array.Count > 1 ? array[1].Value<float>() : fallback.y,
                    array.Count > 2 ? array[2].Value<float>() : fallback.z);
            }
            var obj = token as JObject;
            if (obj != null)
            {
                return new Vector3(
                    obj["x"]?.Value<float>() ?? fallback.x,
                    obj["y"]?.Value<float>() ?? fallback.y,
                    obj["z"]?.Value<float>() ?? fallback.z);
            }
            throw new InvalidOperationException("Vector3 value must be an array [x,y,z] or object {x,y,z}.");
        }

        internal static AnimatorState FindState(AnimatorStateMachine machine, string stateName)
        {
            if (machine == null || string.IsNullOrWhiteSpace(stateName))
            {
                return null;
            }
            foreach (var child in machine.states)
            {
                if (child.state != null && string.Equals(child.state.name, stateName, StringComparison.Ordinal))
                {
                    return child.state;
                }
            }
            foreach (var child in machine.stateMachines)
            {
                var nested = FindState(child.stateMachine, stateName);
                if (nested != null)
                {
                    return nested;
                }
            }
            return null;
        }

        internal static AnimatorStateMachine FindStateMachineForState(AnimatorStateMachine machine, string stateName)
        {
            if (machine == null || string.IsNullOrWhiteSpace(stateName))
            {
                return null;
            }
            if (machine.states.Any(child => child.state != null && string.Equals(child.state.name, stateName, StringComparison.Ordinal)))
            {
                return machine;
            }
            foreach (var child in machine.stateMachines)
            {
                var nested = FindStateMachineForState(child.stateMachine, stateName);
                if (nested != null)
                {
                    return nested;
                }
            }
            return null;
        }

        internal static string AssetPath(UnityEngine.Object obj)
        {
            return obj == null ? "" : AssetDatabase.GetAssetPath(obj);
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_read_avatar_descriptor",
        Summary = "Read VRCAvatarDescriptor core authoring fields: viewpoint, lip sync, visemes, expressions, playable layers, and eye-look summary.",
        Access = VRCForgeCommandAccess.ReadOnly
    )]
    public static class ReadAvatarDescriptorTool
    {
        public class Parameters
        {
            [VRCForgeInput("Optional avatar hierarchy path; Unity resolves the selected/single descriptor when omitted.", IsRequired = false)] public string avatarPath { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                @params = @params ?? new JObject();
                var descriptor = AvatarPrimitiveCrudCore.ResolveAvatarDescriptor(@params["avatarPath"]?.ToString() ?? "");
                return VRCForgeToolResult.Completed("Read avatar descriptor.", new
                {
                    ok = true,
                    avatarPath = AvatarPrimitiveCrudCore.GetTransformPath(descriptor.transform),
                    avatarName = descriptor.name,
                    viewPosition = new { x = descriptor.ViewPosition.x, y = descriptor.ViewPosition.y, z = descriptor.ViewPosition.z },
                    lipSync = descriptor.lipSync.ToString(),
                    visemeSkinnedMeshPath = descriptor.VisemeSkinnedMesh != null ? AvatarPrimitiveCrudCore.GetTransformPath(descriptor.VisemeSkinnedMesh.transform) : "",
                    visemeBlendShapes = descriptor.VisemeBlendShapes ?? Array.Empty<string>(),
                    expressionParametersPath = AvatarPrimitiveCrudCore.AssetPath(descriptor.expressionParameters),
                    expressionsMenuPath = AvatarPrimitiveCrudCore.AssetPath(descriptor.expressionsMenu),
                    baseAnimationLayers = DescribeLayers(descriptor.baseAnimationLayers),
                    specialAnimationLayers = DescribeLayers(descriptor.specialAnimationLayers),
                    customEyeLookSettings = DescribeObject(descriptor.customEyeLookSettings, 2)
                });
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Read avatar descriptor failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static object[] DescribeLayers(VRCAvatarDescriptor.CustomAnimLayer[] layers)
        {
            return (layers ?? Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>())
                .Select(layer => new
                {
                    type = layer.type.ToString(),
                    isDefault = layer.isDefault,
                    controllerPath = AvatarPrimitiveCrudCore.AssetPath(layer.animatorController),
                    controllerName = layer.animatorController != null ? layer.animatorController.name : ""
                })
                .Cast<object>()
                .ToArray();
        }

        private static object DescribeObject(object source, int depth)
        {
            if (source == null)
            {
                return null;
            }
            if (depth <= 0)
            {
                return source.ToString();
            }
            if (source is string || source.GetType().IsPrimitive || source.GetType().IsEnum)
            {
                return source.ToString();
            }
            if (source is UnityEngine.Object unityObject)
            {
                return new { name = unityObject.name, assetPath = AvatarPrimitiveCrudCore.AssetPath(unityObject) };
            }
            var result = new Dictionary<string, object>();
            foreach (var member in source.GetType().GetMembers(BindingFlags.Instance | BindingFlags.Public))
            {
                if (!(member is FieldInfo) && !(member is PropertyInfo))
                {
                    continue;
                }
                try
                {
                    var value = member is FieldInfo field ? field.GetValue(source) : ((PropertyInfo)member).GetValue(source);
                    result[member.Name] = DescribeObject(value, depth - 1);
                }
                catch
                {
                    // Some SDK properties are editor-only wrappers; ignore unreadable values.
                }
            }
            return result;
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_write_avatar_descriptor",
        Summary = "Write selected VRCAvatarDescriptor fields: viewpoint, lip sync, viseme mesh/blendshapes, expression assets, playable-layer controllers, and eye-look enable flag. Supports preview."
    )]
    public static class WriteAvatarDescriptorTool
    {
        public class Parameters
        {
            [VRCForgeInput("Optional avatar hierarchy path.", IsRequired = false)] public string avatarPath { get; set; } = "";
            [VRCForgeInput("Return the descriptor change plan without writing.", IsRequired = false)] public bool? preview { get; set; } = false;
            [VRCForgeInput("Optional viewpoint vector object with x, y, and z.", IsRequired = false)] public object viewPosition { get; set; }
            [VRCForgeInput("Optional avatar lip-sync mode.", IsRequired = false)] public string lipSync { get; set; } = "";
            [VRCForgeInput("Optional scene path to the viseme SkinnedMeshRenderer.", IsRequired = false)] public string visemeSkinnedMeshPath { get; set; } = "";
            [VRCForgeInput("Optional viseme blendshape-name array.", IsRequired = false)] public object[] visemeBlendShapes { get; set; }
            [VRCForgeInput("Optional expression-parameters asset path.", IsRequired = false)] public string expressionParametersPath { get; set; } = "";
            [VRCForgeInput("Optional expressions-menu asset path.", IsRequired = false)] public string expressionsMenuPath { get; set; } = "";
            [VRCForgeInput("Optional base playable-layer update array.", IsRequired = false)] public object[] baseAnimationLayers { get; set; }
            [VRCForgeInput("Optional special playable-layer update array.", IsRequired = false)] public object[] specialAnimationLayers { get; set; }
            [VRCForgeInput("Optional source avatar hierarchy path whose complete Eye Look settings and scene references will be copied.", IsRequired = false)] public string eyeLookSettingsSourceAvatarPath { get; set; } = "";
            [VRCForgeInput("Optional eye-look enabled flag.", IsRequired = false)] public bool? eyeLookEnabled { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            SavedSceneSnapshot beforeScene = null;
            var undoGroup = -1;
            var mutationStarted = false;
            var avatarPath = string.Empty;
            var descriptorGlobalObjectId = string.Empty;
            var beforeJson = string.Empty;
            var failureStage = "validation";
            bool? mutationApplied = null;
            try
            {
                @params = @params ?? new JObject();
                var descriptor = AvatarPrimitiveCrudCore.ResolveAvatarDescriptor(@params["avatarPath"]?.ToString() ?? "");
                var preview = @params["preview"]?.Value<bool?>() ?? false;
                avatarPath = AvatarPrimitiveCrudCore.GetTransformPath(descriptor.transform);
                beforeScene = ComponentCrudCore.ResolveSavedSceneFor(descriptor.gameObject);
                descriptorGlobalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(descriptor).ToString();
                beforeJson = EditorJsonUtility.ToJson(descriptor);
                var plan = BuildPlan(descriptor, @params);
                if (preview)
                {
                    return VRCForgeToolResult.Completed("Preview: would write avatar descriptor fields.", new { ok = true, preview = true, plan });
                }

                Undo.IncrementCurrentGroup();
                undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName("Write VRCForge avatar descriptor");
                Undo.RegisterCompleteObjectUndo(descriptor, "Write avatar descriptor");
                mutationStarted = true;
                failureStage = "unity_mutation";
                Apply(descriptor, @params);
                mutationApplied = true;
                var expectedJson = EditorJsonUtility.ToJson(descriptor);

                if (string.Equals(expectedJson, beforeJson, StringComparison.Ordinal))
                {
                    Undo.RevertAllDownToGroup(undoGroup);
                    mutationStarted = false;
                    var unchangedScene = SceneObjectCopyCore.ResolveSavedScene(
                        beforeScene.Path,
                        "unchanged avatar descriptor readback");
                    var unchangedObject = SceneObjectCopyCore.ResolveUniqueGameObject(
                        unchangedScene.Scene,
                        avatarPath,
                        "unchanged avatar descriptor target");
                    var unchangedDescriptor = unchangedObject.GetComponent<VRCAvatarDescriptor>()
                        ?? throw new InvalidOperationException("Avatar descriptor unchanged readback failed.");
                    var unchangedJson = EditorJsonUtility.ToJson(unchangedDescriptor);
                    return VRCForgeToolResult.Completed("Avatar descriptor already has the requested values.", new
                    {
                        ok = true,
                        preview = false,
                        changed = false,
                        action = "write_avatar_descriptor",
                        avatarPath,
                        changedFields = plan.changedFields,
                        before = JToken.Parse(beforeJson),
                        after = JToken.Parse(unchangedJson),
                        affected = new
                        {
                            count = plan.changedFields.Length,
                            items = plan.changedFields.Take(20).ToArray(),
                            handle = descriptorGlobalObjectId
                        },
                        scenePath = beforeScene.Path,
                        sceneSaved = true,
                        persistedReadback = true,
                        mutationStarted = false,
                        committed = true,
                        commitState = "committed",
                        checkpointRecoveryRequired = false
                    });
                }

                EditorUtility.SetDirty(descriptor);
                EditorUtility.SetDirty(descriptor.gameObject);
                AssetDatabase.SaveAssets();
                failureStage = "scene_save";
                var afterScene = ComponentCrudCore.SaveAndResolveScene(beforeScene);
                failureStage = "persisted_readback";
                var readbackObject = SceneObjectCopyCore.ResolveUniqueGameObject(
                    afterScene.Scene,
                    avatarPath,
                    "avatar descriptor target");
                var readbackDescriptor = readbackObject.GetComponent<VRCAvatarDescriptor>();
                if (readbackDescriptor == null
                    || !string.Equals(
                        GlobalObjectId.GetGlobalObjectIdSlow(readbackDescriptor).ToString(),
                        descriptorGlobalObjectId,
                        StringComparison.Ordinal)
                    || !string.Equals(EditorJsonUtility.ToJson(readbackDescriptor), expectedJson, StringComparison.Ordinal)
                    || afterScene.FileDigest == beforeScene.FileDigest)
                {
                    throw new InvalidOperationException("The avatar descriptor persisted readback was not exact.");
                }
                Undo.CollapseUndoOperations(undoGroup);
                return VRCForgeToolResult.Completed("Avatar descriptor updated.", new
                {
                    ok = true,
                    preview = false,
                    changed = true,
                    action = "write_avatar_descriptor",
                    avatarPath,
                    changedFields = plan.changedFields,
                    before = JToken.Parse(beforeJson),
                    after = JToken.Parse(EditorJsonUtility.ToJson(readbackDescriptor)),
                    affected = new
                    {
                        count = plan.changedFields.Length,
                        items = plan.changedFields.Take(20).ToArray(),
                        handle = descriptorGlobalObjectId
                    },
                    scenePath = afterScene.Path,
                    sceneSaved = true,
                    persistedReadback = true,
                    mutationStarted = true,
                    committed = true,
                    commitState = "committed",
                    checkpointRecoveryRequired = false
                });
            }
            catch (Exception ex)
            {
                if (mutationStarted && beforeScene != null && undoGroup >= 0)
                {
                    var cleanup = ComponentCrudCore.RestoreFailedMutation(
                        undoGroup,
                        beforeScene,
                        avatarPath,
                        restoredObject =>
                        {
                            var restoredDescriptor = restoredObject.GetComponent<VRCAvatarDescriptor>();
                            return restoredDescriptor != null
                                && string.Equals(
                                    GlobalObjectId.GetGlobalObjectIdSlow(restoredDescriptor).ToString(),
                                    descriptorGlobalObjectId,
                                    StringComparison.Ordinal)
                                && string.Equals(
                                    EditorJsonUtility.ToJson(restoredDescriptor),
                                    beforeJson,
                                    StringComparison.Ordinal);
                        });
                    return ComponentCrudCore.FailedMutationResult(
                        "avatar_descriptor_failed_after_mutation",
                        "write_avatar_descriptor",
                        failureStage,
                        ex,
                        beforeScene,
                        cleanup,
                        mutationApplied);
                }
                return VRCForgeToolResult.Failed($"Write avatar descriptor failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static DescriptorPlan BuildPlan(VRCAvatarDescriptor descriptor, JObject @params)
        {
            var changed = new List<string>();
            foreach (var key in new[] { "viewPosition", "lipSync", "visemeSkinnedMeshPath", "visemeBlendShapes", "expressionParametersPath", "expressionsMenuPath", "baseAnimationLayers", "specialAnimationLayers", "eyeLookSettingsSourceAvatarPath", "eyeLookEnabled" })
            {
                if (@params[key] != null)
                {
                    changed.Add(key);
                }
            }
            var eyeLookSourcePath = string.Empty;
            if (@params["eyeLookSettingsSourceAvatarPath"] != null)
            {
                var sourceDescriptor = AvatarPrimitiveCrudCore.ResolveAvatarDescriptor(
                    @params["eyeLookSettingsSourceAvatarPath"]?.ToString() ?? string.Empty);
                eyeLookSourcePath = AvatarPrimitiveCrudCore.GetTransformPath(sourceDescriptor.transform);
            }
            return new DescriptorPlan
            {
                avatarPath = AvatarPrimitiveCrudCore.GetTransformPath(descriptor.transform),
                avatarName = descriptor.name,
                changedFields = changed,
                eyeLookSettingsSourceAvatarPath = eyeLookSourcePath
            };
        }

        private static void Apply(VRCAvatarDescriptor descriptor, JObject @params)
        {
            if (@params["viewPosition"] != null)
            {
                descriptor.ViewPosition = AvatarPrimitiveCrudCore.ReadVector3(@params["viewPosition"], descriptor.ViewPosition);
            }
            if (@params["lipSync"] != null)
            {
                descriptor.lipSync = ParseEnum(@params["lipSync"].ToString(), descriptor.lipSync);
            }
            if (@params["visemeSkinnedMeshPath"] != null)
            {
                descriptor.VisemeSkinnedMesh = ResolveSceneComponent<SkinnedMeshRenderer>(@params["visemeSkinnedMeshPath"].ToString());
            }
            if (@params["visemeBlendShapes"] is JArray visemes)
            {
                descriptor.VisemeBlendShapes = visemes.Select(item => item?.ToString() ?? "").ToArray();
            }
            if (@params["expressionParametersPath"] != null)
            {
                descriptor.expressionParameters = LoadAssetOrNull<VRCExpressionParameters>(@params["expressionParametersPath"].ToString());
            }
            if (@params["expressionsMenuPath"] != null)
            {
                descriptor.expressionsMenu = LoadAssetOrNull<VRCExpressionsMenu>(@params["expressionsMenuPath"].ToString());
            }
            if (@params["baseAnimationLayers"] is JArray baseLayers)
            {
                descriptor.baseAnimationLayers = ApplyLayers(descriptor.baseAnimationLayers, baseLayers);
            }
            if (@params["specialAnimationLayers"] is JArray specialLayers)
            {
                descriptor.specialAnimationLayers = ApplyLayers(descriptor.specialAnimationLayers, specialLayers);
            }
            if (@params["eyeLookSettingsSourceAvatarPath"] != null)
            {
                var sourceDescriptor = AvatarPrimitiveCrudCore.ResolveAvatarDescriptor(
                    @params["eyeLookSettingsSourceAvatarPath"]?.ToString() ?? string.Empty);
                descriptor.customEyeLookSettings = sourceDescriptor.customEyeLookSettings;
                descriptor.enableEyeLook = sourceDescriptor.enableEyeLook;
            }
            if (@params["eyeLookEnabled"] != null)
            {
                descriptor.enableEyeLook = @params["eyeLookEnabled"].Value<bool>();
            }
        }

        private static VRCAvatarDescriptor.CustomAnimLayer[] ApplyLayers(VRCAvatarDescriptor.CustomAnimLayer[] existing, JArray updates)
        {
            var layers = (existing ?? Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>()).ToList();
            foreach (var token in updates.OfType<JObject>())
            {
                var typeText = token["type"]?.ToString() ?? "";
                if (string.IsNullOrWhiteSpace(typeText))
                {
                    throw new InvalidOperationException("Playable layer update requires type.");
                }
                var layerType = ParseEnum(typeText, VRCAvatarDescriptor.AnimLayerType.FX);
                var index = layers.FindIndex(layer => layer.type == layerType);
                var layer = index >= 0 ? layers[index] : new VRCAvatarDescriptor.CustomAnimLayer { type = layerType };
                if (token["isDefault"] != null)
                {
                    layer.isDefault = token["isDefault"].Value<bool>();
                }
                if (token["controllerPath"] != null)
                {
                    layer.animatorController = LoadAssetOrNull<RuntimeAnimatorController>(token["controllerPath"].ToString());
                    if (layer.animatorController != null)
                    {
                        layer.isDefault = false;
                    }
                }
                if (index >= 0)
                {
                    layers[index] = layer;
                }
                else
                {
                    layers.Add(layer);
                }
            }
            return layers.ToArray();
        }

        private static T ParseEnum<T>(string value, T fallback) where T : struct
        {
            return Enum.TryParse(value, true, out T parsed) ? parsed : fallback;
        }

        private static T LoadAssetOrNull<T>(string path) where T : UnityEngine.Object
        {
            var normalized = AvatarPrimitiveCrudCore.NormalizeAssetPath(path);
            if (string.IsNullOrWhiteSpace(normalized))
            {
                return null;
            }
            var asset = AssetDatabase.LoadAssetAtPath<T>(normalized);
            if (asset == null)
            {
                throw new InvalidOperationException($"Asset not found as {typeof(T).Name}: {normalized}");
            }
            return asset;
        }

        private static T ResolveSceneComponent<T>(string path) where T : Component
        {
            var normalized = AvatarPrimitiveCrudCore.NormalizePath(path);
            if (string.IsNullOrWhiteSpace(normalized))
            {
                return null;
            }
            foreach (var item in Resources.FindObjectsOfTypeAll<T>())
            {
                if (item != null && item.gameObject.scene.IsValid() && item.gameObject.scene.isLoaded && !EditorUtility.IsPersistent(item)
                    && AvatarPrimitiveCrudCore.NormalizePath(AvatarPrimitiveCrudCore.GetTransformPath(item.transform)) == normalized)
                {
                    return item;
                }
            }
            throw new InvalidOperationException($"Scene component not found: {path}");
        }

        private class DescriptorPlan
        {
            public string avatarPath;
            public string avatarName;
            public List<string> changedFields;
            public string eyeLookSettingsSourceAvatarPath;
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_write_animation_curve",
        Summary = "Create, replace, delete, or retarget one AnimationClip curve binding. When to use: one exact curve edit or a lossless binding-name migration. When NOT to use: bulk workflow planning or silent destination overwrite. Supports preview."
    )]
    public static class WriteAnimationCurveTool
    {
        public class Parameters
        {
            [VRCForgeInput("Curve action: set_curve, delete_curve, or retarget_curve.", IsRequired = false)] public string action { get; set; } = "set_curve";
            [VRCForgeInput("AnimationClip asset path.", IsRequired = true)] public string clipPath { get; set; } = "";
            [VRCForgeInput("Return the curve change plan without writing.", IsRequired = false)] public bool? preview { get; set; } = false;
            [VRCForgeInput("Optional relative binding path.", IsRequired = false)] public string bindingPath { get; set; } = "";
            [VRCForgeInput("Optional compatibility alias for bindingPath.", IsRequired = false)] public string objectPath { get; set; } = "";
            [VRCForgeInput("Binding component type.", IsRequired = false)] public string componentType { get; set; } = "GameObject";
            [VRCForgeInput("Serialized animation property name.", IsRequired = true)] public string propertyName { get; set; } = "";
            [VRCForgeInput("Retarget source binding path; defaults to bindingPath.", IsRequired = false)] public string sourceBindingPath { get; set; } = "";
            [VRCForgeInput("Retarget source component type; defaults to componentType.", IsRequired = false)] public string sourceComponentType { get; set; } = "";
            [VRCForgeInput("Retarget source serialized property name.", IsRequired = false)] public string sourcePropertyName { get; set; } = "";
            [VRCForgeInput("Delete the source binding after retargeting; false copies it.", IsRequired = false)] public bool? deleteSource { get; set; } = true;
            [VRCForgeInput("Allow replacing an existing destination binding. Defaults false.", IsRequired = false)] public bool? overwriteExisting { get; set; } = false;
            [VRCForgeInput("Optional animation keyframe array.", IsRequired = false)] public object[] keys { get; set; }
            [VRCForgeInput("Optional constant curve value.", IsRequired = false)] public float? constantFloat { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                @params = @params ?? new JObject();
                var action = NormalizeAction(@params["action"]?.ToString() ?? "set_curve");
                var clipPath = AvatarPrimitiveCrudCore.NormalizeAssetPath(@params["clipPath"]?.ToString() ?? "");
                var preview = @params["preview"]?.Value<bool?>() ?? false;
                if (string.IsNullOrWhiteSpace(clipPath))
                {
                    return VRCForgeToolResult.Failed("clipPath is required.");
                }
                var bindingPath = AvatarPrimitiveCrudCore.NormalizePath(@params["bindingPath"]?.ToString() ?? @params["objectPath"]?.ToString() ?? "");
                var componentTypeText = @params["componentType"]?.ToString() ?? "GameObject";
                var propertyName = (@params["propertyName"]?.ToString() ?? "").Trim();
                if (string.IsNullOrWhiteSpace(propertyName))
                {
                    return VRCForgeToolResult.Failed("propertyName is required.");
                }
                var type = AvatarPrimitiveCrudCore.FindType(componentTypeText)
                    ?? throw new InvalidOperationException($"Binding component type not found: {componentTypeText}");
                var binding = new EditorCurveBinding { path = bindingPath, type = type, propertyName = propertyName };
                var existingClip = AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath);
                EditorCurveBinding? sourceBinding = null;
                var deleteSource = @params["deleteSource"]?.Value<bool?>() ?? true;
                var overwriteExisting = @params["overwriteExisting"]?.Value<bool?>() ?? false;
                object plan;
                if (action == "retarget_curve")
                {
                    var sourceBindingPathText = @params["sourceBindingPath"]?.ToString();
                    if (string.IsNullOrWhiteSpace(sourceBindingPathText)) sourceBindingPathText = bindingPath;
                    var sourceBindingPath = AvatarPrimitiveCrudCore.NormalizePath(sourceBindingPathText);
                    var sourceComponentTypeText = @params["sourceComponentType"]?.ToString();
                    if (string.IsNullOrWhiteSpace(sourceComponentTypeText)) sourceComponentTypeText = componentTypeText;
                    var sourceType = AvatarPrimitiveCrudCore.FindType(sourceComponentTypeText)
                        ?? throw new InvalidOperationException($"Source binding component type not found: {sourceComponentTypeText}");
                    var sourcePropertyName = (@params["sourcePropertyName"]?.ToString() ?? "").Trim();
                    if (string.IsNullOrWhiteSpace(sourcePropertyName))
                    {
                        return VRCForgeToolResult.Failed("sourcePropertyName is required for retarget_curve.");
                    }
                    sourceBinding = new EditorCurveBinding
                    {
                        path = sourceBindingPath,
                        type = sourceType,
                        propertyName = sourcePropertyName
                    };
                    var sourceCurve = existingClip == null
                        ? null
                        : AnimationUtility.GetEditorCurve(existingClip, sourceBinding.Value);
                    var destinationCurve = existingClip == null
                        ? null
                        : AnimationUtility.GetEditorCurve(existingClip, binding);
                    plan = new
                    {
                        action,
                        clipPath,
                        sourceBindingPath,
                        sourceComponentType = sourceType.FullName,
                        sourcePropertyName,
                        bindingPath,
                        componentType = type.FullName,
                        propertyName,
                        sourceExists = sourceCurve != null,
                        destinationExists = destinationCurve != null,
                        deleteSource,
                        overwriteExisting,
                        willCreateClip = false
                    };
                }
                else
                {
                    plan = new
                    {
                        action,
                        clipPath,
                        bindingPath,
                        componentType = type.FullName,
                        propertyName,
                        willCreateClip = existingClip == null && action == "set_curve",
                        keyCount = (@params["keys"] as JArray)?.Count ?? 0,
                        constantFloat = @params["constantFloat"]?.Value<float?>()
                    };
                }
                if (preview)
                {
                    return VRCForgeToolResult.Completed($"Preview: would {action} on AnimationClip '{clipPath}'.", new { ok = true, preview = true, plan });
                }

                var clip = LoadOrCreateClip(clipPath, action == "set_curve");
                if (clip == null)
                {
                    return VRCForgeToolResult.Failed($"AnimationClip not found for action '{action}': {clipPath}");
                }
                if (action == "retarget_curve")
                {
                    var source = sourceBinding.Value;
                    if (BindingsEqual(source, binding))
                    {
                        return VRCForgeToolResult.Failed("Retarget source and destination bindings are identical; no write was performed.");
                    }
                    var sourceCurve = AnimationUtility.GetEditorCurve(clip, source);
                    if (sourceCurve == null)
                    {
                        return VRCForgeToolResult.Failed(
                            $"Retarget source curve was not found: path='{source.path}', type='{source.type.FullName}', property='{source.propertyName}'.");
                    }
                    var destinationCurve = AnimationUtility.GetEditorCurve(clip, binding);
                    if (destinationCurve != null && !overwriteExisting)
                    {
                        return VRCForgeToolResult.Failed(
                            $"Retarget destination already has a curve and overwriteExisting is false: path='{binding.path}', type='{binding.type.FullName}', property='{binding.propertyName}'.");
                    }
                    var copiedCurve = new AnimationCurve(sourceCurve.keys)
                    {
                        preWrapMode = sourceCurve.preWrapMode,
                        postWrapMode = sourceCurve.postWrapMode
                    };
                    Undo.RegisterCompleteObjectUndo(clip, "Retarget animation curve binding");
                    AnimationUtility.SetEditorCurve(clip, binding, copiedCurve);
                    if (deleteSource)
                    {
                        AnimationUtility.SetEditorCurve(clip, source, null);
                    }
                }
                else
                {
                    Undo.RegisterCompleteObjectUndo(clip, "Write animation curve");
                    if (action == "delete_curve")
                    {
                        AnimationUtility.SetEditorCurve(clip, binding, null);
                    }
                    else
                    {
                        AnimationUtility.SetEditorCurve(clip, binding, BuildCurve(@params));
                    }
                }
                EditorUtility.SetDirty(clip);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();
                return VRCForgeToolResult.Completed($"Animation curve action '{action}' completed.", new
                {
                    ok = true,
                    preview = false,
                    action,
                    clipPath = AssetDatabase.GetAssetPath(clip),
                    bindingPath,
                    componentType = type.FullName,
                    propertyName,
                    sourceBindingPath = sourceBinding?.path,
                    sourceComponentType = sourceBinding?.type?.FullName,
                    sourcePropertyName = sourceBinding?.propertyName,
                    deleteSource = action == "retarget_curve" ? (bool?)deleteSource : null,
                    overwriteExisting = action == "retarget_curve" ? (bool?)overwriteExisting : null
                });
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Write animation curve failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static string NormalizeAction(string value)
        {
            var action = (value ?? "").Trim().ToLowerInvariant().Replace("-", "_");
            if (action == "delete" || action == "remove")
            {
                return "delete_curve";
            }
            if (action == "retarget" || action == "move_binding" || action == "copy_curve")
            {
                return "retarget_curve";
            }
            if (action == "set" || action == "create" || action == "replace")
            {
                return "set_curve";
            }
            if (action == "set_curve" || action == "delete_curve" || action == "retarget_curve")
            {
                return action;
            }
            throw new InvalidOperationException($"Unsupported animation curve action: {value}");
        }

        private static bool BindingsEqual(EditorCurveBinding left, EditorCurveBinding right)
        {
            return string.Equals(left.path, right.path, StringComparison.Ordinal)
                && left.type == right.type
                && string.Equals(left.propertyName, right.propertyName, StringComparison.Ordinal);
        }

        private static AnimationClip LoadOrCreateClip(string clipPath, bool create)
        {
            var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath);
            if (clip != null || !create)
            {
                return clip;
            }
            var folder = Path.GetDirectoryName(clipPath)?.Replace("\\", "/") ?? "";
            AvatarPrimitiveCrudCore.EnsureAssetFolder(folder);
            clip = new AnimationClip { name = Path.GetFileNameWithoutExtension(clipPath) };
            AssetDatabase.CreateAsset(clip, clipPath);
            Undo.RegisterCreatedObjectUndo(clip, "Create animation clip");
            return clip;
        }

        private static AnimationCurve BuildCurve(JObject @params)
        {
            if (@params["constantFloat"] != null)
            {
                var value = @params["constantFloat"].Value<float>();
                return AnimationCurve.Constant(0f, 0f, value);
            }
            if (@params["keys"] is JArray keys && keys.Count > 0)
            {
                var keyframes = new List<Keyframe>();
                foreach (var item in keys.OfType<JObject>())
                {
                    var time = item["time"]?.Value<float>() ?? 0f;
                    var curveValue = item["curveValue"]?.Value<float?>()
                        ?? item["value"]?.Value<float?>()
                        ?? 0f;
                    var key = new Keyframe(time, curveValue);
                    if (item["inTangent"] != null) key.inTangent = item["inTangent"].Value<float>();
                    if (item["outTangent"] != null) key.outTangent = item["outTangent"].Value<float>();
                    keyframes.Add(key);
                }
                return new AnimationCurve(keyframes.OrderBy(item => item.time).ToArray());
            }
            throw new InvalidOperationException("Set curve requires constantFloat or keys.");
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_manage_expression_parameters",
        Summary = "Manage existing VRCExpressionParameters entries: update, delete, rename, or reorder. Use vrc_ensure_expression_parameter for first-time create. Supports preview."
    )]
    public static class ManageExpressionParametersTool
    {
        public class Parameters
        {
            [VRCForgeInput("Parameter management action: update, delete, rename, or reorder.", IsRequired = true)] public string action { get; set; } = "";
            [VRCForgeInput("Return the parameter-management plan without writing.", IsRequired = false)] public bool? preview { get; set; } = false;
            [VRCForgeInput("Optional avatar hierarchy path.", IsRequired = false)] public string avatarPath { get; set; } = "";
            [VRCForgeInput("Existing parameter name for update, delete, or rename.", IsRequired = false)] public string parameterName { get; set; } = "";
            [VRCForgeInput("New parameter name for rename.", IsRequired = false)] public string newName { get; set; } = "";
            [VRCForgeInput("Ordered parameter-name array for reorder.", IsRequired = false)] public object[] orderNames { get; set; }
            [VRCForgeInput("Optional expression parameter value type for update.", IsRequired = false)] public string valueType { get; set; } = "";
            [VRCForgeInput("Optional expression parameter default value for update.", IsRequired = false)] public float? defaultValue { get; set; }
            [VRCForgeInput("Optional persisted flag for update.", IsRequired = false)] public bool? saved { get; set; }
            [VRCForgeInput("Optional network-synchronization flag for update.", IsRequired = false)] public bool? networkSynced { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                @params = @params ?? new JObject();
                var action = NormalizeAction(@params["action"]?.ToString() ?? "");
                var preview = @params["preview"]?.Value<bool?>() ?? false;
                var descriptor = AvatarPrimitiveCrudCore.ResolveAvatarDescriptor(@params["avatarPath"]?.ToString() ?? "");
                var asset = descriptor.expressionParameters ?? throw new InvalidOperationException("Avatar has no VRCExpressionParameters asset.");
                var plan = BuildPlan(action, descriptor, asset, @params);
                if (preview)
                {
                    return VRCForgeToolResult.Completed($"Preview: would manage expression parameters ({action}).", new { ok = true, preview = true, plan });
                }
                var before = DescribeParameters(asset);

                Undo.RegisterCompleteObjectUndo(asset, "Manage expression parameters");
                Apply(action, asset, @params);
                EditorUtility.SetDirty(asset);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();
                var assetPath = AssetDatabase.GetAssetPath(asset);
                AssetDatabase.ImportAsset(
                    assetPath,
                    ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                var readbackAsset = AssetDatabase.LoadAssetAtPath<VRCExpressionParameters>(assetPath);
                if (readbackAsset == null)
                {
                    throw new InvalidOperationException("Expression parameter asset readback failed.");
                }
                var after = DescribeParameters(readbackAsset);
                var affectedNames = before
                    .Select(item => item.name)
                    .Concat(after.Select(item => item.name))
                    .Distinct(StringComparer.Ordinal)
                    .OrderBy(name => name, StringComparer.Ordinal)
                    .ToArray();
                return VRCForgeToolResult.Completed($"Expression parameter action '{action}' completed.", new
                {
                    ok = true,
                    preview = false,
                    action,
                    assetPath,
                    parameterCount = after.Count,
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
                return VRCForgeToolResult.Failed($"Manage expression parameters failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private sealed class ParameterState
        {
            public string name;
            public string valueType;
            public float defaultValue;
            public bool saved;
            public bool networkSynced;
        }

        private static List<ParameterState> DescribeParameters(VRCExpressionParameters asset)
        {
            return (asset.parameters ?? Array.Empty<VRCExpressionParameters.Parameter>())
                .Where(parameter => parameter != null)
                .Select(parameter => new ParameterState
                {
                    name = parameter.name,
                    valueType = parameter.valueType.ToString(),
                    defaultValue = parameter.defaultValue,
                    saved = parameter.saved,
                    networkSynced = parameter.networkSynced
                })
                .ToList();
        }

        private static string NormalizeAction(string value)
        {
            var action = (value ?? "").Trim().ToLowerInvariant().Replace("-", "_");
            if (action == "remove") return "delete";
            if (action == "set" || action == "edit") return "update";
            if (!new[] { "update", "delete", "rename", "reorder" }.Contains(action))
            {
                throw new InvalidOperationException("action must be one of: update, delete, rename, reorder.");
            }
            return action;
        }

        private static object BuildPlan(string action, VRCAvatarDescriptor descriptor, VRCExpressionParameters asset, JObject @params)
        {
            return new
            {
                action,
                avatarPath = AvatarPrimitiveCrudCore.GetTransformPath(descriptor.transform),
                avatarName = descriptor.name,
                assetPath = AssetDatabase.GetAssetPath(asset),
                parameterName = @params["parameterName"]?.ToString() ?? "",
                newName = @params["newName"]?.ToString() ?? "",
                orderNames = (@params["orderNames"] as JArray)?.Select(item => item.ToString()).ToArray() ?? Array.Empty<string>(),
                currentCount = asset.parameters?.Length ?? 0
            };
        }

        private static void Apply(string action, VRCExpressionParameters asset, JObject @params)
        {
            var parameters = asset.parameters?.Where(item => item != null).ToList() ?? new List<VRCExpressionParameters.Parameter>();
            var name = (@params["parameterName"]?.ToString() ?? "").Trim();
            if (action != "reorder" && string.IsNullOrWhiteSpace(name))
            {
                throw new InvalidOperationException("parameterName is required.");
            }
            var index = parameters.FindIndex(item => item.name == name);
            if (action != "reorder" && index < 0)
            {
                throw new InvalidOperationException($"Expression parameter not found: {name}");
            }
            if (action == "delete")
            {
                parameters.RemoveAt(index);
            }
            else if (action == "rename")
            {
                var newName = (@params["newName"]?.ToString() ?? "").Trim();
                if (string.IsNullOrWhiteSpace(newName)) throw new InvalidOperationException("newName is required.");
                var item = parameters[index];
                item.name = newName;
                parameters[index] = item;
            }
            else if (action == "update")
            {
                var item = parameters[index];
                if (@params["valueType"] != null) item.valueType = AvatarAuthoringCrudCore.ParseExpressionParameterType(@params["valueType"].ToString());
                if (@params["defaultValue"] != null) item.defaultValue = @params["defaultValue"].Value<float>();
                if (@params["saved"] != null) item.saved = @params["saved"].Value<bool>();
                if (@params["networkSynced"] != null) item.networkSynced = @params["networkSynced"].Value<bool>();
                parameters[index] = item;
            }
            else if (action == "reorder")
            {
                var orderNames = (@params["orderNames"] as JArray)?.Select(item => item.ToString()).Where(item => !string.IsNullOrWhiteSpace(item)).ToList()
                    ?? new List<string>();
                if (orderNames.Count == 0)
                {
                    throw new InvalidOperationException("orderNames is required for reorder.");
                }
                var byName = parameters.ToDictionary(item => item.name, item => item);
                var ordered = new List<VRCExpressionParameters.Parameter>();
                foreach (var orderedName in orderNames)
                {
                    if (!byName.TryGetValue(orderedName, out var item))
                    {
                        throw new InvalidOperationException($"Cannot reorder: parameter not found: {orderedName}");
                    }
                    ordered.Add(item);
                }
                ordered.AddRange(parameters.Where(item => !orderNames.Contains(item.name)));
                parameters = ordered;
            }
            asset.parameters = parameters.ToArray();
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_manage_expression_menu",
        Summary = "Manage VRCExpressionsMenu controls: create, update, delete, or reorder controls in a root menu or submenu. Supports preview."
    )]
    public static class ManageExpressionMenuTool
    {
        public class Parameters
        {
            [VRCForgeInput("Menu management action: create, update, delete, or reorder.", IsRequired = true)] public string action { get; set; } = "";
            [VRCForgeInput("Return the menu-management plan without writing.", IsRequired = false)] public bool? preview { get; set; } = false;
            [VRCForgeInput("Optional avatar hierarchy path.", IsRequired = false)] public string avatarPath { get; set; } = "";
            [VRCForgeInput("Asset directory for a newly created root or submenu.", IsRequired = false)] public string assetDir { get; set; } = "";
            [VRCForgeInput("Optional slash-delimited submenu path.", IsRequired = false)] public string menuPath { get; set; } = "";
            [VRCForgeInput("Existing or new control name.", IsRequired = false)] public string controlName { get; set; } = "";
            [VRCForgeInput("Optional existing control index.", IsRequired = false)] public int? controlIndex { get; set; }
            [VRCForgeInput("Optional new control name.", IsRequired = false)] public string newName { get; set; } = "";
            [VRCForgeInput("Optional expression-menu control type.", IsRequired = false)] public string controlType { get; set; } = "";
            [VRCForgeInput("Optional control float value.", IsRequired = false)] public float? controlFloat { get; set; }
            [VRCForgeInput("Optional compatibility control value.", IsRequired = false)] public float? value { get; set; }
            [VRCForgeInput("Optional bound expression parameter name.", IsRequired = false)] public string parameterName { get; set; } = "";
            [VRCForgeInput("Optional icon asset path.", IsRequired = false)] public string iconAssetPath { get; set; } = "";
            [VRCForgeInput("Optional submenu asset path.", IsRequired = false)] public string subMenuAssetPath { get; set; } = "";
            [VRCForgeInput("Create a submenu when one is not assigned.", IsRequired = false)] public bool? createSubMenu { get; set; }
            [VRCForgeInput("Optional submenu parameter-name array.", IsRequired = false)] public object[] subParameters { get; set; }
            [VRCForgeInput("Ordered control-name array for reorder.", IsRequired = false)] public object[] orderNames { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                @params = @params ?? new JObject();
                var action = NormalizeAction(@params["action"]?.ToString() ?? "");
                var preview = @params["preview"]?.Value<bool?>() ?? false;
                var descriptor = AvatarPrimitiveCrudCore.ResolveAvatarDescriptor(@params["avatarPath"]?.ToString() ?? "");
                var assetDir = AvatarPrimitiveCrudCore.NormalizeAssetDir(@params["assetDir"]?.ToString() ?? "");
                var root = descriptor.expressionsMenu;
                var normalizedMenuPath = AvatarPrimitiveCrudCore.NormalizePath(@params["menuPath"]?.ToString() ?? "");
                var plan = BuildPlan(action, descriptor, root, @params);
                if (preview)
                {
                    return VRCForgeToolResult.Completed($"Preview: would manage expression menu ({action}).", new { ok = true, preview = true, plan });
                }

                var beforeAssetPaths = CollectMenuAssetPaths(root);
                var before = DescribeMenu(TryResolveMenu(root, normalizedMenuPath));

                if (root == null)
                {
                    root = AvatarAuthoringCrudCore.EnsureRootMenuAsset(descriptor, assetDir);
                }
                var target = ResolveMenu(root, normalizedMenuPath, create: action == "create", assetDir: assetDir);
                Undo.RegisterCompleteObjectUndo(target, "Manage expression menu");
                Apply(action, target, @params, assetDir);
                EditorUtility.SetDirty(target);
                EditorUtility.SetDirty(root);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();
                var rootAssetPath = AssetDatabase.GetAssetPath(root);
                AssetDatabase.ImportAsset(rootAssetPath, ImportAssetOptions.ForceSynchronousImport);
                var readbackRoot = AssetDatabase.LoadAssetAtPath<VRCExpressionsMenu>(rootAssetPath);
                if (readbackRoot == null)
                {
                    throw new InvalidOperationException($"Expression menu readback failed: {rootAssetPath}");
                }
                var readbackTarget = ResolveMenu(readbackRoot, normalizedMenuPath, create: false, assetDir: assetDir);
                var after = DescribeMenu(readbackTarget);
                var afterAssetPaths = CollectMenuAssetPaths(readbackRoot);
                var affectedPaths = afterAssetPaths
                    .Where(path => !beforeAssetPaths.Contains(path))
                    .Concat(new[] { AssetDatabase.GetAssetPath(readbackTarget) })
                    .Where(path => !string.IsNullOrWhiteSpace(path))
                    .Distinct(StringComparer.OrdinalIgnoreCase)
                    .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
                    .ToList();
                return VRCForgeToolResult.Completed($"Expression menu action '{action}' completed.", new
                {
                    ok = true,
                    preview = false,
                    action,
                    rootMenuPath = rootAssetPath,
                    menuPath = @params["menuPath"]?.ToString() ?? "",
                    controlCount = readbackTarget.controls?.Count ?? 0,
                    before,
                    after,
                    affected = new
                    {
                        count = affectedPaths.Count,
                        items = affectedPaths.Take(20).ToArray(),
                        handle = AssetDatabase.AssetPathToGUID(rootAssetPath)
                    }
                });
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Manage expression menu failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static string NormalizeAction(string value)
        {
            var action = (value ?? "").Trim().ToLowerInvariant().Replace("-", "_");
            if (action == "add") return "create";
            if (action == "remove") return "delete";
            if (action == "set" || action == "edit") return "update";
            if (!new[] { "create", "update", "delete", "reorder" }.Contains(action))
            {
                throw new InvalidOperationException("action must be one of: create, update, delete, reorder.");
            }
            return action;
        }

        private static object BuildPlan(string action, VRCAvatarDescriptor descriptor, VRCExpressionsMenu root, JObject @params)
        {
            return new
            {
                action,
                avatarPath = AvatarPrimitiveCrudCore.GetTransformPath(descriptor.transform),
                avatarName = descriptor.name,
                rootMenuPath = AssetDatabase.GetAssetPath(root),
                menuPath = @params["menuPath"]?.ToString() ?? "",
                controlName = @params["controlName"]?.ToString() ?? "",
                controlIndex = @params["controlIndex"]?.Value<int?>(),
                currentRootControlCount = root?.controls?.Count ?? 0
            };
        }

        private static VRCExpressionsMenu ResolveMenu(VRCExpressionsMenu root, string menuPath, bool create, string assetDir)
        {
            if (root == null)
            {
                throw new InvalidOperationException("Avatar has no expressions menu.");
            }
            if (root.controls == null)
            {
                root.controls = new List<VRCExpressionsMenu.Control>();
            }
            var current = root;
            foreach (var raw in menuPath.Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries))
            {
                var part = raw.Trim();
                var existing = current.controls.FirstOrDefault(control => control != null && control.type == VRCExpressionsMenu.Control.ControlType.SubMenu && control.name == part && control.subMenu != null);
                if (existing != null)
                {
                    current = existing.subMenu;
                    if (current.controls == null) current.controls = new List<VRCExpressionsMenu.Control>();
                    continue;
                }
                if (!create)
                {
                    throw new InvalidOperationException($"Submenu not found: {part}");
                }
                AvatarPrimitiveCrudCore.EnsureAssetFolder(assetDir);
                var sub = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
                sub.controls = new List<VRCExpressionsMenu.Control>();
                var subPath = AssetDatabase.GenerateUniqueAssetPath($"{assetDir}/{AvatarPrimitiveCrudCore.Sanitize(part, "Menu")}_SubMenu.asset");
                AssetDatabase.CreateAsset(sub, subPath);
                Undo.RegisterCreatedObjectUndo(sub, "Create submenu");
                current.controls.Add(new VRCExpressionsMenu.Control { name = part, type = VRCExpressionsMenu.Control.ControlType.SubMenu, subMenu = sub });
                EditorUtility.SetDirty(current);
                current = sub;
            }
            return current;
        }

        private static VRCExpressionsMenu TryResolveMenu(VRCExpressionsMenu root, string menuPath)
        {
            var current = root;
            if (current == null)
            {
                return null;
            }
            foreach (var raw in menuPath.Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries))
            {
                var part = raw.Trim();
                var control = current.controls?.FirstOrDefault(item =>
                    item != null
                    && item.type == VRCExpressionsMenu.Control.ControlType.SubMenu
                    && item.name == part
                    && item.subMenu != null);
                if (control == null)
                {
                    return null;
                }
                current = control.subMenu;
            }
            return current;
        }

        private static JToken DescribeMenu(VRCExpressionsMenu menu)
        {
            if (menu == null)
            {
                return new JObject { ["exists"] = false };
            }
            return new JObject
            {
                ["exists"] = true,
                ["assetPath"] = AssetDatabase.GetAssetPath(menu),
                ["value"] = JToken.Parse(EditorJsonUtility.ToJson(menu))
            };
        }

        private static HashSet<string> CollectMenuAssetPaths(VRCExpressionsMenu root)
        {
            var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var visited = new HashSet<int>();
            var pending = new Stack<VRCExpressionsMenu>();
            if (root != null)
            {
                pending.Push(root);
            }
            while (pending.Count > 0)
            {
                var menu = pending.Pop();
                if (menu == null || !visited.Add(menu.GetInstanceID()))
                {
                    continue;
                }
                var path = AssetDatabase.GetAssetPath(menu);
                if (!string.IsNullOrWhiteSpace(path))
                {
                    paths.Add(path);
                }
                foreach (var control in menu.controls ?? new List<VRCExpressionsMenu.Control>())
                {
                    if (control?.subMenu != null)
                    {
                        pending.Push(control.subMenu);
                    }
                }
            }
            return paths;
        }

        private static void Apply(string action, VRCExpressionsMenu menu, JObject @params, string assetDir)
        {
            if (menu.controls == null)
            {
                menu.controls = new List<VRCExpressionsMenu.Control>();
            }
            if (action == "reorder")
            {
                Reorder(menu, @params);
                return;
            }
            if (action == "create")
            {
                menu.controls.Add(BuildControl(@params, assetDir, existing: null));
                return;
            }
            var index = ResolveControlIndex(menu, @params);
            if (action == "delete")
            {
                menu.controls.RemoveAt(index);
                return;
            }
            if (action == "update")
            {
                menu.controls[index] = BuildControl(@params, assetDir, menu.controls[index]);
            }
        }

        private static int ResolveControlIndex(VRCExpressionsMenu menu, JObject @params)
        {
            if (@params["controlIndex"] != null)
            {
                var index = @params["controlIndex"].Value<int>();
                if (index < 0 || index >= menu.controls.Count)
                {
                    throw new InvalidOperationException($"controlIndex {index} out of range.");
                }
                return index;
            }
            var name = (@params["controlName"]?.ToString() ?? "").Trim();
            if (string.IsNullOrWhiteSpace(name))
            {
                throw new InvalidOperationException("controlName or controlIndex is required.");
            }
            var match = menu.controls.FindIndex(control => control != null && control.name == name);
            if (match < 0)
            {
                throw new InvalidOperationException($"Control not found: {name}");
            }
            return match;
        }

        private static VRCExpressionsMenu.Control BuildControl(JObject @params, string assetDir, VRCExpressionsMenu.Control existing)
        {
            var control = existing ?? new VRCExpressionsMenu.Control();
            var name = @params["newName"]?.ToString() ?? @params["controlName"]?.ToString();
            if (!string.IsNullOrWhiteSpace(name)) control.name = name.Trim();
            if (@params["controlType"] != null)
            {
                control.type = ParseControlType(@params["controlType"].ToString());
            }
            if (@params["controlFloat"] != null) control.value = @params["controlFloat"].Value<float>();
            if (@params["value"] != null) control.value = @params["value"].Value<float>();
            if (@params["parameterName"] != null)
            {
                var parameterName = @params["parameterName"].ToString().Trim();
                control.parameter = string.IsNullOrWhiteSpace(parameterName)
                    ? null
                    : new VRCExpressionsMenu.Control.Parameter { name = parameterName };
            }
            if (@params["iconAssetPath"] != null)
            {
                control.icon = LoadAssetOrNull<Texture2D>(@params["iconAssetPath"].ToString());
            }
            if (@params["subMenuAssetPath"] != null)
            {
                control.subMenu = LoadAssetOrNull<VRCExpressionsMenu>(@params["subMenuAssetPath"].ToString());
            }
            if (@params["createSubMenu"]?.Value<bool?>() == true && control.subMenu == null)
            {
                AvatarPrimitiveCrudCore.EnsureAssetFolder(assetDir);
                var sub = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
                sub.controls = new List<VRCExpressionsMenu.Control>();
                var subPath = AssetDatabase.GenerateUniqueAssetPath($"{assetDir}/{AvatarPrimitiveCrudCore.Sanitize(control.name, "Menu")}_SubMenu.asset");
                AssetDatabase.CreateAsset(sub, subPath);
                Undo.RegisterCreatedObjectUndo(sub, "Create submenu");
                control.subMenu = sub;
                control.type = VRCExpressionsMenu.Control.ControlType.SubMenu;
            }
            if (@params["subParameters"] is JArray subParameters)
            {
                SetSubParameters(control, subParameters.Select(item => item.ToString()).ToArray());
            }
            return control;
        }

        private static VRCExpressionsMenu.Control.ControlType ParseControlType(string value)
        {
            return Enum.TryParse(value, true, out VRCExpressionsMenu.Control.ControlType parsed)
                ? parsed
                : VRCExpressionsMenu.Control.ControlType.Toggle;
        }

        private static T LoadAssetOrNull<T>(string path) where T : UnityEngine.Object
        {
            var normalized = AvatarPrimitiveCrudCore.NormalizeAssetPath(path);
            if (string.IsNullOrWhiteSpace(normalized))
            {
                return null;
            }
            var asset = AssetDatabase.LoadAssetAtPath<T>(normalized);
            if (asset == null)
            {
                throw new InvalidOperationException($"Asset not found as {typeof(T).Name}: {normalized}");
            }
            return asset;
        }

        private static void SetSubParameters(VRCExpressionsMenu.Control control, string[] names)
        {
            var flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
            var member = (MemberInfo)control.GetType().GetField("subParameters", flags)
                ?? control.GetType().GetProperty("subParameters", flags);
            if (member == null)
            {
                throw new InvalidOperationException("Installed VRC SDK control type has no subParameters member.");
            }
            var memberType = member is FieldInfo field ? field.FieldType : ((PropertyInfo)member).PropertyType;
            var elementType = memberType.IsArray ? memberType.GetElementType() : typeof(VRCExpressionsMenu.Control.Parameter);
            var array = Array.CreateInstance(elementType, names.Length);
            for (var i = 0; i < names.Length; i++)
            {
                var item = Activator.CreateInstance(elementType);
                var nameMember = (MemberInfo)elementType.GetField("name", flags) ?? elementType.GetProperty("name", flags);
                if (nameMember is FieldInfo nameField) nameField.SetValue(item, names[i]);
                else if (nameMember is PropertyInfo nameProperty && nameProperty.CanWrite) nameProperty.SetValue(item, names[i]);
                array.SetValue(item, i);
            }
            if (member is FieldInfo setField) setField.SetValue(control, array);
            else ((PropertyInfo)member).SetValue(control, array);
        }

        private static void Reorder(VRCExpressionsMenu menu, JObject @params)
        {
            var orderNames = (@params["orderNames"] as JArray)?.Select(item => item.ToString()).ToList() ?? new List<string>();
            if (orderNames.Count == 0)
            {
                throw new InvalidOperationException("orderNames is required for reorder.");
            }
            var remaining = menu.controls.ToList();
            var ordered = new List<VRCExpressionsMenu.Control>();
            foreach (var name in orderNames)
            {
                var index = remaining.FindIndex(control => control != null && control.name == name);
                if (index < 0)
                {
                    throw new InvalidOperationException($"Cannot reorder: control not found: {name}");
                }
                ordered.Add(remaining[index]);
                remaining.RemoveAt(index);
            }
            ordered.AddRange(remaining);
            menu.controls = ordered;
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_manage_fx_animator",
        Summary = "Manage one FX AnimatorController mutation. When to use: an exact layer, state, transition, motion, condition, or unused-parameter edit. When NOT to use: deleting a referenced parameter or batching unrelated controller edits. Supports preview."
    )]
    public static class ManageFxAnimatorTool
    {
        private const int MaxAnimatorControllerBackupBytes = 32 * 1024 * 1024;

        public class Parameters
        {
            [VRCForgeInput("FX animator action.", IsRequired = true)] public string action { get; set; } = "";
            [VRCForgeInput("Return the FX animator change plan without writing.", IsRequired = false)] public bool? preview { get; set; } = false;
            [VRCForgeInput("Optional avatar hierarchy path.", IsRequired = false)] public string avatarPath { get; set; } = "";
            [VRCForgeInput("Asset directory for a newly created FX controller or motion clip.", IsRequired = false)] public string assetDir { get; set; } = "";
            [VRCForgeInput("FX layer name.", IsRequired = false)] public string layerName { get; set; } = "";
            [VRCForgeInput("State name.", IsRequired = false)] public string stateName { get; set; } = "";
            [VRCForgeInput("Destination state name for a transition.", IsRequired = false)] public string destinationStateName { get; set; } = "";
            [VRCForgeInput("Optional explicit animator controller asset path.", IsRequired = false)] public string controllerPath { get; set; } = "";
            [VRCForgeInput("Optional compatibility alias for controllerPath.", IsRequired = false)] public string fxControllerPath { get; set; } = "";
            [VRCForgeInput("Optional new state name.", IsRequired = false)] public string newName { get; set; } = "";
            [VRCForgeInput("Optional write-defaults flag.", IsRequired = false)] public bool? writeDefaults { get; set; }
            [VRCForgeInput("Optional motion AnimationClip asset path.", IsRequired = false)] public string motionClipPath { get; set; } = "";
            [VRCForgeInput("Optional state speed.", IsRequired = false)] public float? speed { get; set; }
            [VRCForgeInput("Optional Any-State transition exit-time flag.", IsRequired = false)] public bool? hasExitTime { get; set; }
            [VRCForgeInput("Optional Any-State transition exit time.", IsRequired = false)] public float? exitTime { get; set; }
            [VRCForgeInput("Optional Any-State transition duration.", IsRequired = false)] public float? duration { get; set; }
            [VRCForgeInput("Optional Any-State self-transition flag.", IsRequired = false)] public bool? canTransitionToSelf { get; set; }
            [VRCForgeInput("Optional transition index for deletion.", IsRequired = false)] public int? transitionIndex { get; set; }
            [VRCForgeInput("Optional transition-condition object array.", IsRequired = false)] public object[] conditions { get; set; }
            [VRCForgeInput("Optional single transition condition parameter.", IsRequired = false)] public string parameterName { get; set; } = "";
            [VRCForgeInput("Optional single transition condition mode.", IsRequired = false)] public string conditionMode { get; set; } = "";
            [VRCForgeInput("Optional single transition condition threshold.", IsRequired = false)] public float? threshold { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                @params = @params ?? new JObject();
                var action = NormalizeAction(@params["action"]?.ToString() ?? "");
                var preview = @params["preview"]?.Value<bool?>() ?? false;
                var descriptor = AvatarPrimitiveCrudCore.ResolveAvatarDescriptor(@params["avatarPath"]?.ToString() ?? "");
                var assetDir = AvatarPrimitiveCrudCore.NormalizeAssetDir(@params["assetDir"]?.ToString() ?? "");
                var controller = preview || action == "delete_parameter"
                    ? ResolveAnimatorControllerForPreview(descriptor, @params)
                    : AvatarPrimitiveCrudCore.ResolveAnimatorController(descriptor, @params, assetDir);
                var plan = new
                {
                    action,
                    avatarPath = AvatarPrimitiveCrudCore.GetTransformPath(descriptor.transform),
                    avatarName = descriptor.name,
                    controllerPath = AssetDatabase.GetAssetPath(controller),
                    layerName = @params["layerName"]?.ToString() ?? "",
                    stateName = @params["stateName"]?.ToString() ?? "",
                    destinationStateName = @params["destinationStateName"]?.ToString() ?? "",
                    parameterName = @params["parameterName"]?.ToString() ?? ""
                };
                if (action == "delete_parameter")
                {
                    return HandleDeleteParameter(controller, @params, preview, plan);
                }
                if (preview)
                {
                    return VRCForgeToolResult.Completed($"Preview: would manage FX animator ({action}).", new { ok = true, preview = true, plan });
                }

                Undo.RegisterCompleteObjectUndo(controller, "Manage FX animator");
                Apply(action, controller, @params, assetDir);
                EditorUtility.SetDirty(controller);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();
                return VRCForgeToolResult.Completed($"FX animator action '{action}' completed.", new
                {
                    ok = true,
                    preview = false,
                    action,
                    controllerPath = AssetDatabase.GetAssetPath(controller),
                    layerCount = controller.layers.Length
                });
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.RejectedBeforeMutation(
                    "fx_animator_rejected",
                    ex.Message,
                    "unity_core_tool",
                    "pre_mutation_validation",
                    false,
                    new { action = @params?["action"]?.ToString() ?? string.Empty, exceptionType = ex.GetType().FullName });
            }
        }

        private static object HandleDeleteParameter(
            AnimatorController controller,
            JObject @params,
            bool preview,
            object plan)
        {
            var parameterName = string.Empty;
            var controllerPath = string.Empty;
            var absolutePath = string.Empty;
            byte[] beforeBytes = null;
            StableAssetEvidence beforeEvidence = null;
            var undoGroup = -1;
            var mutationStarted = false;
            var mutationApplied = false;
            var failurePhase = "pre_mutation_validation";
            try
            {
                parameterName = Required(@params, "parameterName");
                var parameterIndex = ValidateUnusedParameterDeletion(controller, parameterName);
                var beforeParameterCount = (controller.parameters ?? Array.Empty<AnimatorControllerParameter>()).Length;
                if (preview)
                {
                    return VRCForgeToolResult.Completed(
                        "Preview: would delete one unused FX animator parameter.",
                        new
                        {
                            ok = true,
                            preview = true,
                            plan,
                            parameterName,
                            parameterIndex,
                            beforeParameterCount,
                            mutationStarted = false,
                            committed = false,
                            commitState = "not_started"
                        });
                }

                controllerPath = AvatarPrimitiveCrudCore.NormalizeAssetPath(AssetDatabase.GetAssetPath(controller));
                if (string.IsNullOrWhiteSpace(controllerPath) || !controllerPath.StartsWith("Assets/", StringComparison.Ordinal))
                {
                    throw new InvalidOperationException("FX AnimatorController must be a persistent Assets asset.");
                }
                beforeEvidence = SceneObjectCopyCore.ReadStableAssetEvidence(
                    controllerPath,
                    "FX animator parameter deletion pre-state");
                absolutePath = SceneObjectCopyCore.ToAbsoluteAssetPath(controllerPath);
                var fileInfo = new FileInfo(absolutePath);
                if (!fileInfo.Exists || fileInfo.Length <= 0 || fileInfo.Length > MaxAnimatorControllerBackupBytes)
                {
                    throw new InvalidOperationException("FX AnimatorController exceeds the bounded rollback size.");
                }
                beforeBytes = File.ReadAllBytes(absolutePath);

                Undo.IncrementCurrentGroup();
                undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName("Delete unused FX animator parameter");
                Undo.RegisterCompleteObjectUndo(controller, "Delete unused FX animator parameter");
                mutationStarted = true;
                failurePhase = "unity_mutation";
                controller.RemoveParameter(parameterIndex);
                mutationApplied = true;
                EditorUtility.SetDirty(controller);

                failurePhase = "asset_save";
                AssetDatabase.SaveAssetIfDirty(controller);
                if (EditorUtility.IsDirty(controller))
                {
                    throw new InvalidOperationException("FX AnimatorController remained dirty after save.");
                }

                failurePhase = "persisted_readback";
                var afterEvidence = SceneObjectCopyCore.ReadStableAssetEvidence(
                    controllerPath,
                    "FX animator parameter deletion readback");
                var readback = AssetDatabase.LoadAssetAtPath<AnimatorController>(controllerPath);
                var afterParameters = readback != null
                    ? readback.parameters ?? Array.Empty<AnimatorControllerParameter>()
                    : Array.Empty<AnimatorControllerParameter>();
                if (readback == null
                    || afterParameters.Any(item => item != null && item.name == parameterName)
                    || afterParameters.Length != beforeParameterCount - 1
                    || string.Equals(afterEvidence.File.Digest, beforeEvidence.File.Digest, StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException("FX animator parameter deletion persisted readback was not exact.");
                }
                Undo.CollapseUndoOperations(undoGroup);
                return VRCForgeToolResult.Completed(
                    "Unused FX animator parameter deleted and verified.",
                    new
                    {
                        ok = true,
                        preview = false,
                        action = "delete_parameter",
                        controllerPath,
                        parameterName,
                        beforeParameterCount,
                        afterParameterCount = afterParameters.Length,
                        fileDigestBefore = beforeEvidence.File.Digest,
                        fileDigestAfter = afterEvidence.File.Digest,
                        mutationStarted = true,
                        committed = true,
                        commitState = "committed",
                        checkpointRecoveryRequired = false,
                        temporaryCleanupRequired = false
                    });
            }
            catch (Exception ex)
            {
                if (!mutationStarted)
                {
                    return VRCForgeToolResult.RejectedBeforeMutation(
                        "fx_animator_delete_parameter_rejected",
                        ex.Message,
                        "unity_core_tool",
                        failurePhase,
                        false,
                        new { action = "delete_parameter", controllerPath, parameterName, exceptionType = ex.GetType().FullName });
                }

                var restored = RestoreAnimatorControllerPreState(
                    controllerPath,
                    absolutePath,
                    beforeBytes,
                    beforeEvidence,
                    parameterName,
                    undoGroup);
                return VRCForgeToolResult.FailedWithCode(
                    "fx_animator_delete_parameter_failed_after_mutation",
                    ex.Message,
                    new
                    {
                        failureLayer = "unity_core_tool",
                        failurePhase,
                        toolRoutingStarted = true,
                        mutationStarted = true,
                        mutationApplied,
                        committed = false,
                        commitState = restored ? "rolled_back" : "unknown",
                        commitStateKnown = restored,
                        retryable = false,
                        checkpointRecoveryRequired = !restored,
                        temporaryCleanupRequired = false,
                        restored,
                        action = "delete_parameter",
                        controllerPath,
                        parameterName,
                        exceptionType = ex.GetType().FullName
                    });
            }
        }

        private static int ValidateUnusedParameterDeletion(
            AnimatorController controller,
            string parameterName)
        {
            var parameters = controller.parameters ?? Array.Empty<AnimatorControllerParameter>();
            var parameterIndex = Array.FindIndex(parameters, item => item != null && item.name == parameterName);
            if (parameterIndex < 0)
            {
                throw new InvalidOperationException($"Animator parameter not found: {parameterName}");
            }
            var references = new List<string>();
            foreach (var layer in controller.layers ?? Array.Empty<AnimatorControllerLayer>())
            {
                CollectParameterReferences(layer.stateMachine, layer.name, parameterName, references);
            }
            if (references.Count > 0)
            {
                throw new InvalidOperationException(
                    $"Animator parameter '{parameterName}' is still referenced by: {string.Join(", ", references.Distinct())}");
            }
            return parameterIndex;
        }

        private static bool RestoreAnimatorControllerPreState(
            string controllerPath,
            string absolutePath,
            byte[] beforeBytes,
            StableAssetEvidence beforeEvidence,
            string parameterName,
            int undoGroup)
        {
            try
            {
                Undo.RevertAllDownToGroup(undoGroup);
            }
            catch
            {
                // Exact file restoration below is authoritative.
            }
            if (string.IsNullOrWhiteSpace(controllerPath)
                || string.IsNullOrWhiteSpace(absolutePath)
                || beforeBytes == null
                || beforeEvidence == null)
            {
                return false;
            }

            var tempPath = absolutePath + ".vrcforge-restore-" + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                File.WriteAllBytes(tempPath, beforeBytes);
                File.Replace(tempPath, absolutePath, null);
                AssetDatabase.ImportAsset(
                    controllerPath,
                    ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                var restoredEvidence = SceneObjectCopyCore.ReadStableAssetEvidence(
                    controllerPath,
                    "FX animator parameter deletion compensation");
                var restored = AssetDatabase.LoadAssetAtPath<AnimatorController>(controllerPath);
                return restored != null
                    && (restored.parameters ?? Array.Empty<AnimatorControllerParameter>())
                        .Any(item => item != null && item.name == parameterName)
                    && string.Equals(
                        restoredEvidence.File.Digest,
                        beforeEvidence.File.Digest,
                        StringComparison.OrdinalIgnoreCase)
                    && File.ReadAllBytes(absolutePath).SequenceEqual(beforeBytes);
            }
            catch
            {
                return false;
            }
            finally
            {
                try
                {
                    if (File.Exists(tempPath))
                    {
                        File.Delete(tempPath);
                    }
                }
                catch
                {
                    // Cleanup status remains unknown through the returned failure object.
                }
            }
        }

        private static string NormalizeAction(string value)
        {
            var action = (value ?? "").Trim().ToLowerInvariant().Replace("-", "_");
            if (action == "add_layer") return "ensure_layer";
            if (action == "add_state") return "ensure_state";
            if (action == "set_state" || action == "edit_state") return "update_state";
            if (action == "add_transition") return "ensure_transition";
            if (action == "remove_layer") return "delete_layer";
            if (action == "remove_state") return "delete_state";
            if (action == "remove_transition") return "delete_transition";
            if (action == "remove_parameter") return "delete_parameter";
            if (!new[] { "ensure_layer", "delete_layer", "ensure_state", "update_state", "delete_state", "ensure_transition", "delete_transition", "delete_parameter" }.Contains(action))
            {
                throw new InvalidOperationException("action must be one of: ensure_layer, delete_layer, ensure_state, update_state, delete_state, ensure_transition, delete_transition, delete_parameter.");
            }
            return action;
        }

        private static AnimatorController ResolveAnimatorControllerForPreview(VRCAvatarDescriptor descriptor, JObject @params)
        {
            var explicitPath = AvatarPrimitiveCrudCore.NormalizeAssetPath(@params?["controllerPath"]?.ToString() ?? @params?["fxControllerPath"]?.ToString() ?? "");
            if (!string.IsNullOrWhiteSpace(explicitPath))
            {
                var explicitController = AssetDatabase.LoadAssetAtPath<AnimatorController>(explicitPath);
                if (explicitController == null)
                {
                    throw new InvalidOperationException($"AnimatorController not found: {explicitPath}");
                }
                return explicitController;
            }
            return AvatarPrimitiveCrudCore.GetFxController(descriptor);
        }

        private static void Apply(string action, AnimatorController controller, JObject @params, string assetDir)
        {
            if (action == "ensure_layer")
            {
                EnsureLayer(controller, Required(@params, "layerName"));
            }
            else if (action == "delete_layer")
            {
                DeleteLayer(controller, Required(@params, "layerName"));
            }
            else if (action == "ensure_state" || action == "update_state")
            {
                var layer = EnsureLayer(controller, Required(@params, "layerName"));
                var stateName = Required(@params, "stateName");
                var state = AvatarPrimitiveCrudCore.FindState(layer.stateMachine, stateName);
                if (state == null)
                {
                    state = layer.stateMachine.AddState(stateName);
                }
                UpdateState(state, @params, assetDir);
            }
            else if (action == "delete_state")
            {
                var layer = FindLayer(controller, Required(@params, "layerName"));
                var stateName = Required(@params, "stateName");
                var machine = AvatarPrimitiveCrudCore.FindStateMachineForState(layer.stateMachine, stateName)
                    ?? throw new InvalidOperationException($"State not found: {stateName}");
                var child = machine.states.First(item => item.state != null && item.state.name == stateName);
                machine.RemoveState(child.state);
            }
            else if (action == "ensure_transition")
            {
                EnsureTransition(controller, @params);
            }
            else if (action == "delete_transition")
            {
                DeleteTransition(controller, @params);
            }
            else if (action == "delete_parameter")
            {
                DeleteUnusedParameter(controller, Required(@params, "parameterName"));
            }
        }

        private static void DeleteUnusedParameter(AnimatorController controller, string parameterName)
        {
            var parameterIndex = ValidateUnusedParameterDeletion(controller, parameterName);
            controller.RemoveParameter(parameterIndex);
        }

        private static void CollectParameterReferences(
            AnimatorStateMachine machine,
            string scope,
            string parameterName,
            List<string> references
        )
        {
            if (machine == null)
            {
                return;
            }
            CollectTransitionReferences(machine.anyStateTransitions, scope + "/AnyState", parameterName, references);
            CollectTransitionReferences(machine.entryTransitions, scope + "/Entry", parameterName, references);
            CollectBehaviourReferences(machine.behaviours, scope + "/StateMachine", parameterName, references);

            foreach (var child in machine.states)
            {
                var state = child.state;
                if (state == null)
                {
                    continue;
                }
                var stateScope = scope + "/" + state.name;
                CollectTransitionReferences(state.transitions, stateScope, parameterName, references);
                CollectBehaviourReferences(state.behaviours, stateScope, parameterName, references);
                if ((state.speedParameterActive && state.speedParameter == parameterName)
                    || (state.mirrorParameterActive && state.mirrorParameter == parameterName)
                    || (state.cycleOffsetParameterActive && state.cycleOffsetParameter == parameterName)
                    || (state.timeParameterActive && state.timeParameter == parameterName))
                {
                    references.Add(stateScope + "/StateParameter");
                }
                CollectMotionReferences(state.motion, stateScope, parameterName, references);
            }

            foreach (var child in machine.stateMachines)
            {
                if (child.stateMachine != null)
                {
                    CollectParameterReferences(child.stateMachine, scope + "/" + child.stateMachine.name, parameterName, references);
                }
            }
        }

        private static void CollectTransitionReferences(
            IEnumerable<AnimatorTransitionBase> transitions,
            string scope,
            string parameterName,
            List<string> references
        )
        {
            foreach (var transition in transitions ?? Enumerable.Empty<AnimatorTransitionBase>())
            {
                if (transition != null && transition.conditions.Any(item => item.parameter == parameterName))
                {
                    references.Add(scope + "/Transition");
                }
            }
        }

        private static void CollectMotionReferences(
            Motion motion,
            string scope,
            string parameterName,
            List<string> references
        )
        {
            var blendTree = motion as BlendTree;
            if (blendTree == null)
            {
                return;
            }
            if (blendTree.blendParameter == parameterName || blendTree.blendParameterY == parameterName)
            {
                references.Add(scope + "/BlendTree");
            }
            foreach (var child in blendTree.children)
            {
                if (child.directBlendParameter == parameterName)
                {
                    references.Add(scope + "/BlendTreeDirect");
                }
                CollectMotionReferences(child.motion, scope + "/BlendTreeChild", parameterName, references);
            }
        }

        private static void CollectBehaviourReferences(
            IEnumerable<StateMachineBehaviour> behaviours,
            string scope,
            string parameterName,
            List<string> references
        )
        {
            foreach (var behaviour in behaviours ?? Enumerable.Empty<StateMachineBehaviour>())
            {
                if (behaviour == null)
                {
                    continue;
                }
                var serialized = new SerializedObject(behaviour);
                var property = serialized.GetIterator();
                var enterChildren = true;
                while (property.NextVisible(enterChildren))
                {
                    enterChildren = false;
                    if (property.propertyType == SerializedPropertyType.String
                        && property.propertyPath != "m_Name"
                        && property.stringValue == parameterName)
                    {
                        references.Add(scope + "/" + behaviour.GetType().Name + ":" + property.propertyPath);
                    }
                }
            }
        }

        private static AnimatorControllerLayer EnsureLayer(AnimatorController controller, string layerName)
        {
            var existing = controller.layers.FirstOrDefault(layer => layer.name == layerName);
            if (existing != null)
            {
                if (Mathf.Approximately(existing.defaultWeight, 0f))
                {
                    var layers = controller.layers;
                    var index = Array.FindIndex(layers, layer => layer.name == layerName);
                    existing.defaultWeight = 1f;
                    layers[index] = existing;
                    controller.layers = layers;
                }
                return existing;
            }
            controller.AddLayer(layerName);
            var all = controller.layers;
            var created = all[all.Length - 1];
            created.defaultWeight = 1f;
            all[all.Length - 1] = created;
            controller.layers = all;
            return created;
        }

        private static AnimatorControllerLayer FindLayer(AnimatorController controller, string layerName)
        {
            return controller.layers.FirstOrDefault(layer => layer.name == layerName)
                ?? throw new InvalidOperationException($"FX layer not found: {layerName}");
        }

        private static void DeleteLayer(AnimatorController controller, string layerName)
        {
            var layers = controller.layers.ToList();
            var index = layers.FindIndex(layer => layer.name == layerName);
            if (index < 0)
            {
                throw new InvalidOperationException($"FX layer not found: {layerName}");
            }
            layers.RemoveAt(index);
            controller.layers = layers.ToArray();
        }

        private static void UpdateState(AnimatorState state, JObject @params, string assetDir)
        {
            if (@params["newName"] != null)
            {
                state.name = @params["newName"].ToString().Trim();
            }
            if (@params["writeDefaults"] != null)
            {
                state.writeDefaultValues = @params["writeDefaults"].Value<bool>();
            }
            if (@params["motionClipPath"] != null)
            {
                state.motion = LoadOrCreateClip(@params["motionClipPath"].ToString(), assetDir);
            }
            if (@params["speed"] != null)
            {
                state.speed = @params["speed"].Value<float>();
            }
        }

        private static AnimationClip LoadOrCreateClip(string rawPath, string assetDir)
        {
            var path = AvatarPrimitiveCrudCore.NormalizeAssetPath(rawPath);
            if (string.IsNullOrWhiteSpace(path))
            {
                return null;
            }
            var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
            if (clip != null)
            {
                return clip;
            }
            var folder = Path.GetDirectoryName(path)?.Replace("\\", "/") ?? assetDir;
            AvatarPrimitiveCrudCore.EnsureAssetFolder(folder);
            clip = new AnimationClip { name = Path.GetFileNameWithoutExtension(path) };
            AssetDatabase.CreateAsset(clip, path);
            Undo.RegisterCreatedObjectUndo(clip, "Create state motion clip");
            return clip;
        }

        private static void EnsureTransition(AnimatorController controller, JObject @params)
        {
            var layer = FindLayer(controller, Required(@params, "layerName"));
            var destinationName = Required(@params, "destinationStateName", "stateName");
            var state = AvatarPrimitiveCrudCore.FindState(layer.stateMachine, destinationName)
                ?? throw new InvalidOperationException($"Destination state not found: {destinationName}");
            var transition = layer.stateMachine.AddAnyStateTransition(state);
            transition.hasExitTime = @params["hasExitTime"]?.Value<bool?>() ?? false;
            transition.exitTime = @params["exitTime"]?.Value<float?>() ?? 0f;
            transition.duration = @params["duration"]?.Value<float?>() ?? 0f;
            transition.canTransitionToSelf = @params["canTransitionToSelf"]?.Value<bool?>() ?? false;
            foreach (var condition in ReadConditions(@params))
            {
                transition.AddCondition(condition.mode, condition.threshold, condition.parameter);
            }
        }

        private static void DeleteTransition(AnimatorController controller, JObject @params)
        {
            var layer = FindLayer(controller, Required(@params, "layerName"));
            var index = @params["transitionIndex"]?.Value<int?>() ?? -1;
            if (index < 0)
            {
                var destination = @params["destinationStateName"]?.ToString() ?? @params["stateName"]?.ToString() ?? "";
                var transitions = layer.stateMachine.anyStateTransitions ?? Array.Empty<AnimatorStateTransition>();
                index = Array.FindIndex(transitions, item => item != null && item.destinationState != null && item.destinationState.name == destination);
            }
            if (index < 0 || index >= (layer.stateMachine.anyStateTransitions?.Length ?? 0))
            {
                throw new InvalidOperationException("Any-State transition not found. Pass transitionIndex or destinationStateName.");
            }
            layer.stateMachine.RemoveAnyStateTransition(layer.stateMachine.anyStateTransitions[index]);
        }

        private static List<ConditionSpec> ReadConditions(JObject @params)
        {
            var result = new List<ConditionSpec>();
            if (@params["conditions"] is JArray conditions)
            {
                foreach (var item in conditions.OfType<JObject>())
                {
                    result.Add(new ConditionSpec
                    {
                        parameter = item["parameter"]?.ToString() ?? item["parameterName"]?.ToString() ?? "",
                        mode = AvatarAuthoringCrudCore.ParseConditionMode(item["mode"]?.ToString() ?? "Equals"),
                        threshold = item["threshold"]?.Value<float?>() ?? 0f
                    });
                }
            }
            else if (@params["parameterName"] != null)
            {
                result.Add(new ConditionSpec
                {
                    parameter = @params["parameterName"].ToString(),
                    mode = AvatarAuthoringCrudCore.ParseConditionMode(@params["conditionMode"]?.ToString() ?? "Equals"),
                    threshold = @params["threshold"]?.Value<float?>() ?? 0f
                });
            }
            foreach (var item in result)
            {
                if (string.IsNullOrWhiteSpace(item.parameter))
                {
                    throw new InvalidOperationException("Transition condition parameter is required.");
                }
            }
            return result;
        }

        private static string Required(JObject @params, params string[] names)
        {
            foreach (var name in names)
            {
                var value = (@params[name]?.ToString() ?? "").Trim();
                if (!string.IsNullOrWhiteSpace(value))
                {
                    return value;
                }
            }
            throw new InvalidOperationException($"{string.Join(" or ", names)} is required.");
        }

        private class ConditionSpec
        {
            public string parameter;
            public AnimatorConditionMode mode;
            public float threshold;
        }
    }
}
