using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;
using VRC.SDK3.Dynamics.Contact.Components;
using VRC.SDK3.Dynamics.PhysBone.Components;
using VRC.SDKBase;
using VRCForge.Editor;

public static class AtomicReferenceRenameFixtureProbe
{
    private const string ProbeFolder = "Assets/VRCForge/Generated/AtomicRenameProbe";
    private const string ScenePath = ProbeFolder + "/AtomicRenameProbe.unity";
    private const string AvatarPath = "Avatar";
    private const string ObjectParentPath = "Avatar/Wardrobe";
    private const string ClipPath = ProbeFolder + "/Motion.anim";
    private const string MaskPath = ProbeFolder + "/Mask.mask";
    private const string ControllerPath = ProbeFolder + "/Controller.controller";
    private const string ParametersPath = ProbeFolder + "/Parameters.asset";
    private const string MenuPath = ProbeFolder + "/Menu.asset";
    private const string MaterialPath = ProbeFolder + "/Reference.mat";
    private const string UnknownPath = ProbeFolder + "/Unknown.txt";
    private const string RawResiduePath = ProbeFolder + "/UnimportedResidue.txt";
    private const string EmptyResiduePath = ProbeFolder + "/UnimportedEmpty";
    private static readonly JObject Evidence = new JObject();

    private static string OldObjectName => string.Concat("Legacy", "Piece");
    private static string NewObjectName => string.Concat("Renamed", "Piece");
    private static string OldParameter => string.Concat("Legacy", "Parameter");
    private static string NewParameter => string.Concat("Renamed", "Parameter");
    private static string TargetObjectPath => ObjectParentPath + "/" + OldObjectName;
    private static string RenamedObjectPath => ObjectParentPath + "/" + NewObjectName;

    public static void Run()
    {
        try
        {
            ResetEvidenceOutput();
            CleanupBestEffort();
            PrepareFixture();
            var baseline = CaptureProjectState();
            VerifyRequestShapeFailsClosed(baseline);
            VerifyPlannedDirtyAssetFailsClosed(baseline);
            VerifyUnapprovedDirtyAssetFailsClosed(baseline);
            VerifyPreexistingUnregisteredReferenceFailsClosed(baseline);
            VerifyObjectLifecycle(baseline);
            VerifyUnknownReferenceFailsClosed(baseline);
            VerifyStaleApprovalFailsClosed(baseline);
            VerifyPartialMutationRestore(baseline);
            VerifyUnapprovedConcurrentWriteRequiresCheckpoint(baseline);
            VerifyPlannedConcurrentWriteIsRejectedAndRestored(baseline);
            VerifyUnregisteredFileSystemResidueRequiresCheckpoint(baseline);
            VerifyParameterLifecycle(baseline);
            VerifySemanticBaseline(baseline, "final baseline");

            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            Require(AssetDatabase.DeleteAsset(ProbeFolder), "probe folder cleanup");
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            Require(!Directory.Exists(AbsoluteAssetPath(ProbeFolder)), "generated folder residue");
            Require(!File.Exists(AbsoluteAssetPath(ProbeFolder) + ".meta"), "generated metadata residue");

            Evidence["schema"] = "vrcforge.atomic_reference_rename_fixture.v1";
            Evidence["projectPath"] = CurrentProjectPath();
            Evidence["cleanupVerified"] = true;
            Evidence["residueCount"] = 0;
            WriteFreshEvidence();
            Debug.Log("VRCFORGE_ATOMIC_REFERENCE_RENAME_PROBE_OK");
            EditorApplication.Exit(0);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            CleanupBestEffort();
            CleanupEvidenceOutputBestEffort();
            EditorApplication.Exit(1);
        }
    }

    private static void PrepareFixture()
    {
        EnsureFolder("Assets/VRCForge", "Generated");
        if (AssetDatabase.IsValidFolder(ProbeFolder))
        {
            Require(AssetDatabase.DeleteAsset(ProbeFolder), "old probe folder cleanup");
        }
        AssetDatabase.CreateFolder("Assets/VRCForge/Generated", "AtomicRenameProbe");

        var material = new Material(Shader.Find("Standard"));
        AssetDatabase.CreateAsset(material, MaterialPath);

        var clip = new AnimationClip { name = "Motion" };
        AssetDatabase.CreateAsset(clip, ClipPath);
        var objectRelative = "Wardrobe/" + OldObjectName;
        var descendantRelative = objectRelative + "/Detail";
        AnimationUtility.SetEditorCurve(
            clip,
            EditorCurveBinding.FloatCurve(objectRelative, typeof(GameObject), "m_IsActive"),
            AnimationCurve.Constant(0f, 1f, 1f));
        AnimationUtility.SetObjectReferenceCurve(
            clip,
            EditorCurveBinding.PPtrCurve(
                descendantRelative,
                typeof(Renderer),
                "m_Materials.Array.data[0]"),
            new[]
            {
                new ObjectReferenceKeyframe { time = 0f, value = material }
            });
        EditorUtility.SetDirty(clip);

        var mask = new AvatarMask { name = "Mask", transformCount = 2 };
        mask.SetTransformPath(0, objectRelative);
        mask.SetTransformActive(0, true);
        mask.SetTransformPath(1, descendantRelative);
        mask.SetTransformActive(1, true);
        AssetDatabase.CreateAsset(mask, MaskPath);

        var controller = AnimatorController.CreateAnimatorControllerAtPath(ControllerPath);
        controller.parameters = new[]
        {
            new AnimatorControllerParameter
            {
                name = OldParameter,
                type = AnimatorControllerParameterType.Float,
                defaultFloat = 0f
            }
        };
        var layer = controller.layers[0];
        layer.avatarMask = mask;
        controller.layers = new[] { layer };
        var stateMachine = controller.layers[0].stateMachine;
        var state = stateMachine.AddState("DrivenState");
        state.motion = clip;
        state.speedParameterActive = true;
        state.speedParameter = OldParameter;
        state.mirrorParameterActive = true;
        state.mirrorParameter = OldParameter;
        state.cycleOffsetParameterActive = true;
        state.cycleOffsetParameter = OldParameter;
        state.timeParameterActive = true;
        state.timeParameter = OldParameter;
        var transition = state.AddTransition(stateMachine.AddState("Destination"));
        transition.AddCondition(AnimatorConditionMode.Greater, 0.25f, OldParameter);

        var blendTree = new BlendTree
        {
            name = "Blend",
            blendType = BlendTreeType.Direct,
            blendParameter = OldParameter,
            blendParameterY = OldParameter
        };
        AssetDatabase.AddObjectToAsset(blendTree, controller);
        blendTree.children = new[]
        {
            new ChildMotion
            {
                motion = clip,
                directBlendParameter = OldParameter,
                threshold = 0f,
                timeScale = 1f
            }
        };
        var blendState = stateMachine.AddState("BlendState");
        blendState.motion = blendTree;

        var driver = state.AddStateMachineBehaviour<VRCAvatarParameterDriver>();
        driver.parameters = new List<VRC_AvatarParameterDriver.Parameter>
        {
            new VRC_AvatarParameterDriver.Parameter
            {
                name = OldParameter,
                source = OldParameter,
                type = VRC_AvatarParameterDriver.ChangeType.Copy
            }
        };
        EditorUtility.SetDirty(controller);

        var definitions = ScriptableObject.CreateInstance<VRCExpressionParameters>();
        definitions.parameters = new[]
        {
            new VRCExpressionParameters.Parameter
            {
                name = OldParameter,
                valueType = VRCExpressionParameters.ValueType.Float,
                defaultValue = 0f,
                saved = true
            }
        };
        AssetDatabase.CreateAsset(definitions, ParametersPath);

        var menu = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
        menu.controls = new List<VRCExpressionsMenu.Control>
        {
            new VRCExpressionsMenu.Control
            {
                name = "Control",
                type = VRCExpressionsMenu.Control.ControlType.TwoAxisPuppet,
                parameter = new VRCExpressionsMenu.Control.Parameter { name = OldParameter },
                subParameters = new[]
                {
                    new VRCExpressionsMenu.Control.Parameter { name = OldParameter }
                }
            }
        };
        AssetDatabase.CreateAsset(menu, MenuPath);
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var avatar = new GameObject("Avatar");
        var wardrobe = AddChild(avatar, "Wardrobe");
        var target = AddChild(wardrobe, OldObjectName);
        AddChild(target, "Detail");
        var dynamics = AddChild(avatar, "Dynamics");
        var contact = dynamics.AddComponent<VRCContactReceiver>();
        contact.parameter = OldParameter;
        var physBone = dynamics.AddComponent<VRCPhysBone>();
        physBone.parameter = OldParameter;
        var registeredHost = AddChild(avatar, "RegisteredFeature");
        CreateRegisteredFeature(registeredHost, OldParameter);
        var registeredListHost = AddChild(avatar, "RegisteredListFeature");
        CreateRegisteredListFeature(registeredListHost, OldParameter);

        var descriptor = avatar.AddComponent<VRCAvatarDescriptor>();
        descriptor.customExpressions = true;
        descriptor.expressionParameters = definitions;
        descriptor.expressionsMenu = menu;
        descriptor.customizeAnimationLayers = true;
        descriptor.baseAnimationLayers = new[]
        {
            new VRCAvatarDescriptor.CustomAnimLayer
            {
                type = VRCAvatarDescriptor.AnimLayerType.FX,
                isDefault = false,
                animatorController = controller
            }
        };
        descriptor.specialAnimationLayers = Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>();
        EditorUtility.SetDirty(avatar);
        Require(EditorSceneManager.SaveScene(scene, ScenePath), "fixture scene save");
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        Require(!scene.isDirty, "fixture scene clean");
        Undo.ClearAll();
    }

