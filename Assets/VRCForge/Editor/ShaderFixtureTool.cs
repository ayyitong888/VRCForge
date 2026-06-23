using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_set_material_shader",
        Description = "Set one scene renderer material slot or material asset to a named shader for supervised shader-adapter proof fixtures."
    )]
    public static class ShaderFixtureTool
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                var shaderName = (@params?["shaderName"]?.ToString() ?? @params?["targetShader"]?.ToString() ?? string.Empty).Trim();
                var shaderAssetPath = (@params?["shaderAssetPath"]?.ToString() ?? string.Empty).Trim();
                var rendererPath = (@params?["rendererPath"]?.ToString() ?? string.Empty).Trim();
                var materialAssetPath = (@params?["materialAssetPath"]?.ToString() ?? string.Empty).Trim();
                var slotIndex = @params?["slotIndex"]?.Value<int?>() ?? 0;
                var preview = @params?["preview"]?.Value<bool?>() ?? false;
                var saveAssets = @params?["saveAssets"]?.Value<bool?>() ?? true;

                if (string.IsNullOrWhiteSpace(shaderName))
                {
                    return new ErrorResponse("shaderName is required.");
                }

                var shader = ResolveShader(shaderName, shaderAssetPath);
                if (shader == null)
                {
                    return new ErrorResponse($"Shader was not found in the active Unity project: {shaderName}; shaderAssetPath={shaderAssetPath}");
                }

                var target = ResolveMaterialTarget(rendererPath, materialAssetPath, slotIndex);
                if (target.material == null)
                {
                    return new ErrorResponse("Material target could not be resolved.");
                }

                var beforeShader = target.material.shader != null ? target.material.shader.name : string.Empty;
                var materialPath = AssetDatabase.GetAssetPath(target.material);

                if (!preview)
                {
                    Undo.RecordObject(target.material, "Set VRCForge material shader fixture");
                    target.material.shader = shader;
                    EditorUtility.SetDirty(target.material);
                    if (saveAssets)
                    {
                        AssetDatabase.SaveAssets();
                        AssetDatabase.Refresh();
                    }
                }

                return new SuccessResponse(
                    preview ? "Material shader fixture preview completed." : "Material shader fixture applied.",
                    new
                    {
                        ok = true,
                        preview,
                        saved = !preview && saveAssets,
                        rendererPath = target.rendererPath,
                        materialAssetPath = materialPath,
                        slotIndex = target.slotIndex,
                        materialName = target.material.name,
                        beforeShader,
                        afterShader = shader.name,
                        shaderAssetPath = AssetDatabase.GetAssetPath(shader)
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Material shader fixture failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static Shader ResolveShader(string shaderName, string shaderAssetPath)
        {
            var explicitShader = LoadExplicitShaderAtAssetPath(shaderAssetPath);
            if (explicitShader != null)
            {
                return explicitShader;
            }

            var shader = Shader.Find(shaderName);
            if (shader != null)
            {
                return shader;
            }

            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            var leafName = shaderName.Split('/').LastOrDefault() ?? shaderName;
            var guids = AssetDatabase.FindAssets($"{leafName} t:Shader")
                .Concat(AssetDatabase.FindAssets("t:Shader", new[] { "Assets", "Packages" }))
                .Distinct();
            foreach (var guid in guids)
            {
                var path = AssetDatabase.GUIDToAssetPath(guid);
                var candidate = AssetDatabase.LoadAssetAtPath<Shader>(path);
                if (candidate == null)
                {
                    continue;
                }

                if (string.Equals(candidate.name, shaderName, StringComparison.Ordinal)
                    || string.Equals(Path.GetFileNameWithoutExtension(path), leafName, StringComparison.OrdinalIgnoreCase))
                {
                    return candidate;
                }
            }

            foreach (var path in EnumerateShaderAssetPaths(leafName))
            {
                var candidate = LoadShaderAtAssetPath(path, shaderName);
                if (candidate != null)
                {
                    return candidate;
                }
            }

            return null;
        }

        private static Shader LoadShaderAtAssetPath(string shaderAssetPath, string shaderName)
        {
            if (string.IsNullOrWhiteSpace(shaderAssetPath))
            {
                return null;
            }

            var normalizedPath = shaderAssetPath.Replace("\\", "/").Trim();
            var candidate = AssetDatabase.LoadAssetAtPath<Shader>(normalizedPath);
            if (candidate == null)
            {
                return null;
            }

            var leafName = shaderName.Split('/').LastOrDefault() ?? shaderName;
            if (string.Equals(candidate.name, shaderName, StringComparison.Ordinal)
                || string.Equals(Path.GetFileNameWithoutExtension(normalizedPath), leafName, StringComparison.OrdinalIgnoreCase))
            {
                return candidate;
            }

            return null;
        }

        private static Shader LoadExplicitShaderAtAssetPath(string shaderAssetPath)
        {
            if (string.IsNullOrWhiteSpace(shaderAssetPath))
            {
                return null;
            }

            var normalizedPath = shaderAssetPath.Replace("\\", "/").Trim();
            AssetDatabase.ImportAsset(normalizedPath, ImportAssetOptions.ForceSynchronousImport);
            return AssetDatabase.LoadAssetAtPath<Shader>(normalizedPath);
        }

        private static string[] EnumerateShaderAssetPaths(string leafName)
        {
            try
            {
                var paths = Directory.GetFiles("Packages", "*.shader", SearchOption.AllDirectories)
                    .Concat(Directory.GetFiles("Assets", "*.shader", SearchOption.AllDirectories))
                    .Select(path => path.Replace("\\", "/"))
                    .Where(path => string.Equals(Path.GetFileNameWithoutExtension(path), leafName, StringComparison.OrdinalIgnoreCase))
                    .ToArray();
                return paths;
            }
            catch
            {
                return Array.Empty<string>();
            }
        }

        private static MaterialTarget ResolveMaterialTarget(string rendererPath, string materialAssetPath, int slotIndex)
        {
            if (!string.IsNullOrWhiteSpace(materialAssetPath))
            {
                var materialAsset = AssetDatabase.LoadAssetAtPath<Material>(materialAssetPath.Replace("\\", "/"));
                if (materialAsset == null)
                {
                    throw new InvalidOperationException($"Material asset was not found: {materialAssetPath}");
                }

                return new MaterialTarget
                {
                    material = materialAsset,
                    rendererPath = string.Empty,
                    slotIndex = -1
                };
            }

            if (string.IsNullOrWhiteSpace(rendererPath))
            {
                throw new InvalidOperationException("rendererPath or materialAssetPath is required.");
            }

            if (slotIndex < 0)
            {
                throw new InvalidOperationException("slotIndex must be zero or greater.");
            }

            var normalizedRendererPath = NormalizePath(rendererPath);
            var renderer = Resources.FindObjectsOfTypeAll<Renderer>()
                .Where(IsSceneObject)
                .FirstOrDefault(item =>
                {
                    var path = NormalizePath(GetTransformPath(item.transform));
                    return string.Equals(path, normalizedRendererPath, StringComparison.OrdinalIgnoreCase)
                        || path.EndsWith("/" + normalizedRendererPath, StringComparison.OrdinalIgnoreCase);
                });
            if (renderer == null)
            {
                throw new InvalidOperationException($"Renderer was not found: {rendererPath}");
            }

            var materials = renderer.sharedMaterials ?? Array.Empty<Material>();
            if (slotIndex >= materials.Length)
            {
                throw new InvalidOperationException($"Renderer '{rendererPath}' has {materials.Length} material slot(s); slotIndex {slotIndex} is out of range.");
            }

            var material = materials[slotIndex];
            if (material == null)
            {
                throw new InvalidOperationException($"Renderer '{rendererPath}' material slot {slotIndex} is empty.");
            }

            return new MaterialTarget
            {
                material = material,
                rendererPath = GetTransformPath(renderer.transform),
                slotIndex = slotIndex
            };
        }

        private static bool IsSceneObject(Component component)
        {
            return component != null
                && component.gameObject.scene.IsValid()
                && component.gameObject.scene.isLoaded
                && !EditorUtility.IsPersistent(component);
        }

        private static string GetTransformPath(Transform transform)
        {
            if (transform == null)
            {
                return string.Empty;
            }

            var current = transform;
            var path = current.name;
            while (current.parent != null)
            {
                current = current.parent;
                path = current.name + "/" + path;
            }

            return path;
        }

        private static string NormalizePath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim().Trim('/');
        }

        private sealed class MaterialTarget
        {
            public Material material;
            public string rendererPath = string.Empty;
            public int slotIndex;
        }
    }
}
