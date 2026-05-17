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
        name: "vrc_scan_avatar_items",
        Description = "Scan avatar GameObjects and renderer-backed items into a read-only hierarchy inventory."
    )]
    public static class GameObjectTools
    {
        public const string ScanAvatarItemsToolName = "vrc_scan_avatar_items";
        public const string DefaultOutputPath = "Assets/VRCForge/avatar_items_inventory.json";

        private static readonly string[] WardrobeKeywords =
        {
            "cloth", "clothes", "clothing", "outfit", "wardrobe", "dress", "shirt", "skirt",
            "pants", "jacket", "coat", "hood", "hat", "shoe", "socks", "accessory", "acc",
            "top", "bottom", "costume", "toggle", "wear", "glasses", "bag", "mask"
        };

        public class ScanAvatarItemsParameters
        {
            [ToolParameter("Optional avatar root hierarchy path. If empty, all scene avatar roots are scanned.", Required = false)]
            public string avatarPath { get; set; } = "";

            [ToolParameter("Asset-relative or absolute output path. Leave empty to skip writing JSON.", Required = false)]
            public string outputPath { get; set; } = DefaultOutputPath;

            [ToolParameter("Maximum number of hierarchy items to return.", Required = false)]
            public int? maxItems { get; set; } = 500;

            [ToolParameter("Refresh the Unity AssetDatabase after writing JSON.", Required = false)]
            public bool? refreshAssets { get; set; } = true;
        }

        [MenuItem("VRCForge/Scan Avatar Items")]
        public static void ScanAvatarItemsFromMenu()
        {
            var payload = BuildAvatarItemsPayload("", 500);
            var absolutePath = WriteJson(DefaultOutputPath, payload, true);
            Debug.Log($"[{ScanAvatarItemsToolName}] Avatar item scan complete: {absolutePath}");
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<ScanAvatarItemsParameters>()
                ?? new ScanAvatarItemsParameters();

            try
            {
                var maxItems = Mathf.Clamp(parameters.maxItems ?? 500, 1, 2000);
                var payload = BuildAvatarItemsPayload(parameters.avatarPath ?? "", maxItems);
                var requestedPath = parameters.outputPath ?? "";
                if (!string.IsNullOrWhiteSpace(requestedPath))
                {
                    var absolutePath = WriteJson(requestedPath, payload, parameters.refreshAssets ?? true);
                    payload.outputPath = ToAssetRelativePath(absolutePath);
                    payload.absoluteOutputPath = absolutePath.Replace("\\", "/");
                }

                return new SuccessResponse(
                    $"Scanned {payload.summary.itemCount} avatar item(s) from {payload.summary.avatarCount} avatar(s).",
                    payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Avatar item scan failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static AvatarItemsPayload BuildAvatarItemsPayload(string avatarPath, int maxItems)
        {
            var normalizedAvatarPath = NormalizePath(avatarPath);
            var avatars = ResolveAvatarRoots(normalizedAvatarPath);
            var items = new List<AvatarItem>();
            var sceneNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            foreach (var avatarRoot in avatars.OrderBy(GetTransformPath))
            {
                sceneNames.Add(avatarRoot.gameObject.scene.name);
                var avatarPathValue = GetTransformPath(avatarRoot);
                var transforms = avatarRoot.GetComponentsInChildren<Transform>(true)
                    .Where(item => item != null)
                    .OrderBy(item => GetTransformPath(item))
                    .Take(maxItems)
                    .ToList();

                foreach (var transform in transforms)
                {
                    var renderers = transform.GetComponentsInChildren<Renderer>(true)
                        .Where(IsSceneObject)
                        .ToList();
                    var directRenderers = transform.GetComponents<Renderer>().Where(IsSceneObject).ToList();
                    var meshNames = renderers
                        .Select(GetMeshName)
                        .Where(value => !string.IsNullOrWhiteSpace(value))
                        .Distinct(StringComparer.OrdinalIgnoreCase)
                        .OrderBy(value => value)
                        .Take(12)
                        .ToList();
                    var materialNames = renderers
                        .SelectMany(renderer => renderer.sharedMaterials ?? Array.Empty<Material>())
                        .Where(material => material != null)
                        .Select(material => material.name)
                        .Where(value => !string.IsNullOrWhiteSpace(value))
                        .Distinct(StringComparer.OrdinalIgnoreCase)
                        .OrderBy(value => value)
                        .Take(16)
                        .ToList();
                    var shaderNames = renderers
                        .SelectMany(renderer => renderer.sharedMaterials ?? Array.Empty<Material>())
                        .Where(material => material != null && material.shader != null)
                        .Select(material => material.shader.name)
                        .Where(value => !string.IsNullOrWhiteSpace(value))
                        .Distinct(StringComparer.OrdinalIgnoreCase)
                        .OrderBy(value => value)
                        .Take(12)
                        .ToList();
                    var objectPath = GetTransformPath(transform);
                    var relativePath = GetRelativePath(avatarRoot, transform);
                    var category = DetectCategory(objectPath, string.Join(" ", meshNames), string.Join(" ", materialNames));

                    items.Add(new AvatarItem
                    {
                        item_id = StableId("item", objectPath),
                        avatar_name = avatarRoot.name,
                        avatar_path = avatarPathValue,
                        object_name = transform.name,
                        object_path = objectPath,
                        relative_path = relativePath,
                        active_self = transform.gameObject.activeSelf,
                        active_in_hierarchy = transform.gameObject.activeInHierarchy,
                        direct_child_count = transform.childCount,
                        component_types = transform.GetComponents<Component>()
                            .Where(component => component != null)
                            .Select(component => component.GetType().Name)
                            .Distinct(StringComparer.OrdinalIgnoreCase)
                            .OrderBy(value => value)
                            .ToList(),
                        renderer_count = renderers.Count,
                        direct_renderer_count = directRenderers.Count,
                        skinned_renderer_count = renderers.Count(renderer => renderer is SkinnedMeshRenderer),
                        mesh_summary = new MeshSummary
                        {
                            mesh_count = meshNames.Count,
                            mesh_names = meshNames
                        },
                        material_summary = new MaterialSummary
                        {
                            material_slot_count = renderers.Sum(renderer => (renderer.sharedMaterials ?? Array.Empty<Material>()).Length),
                            material_names = materialNames,
                            shader_names = shaderNames
                        },
                        likely_category = category,
                        wardrobe_related = IsWardrobeRelated(category, objectPath, string.Join(" ", meshNames), string.Join(" ", materialNames))
                    });
                }
            }

            var limited = items
                .OrderBy(item => item.avatar_path, StringComparer.OrdinalIgnoreCase)
                .ThenBy(item => item.object_path, StringComparer.OrdinalIgnoreCase)
                .Take(maxItems)
                .ToList();

            return new AvatarItemsPayload
            {
                type = "avatar_items_snapshot",
                version = "0.1",
                id = $"items_{DateTime.UtcNow:yyyyMMdd_HHmmss}",
                created_at = DateTime.UtcNow.ToString("O"),
                unity_project = Directory.GetParent(Application.dataPath)?.Name ?? "UnknownProject",
                requested_avatar_path = normalizedAvatarPath,
                scenes = sceneNames.OrderBy(name => name).ToList(),
                items = limited,
                summary = new AvatarItemsSummary
                {
                    avatarCount = avatars.Count,
                    itemCount = limited.Count,
                    rendererBackedItemCount = limited.Count(item => item.renderer_count > 0),
                    wardrobeCandidateCount = limited.Count(item => item.wardrobe_related),
                    truncated = items.Count > limited.Count
                }
            };
        }

        private static List<Transform> ResolveAvatarRoots(string normalizedAvatarPath)
        {
            var roots = new Dictionary<string, Transform>(StringComparer.OrdinalIgnoreCase);
            var descriptorType = FindType("VRC.SDK3.Avatars.Components.VRCAvatarDescriptor");
            if (descriptorType != null)
            {
                foreach (var descriptor in Resources.FindObjectsOfTypeAll(descriptorType).OfType<Component>().Where(IsSceneObject))
                {
                    AddRootIfMatches(roots, descriptor.transform, normalizedAvatarPath);
                }
            }

            if (roots.Count == 0)
            {
                foreach (var renderer in Resources.FindObjectsOfTypeAll<Renderer>().Where(IsSceneObject))
                {
                    AddRootIfMatches(roots, FindAvatarRoot(renderer.transform), normalizedAvatarPath);
                }
            }

            if (roots.Count == 0 && !string.IsNullOrEmpty(normalizedAvatarPath))
            {
                throw new InvalidOperationException($"Could not locate avatar root: {normalizedAvatarPath}");
            }

            return roots.Values.ToList();
        }

        private static void AddRootIfMatches(Dictionary<string, Transform> roots, Transform root, string normalizedAvatarPath)
        {
            if (root == null)
            {
                return;
            }

            var path = NormalizePath(GetTransformPath(root));
            if (!string.IsNullOrEmpty(normalizedAvatarPath)
                && !string.Equals(path, normalizedAvatarPath, StringComparison.OrdinalIgnoreCase)
                && !path.EndsWith("/" + normalizedAvatarPath, StringComparison.OrdinalIgnoreCase)
                && !root.name.Equals(normalizedAvatarPath, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }

            if (!roots.ContainsKey(path))
            {
                roots.Add(path, root);
            }
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

        private static string DetectCategory(params string[] values)
        {
            var text = string.Join(" ", values.Where(value => !string.IsNullOrWhiteSpace(value))).ToLowerInvariant();

            if (ContainsAny(text, "face", "skin", "body", "head"))
            {
                return "body";
            }

            if (ContainsAny(text, "eye", "iris", "pupil"))
            {
                return "eyes";
            }

            if (ContainsAny(text, "hair", "bang", "twin", "ponytail"))
            {
                return "hair";
            }

            if (ContainsAny(text, "cloth", "clothes", "hoodie", "shirt", "skirt", "dress", "pants", "shoe", "coat", "jacket"))
            {
                return "clothes";
            }

            if (ContainsAny(text, "accessory", "acc", "ring", "glasses", "hat", "bag", "mask"))
            {
                return "accessory";
            }

            if (ContainsAny(text, "physbone", "collider", "spring", "constraint"))
            {
                return "physics";
            }

            return "unknown";
        }

        private static bool IsWardrobeRelated(string category, params string[] values)
        {
            if (category == "clothes" || category == "accessory")
            {
                return true;
            }

            return values.Any(value => WardrobeKeywords.Any(keyword =>
                !string.IsNullOrWhiteSpace(value)
                && value.IndexOf(keyword, StringComparison.OrdinalIgnoreCase) >= 0));
        }

        private static bool ContainsAny(string haystack, params string[] needles)
        {
            return needles.Any(needle => haystack.Contains(needle));
        }

        private static bool IsSceneObject(Component component)
        {
            return component != null
                && component.gameObject.scene.IsValid()
                && component.gameObject.scene.isLoaded
                && !EditorUtility.IsPersistent(component);
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

        private static string GetRelativePath(Transform root, Transform child)
        {
            if (root == child)
            {
                return child.name;
            }

            var childPath = GetTransformPath(child);
            var rootPath = GetTransformPath(root);
            return childPath.StartsWith(rootPath + "/", StringComparison.Ordinal)
                ? childPath.Substring(rootPath.Length + 1)
                : childPath;
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

        private static string StableId(string prefix, string value)
        {
            using (var sha1 = SHA1.Create())
            {
                var bytes = sha1.ComputeHash(Encoding.UTF8.GetBytes(NormalizePath(value)));
                var hex = BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();
                return $"{prefix}_{hex.Substring(0, 16)}";
            }
        }

        private static string WriteJson(string requestedPath, object payload, bool refreshAssets)
        {
            var absolutePath = ResolveToAbsolutePath(requestedPath);
            var directory = Path.GetDirectoryName(absolutePath);
            if (string.IsNullOrEmpty(directory))
            {
                throw new InvalidOperationException($"Cannot resolve parent folder for item scan path: {requestedPath}");
            }

            Directory.CreateDirectory(directory);
            File.WriteAllText(absolutePath, JsonConvert.SerializeObject(payload, Formatting.Indented), Encoding.UTF8);

            if (refreshAssets)
            {
                AssetDatabase.Refresh();
            }

            return absolutePath;
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

        [Serializable]
        private class AvatarItemsPayload
        {
            public string type;
            public string version;
            public string id;
            public string created_at;
            public string unity_project;
            public string requested_avatar_path;
            public List<string> scenes;
            public List<AvatarItem> items;
            public AvatarItemsSummary summary;
            public string outputPath;
            public string absoluteOutputPath;
        }

        [Serializable]
        private class AvatarItemsSummary
        {
            public int avatarCount;
            public int itemCount;
            public int rendererBackedItemCount;
            public int wardrobeCandidateCount;
            public bool truncated;
        }

        [Serializable]
        private class AvatarItem
        {
            public string item_id;
            public string avatar_name;
            public string avatar_path;
            public string object_name;
            public string object_path;
            public string relative_path;
            public bool active_self;
            public bool active_in_hierarchy;
            public int direct_child_count;
            public List<string> component_types;
            public int renderer_count;
            public int direct_renderer_count;
            public int skinned_renderer_count;
            public MeshSummary mesh_summary;
            public MaterialSummary material_summary;
            public string likely_category;
            public bool wardrobe_related;
        }

        [Serializable]
        private class MeshSummary
        {
            public int mesh_count;
            public List<string> mesh_names;
        }

        [Serializable]
        private class MaterialSummary
        {
            public int material_slot_count;
            public List<string> material_names;
            public List<string> shader_names;
        }
    }
}
