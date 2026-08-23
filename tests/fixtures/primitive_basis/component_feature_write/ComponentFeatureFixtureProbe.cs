using System;
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRCForge.Editor;

public static class ComponentFeatureFixtureProbe
{
    private const string ProbeFolder = "Assets/VRCForge/Generated/ComponentFeatureProbe";
    private const string ScenePath = ProbeFolder + "/ComponentFeatureProbe.unity";
    private const string ToggleHostPath = "Avatar/FeatureHost";
    private const string ArmatureHostPath = "Avatar/ArmatureFeatureHost";
    private const string EmbeddedPackageFolder = "com.vrcfury.vrcfury";
    private static byte[] BaselineSceneBytes = Array.Empty<byte>();
    private static readonly JObject Evidence = new JObject();

    public static void Run()
    {
        try
        {
            CleanupBestEffort();
            VerifyMutationFailureSignals();
            PrepareScene();
            var baseline = ComponentFeatureWriteCore.ResolveSavedScene(ScenePath);
            var baselineDigest = baseline.FileDigest;
            var baselineMetaDigest = baseline.MetaDigest;
            BaselineSceneBytes = File.ReadAllBytes(AbsoluteAssetPath(ScenePath));

            VerifyMissingPackageFailsClosed();
            VerifyCompatiblePackageTreeDriftAccepted();
            VerifyMethodSignatureDriftFailsClosed();
            ComponentFeatureWriteCore.ValidateCompatibility();
            VerifyUnknownFieldFailsClosed(baselineDigest, baselineMetaDigest);
            VerifyPreviewZeroWrite(baselineDigest, baselineMetaDigest);
            VerifyStaleExpectedBeforeFailsClosed(baselineDigest, baselineMetaDigest);
            VerifyPartialFailureRestore(baselineDigest, baselineMetaDigest);
            RunToggleLifecycle(baselineDigest, baselineMetaDigest);
            RunArmatureLinkLifecycle(baselineDigest, baselineMetaDigest);

            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            Require(AssetDatabase.DeleteAsset(ProbeFolder), "fixture cleanup");
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            Require(!File.Exists(AbsoluteAssetPath(ScenePath)), "scene residue");
            Require(!File.Exists(AbsoluteAssetPath(ScenePath) + ".meta"), "scene metadata residue");

            Evidence["schema"] = "vrcforge.component_feature_fixture_evidence.v1";
            Evidence["projectPath"] = CurrentProjectPath();
            Evidence["cleanupVerified"] = true;
            File.WriteAllText(
                EvidencePath(),
                Evidence.ToString(Newtonsoft.Json.Formatting.Indented)
            );

            Debug.Log("VRCFORGE_COMPONENT_FEATURE_PROBE_OK");
            EditorApplication.Exit(0);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            CleanupBestEffort();
            EditorApplication.Exit(1);
        }
    }

    private static void PrepareScene()
    {
        EnsureFolder("Assets/VRCForge", "Generated");
        if (AssetDatabase.IsValidFolder(ProbeFolder))
        {
            AssetDatabase.DeleteAsset(ProbeFolder);
        }
        AssetDatabase.CreateFolder("Assets/VRCForge/Generated", "ComponentFeatureProbe");
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var avatar = new GameObject("Avatar");
        AddChild(avatar, "FeatureHost");
        var hat = AddChild(avatar, "Hat");
        AddChild(hat, "Charm");
        AddChild(avatar, "ArmatureFeatureHost");
        AddChild(avatar, "PropRoot");
        AddChild(avatar, "ChestTarget");
        var armature = AddChild(avatar, "Armature");
        var hips = AddChild(armature, "Hips");
        AddChild(hips, "Spine");
        Require(EditorSceneManager.SaveScene(scene, ScenePath), "fixture scene save");
        Require(!scene.isDirty, "fixture scene dirty after save");
        Undo.ClearAll();
    }

