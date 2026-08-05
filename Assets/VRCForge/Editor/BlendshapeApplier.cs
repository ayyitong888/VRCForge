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
            [VRCForgeInput("Save assets and open scenes after applying weights.", IsRequired = false, DefaultLiteral = "true")]
            public bool? saveAssets { get; set; } = true;
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var saveAssets = @params?["saveAssets"]?.Value<bool?>() ?? true;
                var adjustments = @params?["adjustments"] as JArray;
                if (adjustments == null || adjustments.Count == 0)
                {
                    return VRCForgeToolResult.Failed("Missing required parameter: adjustments");
                }

                var applied = new List<object>();
                foreach (var token in adjustments.OfType<JObject>())
                {
                    var rendererPath = (token["rendererPath"]?.ToString() ?? string.Empty).Trim();
                    var blendshapeName = (token["blendshapeName"]?.ToString() ?? string.Empty).Trim();
                    var targetWeight = token["targetWeight"]?.Value<float?>() ?? float.NaN;
                    if (string.IsNullOrWhiteSpace(rendererPath) || string.IsNullOrWhiteSpace(blendshapeName) || float.IsNaN(targetWeight))
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

                    var previousWeight = renderer.GetBlendShapeWeight(blendshapeIndex);
                    var clampedWeight = Mathf.Clamp(targetWeight, 0f, 100f);
                    Undo.RecordObject(renderer, "Apply VRCForge blendshape weight");
                    renderer.SetBlendShapeWeight(blendshapeIndex, clampedWeight);
                    EditorUtility.SetDirty(renderer);
                    EditorUtility.SetDirty(renderer.gameObject);
                    EditorSceneManager.MarkSceneDirty(renderer.gameObject.scene);
                    applied.Add(new
                    {
                        rendererPath,
                        blendshapeName,
                        previousWeight,
                        targetWeight = clampedWeight
                    });
                }

                if (saveAssets)
                {
                    AssetDatabase.SaveAssets();
                    EditorSceneManager.SaveOpenScenes();
                }

                return VRCForgeToolResult.Completed(
                    $"Applied {applied.Count} blendshape adjustment(s).",
                    new
                    {
                        avatarPath,
                        appliedCount = applied.Count,
                        applied,
                        saved = saveAssets
                    });
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Blendshape apply failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static SkinnedMeshRenderer ResolveRenderer(string avatarPath, string rendererPath)
        {
            var renderers = Resources.FindObjectsOfTypeAll<SkinnedMeshRenderer>().Where(IsSceneObject);

            var normalizedAvatarPath = NormalizePath(avatarPath);
            var normalizedRendererPath = NormalizePath(rendererPath);

            var match = renderers.FirstOrDefault(renderer =>
                NormalizePath(GetTransformPath(renderer.transform)) == normalizedRendererPath
                && (string.IsNullOrEmpty(normalizedAvatarPath)
                    || NormalizePath(GetTransformPath(FindAvatarRoot(renderer.transform))) == normalizedAvatarPath));

            if (match == null)
            {
                throw new InvalidOperationException(
                    $"Could not locate renderer '{rendererPath}' under avatar '{avatarPath}'.");
            }

            return match;
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
