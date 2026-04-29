using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace VRCAutoRig.Editor
{
    [McpForUnityTool(
        name: "vrc_export_blendshapes",
        Description = "Export all VRChat avatar blendshapes in the open scenes to JSON for LLM semantic matching."
    )]
    public static class BlendshapeExporter
    {
        public const string ToolName = "vrc_export_blendshapes";
        public const string DefaultOutputPath = "Assets/VRCAutoRig/blendshapes_export.json";

        public class Parameters
        {
            [ToolParameter("Asset-relative or absolute export path.", Required = false)]
            public string outputPath { get; set; } = DefaultOutputPath;

            [ToolParameter("Refresh the Unity AssetDatabase after writing JSON.", Required = false)]
            public bool? refreshAssets { get; set; } = true;
        }

        [MenuItem("VRCAutoRig/Export Blendshapes")]
        public static void ExportFromMenu()
        {
            ExportToDisk(DefaultOutputPath, true);
            Debug.Log($"[{ToolName}] Export complete: {DefaultOutputPath}");
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters();

            try
            {
                var exportResult = ExportToDisk(
                    string.IsNullOrWhiteSpace(parameters.outputPath) ? DefaultOutputPath : parameters.outputPath,
                    parameters.refreshAssets ?? true);

                return new SuccessResponse(
                    $"Exported {exportResult.summary.blendshapeCount} blendshapes from {exportResult.summary.rendererCount} renderers.",
                    new
                    {
                        exportResult.generatedAtUtc,
                        exportResult.summary,
                        exportResult.outputPath,
                        exportResult.absoluteOutputPath
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Blendshape export failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static ExportPayload ExportToDisk(string requestedPath, bool refreshAssets)
        {
            var payload = BuildPayload();
            var absolutePath = ResolveToAbsolutePath(requestedPath);
            var parentDirectory = Path.GetDirectoryName(absolutePath);

            if (string.IsNullOrEmpty(parentDirectory))
            {
                throw new InvalidOperationException($"Cannot resolve parent folder for export path: {requestedPath}");
            }

            Directory.CreateDirectory(parentDirectory);
            File.WriteAllText(absolutePath, JsonConvert.SerializeObject(payload, Formatting.Indented));

            if (refreshAssets)
            {
                AssetDatabase.Refresh();
            }

            payload.outputPath = ToAssetRelativePath(absolutePath);
            payload.absoluteOutputPath = absolutePath.Replace("\\", "/");
            return payload;
        }

        private static ExportPayload BuildPayload()
        {
            var renderers = Resources.FindObjectsOfTypeAll<SkinnedMeshRenderer>();

            var avatarGroups = new Dictionary<string, AvatarExport>();
            var blendshapeCount = 0;
            var rendererCount = 0;
            var sceneNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            foreach (var renderer in renderers.Where(IsSceneObject))
            {
                if (renderer.sharedMesh == null || renderer.sharedMesh.blendShapeCount == 0)
                {
                    continue;
                }

                rendererCount++;
                sceneNames.Add(renderer.gameObject.scene.name);

                var avatarRoot = FindAvatarRoot(renderer.transform);
                var avatarKey = GetTransformPath(avatarRoot);
                if (!avatarGroups.TryGetValue(avatarKey, out var avatarExport))
                {
                    avatarExport = new AvatarExport
                    {
                        avatarName = avatarRoot.name,
                        avatarPath = avatarKey,
                        sceneName = avatarRoot.gameObject.scene.name,
                        scenePath = avatarRoot.gameObject.scene.path,
                        isVrChatAvatar = HasVrChatAvatarDescriptor(avatarRoot)
                    };
                    avatarGroups.Add(avatarKey, avatarExport);
                }

                var rendererExport = new RendererExport
                {
                    rendererName = renderer.name,
                    rendererPath = GetTransformPath(renderer.transform),
                    relativeRendererPath = GetRelativePath(avatarRoot, renderer.transform),
                    meshName = renderer.sharedMesh.name,
                    blendshapeCount = renderer.sharedMesh.blendShapeCount,
                    blendshapes = new List<BlendshapeExport>()
                };

                for (var index = 0; index < renderer.sharedMesh.blendShapeCount; index++)
                {
                    var weight = renderer.GetBlendShapeWeight(index);
                    rendererExport.blendshapes.Add(new BlendshapeExport
                    {
                        index = index,
                        name = renderer.sharedMesh.GetBlendShapeName(index),
                        currentWeight = weight,
                        normalizedWeight = Mathf.Clamp01(weight / 100f)
                    });
                    blendshapeCount++;
                }

                avatarExport.renderers.Add(rendererExport);
            }

            return new ExportPayload
            {
                generatedAtUtc = DateTime.UtcNow.ToString("O"),
                unityProject = Directory.GetParent(Application.dataPath)?.Name ?? "UnknownProject",
                scenes = sceneNames.OrderBy(name => name).ToList(),
                avatars = avatarGroups.Values.OrderBy(item => item.avatarPath).ToList(),
                summary = new ExportSummary
                {
                    avatarCount = avatarGroups.Count,
                    rendererCount = rendererCount,
                    blendshapeCount = blendshapeCount
                }
            };
        }

        private static bool IsSceneObject(SkinnedMeshRenderer renderer)
        {
            return renderer != null
                && renderer.gameObject.scene.IsValid()
                && renderer.gameObject.scene.isLoaded
                && !EditorUtility.IsPersistent(renderer);
        }

        private static Transform FindAvatarRoot(Transform source)
        {
            var current = source;
            Transform fallback = source.root;

            while (current != null)
            {
                if (HasVrChatAvatarDescriptor(current))
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

        private static bool HasVrChatAvatarDescriptor(Transform transform)
        {
            var descriptorType = FindType("VRC.SDK3.Avatars.Components.VRCAvatarDescriptor");
            return descriptorType != null && transform.GetComponent(descriptorType) != null;
        }

        private static Type FindType(string fullName)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    var match = assembly.GetType(fullName, false);
                    if (match != null)
                    {
                        return match;
                    }
                }
                catch
                {
                    // Ignore transient reflection failures from editor reloads.
                }
            }

            return null;
        }

        private static string ResolveToAbsolutePath(string requestedPath)
        {
            if (Path.IsPathRooted(requestedPath))
            {
                return requestedPath.Replace("\\", "/");
            }

            var projectRoot = Directory.GetParent(Application.dataPath)?.FullName
                ?? throw new InvalidOperationException("Cannot determine Unity project root.");

            if (requestedPath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase)
                || string.Equals(requestedPath, "Assets", StringComparison.OrdinalIgnoreCase))
            {
                return Path.Combine(projectRoot, requestedPath).Replace("\\", "/");
            }

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

        [Serializable]
        private class ExportPayload
        {
            public string generatedAtUtc;
            public string unityProject;
            public List<string> scenes;
            public List<AvatarExport> avatars;
            public ExportSummary summary;
            public string outputPath;
            public string absoluteOutputPath;
        }

        [Serializable]
        private class ExportSummary
        {
            public int avatarCount;
            public int rendererCount;
            public int blendshapeCount;
        }

        [Serializable]
        private class AvatarExport
        {
            public string avatarName;
            public string avatarPath;
            public string sceneName;
            public string scenePath;
            public bool isVrChatAvatar;
            public List<RendererExport> renderers = new List<RendererExport>();
        }

        [Serializable]
        private class RendererExport
        {
            public string rendererName;
            public string rendererPath;
            public string relativeRendererPath;
            public string meshName;
            public int blendshapeCount;
            public List<BlendshapeExport> blendshapes;
        }

        [Serializable]
        private class BlendshapeExport
        {
            public int index;
            public string name;
            public float currentWeight;
            public float normalizedWeight;
        }
    }
}
