using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRCForge.Editor;

public static class ConstraintSourceFixtureProbe
{
    private const string ProbeFolder = "Assets/VRCForge/Generated/ConstraintSourceProbe";
    private const string ScenePath = ProbeFolder + "/ConstraintSourceProbe.unity";
    private const string HostPath = "Avatar/ConstraintHost";
    private const string PositionConstraintType =
        "VRC.SDK3.Dynamics.Constraint.Components.VRCPositionConstraint";

    public static void Run()
    {
        try
        {
            CleanupBestEffort();
            VerifyStructuredMutationFailureSignals();
            var component = PrepareScene();
            var baseline = SceneObjectCopyCore.ResolveSavedScene(ScenePath, "fixture baseline");
            var baselineDigest = baseline.FileDigest;
            var baselineMetaDigest = baseline.MetaDigest;
            var target = BuildTargetSources(18);

            VerifyRejectedRequestsStayReadOnly(baselineDigest, baselineMetaDigest);
            VerifyDirectCapture(target);
            var preview = RequireSuccess(ConstraintSourceTool.HandleCommand(
                Request(target, true)
            ));
            Require(preview.Value<bool>("preview"), "preview flag");
            Require(!preview.Value<bool>("changed"), "preview changed");
            Require(!preview.Value<bool>("saved"), "preview saved");
            Require(preview.Value<bool>("wouldChange"), "preview wouldChange");
            Require(
                SceneObjectCopyCore.ResolveSavedScene(ScenePath, "preview zero write").FileDigest
                    == baselineDigest,
                "preview zero write"
            );
            Require(
                preview.Value<string>("sceneMetaDigestAfter") == baselineMetaDigest,
                "preview metadata write"
            );

            VerifyStalePreconditionRejected(preview, target, baselineDigest);

            var apply = RequireSuccess(ConstraintSourceTool.HandleCommand(
                ApprovedRequest(target, preview)
            ));
            Require(!apply.Value<bool>("preview"), "apply preview");
            Require(apply.Value<bool>("changed"), "apply changed");
            Require(apply.Value<bool>("saved"), "apply saved");
            Require(apply.Value<bool>("verified"), "apply verified");
            VerifyOrderedReadback(component, target, "apply list order");
            var appliedDigest = SceneObjectCopyCore.ResolveSavedScene(
                ScenePath,
                "apply readback"
            ).FileDigest;
            Require(appliedDigest != baselineDigest, "apply scene persistence");

            var noOpPreview = RequireSuccess(ConstraintSourceTool.HandleCommand(
                Request(target, true)
            ));
            Require(!noOpPreview.Value<bool>("wouldChange"), "no-op preview");
            var noOp = RequireSuccess(ConstraintSourceTool.HandleCommand(
                ApprovedRequest(target, noOpPreview)
            ));
            Require(!noOp.Value<bool>("changed"), "no-op changed");
            Require(!noOp.Value<bool>("saved"), "no-op saved");
            Require(
                SceneObjectCopyCore.ResolveSavedScene(ScenePath, "no-op readback").FileDigest
                    == appliedDigest,
                "no-op scene bytes"
            );

            var empty = new JArray();
            var restorePreview = RequireSuccess(ConstraintSourceTool.HandleCommand(
                Request(empty, true)
            ));
            var restore = RequireSuccess(ConstraintSourceTool.HandleCommand(
                ApprovedRequest(empty, restorePreview)
            ));
            Require(restore.Value<bool>("changed"), "restore changed");
            Require(restore.Value<bool>("saved"), "restore saved");
            Require(
                SceneObjectCopyCore.ResolveSavedScene(ScenePath, "restore baseline").FileDigest
                    == baselineDigest,
                "restore baseline"
            );
            VerifyOrderedReadback(component, empty, "restore list order");
            VerifyPartialFailureRestore(component, target, baselineDigest);

            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            Require(AssetDatabase.DeleteAsset(ProbeFolder), "fixture cleanup");
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            Require(!File.Exists(AbsoluteAssetPath(ScenePath)), "scene residue");
            Require(!File.Exists(AbsoluteAssetPath(ScenePath) + ".meta"), "scene metadata residue");

            Debug.Log("VRCFORGE_CONSTRAINT_SOURCE_PROBE_OK");
            EditorApplication.Exit(0);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            CleanupBestEffort();
            EditorApplication.Exit(1);
        }
    }

