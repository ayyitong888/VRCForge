using System;
using System.Linq;
using VRCForge.Core.MCP;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_duplicate_scene_object",
        Summary = "Preview or CreateNew-duplicate one saved scene hierarchy beneath an exact saved-scene parent."
    )]
    public static class DuplicateSceneObjectTool
    {
        public const string ToolName = "vrc_duplicate_scene_object";

        public class Parameters
        {
            [VRCForgeInput("Saved source scene asset path.", IsRequired = true)] public string sourceScenePath { get; set; } = "";
            [VRCForgeInput("Exact source hierarchy path.", IsRequired = true)] public string sourceObjectPath { get; set; } = "";
            [VRCForgeInput("Saved target parent scene path.", IsRequired = false)] public string targetParentScenePath { get; set; } = "";
            [VRCForgeInput("Exact target parent hierarchy path.", IsRequired = false)] public string targetParentPath { get; set; } = "";
            [VRCForgeInput("New duplicated object name.", IsRequired = false)] public string targetName { get; set; } = "";
            [VRCForgeInput("Preserve world transform while parenting.", IsRequired = false)] public bool? preserveWorldTransform { get; set; } = false;
            [VRCForgeInput("Return a non-mutating duplicate preview.", IsRequired = false)] public bool? preview { get; set; } = false;
            [VRCForgeInput("Save the target scene for apply.", IsRequired = false)] public bool? saveScene { get; set; } = false;
            [VRCForgeInput("Must remain false; overwrite is unsupported.", IsRequired = false)] public bool? overwrite { get; set; } = false;
            [VRCForgeInput("Expected active Unity project root from preview.", IsRequired = false)] public string expectedProjectPath { get; set; } = "";
            [VRCForgeInput("Expected source scene GUID.", IsRequired = false)] public string expectedSourceSceneGuid { get; set; } = "";
            [VRCForgeInput("Expected source scene handle.", IsRequired = false)] public int? expectedSourceSceneHandle { get; set; }
            [VRCForgeInput("Expected source object identity.", IsRequired = false)] public string expectedSourceObjectId { get; set; } = "";
            [VRCForgeInput("Expected source hierarchy digest.", IsRequired = false)] public string expectedSourceHierarchyDigest { get; set; } = "";
            [VRCForgeInput("Expected source scene file digest.", IsRequired = false)] public string expectedSourceSceneFileDigest { get; set; } = "";
            [VRCForgeInput("Expected source scene file identity.", IsRequired = false)] public string expectedSourceSceneFileIdentity { get; set; } = "";
            [VRCForgeInput("Expected source scene meta digest.", IsRequired = false)] public string expectedSourceSceneMetaDigest { get; set; } = "";
            [VRCForgeInput("Expected source scene meta identity.", IsRequired = false)] public string expectedSourceSceneMetaIdentity { get; set; } = "";
            [VRCForgeInput("Expected target scene GUID.", IsRequired = false)] public string expectedTargetSceneGuid { get; set; } = "";
            [VRCForgeInput("Expected target scene handle.", IsRequired = false)] public int? expectedTargetSceneHandle { get; set; }
            [VRCForgeInput("Expected target parent object identity.", IsRequired = false)] public string expectedTargetParentObjectId { get; set; } = "";
            [VRCForgeInput("Expected target parent hierarchy digest.", IsRequired = false)] public string expectedTargetParentHierarchyDigest { get; set; } = "";
            [VRCForgeInput("Expected target scene file digest.", IsRequired = false)] public string expectedTargetSceneFileDigest { get; set; } = "";
            [VRCForgeInput("Expected target scene file identity.", IsRequired = false)] public string expectedTargetSceneFileIdentity { get; set; } = "";
            [VRCForgeInput("Expected target scene meta digest.", IsRequired = false)] public string expectedTargetSceneMetaDigest { get; set; } = "";
            [VRCForgeInput("Expected target scene meta identity.", IsRequired = false)] public string expectedTargetSceneMetaIdentity { get; set; } = "";
            [VRCForgeInput("Expected destination hierarchy path.", IsRequired = false)] public string expectedDestinationPath { get; set; } = "";
            [VRCForgeInput("Expected preview digest.", IsRequired = false)] public string expectedPreviewDigest { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var parameters = @params ?? new JObject();
                var sourceScenePath = parameters["sourceScenePath"]?.ToString() ?? string.Empty;
                var sourceObjectPath = parameters["sourceObjectPath"]?.ToString() ?? string.Empty;
                var targetParentScenePath = parameters["targetParentScenePath"]?.ToString() ?? string.Empty;
                var targetParentPath = parameters["targetParentPath"]?.ToString() ?? string.Empty;
                var targetName = parameters["targetName"]?.ToString() ?? string.Empty;
                var preserveWorldTransform = parameters["preserveWorldTransform"]?.Value<bool?>() ?? false;
                var preview = parameters["preview"]?.Value<bool?>() ?? false;
                var saveScene = parameters["saveScene"]?.Value<bool?>() ?? false;
                var overwrite = parameters["overwrite"]?.Value<bool?>() ?? false;
                if (overwrite)
                {
                    throw new SceneObjectCopyException("Duplicate overwrite is not supported.");
                }
                if (preview && saveScene)
                {
                    throw new SceneObjectCopyException("Preview cannot save a scene.");
                }

                var snapshot = SceneObjectCopyCore.BuildDuplicatePreview(
                    sourceScenePath,
                    sourceObjectPath,
                    targetParentScenePath,
                    targetParentPath,
                    targetName,
                    preserveWorldTransform);
                if (preview)
                {
                    return SceneObjectCopyCore.Success(
                    snapshot.ToPayload());
                }

                SceneObjectCopyCore.VerifyDuplicateExpected(parameters, snapshot);
                return Apply(snapshot);
            }
            catch (Exception exception)
            {
                return SceneObjectCopyCore.Failure(exception);
            }
        }

        private static object Apply(DuplicatePreviewSnapshot snapshot)
        {
            var undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("Duplicate VRCForge scene object");
            GameObject duplicate = null;
            var mutationStarted = false;
            try
            {
                var source = snapshot.Source.GameObject;
                var sourceLocalPosition = source.transform.localPosition;
                var sourceLocalRotation = source.transform.localRotation;
                var sourceLocalScale = source.transform.localScale;
                duplicate = UnityEngine.Object.Instantiate(source);
                mutationStarted = true;
                duplicate.name = snapshot.Target.Name;
                SceneManager.MoveGameObjectToScene(duplicate, snapshot.Target.Scene.Scene);
                duplicate.transform.SetParent(
                    snapshot.Target.Parent.transform,
                    snapshot.PreserveWorldTransform);
                if (!snapshot.PreserveWorldTransform)
                {
                    duplicate.transform.localPosition = sourceLocalPosition;
                    duplicate.transform.localRotation = sourceLocalRotation;
                    duplicate.transform.localScale = sourceLocalScale;
                }
                Undo.RegisterCreatedObjectUndo(duplicate, "Duplicate VRCForge scene object");

                var inMemoryReadback = SceneObjectCopyCore.ResolveUniqueGameObject(
                    snapshot.Target.Scene.Scene,
                    snapshot.Target.ObjectPath,
                    "duplicate destination");
                if (!ReferenceEquals(inMemoryReadback, duplicate))
                {
                    throw new SceneObjectCopyException("The duplicate in-memory readback was not exact.");
                }
                var hierarchyDigestBeforeSave = SceneObjectCopyCore.ComputeHierarchyDigest(duplicate);
                EditorSceneManager.MarkSceneDirty(snapshot.Target.Scene.Scene);
                if (!EditorSceneManager.SaveScene(snapshot.Target.Scene.Scene))
                {
                    throw new SceneObjectCopyException("The duplicate target scene could not be saved.");
                }

                var readback = SceneObjectCopyCore.BuildSourceSnapshot(
                    snapshot.Target.Scene.Path,
                    snapshot.Target.ObjectPath);
                if (readback.Scene.Guid != snapshot.Target.Scene.Guid
                    || readback.Scene.Handle != snapshot.Target.Scene.Handle
                    || readback.HierarchyDigest != hierarchyDigestBeforeSave)
                {
                    throw new SceneObjectCopyException("The duplicate persisted readback was not exact.");
                }
                if (readback.Scene.FileDigest == snapshot.Target.Scene.FileDigest)
                {
                    throw new SceneObjectCopyException("The duplicate target scene did not persist a change.");
                }
                SceneObjectCopyCore.VerifySourceUnchanged(
                    snapshot.Source,
                    snapshot.Source.Scene.Path != snapshot.Target.Scene.Path);
                Undo.CollapseUndoOperations(undoGroup);

                return SceneObjectCopyCore.Success(new
                {
                    schema = SceneObjectCopyCore.ResultSchema,
                    ok = true,
                    operation = SceneObjectCopyCore.DuplicateOperation,
                    preview = false,
                    verified = true,
                    changed = true,
                    saved = true,
                    mutationCount = 1,
                    source = new
                    {
                        scenePath = snapshot.Source.Scene.Path,
                        sceneGuid = snapshot.Source.Scene.Guid,
                        sceneHandle = snapshot.Source.Scene.Handle,
                        objectPath = snapshot.Source.ObjectPath,
                        objectId = snapshot.Source.ObjectId,
                        hierarchyDigestBefore = snapshot.Source.HierarchyDigest,
                        hierarchyDigestAfter = snapshot.Source.HierarchyDigest,
                        sceneFileDigestBefore = snapshot.Source.Scene.FileDigest,
                        sceneFileDigestAfter = snapshot.Source.Scene.Path == snapshot.Target.Scene.Path
                            ? readback.Scene.FileDigest
                            : snapshot.Source.Scene.FileDigest,
                        sceneFileIdentityBefore = snapshot.Source.Scene.FileIdentity,
                        sceneFileIdentityAfter = snapshot.Source.Scene.Path == snapshot.Target.Scene.Path
                            ? readback.Scene.FileIdentity
                            : snapshot.Source.Scene.FileIdentity,
                        sceneMetaDigest = snapshot.Source.Scene.Path == snapshot.Target.Scene.Path
                            ? readback.Scene.MetaDigest
                            : snapshot.Source.Scene.MetaDigest,
                        sceneMetaIdentity = snapshot.Source.Scene.Path == snapshot.Target.Scene.Path
                            ? readback.Scene.MetaIdentity
                            : snapshot.Source.Scene.MetaIdentity,
                        sceneFileChangedByTargetWrite = snapshot.Source.Scene.Path
                            == snapshot.Target.Scene.Path,
                        unchanged = true
                    },
                    target = new
                    {
                        scenePath = readback.Scene.Path,
                        sceneGuid = readback.Scene.Guid,
                        sceneHandle = readback.Scene.Handle,
                        parentPath = snapshot.Target.ParentPath,
                        parentObjectId = snapshot.Target.ParentObjectId,
                        objectPath = readback.ObjectPath,
                        objectId = readback.ObjectId,
                        hierarchyDigest = readback.HierarchyDigest,
                        sceneFileDigestBefore = snapshot.Target.Scene.FileDigest,
                        sceneFileDigestAfter = readback.Scene.FileDigest,
                        sceneFileIdentityBefore = snapshot.Target.Scene.FileIdentity,
                        sceneFileIdentityAfter = readback.Scene.FileIdentity,
                        sceneMetaDigest = readback.Scene.MetaDigest,
                        sceneMetaIdentity = readback.Scene.MetaIdentity,
                        readbackExact = true
                    },
                    preserveWorldTransform = snapshot.PreserveWorldTransform,
                    previewDigest = snapshot.PreviewDigest,
                    cleanupRequired = false
                });
            }
            catch (Exception exception)
            {
                if (!mutationStarted)
                {
                    return SceneObjectCopyCore.Failure(exception);
                }
                var restored = CleanupFailedApply(snapshot, duplicate, true);
                return SceneObjectCopyCore.BuildMutationFailure(
                    SceneObjectCopyCore.DuplicateOperation,
                    restored);
            }
        }

        private static bool CleanupFailedApply(
            DuplicatePreviewSnapshot snapshot,
            GameObject duplicate,
            bool mutationStarted)
        {
            if (!mutationStarted)
            {
                return true;
            }
            try
            {
                if (duplicate != null)
                {
                    UnityEngine.Object.DestroyImmediate(duplicate);
                }
                EditorSceneManager.MarkSceneDirty(snapshot.Target.Scene.Scene);
                if (!EditorSceneManager.SaveScene(snapshot.Target.Scene.Scene))
                {
                    return false;
                }
                if (snapshot.Target.Parent.transform.Cast<Transform>().Any(child =>
                    string.Equals(child.name, snapshot.Target.Name, StringComparison.OrdinalIgnoreCase)))
                {
                    return false;
                }
                var restoredTargetScene = SceneObjectCopyCore.ResolveSavedScene(
                    snapshot.Target.Scene.Path,
                    "target scene cleanup");
                if (restoredTargetScene.Guid != snapshot.Target.Scene.Guid
                    || restoredTargetScene.Handle != snapshot.Target.Scene.Handle
                    || restoredTargetScene.FileDigest != snapshot.Target.Scene.FileDigest)
                {
                    return false;
                }
                SceneObjectCopyCore.VerifySourceUnchanged(snapshot.Source, true);
                return true;
            }
            catch
            {
                return false;
            }
        }
    }
}