    private static void VerifyRequestShapeFailsClosed(ProjectState baseline)
    {
        var request = ObjectRequest(true);
        request["serializedPropertyPath"] = "m_Arbitrary";
        RequireFailure(request, "arbitrary property path accepted");
        VerifyProjectState(baseline, "request shape rejection");
    }

    private static void VerifyObjectLifecycle(ProjectState baseline)
    {
        var previewRequest = ObjectRequest(true);
        var before = CaptureProjectState();
        var preview = RequireSuccess(AtomicReferenceRenameTool.HandleCommand(previewRequest));
        VerifyPreview(preview, "game_object", 5);
        Require(
            preview["references"].Values<JObject>().Any(item =>
                item.Value<string>("kind") == "hierarchy_object"),
            "object hierarchy reference");
        Require(
            preview["references"].Values<JObject>().Count(item =>
                item.Value<string>("kind") == "animation_binding") == 2,
            "object animation references");
        Require(
            preview["references"].Values<JObject>().Count(item =>
                item.Value<string>("kind") == "avatar_mask_transform") == 2,
            "object mask references");
        VerifyProjectState(before, "object preview zero write");

        var apply = RequireSuccess(AtomicReferenceRenameTool.HandleCommand(
            ApprovedRequest(previewRequest, preview)));
        VerifyApply(apply, 5, preview.Value<string>("planDigest"));
        Require(Resolve(RenamedObjectPath) != null, "renamed object readback");
        RequireNoRawToken(OldObjectName, "old object reference after apply");

        var reverseRequest = ObjectRequest(
            true,
            RenamedObjectPath,
            OldObjectName);
        var reversePreview = RequireSuccess(
            AtomicReferenceRenameTool.HandleCommand(reverseRequest));
        var reverseApply = RequireSuccess(AtomicReferenceRenameTool.HandleCommand(
            ApprovedRequest(reverseRequest, reversePreview)));
        VerifyApply(reverseApply, 5, reversePreview.Value<string>("planDigest"));
        VerifyProjectState(baseline, "object reverse restore");
        Evidence["objectRequest"] = previewRequest.DeepClone();
        Evidence["objectPreview"] = preview.DeepClone();
        Evidence["objectApply"] = apply.DeepClone();
    }

    private static void VerifyPlannedDirtyAssetFailsClosed(ProjectState baseline)
    {
        var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(ClipPath);
        Require(clip != null, "planned dirty clip");
        var clipBytes = File.ReadAllBytes(AbsoluteAssetPath(ClipPath));
        var originalFrameRate = clip.frameRate;
        clip.frameRate = originalFrameRate + 1f;
        EditorUtility.SetDirty(clip);
        RequireFailure(ObjectRequest(true), "planned dirty clip accepted");
        Require(EditorUtility.IsDirty(clip), "planned dirty clip state consumed");
        Require(clipBytes.SequenceEqual(File.ReadAllBytes(AbsoluteAssetPath(ClipPath))),
            "planned dirty clip persisted");
        clip.frameRate = originalFrameRate;
        EditorUtility.ClearDirty(clip);

        var controller = AssetDatabase.LoadAssetAtPath<AnimatorController>(ControllerPath);
        Require(controller != null, "planned dirty controller");
        var controllerBytes = File.ReadAllBytes(AbsoluteAssetPath(ControllerPath));
        var originalName = controller.name;
        controller.name = originalName + "Dirty";
        EditorUtility.SetDirty(controller);
        RequireFailure(ParameterRequest(true), "planned dirty controller accepted");
        Require(EditorUtility.IsDirty(controller), "planned dirty controller state consumed");
        Require(controllerBytes.SequenceEqual(File.ReadAllBytes(AbsoluteAssetPath(ControllerPath))),
            "planned dirty controller persisted");
        controller.name = originalName;
        EditorUtility.ClearDirty(controller);

        VerifyProjectState(baseline, "planned dirty asset rejection");
        Evidence["plannedDirtyAssets"] = new JObject
        {
            ["clipRejected"] = true,
            ["controllerRejected"] = true,
            ["diskUnchanged"] = true
        };
    }