    private static Component PrepareScene()
    {
        EnsureFolder("Assets/VRCForge", "Generated");
        if (AssetDatabase.IsValidFolder(ProbeFolder))
        {
            AssetDatabase.DeleteAsset(ProbeFolder);
        }
        AssetDatabase.CreateFolder("Assets/VRCForge/Generated", "ConstraintSourceProbe");
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var avatar = new GameObject("Avatar");
        var host = new GameObject("ConstraintHost");
        host.transform.SetParent(avatar.transform, false);
        for (var index = 0; index < 18; index++)
        {
            var source = new GameObject("Source" + index.ToString("00"));
            source.transform.SetParent(avatar.transform, false);
        }
        var duplicateParent = new GameObject("DuplicateParent");
        duplicateParent.transform.SetParent(avatar.transform, false);
        for (var index = 0; index < 2; index++)
        {
            var duplicate = new GameObject("Duplicate");
            duplicate.transform.SetParent(duplicateParent.transform, false);
        }
        var type = ResolveExactType(PositionConstraintType);
        var component = host.AddComponent(type);
        Require(component != null, "constraint component creation");
        Require(EditorSceneManager.SaveScene(scene, ScenePath), "fixture scene save");
        Require(!scene.isDirty, "fixture scene dirty after save");
        return component;
    }

    private static void VerifyRejectedRequestsStayReadOnly(
        string baselineDigest,
        string baselineMetaDigest)
    {
        var unsupported = Request(new JArray(), true);
        unsupported["constraintKind"] = "unsupported";
        RequireFailure(unsupported, "unsupported kind accepted");

        var unknownField = Request(new JArray
        {
            new JObject
            {
                ["sourcePath"] = "Avatar/Source00",
                ["weight"] = 0.5f,
                ["unknown"] = true
            }
        }, true);
        RequireFailure(unknownField, "unknown source field accepted");

        var nullSource = Request(new JArray
        {
            new JObject { ["sourcePath"] = JValue.CreateNull(), ["weight"] = 0.5f }
        }, true);
        RequireFailure(nullSource, "null source accepted");

        var invalidWeight = Request(new JArray
        {
            new JObject { ["sourcePath"] = "Avatar/Source00", ["weight"] = 1.01f }
        }, true);
        RequireFailure(invalidWeight, "weight bounds accepted");

        var ambiguous = Request(new JArray
        {
            new JObject
            {
                ["sourcePath"] = "Avatar/DuplicateParent/Duplicate",
                ["weight"] = 0.5f
            }
        }, true);
        RequireFailure(ambiguous, "ambiguous source accepted");

        var duplicate = Request(new JArray
        {
            new JObject { ["sourcePath"] = "Avatar/Source00", ["weight"] = 0.5f },
            new JObject { ["sourcePath"] = "Avatar/Source00", ["weight"] = 0.25f }
        }, true);
        RequireFailure(duplicate, "duplicate source accepted");

        var readback = SceneObjectCopyCore.ResolveSavedScene(ScenePath, "rejection readback");
        Require(readback.FileDigest == baselineDigest, "rejection scene bytes");
        Require(readback.MetaDigest == baselineMetaDigest, "rejection metadata bytes");
        Require(!readback.Scene.isDirty, "rejection scene dirty");
    }

    private static void VerifyStalePreconditionRejected(
        JObject preview,
        JArray target,
        string baselineDigest)
    {
        var stale = ApprovedRequest(target, preview);
        stale["expectedSceneFileDigest"] = new string('a', 64);
        RequireFailure(stale, "stale precondition accepted");
        Require(
            SceneObjectCopyCore.ResolveSavedScene(ScenePath, "stale readback").FileDigest
                == baselineDigest,
            "stale request changed scene"
        );
    }

