using System;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_revert_removed_component",
        Summary = "when-to-use: preview or restore one exact removed-component override on a connected scene Prefab instance after approval. when-NOT-to-use: do not add a new component, alter the Prefab asset, restore a removed GameObject, or guess an ambiguous source identity. Negative example: do not use this to recreate a missing component from only its type name.")]
    public static class RevertRemovedComponentTool
    {
        public const string ToolName = "vrc_revert_removed_component";

        public sealed class Parameters
        {
            [VRCForgeInput("Exact full hierarchy path of the Prefab-instance GameObject that owns the removed-component override.", IsRequired = true)]
            public string gameObjectPath { get; set; } = "";

            [VRCForgeInput("Exact GlobalObjectId of the removed source component on the Prefab asset.", IsRequired = true)]
            public string sourceComponentGlobalObjectId { get; set; } = "";

            [VRCForgeInput("Exact fully-qualified component type name of the removed source component.", IsRequired = true)]
            public string componentType { get; set; } = "";

            [VRCForgeInput("Exact zero-based index of the source component among components of the same type on its Prefab-asset GameObject.", IsRequired = true)]
            public int? sourceComponentIndex { get; set; }

            [VRCForgeInput("If true, verify and report the exact removed override without mutating the scene.", IsRequired = false)]
            public bool? preview { get; set; } = false;
        }

        private sealed class RemovedMatch
        {
            internal Component AssetComponent;
            internal string SourceGlobalObjectId;
            internal int SourceComponentIndex;
        }

        private sealed class RemovedSnapshot
        {
            internal int TotalCount;
            internal int TypeCount;
            internal RemovedMatch Match;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters();
            SavedSceneSnapshot beforeScene = null;
            var undoGroup = -1;
            var mutationStarted = false;
            var failureStage = "validation";
            bool? mutationApplied = null;
            string exactPath = null;
            Type componentType = null;
            RemovedSnapshot beforeRemoved = null;
            var beforeComponentCount = 0;
            try
            {
                ValidateExactInputs(p);
                var target = ComponentCrudCore.ResolveGameObject(p.gameObjectPath);
                exactPath = ComponentCrudCore.GetHierarchyPath(target.transform);
                if (!string.Equals(exactPath, ComponentCrudCore.NormalizePath(p.gameObjectPath), StringComparison.Ordinal))
                {
                    throw new InvalidOperationException("gameObjectPath must be an exact full hierarchy path; unique-name fallback is not allowed.");
                }
                if (!PrefabUtility.IsPartOfPrefabInstance(target))
                {
                    throw new InvalidOperationException("The target GameObject is not part of a connected Prefab instance.");
                }

                componentType = ComponentCrudCore.ResolveComponentType(p.componentType);
                if (!string.Equals(componentType.FullName, p.componentType.Trim(), StringComparison.Ordinal))
                {
                    throw new InvalidOperationException("componentType must be the exact fully-qualified component type name.");
                }

                beforeRemoved = CaptureRemoved(target, componentType, p.sourceComponentGlobalObjectId.Trim(), p.sourceComponentIndex.Value);
                beforeComponentCount = target.GetComponents(componentType).Length;
                beforeScene = ComponentCrudCore.ResolveSavedSceneFor(target);
                var targetGlobalObjectId = ObjectId(target);
                var source = beforeRemoved.Match.AssetComponent;
                var sourceAssetPath = AssetDatabase.GetAssetPath(source) ?? string.Empty;
                var sourceAssetGuid = AssetDatabase.AssetPathToGUID(sourceAssetPath) ?? string.Empty;
                var sourceIdentityRead = AssetDatabase.TryGetGUIDAndLocalFileIdentifier(
                    source,
                    out var sourceGuidReadback,
                    out long sourceLocalFileId);
                if (!sourceIdentityRead
                    || string.IsNullOrWhiteSpace(sourceAssetPath)
                    || sourceAssetGuid.Length != 32
                    || sourceLocalFileId == 0
                    || !string.Equals(sourceAssetGuid, sourceGuidReadback, StringComparison.Ordinal))
                {
                    throw new InvalidOperationException("The removed source component asset identity could not be read back exactly.");
                }

                var before = SnapshotPayload(
                    targetGlobalObjectId,
                    beforeComponentCount,
                    beforeRemoved.TotalCount,
                    beforeRemoved.TypeCount,
                    beforeRemoved.Match.SourceGlobalObjectId,
                    beforeRemoved.Match.SourceComponentIndex);

                if (p.preview ?? false)
                {
                    return VRCForgeToolResult.Completed(
                        $"Preview: verified removed '{componentType.Name}' source index {p.sourceComponentIndex.Value} on '{exactPath}'.",
                        new
                        {
                            action = "revert_removed_component",
                            preview = true,
                            gameObjectPath = exactPath,
                            componentType = componentType.FullName,
                            sourceComponentGlobalObjectId = beforeRemoved.Match.SourceGlobalObjectId,
                            sourceComponentIndex = beforeRemoved.Match.SourceComponentIndex,
                            sourceAssetPath,
                            sourceAssetGuid,
                            sourceLocalFileId,
                            before,
                            mutationStarted = false,
                            committed = false,
                            commitState = "preview_only",
                            checkpointRecoveryRequired = false
                        });
                }

                Undo.IncrementCurrentGroup();
                undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName("Revert VRCForge removed component override");
                mutationStarted = true;
                failureStage = "unity_mutation";
                PrefabUtility.RevertRemovedComponent(
                    target,
                    beforeRemoved.Match.AssetComponent,
                    InteractionMode.UserAction);
                mutationApplied = true;
                EditorUtility.SetDirty(target);

                failureStage = "scene_save";
                var afterScene = ComponentCrudCore.SaveAndResolveScene(beforeScene);
                failureStage = "persisted_readback";
                var readbackTarget = SceneObjectCopyCore.ResolveUniqueGameObject(
                    afterScene.Scene,
                    exactPath,
                    "reverted removed component target");
                var afterRemoved = CaptureRemovedOptional(
                    readbackTarget,
                    componentType,
                    p.sourceComponentGlobalObjectId.Trim(),
                    p.sourceComponentIndex.Value);
                var afterComponents = readbackTarget.GetComponents(componentType);
                var restoredComponents = afterComponents
                    .Where(item => SourceObjectId(item) == p.sourceComponentGlobalObjectId.Trim())
                    .ToArray();

                if (!string.Equals(ObjectId(readbackTarget), targetGlobalObjectId, StringComparison.Ordinal)
                    || afterComponents.Length != beforeComponentCount + 1
                    || afterRemoved.TotalCount != beforeRemoved.TotalCount - 1
                    || afterRemoved.TypeCount != beforeRemoved.TypeCount - 1
                    || afterRemoved.Match != null
                    || restoredComponents.Length != 1
                    || afterScene.FileDigest == beforeScene.FileDigest)
                {
                    throw new InvalidOperationException("The reverted removed component persisted readback was not exact.");
                }

                var restored = restoredComponents[0];
                var restoredComponentIndex = Array.IndexOf(afterComponents, restored);
                Undo.CollapseUndoOperations(undoGroup);
                var after = SnapshotPayload(
                    ObjectId(readbackTarget),
                    afterComponents.Length,
                    afterRemoved.TotalCount,
                    afterRemoved.TypeCount,
                    SourceObjectId(restored),
                    p.sourceComponentIndex.Value);

                return VRCForgeToolResult.Completed(
                    $"Restored removed '{componentType.Name}' source index {p.sourceComponentIndex.Value} on '{exactPath}'.",
                    new
                    {
                        action = "revert_removed_component",
                        preview = false,
                        gameObjectPath = exactPath,
                        componentType = componentType.FullName,
                        sourceComponentGlobalObjectId = p.sourceComponentGlobalObjectId.Trim(),
                        sourceComponentIndex = p.sourceComponentIndex.Value,
                        restoredComponentGlobalObjectId = ObjectId(restored),
                        restoredComponentIndex,
                        sourceAssetPath,
                        sourceAssetGuid,
                        sourceLocalFileId,
                        scenePath = afterScene.Path,
                        before,
                        after,
                        sceneSaved = true,
                        persistedReadback = true,
                        mutationStarted = true,
                        committed = true,
                        commitState = "committed",
                        checkpointRecoveryRequired = false
                    });
            }
            catch (Exception ex)
            {
                if (mutationStarted && beforeScene != null && undoGroup >= 0)
                {
                    var expectedSourceId = (p.sourceComponentGlobalObjectId ?? string.Empty).Trim();
                    var expectedSourceIndex = p.sourceComponentIndex ?? -1;
                    var cleanup = ComponentCrudCore.RestoreFailedMutation(
                        undoGroup,
                        beforeScene,
                        exactPath,
                        restoredObject =>
                        {
                            var restoredRemoved = CaptureRemovedOptional(
                                restoredObject,
                                componentType,
                                expectedSourceId,
                                expectedSourceIndex);
                            return restoredObject.GetComponents(componentType).Length == beforeComponentCount
                                && restoredRemoved.TotalCount == beforeRemoved.TotalCount
                                && restoredRemoved.TypeCount == beforeRemoved.TypeCount
                                && restoredRemoved.Match != null;
                        });
                    return ComponentCrudCore.FailedMutationResult(
                        "removed_component_revert_failed_after_mutation",
                        "revert_removed_component",
                        failureStage,
                        ex,
                        beforeScene,
                        cleanup,
                        mutationApplied);
                }
                return VRCForgeToolResult.FailedWithCode(
                    "removed_component_revert_validation_failed",
                    $"Revert removed component failed: {ComponentCrudCore.SafeExceptionMessage(ex)}");
            }
        }

        private static void ValidateExactInputs(Parameters p)
        {
            if (string.IsNullOrWhiteSpace(p.gameObjectPath) || !ComponentCrudCore.NormalizePath(p.gameObjectPath).Contains("/"))
            {
                throw new InvalidOperationException("gameObjectPath must be an exact full hierarchy path.");
            }
            if (string.IsNullOrWhiteSpace(p.sourceComponentGlobalObjectId))
            {
                throw new InvalidOperationException("sourceComponentGlobalObjectId is required.");
            }
            if (string.IsNullOrWhiteSpace(p.componentType) || !p.componentType.Contains("."))
            {
                throw new InvalidOperationException("componentType must be an exact fully-qualified type name.");
            }
            if (!p.sourceComponentIndex.HasValue || p.sourceComponentIndex.Value < 0)
            {
                throw new InvalidOperationException("sourceComponentIndex must be a non-negative exact index.");
            }
        }

        private static RemovedSnapshot CaptureRemoved(
            GameObject target,
            Type componentType,
            string sourceGlobalObjectId,
            int sourceComponentIndex)
        {
            var snapshot = CaptureRemovedOptional(target, componentType, sourceGlobalObjectId, sourceComponentIndex);
            if (snapshot.Match == null)
            {
                throw new InvalidOperationException("No removed-component override matched the exact target, source identity, type, and source index.");
            }
            return snapshot;
        }

        private static RemovedSnapshot CaptureRemovedOptional(
            GameObject target,
            Type componentType,
            string sourceGlobalObjectId,
            int sourceComponentIndex)
        {
            var instanceRoot = PrefabUtility.GetOutermostPrefabInstanceRoot(target)
                ?? PrefabUtility.GetNearestPrefabInstanceRoot(target);
            if (instanceRoot == null)
            {
                return new RemovedSnapshot();
            }

            var removed = PrefabUtility.GetRemovedComponents(instanceRoot)
                .Where(item => item != null && item.containingInstanceGameObject == target && item.assetComponent != null)
                .ToArray();
            var typed = removed.Where(item => item.assetComponent.GetType() == componentType).ToArray();
            var matches = typed
                .Select(item => DescribeMatch(item, componentType))
                .Where(item => item != null
                    && string.Equals(item.SourceGlobalObjectId, sourceGlobalObjectId, StringComparison.Ordinal)
                    && item.SourceComponentIndex == sourceComponentIndex)
                .ToArray();
            if (matches.Length > 1)
            {
                throw new InvalidOperationException("Multiple removed-component overrides matched the exact source identity; refusing an ambiguous revert.");
            }
            return new RemovedSnapshot
            {
                TotalCount = removed.Length,
                TypeCount = typed.Length,
                Match = matches.SingleOrDefault()
            };
        }

        private static RemovedMatch DescribeMatch(RemovedComponent removed, Type componentType)
        {
            var source = removed.assetComponent;
            if (source == null || source.gameObject == null)
            {
                return null;
            }
            var sameType = source.gameObject.GetComponents(componentType);
            var index = Array.IndexOf(sameType, source);
            if (index < 0)
            {
                throw new InvalidOperationException("The removed source component was not present in its Prefab-asset component list.");
            }
            return new RemovedMatch
            {
                AssetComponent = source,
                SourceGlobalObjectId = ObjectId(source),
                SourceComponentIndex = index
            };
        }

        private static string SourceObjectId(Component instanceComponent)
        {
            if (instanceComponent == null)
            {
                return string.Empty;
            }
            var source = PrefabUtility.GetCorrespondingObjectFromSource(instanceComponent);
            if (source == null)
            {
                source = PrefabUtility.GetCorrespondingObjectFromOriginalSource(instanceComponent);
            }
            return ObjectId(source);
        }

        private static string ObjectId(UnityEngine.Object value)
        {
            return value == null ? string.Empty : GlobalObjectId.GetGlobalObjectIdSlow(value).ToString();
        }

        private static object SnapshotPayload(
            string targetGlobalObjectId,
            int componentCount,
            int removedComponentCount,
            int removedTypeCount,
            string sourceComponentGlobalObjectId,
            int sourceComponentIndex)
        {
            return new
            {
                targetGlobalObjectId,
                componentCount,
                removedComponentCount,
                removedTypeCount,
                sourceComponentGlobalObjectId,
                sourceComponentIndex
            };
        }
    }
}