    private static void VerifyUnapprovedDirtyAssetFailsClosed(ProjectState baseline)
    {
        var material = AssetDatabase.LoadAssetAtPath<Material>(MaterialPath);
        Require(material != null, "unapproved dirty material");
        var absolute = AbsoluteAssetPath(MaterialPath);
        var beforeBytes = File.ReadAllBytes(absolute);
        var originalQueue = material.renderQueue;
        var approvedRequest = ObjectRequest(true);
        var approvedPreview = RequireSuccess(
            AtomicReferenceRenameTool.HandleCommand(approvedRequest));
        material.renderQueue = originalQueue == 2001 ? 2002 : 2001;
        EditorUtility.SetDirty(material);
        Require(EditorUtility.IsDirty(material), "unapproved asset dirty setup");

        RequireFailure(ObjectRequest(true), "unapproved dirty asset preview accepted");
        RequireFailure(
            ApprovedRequest(approvedRequest, approvedPreview),
            "unapproved dirty asset approved apply accepted");
        Require(beforeBytes.SequenceEqual(File.ReadAllBytes(absolute)),
            "unapproved dirty asset persisted during rejection");
        Require(EditorUtility.IsDirty(material),
            "unapproved dirty asset was consumed during rejection");

        material.renderQueue = originalQueue;
        EditorUtility.ClearDirty(material);
        Require(!EditorUtility.IsDirty(material), "unapproved dirty material reset");
        Require(beforeBytes.SequenceEqual(File.ReadAllBytes(absolute)),
            "unapproved dirty material disk reset");
        VerifyProjectState(baseline, "unapproved dirty asset isolation");
        Evidence["unapprovedDirtyAsset"] = new JObject
        {
            ["previewRejected"] = true,
            ["approvedApplyRejected"] = true,
            ["diskUnchanged"] = true,
            ["dirtyStatePreservedUntilCallerCleanup"] = true
        };
    }

    private static void VerifyPreexistingUnregisteredReferenceFailsClosed(
        ProjectState baseline)
    {
        var absolute = AbsoluteAssetPath(RawResiduePath);
        AssetDatabase.DisallowAutoRefresh();
        try
        {
            File.WriteAllText(absolute, "unregistered=" + OldParameter);
            Require(!AssetDatabase.GetAllAssetPaths().Contains(
                    RawResiduePath,
                    StringComparer.Ordinal),
                "preexisting raw reference was unexpectedly registered");
            RequireFailure(
                ParameterRequest(true),
                "preexisting unregistered reference was accepted");
        }
        finally
        {
            if (File.Exists(absolute)) File.Delete(absolute);
            if (File.Exists(absolute + ".meta")) File.Delete(absolute + ".meta");
            AssetDatabase.AllowAutoRefresh();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        }
        VerifyProjectState(baseline, "preexisting unregistered reference cleanup");
        Evidence["preexistingUnregisteredReference"] = new JObject
        {
            ["assetDatabaseBlind"] = true,
            ["previewRejected"] = true,
            ["cleanupCompleted"] = true
        };
    }

    private static void VerifyUnknownReferenceFailsClosed(ProjectState baseline)
    {
        File.WriteAllText(AbsoluteAssetPath(UnknownPath), OldParameter);
        AssetDatabase.ImportAsset(UnknownPath, ImportAssetOptions.ForceSynchronousImport);
        RequireFailure(ParameterRequest(true), "unknown old reference accepted");
        Require(AssetDatabase.DeleteAsset(UnknownPath), "unknown asset cleanup");
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        VerifyProjectState(baseline, "unknown reference restore");
    }

    private static void VerifyStaleApprovalFailsClosed(ProjectState baseline)
    {
        var request = ParameterRequest(true);
        var preview = RequireSuccess(AtomicReferenceRenameTool.HandleCommand(request));
        var approved = ApprovedRequest(request, preview);
        var dynamics = Resolve("Avatar/Dynamics");
        dynamics.transform.localPosition = new Vector3(0.125f, 0f, 0f);
        EditorUtility.SetDirty(dynamics.transform);
        EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
        Require(EditorSceneManager.SaveScene(SceneManager.GetActiveScene()), "stale change save");
        RequireFailure(approved, "stale approval accepted");
        Require(Definitions().parameters.Single().name == OldParameter, "stale apply mutated definitions");
        dynamics.transform.localPosition = Vector3.zero;
        EditorUtility.SetDirty(dynamics.transform);
        EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
        Require(EditorSceneManager.SaveScene(SceneManager.GetActiveScene()), "stale restore save");
        VerifyProjectState(baseline, "stale approval restore");
    }