    private static void VerifyPartialFailureRestore(
        Component component,
        JArray target,
        string baselineDigest)
    {
        var flags = BindingFlags.NonPublic | BindingFlags.Static;
        var capture = typeof(ConstraintSourceTool).GetMethod("CaptureSnapshot", flags);
        var restore = typeof(ConstraintSourceTool).GetMethod("TryRestoreBeforeSources", flags);
        Require(capture != null && restore != null, "partial failure helpers");
        var snapshot = capture.Invoke(
            null,
            new object[] { ScenePath, HostPath, "position", 0, target }
        );
        Require(snapshot != null, "partial failure snapshot");
        var snapshotType = snapshot.GetType();
        var planField = snapshotType.GetField("Plan", BindingFlags.NonPublic | BindingFlags.Instance);
        var sceneField = snapshotType.GetField("Scene", BindingFlags.NonPublic | BindingFlags.Instance);
        Require(planField != null && sceneField != null, "partial failure snapshot fields");
        var plan = planField.GetValue(snapshot) as StructuredListPlan;
        var scene = sceneField.GetValue(snapshot) as SavedSceneSnapshot;
        Require(plan != null && scene != null, "partial failure snapshot values");

        TypedStructuredListCore.Apply(component, plan);
        EditorUtility.SetDirty(component);
        EditorSceneManager.MarkSceneDirty(scene.Scene);
        Require(EditorSceneManager.SaveScene(scene.Scene), "partial failure mutation save");
        Require(
            SceneObjectCopyCore.ResolveSavedScene(ScenePath, "partial failure mutation").FileDigest
                != baselineDigest,
            "partial failure mutation missing"
        );
        var restored = (bool)restore.Invoke(null, new[] { snapshot });
        Require(restored, "partial failure restore failed");
        Require(
            SceneObjectCopyCore.ResolveSavedScene(ScenePath, "partial failure restore").FileDigest
                == baselineDigest,
            "partial failure restore baseline"
        );
    }

    private static void VerifyDirectCapture(JArray target)
    {
        var capture = typeof(ConstraintSourceTool).GetMethod(
            "CaptureSnapshot",
            BindingFlags.NonPublic | BindingFlags.Static
        );
        Require(capture != null, "capture helper");
        try
        {
            Require(
                capture.Invoke(null, new object[] { ScenePath, HostPath, "position", 0, target })
                    != null,
                "capture result"
            );
        }
        catch (TargetInvocationException exception) when (exception.InnerException != null)
        {
            throw exception.InnerException;
        }
    }

    private static void VerifyStructuredMutationFailureSignals()
    {
        var builder = typeof(ConstraintSourceTool).GetMethod(
            "BuildMutationFailure",
            BindingFlags.NonPublic | BindingFlags.Static
        );
        Require(builder != null, "mutation failure builder");
        var required = JObject.FromObject(builder.Invoke(null, new object[] { false }));
        Require(!required.Value<bool>("success"), "restore-required success");
        var requiredData = (JObject)required["data"];
        Require(requiredData.Value<bool>("mutationStarted"), "mutationStarted");
        Require(requiredData.Value<bool>("checkpointRestoreRequired"), "checkpointRestoreRequired");
        Require(requiredData.Value<bool>("cleanupRequired"), "cleanupRequired");
        Require(requiredData.Value<string>("operationState") == "checkpoint_restore_required",
            "restore-required operation state");

        var restored = JObject.FromObject(builder.Invoke(null, new object[] { true }));
        var restoredData = (JObject)restored["data"];
        Require(restoredData.Value<bool>("restored"), "restored flag");
        Require(!restoredData.Value<bool>("checkpointRestoreRequired"),
            "restored checkpointRestoreRequired");
        Require(!restoredData.Value<bool>("cleanupRequired"), "restored cleanupRequired");
    }