    private static void VerifyMissingPackageFailsClosed()
    {
        var packageJson = Path.Combine(PackageRoot(), "package.json");
        var backup = Path.Combine(
            CurrentProjectPath(),
            "Temp",
            "vrcforge-component-feature-package-" + Guid.NewGuid().ToString("N") + ".json"
        );
        Directory.CreateDirectory(Path.GetDirectoryName(backup));
        Require(File.Exists(packageJson), "package manifest precondition");
        try
        {
            File.Move(packageJson, backup);
            ExpectCompatibilityFailure("missing package accepted");
        }
        finally
        {
            if (!File.Exists(packageJson) && File.Exists(backup))
            {
                File.Move(backup, packageJson);
            }
        }
        Require(File.Exists(packageJson) && !File.Exists(backup), "package manifest restore");
    }

    private static void VerifyCompatiblePackageTreeDriftAccepted()
    {
        var publicApiSource = Path.Combine(PackageRoot(), "PublicApi", "FuryComponents.cs");
        Require(File.Exists(publicApiSource), "package source precondition");
        var before = File.ReadAllBytes(publicApiSource);
        var beforeWriteTime = File.GetLastWriteTimeUtc(publicApiSource);
        try
        {
            File.WriteAllBytes(publicApiSource, before.Concat(new byte[] { 0x20 }).ToArray());
            var drifted = ComponentFeatureWriteCore.ValidateCompatibility();
            Require(drifted.PackageTreeDigest != string.Empty, "package tree drift evidence");
        }
        finally
        {
            File.WriteAllBytes(publicApiSource, before);
            File.SetLastWriteTimeUtc(publicApiSource, beforeWriteTime);
        }
        Require(File.ReadAllBytes(publicApiSource).SequenceEqual(before), "package source restore");
    }

    private static void VerifyMethodSignatureDriftFailsClosed()
    {
        var failed = false;
        try
        {
            ComponentFeatureWriteCore.RunMethodSignatureDriftProbe();
        }
        catch (ComponentFeatureWriteException)
        {
            failed = true;
        }
        Require(failed, "method signature drift accepted");
    }

    private static void VerifyUnknownFieldFailsClosed(
        string baselineDigest,
        string baselineMetaDigest)
    {
        var request = ToggleRequest(true);
        request["typeName"] = "arbitrary.runtime.type";
        RequireFailure(request, "unknown field accepted");
        VerifyBaseline(baselineDigest, baselineMetaDigest, "unknown field");
    }

    private static void VerifyPreviewZeroWrite(
        string baselineDigest,
        string baselineMetaDigest)
    {
        var beforeBytes = File.ReadAllBytes(AbsoluteAssetPath(ScenePath));
        var beforeMetaBytes = File.ReadAllBytes(AbsoluteAssetPath(ScenePath) + ".meta");
        var beforeComponents = FeatureCount(ToggleHostPath, ComponentFeatureWriteCore.ToggleKind);
        var preview = RequireSuccess(ComponentFeatureWriterTool.HandleCommand(ToggleRequest(true)));
        Require(preview.Value<bool>("preview"), "preview flag");
        Require(!preview.Value<bool>("changed"), "preview changed");
        Require(!preview.Value<bool>("saved"), "preview saved");
        Require(preview.Value<int>("mutationCount") == 0, "preview mutation count");
        Require(preview.Value<bool>("wouldChange"), "preview wouldChange");
        Require(FeatureCount(ToggleHostPath, ComponentFeatureWriteCore.ToggleKind) == beforeComponents,
            "preview component count");
        Require(File.ReadAllBytes(AbsoluteAssetPath(ScenePath)).SequenceEqual(beforeBytes),
            "preview scene bytes");
        Require(File.ReadAllBytes(AbsoluteAssetPath(ScenePath) + ".meta").SequenceEqual(beforeMetaBytes),
            "preview metadata bytes");
        VerifyBaseline(baselineDigest, baselineMetaDigest, "preview zero write");
    }