    private static void VerifyPartialMutationRestore(ProjectState baseline)
    {
        var parse = typeof(AtomicReferenceRenameTool).GetMethod(
            "ParseRequest",
            BindingFlags.Static | BindingFlags.NonPublic);
        var build = typeof(AtomicReferenceRenameTool).GetMethod(
            "BuildPreview",
            BindingFlags.Static | BindingFlags.NonPublic);
        var capture = typeof(AtomicReferenceRenameTool).GetMethod(
            "CaptureBackups",
            BindingFlags.Static | BindingFlags.NonPublic);
        var restore = typeof(AtomicReferenceRenameTool).GetMethod(
            "RestoreFailedApply",
            BindingFlags.Static | BindingFlags.NonPublic);
        Require(parse != null && build != null && capture != null && restore != null,
            "partial restore methods");
        var request = ParameterRequest(true);
        var expectedPreview = RequireSuccess(AtomicReferenceRenameTool.HandleCommand(request));
        var sceneBefore = SceneObjectCopyCore.ReadStableAssetEvidence(
            ScenePath,
            "atomic rename fixture partial baseline");
        var parsed = parse.Invoke(null, new object[] { request, true });
        var snapshot = build.Invoke(null, new[] { parsed });
        var snapshotType = snapshot.GetType();
        var assets = snapshotType.GetField("Assets", BindingFlags.Instance | BindingFlags.NonPublic)
            ?.GetValue(snapshot);
        var references = snapshotType.GetField("References", BindingFlags.Instance | BindingFlags.NonPublic)
            ?.GetValue(snapshot) as IEnumerable;
        Require(assets != null && references != null, "partial restore snapshot");
        var backups = capture.Invoke(null, new[] { assets });
        Undo.IncrementCurrentGroup();
        var undoGroup = Undo.GetCurrentGroup();
        Undo.SetCurrentGroupName("Probe atomic reference rollback");
        var first = references.Cast<object>().First();
        var apply = first.GetType().GetField("Apply", BindingFlags.Instance | BindingFlags.NonPublic)
            ?.GetValue(first) as Action;
        Require(apply != null, "partial restore mutation action");
        apply();
        AssetDatabase.SaveAssets();
        EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
        Require(EditorSceneManager.SaveScene(SceneManager.GetActiveScene()), "partial mutation save");
        Require(CaptureProjectState().Digest != baseline.Digest, "partial mutation missing");
        var sceneAfterMutation = SceneObjectCopyCore.ReadStableAssetEvidence(
            ScenePath,
            "atomic rename fixture partial mutation");
        Require(
            sceneAfterMutation.File.Identity != sceneBefore.File.Identity,
            "partial mutation scene body identity churn");
        var restored = (bool)restore.Invoke(
            null,
            new[]
            {
                snapshot,
                backups,
                EditorSceneManager.GetSceneManagerSetup(),
                (object)undoGroup
            });
        Require(restored, "partial mutation exact restore");
        VerifyProjectState(baseline, "partial mutation restore");
        var sceneAfterRestore = SceneObjectCopyCore.ReadStableAssetEvidence(
            ScenePath,
            "atomic rename fixture partial restore");
        Require(sceneAfterRestore.Guid == sceneBefore.Guid, "partial restore scene guid");
        Require(sceneAfterRestore.File.Digest == sceneBefore.File.Digest,
            "partial restore scene body digest");
        Require(sceneAfterRestore.File.Length == sceneBefore.File.Length,
            "partial restore scene body length");
        Require(sceneAfterRestore.Meta.Digest == sceneBefore.Meta.Digest,
            "partial restore scene metadata digest");
        Require(sceneAfterRestore.Meta.Identity == sceneBefore.Meta.Identity,
            "partial restore scene metadata identity");
        Require(sceneAfterRestore.Meta.Length == sceneBefore.Meta.Length,
            "partial restore scene metadata length");
        Require(sceneAfterRestore.File.LinkCount == 1 && sceneAfterRestore.Meta.LinkCount == 1,
            "partial restore scene single link");
        Require(sceneAfterRestore.File.Identity != sceneBefore.File.Identity,
            "partial restore body identity must not be contractual");
        Require(Definitions().parameters.Single().name == OldParameter,
            "partial restore parameter definition");
        RequireNoRawToken(NewParameter, "new parameter residue after partial restore");
        var restoredPreview = RequireSuccess(
            AtomicReferenceRenameTool.HandleCommand(ParameterRequest(true)));
        Require(
            ReferenceSignature(restoredPreview) == ReferenceSignature(expectedPreview),
            "partial restore reference matrix");
        Evidence["partialRestore"] = new JObject
        {
            ["restored"] = true,
            ["bodyIdentityChanged"] = sceneAfterRestore.File.Identity != sceneBefore.File.Identity,
            ["sceneGuidExact"] = sceneAfterRestore.Guid == sceneBefore.Guid,
            ["bodyDigestExact"] = sceneAfterRestore.File.Digest == sceneBefore.File.Digest,
            ["metaDigestExact"] = sceneAfterRestore.Meta.Digest == sceneBefore.Meta.Digest,
            ["metaIdentityExact"] = sceneAfterRestore.Meta.Identity == sceneBefore.Meta.Identity,
            ["singleLink"] = sceneAfterRestore.File.LinkCount == 1
                && sceneAfterRestore.Meta.LinkCount == 1,
            ["referenceMatrixExact"] = ReferenceSignature(restoredPreview)
                == ReferenceSignature(expectedPreview),
            ["newTokenResidue"] = 0
        };
    }