    private static void VerifyOrderedReadback(Component component, JArray expected, string label)
    {
        var sourcesField = component.GetType().GetField(
            "Sources",
            BindingFlags.Public | BindingFlags.Instance
        );
        Require(sourcesField != null, label + " field");
        var enumerable = sourcesField.GetValue(component) as IEnumerable;
        Require(enumerable != null, label + " enumerable");
        var actual = enumerable.Cast<object>().ToList();
        Require(actual.Count == expected.Count, label + " count");
        for (var index = 0; index < actual.Count; index++)
        {
            var elementType = actual[index].GetType();
            var transformField = elementType.GetField("SourceTransform", BindingFlags.Public | BindingFlags.Instance);
            var weightField = elementType.GetField("Weight", BindingFlags.Public | BindingFlags.Instance);
            Require(transformField != null && weightField != null, label + " element fields");
            var transform = transformField.GetValue(actual[index]) as Transform;
            var weight = (float)weightField.GetValue(actual[index]);
            Require(transform != null, label + " null transform");
            Require(
                SceneObjectCopyCore.GetHierarchyPath(transform)
                    == expected[index].Value<string>("sourcePath"),
                label + " path " + index
            );
            Require(
                BitConverter.GetBytes(weight).SequenceEqual(
                    BitConverter.GetBytes(expected[index].Value<float>("weight"))
                ),
                label + " weight " + index
            );
        }
    }

    private static JObject Request(JArray sources, bool preview)
    {
        return new JObject
        {
            ["scenePath"] = ScenePath,
            ["gameObjectPath"] = HostPath,
            ["constraintKind"] = "position",
            ["componentIndex"] = 0,
            ["sources"] = sources.DeepClone(),
            ["preview"] = preview,
            ["saveScene"] = !preview
        };
    }

    private static JObject ApprovedRequest(JArray sources, JObject preview)
    {
        var request = Request(sources, false);
        request["expectedProjectPath"] = CurrentProjectPath();
        request["expectedScenePath"] = preview["scenePath"];
        request["expectedSceneGuid"] = preview["sceneGuid"];
        request["expectedSceneHandle"] = preview["sceneHandle"];
        request["expectedSceneFileDigest"] = preview["sceneFileDigestBefore"];
        request["expectedSceneFileIdentity"] = preview["sceneFileIdentity"];
        request["expectedSceneMetaDigest"] = preview["sceneMetaDigestBefore"];
        request["expectedSceneMetaIdentity"] = preview["sceneMetaIdentity"];
        request["expectedGameObjectPath"] = preview["gameObjectPath"];
        request["expectedConstraintKind"] = preview["constraintKind"];
        request["expectedComponentType"] = preview["componentType"];
        request["expectedComponentIndex"] = preview["componentIndex"];
        request["expectedComponentId"] = preview["componentId"];
        request["expectedComponentGlobalId"] = preview["componentGlobalId"];
        request["expectedBeforeSourcesDigest"] = preview["beforeSourcesDigest"];
        request["expectedTargetSourcesDigest"] = preview["targetSourcesDigest"];
        return request;
    }

    private static JArray BuildTargetSources(int count)
    {
        var sources = new JArray();
        for (var index = count - 1; index >= 0; index--)
        {
            sources.Add(new JObject
            {
                ["sourcePath"] = "Avatar/Source" + index.ToString("00"),
                ["weight"] = (index + 1) / 20f
            });
        }
        return sources;
    }

    private static JObject RequireSuccess(object response)
    {
        var value = JObject.FromObject(response);
        Require(
            value.Value<bool>("success"),
            "tool returned an error response: " + value.ToString(Newtonsoft.Json.Formatting.None)
        );
        return (JObject)value["data"];
    }

    private static void RequireFailure(JObject request, string label)
    {
        var value = JObject.FromObject(ConstraintSourceTool.HandleCommand(request));
        Require(!value.Value<bool>("success"), label);
    }

    private static Type ResolveExactType(string fullName)
    {
        var matches = AppDomain.CurrentDomain.GetAssemblies()
            .Select(assembly => assembly.GetType(fullName, false, false))
            .Where(type => type != null)
            .ToList();
        Require(matches.Count == 1, "constraint type resolution");
        return matches[0];
    }

    private static void EnsureFolder(string parent, string child)
    {
        var path = parent + "/" + child;
        if (!AssetDatabase.IsValidFolder(path))
        {
            AssetDatabase.CreateFolder(parent, child);
        }
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