    private static void VerifyStaleExpectedBeforeFailsClosed(
        string baselineDigest,
        string baselineMetaDigest)
    {
        var request = ToggleRequest(true);
        var preview = RequireSuccess(ComponentFeatureWriterTool.HandleCommand(request));
        var approved = ApprovedRequest(request, preview);
        var host = ComponentFeatureWriteCore.ResolveUniqueGameObject(
            SceneManager.GetActiveScene(),
            ToggleHostPath,
            "fixture host"
        );
        host.transform.localPosition = new Vector3(0.125f, 0f, 0f);
        EditorUtility.SetDirty(host.transform);
        EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
        Require(EditorSceneManager.SaveScene(SceneManager.GetActiveScene()), "stale mutation save");
        Require(
            ComponentFeatureWriteCore.ResolveSavedScene(ScenePath).FileDigest != baselineDigest,
            "stale mutation missing"
        );
        RequireFailure(approved, "stale expected-before accepted");
        Require(FeatureCount(ToggleHostPath, ComponentFeatureWriteCore.ToggleKind) == 0,
            "stale request created component");
        host.transform.localPosition = Vector3.zero;
        EditorUtility.SetDirty(host.transform);
        EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
        Require(EditorSceneManager.SaveScene(SceneManager.GetActiveScene()), "stale restore save");
        VerifyBaseline(baselineDigest, baselineMetaDigest, "stale restore");
    }

    private static void RunToggleLifecycle(string baselineDigest, string baselineMetaDigest)
    {
        var request = ToggleRequest(true);
        var preview = RequireSuccess(ComponentFeatureWriterTool.HandleCommand(request));
        var apply = RequireSuccess(ComponentFeatureWriterTool.HandleCommand(
            ApprovedRequest(request, preview)
        ));
        Evidence["toggle"] = new JObject
        {
            ["request"] = request.DeepClone(),
            ["preview"] = preview.DeepClone(),
            ["apply"] = apply.DeepClone()
        };
        VerifyApplyPayload(apply, ComponentFeatureWriteCore.ToggleKind);
        Require(FeatureCount(ToggleHostPath, ComponentFeatureWriteCore.ToggleKind) == 1,
            "toggle CreateNew count");
        Require(
            ComponentFeatureWriteCore.ResolveUniqueGameObject(
                SceneManager.GetActiveScene(),
                ToggleHostPath,
                "toggle host"
            ).activeSelf,
            "toggle apply changed host active state"
        );
        var appliedDigest = ComponentFeatureWriteCore.ResolveSavedScene(ScenePath).FileDigest;
        Require(appliedDigest != baselineDigest, "toggle persistence");
        VerifyDuplicateFeatureFailsClosed(request, appliedDigest);
        Undo.PerformUndo();
        SaveAfterUndo("toggle undo save");
        Require(FeatureCount(ToggleHostPath, ComponentFeatureWriteCore.ToggleKind) == 0,
            "toggle undo component residue");
        VerifyBaseline(baselineDigest, baselineMetaDigest, "toggle undo restore");
        Undo.ClearAll();
    }

    private static void VerifyDuplicateFeatureFailsClosed(JObject request, string appliedDigest)
    {
        var duplicate = (JObject)request.DeepClone();
        duplicate["preview"] = true;
        duplicate["saveScene"] = false;
        RequireFailure(duplicate, "duplicate component accepted");
        Require(
            ComponentFeatureWriteCore.ResolveSavedScene(ScenePath).FileDigest == appliedDigest,
            "duplicate rejection changed scene"
        );
        Require(FeatureCount(ToggleHostPath, ComponentFeatureWriteCore.ToggleKind) == 1,
            "duplicate rejection changed component count");
    }

    private static void RunArmatureLinkLifecycle(
        string baselineDigest,
        string baselineMetaDigest)
    {
        var request = ArmatureRequest(true);
        var preview = RequireSuccess(ComponentFeatureWriterTool.HandleCommand(request));
        var apply = RequireSuccess(ComponentFeatureWriterTool.HandleCommand(
            ApprovedRequest(request, preview)
        ));
        Evidence["armatureLink"] = new JObject
        {
            ["request"] = request.DeepClone(),
            ["preview"] = preview.DeepClone(),
            ["apply"] = apply.DeepClone()
        };
        VerifyApplyPayload(apply, ComponentFeatureWriteCore.ArmatureLinkKind);
        Require(FeatureCount(ArmatureHostPath, ComponentFeatureWriteCore.ArmatureLinkKind) == 1,
            "armature CreateNew count");
        Require(
            ComponentFeatureWriteCore.ResolveSavedScene(ScenePath).FileDigest != baselineDigest,
            "armature persistence"
        );
        Undo.PerformUndo();
        SaveAfterUndo("armature undo save");
        Require(FeatureCount(ArmatureHostPath, ComponentFeatureWriteCore.ArmatureLinkKind) == 0,
            "armature undo component residue");
        VerifyBaseline(baselineDigest, baselineMetaDigest, "armature undo restore");
        Undo.ClearAll();
    }

