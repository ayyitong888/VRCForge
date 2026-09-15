using System;
using System.Collections.Generic;
using System.Linq;
using VRCForge.Core.MCP;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_apply_blendshapes",
        Summary = "Apply explicit blendshape weights to scene avatar renderers via a predefined VRCForge tool."
    )]
    public static class BlendshapeApplier
    {
        public const string ToolName = "vrc_apply_blendshapes";

        public class Parameters
        {
            [VRCForgeInput("Optional avatar root hierarchy path used to scope renderer lookup.", IsRequired = false)]
            public string avatarPath { get; set; } = "";
            [VRCForgeInput("One or more objects containing rendererPath, blendshapeName, and targetWeight.", IsRequired = true)]
            public JArray adjustments { get; set; } = new JArray();
            [VRCForgeInput("Save only affected scenes after applying weights.", IsRequired = false, DefaultLiteral = "true")]
            public bool? saveAssets { get; set; } = true;
        }

        public static object HandleCommand(JObject @params)
        {
            var recovery = new WriteAnimationCurveTool.AssetEditRecovery();
            var originals = new Dictionary<Tuple<SkinnedMeshRenderer, int>, float>();
            var sceneDirty = new Dictionary<UnityEngine.SceneManagement.Scene, bool>();
            var mutationStarted = false;
            try
            {
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var saveAssets = @params?["saveAssets"]?.Value<bool?>() ?? true;
                var adjustments = @params?["adjustments"] as JArray;
                if (adjustments == null || adjustments.Count == 0)
                {
                    return VRCForgeToolResult.Failed("Missing required parameter: adjustments");
                }

                var applied = new List<BlendshapeChangeReceipt>();
                var touchedScenes = new HashSet<UnityEngine.SceneManagement.Scene>();
                var planned = new List<Tuple<SkinnedMeshRenderer, int, string, string, float>>();
                foreach (var entry in adjustments)
                {
                    var token = entry as JObject;
                    if (token == null)
                        throw new InvalidOperationException("Each adjustment must be an object.");
                    var rendererPath = (token["rendererPath"]?.ToString() ?? string.Empty).Trim();
                    var blendshapeName = (token["blendshapeName"]?.ToString() ?? string.Empty).Trim();
                    var targetWeight = token["targetWeight"]?.Value<float?>() ?? float.NaN;
                    if (string.IsNullOrWhiteSpace(rendererPath) || string.IsNullOrWhiteSpace(blendshapeName)
                        || float.IsNaN(targetWeight) || float.IsInfinity(targetWeight))
                    {
                        return VRCForgeToolResult.Failed("Each adjustment requires rendererPath, blendshapeName, and targetWeight.");
                    }

                    var renderer = ResolveRenderer(avatarPath, rendererPath);
                    var mesh = renderer.sharedMesh;
                    if (mesh == null)
                    {
                        return VRCForgeToolResult.Failed($"Renderer '{rendererPath}' has no shared mesh.");
                    }

                    var blendshapeIndex = mesh.GetBlendShapeIndex(blendshapeName);
                    if (blendshapeIndex < 0)
                    {
                        return VRCForgeToolResult.Failed($"Blendshape '{blendshapeName}' was not found on renderer '{rendererPath}'.");
                    }

                    var scene = renderer.gameObject.scene;
                    if (saveAssets && (string.IsNullOrWhiteSpace(scene.path)
                        || !scene.path.StartsWith("Assets/", StringComparison.Ordinal)))
                        throw new InvalidOperationException("Blendshape persistence requires a saved project scene.");
                    if (saveAssets && scene.isDirty)
                        throw new InvalidOperationException("Save the target scene before applying persistent blendshape changes.");
                    touchedScenes.Add(scene);
                    sceneDirty[scene] = scene.isDirty;
                    var key = Tuple.Create(renderer, blendshapeIndex);
                    if (!originals.ContainsKey(key))
                        originals.Add(key, renderer.GetBlendShapeWeight(blendshapeIndex));
                    planned.Add(Tuple.Create(renderer, blendshapeIndex, rendererPath, blendshapeName,
                        Mathf.Clamp(targetWeight, 0f, 100f)));
                }

                if (saveAssets)
                    foreach (var scene in touchedScenes) recovery.Capture(scene.path);
                recovery.Begin();
                foreach (var change in planned)
                {
                    var renderer = change.Item1;
                    var blendshapeIndex = change.Item2;
                    var rendererPath = change.Item3;
                    var blendshapeName = change.Item4;
                    var clampedWeight = change.Item5;
                    var previousWeight = renderer.GetBlendShapeWeight(blendshapeIndex);
                    Undo.RecordObject(renderer, "Apply VRCForge blendshape weight");
                    mutationStarted = true;
                    renderer.SetBlendShapeWeight(blendshapeIndex, clampedWeight);
                    var currentWeight = renderer.GetBlendShapeWeight(blendshapeIndex);
                    if (currentWeight != clampedWeight)
                        throw new InvalidOperationException("Blendshape weight readback did not match the requested value.");
                    EditorUtility.SetDirty(renderer);
                    EditorUtility.SetDirty(renderer.gameObject);
                    EditorSceneManager.MarkSceneDirty(renderer.gameObject.scene);
                    applied.Add(new BlendshapeChangeReceipt
                    {
                        rendererPath = rendererPath,
                        blendshapeName = blendshapeName,
                        previousWeight = previousWeight,
                        targetWeight = clampedWeight,
                        currentWeight = currentWeight
                    });
                }

                if (saveAssets)
                {
                    // RecordObject finalizes at the end of an editor action. Flush
                    // before same-frame saving so it cannot dirty the scene again.
                    Undo.FlushUndoRecordObjects();
                    foreach (var scene in touchedScenes)
                    {
                        if (!EditorSceneManager.SaveScene(scene) || scene.isDirty)
                            throw new InvalidOperationException($"The blendshape target scene could not be saved cleanly: {scene.path}");
                    }
                }

                recovery.Complete();
                return VRCForgeToolResult.Completed(
                    $"Applied {applied.Count} blendshape adjustment(s).",
                    new
                    {
                        avatarPath,
                        appliedCount = applied.Count,
                        applied,
                        saved = saveAssets,
                        before = applied.Select(item => new
                        {
                            item.rendererPath,
                            item.blendshapeName,
                            weight = item.previousWeight
                        }).ToArray(),
                        after = applied.Select(item => new
                        {
                            item.rendererPath,
                            item.blendshapeName,
                            weight = item.currentWeight
                        }).ToArray(),
                        pending = !saveAssets,
                        note = saveAssets ? "已修改并落盘" : "已修改，尚未落盘"
                    });
            }
            catch (Exception ex)
            {
                var restored = false;
                if (mutationStarted)
                {
                    // Finalize same-call RecordObject snapshots before reverting.
                    try { Undo.FlushUndoRecordObjects(); }
                    catch { /* Restore still runs and its loaded-state checks decide the outcome. */ }
                    restored = recovery.Restore();
                    // Asset recovery verifies saved bytes/meta and reverts our Undo
                    // group. A scene import may invalidate references; verify the
                    // loaded objects and dirty baseline separately before claiming
                    // compensation. Never save a scene during this recovery path.
                    foreach (var original in originals)
                    {
                        try
                        {
                            restored &= original.Key.Item1 != null
                                && original.Key.Item1.GetBlendShapeWeight(original.Key.Item2) == original.Value;
                        }
                        catch { restored = false; }
                    }
                    foreach (var scene in sceneDirty)
                        restored &= scene.Key.IsValid() && scene.Key.isLoaded && scene.Key.isDirty == scene.Value;
                }
                return VRCForgeToolResult.Failed($"Blendshape apply failed: {ex.Message}\n{ex.StackTrace}", new
                {
                    mutationStarted,
                    committed = !mutationStarted || restored ? (bool?)false : null,
                    commitState = !mutationStarted ? "not_started" : restored ? "rolled_back" : "unknown",
                    commitStateKnown = !mutationStarted || restored,
                    restored,
                    checkpointRecoveryRequired = mutationStarted && !restored,
                    retryable = false
                });
            }
        }

        private sealed class BlendshapeChangeReceipt
        {
            public string rendererPath;
            public string blendshapeName;
            public float previousWeight;
            public float targetWeight;
            public float currentWeight;
        }

        private static SkinnedMeshRenderer ResolveRenderer(string avatarPath, string rendererPath)
        {
            var renderers = Resources.FindObjectsOfTypeAll<SkinnedMeshRenderer>().Where(IsSceneObject);

            var normalizedAvatarPath = NormalizePath(avatarPath);
            var normalizedRendererPath = NormalizePath(rendererPath);

            var matches = renderers.Where(renderer =>
                NormalizePath(GetTransformPath(renderer.transform)) == normalizedRendererPath
                && (string.IsNullOrEmpty(normalizedAvatarPath)
                    || NormalizePath(GetTransformPath(FindAvatarRoot(renderer.transform))) == normalizedAvatarPath)).ToArray();

            if (matches.Length != 1)
            {
                throw new InvalidOperationException(
                    $"Expected one renderer '{rendererPath}' under avatar '{avatarPath}', found {matches.Length}.");
            }

            return matches[0];
        }

        private static bool IsSceneObject(Component component)
        {
            return component != null
                && component.gameObject.scene.IsValid()
                && component.gameObject.scene.isLoaded
                && !EditorUtility.IsPersistent(component);
        }

        private static Transform FindAvatarRoot(Transform source)
        {
            var current = source;
            Transform fallback = source.root;
            var avatarDescriptorType = FindType("VRC.SDK3.Avatars.Components.VRCAvatarDescriptor");

            while (current != null)
            {
                if (avatarDescriptorType != null && current.GetComponent(avatarDescriptorType) != null)
                {
                    return current;
                }

                if (current.GetComponent<Animator>() != null)
                {
                    fallback = current;
                }

                current = current.parent;
            }

            return fallback;
        }

        private static Type FindType(string fullName)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    var type = assembly.GetType(fullName, false);
                    if (type != null)
                    {
                        return type;
                    }
                }
                catch
                {
                    // Ignore transient reflection failures from editor reloads.
                }
            }

            return null;
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
    }
}
