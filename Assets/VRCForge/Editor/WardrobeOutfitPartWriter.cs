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
    // Adds one INT-GATED part toggle to an outfit that already lives in an
    // int-exclusive wardrobe (the "hat on a costume" case). The part is skinned
    // to the shared armature, so it cannot be a child of the outfit object and
    // therefore cannot ride the wardrobe's parent on/off cascade. Instead it gets:
    //   - its own Bool expression parameter (created if missing),
    //   - a dedicated FX layer with Off (default) + On states, where:
    //       Off -> On  requires  (wardrobe int Equals N) AND (part bool == true)
    //       On  -> Off  fires on  (part bool == false)  OR  (wardrobe int != N)
    //   - a menu Toggle bound to the part bool.
    // Net behavior: the part only shows when outfit N is worn AND its toggle is on;
    // when outfit N is not worn the toggle is inert (the int!=N transition forces
    // the part back off). Add-only: never rewrites the wardrobe's own states/clips.
    // Supports preview.
    [McpForUnityTool(
        name: "vrc_add_outfit_part",
        Description = "Add an int-gated part toggle (e.g. a hat) to one outfit value of an existing int-exclusive wardrobe. Creates a Bool parameter, a dedicated FX layer (Off default; Off->On = int Equals N AND bool true; On->Off = bool false OR int != N), authors on/off clips, sets the part scene-default off, and adds a menu toggle. Add-only. Supports preview."
    )]
    public static class WardrobeOutfitPartWriter
    {
        public const string ToolName = "vrc_add_outfit_part";
        private const string DefaultClipDir = "Assets/VRCForge/Generated/Wardrobe";

        public static object HandleCommand(JObject @params)
        {
            try
            {
                @params = @params ?? new JObject();
                var avatarPath = (@params["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var parameterName = (@params["parameterName"]?.ToString() ?? string.Empty).Trim();
                var partName = (@params["partName"]?.ToString() ?? @params["displayName"]?.ToString() ?? string.Empty).Trim();
                var partParameterName = (@params["partParameterName"]?.ToString() ?? @params["boolParameterName"]?.ToString() ?? string.Empty).Trim();
                var preview = @params["preview"] != null && @params["preview"].ToObject<bool>();
                var addMenuToggle = @params["addMenuToggle"] == null || @params["addMenuToggle"].ToObject<bool>();
                var setObjectsDefaultOff = @params["setObjectsDefaultOff"] == null || @params["setObjectsDefaultOff"].ToObject<bool>();
                var defaultOn = @params["defaultOn"] != null && @params["defaultOn"].ToObject<bool>();
                var subMenuName = (@params["subMenuName"]?.ToString() ?? string.Empty).Trim();
                var clipDir = NormalizeAssetDir((@params["clipOutputDir"]?.ToString() ?? DefaultClipDir).Trim());

                if (string.IsNullOrWhiteSpace(parameterName))
                {
                    return new ErrorResponse("Missing required parameter: parameterName (the existing int wardrobe parameter the part is gated on).");
                }
                if (string.IsNullOrWhiteSpace(partName))
                {
                    return new ErrorResponse("Missing required parameter: partName (display name for the new part toggle).");
                }
                if (@params["value"] == null && @params["outfitValue"] == null)
                {
                    return new ErrorResponse("Missing required parameter: value (the wardrobe int value N this part belongs to).");
                }
                var outfitValue = (@params["value"] ?? @params["outfitValue"]).ToObject<int>();

                if (string.IsNullOrWhiteSpace(partParameterName))
                {
                    partParameterName = Sanitize(partName, "Part");
                }

                var objectInputs = ReadStringArray(@params, "objectPaths", "onObjectPaths");
                if (objectInputs.Count == 0)
                {
                    return new ErrorResponse("Missing required parameter: objectPaths (the part's scene objects to toggle on/off).");
                }

                var descriptor = ResolveAvatarDescriptor(avatarPath);
                var avatarRoot = descriptor.transform;
                var avatarRootPath = GetTransformPath(avatarRoot);

                // 1. Validate the wardrobe int parameter exists.
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
                        "Build the wardrobe (and its outfit value) first, then add parts to it.");
                }

                // 2. Locate the wardrobe FX layer and reconcile existing outfit values.
                var fxController = GetFxController(descriptor);
                if (fxController == null)
                {
                    return new ErrorResponse("No FX AnimatorController found on the avatar.");
                }
                var fxControllerPath = AssetDatabase.GetAssetPath(fxController);
                var wardrobeLayerIndex = FindWardrobeLayerIndex(fxController, parameterName);

                var existingValues = new SortedSet<int>();
                bool wardrobeWriteDefaults = true;
                if (wardrobeLayerIndex >= 0)
                {
                    var wardrobeMachine = fxController.layers[wardrobeLayerIndex].stateMachine;
                    var equals = new List<EqualsInfo>();
                    CollectAnyStateEquals(wardrobeMachine, parameterName, equals);
                    var stateByName = new Dictionary<string, AnimatorState>();
                    CollectStatesByName(wardrobeMachine, stateByName);
                    var wdFlags = new List<bool>();
                    foreach (var eq in equals)
                    {
                        existingValues.Add(eq.value);
                        if (stateByName.TryGetValue(eq.stateName, out var st) && st != null)
                        {
                            wdFlags.Add(st.writeDefaultValues);
                        }
                    }
                    if (wdFlags.Count > 0)
                    {
                        wardrobeWriteDefaults = wdFlags[0];
                    }
                }
                var newWriteDefaults = @params["writeDefaults"] != null
                    ? @params["writeDefaults"].ToObject<bool>()
                    : wardrobeWriteDefaults;

                // 3. Resolve the part objects to avatar-root-relative binding paths.
                var onObjects = new List<string>();
                var resolvedTargets = new List<Transform>();
                var unresolved = new List<string>();
                foreach (var input in objectInputs)
                {
                    var t = ResolveUnderRoot(avatarRoot, input);
                    if (t == null) { unresolved.Add(input); continue; }
                    var rel = RelativePath(avatarRoot, t);
                    if (!onObjects.Contains(rel)) { onObjects.Add(rel); resolvedTargets.Add(t); }
                }
                if (unresolved.Count > 0)
                {
                    return new ErrorResponse(
                        "Could not resolve these part object path(s) under the avatar '" + descriptor.name + "': " +
                        string.Join(", ", unresolved) + ". Use a path relative to the avatar root or a unique object name.");
                }

                // 4. Reconcile the part Bool expression parameter.
                var boolParamExists = parametersAsset.parameters.Any(p =>
                    p != null && p.name == partParameterName);
                var boolParamIsBool = parametersAsset.parameters.Any(p =>
                    p != null && p.name == partParameterName && p.valueType == VRCExpressionParameters.ValueType.Bool);
                if (boolParamExists && !boolParamIsBool)
                {
                    return new ErrorResponse(
                        $"Parameter '{partParameterName}' already exists but is not a Bool parameter. " +
                        "Pass a different partParameterName for this part toggle.");
                }

                var warnings = new List<string>();
                if (existingValues.Count > 0 && !existingValues.Contains(outfitValue))
                {
                    warnings.Add($"Outfit value {outfitValue} was not found among the wardrobe's existing values "
                        + $"[{string.Join(", ", existingValues)}]; the part will stay hidden until that outfit value exists.");
                }
                if (wardrobeLayerIndex < 0)
                {
                    warnings.Add($"No FX layer has an Any-State 'Equals' transition on '{parameterName}'; the part layer is still authored, "
                        + "but verify the wardrobe layer drives this int parameter.");
                }

                var layerName = MakeUniqueLayerName(fxController, $"{Sanitize(partName, "Part")} (part)");
                var onClipFile = $"{Sanitize(descriptor.name, "Avatar")}_{Sanitize(partParameterName, "Part")}_On.anim";
                var offClipFile = $"{Sanitize(descriptor.name, "Avatar")}_{Sanitize(partParameterName, "Part")}_Off.anim";

                var menuPlan = PlanPartMenu(descriptor.expressionsMenu, subMenuName);
                if (addMenuToggle && menuPlan.menu == null && !menuPlan.willCreate)
                {
                    warnings.Add("Menu is full and no SubMenu could be created; no menu toggle will be added (FX/parameter still wired).");
                }

                var plan = new
                {
                    action = "add_outfit_part",
                    avatarPath = avatarRootPath,
                    avatarName = descriptor.name,
                    parameterName,
                    outfitValue,
                    partName,
                    partParameterName,
                    boolParameterExists = boolParamExists,
                    defaultOn,
                    fxControllerPath,
                    fxLayerName = layerName,
                    onClipPath = $"{clipDir}/{onClipFile}",
                    offClipPath = $"{clipDir}/{offClipFile}",
                    writeDefaults = newWriteDefaults,
                    onObjects,
                    setObjectsDefaultOff,
                    addMenuToggle = addMenuToggle && (menuPlan.menu != null || menuPlan.willCreate),
                    menuPath = menuPlan.menuPathDisplay,
                    existingOutfitValues = existingValues.ToArray(),
                    warnings
                };

                if (preview)
                {
                    return new SuccessResponse(
                        $"Preview: would add part '{partName}' (bool '{partParameterName}') gated on '{parameterName}' == {outfitValue} on '{descriptor.name}'.",
                        new { ok = true, preview = true, plan });
                }

                // ---- APPLY -------------------------------------------------------
                Directory.CreateDirectory(clipDir);
                var undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName($"Add outfit part '{partName}'");

                // a. Ensure the Bool expression parameter.
                if (!boolParamExists)
                {
                    Undo.RegisterCompleteObjectUndo(parametersAsset, "Add part expression parameter");
                    var list = parametersAsset.parameters.ToList();
                    list.Add(new VRCExpressionParameters.Parameter
                    {
                        name = partParameterName,
                        valueType = VRCExpressionParameters.ValueType.Bool,
                        defaultValue = defaultOn ? 1f : 0f,
                        saved = true
                    });
                    parametersAsset.parameters = list.ToArray();
                    EditorUtility.SetDirty(parametersAsset);
                }

                // b. Part objects scene-default OFF (unless they should default on).
                if (setObjectsDefaultOff && !defaultOn)
                {
                    foreach (var t in resolvedTargets)
                    {
                        var go = t.gameObject;
                        Undo.RecordObject(go, "Outfit part default off");
                        if (go.activeSelf) { go.SetActive(false); }
                        EditorUtility.SetDirty(go);
                    }
                }

                // c. Author the On / Off clips for the part objects.
                var onClip = new AnimationClip { name = Path.GetFileNameWithoutExtension(onClipFile) };
                var offClip = new AnimationClip { name = Path.GetFileNameWithoutExtension(offClipFile) };
                foreach (var path in onObjects)
                {
                    var binding = new EditorCurveBinding { path = path, type = typeof(GameObject), propertyName = "m_IsActive" };
                    AnimationUtility.SetEditorCurve(onClip, binding, AnimationCurve.Constant(0f, 0f, 1f));
                    AnimationUtility.SetEditorCurve(offClip, binding, AnimationCurve.Constant(0f, 0f, 0f));
                }
                AssetDatabase.CreateAsset(onClip, AssetDatabase.GenerateUniqueAssetPath($"{clipDir}/{onClipFile}"));
                AssetDatabase.CreateAsset(offClip, AssetDatabase.GenerateUniqueAssetPath($"{clipDir}/{offClipFile}"));
                var createdOnClipPath = AssetDatabase.GetAssetPath(onClip);
                var createdOffClipPath = AssetDatabase.GetAssetPath(offClip);

                // d. Ensure controller parameters, then add the int-gated part layer.
                Undo.RegisterCompleteObjectUndo(fxController, "Add outfit part FX layer");
                EnsureControllerParameter(fxController, parameterName, AnimatorControllerParameterType.Int);
                EnsureControllerParameter(fxController, partParameterName, AnimatorControllerParameterType.Bool);

                var stateMachine = new AnimatorStateMachine
                {
                    name = layerName,
                    hideFlags = HideFlags.HideInHierarchy
                };
                if (AssetDatabase.GetAssetPath(fxController).Length > 0)
                {
                    AssetDatabase.AddObjectToAsset(stateMachine, fxController);
                }
                var offState = stateMachine.AddState("Off");
                var onState = stateMachine.AddState("On");
                stateMachine.defaultState = offState;
                offState.writeDefaultValues = newWriteDefaults;
                onState.writeDefaultValues = newWriteDefaults;
                offState.motion = offClip;
                onState.motion = onClip;

                var toOn = offState.AddTransition(onState);
                toOn.hasExitTime = false; toOn.exitTime = 0f; toOn.duration = 0f; toOn.canTransitionToSelf = false;
                toOn.AddCondition(AnimatorConditionMode.Equals, outfitValue, parameterName);
                toOn.AddCondition(AnimatorConditionMode.If, 0f, partParameterName);

                var toOffBool = onState.AddTransition(offState);
                toOffBool.hasExitTime = false; toOffBool.exitTime = 0f; toOffBool.duration = 0f; toOffBool.canTransitionToSelf = false;
                toOffBool.AddCondition(AnimatorConditionMode.IfNot, 0f, partParameterName);

                var toOffInt = onState.AddTransition(offState);
                toOffInt.hasExitTime = false; toOffInt.exitTime = 0f; toOffInt.duration = 0f; toOffInt.canTransitionToSelf = false;
                toOffInt.AddCondition(AnimatorConditionMode.NotEqual, outfitValue, parameterName);

                var layer = new AnimatorControllerLayer
                {
                    name = layerName,
                    defaultWeight = 1f,
                    stateMachine = stateMachine
                };
                fxController.AddLayer(layer);
                EditorUtility.SetDirty(fxController);
                EditorUtility.SetDirty(stateMachine);

                // e. Add the menu toggle bound to the part bool.
                string appliedMenuPath = "";
                bool menuToggleAdded = false;
                if (addMenuToggle)
                {
                    var target = ResolveOrCreatePartMenu(descriptor.expressionsMenu, subMenuName, clipDir, out appliedMenuPath);
                    if (target != null)
                    {
                        Undo.RegisterCompleteObjectUndo(target, "Add outfit part menu toggle");
                        if (target.controls == null) { target.controls = new List<VRCExpressionsMenu.Control>(); }
                        target.controls.Add(new VRCExpressionsMenu.Control
                        {
                            name = partName,
                            type = VRCExpressionsMenu.Control.ControlType.Toggle,
                            parameter = new VRCExpressionsMenu.Control.Parameter { name = partParameterName },
                            value = 1f
                        });
                        EditorUtility.SetDirty(target);
                        menuToggleAdded = true;
                    }
                }

                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();
                Undo.CollapseUndoOperations(undoGroup);

                return new SuccessResponse(
                    $"Added part '{partName}' (bool '{partParameterName}') gated on '{parameterName}' == {outfitValue} on '{descriptor.name}'.",
                    new
                    {
                        ok = true,
                        preview = false,
                        action = "add_outfit_part",
                        avatarPath = avatarRootPath,
                        avatarName = descriptor.name,
                        parameterName,
                        outfitValue,
                        partName,
                        assignedPartParameter = partParameterName,
                        boolParameterCreated = !boolParamExists,
                        defaultOn,
                        fxControllerPath,
                        fxLayerName = layerName,
                        onClipPath = createdOnClipPath,
                        offClipPath = createdOffClipPath,
                        writeDefaults = newWriteDefaults,
                        onObjects,
                        setObjectsDefaultOff = setObjectsDefaultOff && !defaultOn,
                        menuToggleAdded,
                        menuPath = appliedMenuPath,
                        warnings
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Add outfit part failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        // --- FX reconciliation -------------------------------------------------------

        private static AnimatorController GetFxController(VRCAvatarDescriptor descriptor)
        {
            if (descriptor.baseAnimationLayers == null) { return null; }
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
                if (layer?.stateMachine == null) { continue; }
                if (LayerHasEquals(layer.stateMachine, parameterName)) { return i; }
            }
            return -1;
        }

        private static bool LayerHasEquals(AnimatorStateMachine machine, string parameterName)
        {
            if (machine == null) { return false; }
            foreach (var transition in machine.anyStateTransitions ?? Array.Empty<AnimatorStateTransition>())
            {
                if (transition?.conditions == null) { continue; }
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
                if (LayerHasEquals(sub.stateMachine, parameterName)) { return true; }
            }
            return false;
        }

        private static void CollectAnyStateEquals(AnimatorStateMachine machine, string parameterName, List<EqualsInfo> sink)
        {
            if (machine == null) { return; }
            foreach (var transition in machine.anyStateTransitions ?? Array.Empty<AnimatorStateTransition>())
            {
                if (transition?.destinationState == null || transition.conditions == null) { continue; }
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
            if (machine == null) { return; }
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

        private static void EnsureControllerParameter(AnimatorController controller, string name, AnimatorControllerParameterType type)
        {
            if (controller.parameters.Any(p => p != null && p.name == name)) { return; }
            controller.AddParameter(name, type);
        }

        private static string MakeUniqueLayerName(AnimatorController controller, string baseName)
        {
            var existing = new HashSet<string>(controller.layers.Select(l => l.name ?? ""), StringComparer.Ordinal);
            if (!existing.Contains(baseName)) { return baseName; }
            for (var i = 2; i < 1000; i++)
            {
                var candidate = baseName + " " + i;
                if (!existing.Contains(candidate)) { return candidate; }
            }
            return baseName + " " + Guid.NewGuid().ToString("N").Substring(0, 6);
        }

        // --- Menu placement ----------------------------------------------------------

        private static PartMenuPlan PlanPartMenu(VRCExpressionsMenu rootMenu, string subMenuName)
        {
            if (rootMenu == null)
            {
                return new PartMenuPlan { menu = null, willCreate = false, menuPathDisplay = "" };
            }
            if (!string.IsNullOrWhiteSpace(subMenuName))
            {
                var existing = FindSubMenu(rootMenu, subMenuName);
                if (existing != null && (existing.controls?.Count ?? 0) < VRCExpressionsMenu.MAX_CONTROLS)
                {
                    return new PartMenuPlan { menu = existing, willCreate = false, menuPathDisplay = subMenuName + " (existing SubMenu)" };
                }
                var ownerName = existing != null ? subMenuName : "root";
                return new PartMenuPlan
                {
                    menu = null,
                    willCreate = true,
                    menuPathDisplay = existing != null
                        ? subMenuName + "/More (new nested SubMenu; moves one control from the full owner)"
                        : subMenuName + $" (new SubMenu under {ownerName}; moves one control if needed)"
                };
            }
            if ((rootMenu.controls?.Count ?? 0) < VRCExpressionsMenu.MAX_CONTROLS)
            {
                return new PartMenuPlan { menu = rootMenu, willCreate = false, menuPathDisplay = "(root menu)" };
            }
            var partsMenu = FindSubMenu(rootMenu, "Parts");
            if (partsMenu != null && (partsMenu.controls?.Count ?? 0) < VRCExpressionsMenu.MAX_CONTROLS)
            {
                return new PartMenuPlan { menu = partsMenu, willCreate = false, menuPathDisplay = "Parts (existing SubMenu)" };
            }
            return new PartMenuPlan
            {
                menu = null,
                willCreate = true,
                menuPathDisplay = partsMenu != null
                    ? "Parts/More (new nested SubMenu; moves one control from the full owner)"
                    : "Parts (new SubMenu; moves one root control to preserve the 8-control limit)"
            };
        }

        private static VRCExpressionsMenu ResolveOrCreatePartMenu(
            VRCExpressionsMenu rootMenu, string subMenuName, string assetDir, out string appliedMenuPath)
        {
            appliedMenuPath = "";
            if (rootMenu == null) { return null; }

            if (!string.IsNullOrWhiteSpace(subMenuName))
            {
                var existing = FindSubMenu(rootMenu, subMenuName);
                if (existing != null)
                {
                    if ((existing.controls?.Count ?? 0) < VRCExpressionsMenu.MAX_CONTROLS)
                    {
                        appliedMenuPath = subMenuName;
                        return existing;
                    }
                    return CreateOverflowSubMenu(existing, "More", assetDir, subMenuName, out appliedMenuPath);
                }
                return CreateOverflowSubMenu(rootMenu, subMenuName, assetDir, "", out appliedMenuPath);
            }
            if ((rootMenu.controls?.Count ?? 0) < VRCExpressionsMenu.MAX_CONTROLS)
            {
                appliedMenuPath = "(root menu)";
                return rootMenu;
            }
            var partsMenu = FindSubMenu(rootMenu, "Parts");
            if (partsMenu != null)
            {
                if ((partsMenu.controls?.Count ?? 0) < VRCExpressionsMenu.MAX_CONTROLS)
                {
                    appliedMenuPath = "Parts";
                    return partsMenu;
                }
                return CreateOverflowSubMenu(partsMenu, "More", assetDir, "Parts", out appliedMenuPath);
            }
            return CreateOverflowSubMenu(rootMenu, "Parts", assetDir, "", out appliedMenuPath);
        }

        private static VRCExpressionsMenu FindSubMenu(VRCExpressionsMenu menu, string name)
        {
            if (menu?.controls == null) { return null; }
            foreach (var control in menu.controls)
            {
                if (control != null
                    && control.type == VRCExpressionsMenu.Control.ControlType.SubMenu
                    && control.subMenu != null
                    && string.Equals(control.name, name, StringComparison.Ordinal))
                {
                    return control.subMenu;
                }
            }
            return null;
        }

        private static VRCExpressionsMenu CreateOverflowSubMenu(
            VRCExpressionsMenu owner,
            string subMenuName,
            string assetDir,
            string ownerPath,
            out string appliedMenuPath)
        {
            appliedMenuPath = "";
            if (owner == null) { return null; }
            Directory.CreateDirectory(assetDir);
            var subMenu = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
            subMenu.controls = new List<VRCExpressionsMenu.Control>();
            var subPath = AssetDatabase.GenerateUniqueAssetPath($"{assetDir}/{Sanitize(subMenuName, "Parts")}_SubMenu.asset");
            AssetDatabase.CreateAsset(subMenu, subPath);

            Undo.RegisterCompleteObjectUndo(owner, "Add part submenu");
            if (owner.controls == null) { owner.controls = new List<VRCExpressionsMenu.Control>(); }
            while (owner.controls.Count >= VRCExpressionsMenu.MAX_CONTROLS && owner.controls.Count > 0)
            {
                var moveIndex = owner.controls.FindLastIndex(control =>
                    control != null && control.type != VRCExpressionsMenu.Control.ControlType.SubMenu);
                if (moveIndex < 0) { moveIndex = owner.controls.Count - 1; }
                var moved = owner.controls[moveIndex];
                owner.controls.RemoveAt(moveIndex);
                if (moved != null) { subMenu.controls.Insert(0, moved); }
            }
            owner.controls.Add(new VRCExpressionsMenu.Control
            {
                name = subMenuName,
                type = VRCExpressionsMenu.Control.ControlType.SubMenu,
                subMenu = subMenu
            });
            EditorUtility.SetDirty(owner);
            EditorUtility.SetDirty(subMenu);
            appliedMenuPath = string.IsNullOrWhiteSpace(ownerPath)
                ? subMenuName
                : ownerPath + "/" + subMenuName;
            return subMenu;
        }

        // --- Path / naming helpers ---------------------------------------------------

        private static Transform ResolveUnderRoot(Transform root, string rawPath)
        {
            var path = NormalizePath(rawPath);
            if (string.IsNullOrEmpty(path)) { return null; }

            var direct = root.Find(path);
            if (direct != null) { return direct; }

            var rootName = root.name;
            if (path.Equals(rootName, StringComparison.Ordinal)) { return root; }
            if (path.StartsWith(rootName + "/", StringComparison.Ordinal))
            {
                var sub = path.Substring(rootName.Length + 1);
                var byFull = root.Find(sub);
                if (byFull != null) { return byFull; }
            }

            var leaf = path.Contains("/") ? path.Substring(path.LastIndexOf('/') + 1) : path;
            Transform match = null;
            foreach (var t in root.GetComponentsInChildren<Transform>(true))
            {
                if (t == root) { continue; }
                var rel = RelativePath(root, t);
                if (rel.Equals(path, StringComparison.Ordinal) || rel.EndsWith("/" + path, StringComparison.Ordinal) || t.name.Equals(leaf, StringComparison.Ordinal))
                {
                    if (match != null && !match.Equals(t))
                    {
                        if (rel.Equals(path, StringComparison.Ordinal)) { return t; }
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
                        if (!string.IsNullOrWhiteSpace(value) && !result.Contains(value)) { result.Add(value); }
                    }
                }
                else if (@params[key] != null && @params[key].Type == JTokenType.String)
                {
                    var value = @params[key].ToString().Trim();
                    if (!string.IsNullOrWhiteSpace(value) && !result.Contains(value)) { result.Add(value); }
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
            if (string.IsNullOrEmpty(normalized)) { return descriptors[0]; }
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

        private class PartMenuPlan
        {
            public VRCExpressionsMenu menu;
            public bool willCreate;
            public string menuPathDisplay;
        }
    }
}