    private static void VerifyPartialFailureRestore(
        string baselineDigest,
        string baselineMetaDigest)
    {
        var request = new ComponentFeatureRequest
        {
            ScenePath = ScenePath,
            GameObjectPath = ToggleHostPath,
            FeatureKind = ComponentFeatureWriteCore.ToggleKind,
            MenuPath = "Wardrobe/Hat",
            TargetObjectPaths = new System.Collections.Generic.List<string>
            {
                "Avatar/Hat",
                "Avatar/Hat/Charm"
            },
            Slider = false,
            DefaultOn = true,
            Saved = true,
            GlobalParameter = "Wardrobe_Hat"
        };
        var snapshot = ComponentFeatureWriteCore.BuildPreview(request);
        Undo.IncrementCurrentGroup();
        var undoGroup = Undo.GetCurrentGroup();
        Undo.SetCurrentGroupName("Probe component feature partial rollback");
        Undo.RegisterCompleteObjectUndo(snapshot.Host.Host, "Probe component feature partial rollback");
        Component created;
        ComponentFeatureWriteCore.InvokePublicCreate(snapshot, out created);
        Require(created != null, "partial rollback component creation");
        Undo.RegisterCreatedObjectUndo(created, "Probe component feature partial rollback");
        EditorSceneManager.MarkSceneDirty(snapshot.Scene.Scene);
        Require(EditorSceneManager.SaveScene(snapshot.Scene.Scene), "partial rollback mutation save");
        Require(FeatureCount(ToggleHostPath, ComponentFeatureWriteCore.ToggleKind) == 1,
            "partial rollback mutation missing");
        Require(
            ComponentFeatureWriteCore.ResolveSavedScene(ScenePath).FileDigest != baselineDigest,
            "partial rollback scene mutation missing"
        );

        var restore = typeof(ComponentFeatureWriterTool).GetMethod(
            "TryRestoreFailedApply",
            BindingFlags.NonPublic | BindingFlags.Static
        );
        Require(restore != null, "partial rollback helper");
        var restored = (bool)restore.Invoke(null, new object[] { snapshot, created, undoGroup });
        Require(restored, "partial rollback helper failed");
        Require(FeatureCount(ToggleHostPath, ComponentFeatureWriteCore.ToggleKind) == 0,
            "partial rollback component residue");
        VerifyBaseline(baselineDigest, baselineMetaDigest, "partial rollback restore");
        Undo.ClearAll();
    }

    private static void VerifyMutationFailureSignals()
    {
        var required = JObject.FromObject(ComponentFeatureWriteCore.BuildMutationFailure(false));
        Require(!required.Value<bool>("success"), "restore-required success");
        var requiredData = required["data"] as JObject;
        Require(requiredData != null, "restore-required data");
        Require(requiredData.Value<bool>("mutationStarted"), "mutationStarted");
        Require(requiredData.Value<bool>("checkpointRestoreRequired"),
            "checkpointRestoreRequired");
        Require(requiredData.Value<bool>("cleanupRequired"), "cleanupRequired");
        Require(requiredData.Value<string>("operationState") == "checkpoint_restore_required",
            "restore-required operation state");

        var restored = JObject.FromObject(ComponentFeatureWriteCore.BuildMutationFailure(true));
        var restoredData = restored["data"] as JObject;
        Require(restoredData != null && restoredData.Value<bool>("restored"), "restored flag");
        Require(!restoredData.Value<bool>("checkpointRestoreRequired"),
            "restored checkpointRestoreRequired");
        Require(!restoredData.Value<bool>("cleanupRequired"), "restored cleanupRequired");
    }

