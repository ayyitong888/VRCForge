using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;

namespace VRCForge.Editor
{
    // ------------------------------------------------------------------
    // Generic avatar authoring primitives (v0.5): expression parameters,
    // expression menus, and FX animator states. These are reusable building
    // blocks for wardrobe workflows and future avatar-editing skills.
    //
    // All writes support preview and register Undo. Payload keys avoid the
    // gateway auto-unwrap trap (data/result/payload/value at top level).
    // ------------------------------------------------------------------

    internal static class AvatarAuthoringCrudCore
    {
        internal const string DefaultAssetDir = "Assets/VRCForge/Generated/AvatarAuthoring";

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

        internal static VRCExpressionParameters.ValueType ParseExpressionParameterType(string value)
        {
            if (Enum.TryParse(value, true, out VRCExpressionParameters.ValueType parsed))
            {
                return parsed;
            }
            return VRCExpressionParameters.ValueType.Int;
        }

        internal static AnimatorControllerParameterType ParseAnimatorParameterType(string value)
        {
            if (Enum.TryParse(value, true, out AnimatorControllerParameterType parsed))
            {
                return parsed;
            }
            return AnimatorControllerParameterType.Int;
        }

        internal static AnimatorConditionMode ParseConditionMode(string value)
        {
            if (Enum.TryParse(value, true, out AnimatorConditionMode parsed))
            {
                return parsed;
            }
            return AnimatorConditionMode.Equals;
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

        internal static string NormalizeAssetDir(string value)
        {
            var normalized = NormalizePath(value);
            return string.IsNullOrEmpty(normalized) ? DefaultAssetDir : normalized;
        }

        internal static string Sanitize(string value, string fallback)
        {
            var cleaned = new string((value ?? string.Empty).Select(c => char.IsLetterOrDigit(c) || c == '_' ? c : '_').ToArray()).Trim('_');
            return string.IsNullOrWhiteSpace(cleaned) ? fallback : cleaned;
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

        internal static VRCExpressionParameters EnsureExpressionParametersAsset(VRCAvatarDescriptor descriptor, string assetDir)
        {
            if (descriptor.expressionParameters != null)
            {
                return descriptor.expressionParameters;
            }

            EnsureAssetFolder(assetDir);
            var asset = ScriptableObject.CreateInstance<VRCExpressionParameters>();
            asset.parameters = Array.Empty<VRCExpressionParameters.Parameter>();
            var path = AssetDatabase.GenerateUniqueAssetPath($"{assetDir}/{Sanitize(descriptor.name, "Avatar")}_ExpressionParameters.asset");
            AssetDatabase.CreateAsset(asset, path);
            Undo.RegisterCreatedObjectUndo(asset, "Create expression parameters asset");
            descriptor.expressionParameters = asset;
            EditorUtility.SetDirty(descriptor);
            return asset;
        }

        internal static VRCExpressionsMenu EnsureRootMenuAsset(VRCAvatarDescriptor descriptor, string assetDir)
        {
            if (descriptor.expressionsMenu != null)
            {
                return descriptor.expressionsMenu;
            }

            EnsureAssetFolder(assetDir);
            var asset = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
            asset.controls = new List<VRCExpressionsMenu.Control>();
            var path = AssetDatabase.GenerateUniqueAssetPath($"{assetDir}/{Sanitize(descriptor.name, "Avatar")}_ExpressionsMenu.asset");
            AssetDatabase.CreateAsset(asset, path);
            Undo.RegisterCreatedObjectUndo(asset, "Create expressions menu asset");
            descriptor.expressionsMenu = asset;
            EditorUtility.SetDirty(descriptor);
            return asset;
        }

        internal static AnimatorController EnsureFxController(VRCAvatarDescriptor descriptor, string assetDir)
        {
            var existing = GetFxController(descriptor);
            if (existing != null)
            {
                return existing;
            }

            EnsureAssetFolder(assetDir);
            var path = AssetDatabase.GenerateUniqueAssetPath($"{assetDir}/{Sanitize(descriptor.name, "Avatar")}_FX.controller");
            var controller = AnimatorController.CreateAnimatorControllerAtPath(path);
            Undo.RegisterCreatedObjectUndo(controller, "Create FX animator controller");
            var layers = descriptor.baseAnimationLayers?.ToList() ?? new List<VRCAvatarDescriptor.CustomAnimLayer>();
            var index = layers.FindIndex(layer => layer.type == VRCAvatarDescriptor.AnimLayerType.FX);
            if (index < 0)
            {
                layers.Add(new VRCAvatarDescriptor.CustomAnimLayer
                {
                    type = VRCAvatarDescriptor.AnimLayerType.FX,
                    isDefault = false,
                    animatorController = controller
                });
            }
            else
            {
                var layer = layers[index];
                layer.isDefault = false;
                layer.animatorController = controller;
                layers[index] = layer;
            }
            descriptor.baseAnimationLayers = layers.ToArray();
            EditorUtility.SetDirty(descriptor);
            return controller;
        }

        internal static AnimatorController GetFxController(VRCAvatarDescriptor descriptor)
        {
            if (descriptor.baseAnimationLayers == null)
            {
                return null;
            }
            foreach (var layer in descriptor.baseAnimationLayers)
            {
                if (layer.type == VRCAvatarDescriptor.AnimLayerType.FX && !layer.isDefault
                    && layer.animatorController is AnimatorController controller)
                {
                    return controller;
                }
            }
            foreach (var layer in descriptor.baseAnimationLayers)
            {
                if (layer.type == VRCAvatarDescriptor.AnimLayerType.FX
                    && layer.animatorController is AnimatorController controller)
                {
                    return controller;
                }
            }
            return null;
        }
    }

    [McpForUnityTool(
        name: "vrc_ensure_expression_parameter",
        Description = "Create or update an avatar VRCExpressionParameters entry (Bool/Int/Float), creating the parameters asset if missing. Supports preview."
    )]
    public static class EnsureExpressionParameterTool
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                @params = @params ?? new JObject();
                var avatarPath = (@params["avatarPath"]?.ToString() ?? "").Trim();
                var parameterName = (@params["parameterName"]?.ToString() ?? "").Trim();
                var type = AvatarAuthoringCrudCore.ParseExpressionParameterType(@params["valueType"]?.ToString() ?? "Int");
                var defaultValue = @params["defaultValue"]?.Value<float?>() ?? 0f;
                var saved = @params["saved"]?.Value<bool?>() ?? true;
                var networkSynced = @params["networkSynced"]?.Value<bool?>() ?? true;
                var preview = @params["preview"]?.Value<bool?>() ?? false;
                var assetDir = AvatarAuthoringCrudCore.NormalizeAssetDir(@params["assetDir"]?.ToString() ?? "");
                if (string.IsNullOrWhiteSpace(parameterName))
                {
                    return new ErrorResponse("parameterName is required.");
                }

                var descriptor = AvatarAuthoringCrudCore.ResolveAvatarDescriptor(avatarPath);
                var asset = descriptor.expressionParameters;
                var existing = asset?.parameters?.FirstOrDefault(parameter => parameter != null && parameter.name == parameterName);
                var plan = new
                {
                    action = "ensure_expression_parameter",
                    avatarPath = AvatarAuthoringCrudCore.GetTransformPath(descriptor.transform),
                    avatarName = descriptor.name,
                    parameterName,
                    valueType = type.ToString(),
                    defaultValue,
                    saved,
                    networkSynced,
                    willCreateAsset = asset == null,
                    willCreateParameter = existing == null,
                    existingValueType = existing != null ? existing.valueType.ToString() : null,
                    assetDir
                };
                if (preview)
                {
                    return new SuccessResponse($"Preview: would ensure expression parameter '{parameterName}'.", new { ok = true, preview = true, plan });
                }

                asset = AvatarAuthoringCrudCore.EnsureExpressionParametersAsset(descriptor, assetDir);
                var parameters = asset.parameters?.Where(parameter => parameter != null).ToList() ?? new List<VRCExpressionParameters.Parameter>();
                var index = parameters.FindIndex(parameter => parameter.name == parameterName);
                Undo.RegisterCompleteObjectUndo(asset, "Ensure expression parameter");
                var created = index < 0;
                var item = created
                    ? new VRCExpressionParameters.Parameter { name = parameterName }
                    : parameters[index];
                item.valueType = type;
                item.defaultValue = defaultValue;
                item.saved = saved;
                item.networkSynced = networkSynced;
                if (created)
                {
                    parameters.Add(item);
                }
                else
                {
                    parameters[index] = item;
                }
                asset.parameters = parameters.ToArray();
                EditorUtility.SetDirty(asset);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();
                return new SuccessResponse($"Ensured expression parameter '{parameterName}'.", new
                {
                    ok = true,
                    preview = false,
                    action = "ensure_expression_parameter",
                    parameterName,
                    valueType = type.ToString(),
                    parameterCreated = created,
                    assetPath = AssetDatabase.GetAssetPath(asset)
                });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Ensure expression parameter failed: {ex.Message}\n{ex.StackTrace}");
            }
        }
    }

    [McpForUnityTool(
        name: "vrc_ensure_expression_menu_control",
        Description = "Create an avatar expression menu control under a menu path, creating root/submenus when needed. Supports Toggle/SubMenu and preview."
    )]
    public static class EnsureExpressionMenuControlTool
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                @params = @params ?? new JObject();
                var avatarPath = (@params["avatarPath"]?.ToString() ?? "").Trim();
                var menuPath = AvatarAuthoringCrudCore.NormalizePath(@params["menuPath"]?.ToString() ?? "");
                var controlName = (@params["controlName"]?.ToString() ?? "Control").Trim();
                var controlTypeText = (@params["controlType"]?.ToString() ?? "Toggle").Trim();
                var parameterName = (@params["parameterName"]?.ToString() ?? "").Trim();
                var controlValue = @params["controlValue"]?.Value<float?>() ?? 0f;
                var preview = @params["preview"]?.Value<bool?>() ?? false;
                var assetDir = AvatarAuthoringCrudCore.NormalizeAssetDir(@params["assetDir"]?.ToString() ?? "");
                if (string.IsNullOrWhiteSpace(controlName))
                {
                    return new ErrorResponse("controlName is required.");
                }

                var descriptor = AvatarAuthoringCrudCore.ResolveAvatarDescriptor(avatarPath);
                var root = descriptor.expressionsMenu;
                var type = ParseMenuControlType(controlTypeText);
                var exists = MenuContainsControl(root, menuPath, controlName, parameterName, Mathf.RoundToInt(controlValue), new HashSet<int>(), 0);
                var plan = new
                {
                    action = "ensure_expression_menu_control",
                    avatarPath = AvatarAuthoringCrudCore.GetTransformPath(descriptor.transform),
                    avatarName = descriptor.name,
                    menuPath,
                    controlName,
                    controlType = type.ToString(),
                    parameterName,
                    controlValue,
                    willCreateRootMenu = root == null,
                    willCreateControl = !exists,
                    assetDir
                };
                if (preview)
                {
                    return new SuccessResponse($"Preview: would ensure menu control '{controlName}'.", new { ok = true, preview = true, plan });
                }

                root = AvatarAuthoringCrudCore.EnsureRootMenuAsset(descriptor, assetDir);
                var target = EnsureMenuPath(root, menuPath, assetDir);
                Undo.RegisterCompleteObjectUndo(target, "Ensure expression menu control");
                var created = false;
                if (!MenuContainsControl(root, menuPath, controlName, parameterName, Mathf.RoundToInt(controlValue), new HashSet<int>(), 0))
                {
                    target = EnsureMenuHasRoom(target, assetDir);
                    Undo.RegisterCompleteObjectUndo(target, "Ensure expression menu control");
                    var control = new VRCExpressionsMenu.Control
                    {
                        name = controlName,
                        type = type,
                        value = controlValue
                    };
                    if (!string.IsNullOrWhiteSpace(parameterName))
                    {
                        control.parameter = new VRCExpressionsMenu.Control.Parameter { name = parameterName };
                    }
                    if (type == VRCExpressionsMenu.Control.ControlType.SubMenu)
                    {
                        var subMenu = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
                        subMenu.controls = new List<VRCExpressionsMenu.Control>();
                        var subPath = AssetDatabase.GenerateUniqueAssetPath($"{assetDir}/{AvatarAuthoringCrudCore.Sanitize(controlName, "Menu")}_SubMenu.asset");
                        AvatarAuthoringCrudCore.EnsureAssetFolder(assetDir);
                        AssetDatabase.CreateAsset(subMenu, subPath);
                        Undo.RegisterCreatedObjectUndo(subMenu, "Create submenu asset");
                        control.subMenu = subMenu;
                    }
                    target.controls.Add(control);
                    created = true;
                }
                EditorUtility.SetDirty(root);
                EditorUtility.SetDirty(target);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();
                return new SuccessResponse($"Ensured menu control '{controlName}'.", new
                {
                    ok = true,
                    preview = false,
                    action = "ensure_expression_menu_control",
                    menuPath,
                    controlName,
                    controlType = type.ToString(),
                    parameterName,
                    controlFloat = controlValue,
                    controlCreated = created,
                    rootMenuPath = AssetDatabase.GetAssetPath(root)
                });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Ensure expression menu control failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static VRCExpressionsMenu.Control.ControlType ParseMenuControlType(string value)
        {
            if (Enum.TryParse(value, true, out VRCExpressionsMenu.Control.ControlType parsed))
            {
                return parsed;
            }
            return VRCExpressionsMenu.Control.ControlType.Toggle;
        }

        private static VRCExpressionsMenu EnsureMenuPath(VRCExpressionsMenu root, string menuPath, string assetDir)
        {
            if (root.controls == null)
            {
                root.controls = new List<VRCExpressionsMenu.Control>();
            }
            var current = root;
            foreach (var rawPart in menuPath.Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries))
            {
                var part = rawPart.Trim();
                if (string.IsNullOrWhiteSpace(part))
                {
                    continue;
                }
                var existing = current.controls?.FirstOrDefault(control =>
                    control != null
                    && control.type == VRCExpressionsMenu.Control.ControlType.SubMenu
                    && string.Equals(control.name, part, StringComparison.Ordinal)
                    && control.subMenu != null);
                if (existing != null)
                {
                    current = existing.subMenu;
                    if (current.controls == null) current.controls = new List<VRCExpressionsMenu.Control>();
                    continue;
                }

                current = EnsureMenuHasRoom(current, assetDir);
                var subMenu = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
                subMenu.controls = new List<VRCExpressionsMenu.Control>();
                AvatarAuthoringCrudCore.EnsureAssetFolder(assetDir);
                var subPath = AssetDatabase.GenerateUniqueAssetPath($"{assetDir}/{AvatarAuthoringCrudCore.Sanitize(part, "Menu")}_SubMenu.asset");
                AssetDatabase.CreateAsset(subMenu, subPath);
                Undo.RegisterCreatedObjectUndo(subMenu, "Create submenu asset");
                current.controls.Add(new VRCExpressionsMenu.Control
                {
                    name = part,
                    type = VRCExpressionsMenu.Control.ControlType.SubMenu,
                    subMenu = subMenu
                });
                EditorUtility.SetDirty(current);
                current = subMenu;
            }
            return current;
        }

        private static VRCExpressionsMenu EnsureMenuHasRoom(VRCExpressionsMenu menu, string assetDir)
        {
            if (menu.controls == null)
            {
                menu.controls = new List<VRCExpressionsMenu.Control>();
            }
            if (menu.controls.Count < VRCExpressionsMenu.MAX_CONTROLS)
            {
                return menu;
            }
            var existingOverflow = menu.controls.FirstOrDefault(control =>
                control != null
                && control.type == VRCExpressionsMenu.Control.ControlType.SubMenu
                && control.subMenu != null
                && string.Equals(control.name, "More", StringComparison.Ordinal));
            if (existingOverflow?.subMenu != null)
            {
                if (existingOverflow.subMenu.controls == null)
                {
                    existingOverflow.subMenu.controls = new List<VRCExpressionsMenu.Control>();
                }
                return EnsureMenuHasRoom(existingOverflow.subMenu, assetDir);
            }
            AvatarAuthoringCrudCore.EnsureAssetFolder(assetDir);
            var overflow = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
            overflow.controls = new List<VRCExpressionsMenu.Control>();
            var overflowPath = AssetDatabase.GenerateUniqueAssetPath($"{assetDir}/Overflow_SubMenu.asset");
            AssetDatabase.CreateAsset(overflow, overflowPath);
            Undo.RegisterCreatedObjectUndo(overflow, "Create overflow submenu");
            var moved = menu.controls[menu.controls.Count - 1];
            menu.controls.RemoveAt(menu.controls.Count - 1);
            overflow.controls.Add(moved);
            menu.controls.Add(new VRCExpressionsMenu.Control
            {
                name = "More",
                type = VRCExpressionsMenu.Control.ControlType.SubMenu,
                subMenu = overflow
            });
            EditorUtility.SetDirty(overflow);
            EditorUtility.SetDirty(menu);
            return overflow;
        }

        private static bool MenuContainsControl(VRCExpressionsMenu menu, string menuPath, string controlName, string parameterName, int intValue, HashSet<int> visited, int depth)
        {
            var target = FindMenuByPath(menu, menuPath, visited, depth);
            if (target?.controls == null)
            {
                return false;
            }
            return target.controls.Any(control =>
                control != null
                && string.Equals(control.name, controlName, StringComparison.Ordinal)
                && (string.IsNullOrWhiteSpace(parameterName)
                    || (control.parameter != null
                        && string.Equals(control.parameter.name, parameterName, StringComparison.Ordinal)
                        && Mathf.RoundToInt(control.value) == intValue)));
        }

        private static VRCExpressionsMenu FindMenuByPath(VRCExpressionsMenu menu, string menuPath, HashSet<int> visited, int depth)
        {
            if (menu == null || depth > 8 || !visited.Add(menu.GetInstanceID()))
            {
                return null;
            }
            var parts = menuPath.Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries);
            return FindMenuByPathParts(menu, parts, 0, visited, depth);
        }

        private static VRCExpressionsMenu FindMenuByPathParts(VRCExpressionsMenu menu, string[] parts, int index, HashSet<int> visited, int depth)
        {
            if (index >= parts.Length)
            {
                return menu;
            }
            if (menu.controls == null)
            {
                return null;
            }
            foreach (var control in menu.controls)
            {
                if (control?.type == VRCExpressionsMenu.Control.ControlType.SubMenu
                    && control.subMenu != null
                    && string.Equals(control.name, parts[index], StringComparison.Ordinal))
                {
                    return FindMenuByPathParts(control.subMenu, parts, index + 1, visited, depth + 1);
                }
            }
            return null;
        }
    }

    [McpForUnityTool(
        name: "vrc_ensure_animator_state",
        Description = "Ensure an FX animator parameter, layer, state, optional generated clip, and Any-State transition condition. Supports preview."
    )]
    public static class EnsureAnimatorStateTool
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                @params = @params ?? new JObject();
                var avatarPath = (@params["avatarPath"]?.ToString() ?? "").Trim();
                var layerName = (@params["layerName"]?.ToString() ?? "").Trim();
                var stateName = (@params["stateName"]?.ToString() ?? "State").Trim();
                var parameterName = (@params["parameterName"]?.ToString() ?? "").Trim();
                var parameterType = AvatarAuthoringCrudCore.ParseAnimatorParameterType(@params["parameterType"]?.ToString() ?? "Int");
                var conditionMode = AvatarAuthoringCrudCore.ParseConditionMode(@params["conditionMode"]?.ToString() ?? "Equals");
                var threshold = @params["threshold"]?.Value<float?>() ?? 0f;
                var writeDefaults = @params["writeDefaults"]?.Value<bool?>() ?? true;
                var preview = @params["preview"]?.Value<bool?>() ?? false;
                var assetDir = AvatarAuthoringCrudCore.NormalizeAssetDir(@params["assetDir"]?.ToString() ?? "");
                if (string.IsNullOrWhiteSpace(layerName)) return new ErrorResponse("layerName is required.");
                if (string.IsNullOrWhiteSpace(stateName)) return new ErrorResponse("stateName is required.");
                if (string.IsNullOrWhiteSpace(parameterName)) return new ErrorResponse("parameterName is required.");

                var descriptor = AvatarAuthoringCrudCore.ResolveAvatarDescriptor(avatarPath);
                var controller = AvatarAuthoringCrudCore.GetFxController(descriptor);
                var layerExists = controller != null && controller.layers.Any(layer => string.Equals(layer.name, layerName, StringComparison.Ordinal));
                var stateExists = layerExists && FindState(controller.layers.First(layer => layer.name == layerName).stateMachine, stateName) != null;
                var plan = new
                {
                    action = "ensure_animator_state",
                    avatarPath = AvatarAuthoringCrudCore.GetTransformPath(descriptor.transform),
                    avatarName = descriptor.name,
                    layerName,
                    stateName,
                    parameterName,
                    parameterType = parameterType.ToString(),
                    conditionMode = conditionMode.ToString(),
                    threshold,
                    writeDefaults,
                    willCreateFxController = controller == null,
                    willCreateLayer = !layerExists,
                    willCreateState = !stateExists,
                    assetDir
                };
                if (preview)
                {
                    return new SuccessResponse($"Preview: would ensure animator state '{stateName}'.", new { ok = true, preview = true, plan });
                }

                controller = AvatarAuthoringCrudCore.EnsureFxController(descriptor, assetDir);
                Undo.RegisterCompleteObjectUndo(controller, "Ensure animator state");
                var parameterCreated = EnsureControllerParameter(controller, parameterName, parameterType);
                var layer = EnsureLayer(controller, layerName);
                var state = FindState(layer.stateMachine, stateName);
                var stateCreated = false;
                if (state == null)
                {
                    state = layer.stateMachine.AddState(stateName);
                    stateCreated = true;
                }
                state.writeDefaultValues = writeDefaults;
                if (state.motion == null)
                {
                    var clip = CreateEmptyClip(assetDir, descriptor.name, layerName, stateName);
                    state.motion = clip;
                }
                var transitionCreated = EnsureAnyStateTransition(layer.stateMachine, state, parameterName, conditionMode, threshold);
                EditorUtility.SetDirty(controller);
                EditorUtility.SetDirty(layer.stateMachine);
                EditorUtility.SetDirty(state);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();
                return new SuccessResponse($"Ensured animator state '{stateName}'.", new
                {
                    ok = true,
                    preview = false,
                    action = "ensure_animator_state",
                    fxControllerPath = AssetDatabase.GetAssetPath(controller),
                    layerName,
                    stateName,
                    parameterName,
                    parameterType = parameterType.ToString(),
                    parameterCreated,
                    stateCreated,
                    transitionCreated,
                    writeDefaults
                });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Ensure animator state failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static bool EnsureControllerParameter(AnimatorController controller, string parameterName, AnimatorControllerParameterType parameterType)
        {
            var existing = controller.parameters.FirstOrDefault(parameter => parameter.name == parameterName);
            if (existing != null)
            {
                if (existing.type != parameterType)
                {
                    throw new InvalidOperationException($"Animator parameter '{parameterName}' already exists as {existing.type}, not {parameterType}.");
                }
                return false;
            }
            controller.AddParameter(parameterName, parameterType);
            return true;
        }

        private static AnimatorControllerLayer EnsureLayer(AnimatorController controller, string layerName)
        {
            var existing = controller.layers.FirstOrDefault(layer => layer.name == layerName);
            if (existing != null)
            {
                return existing;
            }
            controller.AddLayer(layerName);
            var layers = controller.layers;
            var layer = layers[layers.Length - 1];
            layer.defaultWeight = 1f;
            controller.layers = layers;
            return layer;
        }

        private static AnimatorState FindState(AnimatorStateMachine machine, string stateName)
        {
            if (machine == null)
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
            foreach (var sub in machine.stateMachines)
            {
                var nested = FindState(sub.stateMachine, stateName);
                if (nested != null)
                {
                    return nested;
                }
            }
            return null;
        }

        private static bool EnsureAnyStateTransition(AnimatorStateMachine machine, AnimatorState state, string parameterName, AnimatorConditionMode conditionMode, float threshold)
        {
            foreach (var transition in machine.anyStateTransitions ?? Array.Empty<AnimatorStateTransition>())
            {
                if (transition?.destinationState != state || transition.conditions == null)
                {
                    continue;
                }
                if (transition.conditions.Any(condition =>
                    condition.mode == conditionMode
                    && string.Equals(condition.parameter, parameterName, StringComparison.Ordinal)
                    && Mathf.Approximately(condition.threshold, threshold)))
                {
                    return false;
                }
            }
            var created = machine.AddAnyStateTransition(state);
            created.hasExitTime = false;
            created.exitTime = 0f;
            created.duration = 0f;
            created.canTransitionToSelf = false;
            created.AddCondition(conditionMode, threshold, parameterName);
            return true;
        }

        private static AnimationClip CreateEmptyClip(string assetDir, string avatarName, string layerName, string stateName)
        {
            AvatarAuthoringCrudCore.EnsureAssetFolder(assetDir);
            var path = AssetDatabase.GenerateUniqueAssetPath(
                $"{assetDir}/{AvatarAuthoringCrudCore.Sanitize(avatarName, "Avatar")}_{AvatarAuthoringCrudCore.Sanitize(layerName, "Layer")}_{AvatarAuthoringCrudCore.Sanitize(stateName, "State")}.anim");
            var clip = new AnimationClip { name = Path.GetFileNameWithoutExtension(path) };
            AssetDatabase.CreateAsset(clip, path);
            Undo.RegisterCreatedObjectUndo(clip, "Create animator state clip");
            return clip;
        }
    }
}
