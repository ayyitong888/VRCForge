using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_apply_material_tuning",
        Description = "Apply validated semantic material parameter changes through shader adapters."
    )]
    public static class MaterialTuningApplier
    {
        public const string ToolName = "vrc_apply_material_tuning";

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var avatarPath = (@params?["avatarPath"]?.ToString() ?? string.Empty).Trim();
                var saveAssets = @params?["saveAssets"]?.Value<bool?>() ?? true;
                var changes = @params?["changes"] as JArray;
                if (changes == null || changes.Count == 0)
                {
                    return new ErrorResponse("Missing required parameter: changes");
                }

                var materialIndex = BuildMaterialIndex(avatarPath);
                var applied = new List<object>();
                var skipped = new List<object>();

                foreach (var token in changes.OfType<JObject>())
                {
                    var materialId = (token["material_id"]?.ToString() ?? token["materialId"]?.ToString() ?? string.Empty).Trim();
                    var semanticProperty = (token["semantic_property"]?.ToString() ?? token["semanticProperty"]?.ToString() ?? string.Empty).Trim();
                    var valueToken = token["after"] ?? token["target"] ?? token["value"];
                    if (string.IsNullOrWhiteSpace(materialId) || string.IsNullOrWhiteSpace(semanticProperty) || valueToken == null)
                    {
                        skipped.Add(new { materialId, semanticProperty, warning = "Each change requires material_id, semantic_property, and after." });
                        continue;
                    }

                    if (!materialIndex.TryGetValue(materialId, out var target))
                    {
                        skipped.Add(new { materialId, semanticProperty, warning = "Material id was not found in the current scene." });
                        continue;
                    }

                    var adapter = ShaderAdapterRegistry.GetAdapter(target.material);
                    if (adapter == null)
                    {
                        skipped.Add(new { materialId, semanticProperty, warning = "Unsupported shader family." });
                        continue;
                    }

                    if (!adapter.TryApplyChange(target.material, semanticProperty, ExtractValue(valueToken), out var previousValue, out var appliedValue, out var warning))
                    {
                        skipped.Add(new { materialId, semanticProperty, warning });
                        continue;
                    }

                    EditorUtility.SetDirty(target.material);
                    applied.Add(new
                    {
                        material_id = materialId,
                        material_name = target.material.name,
                        renderer_path = target.rendererPath,
                        slot_index = target.slotIndex,
                        shader_family = adapter.ShaderFamily,
                        semantic_property = semanticProperty,
                        before = previousValue,
                        after = appliedValue
                    });
                }

                if (saveAssets && applied.Count > 0)
                {
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh();
                }

                return new SuccessResponse(
                    $"Applied {applied.Count} material tuning change(s); skipped {skipped.Count}.",
                    new
                    {
                        avatarPath,
                        appliedCount = applied.Count,
                        skippedCount = skipped.Count,
                        applied,
                        skipped,
                        saved = saveAssets
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Material tuning apply failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static Dictionary<string, MaterialTarget> BuildMaterialIndex(string avatarPath)
        {
            var normalizedAvatarPath = NormalizePath(avatarPath);
            var index = new Dictionary<string, MaterialTarget>(StringComparer.OrdinalIgnoreCase);
            var renderers = Resources.FindObjectsOfTypeAll<Renderer>()
                .Where(IsSceneObject)
                .OrderBy(renderer => GetTransformPath(renderer.transform));

            foreach (var renderer in renderers)
            {
                var avatarRoot = FindAvatarRoot(renderer.transform);
                var rootPath = NormalizePath(GetTransformPath(avatarRoot));
                if (!string.IsNullOrEmpty(normalizedAvatarPath)
                    && !string.Equals(rootPath, normalizedAvatarPath, StringComparison.OrdinalIgnoreCase)
                    && !rootPath.EndsWith("/" + normalizedAvatarPath, StringComparison.OrdinalIgnoreCase)
                    && !avatarRoot.name.Equals(normalizedAvatarPath, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                var rendererPath = GetTransformPath(renderer.transform);
                var sharedMaterials = renderer.sharedMaterials ?? Array.Empty<Material>();
                for (var slotIndex = 0; slotIndex < sharedMaterials.Length; slotIndex++)
                {
                    var material = sharedMaterials[slotIndex];
                    if (material == null)
                    {
                        continue;
                    }

                    var shaderName = material.shader != null ? material.shader.name : "";
                    var materialId = StableId("mat", $"{NormalizePath(rendererPath)}|{slotIndex}|{material.name}|{shaderName}");
                    if (!index.ContainsKey(materialId))
                    {
                        index.Add(materialId, new MaterialTarget
                        {
                            material = material,
                            rendererPath = rendererPath,
                            slotIndex = slotIndex
                        });
                    }
                }
            }

            return index;
        }

        private static object ExtractValue(JToken token)
        {
            if (token == null || token.Type == JTokenType.Null)
            {
                return null;
            }

            if (token.Type == JTokenType.Float || token.Type == JTokenType.Integer)
            {
                return token.Value<float>();
            }

            return token.ToString();
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

        private static string StableId(string prefix, string value)
        {
            using (var sha1 = SHA1.Create())
            {
                var bytes = sha1.ComputeHash(Encoding.UTF8.GetBytes(NormalizePath(value)));
                var hex = BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();
                return $"{prefix}_{hex.Substring(0, 16)}";
            }
        }

        private static string NormalizePath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim().Trim('/');
        }

        private sealed class MaterialTarget
        {
            public Material material;
            public string rendererPath;
            public int slotIndex;
        }
    }
}