    private static JObject ToggleRequest(bool preview)
    {
        return new JObject
        {
            ["scenePath"] = ScenePath,
            ["gameObjectPath"] = ToggleHostPath,
            ["featureKind"] = ComponentFeatureWriteCore.ToggleKind,
            ["menuPath"] = "Wardrobe/Hat",
            ["targetObjectPaths"] = new JArray("Avatar/Hat", "Avatar/Hat/Charm"),
            ["slider"] = false,
            ["defaultOn"] = true,
            ["saved"] = true,
            ["globalParameter"] = "Wardrobe_Hat",
            ["preview"] = preview,
            ["saveScene"] = !preview,
            ["expectedProjectPath"] = CurrentProjectPath()
        };
    }

    private static JObject ArmatureRequest(bool preview)
    {
        return new JObject
        {
            ["scenePath"] = ScenePath,
            ["gameObjectPath"] = ArmatureHostPath,
            ["featureKind"] = ComponentFeatureWriteCore.ArmatureLinkKind,
            ["linkFromPath"] = "Avatar/PropRoot",
            ["linkTargets"] = new JArray
            {
                new JObject
                {
                    ["targetKind"] = "humanoid_bone",
                    ["target"] = "Chest",
                    ["offset"] = "SpineOffset"
                },
                new JObject
                {
                    ["targetKind"] = "game_object",
                    ["target"] = "Avatar/ChestTarget",
                    ["offset"] = "Socket"
                },
                new JObject
                {
                    ["targetKind"] = "relative_path",
                    ["target"] = "Armature/Hips/Spine",
                    ["offset"] = ""
                }
            },
            ["recursive"] = true,
            ["align"] = false,
            ["preview"] = preview,
            ["saveScene"] = !preview,
            ["expectedProjectPath"] = CurrentProjectPath()
        };
    }

    private static JObject ApprovedRequest(JObject request, JObject preview)
    {
        var approved = (JObject)request.DeepClone();
        approved["preview"] = false;
        approved["saveScene"] = true;
        var scene = preview["scene"] as JObject;
        var host = preview["host"] as JObject;
        Require(scene != null && host != null, "preview evidence objects");
        approved["expectedProjectPath"] = preview["projectPath"];
        approved["expectedSceneGuid"] = scene["guid"];
        approved["expectedSceneHandle"] = scene["handle"];
        approved["expectedSceneFileDigest"] = scene["fileDigestBefore"];
        approved["expectedSceneFileIdentity"] = scene["fileIdentity"];
        approved["expectedSceneMetaDigest"] = scene["metaDigestBefore"];
        approved["expectedSceneMetaIdentity"] = scene["metaIdentity"];
        approved["expectedHostObjectId"] = host["objectId"];
        approved["expectedComponentType"] = host["componentType"];
        approved["expectedComponentIndex"] = host["componentIndex"];
        approved["expectedComponentIdentitySeed"] = host["componentIdentitySeed"];
        approved["expectedBeforeFeatureDigest"] = preview["beforeFeatureDigest"];
        approved["expectedTargetFeatureDigest"] = preview["targetFeatureDigest"];
        approved["expectedCompatibilityDigest"] = preview["compatibilityDigest"];
        approved["expectedPreviewDigest"] = preview["previewDigest"];
        return approved;
    }

    private static void VerifyApplyPayload(JObject apply, string expectedKind)
    {
        Require(!apply.Value<bool>("preview"), "apply preview");
        Require(apply.Value<bool>("changed"), "apply changed");
        Require(apply.Value<bool>("saved"), "apply saved");
        Require(apply.Value<bool>("verified"), "apply verified");
        Require(apply.Value<int>("mutationCount") == 1, "apply mutation count");
        Require(apply.Value<bool>("CreateNew"), "apply CreateNew");
        Require(!apply.Value<bool>("cleanupRequired"), "apply cleanup required");
        var target = apply["target"] as JObject;
        var managed = apply["managedReadback"] as JObject;
        var compatibility = apply["compatibility"] as JObject;
        Require(target != null && target.Value<string>("featureKind") == expectedKind,
            "apply target kind");
        Require(managed != null, "managed readback");
        Require(compatibility != null, "apply compatibility");
        Require(!string.IsNullOrWhiteSpace(apply.Value<string>("managedReadbackDigest")),
            "managed readback digest");
        Require(
            apply.Value<string>("compatibilityDigest")
                == ComponentFeatureWriteCore.ComputeCompatibilityDigest(compatibility),
            "apply compatibility digest"
        );
    }