    private static void VerifyUnapprovedConcurrentWriteRequiresCheckpoint(ProjectState baseline)
    {
        var parse = typeof(AtomicReferenceRenameTool).GetMethod(
            "ParseRequest",
            BindingFlags.Static | BindingFlags.NonPublic);
        var build = typeof(AtomicReferenceRenameTool).GetMethod(
            "BuildPreview",
            BindingFlags.Static | BindingFlags.NonPublic);
        var capture = typeof(AtomicReferenceRenameTool).GetMethod(
            "CaptureBackups",
            BindingFlags.Static | BindingFlags.NonPublic);
        var restore = typeof(AtomicReferenceRenameTool).GetMethod(
            "RestoreFailedApply",
            BindingFlags.Static | BindingFlags.NonPublic);
        var failure = typeof(AtomicReferenceRenameTool).GetMethod(
            "Failure",
            BindingFlags.Static | BindingFlags.NonPublic);
        Require(parse != null && build != null && capture != null && restore != null
            && failure != null, "concurrent-write checkpoint methods");

        var request = ParameterRequest(true);
        var parsed = parse.Invoke(null, new object[] { request, true });
        var snapshot = build.Invoke(null, new[] { parsed });
        var assets = snapshot.GetType()
            .GetField("Assets", BindingFlags.Instance | BindingFlags.NonPublic)
            ?.GetValue(snapshot);
        Require(assets != null, "concurrent-write snapshot assets");
        var backups = capture.Invoke(null, new[] { assets });
        var materialAbsolute = AbsoluteAssetPath(MaterialPath);
        var materialBytes = File.ReadAllBytes(materialAbsolute);
        File.AppendAllText(materialAbsolute, "\n# concurrent-drift\n");
        Require(!materialBytes.SequenceEqual(File.ReadAllBytes(materialAbsolute)),
            "concurrent-write setup");

        Undo.IncrementCurrentGroup();
        var undoGroup = Undo.GetCurrentGroup();
        var restored = (bool)restore.Invoke(
            null,
            new[]
            {
                snapshot,
                backups,
                EditorSceneManager.GetSceneManagerSetup(),
                (object)undoGroup
            });
        Require(!restored, "unapproved concurrent write reported restored");
        var failureResponse = JObject.FromObject(failure.Invoke(
            null,
            new object[] { new InvalidOperationException("probe"), true, restored }));
        var failureData = failureResponse["data"] as JObject;
        Require(!failureResponse.Value<bool>("success"), "concurrent-write failure response");
        Require(failureData != null
            && failureData.Value<bool>("checkpointRestoreRequired")
            && !failureData.Value<bool>("cleanupVerified")
            && failureData.Value<string>("operationState") == "checkpoint_restore_required",
            "concurrent-write checkpoint requirement");

        File.WriteAllBytes(materialAbsolute, materialBytes);
        AssetDatabase.ImportAsset(
            MaterialPath,
            ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
        VerifyProjectState(baseline, "unapproved concurrent write cleanup");
        Evidence["unapprovedConcurrentWrite"] = new JObject
        {
            ["restored"] = false,
            ["cleanupVerified"] = false,
            ["checkpointRestoreRequired"] = true,
            ["cleanupCompleted"] = true
        };
    }

    private static void VerifyPlannedConcurrentWriteIsRejectedAndRestored(ProjectState baseline)
    {
        var parse = typeof(AtomicReferenceRenameTool).GetMethod(
            "ParseRequest",
            BindingFlags.Static | BindingFlags.NonPublic);
        var build = typeof(AtomicReferenceRenameTool).GetMethod(
            "BuildPreview",
            BindingFlags.Static | BindingFlags.NonPublic);
        var capture = typeof(AtomicReferenceRenameTool).GetMethod(
            "CaptureBackups",
            BindingFlags.Static | BindingFlags.NonPublic);
        var save = typeof(AtomicReferenceRenameTool).GetMethod(
            "SavePlannedAssets",
            BindingFlags.Static | BindingFlags.NonPublic);
        var verify = typeof(AtomicReferenceRenameTool).GetMethod(
            "VerifyExactInventoryDelta",
            BindingFlags.Static | BindingFlags.NonPublic);
        var restore = typeof(AtomicReferenceRenameTool).GetMethod(
            "RestoreFailedApply",
            BindingFlags.Static | BindingFlags.NonPublic);
        Require(parse != null && build != null && capture != null && save != null
            && verify != null && restore != null, "planned concurrent-write methods");

        var request = ObjectRequest(true);
        var parsed = parse.Invoke(null, new object[] { request, true });
        var snapshot = build.Invoke(null, new[] { parsed });
        var snapshotType = snapshot.GetType();
        var assets = snapshotType
            .GetField("Assets", BindingFlags.Instance | BindingFlags.NonPublic)
            ?.GetValue(snapshot);
        var references = snapshotType
            .GetField("References", BindingFlags.Instance | BindingFlags.NonPublic)
            ?.GetValue(snapshot) as IEnumerable;
        Require(assets != null && references != null, "planned concurrent-write snapshot");
        var backups = capture.Invoke(null, new[] { assets });
        var sceneSetup = EditorSceneManager.GetSceneManagerSetup();
        Undo.IncrementCurrentGroup();
        var undoGroup = Undo.GetCurrentGroup();
        Undo.SetCurrentGroupName("Probe planned concurrent write");
        foreach (var reference in references.Cast<object>())
        {
            var apply = reference.GetType()
                .GetField("Apply", BindingFlags.Instance | BindingFlags.NonPublic)
                ?.GetValue(reference) as Action;
            Require(apply != null, "planned concurrent-write action");
            apply();
        }
        save.Invoke(null, new[] { snapshot });
        EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
        Require(EditorSceneManager.SaveScene(SceneManager.GetActiveScene()),
            "planned concurrent-write scene save");
        Undo.CollapseUndoOperations(undoGroup);
        if (SceneManager.GetActiveScene().isDirty)
        {
            Require(EditorSceneManager.SaveScene(SceneManager.GetActiveScene()),
                "planned concurrent-write scene finalize");
        }

        var reverseParsed = parse.Invoke(
            null,
            new object[] { ObjectRequest(true, RenamedObjectPath, OldObjectName), true });
        var reverse = build.Invoke(null, new[] { reverseParsed });
        verify.Invoke(null, new[] { snapshot, reverse });

        File.AppendAllText(AbsoluteAssetPath(ClipPath), "\n# unapproved-planned-drift\n");
        var driftedReverse = build.Invoke(null, new[] { reverseParsed });
        var rejected = false;
        try
        {
            verify.Invoke(null, new[] { snapshot, driftedReverse });
        }
        catch (TargetInvocationException exception)
        {
            rejected = exception.InnerException != null;
        }
        var restored = (bool)restore.Invoke(
            null,
            new[] { snapshot, backups, sceneSetup, (object)undoGroup });
        Require(rejected, "planned concurrent write accepted");
        Require(restored, "planned concurrent write exact restore");
        VerifyProjectState(baseline, "planned concurrent write restore");
        Evidence["plannedConcurrentWrite"] = new JObject
        {
            ["normalTargetExact"] = true,
            ["driftRejected"] = true,
            ["restored"] = true
        };
    }

    private static void VerifyUnregisteredFileSystemResidueRequiresCheckpoint(
        ProjectState baseline)
    {
        var parse = typeof(AtomicReferenceRenameTool).GetMethod(
            "ParseRequest",
            BindingFlags.Static | BindingFlags.NonPublic);
        var build = typeof(AtomicReferenceRenameTool).GetMethod(
            "BuildPreview",
            BindingFlags.Static | BindingFlags.NonPublic);
        var capture = typeof(AtomicReferenceRenameTool).GetMethod(
            "CaptureBackups",
            BindingFlags.Static | BindingFlags.NonPublic);
        var restore = typeof(AtomicReferenceRenameTool).GetMethod(
            "RestoreFailedApply",
            BindingFlags.Static | BindingFlags.NonPublic);
        var failure = typeof(AtomicReferenceRenameTool).GetMethod(
            "Failure",
            BindingFlags.Static | BindingFlags.NonPublic);
        Require(parse != null && build != null && capture != null && restore != null
            && failure != null, "unregistered residue methods");

        var request = ParameterRequest(true);
        var parsed = parse.Invoke(null, new object[] { request, true });
        var snapshot = build.Invoke(null, new[] { parsed });
        var assets = snapshot.GetType()
            .GetField("Assets", BindingFlags.Instance | BindingFlags.NonPublic)
            ?.GetValue(snapshot);
        Require(assets != null, "unregistered residue snapshot assets");
        var backups = capture.Invoke(null, new[] { assets });
        var rawAbsolute = AbsoluteAssetPath(RawResiduePath);
        var emptyAbsolute = AbsoluteAssetPath(EmptyResiduePath);
        bool restored;
        AssetDatabase.DisallowAutoRefresh();
        try
        {
            File.WriteAllText(rawAbsolute, "unregistered residue");
            Directory.CreateDirectory(emptyAbsolute);
            Require(!AssetDatabase.GetAllAssetPaths().Contains(
                    RawResiduePath,
                    StringComparer.Ordinal),
                "raw residue was unexpectedly registered");
            Require(!AssetDatabase.IsValidFolder(EmptyResiduePath),
                "empty residue was unexpectedly registered");

            Undo.IncrementCurrentGroup();
            var undoGroup = Undo.GetCurrentGroup();
            restored = (bool)restore.Invoke(
                null,
                new[]
                {
                    snapshot,
                    backups,
                    EditorSceneManager.GetSceneManagerSetup(),
                    (object)undoGroup
                });
            Require(!restored, "unregistered filesystem residue reported restored");
            var failureResponse = JObject.FromObject(failure.Invoke(
                null,
                new object[] { new InvalidOperationException("probe"), true, restored }));
            var failureData = failureResponse["data"] as JObject;
            Require(!failureResponse.Value<bool>("success")
                && failureData != null
                && failureData.Value<bool>("checkpointRestoreRequired")
                && !failureData.Value<bool>("cleanupVerified")
                && failureData.Value<string>("operationState")
                    == "checkpoint_restore_required",
                "unregistered residue checkpoint requirement");
        }
        finally
        {
            if (File.Exists(rawAbsolute)) File.Delete(rawAbsolute);
            if (File.Exists(rawAbsolute + ".meta")) File.Delete(rawAbsolute + ".meta");
            if (Directory.Exists(emptyAbsolute)) Directory.Delete(emptyAbsolute, true);
            if (File.Exists(emptyAbsolute + ".meta")) File.Delete(emptyAbsolute + ".meta");
            AssetDatabase.AllowAutoRefresh();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        }
        VerifyProjectState(baseline, "unregistered filesystem residue cleanup");
        Evidence["unregisteredRawResidue"] = new JObject
        {
            ["assetDatabaseBlind"] = true,
            ["restored"] = false,
            ["cleanupVerified"] = false,
            ["checkpointRestoreRequired"] = true,
            ["cleanupCompleted"] = true
        };
    }

    private static void VerifyParameterLifecycle(ProjectState baseline)
    {
        var request = ParameterRequest(true);
        var before = CaptureProjectState();
        var preview = RequireSuccess(AtomicReferenceRenameTool.HandleCommand(request));
        var kinds = preview["references"].Values<JObject>()
            .GroupBy(item => item.Value<string>("kind"))
            .ToDictionary(group => group.Key, group => group.Count());
        var requiredKinds = new[]
        {
            "expression_parameter",
            "expression_menu_parameter",
            "animator_parameter",
            "animator_condition",
            "animator_state_parameter",
            "blend_tree_parameter",
            "state_behaviour_parameter",
            "contact_parameter",
            "physbone_parameter",
            "registered_component_parameter"
        };
        foreach (var kind in requiredKinds)
        {
            Require(kinds.ContainsKey(kind) && kinds[kind] > 0, "parameter kind " + kind);
        }
        VerifyPreview(preview, "parameter", preview["references"].Count());
        VerifyProjectState(before, "parameter preview zero write");

        var apply = RequireSuccess(AtomicReferenceRenameTool.HandleCommand(
            ApprovedRequest(request, preview)));
        VerifyApply(
            apply,
            preview["references"].Count(),
            preview.Value<string>("planDigest"));
        Require(Definitions().parameters.Single().name == NewParameter, "parameter definition readback");
        RequireNoRawToken(OldParameter, "old parameter reference after apply");

        var reverseRequest = ParameterRequest(true, NewParameter, OldParameter);
        var reversePreview = RequireSuccess(AtomicReferenceRenameTool.HandleCommand(reverseRequest));
        var reverseApply = RequireSuccess(AtomicReferenceRenameTool.HandleCommand(
            ApprovedRequest(reverseRequest, reversePreview)));
        VerifyApply(
            reverseApply,
            preview["references"].Count(),
            reversePreview.Value<string>("planDigest"));
        Require(Definitions().parameters.Single().name == OldParameter,
            "parameter reverse definition");
        RequireNoRawToken(NewParameter, "new parameter reference after reverse");
        var restoredPreview = RequireSuccess(
            AtomicReferenceRenameTool.HandleCommand(ParameterRequest(true)));
        Require(
            ReferenceSignature(restoredPreview) == ReferenceSignature(preview),
            "parameter reverse reference matrix");
        VerifySemanticBaseline(baseline, "parameter reverse restore");
        Evidence["parameterRequest"] = request.DeepClone();
        Evidence["parameterPreview"] = preview.DeepClone();
        Evidence["parameterApply"] = apply.DeepClone();
        Evidence["parameterReferenceCounts"] = JObject.FromObject(kinds);
        Evidence["parameterReverseByteExact"] = CaptureProjectState().Digest == baseline.Digest;
    }

    private static JObject ObjectRequest(
        bool preview,
        string targetPath = null,
        string newName = null)
    {
        return new JObject
        {
            ["operationKind"] = "game_object",
            ["scenePath"] = ScenePath,
            ["avatarPath"] = AvatarPath,
            ["targetObjectPath"] = targetPath ?? TargetObjectPath,
            ["newName"] = newName ?? NewObjectName,
            ["preview"] = preview,
            ["saveScene"] = !preview
        };
    }

    private static JObject ParameterRequest(
        bool preview,
        string before = null,
        string after = null)
    {
        return new JObject
        {
            ["operationKind"] = "parameter",
            ["scenePath"] = ScenePath,
            ["avatarPath"] = AvatarPath,
            ["oldParameterName"] = before ?? OldParameter,
            ["newParameterName"] = after ?? NewParameter,
            ["preview"] = preview,
            ["saveScene"] = !preview
        };
    }

    private static JObject ApprovedRequest(JObject request, JObject preview)
    {
        var approved = (JObject)request.DeepClone();
        approved["preview"] = false;
        approved["saveScene"] = true;
        var scene = (JObject)preview["scene"];
        var avatar = (JObject)preview["avatar"];
        var target = (JObject)preview["target"];
        var scan = (JObject)preview["scan"];
        approved["expectedProjectPath"] = preview["projectPath"];
        approved["expectedSceneGuid"] = scene["guid"];
        approved["expectedSceneHandle"] = scene["handle"];
        approved["expectedSceneFileDigest"] = scene["fileDigestBefore"];
        approved["expectedSceneFileIdentity"] = scene["fileIdentity"];
        approved["expectedSceneMetaDigest"] = scene["metaDigestBefore"];
        approved["expectedSceneMetaIdentity"] = scene["metaIdentity"];
        approved["expectedAvatarObjectId"] = avatar["objectId"];
        approved["expectedTargetIdentityDigest"] = target["identityDigest"];
        approved["expectedAssemblySetDigest"] = scan["assemblySetDigest"];
        approved["expectedAssetInventoryDigest"] = scan["assetInventoryDigest"];
        approved["expectedBeforeStateDigest"] = preview["beforeStateDigest"];
        approved["expectedTargetStateDigest"] = preview["targetStateDigest"];
        approved["expectedPlanDigest"] = preview["planDigest"];
        return approved;
    }

    private static void VerifyPreview(JObject preview, string operation, int referenceCount)
    {
        Require(preview.Value<string>("schema") == "vrcforge.atomic_reference_rename.v1",
            "preview schema");
        Require(preview.Value<bool>("ok"), "preview ok");
        Require(preview.Value<bool>("preview"), "preview flag");
        Require(preview.Value<bool>("verified"), "preview verified");
        Require(!preview.Value<bool>("changed") && !preview.Value<bool>("saved"),
            "preview zero write flags");
        Require(preview.Value<int>("mutationCount") == 0, "preview mutation count");
        Require(((JObject)preview["operation"]).Value<string>("kind") == operation,
            "preview operation");
        Require(preview["references"].Count() == referenceCount, "preview reference count");
        Require(((JObject)preview["scan"]).Value<int>("knownReferenceCount") == referenceCount,
            "preview scan count");
        Require(((JObject)preview["scan"]).Value<int>("unknownReferenceCount") == 0,
            "preview unknown count");
        Require(((JObject)preview["scan"]).Value<int>("unresolvedReferenceCount") == 0,
            "preview unresolved count");
    }

    private static void VerifyApply(JObject apply, int mutationCount, string expectedPlanDigest)
    {
        var expectedKeys = new HashSet<string>(StringComparer.Ordinal)
        {
            "schema",
            "ok",
            "preview",
            "verified",
            "changed",
            "saved",
            "mutationCount",
            "projectPath",
            "operation",
            "scene",
            "avatar",
            "target",
            "references",
            "approvedPlan",
            "beforeStateDigest",
            "targetStateDigest",
            "planDigestSchema",
            "planDigest",
            "readback",
            "readbackExact",
            "checkpointRestoreRequired"
        };
        Require(expectedKeys.SetEquals(apply.Properties().Select(property => property.Name)),
            "apply result shape");
        Require(apply.Value<string>("schema") == "vrcforge.atomic_reference_rename.v1",
            "apply schema");
        Require(apply.Value<bool>("ok"), "apply ok");
        Require(!apply.Value<bool>("preview"), "apply preview flag");
        Require(apply.Value<bool>("verified"), "apply verified");
        Require(apply.Value<bool>("changed") && apply.Value<bool>("saved"),
            "apply persisted flags");
        Require(apply.Value<int>("mutationCount") == mutationCount, "apply mutation count");
        Require(apply.Value<bool>("readbackExact"), "apply exact readback");
        Require(!apply.Value<bool>("checkpointRestoreRequired"),
            "apply checkpoint restore required");
        Require(apply.Value<string>("planDigestSchema")
            == "vrcforge.atomic_reference_rename_plan.v1", "apply plan schema");
        var approvedPlan = apply["approvedPlan"] as JObject;
        var readback = apply["readback"] as JObject;
        Require(approvedPlan != null && readback != null, "apply plan and readback");
        var planKeys = new HashSet<string>(StringComparer.Ordinal)
        {
            "operation",
            "scene",
            "avatar",
            "target",
            "scan",
            "assets",
            "references",
            "beforeStateDigest",
            "targetStateDigest"
        };
        Require(planKeys.SetEquals(approvedPlan.Properties().Select(property => property.Name)),
            "apply approved plan shape");
        var readbackKeys = new HashSet<string>(planKeys, StringComparer.Ordinal)
        {
            "planDigestSchema",
            "planDigest"
        };
        Require(readbackKeys.SetEquals(readback.Properties().Select(property => property.Name)),
            "apply readback shape");
        Require(apply.Value<string>("planDigest") == expectedPlanDigest,
            "apply approved plan digest");
        Require(readback.Value<string>("planDigestSchema")
            == "vrcforge.atomic_reference_rename_plan.v1", "apply readback plan schema");
    }

    private static JObject RequireSuccess(object response)
    {
        var wrapper = JObject.FromObject(response);
        Require(
            wrapper.Value<bool>("success"),
            "tool failure: " + wrapper.ToString(Newtonsoft.Json.Formatting.None));
        var data = wrapper["data"] as JObject;
        Require(data != null, "success payload");
        return data;
    }

    private static void RequireFailure(JObject request, string label)
    {
        var wrapper = JObject.FromObject(AtomicReferenceRenameTool.HandleCommand(request));
        Require(!wrapper.Value<bool>("success"), label);
    }

    private static ProjectState CaptureProjectState()
    {
        AssetDatabase.SaveAssets();
        var paths = AssetDatabase.GetAllAssetPaths()
            .Where(path => path.StartsWith(ProbeFolder + "/", StringComparison.Ordinal))
            .Where(path => !AssetDatabase.IsValidFolder(path))
            .OrderBy(path => path, StringComparer.Ordinal)
            .ToList();
        var parts = new List<string>();
        var contractParts = new List<string>();
        foreach (var path in paths)
        {
            var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(
                path,
                "atomic rename fixture project state");
            Require(evidence.File.LinkCount == 1 && evidence.Meta.LinkCount == 1,
                "project state single-link evidence");
            parts.Add(path);
            parts.Add(Hash(File.ReadAllBytes(AbsoluteAssetPath(path))));
            parts.Add(Hash(File.ReadAllBytes(AbsoluteAssetPath(path) + ".meta")));
            contractParts.Add(path);
            contractParts.Add(evidence.Guid);
            contractParts.Add(evidence.File.Digest);
            contractParts.Add(evidence.File.Length.ToString(
                System.Globalization.CultureInfo.InvariantCulture));
            contractParts.Add(evidence.Meta.Digest);
            contractParts.Add(evidence.Meta.Identity);
            contractParts.Add(evidence.Meta.Length.ToString(
                System.Globalization.CultureInfo.InvariantCulture));
            contractParts.Add(evidence.File.LinkCount.ToString(
                System.Globalization.CultureInfo.InvariantCulture));
            contractParts.Add(evidence.Meta.LinkCount.ToString(
                System.Globalization.CultureInfo.InvariantCulture));
        }
        return new ProjectState
        {
            Digest = Hash(System.Text.Encoding.UTF8.GetBytes(string.Join("\n", parts))),
            ContractDigest = Hash(System.Text.Encoding.UTF8.GetBytes(
                string.Join("\n", contractParts))),
            AssetCount = paths.Count,
            SceneDirty = SceneManager.GetActiveScene().isDirty
        };
    }

    private static void VerifyProjectState(ProjectState expected, string label)
    {
        var current = CaptureProjectState();
        Require(current.Digest == expected.Digest, label + " bytes");
        Require(current.ContractDigest == expected.ContractDigest, label + " contract evidence");
        Require(current.AssetCount == expected.AssetCount, label + " asset count");
        Require(!current.SceneDirty, label + " scene dirty");
    }

    private static void VerifySemanticBaseline(ProjectState expected, string label)
    {
        var current = CaptureProjectState();
        Require(current.AssetCount == expected.AssetCount, label + " asset count");
        Require(!current.SceneDirty, label + " scene dirty");
    }

    private static string ReferenceSignature(JObject preview)
    {
        return string.Join(
            "\n",
            preview["references"].Values<JObject>().Select(item => string.Join(
                "|",
                item.Value<string>("kind"),
                item.Value<string>("assetPath"),
                item.Value<string>("objectId"),
                item.Value<string>("propertyPath"),
                item.Value<string>("before"),
                item.Value<string>("after"))));
    }

    private static void RequireNoRawToken(string token, string label)
    {
        var needle = System.Text.Encoding.UTF8.GetBytes(token);
        foreach (var path in AssetDatabase.GetAllAssetPaths()
            .Where(path => path.StartsWith("Assets/", StringComparison.Ordinal))
            .Where(path => !AssetDatabase.IsValidFolder(path)))
        {
            Require(!Contains(File.ReadAllBytes(AbsoluteAssetPath(path)), needle), label + " in " + path);
            Require(!Contains(File.ReadAllBytes(AbsoluteAssetPath(path) + ".meta"), needle),
                label + " in metadata " + path);
        }
    }

    private static bool Contains(byte[] value, byte[] needle)
    {
        for (var index = 0; index <= value.Length - needle.Length; index++)
        {
            var match = true;
            for (var offset = 0; offset < needle.Length; offset++)
            {
                if (value[index + offset] == needle[offset]) continue;
                match = false;
                break;
            }
            if (match) return true;
        }
        return false;
    }

    private static VRCExpressionParameters Definitions()
    {
        return AssetDatabase.LoadAssetAtPath<VRCExpressionParameters>(ParametersPath)
            ?? throw new InvalidOperationException("definitions unavailable");
    }

    private static void CreateRegisteredFeature(GameObject host, string parameter)
    {
        var apiType = AppDomain.CurrentDomain.GetAssemblies()
            .Select(assembly => assembly.GetType("com.vrcfury.api.FuryComponents", false))
            .Single(type => type != null);
        var create = apiType.GetMethod(
            "CreateToggle",
            BindingFlags.Public | BindingFlags.Static,
            null,
            new[] { typeof(GameObject) },
            null);
        Require(create != null, "registered feature creator");
        var feature = create.Invoke(null, new object[] { host });
        Require(feature != null, "registered feature creation");
        var setParameter = feature.GetType().GetMethod(
            "SetGlobalParameter",
            BindingFlags.Public | BindingFlags.Instance,
            null,
            new[] { typeof(string) },
            null);
        Require(setParameter != null, "registered feature parameter setter");
        setParameter.Invoke(feature, new object[] { parameter });
        var component = host.GetComponents<Component>()
            .Single(item => item != null && item.GetType().FullName == "VF.Model.VRCFury");
        var serialized = new SerializedObject(component);
        var driven = serialized.FindProperty("content.driveGlobalParam");
        Require(driven != null && driven.propertyType == SerializedPropertyType.String,
            "registered driven parameter field");
        driven.stringValue = parameter;
        Require(serialized.ApplyModifiedPropertiesWithoutUndo(),
            "registered driven parameter write");
    }

    private static void CreateRegisteredListFeature(GameObject host, string parameter)
    {
        var apiType = AppDomain.CurrentDomain.GetAssemblies()
            .Select(assembly => assembly.GetType("com.vrcfury.api.FuryComponents", false))
            .Single(type => type != null);
        var create = apiType.GetMethod(
            "CreateFullController",
            BindingFlags.Public | BindingFlags.Static,
            null,
            new[] { typeof(GameObject) },
            null);
        Require(create != null, "registered list feature creator");
        var feature = create.Invoke(null, new object[] { host });
        Require(feature != null, "registered list feature creation");
        var addParameter = feature.GetType().GetMethod(
            "AddGlobalParam",
            BindingFlags.Public | BindingFlags.Instance,
            null,
            new[] { typeof(string) },
            null);
        Require(addParameter != null, "registered list parameter setter");
        addParameter.Invoke(feature, new object[] { parameter });
    }

    private static GameObject Resolve(string path)
    {
        return SceneObjectCopyCore.ResolveUniqueGameObject(
            SceneManager.GetActiveScene(),
            path,
            "fixture object");
    }

    private static GameObject AddChild(GameObject parent, string name)
    {
        var child = new GameObject(name);
        child.transform.SetParent(parent.transform, false);
        return child;
    }

    private static void EnsureFolder(string parent, string child)
    {
        if (!AssetDatabase.IsValidFolder(parent + "/" + child))
        {
            AssetDatabase.CreateFolder(parent, child);
        }
    }

    private static string Hash(byte[] value)
    {
        using (var sha = System.Security.Cryptography.SHA256.Create())
        {
            return BitConverter.ToString(sha.ComputeHash(value)).Replace("-", string.Empty);
        }
    }

    private static string CurrentProjectPath()
    {
        return Directory.GetParent(Application.dataPath)?.FullName
            ?? throw new InvalidOperationException("project root unavailable");
    }

    private static string AbsoluteAssetPath(string assetPath)
    {
        return Path.Combine(CurrentProjectPath(), assetPath.Replace('/', Path.DirectorySeparatorChar));
    }

    private static string EvidencePath()
    {
        return Path.GetFullPath(Path.Combine(
            CurrentProjectPath(),
            "..",
            "atomic-reference-rename-fixture-report.json"));
    }

    private static void ResetEvidenceOutput()
    {
        Evidence.RemoveAll();
        foreach (var path in new[] { EvidencePath(), EvidenceTempPath() })
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
            Require(!File.Exists(path), "stale evidence cleanup");
        }
    }

