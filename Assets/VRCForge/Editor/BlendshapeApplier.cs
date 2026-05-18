using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_apply_blendshapes",
        Description = "Apply explicit blendshape weights to scene avatar renderers via a predefined VRCForge tool."
    )]
    public static class BlendshapeApplier
    {
        public const string ToolName = "vrc_apply_blendshapes";

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var saveAssets = @params?["saveAssets"]?.Value<bool?>() ?? true;
                var adjustments = @params?["adjustments"] as JArray;
                if (adjustments == null || adjustments.Count == 0)
                {
                    return new ErrorResponse("Missing required parameter: adjustments");
                }

                var applied = new List<object>();
                foreach (var token in adjustments.OfType<JObject>())
                {
                    var rendererPath = (token["rendererPath"]?.ToString() ?? string.Empty).Trim();
                    var blendshapeName = (token["blendshapeName"]?.ToString() ?? string.Empty).Trim();
                    var targetWeight = token["targetWeight"]?.Value<float?>() ?? float.NaN;
                    if (string.IsNullOrWhiteSpace(rendererPath) || string.IsNullOrWhiteSpace(blendshapeName) || float.IsNaN(targetWeight))
                    {
                        return new ErrorResponse("Each adjustment requires rendererPath, blendshapeName, and targetWeight.");
                    }

                    var renderer = ResolveRenderer(avatarPath, rendererPath);
                    var mesh = renderer.sharedMesh;
                    if (mesh == null)
                    {
                        return new ErrorResponse($"Renderer '{rendererPath}' has no shared mesh.");
                    }

                    var blendshapeIndex = mesh.GetBlendShapeIndex(blendshapeName);
                    if (blendshapeIndex < 0)
                    {
                        return new ErrorResponse($"Blendshape '{blendshapeName}' was not found on renderer '{rendererPath}'.");
                    }

                    var previousWeight = renderer.GetBlendShapeWeight(blendshapeIndex);
                    var clampedWeight = Mathf.Clamp(targetWeight, 0f, 100f);
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

                return new SuccessResponse(
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
                return new ErrorResponse($"Blendshape apply failed: {ex.Message}\n{ex.StackTrace}");
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
