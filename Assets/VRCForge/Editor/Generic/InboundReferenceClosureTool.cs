using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_scan_inbound_reference_closure",
        Summary = "Read the exact serialized, animation-path, framework-path, and parameter edges that still point into selected scene roots or components. Read-only and fail-closed.",
        Access = VRCForgeCommandAccess.ReadOnly
    )]
    public static class InboundReferenceClosureTool
    {
        public const string ToolName = "vrc_scan_inbound_reference_closure";

        public sealed class ComponentSelector
        {
            public string objectPath { get; set; } = "";
            public string componentType { get; set; } = "";
            public int? componentIndex { get; set; } = 0;
        }

        public sealed class Parameters
        {
            [VRCForgeInput("Avatar root hierarchy path used to resolve animation and framework-relative paths.", IsRequired = true)]
            public string avatarPath { get; set; } = "";

            [VRCForgeInput("One or more scene hierarchy roots whose entire subtrees are deletion candidates.", IsRequired = false)]
            public string[] targetPaths { get; set; } = new string[0];

            [VRCForgeInput("Optional exact component deletion candidates.", IsRequired = false)]
            public ComponentSelector[] targetComponentSelectors { get; set; } = new ComponentSelector[0];

            [VRCForgeInput("Scan AnimatorController, AnimationClip, menu, parameter, and framework assets referenced by the avatar (default true).", IsRequired = false)]
            public bool? includeProjectAssets { get; set; } = true;

            [VRCForgeInput("Resolve AnimationClip binding paths against their Animator or avatar root (default true).", IsRequired = false)]
            public bool? includeAnimationBindings { get; set; } = true;

            [VRCForgeInput("Trace PhysBone and Contact output parameter names into referenced assets (default true).", IsRequired = false)]
            public bool? includeIndirectParameterEdges { get; set; } = true;

            [VRCForgeInput("Maximum returned reference edges (1-5000, default 1000). Truncation makes proof indeterminate.", IsRequired = false)]
            public int? maxResults { get; set; } = 1000;
        }

        private sealed class TargetRoot
        {
            internal Transform Transform;
            internal string Path;
            internal string Identity;
        }

        private sealed class TargetComponent
        {
            internal Component Component;
            internal string ObjectPath;
            internal string Identity;
        }

        private sealed class ReferenceEdge
        {
            public string edgeKind { get; set; }
            public string sourceAssetPath { get; set; }
            public string sourceObjectPath { get; set; }
            public string sourceComponentType { get; set; }
            public string sourcePropertyPath { get; set; }
            public string targetObjectPath { get; set; }
            public string targetIdentity { get; set; }
            public string confidence { get; set; }
            public string reason { get; set; }
        }

        private sealed class UnresolvedEdge
        {
            public string source { get; set; }
            public string propertyPath { get; set; }
            public string reason { get; set; }
        }

        private sealed class ControllerRoot
        {
            internal RuntimeAnimatorController Controller;
            internal Transform Root;
            internal string SourceObjectPath;
            internal string SourceComponentType;
            internal string SourcePropertyPath;
        }

        private sealed class ScanState
        {
            internal GameObject Avatar;
            internal List<TargetRoot> TargetRoots;
            internal List<TargetComponent> TargetComponents;
            internal List<ReferenceEdge> References = new List<ReferenceEdge>();
            internal List<UnresolvedEdge> Unresolved = new List<UnresolvedEdge>();
            internal HashSet<string> ReferenceKeys = new HashSet<string>(StringComparer.Ordinal);
            internal HashSet<string> UnresolvedKeys = new HashSet<string>(StringComparer.Ordinal);
            internal HashSet<string> SemanticTokens = new HashSet<string>(StringComparer.Ordinal);
            internal Queue<string> AssetQueue = new Queue<string>();
            internal HashSet<string> QueuedAssetPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            internal List<ControllerRoot> Controllers = new List<ControllerRoot>();
            internal HashSet<string> ControllerKeys = new HashSet<string>(StringComparer.Ordinal);
            internal int MaxResults;
            internal bool Truncated;
            internal int ScannedComponentCount;
            internal int ScannedAssetObjectCount;
            internal int ScannedAnimationClipCount;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters();
            try
            {
                var maxResults = p.maxResults ?? 1000;
                if (maxResults < 1 || maxResults > 5000)
                {
                    throw new InvalidOperationException("maxResults must be between 1 and 5000.");
                }

                var avatar = ComponentCrudCore.ResolveGameObject(p.avatarPath);
                if (!avatar.scene.IsValid() || !avatar.scene.isLoaded || string.IsNullOrWhiteSpace(avatar.scene.path))
                {
                    throw new InvalidOperationException("avatarPath must resolve in one loaded, saved scene.");
                }

                var roots = ResolveTargetRoots(p.targetPaths, avatar.scene.handle);
                var components = ResolveTargetComponents(p.targetComponentSelectors, avatar.scene.handle);
                if (roots.Count == 0 && components.Count == 0)
                {
                    throw new InvalidOperationException("At least one targetPaths or targetComponentSelectors entry is required.");
                }

                var state = new ScanState
                {
                    Avatar = avatar,
                    TargetRoots = roots,
                    TargetComponents = components,
                    MaxResults = maxResults,
                };

                if (avatar.scene.isDirty)
                {
                    AddUnresolved(state, avatar.scene.path, "", "The scene has unsaved changes; save it before using this scan as deletion evidence.");
                }

                CollectSemanticTokens(state);
                ScanSceneComponents(state);

                var includeAssets = p.includeProjectAssets ?? true;
                var includeAnimations = p.includeAnimationBindings ?? true;
                var includeParameters = p.includeIndirectParameterEdges ?? true;
                if (includeAssets)
                {
                    ScanQueuedAssets(state, includeParameters);
                }
                else
                {
                    AddUnresolved(state, "project-assets", "", "Project asset scanning was disabled, so a complete negative reference proof is unavailable.");
                }

                if (includeAnimations)
                {
                    ScanAnimationBindings(state);
                }
                else
                {
                    AddUnresolved(state, "animation-bindings", "", "Animation binding scanning was disabled, so a complete negative reference proof is unavailable.");
                }

                if (!includeParameters && state.SemanticTokens.Count > 0)
                {
                    AddUnresolved(state, "indirect-parameters", "", "Indirect PhysBone/Contact parameter scanning was disabled.");
                }
                if (state.Truncated)
                {
                    AddUnresolved(state, "result-limit", "", "Reference results reached maxResults and were truncated.");
                }

                state.References = state.References
                    .OrderBy(item => item.edgeKind, StringComparer.Ordinal)
                    .ThenBy(item => item.sourceAssetPath, StringComparer.Ordinal)
                    .ThenBy(item => item.sourceObjectPath, StringComparer.Ordinal)
                    .ThenBy(item => item.sourceComponentType, StringComparer.Ordinal)
                    .ThenBy(item => item.sourcePropertyPath, StringComparer.Ordinal)
                    .ThenBy(item => item.targetObjectPath, StringComparer.Ordinal)
                    .ToList();
                state.Unresolved = state.Unresolved
                    .OrderBy(item => item.source, StringComparer.Ordinal)
                    .ThenBy(item => item.propertyPath, StringComparer.Ordinal)
                    .ThenBy(item => item.reason, StringComparer.Ordinal)
                    .ToList();

                var complete = state.Unresolved.Count == 0 && !state.Truncated;
                var deletionSafe = complete && state.References.Count == 0;
                var projectPath = Directory.GetParent(Application.dataPath)?.FullName ?? string.Empty;
                var targetIdentities = roots.Select(item => new
                    {
                        kind = "scene_root",
                        objectPath = item.Path,
                        componentType = "",
                        componentIndex = (int?)null,
                        identity = item.Identity,
                    })
                    .Concat(components.Select(item => new
                    {
                        kind = "component",
                        objectPath = item.ObjectPath,
                        componentType = item.Component.GetType().FullName,
                        componentIndex = (int?)Array.IndexOf(item.Component.gameObject.GetComponents(item.Component.GetType()), item.Component),
                        identity = item.Identity,
                    }))
                    .ToArray();
                var scopeDigest = ComputeScopeDigest(projectPath, avatar.scene.path, avatar, targetIdentities.Select(item => item.identity));

                var payload = new
                {
                    schema = "vrcforge.inbound_reference_closure.v1",
                    ok = true,
                    proofStatus = complete ? "complete" : "indeterminate",
                    deletionSafe,
                    scope = new
                    {
                        projectPath,
                        scenePath = avatar.scene.path,
                        avatarPath = ComponentCrudCore.GetHierarchyPath(avatar.transform),
                        targetIdentities,
                        scopeDigest,
                    },
                    references = state.References,
                    unresolved = state.Unresolved,
                    summary = new
                    {
                        referenceCount = state.References.Count,
                        directReferenceCount = state.References.Count(item => item.confidence == "exact"),
                        indirectReferenceCount = state.References.Count(item => item.confidence != "exact"),
                        unresolvedCount = state.Unresolved.Count,
                        serializedEdgeCount = state.References.Count(item => item.edgeKind == "serialized_object"),
                        animationEdgeCount = state.References.Count(item => item.edgeKind == "animation_binding"),
                        frameworkPathEdgeCount = state.References.Count(item => item.edgeKind == "framework_path"),
                        parameterEdgeCount = state.References.Count(item => item.edgeKind == "indirect_parameter"),
                        scannedComponentCount = state.ScannedComponentCount,
                        scannedAssetObjectCount = state.ScannedAssetObjectCount,
                        scannedAnimationClipCount = state.ScannedAnimationClipCount,
                        resultTruncated = state.Truncated,
                    },
                    mutationStarted = false,
                    committed = false,
                    commitState = "not_started",
                };
                return VRCForgeToolResult.Completed(
                    deletionSafe
                        ? "Inbound reference closure is complete and contains no references."
                        : complete
                            ? $"Inbound reference closure found {state.References.Count} reference edge(s)."
                            : "Inbound reference closure is indeterminate; deletion must remain blocked.",
                    payload);
            }
            catch (Exception ex)
            {
                var error = "Inbound reference closure scan failed: " + SafeMessage(ex);
                return VRCForgeToolResult.FailedWithCode(
                    "inbound_reference_closure_scan_failed",
                    error,
                    new
                    {
                        schema = "vrcforge.inbound_reference_closure.v1",
                        ok = false,
                        failureLayer = "unity_read_scan",
                        errorCode = "inbound_reference_closure_scan_failed",
                        error,
                        mutationStarted = false,
                        committed = false,
                        commitState = "not_started",
                        requestMayHaveCommitted = false,
                        checkpointRecoveryRequired = false,
                    });
            }
        }

        private static List<TargetRoot> ResolveTargetRoots(IEnumerable<string> rawPaths, int sceneHandle)
        {
            var result = new List<TargetRoot>();
            var seen = new HashSet<int>();
            foreach (var raw in rawPaths ?? Enumerable.Empty<string>())
            {
                if (string.IsNullOrWhiteSpace(raw))
                {
                    continue;
                }
                var go = ComponentCrudCore.ResolveGameObject(raw);
                if (go.scene.handle != sceneHandle)
                {
                    throw new InvalidOperationException($"Target root '{raw}' is not in the avatar scene.");
                }
                if (seen.Add(go.GetInstanceID()))
                {
                    result.Add(new TargetRoot
                    {
                        Transform = go.transform,
                        Path = ComponentCrudCore.GetHierarchyPath(go.transform),
                        Identity = GlobalObjectId.GetGlobalObjectIdSlow(go).ToString(),
                    });
                }
            }
            return result;
        }

        private static List<TargetComponent> ResolveTargetComponents(IEnumerable<ComponentSelector> selectors, int sceneHandle)
        {
            var result = new List<TargetComponent>();
            var seen = new HashSet<int>();
            foreach (var selector in selectors ?? Enumerable.Empty<ComponentSelector>())
            {
                if (selector == null || string.IsNullOrWhiteSpace(selector.objectPath) || string.IsNullOrWhiteSpace(selector.componentType))
                {
                    throw new InvalidOperationException("Each targetComponentSelectors entry requires objectPath and componentType.");
                }
                var go = ComponentCrudCore.ResolveGameObject(selector.objectPath);
                if (go.scene.handle != sceneHandle)
                {
                    throw new InvalidOperationException($"Target component '{selector.objectPath}' is not in the avatar scene.");
                }
                var type = ComponentCrudCore.ResolveComponentType(selector.componentType);
                var component = ComponentCrudCore.ResolveComponent(go, type, selector.componentIndex ?? 0);
                if (component is Transform)
                {
                    throw new InvalidOperationException("Transform cannot be selected as a removable component target.");
                }
                if (seen.Add(component.GetInstanceID()))
                {
                    result.Add(new TargetComponent
                    {
                        Component = component,
                        ObjectPath = ComponentCrudCore.GetHierarchyPath(go.transform),
                        Identity = GlobalObjectId.GetGlobalObjectIdSlow(component).ToString(),
                    });
                }
            }
            return result;
        }

        private static void CollectSemanticTokens(ScanState state)
        {
            var components = new List<Component>();
            foreach (var root in state.TargetRoots)
            {
                components.AddRange(root.Transform.GetComponentsInChildren<Component>(true).Where(item => item != null));
            }
            components.AddRange(state.TargetComponents.Select(item => item.Component));
            foreach (var component in components.Distinct())
            {
                var typeName = component.GetType().FullName ?? string.Empty;
                if (typeName.IndexOf("PhysBone", StringComparison.OrdinalIgnoreCase) < 0
                    && typeName.IndexOf("ContactReceiver", StringComparison.OrdinalIgnoreCase) < 0)
                {
                    continue;
                }
                try
                {
                    var serialized = new SerializedObject(component);
                    var iterator = serialized.GetIterator();
                    while (iterator.Next(true))
                    {
                        if (iterator.propertyType != SerializedPropertyType.String
                            || iterator.name.IndexOf("parameter", StringComparison.OrdinalIgnoreCase) < 0)
                        {
                            continue;
                        }
                        var value = (iterator.stringValue ?? string.Empty).Trim();
                        if (string.IsNullOrEmpty(value))
                        {
                            continue;
                        }
                        state.SemanticTokens.Add(value);
                        if (typeName.IndexOf("PhysBone", StringComparison.OrdinalIgnoreCase) >= 0)
                        {
                            foreach (var suffix in new[] { "_IsGrabbed", "_Angle", "_Stretch", "_Squish", "_IsPosed" })
                            {
                                state.SemanticTokens.Add(value + suffix);
                            }
                        }
                    }
                }
                catch (Exception ex)
                {
                    AddUnresolved(state, ComponentCrudCore.GetHierarchyPath(component.transform), component.GetType().FullName, "Could not inspect target semantic parameters: " + SafeMessage(ex));
                }
            }
        }

        private static void ScanSceneComponents(ScanState state)
        {
            foreach (var transform in ComponentCrudCore.EnumerateSceneGameObjects()
                .Where(item => item.scene.handle == state.Avatar.scene.handle)
                .Select(item => item.transform)
                .OrderBy(ComponentCrudCore.GetHierarchyPath, StringComparer.Ordinal))
            {
                if (IsWithinAnyTargetRoot(transform, state.TargetRoots))
                {
                    continue;
                }
                var components = transform.GetComponents<Component>();
                for (var index = 0; index < components.Length; index++)
                {
                    var component = components[index];
                    if (component == null)
                    {
                        AddUnresolved(state, ComponentCrudCore.GetHierarchyPath(transform), $"missing_component[{index}]", "A missing script component prevents a complete serialized reference proof.");
                        continue;
                    }
                    if (state.TargetComponents.Any(item => ReferenceEquals(item.Component, component)))
                    {
                        continue;
                    }
                    // Transform parent/child serialization is Unity hierarchy
                    // structure, not an author-authored inbound dependency.
                    // Reporting it would make every valid subtree deletion
                    // look referenced by its parent.
                    if (component is Transform)
                    {
                        continue;
                    }
                    state.ScannedComponentCount++;
                    ScanSerializedObject(state, component, transform, "", true, true);
                    if (component is Animator animator && animator.runtimeAnimatorController != null)
                    {
                        AddController(state, animator.runtimeAnimatorController, animator.transform, component, "m_Controller");
                    }
                }
            }
        }

        private static void ScanSerializedObject(
            ScanState state,
            UnityEngine.Object source,
            Transform sourceTransform,
            string sourceAssetPath,
            bool queueAssets,
            bool inspectPathStrings)
        {
            try
            {
                var serialized = new SerializedObject(source);
                var iterator = serialized.GetIterator();
                while (iterator.Next(true))
                {
                    if (iterator.propertyType == SerializedPropertyType.ObjectReference)
                    {
                        var referenced = iterator.objectReferenceValue;
                        if (referenced == null)
                        {
                            continue;
                        }
                        if (TryMatchTarget(state, referenced, out var targetPath, out var targetIdentity))
                        {
                            AddReference(state, new ReferenceEdge
                            {
                                edgeKind = "serialized_object",
                                sourceAssetPath = sourceAssetPath,
                                sourceObjectPath = sourceTransform == null ? "" : ComponentCrudCore.GetHierarchyPath(sourceTransform),
                                sourceComponentType = source.GetType().FullName,
                                sourcePropertyPath = iterator.propertyPath,
                                targetObjectPath = targetPath,
                                targetIdentity = targetIdentity,
                                confidence = "exact",
                                reason = "Serialized ObjectReference resolves to the deletion candidate.",
                            });
                        }
                        if (queueAssets && EditorUtility.IsPersistent(referenced))
                        {
                            QueueAsset(state, referenced);
                        }
                        if (referenced is RuntimeAnimatorController controller)
                        {
                            var root = sourceTransform ?? state.Avatar.transform;
                            AddController(state, controller, root, source, iterator.propertyPath);
                            if (root != state.Avatar.transform && IsDescendantOrSelf(root, state.Avatar.transform))
                            {
                                // Descriptor and framework components may use
                                // either local or avatar-root binding modes.
                                AddController(state, controller, state.Avatar.transform, source, iterator.propertyPath);
                            }
                        }
                    }
                    else if (iterator.propertyType == SerializedPropertyType.String)
                    {
                        var value = (iterator.stringValue ?? string.Empty).Trim();
                        if (string.IsNullOrEmpty(value))
                        {
                            continue;
                        }
                        if (inspectPathStrings
                            && (LooksLikePathProperty(iterator.propertyPath, value)
                                || IsPotentialTargetPathString(state, value)))
                        {
                            if (TryResolveTargetPathString(state, sourceTransform, value, out var targetPath, out var targetIdentity))
                            {
                                AddReference(state, new ReferenceEdge
                                {
                                    edgeKind = "framework_path",
                                    sourceAssetPath = sourceAssetPath,
                                    sourceObjectPath = sourceTransform == null ? "" : ComponentCrudCore.GetHierarchyPath(sourceTransform),
                                    sourceComponentType = source.GetType().FullName,
                                    sourcePropertyPath = iterator.propertyPath,
                                    targetObjectPath = targetPath,
                                    targetIdentity = targetIdentity,
                                    confidence = "resolved_path",
                                    reason = "Serialized framework path resolves into the deletion candidate.",
                                });
                            }
                            else if (IsPotentialTargetPathString(state, value))
                            {
                                AddUnresolvedPathString(
                                    state,
                                    source,
                                    sourceTransform,
                                    sourceAssetPath,
                                    iterator.propertyPath);
                            }
                        }
                        if (state.SemanticTokens.Contains(value))
                        {
                            AddReference(state, new ReferenceEdge
                            {
                                edgeKind = "indirect_parameter",
                                sourceAssetPath = sourceAssetPath,
                                sourceObjectPath = sourceTransform == null ? "" : ComponentCrudCore.GetHierarchyPath(sourceTransform),
                                sourceComponentType = source.GetType().FullName,
                                sourcePropertyPath = iterator.propertyPath,
                                targetObjectPath = "",
                                targetIdentity = "parameter:" + value,
                                confidence = "indirect",
                                reason = "Serialized parameter name consumes an output produced by a target PhysBone or Contact component.",
                            });
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                AddUnresolved(
                    state,
                    string.IsNullOrEmpty(sourceAssetPath)
                        ? (sourceTransform == null ? source.name : ComponentCrudCore.GetHierarchyPath(sourceTransform))
                        : sourceAssetPath,
                    source.GetType().FullName,
                    "Serialized reference scan failed: " + SafeMessage(ex));
            }
        }

        private static void ScanQueuedAssets(ScanState state, bool includeParameterEdges)
        {
            while (state.AssetQueue.Count > 0)
            {
                var path = state.AssetQueue.Dequeue();
                UnityEngine.Object[] assets;
                try
                {
                    assets = AssetDatabase.LoadAllAssetsAtPath(path) ?? new UnityEngine.Object[0];
                }
                catch (Exception ex)
                {
                    AddUnresolved(state, path, "", "Asset reference scan failed to load the asset: " + SafeMessage(ex));
                    continue;
                }
                if (assets.Length == 0)
                {
                    AddUnresolved(state, path, "", "Referenced inspectable asset could not be loaded.");
                    continue;
                }
                foreach (var asset in assets.Where(item => item != null))
                {
                    state.ScannedAssetObjectCount++;
                    ScanSerializedObject(state, asset, null, path, true, true);
                    if (!includeParameterEdges)
                    {
                        // The generic scan may have found parameter strings;
                        // keep the result conservative by making the proof indeterminate below.
                    }
                }
            }
        }

        private static void ScanAnimationBindings(ScanState state)
        {
            var seenClips = new HashSet<string>(StringComparer.Ordinal);
            foreach (var binding in state.Controllers.OrderBy(item => item.SourceObjectPath, StringComparer.Ordinal))
            {
                var clips = binding.Controller == null
                    ? new AnimationClip[0]
                    : (binding.Controller.animationClips ?? new AnimationClip[0]);
                foreach (var clip in clips.Where(item => item != null).Distinct())
                {
                    var clipKey = GlobalObjectId.GetGlobalObjectIdSlow(clip) + "|" + GlobalObjectId.GetGlobalObjectIdSlow(binding.Root);
                    if (!seenClips.Add(clipKey))
                    {
                        continue;
                    }
                    state.ScannedAnimationClipCount++;
                    ScanAnimationClipBindings(state, binding, clip, AnimationUtility.GetCurveBindings(clip));
                    ScanAnimationClipBindings(state, binding, clip, AnimationUtility.GetObjectReferenceCurveBindings(clip));
                }
            }
        }

        private static void ScanAnimationClipBindings(
            ScanState state,
            ControllerRoot controller,
            AnimationClip clip,
            IEnumerable<EditorCurveBinding> bindings)
        {
            foreach (var binding in bindings)
            {
                var resolved = ResolveBindingTransform(controller.Root, binding.path);
                if (resolved != null && TryMatchTargetTransform(state, resolved, binding.type, out var targetPath, out var targetIdentity))
                {
                    AddReference(state, new ReferenceEdge
                    {
                        edgeKind = "animation_binding",
                        sourceAssetPath = AssetDatabase.GetAssetPath(clip),
                        sourceObjectPath = controller.SourceObjectPath,
                        sourceComponentType = binding.type == null ? "" : binding.type.FullName,
                        sourcePropertyPath = (binding.path ?? "") + ":" + (binding.propertyName ?? ""),
                        targetObjectPath = targetPath,
                        targetIdentity = targetIdentity,
                        confidence = "resolved_path",
                        reason = "AnimationClip binding path resolves into the deletion candidate.",
                    });
                    continue;
                }

                if (resolved == null && BindingPathMayTargetCandidate(state, controller.Root, binding.path))
                {
                    AddUnresolved(
                        state,
                        AssetDatabase.GetAssetPath(clip),
                        (binding.path ?? "") + ":" + (binding.propertyName ?? ""),
                        "Animation binding path overlaps a target path but could not be resolved in the loaded scene.");
                }
            }
        }

        private static void AddController(ScanState state, RuntimeAnimatorController controller, Transform root, UnityEngine.Object source, string propertyPath)
        {
            if (controller == null || root == null)
            {
                return;
            }
            var key = controller.GetInstanceID() + "|" + root.GetInstanceID();
            if (!state.ControllerKeys.Add(key))
            {
                return;
            }
            state.Controllers.Add(new ControllerRoot
            {
                Controller = controller,
                Root = root,
                SourceObjectPath = source is Component component ? ComponentCrudCore.GetHierarchyPath(component.transform) : "",
                SourceComponentType = source == null ? "" : source.GetType().FullName,
                SourcePropertyPath = propertyPath ?? "",
            });
            QueueAsset(state, controller);
            foreach (var clip in controller.animationClips ?? new AnimationClip[0])
            {
                QueueAsset(state, clip);
            }
        }

        private static void QueueAsset(ScanState state, UnityEngine.Object asset)
        {
            if (asset == null || !EditorUtility.IsPersistent(asset))
            {
                return;
            }
            var path = (AssetDatabase.GetAssetPath(asset) ?? string.Empty).Replace('\\', '/');
            if (string.IsNullOrEmpty(path) || !IsInspectableAssetPath(path) || !state.QueuedAssetPaths.Add(path))
            {
                return;
            }
            state.AssetQueue.Enqueue(path);
        }

        private static bool IsInspectableAssetPath(string path)
        {
            var extension = Path.GetExtension(path).ToLowerInvariant();
            return extension == ".controller"
                || extension == ".overridecontroller"
                || extension == ".anim"
                || extension == ".asset"
                || extension == ".mask";
        }

        private static bool TryMatchTarget(ScanState state, UnityEngine.Object value, out string targetPath, out string targetIdentity)
        {
            foreach (var target in state.TargetComponents)
            {
                if (ReferenceEquals(value, target.Component))
                {
                    targetPath = target.ObjectPath;
                    targetIdentity = target.Identity;
                    return true;
                }
            }
            var transform = ObjectTransform(value);
            if (transform != null)
            {
                foreach (var root in state.TargetRoots)
                {
                    if (IsDescendantOrSelf(transform, root.Transform))
                    {
                        targetPath = ComponentCrudCore.GetHierarchyPath(transform);
                        targetIdentity = GlobalObjectId.GetGlobalObjectIdSlow(value).ToString();
                        return true;
                    }
                }
            }
            targetPath = "";
            targetIdentity = "";
            return false;
        }

        private static bool TryMatchTargetTransform(ScanState state, Transform transform, Type bindingType, out string targetPath, out string targetIdentity)
        {
            foreach (var root in state.TargetRoots)
            {
                if (IsDescendantOrSelf(transform, root.Transform))
                {
                    targetPath = ComponentCrudCore.GetHierarchyPath(transform);
                    targetIdentity = GlobalObjectId.GetGlobalObjectIdSlow(transform.gameObject).ToString();
                    return true;
                }
            }
            foreach (var target in state.TargetComponents)
            {
                if (target.Component.transform == transform
                    && bindingType != null
                    && (bindingType.IsAssignableFrom(target.Component.GetType()) || target.Component.GetType().IsAssignableFrom(bindingType)))
                {
                    targetPath = target.ObjectPath;
                    targetIdentity = target.Identity;
                    return true;
                }
            }
            targetPath = "";
            targetIdentity = "";
            return false;
        }

        private static bool TryResolveTargetPathString(ScanState state, Transform source, string raw, out string targetPath, out string targetIdentity)
        {
            var normalized = NormalizePath(raw);
            if (string.IsNullOrEmpty(normalized))
            {
                targetPath = "";
                targetIdentity = "";
                return false;
            }

            var candidates = new List<Transform>();
            var avatarPath = ComponentCrudCore.GetHierarchyPath(state.Avatar.transform);
            if (normalized == avatarPath || normalized.StartsWith(avatarPath + "/", StringComparison.Ordinal))
            {
                try
                {
                    candidates.Add(ComponentCrudCore.ResolveGameObject(normalized).transform);
                }
                catch
                {
                    // Unresolvable strings are not treated as exact references.
                }
            }
            var underAvatar = state.Avatar.transform.Find(normalized);
            if (underAvatar != null)
            {
                candidates.Add(underAvatar);
            }
            var relative = ResolveRelativeTransform(state.Avatar.transform, source, normalized);
            if (relative != null)
            {
                candidates.Add(relative);
            }
            foreach (var candidate in candidates.Distinct())
            {
                if (TryMatchTargetTransform(state, candidate, null, out targetPath, out targetIdentity))
                {
                    return true;
                }
            }
            targetPath = "";
            targetIdentity = "";
            return false;
        }

        private static Transform ResolveRelativeTransform(Transform avatarRoot, Transform source, string raw)
        {
            if (avatarRoot == null || source == null || !IsDescendantOrSelf(source, avatarRoot))
            {
                return null;
            }
            var avatarPath = ComponentCrudCore.GetHierarchyPath(avatarRoot);
            var sourcePath = ComponentCrudCore.GetHierarchyPath(source);
            var relativeSource = sourcePath == avatarPath ? "" : sourcePath.Substring(avatarPath.Length + 1);
            var segments = new List<string>(relativeSource.Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries));
            foreach (var segment in raw.Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries))
            {
                if (segment == ".")
                {
                    continue;
                }
                if (segment == "..")
                {
                    if (segments.Count == 0)
                    {
                        return null;
                    }
                    segments.RemoveAt(segments.Count - 1);
                    continue;
                }
                segments.Add(segment);
            }
            return segments.Count == 0 ? avatarRoot : avatarRoot.Find(string.Join("/", segments));
        }

        private static bool BindingPathMayTargetCandidate(ScanState state, Transform root, string bindingPath)
        {
            var normalized = NormalizePath(bindingPath);
            foreach (var target in state.TargetRoots)
            {
                if (!IsDescendantOrSelf(target.Transform, root))
                {
                    continue;
                }
                var relative = RelativePath(root, target.Transform);
                if (normalized == relative
                    || normalized.StartsWith(relative + "/", StringComparison.Ordinal)
                    || relative.StartsWith(normalized + "/", StringComparison.Ordinal))
                {
                    return true;
                }
            }
            foreach (var target in state.TargetComponents)
            {
                if (!IsDescendantOrSelf(target.Component.transform, root))
                {
                    continue;
                }
                var relative = RelativePath(root, target.Component.transform);
                if (normalized == relative)
                {
                    return true;
                }
            }
            return false;
        }

        private static Transform ResolveBindingTransform(Transform root, string path)
        {
            if (root == null)
            {
                return null;
            }
            var normalized = NormalizePath(path);
            return string.IsNullOrEmpty(normalized) ? root : root.Find(normalized);
        }

        private static bool IsWithinAnyTargetRoot(Transform transform, IEnumerable<TargetRoot> roots)
        {
            return roots.Any(root => IsDescendantOrSelf(transform, root.Transform));
        }

        private static bool IsDescendantOrSelf(Transform transform, Transform root)
        {
            for (var current = transform; current != null; current = current.parent)
            {
                if (current == root)
                {
                    return true;
                }
            }
            return false;
        }

        private static Transform ObjectTransform(UnityEngine.Object value)
        {
            if (value is GameObject gameObject)
            {
                return gameObject.transform;
            }
            if (value is Component component)
            {
                return component.transform;
            }
            return null;
        }

        private static string RelativePath(Transform root, Transform target)
        {
            var rootPath = ComponentCrudCore.GetHierarchyPath(root);
            var targetPath = ComponentCrudCore.GetHierarchyPath(target);
            return targetPath == rootPath ? "" : targetPath.Substring(rootPath.Length + 1);
        }

        private static string NormalizePath(string value)
        {
            return (value ?? string.Empty).Trim().Replace('\\', '/').Trim('/');
        }

        private static bool LooksLikePathProperty(string propertyPath, string value)
        {
            var property = (propertyPath ?? string.Empty).ToLowerInvariant();
            return (value ?? string.Empty).IndexOf('/') >= 0
                || (value ?? string.Empty).IndexOf("..", StringComparison.Ordinal) >= 0
                || property.Contains("path")
                || property.Contains("target")
                || property.Contains("source")
                || property.Contains("reference")
                || property.Contains("root");
        }

        private static bool IsPotentialTargetPathString(ScanState state, string raw)
        {
            var candidate = NormalizePath(raw);
            if (string.IsNullOrEmpty(candidate))
            {
                return false;
            }

            var avatarRoot = state.Avatar.transform;
            foreach (var root in state.TargetRoots)
            {
                if (PathsOverlap(candidate, NormalizePath(root.Path))
                    || (IsDescendantOrSelf(root.Transform, avatarRoot)
                        && PathsOverlap(candidate, NormalizePath(RelativePath(avatarRoot, root.Transform))))
                    || PathsOverlap(candidate, NormalizePath(root.Transform.name)))
                {
                    return true;
                }
            }
            // A serialized GameObject path is not evidence that a component on
            // that object is referenced. Component targets are matched only by
            // exact object references or animation bindings of a compatible
            // component type; otherwise removing a collider would be blocked by
            // an unrelated animation of the carrier Transform.
            return false;
        }

        private static bool PathsOverlap(string candidate, string target)
        {
            if (string.IsNullOrEmpty(candidate) || string.IsNullOrEmpty(target))
            {
                return false;
            }
            return string.Equals(candidate, target, StringComparison.Ordinal)
                || candidate.StartsWith(target + "/", StringComparison.Ordinal)
                || target.StartsWith(candidate + "/", StringComparison.Ordinal)
                || candidate.EndsWith("/" + target, StringComparison.Ordinal)
                || target.EndsWith("/" + candidate, StringComparison.Ordinal);
        }

        private static void AddUnresolvedPathString(
            ScanState state,
            UnityEngine.Object source,
            Transform sourceTransform,
            string sourceAssetPath,
            string propertyPath)
        {
            AddUnresolved(
                state,
                string.IsNullOrEmpty(sourceAssetPath)
                    ? (sourceTransform == null ? source.name : ComponentCrudCore.GetHierarchyPath(sourceTransform))
                    : sourceAssetPath,
                propertyPath,
                "The serialized path-like value could not be resolved safely but overlaps a deletion candidate; deletion must remain blocked.");
        }

        private static void AddReference(ScanState state, ReferenceEdge edge)
        {
            var key = string.Join("|", new[]
            {
                edge.edgeKind ?? "", edge.sourceAssetPath ?? "", edge.sourceObjectPath ?? "",
                edge.sourceComponentType ?? "", edge.sourcePropertyPath ?? "", edge.targetIdentity ?? "",
            });
            if (!state.ReferenceKeys.Add(key))
            {
                return;
            }
            if (state.References.Count >= state.MaxResults)
            {
                state.Truncated = true;
                return;
            }
            state.References.Add(edge);
        }

        private static void AddUnresolved(ScanState state, string source, string propertyPath, string reason)
        {
            var key = (source ?? "") + "|" + (propertyPath ?? "") + "|" + (reason ?? "");
            if (!state.UnresolvedKeys.Add(key))
            {
                return;
            }
            state.Unresolved.Add(new UnresolvedEdge
            {
                source = source ?? "",
                propertyPath = propertyPath ?? "",
                reason = reason ?? "Unknown reference scan failure.",
            });
        }

        private static string ComputeScopeDigest(string projectPath, string scenePath, GameObject avatar, IEnumerable<string> identities)
        {
            var canonical = string.Join("\n", new[]
            {
                NormalizePath(projectPath), NormalizePath(scenePath), ComponentCrudCore.GetHierarchyPath(avatar.transform),
                SceneFileDigest(projectPath, scenePath),
                string.Join("\n", identities.OrderBy(item => item, StringComparer.Ordinal)),
            });
            using (var sha = SHA256.Create())
            {
                return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(canonical))).Replace("-", "").ToLowerInvariant();
            }
        }

        private static string SceneFileDigest(string projectPath, string scenePath)
        {
            var fullPath = Path.GetFullPath(Path.Combine(projectPath, scenePath.Replace('/', Path.DirectorySeparatorChar)));
            if (!File.Exists(fullPath))
            {
                return "missing";
            }
            using (var sha = SHA256.Create())
            using (var stream = File.OpenRead(fullPath))
            {
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
            }
        }

        private static string SafeMessage(Exception ex)
        {
            var message = ex == null || string.IsNullOrWhiteSpace(ex.Message) ? "unknown error" : ex.Message.Trim();
            return message.Length <= 512 ? message : message.Substring(0, 512);
        }
    }
}