    private static int FeatureCount(string hostPath, string featureKind)
    {
        var compatibility = ComponentFeatureWriteCore.ValidateCompatibility();
        var host = ComponentFeatureWriteCore.ResolveUniqueGameObject(
            SceneManager.GetActiveScene(),
            hostPath,
            "fixture host"
        );
        var serializedType = featureKind == ComponentFeatureWriteCore.ToggleKind
            ? ComponentFeatureWriteCore.ToggleSerializedType
            : ComponentFeatureWriteCore.ArmatureSerializedType;
        return ComponentFeatureWriteCore.GetRootComponents(host, compatibility.RootComponentType)
            .Count(component => ComponentFeatureWriteCore.ReadManagedReferenceType(component) == serializedType);
    }

    private static void SaveAfterUndo(string label)
    {
        var scene = SceneManager.GetActiveScene();
        EditorSceneManager.MarkSceneDirty(scene);
        Require(EditorSceneManager.SaveScene(scene), label);
        Require(!scene.isDirty, label + " dirty");
    }

    private static void VerifyBaseline(string sceneDigest, string metaDigest, string label)
    {
        var readback = ComponentFeatureWriteCore.ResolveSavedScene(ScenePath);
        if (readback.FileDigest != sceneDigest && BaselineSceneBytes.Length != 0)
        {
            var diagnosticFolder = Path.GetFullPath(Path.Combine(
                CurrentProjectPath(),
                "..",
                "undo-diagnostics"
            ));
            Directory.CreateDirectory(diagnosticFolder);
            File.WriteAllBytes(Path.Combine(diagnosticFolder, "baseline.unity"), BaselineSceneBytes);
            File.WriteAllBytes(
                Path.Combine(diagnosticFolder, "actual-" + label.Replace(' ', '-') + ".unity"),
                File.ReadAllBytes(AbsoluteAssetPath(ScenePath))
            );
        }
        Require(readback.FileDigest == sceneDigest, label + " scene bytes");
        Require(readback.MetaDigest == metaDigest, label + " metadata bytes");
        Require(!readback.Dirty, label + " scene dirty");
    }

    private static void ExpectCompatibilityFailure(string label)
    {
        var failed = false;
        try
        {
            ComponentFeatureWriteCore.ValidateCompatibility();
        }
        catch (ComponentFeatureWriteException)
        {
            failed = true;
        }
        Require(failed, label);
    }

    private static JObject RequireSuccess(object response)
    {
        var value = JObject.FromObject(response);
        Require(
            value.Value<bool>("success"),
            "tool returned an error response: " + value.ToString(Newtonsoft.Json.Formatting.None)
        );
        var data = value["data"] as JObject;
        Require(data != null, "success response data");
        return data;
    }

    private static void RequireFailure(JObject request, string label)
    {
        var value = JObject.FromObject(ComponentFeatureWriterTool.HandleCommand(request));
        Require(!value.Value<bool>("success"), label);
    }

    private static GameObject AddChild(GameObject parent, string name)
    {
        var child = new GameObject(name);
        child.transform.SetParent(parent.transform, false);
        return child;
    }

    private static void EnsureFolder(string parent, string child)
    {
        var path = parent + "/" + child;
        if (!AssetDatabase.IsValidFolder(path))
        {
            AssetDatabase.CreateFolder(parent, child);
        }
    }

    private static string PackageRoot()
    {
        return Path.Combine(CurrentProjectPath(), "Packages", EmbeddedPackageFolder);
    }

    private static string CurrentProjectPath()
    {
        return Directory.GetParent(Application.dataPath)?.FullName
            ?? throw new InvalidOperationException("project root unavailable");
    }

    private static string AbsoluteAssetPath(string assetPath)
    {
        return Path.Combine(
            CurrentProjectPath(),
            assetPath.Replace('/', Path.DirectorySeparatorChar)
        );
    }

    private static string EvidencePath()
    {
        return Path.GetFullPath(Path.Combine(
            CurrentProjectPath(),
            "..",
            "component-feature-fixture-report.json"
        ));
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
        catch (Exception)
        {
        }
    }

    private static void Require(bool value, string label)
    {
        if (!value)
        {
            throw new InvalidOperationException("Probe failed: " + label);
        }
    }

}
