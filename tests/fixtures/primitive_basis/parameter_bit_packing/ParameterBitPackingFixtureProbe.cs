using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;
using VRC.SDKBase;
using VRCForge.Editor;

public static class ParameterBitPackingFixtureProbe
{
    private const string ProbeFolder = "Assets/VRCForge/Generated/ParameterBitPackingProbe";
    private const string ScenePath = ProbeFolder + "/ParameterFixture.unity";
    private const string ParamsPath = ProbeFolder + "/Parameters.asset";
    private const string MenuRootPath = ProbeFolder + "/MenuRoot.asset";
    private const string FxControllerPath = ProbeFolder + "/FixtureFX.controller";
    private const string DirtyGuardAssetPath = "Assets/VRCForge/ParameterBitPackingUnrelatedGuard.anim";
    private const string GeneratedBuildRoot = "Packages/com.vrcfury.temp/Builds";
    private const string CacheSeedRoot = GeneratedBuildRoot + "/Fixture Baseline";
    private const string CacheSeedAssetPath = CacheSeedRoot + "/Baseline.anim";
    private const string OutputCloneName = "Packed Clone";
    private const string OutputSceneName = "VRCForge Parameter Build - Packed Clone";
    private const string TemporaryOutputRoot = GeneratedBuildRoot + "/" + OutputCloneName;
    private const string DurableOutputRoot = "Assets/VRCForge/Generated/ParameterBitPacking/" + OutputCloneName;
    private const string OutputPrefabPath = DurableOutputRoot + "/" + OutputCloneName + ".prefab";
    private const int SafeToggleCount = 260;

    public static void Run()
    {
        try
        {
            Require(Application.platform == RuntimePlatform.WindowsEditor, "fixture requires Windows editor");
            CleanupBestEffort();
            EnsureGeneratedRootEmpty();
            SeedGeneratedCache();
            var cacheBaseline = CaptureCacheReceipt();
            Require(cacheBaseline.EntryCount > 0 && cacheBaseline.ByteCount > 0, "fixture cache baseline nonempty");
            var descriptor = CreateSourceFixture(includeInvalidBuildFeature: false);
            var sourceSceneDigest = Sha256(AbsoluteProjectPath(ScenePath));
            var sourceParamsDigest = Sha256(AbsoluteProjectPath(ParamsPath));
            var sourceMenuDigest = Sha256(AbsoluteProjectPath(MenuRootPath));

            VerifyDirtyRegisteredAssetBlocksPreview(
                cacheBaseline,
                sourceSceneDigest,
                sourceParamsDigest,
                sourceMenuDigest
            );
            var previewResponse = JObject.FromObject(ParameterBitPackingTool.HandleCommand(PreviewRequest()));
            var preview = RequireSuccess(previewResponse);
            Require((bool)preview["preview"], "preview flag");
            Require(!(bool)preview["changed"], "preview changed");
            Require(!(bool)preview["callbacksInvoked"], "preview invoked callbacks");
            Require(!(bool)preview["mutationStarted"], "preview mutation");
            Require((int)preview["source"]["sourceCostBits"] == SafeToggleCount, "preview source cost");
            Require((int)preview["source"]["safeCandidateNames"].Count() == SafeToggleCount, "preview safe candidates");
            VerifyDangerousExclusions((JArray)preview["source"]["excludedParameters"]);
            Require(Sha256(AbsoluteProjectPath(ScenePath)) == sourceSceneDigest, "preview scene bytes");
            Require(Sha256(AbsoluteProjectPath(ParamsPath)) == sourceParamsDigest, "preview parameter bytes");
            Require(Sha256(AbsoluteProjectPath(MenuRootPath)) == sourceMenuDigest, "preview menu bytes");
            Require(CaptureCacheReceipt().Digest == cacheBaseline.Digest, "preview cache changed");
            Require(!IsSceneLoaded(OutputSceneName), "preview output scene");

            VerifyDirtyRegisteredAssetBlocksApprovedApply(
                preview,
                cacheBaseline,
                sourceSceneDigest,
                sourceParamsDigest,
                sourceMenuDigest
            );
            var applyResponse = JObject.FromObject(
                ParameterBitPackingTool.HandleCommand(BuildApprovedRequest(preview))
            );
            var apply = RequireSuccess(applyResponse);
            Require(!(bool)apply["preview"], "apply preview flag");
            Require((bool)apply["verified"], "apply verified");
            Require((bool)apply["callbacksInvoked"], "apply callbacks");
            Require((int)apply["costAfterBits"] < (int)apply["costBeforeBits"], "apply cost reduction");
            Require((int)apply["costAfterBits"] <= 256, "apply parameter budget");
            Require((int)apply["compressedParameterNames"].Count() > 0, "apply compressed names");
            Require((bool)apply["sourceUnchanged"], "apply source receipt");
            Require(!(bool)apply["sourceSceneDirtyAfter"], "apply source scene dirty");
            Require((bool)apply["cleanupVerified"], "apply cleanup receipt");
            Require(!(bool)apply["sceneLoadedAfter"], "apply scene cleanup");
            Require(!(bool)apply["temporaryObjectResidue"], "apply object cleanup");
            Require((string)apply["output"]["sceneName"] == OutputSceneName, "apply output scene");
            Require(!(bool)apply["output"]["scenePersistent"], "apply output persistence");
            Require((string)apply["output"]["prefabPath"] == OutputPrefabPath, "apply output prefab path");
            Require((bool)apply["output"]["prefabPersistent"], "apply output prefab persistence");
            Require((bool)apply["output"]["prefabExistsAfter"], "apply output prefab existence");
            Require(!string.IsNullOrWhiteSpace((string)apply["output"]["prefabGuid"]), "apply output prefab guid");
            Require(!string.IsNullOrWhiteSpace((string)apply["output"]["prefabFileDigest"]), "apply output prefab digest");
            Require(!string.IsNullOrWhiteSpace((string)apply["output"]["prefabMetaDigest"]), "apply output prefab meta digest");
            var persistedPrefab = AssetDatabase.LoadAssetAtPath<GameObject>(OutputPrefabPath);
            Require(persistedPrefab != null && persistedPrefab.GetComponent<VRCAvatarDescriptor>() != null, "apply persisted prefab readback");
            Require(!(bool)apply["output"]["sceneLoadedAfter"], "apply output scene loaded receipt");
            Require(!(bool)apply["output"]["temporaryObjectResidue"], "apply output object residue receipt");
            Require(!IsSceneLoaded(OutputSceneName), "apply returned with temporary scene loaded");
            Require((bool)apply["generated"]["cacheRestored"], "apply cache restored");
            Require((bool)apply["generated"]["journalClosed"], "apply cache journal closed");
            Require((int)apply["generated"]["addedEntryCount"] == 0, "apply generated additions");
            Require((int)apply["generated"]["removedEntryCount"] == 0, "apply generated removal");
            Require((string)apply["generated"]["contentDigestBefore"] == (string)apply["generated"]["contentDigestAfter"], "apply cache content receipt");
            Require(CaptureCacheReceipt().Digest == cacheBaseline.Digest, "apply cache bytes changed");
            VerifyDangerousExclusions((JArray)apply["excludedParameters"]);
            VerifyBehaviorAndMigrationProof(preview, apply);
            VerifySourceUnchanged(descriptor, sourceSceneDigest, sourceParamsDigest, sourceMenuDigest);
            Require(!string.IsNullOrWhiteSpace((string)apply["applyReceiptDigest"]), "apply receipt digest");

            VerifyDurableOutputAfterApprovedApply(cacheBaseline);
            VerifyNoResidueAfterFailure(cacheBaseline);
            CleanupBestEffort();
            EnsureGeneratedRootEmpty();
            Require(!Directory.EnumerateFileSystemEntries(AbsoluteProjectPath(GeneratedBuildRoot)).Any(), "fixture final cache cleanup");
            Debug.Log("VRCFORGE_PARAMETER_BIT_PACKING_PROBE_OK");
            EditorApplication.Exit(0);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            CleanupBestEffort();
            try { EnsureGeneratedRootEmpty(); } catch { }
            EditorApplication.Exit(1);
        }
    }