    private static void WriteFreshEvidence()
    {
        var finalPath = EvidencePath();
        var temporaryPath = EvidenceTempPath();
        var bytes = System.Text.Encoding.UTF8.GetBytes(
            Evidence.ToString(Newtonsoft.Json.Formatting.Indented));
        using (var stream = new FileStream(
            temporaryPath,
            FileMode.CreateNew,
            FileAccess.Write,
            FileShare.None))
        {
            stream.Write(bytes, 0, bytes.Length);
            stream.Flush(true);
        }
        Require(!File.Exists(finalPath), "fresh evidence no-clobber");
        File.Move(temporaryPath, finalPath);
        Require(File.Exists(finalPath) && !File.Exists(temporaryPath),
            "fresh evidence atomic publish");
    }

    private static string EvidenceTempPath()
    {
        return EvidencePath() + ".tmp";
    }

    private static void CleanupEvidenceOutputBestEffort()
    {
        try
        {
            foreach (var path in new[] { EvidencePath(), EvidenceTempPath() })
            {
                if (File.Exists(path))
                {
                    File.Delete(path);
                }
            }
        }
        catch
        {
        }
    }

    private static void CleanupBestEffort()
    {
        try
        {
            if (SceneManager.GetActiveScene().IsValid()
                && SceneManager.GetActiveScene().path == ScenePath)
            {
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            }
            if (AssetDatabase.IsValidFolder(ProbeFolder))
            {
                AssetDatabase.DeleteAsset(ProbeFolder);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            }
        }
        catch
        {
        }
    }

    private static void Require(bool value, string label)
    {
        if (!value)
        {
            throw new InvalidOperationException("Atomic rename probe failed: " + label);
        }
    }

    private sealed class ProjectState
    {
        internal string Digest = string.Empty;
        internal string ContractDigest = string.Empty;
        internal int AssetCount;
        internal bool SceneDirty;
    }
}
