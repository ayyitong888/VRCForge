using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;
using VRCForge.Core.MCP;

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
    [VRCForgeCommand(
        toolId: "vrc_add_wardrobe_outfit",
        Summary = "Add one outfit to an existing int-exclusive wardrobe: assign next int value, set new objects scene-default off, author a clip (own objects on, sibling objects off), add an FX state with Any-State Equals N (Write Defaults matched), and a menu toggle (nested SubMenu overflow). Add-only, never rewrites existing clips. Supports preview."
    )]
    public static class WardrobeOutfitWriter
    {
        public const string ToolName = "vrc_add_wardrobe_outfit";
        public class Parameters
        {
            [VRCForgeInput("Avatar root path; empty is allowed only when the descriptor selection is unambiguous.", IsRequired = false)]
            public string avatarPath { get; set; } = "";
            [VRCForgeInput("Existing Int wardrobe parameter name.", IsRequired = true)]
            public string parameterName { get; set; } = "";
            [VRCForgeInput("Display name for the new outfit.", IsRequired = true)]
            public string outfitName { get; set; } = "";
            [VRCForgeInput("Avatar-relative scene object paths that are active for the new outfit.", IsRequired = true)]
            public string[] objectPaths { get; set; } = new string[0];
            [VRCForgeInput("Optional avatar-relative paths that the outfit clip explicitly turns off.", IsRequired = false)]
            public string[] offObjectPaths { get; set; } = new string[0];
            [VRCForgeInput("Report the authoring plan without modifying assets or the scene.", IsRequired = false)]
            public bool? preview { get; set; } = false;
            [VRCForgeInput("Create a matching expression-menu toggle when capacity permits.", IsRequired = false)]
            public bool? addMenuToggle { get; set; } = true;
            [VRCForgeInput("Set new outfit objects inactive before authoring the clip.", IsRequired = false)]
            public bool? setObjectsDefaultOff { get; set; } = true;
            [VRCForgeInput("Permit a nested submenu when the menu is full.", IsRequired = false)]
            public bool? subMenuOverflow { get; set; } = true;
            [VRCForgeInput("Submenu name used for overflow controls.", IsRequired = false)]
            public string subMenuName { get; set; } = "Wardrobe";
            [VRCForgeInput("Assets-relative directory for the generated animation clip.", IsRequired = false)]
            public string clipOutputDir { get; set; } = "";
            [VRCForgeInput("Optional fingerprint from the approved wardrobe scan; rejects drift when supplied.", IsRequired = false)]
            public string expectedWardrobeFingerprint { get; set; } = "";
            [VRCForgeInput("Optional approved object receipt nonce required by the continuation lane.", IsRequired = false)]
            public string approvedObjectReceiptNonce { get; set; } = "";
            [VRCForgeInput("Optional explicit unused Int value for the new outfit.", IsRequired = false)]
            public int? value { get; set; }
            [VRCForgeInput("Optional assigned-value assertion from the approved plan.", IsRequired = false)]
            public int? expectedAssignedValue { get; set; }
            [VRCForgeInput("Optional Write Defaults setting; defaults to the existing wardrobe convention.", IsRequired = false)]
            public bool? writeDefaults { get; set; }
            [VRCForgeInput("Exact checkpointAssetPaths returned by the approved preview.", IsRequired = false)]
            public string[] expectedCheckpointAssetPaths { get; set; } = new string[0];
        }

        public static object HandleCommand(JObject @params)
        {
            var mutationStarted = false;
            var continuationConsumed = false;
            var recoveryAvatarPath = "";
            var recoveryParameterName = "";
            var recoveryClipPath = "";
            var receipts = new List<TransactionReceipt>();
            var transactionHandle = "";
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
                var expectedWardrobeFingerprint = (@params["expectedWardrobeFingerprint"]?.ToString() ?? string.Empty).Trim();
                var approvedObjectReceiptNonce = (@params["approvedObjectReceiptNonce"]?.ToString() ?? string.Empty).Trim();
                var expectedCheckpointAssetPaths = @params["expectedCheckpointAssetPaths"]?.ToObject<string[]>();

                if (string.IsNullOrWhiteSpace(parameterName))
                {
                    return VRCForgeToolResult.Failed("Missing required parameter: parameterName (the existing int wardrobe parameter to add to).");
                }
                if (string.IsNullOrWhiteSpace(outfitName))
                {
                    return VRCForgeToolResult.Failed("Missing required parameter: outfitName (display name for the new outfit).");
                }

                var objectInputs = ReadStringArray(@params, "objectPaths", "onObjectPaths");
                if (objectInputs.Count == 0)
                {
                    return VRCForgeToolResult.Failed("Missing required parameter: objectPaths (the new outfit's scene objects to turn on).");
                }
                var explicitOffInputs = ReadStringArray(@params, "offObjectPaths");

                var descriptor = ResolveAvatarDescriptor(avatarPath);
                var clipDir = GeneratedAssetPaths.ResolveDirectory(@params["clipOutputDir"]?.ToString(), descriptor.name, GeneratedAssetPaths.Animations, "Wardrobe");
                var menuAssetDir = GeneratedAssetPaths.ResolveDirectory("", descriptor.name, GeneratedAssetPaths.Menus, "Wardrobe");
                var avatarRoot = descriptor.transform;
                var avatarRootPath = GetTransformPath(avatarRoot);
                recoveryAvatarPath = avatarRootPath;
                recoveryParameterName = parameterName;
                if (!string.IsNullOrEmpty(expectedWardrobeFingerprint))
                {
                    var liveFingerprint = WardrobeScanner.ComputeStableFingerprintForAvatar(avatarRootPath);
                    if (!string.Equals(liveFingerprint, expectedWardrobeFingerprint, StringComparison.Ordinal))
                    {
                        return VRCForgeToolResult.Failed("Wardrobe state drifted from the approved fingerprint.");
                    }
                }

                // 1. Validate the parameter is an existing Int expression parameter.
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
                        "This tool adds to an EXISTING int-exclusive wardrobe; build the wardrobe first.");
                }

                // 2. Locate the wardrobe FX layer (Any State -> state Equals <param>).
                var fxController = GetFxController(descriptor);
                if (fxController == null)
                {
                    return VRCForgeToolResult.Failed("No FX AnimatorController found on the avatar.");
                }
                var fxControllerPath = AssetDatabase.GetAssetPath(fxController);
                var wardrobeLayerIndex = FindWardrobeLayerIndex(fxController, parameterName);
                if (wardrobeLayerIndex < 0)
                {
                    return VRCForgeToolResult.Failed(
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
                        return VRCForgeToolResult.Failed($"Requested value {newValue} already exists in wardrobe '{parameterName}'.");
                    }
                }
                else
                {
                    newValue = (existingValues.Count > 0 ? existingValues.Max() : 0) + 1;
                }
                if (@params["expectedAssignedValue"] != null
                    && newValue != @params["expectedAssignedValue"].ToObject<int>())
                {
                    return VRCForgeToolResult.Failed("Wardrobe assigned value drifted from the approved expectation.");
                }

                // 5. Resolve the new outfit objects to avatar-root-relative binding paths.
                var onObjects = new List<string>();
                var resolvedTargets = new List<Transform>();
                var objectGlobalObjectIds = new List<string>();
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
                        objectGlobalObjectIds.Add(GlobalObjectId.GetGlobalObjectIdSlow(t.gameObject).ToString());
                    }
                }
                if (unresolved.Count > 0)
                {
                    return VRCForgeToolResult.Failed(
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
                        return VRCForgeToolResult.Failed(
                            "Could not resolve these offObjectPaths under the avatar: " + string.Join(", ", offUnresolved) + ".");
                    }
                }
                else
                {
                    offObjects = siblingOnObjects.Where(p => !onObjects.Contains(p)).OrderBy(p => p, StringComparer.Ordinal).ToList();
                }

                var stateName = MakeUniqueStateName(stateByName, Sanitize(outfitName, "Outfit"));
                var clipFileName = $"{Sanitize(descriptor.name, "Avatar")}_{Sanitize(parameterName, "Wardrobe")}_{stateName}.anim";
                var clipPath = GeneratedAssetPaths.UniqueAssetPath($"{clipDir}/{clipFileName}");
                recoveryClipPath = clipPath;

                // Plan menu placement (read-only resolution).
                var menuPlan = PlanMenuPlacement(descriptor.expressionsMenu, parameterName, menuToggles, subMenuOverflow, subMenuName);
                var menuAssetPath = addMenuToggle && menuPlan.createsSubMenu
                    ? GeneratedAssetPaths.UniqueAssetPath($"{menuAssetDir}/{Sanitize(subMenuName, "Wardrobe")}_SubMenu.asset") : "";

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

                var checkpointPaths = new List<string> { fxControllerPath, clipPath };
                if (addMenuToggle)
                {
                    checkpointPaths.AddRange(ExpressionWritePersistence.Capture(descriptor.expressionsMenu).Keys);
                    if (!string.IsNullOrWhiteSpace(menuAssetPath)) checkpointPaths.Add(menuAssetPath);
                }
                if (setObjectsDefaultOff && resolvedTargets.Any(item => item.gameObject.activeSelf))
                {
                    checkpointPaths.Add(descriptor.gameObject.scene.path);
                }
                var checkpointAssetPaths = AvatarAuthoringCrudCore.CheckpointAssetPaths(checkpointPaths);

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
                    objectGlobalObjectIds,
                    offObjects,
                    setObjectsDefaultOff,
                    addMenuToggle = addMenuToggle && menuPlan.menu != null,
                    menuPath = menuPlan.menuPathDisplay,
                    menuAssetDir,
                    menuAssetPath,
                    menuOverflowToSubMenu = menuPlan.createsSubMenu,
                    checkpointAssetPaths,
                    existingOutfitCount = existingValues.Count,
                    warnings
                };

                if (preview)
                {
                    return VRCForgeToolResult.Completed(
                        $"Preview: would add outfit '{outfitName}' as value {newValue} to wardrobe '{parameterName}' on '{descriptor.name}'.",
                        new
                        {
                            ok = true,
                            preview = true,
                            plan
                        });
                }

                AvatarAuthoringCrudCore.RequireExpectedCheckpointAssetPaths(
                    checkpointAssetPaths, expectedCheckpointAssetPaths);

                // ---- APPLY -------------------------------------------------------
                transactionHandle = $"{avatarRootPath}|{parameterName}";
                var sceneReceipts = new Dictionary<string, TransactionReceipt>(StringComparer.Ordinal);
                var sceneObjectIds = new Dictionary<string, long>(StringComparer.Ordinal);
                var prefabObjectIds = new Dictionary<string, PrefabSceneDefaultIdentity>(StringComparer.Ordinal);
                if (setObjectsDefaultOff)
                {
                    foreach (var targetTransform in resolvedTargets.Where(item => item.gameObject.activeSelf))
                    {
                        var targetPath = GetTransformPath(targetTransform);
                        var sceneGuid = AssetDatabase.AssetPathToGUID(descriptor.gameObject.scene.path);
                        if (PrefabUtility.IsPartOfPrefabInstance(targetTransform.gameObject))
                        {
                            var instanceRoot = PrefabUtility.GetNearestPrefabInstanceRoot(targetTransform.gameObject);
                            var sourceObject = PrefabUtility.GetCorrespondingObjectFromSource(targetTransform.gameObject) as GameObject;
                            var instanceId = GlobalObjectId.GetGlobalObjectIdSlow(instanceRoot);
                            if (instanceRoot == null || sourceObject == null || instanceId.identifierType != 2
                                || instanceId.targetPrefabId == 0
                                || !string.Equals(instanceId.assetGUID.ToString(), sceneGuid, StringComparison.OrdinalIgnoreCase)
                                || !AssetDatabase.TryGetGUIDAndLocalFileIdentifier(sourceObject, out var sourceGuid, out long sourceFileId)
                                || !string.Equals(sourceGuid, AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(sourceObject)), StringComparison.OrdinalIgnoreCase))
                                throw new InvalidOperationException("Wardrobe prefab scene target has no stable instance/source identity.");
                            prefabObjectIds[targetPath] = new PrefabSceneDefaultIdentity
                            {
                                instanceRootFileId = instanceId.targetPrefabId,
                                sourceFileId = sourceFileId,
                                sourceGuid = sourceGuid
                            };
                        }
                        else
                        {
                            if (!AssetDatabase.TryGetGUIDAndLocalFileIdentifier(targetTransform.gameObject, out var sceneObjectGuid, out long sceneObjectId)
                                || !string.Equals(sceneObjectGuid, sceneGuid, StringComparison.OrdinalIgnoreCase))
                                throw new InvalidOperationException("Wardrobe scene default target has no stable scene object identity.");
                            sceneObjectIds[targetPath] = sceneObjectId;
                        }
                        var receipt = new TransactionReceipt
                        {
                            Asset = $"scene:{targetPath}",
                            Before = new { activeSelf = targetTransform.gameObject.activeSelf }
                        };
                        receipts.Add(receipt);
                        sceneReceipts[targetPath] = receipt;
                    }
                }
                var clipReceipt = new TransactionReceipt
                {
                    Asset = clipPath,
                    Before = new { exists = AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath) != null }
                };
                receipts.Add(clipReceipt);
                var controllerReceipt = new TransactionReceipt
                {
                    Asset = fxControllerPath,
                    Before = DescribeAsset(fxController)
                };
                receipts.Add(controllerReceipt);
                TransactionReceipt menuReceipt = null;
                var rootMenuPath = AssetDatabase.GetAssetPath(descriptor.expressionsMenu);
                if (addMenuToggle && descriptor.expressionsMenu != null)
                {
                    menuReceipt = new TransactionReceipt
                    {
                        Asset = rootMenuPath,
                        Before = DescribeAsset(descriptor.expressionsMenu)
                    };
                    receipts.Add(menuReceipt);
                }
                var controllerBefore = ExpressionWritePersistence.Capture(fxController);
                var menuBefore = addMenuToggle && descriptor.expressionsMenu != null
                    ? ExpressionWritePersistence.Capture(descriptor.expressionsMenu) : null;
                var saveScope = new WardrobeAssetSaveScope(fxController,
                    addMenuToggle ? descriptor.expressionsMenu : null);
                var sceneSaveScope = new WardrobeSceneSaveScope(
                    setObjectsDefaultOff && resolvedTargets.Any(item => item.gameObject.activeSelf)
                        ? descriptor.gameObject : null);
                if (!string.IsNullOrWhiteSpace(approvedObjectReceiptNonce))
                {
                    if (resolvedTargets.Count != 1)
                    {
                        return VRCForgeToolResult.Failed("Approval-bound outfit continuation requires exactly one new outfit object.");
                    }
                    VRCForgeApprovedObjectReceipt.Consume(
                        approvedObjectReceiptNonce,
                        ToolName,
                        resolvedTargets[0].gameObject);
                    continuationConsumed = true;
                }
                mutationStarted = true;
                AvatarAuthoringCrudCore.EnsureAssetFolder(clipDir);
                var undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName($"Add wardrobe outfit '{outfitName}'");

                // a. New objects scene-default OFF.
                if (setObjectsDefaultOff)
                {
                    foreach (var t in resolvedTargets.Where(item => item.gameObject.activeSelf))
                    {
                        var go = t.gameObject;
                        Undo.RecordObject(go, "Wardrobe outfit default off");
                        if (go.activeSelf)
                        {
                            go.SetActive(false);
                            if (PrefabUtility.IsPartOfPrefabInstance(go))
                                PrefabUtility.RecordPrefabInstancePropertyModifications(go);
                        }
                        var objectPath = GetTransformPath(t);
                        if (sceneReceipts.TryGetValue(objectPath, out var sceneReceipt))
                        {
                            sceneReceipt.After = new { activeSelf = go.activeSelf };
                            sceneReceipt.Status = "pending_verification";
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
                AssetDatabase.CreateAsset(clip, clipPath);
                var createdClipPath = AssetDatabase.GetAssetPath(clip);
                clipReceipt.Asset = createdClipPath;
                clipReceipt.After = DescribeAsset(clip);
                clipReceipt.Status = "pending_verification";

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
                controllerReceipt.After = DescribeAsset(fxController);
                controllerReceipt.Status = "pending_verification";

                // d. Add the menu toggle (overflow into a nested SubMenu when needed).
                string appliedMenuPath = "";
                VRCExpressionsMenu appliedMenuTarget = null;
                bool menuToggleAdded = false;
                if (addMenuToggle)
                {
                    var target = ResolveOrCreateMenuTarget(
                        descriptor.expressionsMenu, parameterName, menuToggles, subMenuOverflow, subMenuName, menuAssetDir, menuAssetPath, out appliedMenuPath);
                    if (target != null)
                    {
                        appliedMenuTarget = target;
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
                        if (menuReceipt != null)
                        {
                            menuReceipt.After = DescribeAsset(descriptor.expressionsMenu);
                            menuReceipt.Status = "pending_verification";
                        }
                    }
                }

                saveScope.Save(fxController, addMenuToggle ? descriptor.expressionsMenu : null, clip);
                sceneSaveScope.Save();
                AssetDatabase.Refresh();
                Undo.CollapseUndoOperations(undoGroup);
                var postWardrobeFingerprint = WardrobeScanner.ComputeStableFingerprintForAvatar(avatarRootPath);
                if (string.IsNullOrWhiteSpace(postWardrobeFingerprint)
                    || string.Equals(postWardrobeFingerprint, expectedWardrobeFingerprint, StringComparison.Ordinal))
                {
                    throw new InvalidOperationException("Wardrobe write readback fingerprint did not advance.");
                }
                var readbackClip = VerifyClipReadback(createdClipPath, onObjects, offObjects);
                clipReceipt.After = DescribeAsset(readbackClip);
                clipReceipt.Status = "succeeded";
                var controllerReadback = ExpressionWritePersistence.SaveAndVerify(
                    controllerBefore, fxController, descriptor, false, false);
                var readbackController = AssetDatabase.LoadAssetAtPath<AnimatorController>(fxControllerPath);
                VerifyWardrobeFxReadback(readbackController, wardrobeLayer.name, stateName, parameterName, newValue, newWriteDefaults, readbackClip);
                controllerReceipt.After = DescribeAsset(readbackController);
                controllerReceipt.Status = "succeeded";
                object menuReadback = null;
                if (menuReceipt != null && appliedMenuTarget != null)
                {
                    menuReadback = ExpressionWritePersistence.SaveAndVerify(
                        menuBefore, descriptor.expressionsMenu, descriptor, false, true, appliedMenuTarget);
                    VerifyMenuReadback(descriptor.expressionsMenu, outfitName, parameterName, newValue);
                    menuReceipt.After = DescribeAsset(AssetDatabase.LoadAssetAtPath<VRCExpressionsMenu>(rootMenuPath));
                    menuReceipt.Status = "succeeded";
                }
                var sceneReadback = VerifySceneDefaultReadback(sceneReceipts, sceneObjectIds, prefabObjectIds, descriptor.gameObject.scene.path);
                foreach (var receipt in sceneReceipts.Values) receipt.Status = "succeeded";

                return VRCForgeToolResult.Completed(
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
                        previousWardrobeFingerprint = expectedWardrobeFingerprint,
                        wardrobeFingerprint = postWardrobeFingerprint,
                        fxControllerPath,
                        fxLayerName = wardrobeLayer.name,
                        fxStateName = stateName,
                        clipPath = createdClipPath,
                        writeDefaults = newWriteDefaults,
                        onObjects,
                        objectGlobalObjectIds,
                        offObjects,
                        setObjectsDefaultOff,
                        menuToggleAdded,
                        menuPath = appliedMenuPath,
                        continuationConsumed,
                        warnings,
                        transaction = BuildTransaction(receipts, transactionHandle),
                        schema = "vrcforge.wardrobe_outfit_write.receipt.v1",
                        verified = true,
                        persistedReadback = true,
                        committed = true,
                        commitState = "committed",
                        readback = new { clip = new { path = createdClipPath, verified = true }, controller = controllerReadback, menu = menuReadback, scene = sceneReadback }
                    });
            }
            catch (Exception ex)
            {
                if (mutationStarted)
                {
                    var failed = receipts.FirstOrDefault(item => item.Status == "pending_verification") ?? receipts.FirstOrDefault(item => item.Status == "not_attempted");
                    if (failed != null)
                    {
                        failed.Status = "failed";
                        failed.Error = ex.Message;
                    }
                    return VRCForgeToolResult.Failed($"Add wardrobe outfit failed after mutation: {ex.Message}", new
                    {
                        ok = false,
                        committed = true,
                        commitState = "unknown",
                        checkpointRecoveryRequired = true,
                        avatarPath = recoveryAvatarPath,
                        parameterName = recoveryParameterName,
                        clipPath = recoveryClipPath,
                        continuationConsumed,
                        transaction = BuildTransaction(receipts, transactionHandle)
                    });
                }
                return VRCForgeToolResult.Failed($"Add wardrobe outfit failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static AnimationClip VerifyClipReadback(string path, IEnumerable<string> onObjects, IEnumerable<string> offObjects)
        {
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
            if (clip == null) throw new InvalidOperationException("Persisted wardrobe clip is missing: " + path);
            var expected = (onObjects ?? Array.Empty<string>()).Select(item => new { path = item, value = 1f })
                .Concat((offObjects ?? Array.Empty<string>()).Select(item => new { path = item, value = 0f }))
                .ToDictionary(item => item.path, item => item.value, StringComparer.Ordinal);
            var bindings = AnimationUtility.GetCurveBindings(clip);
            if (bindings.Length != expected.Count) throw new InvalidOperationException("Persisted wardrobe clip binding count differs.");
            foreach (var binding in bindings)
            {
                if (binding.type != typeof(GameObject) || binding.propertyName != "m_IsActive" || !expected.TryGetValue(binding.path, out var value))
                    throw new InvalidOperationException("Persisted wardrobe clip has an unexpected binding.");
                var curve = AnimationUtility.GetEditorCurve(clip, binding);
                if (curve == null || curve.length == 0 || curve.keys.Any(key => key.time != 0f || key.value != value))
                    throw new InvalidOperationException("Persisted wardrobe clip curve differs for " + binding.path);
            }
            var stable = SceneObjectCopyCore.ReadStableAssetEvidence(path, "wardrobe clip persisted readback");
            var guid = AssetDatabase.AssetPathToGUID(path);
            if (string.IsNullOrWhiteSpace(guid) || stable.Guid != guid) throw new InvalidOperationException("Persisted wardrobe clip GUID differs.");
            return clip;
        }

        private static void VerifyWardrobeFxReadback(AnimatorController controller, string layerName, string stateName,
            string parameterName, int value, bool writeDefaults, AnimationClip clip)
        {
            if (controller == null) throw new InvalidOperationException("Persisted wardrobe FX controller is missing.");
            var layer = controller.layers.SingleOrDefault(item => item.name == layerName);
            if (layer == null) throw new InvalidOperationException("Persisted wardrobe FX layer is missing.");
            var states = FindStatesForReceipt(layer.stateMachine);
            var matches = states.Where(item => item.name == stateName).ToArray();
            if (matches.Length != 1 || matches[0].motion != clip || matches[0].writeDefaultValues != writeDefaults)
                throw new InvalidOperationException("Persisted wardrobe FX state/motion/Write Defaults differs.");
            var transitions = layer.stateMachine.anyStateTransitions.Where(item => item.destinationState == matches[0]).ToArray();
            if (transitions.Length != 1 || transitions[0].hasExitTime || transitions[0].duration != 0f ||
                transitions[0].conditions.Length != 1 || transitions[0].conditions[0].mode != AnimatorConditionMode.Equals ||
                transitions[0].conditions[0].parameter != parameterName || transitions[0].conditions[0].threshold != value)
                throw new InvalidOperationException("Persisted wardrobe FX Equals transition differs.");
            var path = AssetDatabase.GetAssetPath(controller);
            var stable = SceneObjectCopyCore.ReadStableAssetEvidence(path, "wardrobe FX persisted readback");
            if (stable.Guid != AssetDatabase.AssetPathToGUID(path)) throw new InvalidOperationException("Persisted wardrobe FX GUID differs.");
        }

        private static List<AnimatorState> FindStatesForReceipt(AnimatorStateMachine machine)
        {
            var result = new List<AnimatorState>();
            result.AddRange(machine.states.Select(item => item.state));
            foreach (var child in machine.stateMachines) result.AddRange(FindStatesForReceipt(child.stateMachine));
            return result;
        }

        private static void VerifyMenuReadback(VRCExpressionsMenu root, string name, string parameter, int value)
        {
            var found = new List<VRCExpressionsMenu.Control>();
            var seen = new HashSet<VRCExpressionsMenu>();
            Action<VRCExpressionsMenu> visit = null;
            visit = menu =>
            {
                if (menu == null || !seen.Add(menu)) return;
                foreach (var control in menu.controls ?? new List<VRCExpressionsMenu.Control>())
                {
                    if (control.type == VRCExpressionsMenu.Control.ControlType.Toggle && control.name == name &&
                        control.parameter != null && control.parameter.name == parameter && control.value == value) found.Add(control);
                    if (control.subMenu != null) visit(control.subMenu);
                }
            };
            visit(root);
            if (found.Count != 1) throw new InvalidOperationException("Persisted wardrobe menu toggle/value differs.");
        }

        private static object VerifySceneDefaultReadback(Dictionary<string, TransactionReceipt> receipts, Dictionary<string, long> objectIds, Dictionary<string, PrefabSceneDefaultIdentity> prefabObjectIds, string scenePath)
        {
            if (receipts == null || receipts.Count == 0) return null;
            foreach (var item in receipts)
            {
                var receipt = item.Value;
                if (receipt.After == null || JObject.FromObject(receipt.After)["activeSelf"].Value<bool>())
                    throw new InvalidOperationException("Wardrobe scene default-off state differs: " + item.Key);
            }
            var sceneGuid = AssetDatabase.AssetPathToGUID(scenePath);
            var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(scenePath, "wardrobe scene default persisted readback", (path, meta) =>
            {
                var yaml = File.ReadAllText(path);
                foreach (var item in objectIds)
                {
                    var blocks = System.Text.RegularExpressions.Regex.Matches(yaml,
                        @"(?m)^--- !u!1 &" + item.Value.ToString(System.Globalization.CultureInfo.InvariantCulture) + @"\r?\n(?<body>[\s\S]*?)(?=^--- !u!|\z)");
                    if (blocks.Count != 1 || System.Text.RegularExpressions.Regex.Matches(blocks[0].Groups["body"].Value, @"(?m)^  m_IsActive: 0\s*$").Count != 1)
                        throw new InvalidOperationException("Persisted wardrobe scene default-off YAML differs: " + item.Key);
                }
                foreach (var item in prefabObjectIds)
                {
                    var identity = item.Value;
                    var blocks = System.Text.RegularExpressions.Regex.Matches(yaml,
                        @"(?m)^--- !u!1001 &" + identity.instanceRootFileId.ToString(System.Globalization.CultureInfo.InvariantCulture) + @"\r?\n(?<body>[\s\S]*?)(?=^--- !u!|\z)");
                    if (blocks.Count != 1)
                        throw new InvalidOperationException("Persisted wardrobe prefab instance block is missing or ambiguous: " + item.Key);
                    var body = blocks[0].Groups["body"].Value;
                    var sourcePattern = @"(?m)^\s*m_SourcePrefab: \{fileID:\s*100100000,\s*guid:\s*" + System.Text.RegularExpressions.Regex.Escape(identity.sourceGuid) + @"[,}]";
                    if (!System.Text.RegularExpressions.Regex.IsMatch(body, sourcePattern))
                        throw new InvalidOperationException("Persisted wardrobe prefab source identity differs: " + item.Key);
                    var modifications = System.Text.RegularExpressions.Regex.Matches(body,
                        @"(?ms)^\s*- target: \{fileID:\s*" + identity.sourceFileId.ToString(System.Globalization.CultureInfo.InvariantCulture)
                        + @",\s*guid:\s*" + System.Text.RegularExpressions.Regex.Escape(identity.sourceGuid)
                        + @",\s*type:\s*\d+\}\r?\n(?<fields>(?:(?!^\s*- target:)[\s\S])*)");
                    var matching = modifications.Cast<System.Text.RegularExpressions.Match>().Count(match =>
                        System.Text.RegularExpressions.Regex.IsMatch(match.Groups["fields"].Value,
                            @"(?m)^\s+propertyPath: m_IsActive\s*$[\r\n]+\s+value: 0\s*$"));
                    if (matching != 1)
                        throw new InvalidOperationException("Persisted wardrobe prefab m_IsActive override differs: " + item.Key);
                }
            });
            if (evidence.Guid != sceneGuid) throw new InvalidOperationException("Wardrobe scene GUID differs.");
            return new { path = scenePath, assetGuid = evidence.Guid, fileDigest = evidence.File.Digest, defaults = receipts.Keys.ToArray() };
        }

        private static object DescribeAsset(UnityEngine.Object asset)
        {
            return asset == null
                ? (object)new { exists = false }
                : new
                {
                    exists = true,
                    assetPath = AssetDatabase.GetAssetPath(asset),
                    value = JToken.Parse(EditorJsonUtility.ToJson(asset))
                };
        }

        private static object BuildTransaction(List<TransactionReceipt> receipts, string transactionHandle)
        {
            var transactionItems = receipts
                .Where(item => item.Status != "not_attempted")
                .Select(item => new
                {
                    asset = item.Asset,
                    before = item.Before,
                    after = item.After,
                    status = item.Status,
                    error = item.Error,
                    rolled_back = item.RolledBack
                })
                .ToList();
            return new
            {
                assets_touched = transactionItems.Count,
                items = transactionItems.Take(20).ToArray(),
                handle = transactionHandle
            };
        }

        private sealed class PrefabSceneDefaultIdentity
        {
            public ulong instanceRootFileId;
            public long sourceFileId;
            public string sourceGuid = "";
        }

        private sealed class TransactionReceipt
        {
            public string Asset = "";
            public object Before;
            public object After;
            public string Status = "not_attempted";
            public string Error = "";
            public bool RolledBack = false;
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
                if (layer == null || layer.stateMachine == null)
                {
                    continue;
                }
                if (LayerHasEquals(layer.stateMachine, parameterName))
                {
                    if (selected >= 0)
                        throw new InvalidOperationException("Multiple FX layers match the wardrobe parameter; select an unambiguous wardrobe before adding an outfit.");
                    selected = i;
                }
            }
            return selected;
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
            string plannedSubMenuPath,
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
                return CreateOverflowSubMenu(existingHome.menu, parameterName, subMenuName, assetDir, plannedSubMenuPath, true, out appliedMenuPath);
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

            return CreateOverflowSubMenu(rootMenu, parameterName, subMenuName, assetDir, plannedSubMenuPath, true, out appliedMenuPath);
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
            string plannedSubMenuPath,
            bool splitFullOwner,
            out string appliedMenuPath)
        {
            appliedMenuPath = "";
            if (owner == null)
            {
                return null;
            }

            AvatarAuthoringCrudCore.EnsureAssetFolder(assetDir);
            var subMenu = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
            subMenu.controls = new List<VRCExpressionsMenu.Control>();
            var subPath = GeneratedAssetPaths.ValidateNewAssetPath(plannedSubMenuPath);
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
