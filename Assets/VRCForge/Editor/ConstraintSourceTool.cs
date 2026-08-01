using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using VRCForge.Core.MCP;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace VRCForge.Editor
{
    [VRCForgeTool(
        name: "vrc_set_constraint_sources",
        Description = "Preview or replace the ordered sources of one exact saved-scene constraint through a fixed compatibility schema."
    )]
    public static class ConstraintSourceTool
    {
        public const string ToolName = "vrc_set_constraint_sources";

        private const string ResultSchema = "vrcforge.constraint_source_write.v1";
        private const string SourcesDigestSchema = "vrcforge.constraint_sources_digest.v1";
        private const string ComponentDigestSchema = "vrcforge.constraint_component.v1";
        private const string StructuredSchemaId = "vrcforge.constraint_sources.fixed.v1";
        private const int MaximumSources = 64;

        private static readonly Dictionary<string, string> ComponentTypes =
            new Dictionary<string, string>(StringComparer.Ordinal)
            {
                { "position", "VRC.SDK3.Dynamics.Constraint.Components.VRCPositionConstraint" },
                { "rotation", "VRC.SDK3.Dynamics.Constraint.Components.VRCRotationConstraint" },
                { "scale", "VRC.SDK3.Dynamics.Constraint.Components.VRCScaleConstraint" },
                { "parent", "VRC.SDK3.Dynamics.Constraint.Components.VRCParentConstraint" },
                { "aim", "VRC.SDK3.Dynamics.Constraint.Components.VRCAimConstraint" },
                { "look_at", "VRC.SDK3.Dynamics.Constraint.Components.VRCLookAtConstraint" }
            };

        private static readonly HashSet<string> AllowedRequestKeys = new HashSet<string>(
            new[]
            {
                "scenePath",
                "gameObjectPath",
                "constraintKind",
                "componentIndex",
                "sources",
                "preview",
                "saveScene",
                "expectedProjectPath",
                "expectedScenePath",
                "expectedSceneGuid",
                "expectedSceneHandle",
                "expectedSceneFileDigest",
                "expectedSceneFileIdentity",
                "expectedSceneMetaDigest",
                "expectedSceneMetaIdentity",
                "expectedGameObjectPath",
                "expectedConstraintKind",
                "expectedComponentType",
                "expectedComponentIndex",
                "expectedComponentId",
                "expectedComponentGlobalId",
                "expectedBeforeSourcesDigest",
                "expectedTargetSourcesDigest"
            },
            StringComparer.Ordinal
        );

        static ConstraintSourceTool()
        {
            TypedStructuredListCore.Register(BuildCompatibilitySchema());
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var parameters = @params ?? new JObject();
                RejectUnknownRequestKeys(parameters);
                var scenePath = ReadRequiredString(parameters, "scenePath");
                var gameObjectPath = ReadRequiredString(parameters, "gameObjectPath");
                var constraintKind = ReadConstraintKind(parameters);
                var componentIndex = ReadBoundedInt(parameters, "componentIndex", 0, 31);
                var sources = ReadRequiredArray(parameters, "sources");
                var preview = ReadOptionalBool(parameters, "preview", false);
                var saveScene = ReadOptionalBool(parameters, "saveScene", false);
                if (preview && saveScene)
                {
                    throw new ConstraintSourceToolException("Preview cannot save a scene.");
                }
                if (!preview && !saveScene)
                {
                    throw new ConstraintSourceToolException("saveScene must be true for apply.");
                }

                var snapshot = CaptureSnapshot(
                    scenePath,
                    gameObjectPath,
                    constraintKind,
                    componentIndex,
                    sources
                );
                if (preview)
                {
                    VerifyPreviewStayedReadOnly(snapshot);
                    return Success(snapshot, snapshot, true, false, false);
                }

                ValidateExpected(parameters, snapshot);
                if (!snapshot.WouldChange)
                {
                    var unchanged = CaptureSnapshot(
                        scenePath,
                        gameObjectPath,
                        constraintKind,
                        componentIndex,
                        sources
                    );
                    VerifySamePreState(snapshot, unchanged);
                    return Success(snapshot, unchanged, false, false, false);
                }
                return Apply(snapshot, sources);
            }
            catch (ConstraintSourceToolException exception)
            {
                return new ErrorResponse(exception.Message);
            }
            catch (SceneObjectCopyException exception)
            {
                return new ErrorResponse(exception.Message);
            }
            catch (Exception)
            {
                return new ErrorResponse("Constraint source operation failed closed.");
            }
        }

        private static object Apply(ConstraintSourceSnapshot snapshot, JArray requestedSources)
        {
            var mutationStarted = false;
            try
            {
                var immediate = CaptureSnapshot(
                    snapshot.Scene.Path,
                    snapshot.GameObjectPath,
                    snapshot.ConstraintKind,
                    snapshot.ComponentIndex,
                    requestedSources
                );
                VerifySamePreState(snapshot, immediate);

                Undo.RecordObject(snapshot.Component, "Set VRCForge constraint sources");
                mutationStarted = true;
                TypedStructuredListCore.Apply(snapshot.Component, snapshot.Plan);
                EditorUtility.SetDirty(snapshot.Component);
                EditorSceneManager.MarkSceneDirty(snapshot.Scene.Scene);
                if (!EditorSceneManager.SaveScene(snapshot.Scene.Scene))
                {
                    throw new ConstraintSourceToolException("The constraint scene could not be saved.");
                }

                var readback = CaptureSnapshot(
                    snapshot.Scene.Path,
                    snapshot.GameObjectPath,
                    snapshot.ConstraintKind,
                    snapshot.ComponentIndex,
                    requestedSources
                );
                if (readback.Scene.Guid != snapshot.Scene.Guid
                    || readback.Scene.Handle != snapshot.Scene.Handle
                    || readback.Scene.MetaDigest != snapshot.Scene.MetaDigest
                    || readback.Scene.MetaIdentity != snapshot.Scene.MetaIdentity
                    || readback.SceneFileLinkCount != snapshot.SceneFileLinkCount
                    || readback.SceneMetaLinkCount != snapshot.SceneMetaLinkCount
                    || readback.ComponentId != snapshot.ComponentId
                    || readback.ComponentGlobalId != snapshot.ComponentGlobalId
                    || readback.BeforeSourcesDigest != snapshot.TargetSourcesDigest
                    || readback.TargetSourcesDigest != snapshot.TargetSourcesDigest
                    || readback.WouldChange
                    || readback.Scene.FileDigest == snapshot.Scene.FileDigest
                    || readback.Scene.Scene.isDirty)
                {
                    throw new ConstraintSourceToolException("Constraint source persisted readback did not match the approved order.");
                }
                return Success(snapshot, readback, false, true, true);
            }
            catch (Exception) when (mutationStarted)
            {
                var restored = TryRestoreBeforeSources(snapshot);
                return BuildMutationFailure(restored);
            }
        }

        private static object BuildMutationFailure(bool restored)
        {
            var message = restored
                ? "Constraint source operation failed after restoring the verified pre-state."
                : "Constraint source operation failed; checkpoint restore is required.";
            return new ErrorResponse(
                message,
                new
                {
                    schema = ResultSchema,
                    mutationStarted = true,
                    restored,
                    cleanupVerified = restored,
                    cleanupRequired = !restored,
                    checkpointRestoreRequired = !restored,
                    operationState = restored ? "restored" : "checkpoint_restore_required"
                }
            );
        }

        private static bool TryRestoreBeforeSources(ConstraintSourceSnapshot snapshot)
        {
            try
            {
                if (snapshot == null
                    || snapshot.Component == null
                    || snapshot.Scene == null
                    || !snapshot.Scene.Scene.IsValid()
                    || !snapshot.Scene.Scene.isLoaded)
                {
                    return false;
                }
                TypedStructuredListCore.RestoreOriginal(snapshot.Component, snapshot.Plan);
                EditorUtility.SetDirty(snapshot.Component);
                EditorSceneManager.MarkSceneDirty(snapshot.Scene.Scene);
                if (!EditorSceneManager.SaveScene(snapshot.Scene.Scene))
                {
                    return false;
                }
                var restored = CaptureSnapshot(
                    snapshot.Scene.Path,
                    snapshot.GameObjectPath,
                    snapshot.ConstraintKind,
                    snapshot.ComponentIndex,
                    ToRequestArray(snapshot.BeforeSources)
                );
                return restored.Scene.Guid == snapshot.Scene.Guid
                    && restored.Scene.Handle == snapshot.Scene.Handle
                    && restored.Scene.FileDigest == snapshot.Scene.FileDigest
                    && restored.Scene.MetaDigest == snapshot.Scene.MetaDigest
                    && restored.Scene.MetaIdentity == snapshot.Scene.MetaIdentity
                    && restored.SceneFileLinkCount == snapshot.SceneFileLinkCount
                    && restored.SceneMetaLinkCount == snapshot.SceneMetaLinkCount
                    && restored.ComponentId == snapshot.ComponentId
                    && restored.ComponentGlobalId == snapshot.ComponentGlobalId
                    && restored.BeforeSourcesDigest == snapshot.BeforeSourcesDigest
                    && !restored.WouldChange
                    && !restored.Scene.Scene.isDirty;
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static ConstraintSourceSnapshot CaptureSnapshot(
            string scenePath,
            string gameObjectPath,
            string constraintKind,
            int componentIndex,
            JArray requestedSources)
        {
            var scene = SceneObjectCopyCore.ResolveSavedScene(scenePath, "constraint scene");
            var stableAsset = SceneObjectCopyCore.ReadStableAssetEvidence(
                scene.Path,
                "constraint scene"
            );
            if (stableAsset.Guid != scene.Guid
                || stableAsset.File.Digest != scene.FileDigest
                || stableAsset.File.Identity != scene.FileIdentity
                || stableAsset.Meta.Digest != scene.MetaDigest
                || stableAsset.Meta.Identity != scene.MetaIdentity
                || stableAsset.File.LinkCount != 1
                || stableAsset.Meta.LinkCount != 1)
            {
                throw new ConstraintSourceToolException("Constraint scene identity changed during inspection.");
            }

            var normalizedHostPath = NormalizeHierarchyPath(gameObjectPath, "gameObjectPath");
            var host = SceneObjectCopyCore.ResolveUniqueGameObject(
                scene.Scene,
                normalizedHostPath,
                "constraint host"
            );
            string componentTypeName;
            if (!ComponentTypes.TryGetValue(constraintKind, out componentTypeName))
            {
                throw new ConstraintSourceToolException("constraintKind is unsupported.");
            }
            var componentType = ResolveExactType(componentTypeName);
            var components = host.GetComponents(componentType);
            if (componentIndex < 0 || componentIndex >= components.Length || components[componentIndex] == null)
            {
                throw new ConstraintSourceToolException("The exact constraint component selector did not resolve.");
            }
            var component = components[componentIndex];
            var componentGlobalId = StableGlobalId(component, "constraint component");
            var componentId = ComputeComponentId(
                scene.Guid,
                componentGlobalId,
                normalizedHostPath,
                componentTypeName,
                componentIndex
            );
            var plan = TypedStructuredListCore.BuildPlan(
                component,
                StructuredSchemaId,
                requestedSources,
                (rawPath, requiredType) =>
                {
                    var sourcePath = NormalizeHierarchyPath(rawPath, "sourcePath");
                    var sourceObject = SceneObjectCopyCore.ResolveUniqueGameObject(
                        scene.Scene,
                        sourcePath,
                        "constraint source"
                    );
                    if (requiredType != typeof(Transform))
                    {
                        throw new ConstraintSourceToolException("Constraint source compatibility type changed.");
                    }
                    return sourceObject.transform;
                }
            );
            var beforeSources = BuildEvidenceSources(scene, plan.Before);
            var targetSources = BuildEvidenceSources(scene, plan.Target);
            var beforeDigest = ComputeSourcesDigest(beforeSources);
            var targetDigest = ComputeSourcesDigest(targetSources);

            var afterInspection = SceneObjectCopyCore.ReadStableAssetEvidence(
                scene.Path,
                "constraint scene readback"
            );
            if (afterInspection.Guid != stableAsset.Guid
                || afterInspection.File.Digest != stableAsset.File.Digest
                || afterInspection.File.Identity != stableAsset.File.Identity
                || afterInspection.Meta.Digest != stableAsset.Meta.Digest
                || afterInspection.Meta.Identity != stableAsset.Meta.Identity
                || afterInspection.File.LinkCount != 1
                || afterInspection.Meta.LinkCount != 1
                || scene.Scene.isDirty)
            {
                throw new ConstraintSourceToolException("Constraint source inspection changed the saved scene.");
            }

            return new ConstraintSourceSnapshot
            {
                Scene = scene,
                SceneFileLinkCount = stableAsset.File.LinkCount,
                SceneMetaLinkCount = stableAsset.Meta.LinkCount,
                GameObjectPath = normalizedHostPath,
                ConstraintKind = constraintKind,
                ComponentType = componentTypeName,
                ComponentIndex = componentIndex,
                Component = component,
                ComponentId = componentId,
                ComponentGlobalId = componentGlobalId,
                Plan = plan,
                BeforeSources = beforeSources,
                TargetSources = targetSources,
                BeforeSourcesDigest = beforeDigest,
                TargetSourcesDigest = targetDigest,
                WouldChange = !TypedStructuredListCore.ElementsEqual(
                    plan.Schema,
                    plan.Before,
                    plan.Target
                )
            };
        }

        private static void VerifyPreviewStayedReadOnly(ConstraintSourceSnapshot snapshot)
        {
            var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(
                snapshot.Scene.Path,
                "constraint preview readback"
            );
            if (evidence.Guid != snapshot.Scene.Guid
                || evidence.File.Digest != snapshot.Scene.FileDigest
                || evidence.File.Identity != snapshot.Scene.FileIdentity
                || evidence.Meta.Digest != snapshot.Scene.MetaDigest
                || evidence.Meta.Identity != snapshot.Scene.MetaIdentity
                || evidence.File.LinkCount != 1
                || evidence.Meta.LinkCount != 1
                || snapshot.Scene.Scene.isDirty)
            {
                throw new ConstraintSourceToolException("Constraint source preview was not read-only.");
            }
        }

        private static void VerifySamePreState(
            ConstraintSourceSnapshot expected,
            ConstraintSourceSnapshot actual)
        {
            if (actual.Scene.Path != expected.Scene.Path
                || actual.Scene.Guid != expected.Scene.Guid
                || actual.Scene.Handle != expected.Scene.Handle
                || actual.Scene.FileDigest != expected.Scene.FileDigest
                || actual.Scene.FileIdentity != expected.Scene.FileIdentity
                || actual.Scene.MetaDigest != expected.Scene.MetaDigest
                || actual.Scene.MetaIdentity != expected.Scene.MetaIdentity
                || actual.GameObjectPath != expected.GameObjectPath
                || actual.ConstraintKind != expected.ConstraintKind
                || actual.ComponentType != expected.ComponentType
                || actual.ComponentIndex != expected.ComponentIndex
                || actual.ComponentId != expected.ComponentId
                || actual.ComponentGlobalId != expected.ComponentGlobalId
                || actual.BeforeSourcesDigest != expected.BeforeSourcesDigest
                || actual.TargetSourcesDigest != expected.TargetSourcesDigest
                || actual.WouldChange != expected.WouldChange)
            {
                throw new ConstraintSourceToolException("Constraint source state changed after the verified preview.");
            }
        }

        private static void ValidateExpected(JObject parameters, ConstraintSourceSnapshot snapshot)
        {
            if (!MatchesCurrentProject(ReadRequiredString(parameters, "expectedProjectPath"))
                || ReadRequiredString(parameters, "expectedScenePath") != snapshot.Scene.Path
                || ReadRequiredString(parameters, "expectedSceneGuid") != snapshot.Scene.Guid
                || ReadNonZeroInt(parameters, "expectedSceneHandle") != snapshot.Scene.Handle
                || ReadRequiredString(parameters, "expectedSceneFileDigest") != snapshot.Scene.FileDigest
                || ReadRequiredString(parameters, "expectedSceneFileIdentity") != snapshot.Scene.FileIdentity
                || ReadRequiredString(parameters, "expectedSceneMetaDigest") != snapshot.Scene.MetaDigest
                || ReadRequiredString(parameters, "expectedSceneMetaIdentity") != snapshot.Scene.MetaIdentity
                || ReadRequiredString(parameters, "expectedGameObjectPath") != snapshot.GameObjectPath
                || ReadRequiredString(parameters, "expectedConstraintKind") != snapshot.ConstraintKind
                || ReadRequiredString(parameters, "expectedComponentType") != snapshot.ComponentType
                || ReadBoundedInt(parameters, "expectedComponentIndex", 0, 31) != snapshot.ComponentIndex
                || ReadRequiredString(parameters, "expectedComponentId") != snapshot.ComponentId
                || ReadRequiredString(parameters, "expectedComponentGlobalId") != snapshot.ComponentGlobalId
                || ReadRequiredString(parameters, "expectedBeforeSourcesDigest") != snapshot.BeforeSourcesDigest
                || ReadRequiredString(parameters, "expectedTargetSourcesDigest") != snapshot.TargetSourcesDigest)
            {
                throw new ConstraintSourceToolException("Verified constraint source preconditions no longer match.");
            }
        }

        private static object Success(
            ConstraintSourceSnapshot before,
            ConstraintSourceSnapshot after,
            bool preview,
            bool changed,
            bool saved)
        {
            return new SuccessResponse(
                preview ? "Constraint source preview completed." : "Constraint source operation verified.",
                new
                {
                    schema = ResultSchema,
                    ok = true,
                    preview,
                    verified = true,
                    changed,
                    saved,
                    wouldChange = before.WouldChange,
                    projectPath = CurrentProjectPath(),
                    scenePath = before.Scene.Path,
                    sceneGuid = before.Scene.Guid,
                    sceneHandle = before.Scene.Handle,
                    sceneFileDigestBefore = before.Scene.FileDigest,
                    sceneFileDigestAfter = after.Scene.FileDigest,
                    sceneFileIdentity = before.Scene.FileIdentity,
                    sceneFileIdentityAfter = after.Scene.FileIdentity,
                    sceneFileLinkCount = before.SceneFileLinkCount,
                    sceneMetaDigestBefore = before.Scene.MetaDigest,
                    sceneMetaDigestAfter = after.Scene.MetaDigest,
                    sceneMetaIdentity = before.Scene.MetaIdentity,
                    sceneMetaIdentityAfter = after.Scene.MetaIdentity,
                    sceneMetaLinkCount = before.SceneMetaLinkCount,
                    sceneDirtyBefore = false,
                    sceneDirtyAfter = after.Scene.Scene.isDirty,
                    gameObjectPath = before.GameObjectPath,
                    constraintKind = before.ConstraintKind,
                    componentType = before.ComponentType,
                    componentIndex = before.ComponentIndex,
                    componentId = before.ComponentId,
                    componentGlobalId = before.ComponentGlobalId,
                    beforeSources = before.BeforeSources.Select(item => item.ToPayload()).ToArray(),
                    targetSources = before.TargetSources.Select(item => item.ToPayload()).ToArray(),
                    beforeSourcesDigest = before.BeforeSourcesDigest,
                    targetSourcesDigest = before.TargetSourcesDigest,
                    sourcesDigestSchema = SourcesDigestSchema,
                    cleanupRequired = false,
                    checkpointRestoreRequired = false
                }
            );
        }

        private static List<ConstraintSourceEvidence> BuildEvidenceSources(
            SavedSceneSnapshot scene,
            IReadOnlyList<StructuredListElementValue> logicalSources)
        {
            var result = new List<ConstraintSourceEvidence>();
            var seenPaths = new HashSet<string>(StringComparer.Ordinal);
            var seenIds = new HashSet<string>(StringComparer.Ordinal);
            foreach (var logical in logicalSources)
            {
                object sourceValue;
                object weightValue;
                if (!logical.Values.TryGetValue("sourcePath", out sourceValue)
                    || !logical.Values.TryGetValue("weight", out weightValue))
                {
                    throw new ConstraintSourceToolException("Constraint source schema projection is incomplete.");
                }
                var transform = sourceValue as Transform;
                if (transform == null || transform.gameObject.scene.handle != scene.Handle)
                {
                    throw new ConstraintSourceToolException("Constraint source reference is null or outside the saved scene.");
                }
                var path = SceneObjectCopyCore.GetHierarchyPath(transform);
                var unique = SceneObjectCopyCore.ResolveUniqueGameObject(
                    scene.Scene,
                    path,
                    "constraint source readback"
                );
                if (!ReferenceEquals(unique.transform, transform))
                {
                    throw new ConstraintSourceToolException("Constraint source hierarchy identity is ambiguous.");
                }
                var objectId = StableGlobalId(transform, "constraint source");
                var weight = Convert.ToSingle(weightValue, CultureInfo.InvariantCulture);
                if (float.IsNaN(weight) || float.IsInfinity(weight) || weight < 0f || weight > 1f)
                {
                    throw new ConstraintSourceToolException("Constraint source weight is out of range.");
                }
                if (!seenPaths.Add(path) || !seenIds.Add(objectId))
                {
                    throw new ConstraintSourceToolException("Constraint source identities must be unique.");
                }
                result.Add(new ConstraintSourceEvidence
                {
                    SourcePath = path,
                    SourceObjectId = objectId,
                    Weight = weight,
                    WeightBits = FloatBits(weight)
                });
            }
            return result;
        }

        private static string ComputeSourcesDigest(IReadOnlyList<ConstraintSourceEvidence> sources)
        {
            var value = new StringBuilder();
            AppendDigestField(value, SourcesDigestSchema);
            AppendDigestField(value, sources.Count.ToString(CultureInfo.InvariantCulture));
            foreach (var source in sources)
            {
                AppendDigestField(value, source.SourcePath);
                AppendDigestField(value, source.SourceObjectId);
                AppendDigestField(value, source.WeightBits);
            }
            return Sha256(value.ToString());
        }

        private static string ComputeComponentId(
            string sceneGuid,
            string componentGlobalId,
            string gameObjectPath,
            string componentType,
            int componentIndex)
        {
            var value = new StringBuilder();
            AppendDigestField(value, ComponentDigestSchema);
            AppendDigestField(value, sceneGuid);
            AppendDigestField(value, componentGlobalId);
            AppendDigestField(value, gameObjectPath);
            AppendDigestField(value, componentType);
            AppendDigestField(value, componentIndex.ToString(CultureInfo.InvariantCulture));
            return Sha256(value.ToString());
        }

        private static StructuredListSchema BuildCompatibilitySchema()
        {
            return new StructuredListSchema
            {
                Id = StructuredSchemaId,
                ComponentTypeNames = new HashSet<string>(ComponentTypes.Values, StringComparer.Ordinal),
                ComponentAssemblyName = "VRC.SDK3.Dynamics.Constraint",
                ComponentAssemblyVersion = "1.0.0.0",
                ComponentAssemblyPublicKeyToken = string.Empty,
                ComponentAssemblySha256 = "4c80361ee9938695ceb6e773875cba85ab3bbdce850262328a541e903ff39dab",
                ListMemberName = "Sources",
                ListTypeName = "VRC.Dynamics.VRCConstraintSourceKeyableList",
                ElementTypeName = "VRC.Dynamics.VRCConstraintSource",
                AssemblyName = "VRC.Dynamics",
                AssemblyVersion = "1.0.0.0",
                AssemblyPublicKeyToken = string.Empty,
                AssemblySha256 = "34177c7d681783c9c0b25727b763d920d34fc3f9619b5edc48a765ff19de3243",
                MaximumItems = MaximumSources,
                RequireUniqueObjectReferences = true,
                Fields = new List<StructuredFieldSchema>
                {
                    new StructuredFieldSchema
                    {
                        RequestKey = "sourcePath",
                        MemberName = "SourceTransform",
                        Kind = StructuredValueKind.ObjectReference,
                        ObjectTypeName = "UnityEngine.Transform",
                        AllowNull = false
                    },
                    new StructuredFieldSchema
                    {
                        RequestKey = "weight",
                        MemberName = "Weight",
                        Kind = StructuredValueKind.BoundedSingle,
                        Minimum = 0f,
                        Maximum = 1f
                    }
                },
                ManagedElementFactory = CreateDefaultElement,
                CollectionFactory = CreateKeyableList,
                FixedSlotCount = 16,
                TotalLengthRelativePath = "totalLength",
                FixedSlotPrefix = "source",
                OverflowListRelativePath = "overflowList"
            };
        }

        private static object CreateDefaultElement(Type elementType)
        {
            var method = elementType.GetMethod(
                "CreateDefault",
                BindingFlags.Public | BindingFlags.Static,
                null,
                Type.EmptyTypes,
                null
            );
            if (method == null || method.ReturnType != elementType)
            {
                throw new ConstraintSourceToolException("Constraint source element factory is unsupported.");
            }
            return method.Invoke(null, null);
        }

        private static object CreateKeyableList(
            Type listType,
            Type elementType,
            IReadOnlyList<object> elements)
        {
            var genericListType = typeof(List<>).MakeGenericType(elementType);
            var genericList = Activator.CreateInstance(genericListType) as IList;
            if (genericList == null)
            {
                throw new ConstraintSourceToolException("Constraint source list staging is unavailable.");
            }
            foreach (var element in elements)
            {
                genericList.Add(element);
            }
            var genericIListType = typeof(IList<>).MakeGenericType(elementType);
            var constructors = listType.GetConstructors(BindingFlags.Public | BindingFlags.Instance)
                .Where(candidate =>
                {
                    var parameters = candidate.GetParameters();
                    return parameters.Length == 1 && parameters[0].ParameterType == genericIListType;
                })
                .ToList();
            if (constructors.Count != 1)
            {
                throw new ConstraintSourceToolException("Constraint source list constructor is unsupported.");
            }
            return constructors[0].Invoke(new object[] { genericList });
        }

        private static JArray ToRequestArray(IReadOnlyList<ConstraintSourceEvidence> sources)
        {
            return new JArray(sources.Select(source => new JObject
            {
                ["sourcePath"] = source.SourcePath,
                ["weight"] = source.Weight
            }));
        }

        private static void RejectUnknownRequestKeys(JObject parameters)
        {
            if (parameters.Properties().Any(property => !AllowedRequestKeys.Contains(property.Name)))
            {
                throw new ConstraintSourceToolException("Constraint source request contains unsupported fields.");
            }
        }

        private static string ReadConstraintKind(JObject parameters)
        {
            var value = ReadRequiredString(parameters, "constraintKind").ToLowerInvariant();
            if (!ComponentTypes.ContainsKey(value))
            {
                throw new ConstraintSourceToolException("constraintKind is unsupported.");
            }
            return value;
        }

        private static JArray ReadRequiredArray(JObject parameters, string key)
        {
            var token = parameters[key];
            var value = token as JArray;
            if (value == null || value.Count > MaximumSources)
            {
                throw new ConstraintSourceToolException(key + " must be a bounded array.");
            }
            return value;
        }

        private static string ReadRequiredString(JObject parameters, string key)
        {
            var token = parameters[key];
            if (token == null || token.Type != JTokenType.String)
            {
                throw new ConstraintSourceToolException(key + " is required.");
            }
            var value = token.ToString().Trim();
            if (string.IsNullOrWhiteSpace(value)
                || value.Length > 32768
                || value.Any(character => character < 32))
            {
                throw new ConstraintSourceToolException(key + " is invalid.");
            }
            return value;
        }

        private static int ReadBoundedInt(JObject parameters, string key, int minimum, int maximum)
        {
            var token = parameters[key];
            if (token == null || token.Type != JTokenType.Integer)
            {
                throw new ConstraintSourceToolException(key + " must be an integer.");
            }
            long raw;
            try
            {
                raw = token.Value<long>();
            }
            catch (Exception)
            {
                throw new ConstraintSourceToolException(key + " must be an integer.");
            }
            if (raw < minimum || raw > maximum)
            {
                throw new ConstraintSourceToolException(key + " is out of range.");
            }
            return (int)raw;
        }

        private static int ReadNonZeroInt(JObject parameters, string key)
        {
            var token = parameters[key];
            if (token == null || token.Type != JTokenType.Integer)
            {
                throw new ConstraintSourceToolException(key + " must be an integer.");
            }
            long raw;
            try
            {
                raw = token.Value<long>();
            }
            catch (Exception)
            {
                throw new ConstraintSourceToolException(key + " must be an integer.");
            }
            if (raw == 0 || raw < int.MinValue || raw > int.MaxValue)
            {
                throw new ConstraintSourceToolException(key + " is invalid.");
            }
            return (int)raw;
        }

        private static bool ReadOptionalBool(JObject parameters, string key, bool fallback)
        {
            var token = parameters[key];
            if (token == null)
            {
                return fallback;
            }
            if (token.Type != JTokenType.Boolean)
            {
                throw new ConstraintSourceToolException(key + " must be a boolean.");
            }
            return token.Value<bool>();
        }

        private static string NormalizeHierarchyPath(string raw, string label)
        {
            var value = (raw ?? string.Empty).Trim();
            var segments = value.Split('/');
            if (string.IsNullOrWhiteSpace(value)
                || value.Length > 2048
                || value.StartsWith("/", StringComparison.Ordinal)
                || value.EndsWith("/", StringComparison.Ordinal)
                || value.Contains("\\")
                || segments.Any(segment => string.IsNullOrWhiteSpace(segment)
                    || segment == "."
                    || segment == ".."))
            {
                throw new ConstraintSourceToolException(label + " is invalid.");
            }
            return string.Join("/", segments);
        }

        private static Type ResolveExactType(string fullName)
        {
            var matches = AppDomain.CurrentDomain.GetAssemblies()
                .Select(assembly => assembly.GetType(fullName, false, false))
                .Where(type => type != null)
                .Distinct()
                .ToList();
            if (matches.Count != 1)
            {
                throw new ConstraintSourceToolException("Constraint compatibility type is missing or ambiguous.");
            }
            return matches[0];
        }

        private static string StableGlobalId(UnityEngine.Object target, string label)
        {
            var id = GlobalObjectId.GetGlobalObjectIdSlow(target);
            if (id.identifierType == 0)
            {
                throw new ConstraintSourceToolException("A stable " + label + " identity is unavailable.");
            }
            return id.ToString();
        }

        private static string FloatBits(float value)
        {
            var bytes = BitConverter.GetBytes(value);
            if (BitConverter.IsLittleEndian)
            {
                Array.Reverse(bytes);
            }
            return BitConverter.ToString(bytes).Replace("-", string.Empty).ToLowerInvariant();
        }

        private static void AppendDigestField(StringBuilder target, string value)
        {
            var text = value ?? string.Empty;
            target.Append(text.Length.ToString(CultureInfo.InvariantCulture));
            target.Append(':');
            target.Append(text);
        }

        private static string Sha256(string value)
        {
            using (var sha256 = SHA256.Create())
            {
                return BitConverter.ToString(sha256.ComputeHash(Encoding.UTF8.GetBytes(value)))
                    .Replace("-", string.Empty)
                    .ToLowerInvariant();
            }
        }

        private static bool MatchesCurrentProject(string expectedProjectPath)
        {
            if (string.IsNullOrWhiteSpace(expectedProjectPath) || !Path.IsPathRooted(expectedProjectPath))
            {
                return false;
            }
            string expected;
            try
            {
                expected = Path.GetFullPath(expectedProjectPath)
                    .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            }
            catch (Exception)
            {
                return false;
            }
            return string.Equals(expected, CurrentProjectPath(), PathComparison());
        }

        private static string CurrentProjectPath()
        {
            return Path.GetFullPath(Path.Combine(Application.dataPath, ".."))
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        }

        private static StringComparison PathComparison()
        {
            return Application.platform == RuntimePlatform.WindowsEditor
                ? StringComparison.OrdinalIgnoreCase
                : StringComparison.Ordinal;
        }

        private sealed class ConstraintSourceToolException : InvalidOperationException
        {
            internal ConstraintSourceToolException(string message)
                : base(message)
            {
            }
        }

        private sealed class ConstraintSourceSnapshot
        {
            internal SavedSceneSnapshot Scene;
            internal uint SceneFileLinkCount;
            internal uint SceneMetaLinkCount;
            internal string GameObjectPath = string.Empty;
            internal string ConstraintKind = string.Empty;
            internal string ComponentType = string.Empty;
            internal int ComponentIndex;
            internal Component Component;
            internal string ComponentId = string.Empty;
            internal string ComponentGlobalId = string.Empty;
            internal StructuredListPlan Plan;
            internal List<ConstraintSourceEvidence> BeforeSources = new List<ConstraintSourceEvidence>();
            internal List<ConstraintSourceEvidence> TargetSources = new List<ConstraintSourceEvidence>();
            internal string BeforeSourcesDigest = string.Empty;
            internal string TargetSourcesDigest = string.Empty;
            internal bool WouldChange;
        }

        private sealed class ConstraintSourceEvidence
        {
            internal string SourcePath = string.Empty;
            internal string SourceObjectId = string.Empty;
            internal float Weight;
            internal string WeightBits = string.Empty;

            internal object ToPayload()
            {
                return new
                {
                    sourcePath = SourcePath,
                    sourceObjectId = SourceObjectId,
                    weight = Weight,
                    weightBits = WeightBits
                };
            }
        }
    }
}
