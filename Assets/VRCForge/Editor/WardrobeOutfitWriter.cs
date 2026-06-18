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
    // Adds one outfit to an EXISTING VRChat "int-exclusive wardrobe", the write
    // counterpart of WardrobeScanner. It reconciles the same native triangle the
    // scanner reads (Int parameter + menu toggle values + FX Any-State Equals N),
    // then performs an ADD-ONLY edit:
    //   - assigns the next free int value N,
    //   - sets the new outfit objects scene-default OFF,
    //   - authors one clip that turns the new objects ON and "strips" the other
    //     outfits' objects OFF (default off-set = union of sibling on-objects),
    //   - adds an FX state (Write Defaults matched to the wardrobe convention) to
    //     the wardrobe layer with an Any State -> state transition gated Equals N,
    //   - adds a menu Toggle control bound to the int with value N (overflowing to
    //     a nested SubMenu under the wardrobe menu when the home menu is full).
    // It NEVER rewrites existing clips/states (per-clip author choices are
    // intentional). Exclusivity for previously-authored outfits is preserved by the
    // wardrobe's Write Defaults + scene-default-off convention. Supports preview.
    [McpForUnityTool(
        name: "vrc_add_wardrobe_outfit",
        Description = "Add one outfit to an existing int-exclusive wardrobe: assign next int value, set new objects scene-default off, author a clip (own objects on, sibling objects off), add an FX state with Any-State Equals N (Write Defaults matched), and a menu toggle (nested SubMenu overflow). Add-only, never rewrites existing clips. Supports preview."
    )]
    public static class WardrobeOutfitWriter
    {
        public const string ToolName = "vrc_add_wardrobe_outfit";
        private const string DefaultClipDir = "Assets/VRCForge/Generated/Wardrobe";

        public static object HandleCommand(JObject @params)
        {
            try
            {
                @params = @params ?? new JObject();
                var avatarPath = (@params["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var parameterName = (@params["parameterName"]?.ToString() ?? string.Empty).Trim();
                var outfitName = (@params["outfitName"]?.ToString() ?? @params["displayName"]?.ToString() ?? string.Empty).Trim();
                var preview = @params["preview"] != null && @params["preview"].ToObject<bool>();
                var addMenuToggle = @params["addMenuToggle"] == null || @params["addMenuToggle"].ToObject<bool>();
                var setObjectsDefaultOff = @params["setObjectsDefaultOff"] == null || @params["setObjectsDefaultOff"].ToObject<bool>();
                var subMenuOverflow = @params["subMenuOverflow"] == null || @params["subMenuOverflow"].ToObject<bool>();
                var subMenuName = (@params["subMenuName"]?.ToString() ?? "Wardrobe").Trim();
                var clipDir = NormalizeAssetDir((@params["clipOutputDir"]?.ToString() ?? DefaultClipDir).Trim());

                if (string.IsNullOrWhiteSpace(parameterName))
                {
                    return new ErrorResponse("Missing required parameter: parameterName (the existing int wardrobe parameter to add to).");
                }
                if (string.IsNullOrWhiteSpace(outfitName))
                {
                    return new ErrorResponse("Missing required parameter: outfitName (display name for the new outfit).");
                }

                var objectInputs = ReadStringArray(@params, "objectPaths", "onObjectPaths");
                if (objectInputs.Count == 0)
                {
                    return new ErrorResponse("Missing required parameter: objectPaths (the new outfit's scene objects to turn on).");
                }
                var explicitOffInputs = ReadStringArray(@params, "offObjectPaths");

                var descriptor = ResolveAvatarDescriptor(avatarPath);
                var avatarRoot = descriptor.transform;
                var avatarRootPath = GetTransformPath(avatarRoot);

                // 1. Validate the parameter is an existing Int expression parameter.
                var parametersAsset = descriptor.expressionParameters;
                if (parametersAsset == null || parametersAsset.parameters == null)
                {
                    return new ErrorResponse("Avatar has no VRCExpressionParameters; cannot resolve the wardrobe int parameter.");
                }
                var intParam = parametersAsset.parameters.FirstOrDefault(p =>
                    p != null && p.name == parameterName && p.valueType == VRCExpressionParameters.ValueType.Int);
                if (intParam == null)
                {
                    return new ErrorResponse(
                        $"Parameter '{parameterName}' is not an existing Int expression parameter. " +
                        "This tool adds to an EXISTING int-exclusive wardrobe; build the wardrobe first.");
                }

                // 2. Locate the wardrobe FX layer (Any State -> state Equals <param>).
                var fxController = GetFxController(descriptor);
                if (fxController == null)
                {
                    return new ErrorResponse("No FX AnimatorController found on the avatar.");
                }
                var fxControllerPath = AssetDatabase.GetAssetPath(fxController);
                var wardrobeLayerIndex = FindWardrobeLayerIndex(fxController, parameterName);
                if (wardrobeLayerIndex < 0)
                {
                    return new ErrorResponse(
                        $"No FX layer has an Any-State 'Equals' transition on '{parameterName}'. " +
                        "This tool adds to an EXISTING int-exclusive wardrobe layer; none was found for this parameter.");
                }
                var wardrobeLayer = fxController.layers[wardrobeLayerIndex];
                var wardrobeMachine = wardrobeLayer.stateMachine;

                // 3. Reconcile existing outfits: values, sibling on-objects, WD convention.
                var existingEquals = new List<EqualsInfo>();
                CollectAnyStateEquals(wardrobeMachine, parameterName, existingEquals);
                var stateByName = new Dictionary<string, AnimatorState>();
                CollectStatesByName(wardrobeMachine, stateByName);

                var existingValues = new SortedSet<int>();
                var siblingOnObjects = new HashSet<string>(StringComparer.Ordinal);
                var wdFlags = new List<bool>();
                foreach (var eq in existingEquals)
                {
                    existingValues.Add(eq.value);
                    if (stateByName.TryGetValue(eq.stateName, out var st) && st != null)
                    {
                        wdFlags.Add(st.writeDefaultValues);
                        var existingClip = st.motion as AnimationClip;
                        foreach (var on in ReadClipOnObjects(existingClip))
                        {
                            siblingOnObjects.Add(on);
                        }
                    }
                }

                var menuToggles = new List<MenuToggleRef>();
                CollectMenuToggles(descriptor.expressionsMenu, null, "", parameterName, menuToggles, new HashSet<int>(), 0);
                foreach (var mt in menuToggles)
                {
                    existingValues.Add(mt.value);
                }

                var writeDefaultsConsistent = wdFlags.Count == 0 || wdFlags.All(f => f == wdFlags[0]);
                var wardrobeWriteDefaults = wdFlags.Count > 0 ? wdFlags[0] : true;
                bool newWriteDefaults = @params["writeDefaults"] != null
                    ? @params["writeDefaults"].ToObject<bool>()
                    : wardrobeWriteDefaults;

                // 4. Assign the new int value.
                int newValue;
                if (@params["value"] != null)
                {
                    newValue = @params["value"].ToObject<int>();
                    if (existingValues.Contains(newValue))
                    {
                        return new ErrorResponse($"Requested value {newValue} already exists in wardrobe '{parameterName}'.");
                    }
                }
                else
                {
                    newValue = (existingValues.Count > 0 ? existingValues.Max() : 0) + 1;
                }

                // 5. Resolve the new outfit objects to avatar-root-relative binding paths.
                var onObjects = new List<string>();
                var resolvedTargets = new List<Transform>();
                var unresolved = new List<string>();
                foreach (var input in objectInputs)
                {
                    var t = ResolveUnderRoot(avatarRoot, input);
                    if (t == null)
                    {
                        unresolved.Add(input);
                        continue;
                    }
                    var rel = RelativePath(avatarRoot, t);
                    if (!onObjects.Contains(rel))
                    {
                        onObjects.Add(rel);
                        resolvedTargets.Add(t);
                    }
                }
                if (unresolved.Count > 0)
                {
                    return new ErrorResponse(
                        "Could not resolve these object path(s) under the avatar '" + descriptor.name + "': " +
                        string.Join(", ", unresolved) + ". Use a path relative to the avatar root or a unique object name.");
                }

                // 6. Compute the off-set (strip the other outfits' clothes). Default =
                //    union of sibling on-objects, minus the new outfit's own objects.
                List<string> offObjects;
                if (explicitOffInputs.Count > 0)
                {
                    offObjects = new List<string>();
                    var offUnresolved = new List<string>();
                    foreach (var input in explicitOffInputs)
                    {
                        var t = ResolveUnderRoot(avatarRoot, input);
                        if (t == null) { offUnresolved.Add(input); continue; }
                        var rel = RelativePath(avatarRoot, t);
                        if (!offObjects.Contains(rel) && !onObjects.Contains(rel)) offObjects.Add(rel);
                    }
                    if (offUnresolved.Count > 0)
                    {
                        return new ErrorResponse(
                            "Could not resolve these offObjectPaths under the avatar: " + string.Join(", ", offUnresolved) + ".");
                    }
                }
                else
                {
                    offObjects = siblingOnObjects.Where(p => !onObjects.Contains(p)).OrderBy(p => p, StringComparer.Ordinal).ToList();
                }

                var stateName = MakeUniqueStateName(stateByName, Sanitize(outfitName, "Outfit"));
                var clipFileName = $"{Sanitize(descriptor.name, "Avatar")}_{Sanitize(parameterName, "Wardrobe")}_{stateName}.anim";
                var clipPath = $"{clipDir}/{clipFileName}";

                // Plan menu placement (read-only resolution).
                var menuPlan = PlanMenuPlacement(descriptor.expressionsMenu, parameterName, menuToggles, subMenuOverflow, subMenuName);

                var warnings = new List<string>();
                if (!writeDefaultsConsistent)
                {
                    warnings.Add("Existing wardrobe states have inconsistent Write Defaults; new state uses " + newWriteDefaults + ". Verify exclusivity in-editor.");
                }
                if (!newWriteDefaults && offObjects.Count == 0 && siblingOnObjects.Count > 0)
                {
                    warnings.Add("Write Defaults is OFF and no off-objects were computed; selecting another outfit may not hide this one. Consider Write Defaults on or explicit offObjectPaths.");
                }
                if (addMenuToggle && menuPlan.menu == null)
                {
                    warnings.Add("Menu is full and SubMenu overflow is disabled; no menu toggle will be added (FX/parameter still wired).");
                }

                var plan = new
                {
                    action = "add_wardrobe_outfit",
                    avatarPath = avatarRootPath,
                    avatarName = descriptor.name,
                    parameterName,
                    outfitName,
                    value = newValue,
                    fxControllerPath,
                    fxLayerName = wardrobeLayer.name,
                    fxStateName = stateName,
                    clipPath,
                    writeDefaults = newWriteDefaults,
                    wardrobeWriteDefaults,
                    writeDefaultsConsistent,
                    onObjects,
                    offObjects,
                    setObjectsDefaultOff,
                    addMenuToggle = addMenuToggle && menuPlan.menu != null,
                    menuPath = menuPlan.menuPathDisplay,
                    menuOverflowToSubMenu = menuPlan.createsSubMenu,
                    existingOutfitCount = existingValues.Count,
                    warnings
                };

                if (preview)
                {
                    return new SuccessResponse(
                        $"Preview: would add outfit '{outfitName}' as value {newValue} to wardrobe '{parameterName}' on '{descriptor.name}'.",
                        new
                        {
                            ok = true,
                            preview = true,
                            plan
                        });
                }

                // ---- APPLY -------------------------------------------------------
                Directory.CreateDirectory(clipDir);
                var undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName($"Add wardrobe outfit '{outfitName}'");

                // a. New objects scene-default OFF.
                if (setObjectsDefaultOff)
                {
                    foreach (var t in resolvedTargets)
                    {
                        var go = t.gameObject;
                        Undo.RecordObject(go, "Wardrobe outfit default off");
                        if (go.activeSelf)
                        {
                            go.SetActive(false);
                        }
                        EditorUtility.SetDirty(go);
                    }
                }

                // b. Author the toggle clip.
                var clip = new AnimationClip { name = Path.GetFileNameWithoutExtension(clipFileName) };
                foreach (var path in onObjects)
                {
                    AnimationUtility.SetEditorCurve(
                        clip,
                        new EditorCurveBinding { path = path, type = typeof(GameObject), propertyName = "m_IsActive" },
                        AnimationCurve.Constant(0f, 0f, 1f));
                }
                foreach (var path in offObjects)
                {
                    AnimationUtility.SetEditorCurve(
                        clip,
                        new EditorCurveBinding { path = path, type = typeof(GameObject), propertyName = "m_IsActive" },
                        AnimationCurve.Constant(0f, 0f, 0f));
                }
                AssetDatabase.CreateAsset(clip, AssetDatabase.GenerateUniqueAssetPath(clipPath));
                var createdClipPath = AssetDatabase.GetAssetPath(clip);

                // c. Add the FX state + Any State -> state Equals N.
                Undo.RegisterCompleteObjectUndo(fxController, "Add wardrobe FX state");
                Undo.RegisterCompleteObjectUndo(wardrobeMachine, "Add wardrobe FX state");
                var newState = wardrobeMachine.AddState(stateName);
                newState.motion = clip;
                newState.writeDefaultValues = newWriteDefaults;
                var transition = wardrobeMachine.AddAnyStateTransition(newState);
                transition.hasExitTime = false;
                transition.exitTime = 0f;
                transition.duration = 0f;
                transition.canTransitionToSelf = false;
                transition.AddCondition(AnimatorConditionMode.Equals, newValue, parameterName);
                EditorUtility.SetDirty(fxController);
                EditorUtility.SetDirty(wardrobeMachine);
                EditorUtility.SetDirty(newState);

                // d. Add the menu toggle (overflow into a nested SubMenu when needed).
                string appliedMenuPath = "";
                bool menuToggleAdded = false;
                if (addMenuToggle)
                {
                    var target = ResolveOrCreateMenuTarget(
                        descriptor.expressionsMenu, parameterName, menuToggles, subMenuOverflow, subMenuName, clipDir, out appliedMenuPath);
                    if (target != null)
                    {
                        Undo.RegisterCompleteObjectUndo(target, "Add wardrobe menu toggle");
                        if (target.controls == null)
                        {
                            target.controls = new List<VRCExpressionsMenu.Control>();
                        }
                        target.controls.Add(new VRCExpressionsMenu.Control
                        {
                            name = outfitName,
                            type = VRCExpressionsMenu.Control.ControlType.Toggle,
                            parameter = new VRCExpressionsMenu.Control.Parameter { name = parameterName },
                            value = newValue
                        });
                        EditorUtility.SetDirty(target);
                        menuToggleAdded = true;
                    }
                }

                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();
                Undo.CollapseUndoOperations(undoGroup);

                return new SuccessResponse(
                    $"Added outfit '{outfitName}' as value {newValue} to wardrobe '{parameterName}' on '{descriptor.name}'.",
                    new
                    {
                        ok = true,
                        preview = false,
                        action = "add_wardrobe_outfit",
                        avatarPath = avatarRootPath,
                        avatarName = descriptor.name,
                        parameterName,
                        outfitName,
                        assignedValue = newValue,
                        fxControllerPath,
                        fxLayerName = wardrobeLayer.name,
                        fxStateName = stateName,
                        clipPath = createdClipPath,
                        writeDefaults = newWriteDefaults,
                        onObjects,
                        offObjects,
                        setObjectsDefaultOff,
                        menuToggleAdded,
                        menuPath = appliedMenuPath,
                        warnings
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Add wardrobe outfit failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        // --- FX reconciliation -------------------------------------------------------

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

        private static int FindWardrobeLayerIndex(AnimatorController controller, string parameterName)
        {
            var layers = controller.layers;
            for (var i = 0; i < layers.Length; i++)
            {
                var layer = layers[i];
                if (layer == null || layer.stateMachine == null)
                {
                    continue;
                }
                if (LayerHasEquals(layer.stateMachine, parameterName))
                {
                    return i;
                }
            }
            return -1;
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
                foreach (var condition in transition.conditions)
                {
                    if (condition.mode == AnimatorConditionMode.Equals
                        && string.Equals(condition.parameter, parameterName, StringComparison.Ordinal))
                    {
                        return true;
                    }
                }
            }
            foreach (var sub in machine.stateMachines)
            {
                if (LayerHasEquals(sub.stateMachine, parameterName))
                {
                    return true;
                }
            }
            return false;
        }

        private static void CollectAnyStateEquals(AnimatorStateMachine machine, string parameterName, List<EqualsInfo> sink)
        {
            if (machine == null)
            {
                return;
            }
            foreach (var transition in machine.anyStateTransitions ?? Array.Empty<AnimatorStateTransition>())
            {
                if (transition?.destinationState == null || transition.conditions == null)
                {
                    continue;
                }
                foreach (var condition in transition.conditions)
                {
                    if (condition.mode == AnimatorConditionMode.Equals
                        && string.Equals(condition.parameter, parameterName, StringComparison.Ordinal))
                    {
                        sink.Add(new EqualsInfo { stateName = transition.destinationState.name ?? "", value = Mathf.RoundToInt(condition.threshold) });
                    }
                }
            }
            foreach (var sub in machine.stateMachines)
            {
                CollectAnyStateEquals(sub.stateMachine, parameterName, sink);
            }
        }

        private static void CollectStatesByName(AnimatorStateMachine machine, Dictionary<string, AnimatorState> sink)
        {
            if (machine == null)
            {
                return;
            }
            foreach (var child in machine.states)
            {
                if (child.state != null && !sink.ContainsKey(child.state.name ?? ""))
                {
                    sink[child.state.name ?? ""] = child.state;
                }
            }
            foreach (var sub in machine.stateMachines)
            {
                CollectStatesByName(sub.stateMachine, sink);
            }
        }

        private static IEnumerable<string> ReadClipOnObjects(AnimationClip clip)
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
                if (curve.keys[curve.length - 1].value >= 0.5f)
                {
                    yield return binding.path;
                }
            }
        }

        // --- Menu reconciliation / placement -----------------------------------------

        private static void CollectMenuToggles(
            VRCExpressionsMenu menu,
            VRCExpressionsMenu parent,
            string parentPath,
            string parameterName,
            List<MenuToggleRef> sink,
            HashSet<int> visited,
            int depth)
        {
            if (menu == null || depth > 8 || menu.controls == null)
            {
                return;
            }
            if (!visited.Add(menu.GetInstanceID()))
            {
                return;
            }
            foreach (var control in menu.controls)
            {
                if (control == null)
                {
                    continue;
                }
                var name = control.name ?? "";
                var path = string.IsNullOrWhiteSpace(parentPath) ? name : $"{parentPath}/{name}";
                if (control.parameter != null && string.Equals(control.parameter.name, parameterName, StringComparison.Ordinal))
                {
                    sink.Add(new MenuToggleRef { menu = menu, value = Mathf.RoundToInt(control.value), menuPath = path, depth = depth });
                }
                if (control.type == VRCExpressionsMenu.Control.ControlType.SubMenu && control.subMenu != null)
                {
                    CollectMenuToggles(control.subMenu, menu, path, parameterName, sink, visited, depth + 1);
                }
            }
        }

        private static MenuPlan PlanMenuPlacement(
            VRCExpressionsMenu rootMenu,
            string parameterName,
            List<MenuToggleRef> menuToggles,
            bool subMenuOverflow,
            string subMenuName)
        {
            if (rootMenu == null)
            {
                return new MenuPlan { menu = null, menuPathDisplay = "", createsSubMenu = false };
            }
            var existingWithRoom = FindBestMenuRef(menuToggles, true);
            if (existingWithRoom != null)
            {
                return new MenuPlan { menu = existingWithRoom.menu, menuPathDisplay = "(existing wardrobe menu)", createsSubMenu = false };
            }
            var existingHome = FindBestMenuRef(menuToggles, false);
            if (existingHome != null)
            {
                return subMenuOverflow
                    ? new MenuPlan { menu = existingHome.menu, menuPathDisplay = subMenuName + " (nested SubMenu)", createsSubMenu = true }
                    : new MenuPlan { menu = null, menuPathDisplay = "", createsSubMenu = false };
            }
            if ((rootMenu.controls?.Count ?? 0) < VRCExpressionsMenu.MAX_CONTROLS)
            {
                return new MenuPlan { menu = rootMenu, menuPathDisplay = "(root menu)", createsSubMenu = false };
            }
            if (subMenuOverflow)
            {
                return new MenuPlan { menu = rootMenu, menuPathDisplay = subMenuName + " (new SubMenu)", createsSubMenu = true };
            }
            return new MenuPlan { menu = null, menuPathDisplay = "", createsSubMenu = false };
        }

        private static VRCExpressionsMenu ResolveOrCreateMenuTarget(
            VRCExpressionsMenu rootMenu,
            string parameterName,
            List<MenuToggleRef> menuToggles,
            bool subMenuOverflow,
            string subMenuName,
            string assetDir,
            out string appliedMenuPath)
        {
            appliedMenuPath = "";
            if (rootMenu == null)
            {
                return null;
            }
            var existingWithRoom = FindBestMenuRef(menuToggles, true);
            if (existingWithRoom != null)
            {
                appliedMenuPath = "(existing wardrobe menu)";
                return existingWithRoom.menu;
            }
            var existingHome = FindBestMenuRef(menuToggles, false);
            if (existingHome != null)
            {
                if (!subMenuOverflow)
                {
                    return null;
                }
                return CreateOverflowSubMenu(existingHome.menu, parameterName, subMenuName, assetDir, true, out appliedMenuPath);
            }
            if ((rootMenu.controls?.Count ?? 0) < VRCExpressionsMenu.MAX_CONTROLS)
            {
                appliedMenuPath = "(root menu)";
                return rootMenu;
            }
            if (!subMenuOverflow)
            {
                return null;
            }

            return CreateOverflowSubMenu(rootMenu, parameterName, subMenuName, assetDir, true, out appliedMenuPath);
        }

        private static MenuToggleRef FindBestMenuRef(List<MenuToggleRef> menuToggles, bool requireCapacity)
        {
            MenuToggleRef best = null;
            foreach (var toggle in menuToggles)
            {
                if (toggle?.menu == null)
                {
                    continue;
                }
                if (requireCapacity && (toggle.menu.controls?.Count ?? 0) >= VRCExpressionsMenu.MAX_CONTROLS)
                {
                    continue;
                }
                if (best == null || toggle.depth >= best.depth)
                {
                    best = toggle;
                }
            }
            return best;
        }

        private static VRCExpressionsMenu CreateOverflowSubMenu(
            VRCExpressionsMenu owner,
            string parameterName,
            string subMenuName,
            string assetDir,
            bool splitFullOwner,
            out string appliedMenuPath)
        {
            appliedMenuPath = "";
            if (owner == null)
            {
                return null;
            }

            Directory.CreateDirectory(assetDir);
            var subMenu = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
            subMenu.controls = new List<VRCExpressionsMenu.Control>();
            var subPath = AssetDatabase.GenerateUniqueAssetPath($"{assetDir}/{Sanitize(subMenuName, "Wardrobe")}_SubMenu.asset");
            AssetDatabase.CreateAsset(subMenu, subPath);

            Undo.RegisterCompleteObjectUndo(owner, "Add wardrobe submenu");
            if (owner.controls == null)
            {
                owner.controls = new List<VRCExpressionsMenu.Control>();
            }
            if (splitFullOwner)
            {
                while (owner.controls.Count >= VRCExpressionsMenu.MAX_CONTROLS && owner.controls.Count > 0)
                {
                    var moveIndex = FindLastControlIndex(owner.controls, parameterName);
                    var moved = owner.controls[moveIndex];
                    owner.controls.RemoveAt(moveIndex);
                    subMenu.controls.Insert(0, moved);
                }
            }
            owner.controls.Add(new VRCExpressionsMenu.Control
            {
                name = subMenuName,
                type = VRCExpressionsMenu.Control.ControlType.SubMenu,
                subMenu = subMenu
            });
            EditorUtility.SetDirty(owner);
            EditorUtility.SetDirty(subMenu);
            appliedMenuPath = subMenuName;
            return subMenu;
        }

        private static int FindLastControlIndex(List<VRCExpressionsMenu.Control> controls, string parameterName)
        {
            for (var i = controls.Count - 1; i >= 0; i--)
            {
                var control = controls[i];
                if (control?.parameter != null && string.Equals(control.parameter.name, parameterName, StringComparison.Ordinal))
                {
                    return i;
                }
            }
            return controls.Count - 1;
        }

        // --- Path / naming helpers ---------------------------------------------------

        private static Transform ResolveUnderRoot(Transform root, string rawPath)
        {
            var path = NormalizePath(rawPath);
            if (string.IsNullOrEmpty(path))
            {
                return null;
            }

            // Direct child path relative to the avatar root.
            var direct = root.Find(path);
            if (direct != null)
            {
                return direct;
            }

            // Full hierarchy path that starts with the avatar root name.
            var rootName = root.name;
            if (path.Equals(rootName, StringComparison.Ordinal))
            {
                return root;
            }
            if (path.StartsWith(rootName + "/", StringComparison.Ordinal))
            {
                var sub = path.Substring(rootName.Length + 1);
                var byFull = root.Find(sub);
                if (byFull != null)
                {
                    return byFull;
                }
            }

            // Fallback: unique descendant by leaf name or by suffix match.
            var leaf = path.Contains("/") ? path.Substring(path.LastIndexOf('/') + 1) : path;
            Transform match = null;
            foreach (var t in root.GetComponentsInChildren<Transform>(true))
            {
                if (t == root)
                {
                    continue;
                }
                var rel = RelativePath(root, t);
                if (rel.Equals(path, StringComparison.Ordinal) || rel.EndsWith("/" + path, StringComparison.Ordinal) || t.name.Equals(leaf, StringComparison.Ordinal))
                {
                    if (match != null && !match.Equals(t))
                    {
                        // Ambiguous by leaf name; only accept exact relative matches beyond this point.
                        if (rel.Equals(path, StringComparison.Ordinal))
                        {
                            return t;
                        }
                        continue;
                    }
                    match = t;
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

        private static string MakeUniqueStateName(Dictionary<string, AnimatorState> existing, string baseName)
        {
            if (!existing.ContainsKey(baseName))
            {
                return baseName;
            }
            for (var i = 2; i < 1000; i++)
            {
                var candidate = baseName + "_" + i;
                if (!existing.ContainsKey(candidate))
                {
                    return candidate;
                }
            }
            return baseName + "_" + Guid.NewGuid().ToString("N").Substring(0, 6);
        }

        private static List<string> ReadStringArray(JObject @params, params string[] keys)
        {
            var result = new List<string>();
            foreach (var key in keys)
            {
                if (@params[key] is JArray array)
                {
                    foreach (var item in array)
                    {
                        var value = (item?.ToString() ?? string.Empty).Trim();
                        if (!string.IsNullOrWhiteSpace(value) && !result.Contains(value))
                        {
                            result.Add(value);
                        }
                    }
                }
                else if (@params[key] != null && @params[key].Type == JTokenType.String)
                {
                    var value = @params[key].ToString().Trim();
                    if (!string.IsNullOrWhiteSpace(value) && !result.Contains(value))
                    {
                        result.Add(value);
                    }
                }
            }
            return result;
        }

        private static string Sanitize(string value, string fallback)
        {
            var cleaned = new string((value ?? string.Empty).Select(c => char.IsLetterOrDigit(c) || c == '_' ? c : '_').ToArray()).Trim('_');
            return string.IsNullOrWhiteSpace(cleaned) ? fallback : cleaned;
        }

        private static string NormalizeAssetDir(string dir)
        {
            var normalized = NormalizePath(dir);
            return string.IsNullOrEmpty(normalized) ? DefaultClipDir : normalized;
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
            return (value ?? string.Empty).Replace("\\", "/").Trim().Trim('/');
        }

        private class EqualsInfo
        {
            public string stateName;
            public int value;
        }

        private class MenuToggleRef
        {
            public VRCExpressionsMenu menu;
            public int value;
            public string menuPath;
            public int depth;
        }

        private class MenuPlan
        {
            public VRCExpressionsMenu menu;
            public string menuPathDisplay;
            public bool createsSubMenu;
        }
    }
}
