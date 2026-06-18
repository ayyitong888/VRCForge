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
    // High-risk wardrobe management writer for existing int-exclusive wardrobes.
    // Complements WardrobeScanner and WardrobeOutfitWriter:
    //   - remove_outfit: remove menu toggles + FX Any-State Equals binding/state;
    //     deactivate the outfit objects by default, delete only if requested.
    //   - rename_outfit: rename matching menu toggle(s) and FX state(s).
    //   - reorder_outfits: reorder wardrobe menu toggles by int value, preserving
    //     existing control objects and overflowing into nested SubMenus.
    //   - set_default: set the expression Int parameter default value.
    //   - delete_wardrobe: remove the expression parameter, menu toggles, and FX
    //     wardrobe bindings/layers; scene objects are preserved unless requested.
    // All writes support preview and Undo. Asset deletion is opt-in.
    [McpForUnityTool(
        name: "vrc_manage_wardrobe",
        Description = "Manage an existing int-exclusive wardrobe: remove/rename/reorder outfits, set the default int value, or delete wardrobe bindings. Supports preview; destructive object/asset deletion is opt-in."
    )]
    public static class WardrobeManagerWriter
    {
        private const string DefaultAssetDir = "Assets/VRCForge/Generated/Wardrobe";

        public static object HandleCommand(JObject @params)
        {
            try
            {
                @params = @params ?? new JObject();
                var action = NormalizeAction(@params["action"]?.ToString() ?? "");
                var preview = @params["preview"]?.Value<bool?>() ?? false;
                var avatarPath = (@params["avatarPath"]?.ToString() ?? "").Trim();
                var parameterName = (@params["parameterName"]?.ToString() ?? "").Trim();
                var targetName = FirstNonEmpty(
                    @params["outfitName"]?.ToString(),
                    @params["targetName"]?.ToString(),
                    @params["stateName"]?.ToString(),
                    @params["controlName"]?.ToString());
                var newName = FirstNonEmpty(@params["newName"]?.ToString(), @params["newOutfitName"]?.ToString());
                var deleteObjects = @params["deleteObjects"]?.Value<bool?>() ?? false;
                var deactivateObjects = @params["deactivateObjects"]?.Value<bool?>() ?? action == "remove_outfit";
                var deleteGeneratedAssets = @params["deleteGeneratedAssets"]?.Value<bool?>() ?? false;
                var confirmDeleteWardrobe = @params["confirmDeleteWardrobe"]?.Value<bool?>() ?? false;
                var assetDir = NormalizeAssetDir(@params["assetDir"]?.ToString() ?? @params["clipOutputDir"]?.ToString() ?? "");

                if (string.IsNullOrWhiteSpace(action))
                {
                    return new ErrorResponse("action is required: remove_outfit, rename_outfit, reorder_outfits, set_default, or delete_wardrobe.");
                }
                if (string.IsNullOrWhiteSpace(parameterName))
                {
                    return new ErrorResponse("parameterName is required.");
                }
                if (action == "rename_outfit" && string.IsNullOrWhiteSpace(newName))
                {
                    return new ErrorResponse("newName is required for rename_outfit.");
                }
                if (action == "delete_wardrobe" && !confirmDeleteWardrobe)
                {
                    return new ErrorResponse("confirmDeleteWardrobe=true is required for delete_wardrobe.");
                }

                var descriptor = ResolveAvatarDescriptor(avatarPath);
                var context = BuildContext(descriptor, parameterName);

                List<int> targetValues;
                if (action == "delete_wardrobe")
                {
                    targetValues = context.AllValues().ToList();
                }
                else if (action == "reorder_outfits")
                {
                    targetValues = ReadIntArray(@params, "orderValues", "order_values");
                    if (targetValues.Count == 0)
                    {
                        return new ErrorResponse("orderValues is required for reorder_outfits.");
                    }
                }
                else if (action == "set_default")
                {
                    targetValues = ResolveRequiredTargetValues(@params, context, targetName, allowMany: false);
                }
                else
                {
                    targetValues = ResolveRequiredTargetValues(@params, context, targetName, allowMany: false);
                }

                var plan = BuildPlan(action, descriptor, context, targetValues, newName, deleteObjects, deactivateObjects, deleteGeneratedAssets);
                if (preview)
                {
                    return new SuccessResponse($"Preview: would {action} for wardrobe '{parameterName}'.", new
                    {
                        ok = true,
                        preview = true,
                        plan
                    });
                }

                var undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName($"Manage wardrobe '{parameterName}'");
                ApplyAction(action, descriptor, context, targetValues, newName, deleteObjects, deactivateObjects, deleteGeneratedAssets, assetDir, @params);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();
                Undo.CollapseUndoOperations(undoGroup);

                return new SuccessResponse($"Wardrobe action '{action}' completed for '{parameterName}'.", new
                {
                    ok = true,
                    preview = false,
                    action,
                    avatarPath = GetTransformPath(descriptor.transform),
                    avatarName = descriptor.name,
                    parameterName,
                    targetValues,
                    newName,
                    deleteObjects,
                    deactivateObjects,
                    deleteGeneratedAssets,
                    affectedMenuControls = plan.affectedMenuControls,
                    affectedFxStates = plan.affectedFxStates,
                    affectedObjects = plan.affectedObjects,
                    warnings = plan.warnings
                });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Manage wardrobe failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static void ApplyAction(
            string action,
            VRCAvatarDescriptor descriptor,
            WardrobeContext context,
            List<int> targetValues,
            string newName,
            bool deleteObjects,
            bool deactivateObjects,
            bool deleteGeneratedAssets,
            string assetDir,
            JObject @params)
        {
            switch (action)
            {
                case "remove_outfit":
                    RemoveOutfit(descriptor, context, targetValues, deleteObjects, deactivateObjects, deleteGeneratedAssets);
                    break;
                case "rename_outfit":
                    RenameOutfit(context, targetValues, newName);
                    break;
                case "reorder_outfits":
                    ReorderOutfits(context, targetValues, assetDir);
                    break;
                case "set_default":
                    SetDefaultValue(context, targetValues[0]);
                    break;
                case "delete_wardrobe":
                    DeleteWardrobe(descriptor, context, deleteObjects, deleteGeneratedAssets);
                    break;
                default:
                    throw new InvalidOperationException($"Unsupported wardrobe action: {action}");
            }
        }

        private static object BuildPlan(
            string action,
            VRCAvatarDescriptor descriptor,
            WardrobeContext context,
            List<int> targetValues,
            string newName,
            bool deleteObjects,
            bool deactivateObjects,
            bool deleteGeneratedAssets)
        {
            var targetSet = new HashSet<int>(targetValues);
            var menuTargets = action == "delete_wardrobe"
                ? context.menuControls
                : context.menuControls.Where(item => targetSet.Contains(item.value)).ToList();
            var transitionTargets = action == "delete_wardrobe"
                ? context.transitions
                : context.transitions.Where(item => targetSet.Contains(item.value)).ToList();
            var stateTargets = transitionTargets
                .Select(item => item.state)
                .Where(item => item != null)
                .Distinct()
                .Select(item => item.name ?? "")
                .Where(item => !string.IsNullOrWhiteSpace(item))
                .OrderBy(item => item, StringComparer.Ordinal)
                .ToList();
            var objectTargets = CollectOnObjects(transitionTargets);
            var warnings = new List<string>();

            if (action == "remove_outfit" && objectTargets.Count == 0)
            {
                warnings.Add("No m_IsActive on-objects were found in the target outfit clip(s); only menu/FX bindings will be removed.");
            }
            if (action == "delete_wardrobe" && !deleteObjects)
            {
                warnings.Add("Scene outfit objects will be preserved because deleteObjects is false.");
            }
            if (deleteGeneratedAssets)
            {
                warnings.Add("Generated animation clip assets referenced by removed states may be deleted.");
            }

            return new
            {
                action,
                avatarPath = GetTransformPath(descriptor.transform),
                avatarName = descriptor.name,
                parameterName = context.parameterName,
                targetValues,
                newName,
                deleteObjects,
                deactivateObjects,
                deleteGeneratedAssets,
                affectedMenuControls = menuTargets.Select(item => new { item.menuPath, item.value, name = item.control.name ?? "" }).ToList(),
                affectedFxStates = stateTargets,
                affectedObjects = objectTargets,
                affectedFxLayers = context.layersWithWardrobeEquals.Select(item => item.name).ToList(),
                warnings
            };
        }

        private static void RemoveOutfit(
            VRCAvatarDescriptor descriptor,
            WardrobeContext context,
            List<int> targetValues,
            bool deleteObjects,
            bool deactivateObjects,
            bool deleteGeneratedAssets)
        {
            var targetSet = new HashSet<int>(targetValues);
            var transitions = context.transitions.Where(item => targetSet.Contains(item.value)).ToList();
            var targetStates = transitions.Select(item => item.state).Where(item => item != null).Distinct().ToList();
            var objectPaths = CollectOnObjects(transitions);

            RemoveMenuControls(context, item => targetSet.Contains(item.value));
            RemoveTransitions(context, transitions);
            RemoveUnsharedStates(context, targetStates);
            HandleObjects(descriptor, objectPaths, deleteObjects, deactivateObjects);
            DeleteStateClipAssets(context, targetStates, deleteGeneratedAssets);
        }

        private static void RenameOutfit(WardrobeContext context, List<int> targetValues, string newName)
        {
            var targetSet = new HashSet<int>(targetValues);
            foreach (var item in context.menuControls.Where(item => targetSet.Contains(item.value)))
            {
                Undo.RegisterCompleteObjectUndo(item.menu, "Rename wardrobe menu control");
                item.control.name = newName;
                EditorUtility.SetDirty(item.menu);
            }

            var states = context.transitions
                .Where(item => targetSet.Contains(item.value))
                .Select(item => item.state)
                .Where(item => item != null)
                .Distinct()
                .ToList();
            foreach (var state in states)
            {
                Undo.RegisterCompleteObjectUndo(state, "Rename wardrobe FX state");
                state.name = Sanitize(newName, "Outfit");
                EditorUtility.SetDirty(state);
            }
        }

        private static void ReorderOutfits(WardrobeContext context, List<int> orderValues, string assetDir)
        {
            var existingByValue = context.menuControls
                .GroupBy(item => item.value)
                .ToDictionary(group => group.Key, group => group.ToList());
            foreach (var value in orderValues)
            {
                if (!existingByValue.ContainsKey(value))
                {
                    throw new InvalidOperationException($"Cannot reorder wardrobe: value {value} has no menu toggle for '{context.parameterName}'.");
                }
            }

            var ordered = new List<MenuControlRef>();
            foreach (var value in orderValues)
            {
                ordered.AddRange(existingByValue[value].OrderBy(item => item.menuPath, StringComparer.Ordinal));
            }
            ordered.AddRange(context.menuControls
                .Where(item => !orderValues.Contains(item.value))
                .OrderBy(item => item.menuPath, StringComparer.Ordinal));

            var targetMenu = context.menuControls
                .OrderBy(item => item.depth)
                .Select(item => item.menu)
                .FirstOrDefault(item => item != null)
                ?? context.rootMenu;
            if (targetMenu == null)
            {
                throw new InvalidOperationException("Avatar has no expressions menu to reorder.");
            }

            foreach (var group in context.menuControls.GroupBy(item => item.menu))
            {
                var menu = group.Key;
                if (menu?.controls == null)
                {
                    continue;
                }
                Undo.RegisterCompleteObjectUndo(menu, "Reorder wardrobe menu controls");
                foreach (var item in group.OrderByDescending(item => item.index))
                {
                    if (item.index >= 0 && item.index < menu.controls.Count)
                    {
                        menu.controls.RemoveAt(item.index);
                    }
                }
                EditorUtility.SetDirty(menu);
            }

            var current = targetMenu;
            foreach (var item in ordered)
            {
                current = EnsureMenuHasRoom(current, assetDir, context.parameterName);
                Undo.RegisterCompleteObjectUndo(current, "Reorder wardrobe menu controls");
                if (current.controls == null)
                {
                    current.controls = new List<VRCExpressionsMenu.Control>();
                }
                current.controls.Add(item.control);
                EditorUtility.SetDirty(current);
            }
        }

        private static void SetDefaultValue(WardrobeContext context, int targetValue)
        {
            if (context.parametersAsset == null || context.parameter == null)
            {
                throw new InvalidOperationException($"Expression parameter '{context.parameterName}' was not found.");
            }
            Undo.RegisterCompleteObjectUndo(context.parametersAsset, "Set wardrobe default value");
            var parameters = context.parametersAsset.parameters?.Where(item => item != null).ToArray() ?? Array.Empty<VRCExpressionParameters.Parameter>();
            foreach (var parameter in parameters)
            {
                if (parameter.name == context.parameterName)
                {
                    parameter.defaultValue = targetValue;
                }
            }
            context.parametersAsset.parameters = parameters;
            EditorUtility.SetDirty(context.parametersAsset);
        }

        private static void DeleteWardrobe(
            VRCAvatarDescriptor descriptor,
            WardrobeContext context,
            bool deleteObjects,
            bool deleteGeneratedAssets)
        {
            var transitions = context.transitions.ToList();
            var targetStates = transitions.Select(item => item.state).Where(item => item != null).Distinct().ToList();
            var objectPaths = CollectOnObjects(transitions);

            RemoveExpressionParameter(context);
            RemoveMenuControls(context, _item => true);
            RemoveTransitions(context, transitions);
            RemoveUnsharedStates(context, targetStates);
            RemoveEmptyWardrobeLayers(context);
            HandleObjects(descriptor, objectPaths, deleteObjects, false);
            DeleteStateClipAssets(context, targetStates, deleteGeneratedAssets);
        }

        private static void RemoveExpressionParameter(WardrobeContext context)
        {
            if (context.parametersAsset == null || context.parametersAsset.parameters == null)
            {
                return;
            }
            var before = context.parametersAsset.parameters.Length;
            var remaining = context.parametersAsset.parameters
                .Where(item => item != null && item.name != context.parameterName)
                .ToArray();
            if (remaining.Length == before)
            {
                return;
            }
            Undo.RegisterCompleteObjectUndo(context.parametersAsset, "Remove wardrobe expression parameter");
            context.parametersAsset.parameters = remaining;
            EditorUtility.SetDirty(context.parametersAsset);
        }

        private static void RemoveMenuControls(WardrobeContext context, Func<MenuControlRef, bool> predicate)
        {
            foreach (var group in context.menuControls.Where(predicate).GroupBy(item => item.menu))
            {
                var menu = group.Key;
                if (menu?.controls == null)
                {
                    continue;
                }
                Undo.RegisterCompleteObjectUndo(menu, "Remove wardrobe menu control");
                foreach (var item in group.OrderByDescending(item => item.index))
                {
                    if (item.index >= 0 && item.index < menu.controls.Count)
                    {
                        menu.controls.RemoveAt(item.index);
                    }
                }
                EditorUtility.SetDirty(menu);
            }
        }

        private static void RemoveTransitions(WardrobeContext context, List<TransitionRef> transitions)
        {
            foreach (var group in transitions.GroupBy(item => item.machine))
            {
                var machine = group.Key;
                if (machine == null)
                {
                    continue;
                }
                Undo.RegisterCompleteObjectUndo(machine, "Remove wardrobe FX transition");
                foreach (var item in group)
                {
                    machine.RemoveAnyStateTransition(item.transition);
                }
                EditorUtility.SetDirty(machine);
            }
            foreach (var item in transitions)
            {
                context.transitions.Remove(item);
            }
            if (context.fxController != null)
            {
                Undo.RegisterCompleteObjectUndo(context.fxController, "Remove wardrobe FX transition");
                EditorUtility.SetDirty(context.fxController);
            }
        }

        private static void RemoveUnsharedStates(WardrobeContext context, List<AnimatorState> states)
        {
            foreach (var state in states.Distinct())
            {
                if (state == null)
                {
                    continue;
                }
                if (ContextHasTransitionToState(context, state))
                {
                    continue;
                }
                var owner = context.states.FirstOrDefault(item => item.state == state)?.machine;
                if (owner == null)
                {
                    continue;
                }
                Undo.RegisterCompleteObjectUndo(owner, "Remove wardrobe FX state");
                owner.RemoveState(state);
                EditorUtility.SetDirty(owner);
            }
        }

        private static bool ContextHasTransitionToState(WardrobeContext context, AnimatorState state)
        {
            return context.transitions.Any(item => item.transition != null && item.transition.destinationState == state);
        }

        private static void RemoveEmptyWardrobeLayers(WardrobeContext context)
        {
            if (context.fxController == null)
            {
                return;
            }
            var layers = context.fxController.layers;
            for (var index = layers.Length - 1; index >= 0; index--)
            {
                var layer = layers[index];
                if (layer == null || layer.stateMachine == null)
                {
                    continue;
                }
                var hasWardrobeParam = LayerHasEquals(layer.stateMachine, context.parameterName);
                if (hasWardrobeParam)
                {
                    continue;
                }
                var lookedLikeWardrobe = string.Equals(layer.name, context.parameterName, StringComparison.Ordinal)
                    || context.layersWithWardrobeEquals.Any(item => ReferenceEquals(item.stateMachine, layer.stateMachine));
                if (!lookedLikeWardrobe)
                {
                    continue;
                }
                if (LayerHasAnyAnyStateTransition(layer.stateMachine))
                {
                    continue;
                }
                Undo.RegisterCompleteObjectUndo(context.fxController, "Remove wardrobe FX layer");
                context.fxController.RemoveLayer(index);
                EditorUtility.SetDirty(context.fxController);
            }
        }

        private static void HandleObjects(
            VRCAvatarDescriptor descriptor,
            List<string> objectPaths,
            bool deleteObjects,
            bool deactivateObjects)
        {
            foreach (var path in objectPaths.Distinct().OrderBy(item => item, StringComparer.Ordinal))
            {
                var transform = ResolveUnderRoot(descriptor.transform, path);
                if (transform == null || transform == descriptor.transform)
                {
                    continue;
                }
                if (deleteObjects)
                {
                    Undo.DestroyObjectImmediate(transform.gameObject);
                    continue;
                }
                if (deactivateObjects)
                {
                    Undo.RecordObject(transform.gameObject, "Deactivate removed wardrobe outfit object");
                    transform.gameObject.SetActive(false);
                    EditorUtility.SetDirty(transform.gameObject);
                }
            }
        }

        private static void DeleteStateClipAssets(WardrobeContext context, List<AnimatorState> states, bool deleteGeneratedAssets)
        {
            if (!deleteGeneratedAssets)
            {
                return;
            }
            foreach (var path in states
                .Where(item => item != null && !ContextHasTransitionToState(context, item))
                .Select(item => item?.motion as AnimationClip)
                .Where(item => item != null)
                .Select(AssetDatabase.GetAssetPath)
                .Where(item => !string.IsNullOrWhiteSpace(item))
                .Distinct())
            {
                AssetDatabase.DeleteAsset(path);
            }
        }

        private static WardrobeContext BuildContext(VRCAvatarDescriptor descriptor, string parameterName)
        {
            var context = new WardrobeContext
            {
                descriptor = descriptor,
                parameterName = parameterName,
                parametersAsset = descriptor.expressionParameters,
                rootMenu = descriptor.expressionsMenu,
                fxController = GetFxController(descriptor)
            };

            if (context.parametersAsset != null && context.parametersAsset.parameters != null)
            {
                context.parameter = context.parametersAsset.parameters.FirstOrDefault(item =>
                    item != null && item.name == parameterName && item.valueType == VRCExpressionParameters.ValueType.Int);
            }
            if (context.parameter == null)
            {
                throw new InvalidOperationException($"Int expression parameter '{parameterName}' was not found.");
            }

            CollectMenuControls(context.rootMenu, "", parameterName, context.menuControls, new HashSet<int>(), 0);
            if (context.fxController != null)
            {
                var layers = context.fxController.layers;
                for (var index = 0; index < layers.Length; index++)
                {
                    var layer = layers[index];
                    if (layer == null || layer.stateMachine == null)
                    {
                        continue;
                    }
                    CollectStates(layer.stateMachine, context.states);
                    var before = context.transitions.Count;
                    CollectTransitions(layer.stateMachine, index, layer.name ?? "", parameterName, context.transitions);
                    if (context.transitions.Count > before)
                    {
                        context.layersWithWardrobeEquals.Add(layer);
                    }
                }
            }
            if (context.menuControls.Count == 0 && context.transitions.Count == 0)
            {
                throw new InvalidOperationException($"Parameter '{parameterName}' exists, but no wardrobe menu toggles or FX Any-State Equals bindings were found.");
            }
            return context;
        }

        private static List<int> ResolveRequiredTargetValues(JObject @params, WardrobeContext context, string targetName, bool allowMany)
        {
            var values = ReadIntArray(@params, "targetValues", "target_values", "values");
            var targetValue = @params["targetValue"] ?? @params["target_value"] ?? @params["outfitValue"] ?? @params["outfit_value"] ?? @params["value"];
            if (targetValue != null)
            {
                values.Add(targetValue.Value<int>());
            }
            if (values.Count == 0 && !string.IsNullOrWhiteSpace(targetName))
            {
                values.AddRange(context.menuControls
                    .Where(item => string.Equals(item.control.name, targetName, StringComparison.Ordinal))
                    .Select(item => item.value));
                values.AddRange(context.transitions
                    .Where(item => item.state != null && string.Equals(item.state.name, targetName, StringComparison.Ordinal))
                    .Select(item => item.value));
            }
            values = values.Distinct().OrderBy(item => item).ToList();
            if (values.Count == 0)
            {
                throw new InvalidOperationException("Target outfit value or outfitName is required.");
            }
            if (!allowMany && values.Count > 1)
            {
                throw new InvalidOperationException("Target matched multiple outfit values. Pass targetValue explicitly.");
            }
            var available = new HashSet<int>(context.AllValues());
            foreach (var value in values)
            {
                if (!available.Contains(value))
                {
                    throw new InvalidOperationException($"Wardrobe value {value} was not found for '{context.parameterName}'.");
                }
            }
            return values;
        }

        private static List<string> CollectOnObjects(List<TransitionRef> transitions)
        {
            var result = new SortedSet<string>(StringComparer.Ordinal);
            foreach (var transition in transitions)
            {
                var clip = transition.state?.motion as AnimationClip;
                foreach (var item in ReadClipObjects(clip, active: true))
                {
                    result.Add(item);
                }
            }
            return result.ToList();
        }

        private static IEnumerable<string> ReadClipObjects(AnimationClip clip, bool active)
        {
            if (clip == null)
            {
                yield break;
            }
            foreach (var binding in AnimationUtility.GetCurveBindings(clip))
            {
                if (binding.type != typeof(GameObject) || binding.propertyName != "m_IsActive")
                {
                    continue;
                }
                var curve = AnimationUtility.GetEditorCurve(clip, binding);
                if (curve == null || curve.length == 0)
                {
                    continue;
                }
                var isActive = curve.keys[curve.length - 1].value >= 0.5f;
                if (isActive == active)
                {
                    yield return binding.path;
                }
            }
        }

        private static void CollectMenuControls(
            VRCExpressionsMenu menu,
            string parentPath,
            string parameterName,
            List<MenuControlRef> sink,
            HashSet<int> visited,
            int depth)
        {
            if (menu == null || depth > 8 || menu.controls == null || !visited.Add(menu.GetInstanceID()))
            {
                return;
            }
            for (var index = 0; index < menu.controls.Count; index++)
            {
                var control = menu.controls[index];
                if (control == null)
                {
                    continue;
                }
                var name = control.name ?? "";
                var path = string.IsNullOrWhiteSpace(parentPath) ? name : $"{parentPath}/{name}";
                if (control.parameter != null && string.Equals(control.parameter.name, parameterName, StringComparison.Ordinal))
                {
                    sink.Add(new MenuControlRef
                    {
                        menu = menu,
                        control = control,
                        index = index,
                        value = Mathf.RoundToInt(control.value),
                        menuPath = path,
                        depth = depth
                    });
                }
                if (control.type == VRCExpressionsMenu.Control.ControlType.SubMenu && control.subMenu != null)
                {
                    CollectMenuControls(control.subMenu, path, parameterName, sink, visited, depth + 1);
                }
            }
        }

        private static void CollectStates(AnimatorStateMachine machine, List<StateRef> sink)
        {
            if (machine == null)
            {
                return;
            }
            foreach (var child in machine.states)
            {
                if (child.state != null)
                {
                    sink.Add(new StateRef { machine = machine, state = child.state });
                }
            }
            foreach (var child in machine.stateMachines)
            {
                CollectStates(child.stateMachine, sink);
            }
        }

        private static void CollectTransitions(
            AnimatorStateMachine machine,
            int layerIndex,
            string layerName,
            string parameterName,
            List<TransitionRef> sink)
        {
            if (machine == null)
            {
                return;
            }
            foreach (var transition in machine.anyStateTransitions ?? Array.Empty<AnimatorStateTransition>())
            {
                if (transition == null || transition.destinationState == null || transition.conditions == null)
                {
                    continue;
                }
                foreach (var condition in transition.conditions)
                {
                    if (condition.mode == AnimatorConditionMode.Equals
                        && string.Equals(condition.parameter, parameterName, StringComparison.Ordinal))
                    {
                        sink.Add(new TransitionRef
                        {
                            layerIndex = layerIndex,
                            layerName = layerName,
                            machine = machine,
                            transition = transition,
                            state = transition.destinationState,
                            value = Mathf.RoundToInt(condition.threshold)
                        });
                    }
                }
            }
            foreach (var child in machine.stateMachines)
            {
                CollectTransitions(child.stateMachine, layerIndex, layerName, parameterName, sink);
            }
        }

        private static bool LayerHasEquals(AnimatorStateMachine machine, string parameterName)
        {
            if (machine == null)
            {
                return false;
            }
            foreach (var transition in machine.anyStateTransitions ?? Array.Empty<AnimatorStateTransition>())
            {
                if (transition?.conditions == null)
                {
                    continue;
                }
                if (transition.conditions.Any(condition =>
                    condition.mode == AnimatorConditionMode.Equals
                    && string.Equals(condition.parameter, parameterName, StringComparison.Ordinal)))
                {
                    return true;
                }
            }
            return machine.stateMachines.Any(child => LayerHasEquals(child.stateMachine, parameterName));
        }

        private static bool LayerHasAnyAnyStateTransition(AnimatorStateMachine machine)
        {
            if (machine == null)
            {
                return false;
            }
            if ((machine.anyStateTransitions?.Length ?? 0) > 0)
            {
                return true;
            }
            return machine.stateMachines.Any(child => LayerHasAnyAnyStateTransition(child.stateMachine));
        }

        private static VRCExpressionsMenu EnsureMenuHasRoom(VRCExpressionsMenu menu, string assetDir, string parameterName)
        {
            if (menu.controls == null)
            {
                menu.controls = new List<VRCExpressionsMenu.Control>();
            }
            if (menu.controls.Count < VRCExpressionsMenu.MAX_CONTROLS)
            {
                return menu;
            }

            Directory.CreateDirectory(assetDir);
            var subMenu = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
            subMenu.controls = new List<VRCExpressionsMenu.Control>();
            var subPath = AssetDatabase.GenerateUniqueAssetPath($"{assetDir}/{Sanitize(parameterName, "Wardrobe")}_Reordered_SubMenu.asset");
            AssetDatabase.CreateAsset(subMenu, subPath);
            Undo.RegisterCreatedObjectUndo(subMenu, "Create wardrobe reorder submenu");
            Undo.RegisterCompleteObjectUndo(menu, "Create wardrobe reorder submenu");
            while (menu.controls.Count >= VRCExpressionsMenu.MAX_CONTROLS && menu.controls.Count > 0)
            {
                var moved = menu.controls[menu.controls.Count - 1];
                menu.controls.RemoveAt(menu.controls.Count - 1);
                subMenu.controls.Insert(0, moved);
            }
            menu.controls.Add(new VRCExpressionsMenu.Control
            {
                name = "More",
                type = VRCExpressionsMenu.Control.ControlType.SubMenu,
                subMenu = subMenu
            });
            EditorUtility.SetDirty(menu);
            EditorUtility.SetDirty(subMenu);
            return subMenu;
        }

        private static AnimatorController GetFxController(VRCAvatarDescriptor descriptor)
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

        private static Transform ResolveUnderRoot(Transform root, string rawPath)
        {
            var path = NormalizePath(rawPath);
            if (string.IsNullOrEmpty(path))
            {
                return null;
            }
            var direct = root.Find(path);
            if (direct != null)
            {
                return direct;
            }
            var rootName = root.name;
            if (path.Equals(rootName, StringComparison.Ordinal))
            {
                return root;
            }
            if (path.StartsWith(rootName + "/", StringComparison.Ordinal))
            {
                var byFull = root.Find(path.Substring(rootName.Length + 1));
                if (byFull != null)
                {
                    return byFull;
                }
            }
            var leaf = path.Contains("/") ? path.Substring(path.LastIndexOf('/') + 1) : path;
            Transform match = null;
            foreach (var transform in root.GetComponentsInChildren<Transform>(true))
            {
                if (transform == root)
                {
                    continue;
                }
                var rel = RelativePath(root, transform);
                if (rel.Equals(path, StringComparison.Ordinal)
                    || rel.EndsWith("/" + path, StringComparison.Ordinal)
                    || transform.name.Equals(leaf, StringComparison.Ordinal))
                {
                    if (match != null && !match.Equals(transform))
                    {
                        if (rel.Equals(path, StringComparison.Ordinal))
                        {
                            return transform;
                        }
                        continue;
                    }
                    match = transform;
                }
            }
            return match;
        }

        private static string RelativePath(Transform root, Transform target)
        {
            var segments = new Stack<string>();
            var current = target;
            while (current != null && current != root)
            {
                segments.Push(current.name);
                current = current.parent;
            }
            return string.Join("/", segments);
        }

        private static VRCAvatarDescriptor ResolveAvatarDescriptor(string avatarPath)
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
            return (value ?? "").Replace("\\", "/").Trim().Trim('/');
        }

        private static string NormalizeAssetDir(string value)
        {
            var normalized = NormalizePath(value);
            return string.IsNullOrWhiteSpace(normalized) ? DefaultAssetDir : normalized;
        }

        private static string Sanitize(string value, string fallback)
        {
            var cleaned = new string((value ?? "").Select(c => char.IsLetterOrDigit(c) || c == '_' ? c : '_').ToArray()).Trim('_');
            return string.IsNullOrWhiteSpace(cleaned) ? fallback : cleaned;
        }

        private static string NormalizeAction(string value)
        {
            var normalized = (value ?? "").Trim().ToLowerInvariant().Replace("-", "_");
            if (normalized == "remove" || normalized == "delete_outfit")
            {
                return "remove_outfit";
            }
            if (normalized == "rename")
            {
                return "rename_outfit";
            }
            if (normalized == "reorder" || normalized == "sort")
            {
                return "reorder_outfits";
            }
            if (normalized == "default")
            {
                return "set_default";
            }
            if (normalized == "remove_wardrobe")
            {
                return "delete_wardrobe";
            }
            return normalized;
        }

        private static string FirstNonEmpty(params string[] values)
        {
            foreach (var value in values)
            {
                if (!string.IsNullOrWhiteSpace(value))
                {
                    return value.Trim();
                }
            }
            return "";
        }

        private static List<int> ReadIntArray(JObject @params, params string[] keys)
        {
            var result = new List<int>();
            foreach (var key in keys)
            {
                if (@params[key] is JArray array)
                {
                    foreach (var item in array)
                    {
                        if (item != null)
                        {
                            result.Add(item.Value<int>());
                        }
                    }
                }
                else if (@params[key] != null)
                {
                    var raw = @params[key].ToString();
                    foreach (var part in raw.Split(new[] { ',', ';', ' ' }, StringSplitOptions.RemoveEmptyEntries))
                    {
                        if (int.TryParse(part.Trim(), out var parsed))
                        {
                            result.Add(parsed);
                        }
                    }
                }
            }
            return result.Distinct().ToList();
        }

        private class WardrobeContext
        {
            public VRCAvatarDescriptor descriptor;
            public string parameterName;
            public VRCExpressionParameters parametersAsset;
            public VRCExpressionParameters.Parameter parameter;
            public VRCExpressionsMenu rootMenu;
            public AnimatorController fxController;
            public readonly List<MenuControlRef> menuControls = new List<MenuControlRef>();
            public readonly List<StateRef> states = new List<StateRef>();
            public readonly List<TransitionRef> transitions = new List<TransitionRef>();
            public readonly List<AnimatorControllerLayer> layersWithWardrobeEquals = new List<AnimatorControllerLayer>();

            public IEnumerable<int> AllValues()
            {
                return menuControls.Select(item => item.value)
                    .Concat(transitions.Select(item => item.value))
                    .Distinct()
                    .OrderBy(item => item);
            }
        }

        private class MenuControlRef
        {
            public VRCExpressionsMenu menu;
            public VRCExpressionsMenu.Control control;
            public int index;
            public int value;
            public string menuPath;
            public int depth;
        }

        private class StateRef
        {
            public AnimatorStateMachine machine;
            public AnimatorState state;
        }

        private class TransitionRef
        {
            public int layerIndex;
            public string layerName;
            public AnimatorStateMachine machine;
            public AnimatorStateTransition transition;
            public AnimatorState state;
            public int value;
        }
    }
}
