using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_scan_avatar_materials",
        Description = "Scan avatar renderers and shared materials into a read-only material inventory snapshot."
    )]
    public static class ShaderMaterialScanner
    {
        public const string ToolName = "vrc_scan_avatar_materials";
        public const string DefaultOutputPath = "Assets/VRCForge/material_inventory.json";

        public class Parameters
        {
            [ToolParameter("Optional avatar root hierarchy path. If empty, all scene avatar roots are scanned.", Required = false)]
            public string avatarPath { get; set; } = "";

            [ToolParameter("Asset-relative or absolute output path.", Required = false)]
            public string outputPath { get; set; } = DefaultOutputPath;

            [ToolParameter("Refresh the Unity AssetDatabase after writing JSON.", Required = false)]
            public bool? refreshAssets { get; set; } = true;
        }

        [MenuItem("VRCForge/Scan Shader Materials")]
        public static void ScanFromMenu()
        {
            var payload = BuildPayload("");
            WritePayload(DefaultOutputPath, payload, true);
            Debug.Log($"[{ToolName}] Material scan complete: {DefaultOutputPath}");
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters();

            try
            {
                var payload = BuildPayload(parameters.avatarPath ?? "");
                var requestedPath = string.IsNullOrWhiteSpace(parameters.outputPath)
                    ? DefaultOutputPath
                    : parameters.outputPath;
                var absolutePath = WritePayload(requestedPath, payload, parameters.refreshAssets ?? true);
                payload.outputPath = ToAssetRelativePath(absolutePath);
                payload.absoluteOutputPath = absolutePath.Replace("\\", "/");

                return new SuccessResponse(
                    $"Scanned {payload.summary.materialCount} material slot(s) from {payload.summary.rendererCount} renderer(s).",
                    payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Material scan failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        public static MaterialInventoryPayload BuildPayload(string avatarPath)
        {
            var normalizedAvatarPath = NormalizePath(avatarPath);
            var avatars = ResolveAvatarRoots(normalizedAvatarPath);
            var materials = new List<MaterialInventoryItem>();
            var rendererCount = 0;
            var sceneNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            foreach (var avatarRoot in avatars.OrderBy(GetTransformPath))
            {
                sceneNames.Add(avatarRoot.gameObject.scene.name);
                var renderers = avatarRoot.GetComponentsInChildren<Renderer>(true)
                    .Where(IsSceneObject)
                    .OrderBy(renderer => GetTransformPath(renderer.transform))
                    .ToList();

                foreach (var renderer in renderers)
                {
                    rendererCount++;
                    var rendererPath = GetTransformPath(renderer.transform);
                    var meshName = GetMeshName(renderer);
                    var rendererId = StableId("renderer", rendererPath);
                    var sharedMaterials = renderer.sharedMaterials ?? Array.Empty<Material>();

                    for (var slotIndex = 0; slotIndex < sharedMaterials.Length; slotIndex++)
                    {
                        var material = sharedMaterials[slotIndex];
                        if (material == null)
                        {
                            continue;
                        }

                        var shaderName = material.shader != null ? material.shader.name : "";
                        var materialName = material.name ?? "";
                        var adapter = ShaderAdapterRegistry.GetAdapter(material);
                        var shaderFamily = adapter != null ? adapter.ShaderFamily : "Unsupported";
                        var category = DetectMaterialCategory(rendererPath, renderer.name, meshName, materialName);
                        var materialId = StableId(
                            "mat",
                            $"{NormalizePath(rendererPath)}|{slotIndex}|{materialName}|{shaderName}");

                        materials.Add(new MaterialInventoryItem
                        {
                            material_id = materialId,
                            avatar_name = avatarRoot.name,
                            avatar_path = GetTransformPath(avatarRoot),
                            item_path = rendererPath,
                            renderer_id = rendererId,
                            renderer_name = renderer.name,
                            renderer_path = rendererPath,
                            mesh_name = meshName,
                            slot_index = slotIndex,
                            material_name = materialName,
                            shader_name = shaderName,
                            shader_family = shaderFamily,
                            category = category,
                            shared_material_key = $"{materialName}|{shaderName}",
                            supported_properties = adapter != null
                                ? adapter.ReadSupportedProperties(material)
                                : new Dictionary<string, MaterialPropertyValue>()
                        });
                    }
                }
            }

            return new MaterialInventoryPayload
            {
                type = "material_inventory_snapshot",
                version = "0.2",
                id = $"inv_{DateTime.UtcNow:yyyyMMdd_HHmmss}",
                created_at = DateTime.UtcNow.ToString("O"),
                unity_project = Directory.GetParent(Application.dataPath)?.Name ?? "UnknownProject",
                requested_avatar_path = normalizedAvatarPath,
                scenes = sceneNames.OrderBy(name => name).ToList(),
                materials = materials,
                summary = new MaterialInventorySummary
                {
                    avatarCount = avatars.Count,
                    rendererCount = rendererCount,
                    materialCount = materials.Count,
                    lilToonCount = materials.Count(item => item.shader_family == "lilToon"),
                    poiyomiCount = materials.Count(item => item.shader_family == "Poiyomi"),
                    genericCount = materials.Count(item => item.shader_family == "Generic"),
                    unsupportedCount = materials.Count(item => item.shader_family == "Unsupported")
                }
            };
        }

        private static string WritePayload(string requestedPath, MaterialInventoryPayload payload, bool refreshAssets)
        {
            var absolutePath = ResolveToAbsolutePath(requestedPath);
            var parentDirectory = Path.GetDirectoryName(absolutePath);
            if (string.IsNullOrEmpty(parentDirectory))
            {
                throw new InvalidOperationException($"Cannot resolve parent folder for material scan path: {requestedPath}");
            }

            Directory.CreateDirectory(parentDirectory);
            File.WriteAllText(absolutePath, JsonConvert.SerializeObject(payload, Formatting.Indented), Encoding.UTF8);

            if (refreshAssets)
            {
                AssetDatabase.Refresh();
            }

            return absolutePath;
        }

        private static List<Transform> ResolveAvatarRoots(string normalizedAvatarPath)
        {
            var renderers = Resources.FindObjectsOfTypeAll<Renderer>().Where(IsSceneObject);
            var roots = new Dictionary<string, Transform>(StringComparer.OrdinalIgnoreCase);

            foreach (var renderer in renderers)
            {
                var root = FindAvatarRoot(renderer.transform);
                var path = NormalizePath(GetTransformPath(root));
                if (!string.IsNullOrEmpty(normalizedAvatarPath)
                    && !string.Equals(path, normalizedAvatarPath, StringComparison.OrdinalIgnoreCase)
                    && !path.EndsWith("/" + normalizedAvatarPath, StringComparison.OrdinalIgnoreCase)
                    && !root.name.Equals(normalizedAvatarPath, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                if (!roots.ContainsKey(path))
                {
                    roots.Add(path, root);
                }
            }

            if (roots.Count == 0 && !string.IsNullOrEmpty(normalizedAvatarPath))
            {
                throw new InvalidOperationException($"Could not locate avatar root: {normalizedAvatarPath}");
            }

            return roots.Values.ToList();
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

        private static string GetMeshName(Renderer renderer)
        {
            if (renderer is SkinnedMeshRenderer skinned && skinned.sharedMesh != null)
            {
                return skinned.sharedMesh.name;
            }

            var filter = renderer.GetComponent<MeshFilter>();
            return filter != null && filter.sharedMesh != null ? filter.sharedMesh.name : "";
        }

        private static string DetectMaterialCategory(params string[] values)
        {
            var text = string.Join(" ", values.Where(value => !string.IsNullOrWhiteSpace(value))).ToLowerInvariant();

            if (ContainsAny(text, "face", "skin", "body"))
            {
                return "skin";
            }

            if (ContainsAny(text, "eye", "iris", "pupil"))
            {
                return "eyes";
            }

            if (ContainsAny(text, "hair"))
            {
                return "hair";
            }

            if (ContainsAny(text, "cloth", "clothes", "hoodie", "shirt", "skirt", "dress", "pants", "shoe"))
            {
                return "clothes";
            }

            if (ContainsAny(text, "accessory", "ring", "glasses", "hat"))
            {
                return "accessory";
            }

            return "unknown";
        }

        private static bool ContainsAny(string haystack, params string[] needles)
        {
            return needles.Any(needle => haystack.Contains(needle));
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

        private static string ResolveToAbsolutePath(string requestedPath)
        {
            if (Path.IsPathRooted(requestedPath))
            {
                return requestedPath.Replace("\\", "/");
            }

            var projectRoot = Directory.GetParent(Application.dataPath)?.FullName
                ?? throw new InvalidOperationException("Cannot determine Unity project root.");

            return Path.Combine(projectRoot, requestedPath).Replace("\\", "/");
        }

        private static string ToAssetRelativePath(string absolutePath)
        {
            var dataPath = Application.dataPath.Replace("\\", "/");
            if (absolutePath.StartsWith(dataPath, StringComparison.OrdinalIgnoreCase))
            {
                return "Assets" + absolutePath.Substring(dataPath.Length);
            }

            return absolutePath.Replace("\\", "/");
        }
    }

    [Serializable]
    public class MaterialInventoryPayload
    {
        public string type;
        public string version;
        public string id;
        public string created_at;
        public string unity_project;
        public string requested_avatar_path;
        public List<string> scenes;
        public List<MaterialInventoryItem> materials;
        public MaterialInventorySummary summary;
        public string outputPath;
        public string absoluteOutputPath;
    }

    [Serializable]
    public class MaterialInventorySummary
    {
        public int avatarCount;
        public int rendererCount;
        public int materialCount;
        public int lilToonCount;
        public int poiyomiCount;
        public int genericCount;
        public int unsupportedCount;
    }

    [Serializable]
    public class MaterialInventoryItem
    {
        public string material_id;
        public string avatar_name;
        public string avatar_path;
        public string item_path;
        public string renderer_id;
        public string renderer_name;
        public string renderer_path;
        public string mesh_name;
        public int slot_index;
        public string material_name;
        public string shader_name;
        public string shader_family;
        public string category;
        public string shared_material_key;
        public Dictionary<string, MaterialPropertyValue> supported_properties;
    }

    [Serializable]
    public class MaterialPropertyValue
    {
        public string type;
        public object value;
        public bool writable;
    }
}
