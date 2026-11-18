using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;
using VRCForge.Core.MCP;

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
    [VRCForgeCommand(
        toolId: "vrc_add_outfit_part",
        Summary = "Add an int-gated part toggle (e.g. a hat) to one outfit value of an existing int-exclusive wardrobe. Creates a Bool parameter, a dedicated FX layer (Off default; Off->On = int Equals N AND bool true; On->Off = bool false OR int != N), authors on/off clips, sets the part scene-default off, and adds a menu toggle. Add-only. Supports preview."
    )]
    public static class WardrobeOutfitPartWriter
    {
        public const string ToolName = "vrc_add_outfit_part";
        public class Parameters
        {
            [VRCForgeInput("Avatar root path; empty is allowed only when the descriptor selection is unambiguous.", IsRequired = false)]
            public string avatarPath { get; set; } = "";
            [VRCForgeInput("Existing Int wardrobe parameter name.", IsRequired = true)]
            public string parameterName { get; set; } = "";
            [VRCForgeInput("Display name for the new part toggle.", IsRequired = true)]
            public string partName { get; set; } = "";
            [VRCForgeInput("Existing wardrobe Int value that gates this part.", IsRequired = true)]
            public int? value { get; set; }
            [VRCForgeInput("Avatar-relative scene object paths to toggle.", IsRequired = true)]
            public string[] objectPaths { get; set; } = new string[0];
            [VRCForgeInput("Optional Bool expression parameter name; defaults from partName.", IsRequired = false)]
            public string partParameterName { get; set; } = "";
            [VRCForgeInput("Report the authoring plan without modifying assets or the scene.", IsRequired = false)]
            public bool? preview { get; set; } = false;
            [VRCForgeInput("Create a matching expression-menu toggle when capacity permits.", IsRequired = false)]
            public bool? addMenuToggle { get; set; } = true;
            [VRCForgeInput("Set the part scene objects inactive before authoring clips.", IsRequired = false)]
            public bool? setObjectsDefaultOff { get; set; } = true;
            [VRCForgeInput("Initial Bool toggle value.", IsRequired = false)]
            public bool? defaultOn { get; set; } = false;
            [VRCForgeInput("Optional submenu path for the toggle.", IsRequired = false)]
            public string subMenuName { get; set; } = "";
            [VRCForgeInput("Assets-relative directory for generated animation clips.", IsRequired = false)]
            public string clipOutputDir { get; set; } = "";
            [VRCForgeInput("Optional Write Defaults setting; defaults to the existing wardrobe convention.", IsRequired = false)]
            public bool? writeDefaults { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            var mutationStarted = false;
            var failurePhase = "preflight";
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

                if (string.IsNullOrWhiteSpace(parameterName))
                {
                    return VRCForgeToolResult.Failed("Missing required parameter: parameterName (the existing int wardrobe parameter the part is gated on).");
                }
                if (string.IsNullOrWhiteSpace(partName))
                {
                    return VRCForgeToolResult.Failed("Missing required parameter: partName (display name for the new part toggle).");
                }
                if (@params["value"] == null && @params["outfitValue"] == null)
                {
                    return VRCForgeToolResult.Failed("Missing required parameter: value (the wardrobe int value N this part belongs to).");
                }
                var outfitValue = (@params["value"] ?? @params["outfitValue"]).ToObject<int>();

                if (string.IsNullOrWhiteSpace(partParameterName))
                {
                    partParameterName = Sanitize(partName, "Part");
                }

                var objectInputs = ReadStringArray(@params, "objectPaths", "onObjectPaths");
                if (objectInputs.Count == 0)
                {
                    return VRCForgeToolResult.Failed("Missing required parameter: objectPaths (the part's scene objects to toggle on/off).");
                }

                var descriptor = ResolveAvatarDescriptor(avatarPath);
                var clipDir = GeneratedAssetPaths.ResolveDirectory(@params["clipOutputDir"]?.ToString(), descriptor.name, GeneratedAssetPaths.Animations, "Wardrobe");
                var menuAssetDir = GeneratedAssetPaths.ResolveDirectory("", descriptor.name, GeneratedAssetPaths.Menus, "Wardrobe");
                var avatarRoot = descriptor.transform;
                var avatarRootPath = GetTransformPath(avatarRoot);

                // 1. Validate the wardrobe int parameter exists.
                var parametersAsset = descriptor.expressionParameters;
                if (parametersAsset == null || parametersAsset.parameters == null)
                {
                    return VRCForgeToolResult.Failed("Avatar has no VRCExpressionParameters; cannot resolve the wardrobe int parameter.");
                }
                var intParam = parametersAsset.parameters.FirstOrDefault(p =>
                    p != null && p.name == parameterName && p.valueType == VRCExpressionParameters.ValueType.Int);
                if (intParam == null)
                {
                    return VRCForgeToolResult.Failed(
                        $"Parameter '{parameterName}' is not an existing Int expression parameter. " +
                        "Build the wardrobe (and its outfit value) first, then add parts to it.");
                }

                // 2. Locate the wardrobe FX layer and reconcile existing outfit values.
                var fxController = GetFxController(descriptor);
                if (fxController == null)
                {
                    return VRCForgeToolResult.Failed("No FX AnimatorController found on the avatar.");
                }
                var fxControllerPath = AssetDatabase.GetAssetPath(fxController);
                var wardrobeLayerIndex = FindWardrobeLayerIndex(fxController, parameterName);

                var existingValues = new SortedSet<int>();
                var wardrobeLayer = wardrobeLayerIndex >= 0 ? fxController.layers[wardrobeLayerIndex] : null;
                if (wardrobeLayer != null)
                {
                    var equals = new List<EqualsInfo>();
                    CollectAnyStateEquals(wardrobeLayer.stateMachine, parameterName, equals);
                    CollectStatesByName(wardrobeLayer.stateMachine, new Dictionary<string, AnimatorState>());
                    foreach (var eq in equals)
                    {
                        existingValues.Add(eq.value);
                    }
                }
                var newWriteDefaults = AnimatorWriteDefaults.Resolve(fxController, wardrobeLayer, @params.Value<bool?>("writeDefaults"), avatar: descriptor);

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
                    return VRCForgeToolResult.Failed(
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
                    return VRCForgeToolResult.Failed(
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
                var onClipPath = GeneratedAssetPaths.UniqueAssetPath($"{clipDir}/{onClipFile}");
                var offClipPath = GeneratedAssetPaths.UniqueAssetPath($"{clipDir}/{offClipFile}");

                var menuPlan = PlanPartMenu(descriptor.expressionsMenu, subMenuName);
                var menuAssetPath = addMenuToggle && menuPlan.willCreate
                    ? GeneratedAssetPaths.UniqueAssetPath($"{menuAssetDir}/{Sanitize(menuPlan.newSubMenuName, "Parts")}_SubMenu.asset") : "";
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
                    onClipPath,
                    offClipPath,
                    writeDefaults = newWriteDefaults,
                    onObjects,
                    setObjectsDefaultOff,
                    addMenuToggle = addMenuToggle && (menuPlan.menu != null || menuPlan.willCreate),
                    menuPath = menuPlan.menuPathDisplay,
                    menuAssetDir,
                    menuAssetPath,
                    existingOutfitValues = existingValues.ToArray(),
                    warnings
                };

                if (preview)
                {
                    return VRCForgeToolResult.Completed(
                        $"Preview: would add part '{partName}' (bool '{partParameterName}') gated on '{parameterName}' == {outfitValue} on '{descriptor.name}'.",
                        new { ok = true, preview = true, plan });
                }

                // ---- APPLY -------------------------------------------------------
                var rootMenu = addMenuToggle ? descriptor.expressionsMenu : null;
                var changedParametersAsset = boolParamExists ? null : parametersAsset;
                var saveScope = new WardrobeAssetSaveScope(fxController, changedParametersAsset, rootMenu);
                var menusBefore = ExpressionWritePersistence.Capture(rootMenu);
                var sceneTargets = setObjectsDefaultOff && !defaultOn
                    ? resolvedTargets.Select(item => item.gameObject).Where(item => item.activeSelf).ToArray()
                    : Array.Empty<GameObject>();
                var targetScene = descriptor.gameObject.scene;
                string scenePath = null;
                if (sceneTargets.Length > 0)
                {
                    if (!targetScene.IsValid() || !targetScene.isLoaded || targetScene.isDirty)
                        throw new InvalidOperationException("Outfit part scene changes require a loaded, saved scene with no unsaved changes.");
                    scenePath = SceneObjectCopyCore.NormalizeSceneAssetPath(targetScene.path, "outfit part scene");
                    if (!File.Exists(SceneObjectCopyCore.ToAbsoluteAssetPath(scenePath))
                        || !SceneObjectCopyCore.SafeSiblingFileExists(scenePath, ".meta"))
                        throw new InvalidOperationException("The outfit part scene asset is incomplete.");
                }
                WardrobeToggleReadback.RequireParameterType(fxController, parameterName, AnimatorControllerParameterType.Int);
                WardrobeToggleReadback.RequireParameterType(fxController, partParameterName, AnimatorControllerParameterType.Bool);
                var undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName($"Add outfit part '{partName}'");
                mutationStarted = true;
                failurePhase = "unity_mutation";
                AvatarAuthoringCrudCore.EnsureAssetFolder(clipDir);

                // a. Ensure the Bool expression parameter.
                if (changedParametersAsset != null)
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
                foreach (var go in sceneTargets)
                {
                    Undo.RecordObject(go, "Outfit part default off");
                    go.SetActive(false);
                    if (PrefabUtility.IsPartOfPrefabInstance(go))
                        PrefabUtility.RecordPrefabInstancePropertyModifications(go);
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
                AssetDatabase.CreateAsset(onClip, onClipPath);
                AssetDatabase.CreateAsset(offClip, offClipPath);
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
                string toggleAssetPath = null;
                if (addMenuToggle)
                {
                    var target = ResolveOrCreatePartMenu(rootMenu, subMenuName, menuAssetDir, menuAssetPath, out appliedMenuPath);
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
                        toggleAssetPath = AssetDatabase.GetAssetPath(target);
                    }
                }

                var expectedController = WardrobeToggleReadback.DescribeController(fxController, new[] { layerName },
                    new[] { parameterName, partParameterName });
                var expectedParameters = ExpressionWritePersistence.Capture(changedParametersAsset);
                var expectedMenus = ExpressionWritePersistence.Capture(rootMenu).Where(pair =>
                    !menusBefore.TryGetValue(pair.Key, out var before) || !JToken.DeepEquals(pair.Value, before))
                    .ToDictionary(pair => pair.Key, pair => pair.Value);
                var expectedOn = WardrobeToggleReadback.CaptureActiveCurves(onClip, onObjects, true);
                var expectedOff = WardrobeToggleReadback.CaptureActiveCurves(offClip, onObjects, false);
                failurePhase = "asset_save";
                saveScope.Save(fxController, changedParametersAsset, rootMenu, onClip, offClip);
                if (scenePath != null)
                {
                    Undo.FlushUndoRecordObjects();
                    EditorSceneManager.MarkSceneDirty(targetScene);
                    if (!EditorSceneManager.SaveScene(targetScene))
                        throw new InvalidOperationException("The outfit part scene could not be saved.");
                }
                failurePhase = "persisted_readback";
                var controllerReadback = WardrobeToggleReadback.ReadController(fxControllerPath, expectedController,
                    new[] { layerName }, new[] { parameterName, partParameterName });
                var parameterReadback = WardrobeToggleReadback.ReadExpressionAssets(expectedParameters);
                var menuReadback = WardrobeToggleReadback.ReadExpressionAssets(expectedMenus);
                var curves = new[]
                {
                    WardrobeToggleReadback.ReadActiveCurves(createdOnClipPath, expectedOn),
                    WardrobeToggleReadback.ReadActiveCurves(createdOffClipPath, expectedOff)
                };
                var objects = sceneTargets.Select(target =>
                {
                    var path = GetTransformPath(target.transform);
                    if (target.scene != targetScene || target.activeSelf)
                        throw new InvalidOperationException("Outfit part target is not inactive in the saved scene: " + path);
                    return new { path, activeSelf = false };
                }).ToArray();
                Undo.CollapseUndoOperations(undoGroup);

                return VRCForgeToolResult.Completed(
                    $"Added part '{partName}' (bool '{partParameterName}') gated on '{parameterName}' == {outfitValue} on '{descriptor.name}'.",
                    new
                    {
                        schema = "vrcforge.outfit_part_write.v1",
                        ok = true,
                        preview = false,
                        verified = true,
                        persistedReadback = true,
                        mutationStarted = true,
                        mutationApplied = true,
                        committed = true,
                        commitState = "committed",
                        readback = new { persisted = true, controller = controllerReadback, parameters = parameterReadback,
                            menus = menuReadback, toggleAssetPath, curves, scenePath, objects },
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
                return VRCForgeToolResult.FailedWithException("outfit_part_write_failed", $"Add outfit part failed: {ex.Message}", ex,
                    new { schema = "vrcforge.outfit_part_write.v1", ok = false, verified = false, mutationStarted,
                        committed = false, commitState = mutationStarted ? "unknown" : "not_started", failurePhase,
                        checkpointRecoveryRequired = mutationStarted, retryable = false });
            }
        }

        // --- FX reconciliation -------------------------------------------------------

        private static AnimatorController GetFxController(VRCAvatarDescriptor descriptor)
        {
            return AvatarAuthoringCrudCore.GetFxController(descriptor);
        }

        private static int FindWardrobeLayerIndex(AnimatorController controller, string parameterName)
        {
            var layers = controller.layers;
            var selected = -1;
            for (var i = 0; i < layers.Length; i++)
            {
                var layer = layers[i];
                if (layer?.stateMachine == null) { continue; }
                if (LayerHasEquals(layer.stateMachine, parameterName))
                {
                    if (selected >= 0)
                        throw new InvalidOperationException("Multiple FX layers match the wardrobe parameter; select an unambiguous wardrobe before adding a part.");
                    selected = i;
                }
            }
            return selected;
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
                if (child.state != null)
                {
                    var name = child.state.name ?? "";
                    if (sink.TryGetValue(name, out var existing) && existing != child.state)
                        throw new InvalidOperationException("Wardrobe state name is ambiguous: " + name);
                    sink[name] = child.state;
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
                    newSubMenuName = existing != null ? "More" : subMenuName,
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
                newSubMenuName = partsMenu != null ? "More" : "Parts",
                menuPathDisplay = partsMenu != null
                    ? "Parts/More (new nested SubMenu; moves one control from the full owner)"
                    : "Parts (new SubMenu; moves one root control to preserve the 8-control limit)"
            };
        }

        private static VRCExpressionsMenu ResolveOrCreatePartMenu(
            VRCExpressionsMenu rootMenu, string subMenuName, string assetDir, string plannedSubMenuPath, out string appliedMenuPath)
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
                    return CreateOverflowSubMenu(existing, "More", assetDir, plannedSubMenuPath, subMenuName, out appliedMenuPath);
                }
                return CreateOverflowSubMenu(rootMenu, subMenuName, assetDir, plannedSubMenuPath, "", out appliedMenuPath);
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
                return CreateOverflowSubMenu(partsMenu, "More", assetDir, plannedSubMenuPath, "Parts", out appliedMenuPath);
            }
            return CreateOverflowSubMenu(rootMenu, "Parts", assetDir, plannedSubMenuPath, "", out appliedMenuPath);
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
            string plannedSubMenuPath,
            string ownerPath,
            out string appliedMenuPath)
        {
            appliedMenuPath = "";
            if (owner == null) { return null; }
            AvatarAuthoringCrudCore.EnsureAssetFolder(assetDir);
            var subMenu = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
            subMenu.controls = new List<VRCExpressionsMenu.Control>();
            var subPath = GeneratedAssetPaths.ValidateNewAssetPath(plannedSubMenuPath);
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
            if (string.IsNullOrEmpty(path)) return null;
            if (path.Equals(root.name, StringComparison.Ordinal))
            {
                if (root.GetComponentsInChildren<Transform>(true).Any(item => item != root && item.name == path))
                    throw new InvalidOperationException("Wardrobe object path is ambiguous: " + rawPath);
                return root;
            }
            var qualified = path.Contains("/");
            var prefixed = path.StartsWith(root.name + "/", StringComparison.Ordinal)
                ? path.Substring(root.name.Length + 1) : null;
            var descendants = root.GetComponentsInChildren<Transform>(true)
                .Where(item => item != root).ToArray();
            var matches = descendants.Where(item => qualified
                ? RelativePath(root, item).Equals(path, StringComparison.Ordinal)
                    || (prefixed != null && RelativePath(root, item).Equals(prefixed, StringComparison.Ordinal))
                : item.name.Equals(path, StringComparison.Ordinal)).ToArray();
            if (matches.Length > 1)
                throw new InvalidOperationException("Wardrobe object path is ambiguous: " + rawPath);
            return matches.SingleOrDefault();
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

        private static VRCAvatarDescriptor ResolveAvatarDescriptor(string avatarPath)
        {
            return AvatarAuthoringCrudCore.ResolveAvatarDescriptor(avatarPath);
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
            public string newSubMenuName;
            public string menuPathDisplay;
        }
    }

    internal static class WardrobeToggleReadback
    {
        internal static void RequireParameterType(AnimatorController controller, string name, AnimatorControllerParameterType type)
        {
            var existing = controller.parameters.Where(parameter => parameter.name == name).ToArray();
            if (existing.Length > 1 || (existing.Length == 1 && existing[0].type != type))
                throw new InvalidOperationException($"FX controller parameter '{name}' must have a unique {type} binding.");
        }

        internal static JToken DescribeController(AnimatorController controller, string[] layerNames, string[] parameterNames)
        {
            var controllerState = ManageFxAnimatorTool.DescribeForBatch(controller);
            return new JObject
            {
                ["layers"] = new JArray(controllerState["layers"].Where(layer => layerNames.Contains(layer.Value<string>("name")))),
                ["parameters"] = new JArray(controllerState["parameters"].Where(parameter => parameterNames.Contains(parameter.Value<string>("name"))))
            };
        }

        internal static object ReadController(string path, JToken expected, string[] layerNames, string[] parameterNames)
        {
            var actual = DescribeController(Reload<AnimatorController>(path), layerNames, parameterNames);
            if (!JToken.DeepEquals(actual, expected))
                throw new InvalidOperationException("Persisted wardrobe controller graph differs from the authored graph: " + path);
            return new { path, state = actual };
        }

        internal static string[] ReadExpressionAssets(IReadOnlyDictionary<string, JToken> expected)
        {
            foreach (var pair in expected)
            {
                var asset = Reload<UnityEngine.Object>(pair.Key);
                if (!JToken.DeepEquals(JToken.Parse(EditorJsonUtility.ToJson(asset)), pair.Value))
                    throw new InvalidOperationException("Persisted wardrobe expression state differs from the authored state: " + pair.Key);
            }
            return expected.Keys.ToArray();
        }

        internal static Dictionary<string, AnimationCurve> CaptureActiveCurves(AnimationClip clip, IEnumerable<string> paths, bool active)
        {
            var expected = new Dictionary<string, AnimationCurve>(StringComparer.Ordinal);
            foreach (var path in paths.Distinct())
            {
                var curve = AnimationUtility.GetEditorCurve(clip, ActiveBinding(path));
                if (curve == null || curve.length == 0 || curve.keys.Any(key => key.time != 0f || key.value != (active ? 1f : 0f)))
                    throw new InvalidOperationException($"Authored active-state curve does not match '{path}' on '{clip.name}'.");
                expected.Add(path, curve);
            }
            return expected;
        }

        internal static object ReadActiveCurves(string path, IReadOnlyDictionary<string, AnimationCurve> expected)
        {
            var clip = Reload<AnimationClip>(path);
            foreach (var pair in expected)
            {
                var curve = AnimationUtility.GetEditorCurve(clip, ActiveBinding(pair.Key));
                if (!WriteAnimationCurveTool.CurvesEqual(pair.Value, curve))
                    throw new InvalidOperationException($"Persisted active-state curve differs for '{pair.Key}' in '{path}'.");
            }
            return new { path, bindings = expected.Keys.ToArray() };
        }

        private static EditorCurveBinding ActiveBinding(string path) =>
            new EditorCurveBinding { path = path, type = typeof(GameObject), propertyName = "m_IsActive" };

        private static T Reload<T>(string path) where T : UnityEngine.Object
        {
            var asset = AssetDatabase.LoadAssetAtPath<T>(path);
            if (asset == null || EditorUtility.IsDirty(asset))
                throw new InvalidOperationException("Wardrobe asset is missing or unsaved: " + path);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            return AssetDatabase.LoadAssetAtPath<T>(path)
                ?? throw new InvalidOperationException("Persisted wardrobe asset could not be reloaded: " + path);
        }
    }
}