    public static void LogCapabilityRoster()
    {
        try
        {
            var assembly = AppDomain.CurrentDomain.GetAssemblies()
                .Single(value => value.GetName().Name == "VRCFury-Editor-Avatars");
            Debug.Log("VRCFORGE_PARAMETER_CALLBACK_ASSEMBLY_SHA256=" + Sha256(assembly.Location));
            var roster = UnityEditor.TypeCache.GetTypesDerivedFrom<VRC.SDKBase.Editor.BuildPipeline.IVRCSDKPreprocessAvatarCallback>()
                .Where(type => !type.IsAbstract)
                .Select(type => type.Assembly.GetName().Name + ":" + type.FullName)
                .OrderBy(value => value, StringComparer.Ordinal)
                .ToArray();
            Debug.Log("VRCFORGE_PARAMETER_CALLBACK_ROSTER=" + string.Join("|", roster));
            var packageRoot = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Packages", "com.vrcfury.vrcfury"));
            var captureTree = typeof(ParameterBitPackingTool).GetMethod("CapturePackageTree", BindingFlags.NonPublic | BindingFlags.Static);
            Require(captureTree != null, "package tree capture method");
            var tree = captureTree.Invoke(null, new object[] { packageRoot });
            var treeType = tree.GetType();
            var digest = treeType.GetField("Digest", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(tree);
            var count = treeType.GetField("EntryCount", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(tree);
            Debug.Log("VRCFORGE_PARAMETER_PACKAGE_TREE=" + count + ":" + digest);
            Debug.Log("VRCFORGE_PARAMETER_CAPABILITY_LOG_OK");
            EditorApplication.Exit(0);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            EditorApplication.Exit(1);
        }
    }

    private static VRCAvatarDescriptor CreateSourceFixture(bool includeInvalidBuildFeature)
    {
        EnsureFolder(ProbeFolder);
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var avatar = new GameObject("Avatar");
        var descriptor = avatar.AddComponent<VRCAvatarDescriptor>();
        avatar.AddComponent<Animator>();
        descriptor.baseAnimationLayers = new[]
        {
            DefaultLayer(VRCAvatarDescriptor.AnimLayerType.Base),
            DefaultLayer(VRCAvatarDescriptor.AnimLayerType.Additive),
            DefaultLayer(VRCAvatarDescriptor.AnimLayerType.Gesture),
            DefaultLayer(VRCAvatarDescriptor.AnimLayerType.Action),
            DefaultLayer(VRCAvatarDescriptor.AnimLayerType.FX)
        };
        descriptor.specialAnimationLayers = new[]
        {
            DefaultLayer(VRCAvatarDescriptor.AnimLayerType.Sitting),
            DefaultLayer(VRCAvatarDescriptor.AnimLayerType.TPose),
            DefaultLayer(VRCAvatarDescriptor.AnimLayerType.IKPose)
        };

        var parameters = ScriptableObject.CreateInstance<VRCExpressionParameters>();
        var rows = new List<VRCExpressionParameters.Parameter>();
        for (var index = 0; index < SafeToggleCount; index++)
        {
            rows.Add(
                Parameter(
                    "SafeToggle" + index.ToString("000"),
                    VRCExpressionParameters.ValueType.Bool,
                    networkSynced: true
                )
            );
        }
        rows.Add(Parameter("FT/JawOpen", VRCExpressionParameters.ValueType.Float, networkSynced: false));
        rows.Add(Parameter("Puppet/X", VRCExpressionParameters.ValueType.Float, networkSynced: false));
        rows.Add(Parameter("OSC/Raw", VRCExpressionParameters.ValueType.Int, networkSynced: false));
        parameters.parameters = rows.ToArray();
        AssetDatabase.CreateAsset(parameters, ParamsPath);

        var menuRoot = CreateMenuTree();
        descriptor.customExpressions = true;
        descriptor.expressionParameters = parameters;
        descriptor.expressionsMenu = menuRoot;
        var layersWithFx = descriptor.baseAnimationLayers.ToArray();
        var fxLayerIndex = Array.FindIndex(layersWithFx, layer => layer.type == VRCAvatarDescriptor.AnimLayerType.FX);
        Require(fxLayerIndex >= 0, "fixture FX layer");
        var fxLayer = layersWithFx[fxLayerIndex];
        fxLayer.isDefault = false;
        fxLayer.animatorController = CreateFxController();
        layersWithFx[fxLayerIndex] = fxLayer;
        descriptor.customizeAnimationLayers = true;
        descriptor.baseAnimationLayers = layersWithFx;
        CreatePublicPackageToggle(avatar);
        var dirtyGuard = new AnimationClip { name = "Unrelated Dirty Guard", frameRate = 30f };
        AssetDatabase.CreateAsset(dirtyGuard, DirtyGuardAssetPath);
        if (includeInvalidBuildFeature)
        {
            var sourceFx = descriptor.baseAnimationLayers
                .Single(layer => layer.type == VRCAvatarDescriptor.AnimLayerType.FX)
                .animatorController;
            var unsupported = new AnimatorOverrideController(sourceFx) { name = "Unsupported Controller" };
            AssetDatabase.CreateAsset(unsupported, ProbeFolder + "/UnsupportedController.asset");
            var layers = descriptor.baseAnimationLayers.ToArray();
            var fxIndex = Array.FindIndex(layers, layer => layer.type == VRCAvatarDescriptor.AnimLayerType.FX);
            Require(fxIndex >= 0, "fixture FX layer");
            var fx = layers[fxIndex];
            fx.isDefault = false;
            fx.animatorController = unsupported;
            layers[fxIndex] = fx;
            descriptor.customizeAnimationLayers = true;
            descriptor.baseAnimationLayers = layers;
        }

        Require(EditorSceneManager.SaveScene(scene, ScenePath), "source scene save");
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        Require(!scene.isDirty, "source scene clean");
        return descriptor;
    }

    private static VRCAvatarDescriptor.CustomAnimLayer DefaultLayer(VRCAvatarDescriptor.AnimLayerType type)
    {
        return new VRCAvatarDescriptor.CustomAnimLayer
        {
            type = type,
            isDefault = true,
            animatorController = null
        };
    }

    private static AnimatorController CreateFxController()
    {
        var controller = AnimatorController.CreateAnimatorControllerAtPath(FxControllerPath);
        Require(controller != null, "fixture FX controller create");
        controller.AddParameter("SafeToggle000", AnimatorControllerParameterType.Bool);
        controller.AddParameter("SafeToggle001", AnimatorControllerParameterType.Bool);
        controller.AddParameter("FT/JawOpen", AnimatorControllerParameterType.Float);
        var machine = controller.layers[0].stateMachine;
        var idle = machine.AddState("Idle");
        var active = machine.AddState("Active");
        machine.defaultState = idle;
        var transition = idle.AddTransition(active);
        transition.hasExitTime = false;
        transition.AddCondition(AnimatorConditionMode.If, 0f, "SafeToggle000");
        var driver = active.AddStateMachineBehaviour<VRCAvatarParameterDriver>();
        driver.parameters = new List<VRC_AvatarParameterDriver.Parameter>
        {
            new VRC_AvatarParameterDriver.Parameter
            {
                source = "SafeToggle000",
                name = "SafeToggle001",
                type = VRC_AvatarParameterDriver.ChangeType.Copy
            }
        };
        EditorUtility.SetDirty(controller);
        return controller;
    }

    private static VRCExpressionsMenu CreateMenuTree()
    {
        var root = CreateMenu(MenuRootPath);
        var safeIndex = 0;
        for (var groupIndex = 0; groupIndex < 5; groupIndex++)
        {
            var groupPath = ProbeFolder + "/MenuGroup" + groupIndex + ".asset";
            var group = CreateMenu(groupPath);
            root.controls.Add(Submenu("Group " + groupIndex, group));
            for (var leafIndex = 0; leafIndex < 7; leafIndex++)
            {
                var leafPath = ProbeFolder + "/MenuLeaf" + groupIndex + "_" + leafIndex + ".asset";
                var leaf = CreateMenu(leafPath);
                group.controls.Add(Submenu("Leaf " + leafIndex, leaf));
                for (var slot = 0; slot < 8 && safeIndex < SafeToggleCount; slot++)
                {
                    leaf.controls.Add(Toggle("Safe " + safeIndex, "SafeToggle" + safeIndex.ToString("000")));
                    safeIndex++;
                }
            }
        }
        Require(safeIndex == SafeToggleCount, "menu safe toggle coverage");
        var puppetLeaf = AssetDatabase.LoadAssetAtPath<VRCExpressionsMenu>(ProbeFolder + "/MenuLeaf4_6.asset");
        Require(puppetLeaf != null && puppetLeaf.controls.Count < 8, "puppet leaf capacity");
        puppetLeaf.controls.Add(
            new VRCExpressionsMenu.Control
            {
                name = "Excluded Puppet",
                type = VRCExpressionsMenu.Control.ControlType.RadialPuppet,
                subParameters = new[]
                {
                    new VRCExpressionsMenu.Control.Parameter { name = "Puppet/X" }
                }
            }
        );
        EditorUtility.SetDirty(puppetLeaf);
        return root;
    }

    private static VRCExpressionsMenu CreateMenu(string path)
    {
        var menu = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
        menu.controls = new List<VRCExpressionsMenu.Control>();
        AssetDatabase.CreateAsset(menu, path);
        return menu;
    }

    private static VRCExpressionsMenu.Control Submenu(string name, VRCExpressionsMenu menu)
    {
        return new VRCExpressionsMenu.Control
        {
            name = name,
            type = VRCExpressionsMenu.Control.ControlType.SubMenu,
            subMenu = menu
        };
    }

    private static VRCExpressionsMenu.Control Toggle(string label, string parameter)
    {
        return new VRCExpressionsMenu.Control
        {
            name = label,
            type = VRCExpressionsMenu.Control.ControlType.Toggle,
            parameter = new VRCExpressionsMenu.Control.Parameter { name = parameter },
            value = 1f
        };
    }

    private static VRCExpressionParameters.Parameter Parameter(
        string name,
        VRCExpressionParameters.ValueType type,
        bool networkSynced)
    {
        var parameter = new VRCExpressionParameters.Parameter
        {
            name = name,
            valueType = type,
            defaultValue = 0,
            saved = false
        };
        SetNetworkSynced(parameter, networkSynced);
        return parameter;
    }

    private static void SetNetworkSynced(VRCExpressionParameters.Parameter parameter, bool networkSynced)
    {
        var field = typeof(VRCExpressionParameters.Parameter).GetField(
            "networkSynced",
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance
        );
        Require(field != null && field.FieldType == typeof(bool), "networkSynced field");
        field.SetValue(parameter, networkSynced);
    }

    private static void CreatePublicPackageToggle(GameObject avatar)
    {
        var type = RequirePublicApiType("com.vrcfury.api.FuryComponents");
        var create = type.GetMethod("CreateToggle", BindingFlags.Public | BindingFlags.Static);
        Require(create != null, "public toggle factory");
        var toggle = create.Invoke(null, new object[] { avatar });
        Require(toggle != null, "public toggle result");
        var setMenuPath = toggle.GetType().GetMethod("SetMenuPath", BindingFlags.Public | BindingFlags.Instance);
        Require(setMenuPath != null, "public toggle menu method");
        setMenuPath.Invoke(toggle, new object[] { "Fixture/Build Marker" });
    }

    private static Type RequirePublicApiType(string fullName)
    {
        var type = AppDomain.CurrentDomain.GetAssemblies()
            .Where(assembly => assembly.GetName().Name == "com.vrcfury.api")
            .Select(assembly => assembly.GetType(fullName, false))
            .SingleOrDefault(value => value != null);
        Require(type != null && type.IsPublic, "public package API type");
        return type;
    }

    private static JObject PreviewRequest()
    {
        return new JObject
        {
            ["sourceScenePath"] = ScenePath,
            ["sourceAvatarPath"] = "Avatar",
            ["outputCloneName"] = OutputCloneName,
            ["preview"] = true,
            ["runBuildCallbacks"] = false,
            ["saveScene"] = false
        };
    }

    private static JObject BuildApprovedRequest(JObject preview)
    {
        var source = (JObject)preview["source"];
        var capability = (JObject)preview["capability"];
        var generated = (JObject)preview["generated"];
        var output = (JObject)preview["output"];
        return new JObject
        {
            ["sourceScenePath"] = ScenePath,
            ["sourceAvatarPath"] = "Avatar",
            ["outputCloneName"] = OutputCloneName,
            ["preview"] = false,
            ["runBuildCallbacks"] = true,
            ["saveScene"] = false,
            ["expectedProjectPath"] = (string)preview["projectPath"],
            ["expectedSourceSceneGuid"] = source["sceneGuid"],
            ["expectedSourceSceneFileDigest"] = source["sceneFileDigest"],
            ["expectedSourceSceneMetaDigest"] = source["sceneMetaDigest"],
            ["expectedSourceGlobalObjectId"] = source["globalObjectId"],
            ["expectedSourceHierarchyDigest"] = source["hierarchyDigest"],
            ["expectedSourceStateDigest"] = source["sourceStateDigest"],
            ["expectedSourceAssetSetDigest"] = source["sourceAssetSetDigest"],
            ["expectedSourceAssetCount"] = source["sourceAssetCount"],
            ["expectedParameterStateDigest"] = source["parameterStateDigest"],
            ["expectedControllerStateDigest"] = source["controllerStateDigest"],
            ["expectedMenuStateDigest"] = source["menuStateDigest"],
            ["expectedSourceBehaviorEvidenceDigest"] = source["behaviorEvidence"]["receiptDigest"],
            ["expectedSourceCostBits"] = source["sourceCostBits"],
            ["expectedParameterCount"] = source["parameterCount"],
            ["expectedSafeCandidateDigest"] = source["safeCandidateDigest"],
            ["expectedSafeCandidateCount"] = source["safeCandidateNames"].Count(),
            ["expectedExcludedDigest"] = source["excludedDigest"],
            ["expectedExcludedCount"] = source["excludedParameters"].Count(),
            ["expectedCapabilityDigest"] = capability["capabilityDigest"],
            ["expectedPackageRootIdentityDigest"] = capability["packageRootIdentityDigest"],
            ["expectedRootIdentityDigest"] = generated["rootIdentityDigestBefore"],
            ["expectedRootIdentityCount"] = generated["rootIdentityCountBefore"],
            ["expectedGeneratedTreeDigestBefore"] = generated["treeDigestBefore"],
            ["expectedGeneratedEntryCountBefore"] = generated["entryCountBefore"],
            ["expectedGeneratedContentDigestBefore"] = generated["contentDigestBefore"],
            ["expectedGeneratedByteCountBefore"] = generated["byteCountBefore"],
            ["expectedPreferenceDigest"] = preview["preferences"]["receiptDigest"],
            ["expectedProtectedTreeDigestBefore"] = generated["protectedTreeDigestBefore"],
            ["expectedProtectedEntryCountBefore"] = generated["protectedEntryCountBefore"],
            ["expectedOutputSceneName"] = output["sceneName"],
            ["expectedOutputPrefabPath"] = output["prefabPath"],
            ["expectedOutputTreeDigestBefore"] = output["treeDigestBefore"],
            ["expectedOutputEntryCountBefore"] = output["entryCountBefore"],
            ["expectedOutputRootExistsBefore"] = output["rootExistsBefore"],
            ["expectedPreviewDigest"] = preview["previewDigest"]
        };
    }

    private static JObject RequireSuccess(JObject response)
    {
        Require((bool)response["success"], "tool returned an error response: " + response.ToString(Newtonsoft.Json.Formatting.None));
        return (JObject)response["data"];
    }

    private static void VerifyDangerousExclusions(JArray excluded)
    {
        var byName = excluded.Cast<JObject>().ToDictionary(value => (string)value["name"], StringComparer.Ordinal);
        foreach (var name in new[] { "FT/JawOpen", "Puppet/X", "OSC/Raw" })
        {
            Require(byName.ContainsKey(name), "dangerous exclusion missing");
            Require(!(bool)byName[name]["networkSynced"], "dangerous exclusion sync drift");
        }
        Require(byName["FT/JawOpen"]["reasons"].Values<string>().Contains("face_tracking"), "face-tracking reason");
        Require(byName["Puppet/X"]["reasons"].Values<string>().Contains("puppet"), "puppet reason");
        Require(byName["OSC/Raw"]["reasons"].Values<string>().Contains("osc_or_unmapped"), "OSC reason");
    }

    private static void VerifySourceUnchanged(
        VRCAvatarDescriptor descriptor,
        string sceneDigest,
        string paramsDigest,
        string menuDigest)
    {
        Require(descriptor != null, "source descriptor exists");
        Require(Sha256(AbsoluteProjectPath(ScenePath)) == sceneDigest, "source scene changed");
        Require(Sha256(AbsoluteProjectPath(ParamsPath)) == paramsDigest, "source params changed");
        Require(Sha256(AbsoluteProjectPath(MenuRootPath)) == menuDigest, "source menu changed");
        var byName = descriptor.expressionParameters.parameters.ToDictionary(parameter => parameter.name, StringComparer.Ordinal);
        foreach (var name in new[] { "FT/JawOpen", "Puppet/X", "OSC/Raw" })
        {
            Require(!ReadNetworkSynced(byName[name]), "source dangerous parameter changed");
        }
    }

    private static void VerifyDirtyRegisteredAssetBlocksPreview(
        CacheReceipt cacheBaseline,
        string sceneDigest,
        string paramsDigest,
        string menuDigest)
    {
        var response = InvokeWithUnrelatedDirtyAsset(() =>
            JObject.FromObject(ParameterBitPackingTool.HandleCommand(PreviewRequest())));
        Require(!(bool)response["success"], "dirty registered asset preview unexpectedly succeeded");
        VerifyDirtyGuardNoResidue(cacheBaseline, sceneDigest, paramsDigest, menuDigest, "dirty preview");
    }

    private static void VerifyDirtyRegisteredAssetBlocksApprovedApply(
        JObject preview,
        CacheReceipt cacheBaseline,
        string sceneDigest,
        string paramsDigest,
        string menuDigest)
    {
        var response = InvokeWithUnrelatedDirtyAsset(() =>
            JObject.FromObject(ParameterBitPackingTool.HandleCommand(BuildApprovedRequest(preview))));
        Require(!(bool)response["success"], "post-preview dirty registered asset apply unexpectedly succeeded");
        VerifyDirtyGuardNoResidue(cacheBaseline, sceneDigest, paramsDigest, menuDigest, "dirty apply");
    }

    private static JObject InvokeWithUnrelatedDirtyAsset(Func<JObject> invoke)
    {
        var asset = AssetDatabase.LoadAssetAtPath<AnimationClip>(DirtyGuardAssetPath);
        Require(asset != null && !EditorUtility.IsDirty(asset), "dirty guard asset clean precondition");
        var diskDigest = Sha256(AbsoluteProjectPath(DirtyGuardAssetPath));
        var originalFrameRate = asset.frameRate;
        try
        {
            asset.frameRate = originalFrameRate + 17f;
            EditorUtility.SetDirty(asset);
            Require(EditorUtility.IsDirty(asset), "dirty guard asset mutation");
            var response = invoke();
            Require(Sha256(AbsoluteProjectPath(DirtyGuardAssetPath)) == diskDigest, "dirty guard disk bytes changed");
            return response;
        }
        finally
        {
            asset.frameRate = originalFrameRate;
            EditorUtility.ClearDirty(asset);
            Require(!EditorUtility.IsDirty(asset), "dirty guard asset cleanup");
            Require(Sha256(AbsoluteProjectPath(DirtyGuardAssetPath)) == diskDigest, "dirty guard cleanup changed disk bytes");
        }
    }

    private static void VerifyDirtyGuardNoResidue(
        CacheReceipt cacheBaseline,
        string sceneDigest,
        string paramsDigest,
        string menuDigest,
        string label)
    {
        Require(Sha256(AbsoluteProjectPath(ScenePath)) == sceneDigest, label + " scene bytes");
        Require(Sha256(AbsoluteProjectPath(ParamsPath)) == paramsDigest, label + " parameter bytes");
        Require(Sha256(AbsoluteProjectPath(MenuRootPath)) == menuDigest, label + " menu bytes");
        RequireCacheEquals(cacheBaseline, label + " cache bytes");
        Require(!IsSceneLoaded(OutputSceneName), label + " output scene residue");
        Require(!AssetDatabase.IsValidFolder(TemporaryOutputRoot), label + " temporary output residue");
        Require(!AssetDatabase.IsValidFolder(DurableOutputRoot), label + " durable output residue");
        Require(!AssetDatabase.IsValidFolder(GeneratedBuildRoot + "/VRCForge Input"), label + " staging residue");
        RequireNoActiveCacheTransaction(label + " cache journal residue");
    }

    private static bool ReadNetworkSynced(VRCExpressionParameters.Parameter parameter)
    {
        var field = typeof(VRCExpressionParameters.Parameter).GetField(
            "networkSynced",
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance
        );
        Require(field != null, "networkSynced read field");
        return (bool)field.GetValue(parameter);
    }

    private static void VerifyBehaviorAndMigrationProof(JObject preview, JObject apply)
    {
        var sourceEvidence = (JObject)preview["source"]["behaviorEvidence"];
        var proof = (JObject)apply["behaviorProof"];
        var output = (JObject)apply["output"];
        var managed = (JObject)apply["managedOutput"];
        var staged = (JObject)managed["stagedManifest"];
        var final = (JObject)managed["finalManifest"];
        Require((string)proof["status"] == "verified", "behavior proof status");
        Require((string)proof["platformScope"] == "current-target-only", "behavior proof platform scope");
        Require(!(bool)proof["crossPlatformEquivalent"], "behavior proof platform claim");
        Require((string)proof["sourceOrderedParameterDigest"] == (string)sourceEvidence["orderedParameterDigest"], "behavior source parameter order");
        Require((string)proof["sourceMenuGraphDigest"] == (string)sourceEvidence["menuGraphDigest"], "behavior source menu graph");
        Require((string)proof["sourceAnimatorBehaviorDigest"] == (string)sourceEvidence["animatorBehaviorDigest"], "behavior source animator graph");
        Require((int)sourceEvidence["animatorRowCount"] > 0, "behavior source animator rows");
        Require((int)proof["outputAnimatorRowCount"] > (int)proof["sourceAnimatorRowCount"], "behavior generated animator rows");
        Require((int)proof["codecMappingCount"] == (int)apply["compressedParameterNames"].Count(), "behavior codec mapping count");
        Require((string)proof["excludedBeforeDigest"] == (string)proof["excludedAfterDigest"], "behavior excluded preservation");
        Require((string)output["clonePortableAvatarDigest"] == (string)output["prefabPortableAvatarDigest"], "portable avatar readback");
        Require((string)output["cloneEvidenceDigest"] == (string)output["prefabEvidenceDigest"], "semantic evidence readback");
        Require((string)output["prefabOrderedParameterDigest"] == (string)proof["outputOrderedParameterDigest"], "ordered parameter readback");
        Require((string)output["prefabMenuGraphDigest"] == (string)proof["outputMenuGraphDigest"], "menu graph readback");
        Require((string)output["prefabAnimatorBehaviorDigest"] == (string)proof["outputAnimatorBehaviorDigest"], "animator behavior readback");
        Require((string)output["prefabBehaviorProofDigest"] == (string)proof["receiptDigest"], "behavior proof readback");
        Require((bool)managed["stageSavedBeforeMove"], "prefab staged before move");
        Require((bool)managed["guidPreservingWholeTreeMove"], "GUID-preserving whole-tree move");
        Require((bool)managed["temporaryTreeRemoved"], "temporary output tree removal");
        Require((bool)managed["prefabGuidPreserved"], "prefab GUID preservation");
        Require((string)staged["rootPath"] == TemporaryOutputRoot, "staged manifest root");
        Require((string)final["rootPath"] == DurableOutputRoot, "final manifest root");
        foreach (var key in new[] { "entryCount", "byteCount", "contentDigest", "handleEvidenceDigest", "guidMapDigest", "dependencyGuidDigest" })
        {
            Require(JToken.DeepEquals(staged[key], final[key]), "migration manifest " + key);
        }
        Require(!(bool)staged["noTemporaryReferences"], "staged manifest temporary scope");
        Require((bool)final["noTemporaryReferences"], "final manifest reference closure");
        Require((bool)final["reparseFree"] && (bool)final["singleLink"] && (bool)final["handleHashed"], "final manifest handle proof");
        Require((bool)final["finalEnumerationVerified"], "final manifest enumeration");
        Require((string)apply["platformProof"]["scope"] == "current-target-only", "apply platform scope");
        Require(!(bool)apply["platformProof"]["crossPlatformEquivalent"], "apply platform equivalence");
        Require(!(bool)apply["platformProof"]["localAppDataAccessed"], "apply local app data access");
        Require((bool)apply["preferences"]["readOnly"], "preference read-only receipt");
        Require((int)apply["preferences"]["compressorValue"] == 0, "compressor preference automatic");
        Require(apply["preferences"]["alignMobilePresent"] != null, "mobile alignment preference presence receipt");
        Require(apply["preferences"]["alignMobileValue"] != null, "mobile alignment preference value receipt");
        Require(!AssetDatabase.IsValidFolder(TemporaryOutputRoot), "temporary output tree remains");
        Require(AssetDatabase.IsValidFolder(DurableOutputRoot), "durable output tree missing");
    }

    private static void VerifyNoResidueAfterFailure(CacheReceipt cacheBaseline)
    {
        CleanupBestEffort();
        RequireCacheEquals(cacheBaseline, "failure precondition cache");
        CreateSourceFixture(includeInvalidBuildFeature: true);
        var preview = RequireSuccess(JObject.FromObject(ParameterBitPackingTool.HandleCommand(PreviewRequest())));
        var response = JObject.FromObject(ParameterBitPackingTool.HandleCommand(BuildApprovedRequest(preview)));
        Require(!(bool)response["success"], "invalid public build unexpectedly succeeded");
        var details = response["data"] as JObject;
        Require(details != null && (bool)details["mutationStarted"], "failure mutation state");
        Require((bool)details["restored"], "failure restore state");
        Require(!(bool)details["cleanupRequired"], "failure cleanup state");
        Require(!(bool)details["checkpointRestoreRequired"], "failure checkpoint state");
        Require(!IsSceneLoaded(OutputSceneName), "failure output scene residue");
        Require(!AssetDatabase.IsValidFolder(TemporaryOutputRoot), "failure temporary output residue");
        Require(!AssetDatabase.IsValidFolder(DurableOutputRoot), "failure durable output residue");
        RequireCacheEquals(cacheBaseline, "failure cache restore");
        RequireNoActiveCacheTransaction("failure cache journal residue");
    }

    private static void VerifyDurableOutputAfterApprovedApply(CacheReceipt cacheBaseline)
    {
        Require(!IsSceneLoaded(OutputSceneName), "approved apply temporary scene residue");
        Require(!AssetDatabase.IsValidFolder(TemporaryOutputRoot), "approved apply temporary output residue");
        Require(AssetDatabase.LoadAssetAtPath<GameObject>(OutputPrefabPath) != null, "approved apply durable prefab missing");
        RequireCacheEquals(cacheBaseline, "approved apply cache restore");
        RequireNoActiveCacheTransaction("approved apply cache journal residue");
        Require(AssetDatabase.DeleteAsset(DurableOutputRoot), "approved apply durable output cleanup");
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        Require(AssetDatabase.LoadAssetAtPath<GameObject>(OutputPrefabPath) == null, "approved apply durable prefab cleanup residue");
        RequireCacheEquals(cacheBaseline, "approved apply cleanup cache preservation");
    }

    private static void RequireNoActiveCacheTransaction(string label)
    {
        var transactionRoot = AbsoluteProjectPath("Library/VRCForge/transactions");
        Require(!Directory.Exists(transactionRoot)
            || !Directory.EnumerateDirectories(transactionRoot, "parameter-bit-packing-*", SearchOption.TopDirectoryOnly).Any(),
            label);
    }

    private static void CleanupOutputOnly()
    {
        for (var index = SceneManager.sceneCount - 1; index >= 0; index--)
        {
            var scene = SceneManager.GetSceneAt(index);
            if (scene.name == OutputSceneName) EditorSceneManager.CloseScene(scene, true);
        }
        if (AssetDatabase.IsValidFolder(TemporaryOutputRoot)) AssetDatabase.DeleteAsset(TemporaryOutputRoot);
        if (AssetDatabase.IsValidFolder(DurableOutputRoot)) AssetDatabase.DeleteAsset(DurableOutputRoot);
        if (AssetDatabase.IsValidFolder(GeneratedBuildRoot + "/VRCForge Input")) AssetDatabase.DeleteAsset(GeneratedBuildRoot + "/VRCForge Input");
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
    }

    private static void CleanupBestEffort()
    {
        try
        {
            CleanupOutputOnly();
            var scene = SceneManager.GetSceneByPath(ScenePath);
            if (scene.IsValid() && scene.isLoaded)
            {
                if (SceneManager.sceneCount == 1)
                {
                    EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                }
                else
                {
                    EditorSceneManager.CloseScene(scene, true);
                }
            }
            if (AssetDatabase.LoadMainAssetAtPath(DirtyGuardAssetPath) != null)
            {
                AssetDatabase.DeleteAsset(DirtyGuardAssetPath);
            }
            if (AssetDatabase.IsValidFolder(ProbeFolder)) AssetDatabase.DeleteAsset(ProbeFolder);
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        }
        catch
        {
        }
    }

    private static void SeedGeneratedCache()
    {
        Require(AssetDatabase.IsValidFolder(GeneratedBuildRoot), "generated build root missing");
        if (AssetDatabase.IsValidFolder(CacheSeedRoot)) AssetDatabase.DeleteAsset(CacheSeedRoot);
        Require(!string.IsNullOrWhiteSpace(AssetDatabase.CreateFolder(GeneratedBuildRoot, "Fixture Baseline")), "cache seed folder create");
        var clip = new AnimationClip { name = "Baseline" };
        AssetDatabase.CreateAsset(clip, CacheSeedAssetPath);
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        Require(AssetDatabase.LoadAssetAtPath<AnimationClip>(CacheSeedAssetPath) != null, "cache seed asset readback");
        Require(File.Exists(AbsoluteProjectPath(CacheSeedAssetPath) + ".meta"), "cache seed metadata readback");
    }

    private static CacheReceipt CaptureCacheReceipt()
    {
        var root = AbsoluteProjectPath(GeneratedBuildRoot).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        Require(Directory.Exists(root), "cache receipt root missing");
        var prefix = root + Path.DirectorySeparatorChar;
        var rows = new List<string>();
        var byteCount = 0L;
        foreach (var path in Directory.EnumerateFileSystemEntries(root, "*", SearchOption.AllDirectories)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase))
        {
            var relative = path.Substring(prefix.Length).Replace('\\', '/');
            var attributes = File.GetAttributes(path);
            Require((attributes & FileAttributes.ReparsePoint) == 0, "cache receipt reparse point");
            if ((attributes & FileAttributes.Directory) != 0)
            {
                rows.Add("D|" + relative);
            }
            else
            {
                var length = new FileInfo(path).Length;
                byteCount += length;
                rows.Add("F|" + relative + "|" + length + "|" + Sha256(path));
            }
        }
        return new CacheReceipt
        {
            Digest = Sha256Text(string.Join("\n", rows)),
            EntryCount = rows.Count,
            ByteCount = byteCount
        };
    }

    private static void RequireCacheEquals(CacheReceipt expected, string label)
    {
        var actual = CaptureCacheReceipt();
        Require(actual.Digest == expected.Digest, label + " digest");
        Require(actual.EntryCount == expected.EntryCount, label + " entry count");
        Require(actual.ByteCount == expected.ByteCount, label + " byte count");
    }

    private static void EnsureGeneratedRootEmpty()
    {
        var absolute = AbsoluteProjectPath(GeneratedBuildRoot);
        Require(Directory.Exists(absolute), "generated build root missing");
        foreach (var entry in Directory.EnumerateFileSystemEntries(absolute).ToArray())
        {
            var name = Path.GetFileName(entry);
            if (name.EndsWith(".meta", StringComparison.Ordinal)) name = name.Substring(0, name.Length - 5);
            if (!string.IsNullOrWhiteSpace(name)) AssetDatabase.DeleteAsset(GeneratedBuildRoot + "/" + name);
        }
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        Require(!Directory.EnumerateFileSystemEntries(absolute).Any(), "generated build root cleanup");
    }

    private static void EnsureFolder(string path)
    {
        var parts = path.Split('/');
        var current = parts[0];
        for (var index = 1; index < parts.Length; index++)
        {
            var next = current + "/" + parts[index];
            if (!AssetDatabase.IsValidFolder(next))
            {
                Require(!string.IsNullOrWhiteSpace(AssetDatabase.CreateFolder(current, parts[index])), "folder create");
            }
            current = next;
        }
    }

    private static bool IsSceneLoaded(string name)
    {
        for (var index = 0; index < SceneManager.sceneCount; index++)
        {
            if (SceneManager.GetSceneAt(index).name == name) return true;
        }
        return false;
    }

    private static string AbsoluteProjectPath(string assetPath)
    {
        var project = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
        return Path.GetFullPath(Path.Combine(project, assetPath.Replace('/', Path.DirectorySeparatorChar)));
    }

    private static string Sha256(string path)
    {
        using (var stream = File.OpenRead(path))
        using (var hash = SHA256.Create())
        {
            return BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", string.Empty).ToLowerInvariant();
        }
    }

    private static string Sha256Text(string value)
    {
        using (var hash = SHA256.Create())
        {
            return BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", string.Empty).ToLowerInvariant();
        }
    }

    private sealed class CacheReceipt
    {
        internal string Digest;
        internal int EntryCount;
        internal long ByteCount;
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }
}
