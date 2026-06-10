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
        name: "vrc_setup_outfit",
        Description = "Validate or run Modular Avatar Setup Outfit on an outfit object parented under a VRChat avatar."
    )]
    public static class SetupOutfitTool
    {
        public const string ToolName = "vrc_setup_outfit";

        private static readonly string[] SetupOutfitMenuPaths =
        {
            "GameObject/Modular Avatar/Setup Outfit",
            "GameObject/ModularAvatar/Setup Outfit",
        };

        public class SetupOutfitParameters
        {
            [ToolParameter("Avatar root hierarchy path or avatar name.", Required = false)]
            public string avatarPath { get; set; } = "";

            [ToolParameter("Hierarchy path of the outfit object under the avatar root.", Required = true)]
            public string outfitPath { get; set; } = "";

            [ToolParameter("Must be true to actually run Setup Outfit. False returns a readiness preview.", Required = false)]
            public bool? confirmSetup { get; set; } = false;

            [ToolParameter("Save open scenes after a confirmed setup.", Required = false)]
            public bool? saveScene { get; set; } = true;
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<SetupOutfitParameters>()
                ?? new SetupOutfitParameters();

            try
            {
                var payload = PreviewOrSetup(parameters);
                var action = (bool)payload["confirmed"] ? "Ran" : "Previewed";
                return new SuccessResponse(
                    $"{action} Modular Avatar Setup Outfit for '{payload["outfitPath"]}'.",
                    payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Setup Outfit failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static JObject PreviewOrSetup(SetupOutfitParameters parameters)
        {
            var warnings = new List<string>();
            var mergeArmatureType = FindType("nadena.dev.modular_avatar.core.ModularAvatarMergeArmature");
            if (mergeArmatureType == null)
            {
                throw new InvalidOperationException(
                    "Modular Avatar runtime types were not found. Install the Modular Avatar package first.");
            }

            var descriptor = ResolveAvatarDescriptor(parameters.avatarPath ?? "");
            var avatarRoot = descriptor.transform;
            var outfit = ResolveOutfitTransform(avatarRoot, parameters.outfitPath ?? "");
            if (outfit == avatarRoot)
            {
                throw new InvalidOperationException("outfitPath must point to an outfit object, not the avatar root.");
            }

            var existingMerge = outfit.GetComponentsInChildren(mergeArmatureType, true).Length;
            if (existingMerge > 0)
            {
                warnings.Add($"Outfit already contains {existingMerge} ModularAvatarMergeArmature component(s); it may already be set up.");
            }

            var hasAnimator = outfit.GetComponentsInChildren<Animator>(true).Length > 0;
            var hasSkinnedMesh = outfit.GetComponentsInChildren<SkinnedMeshRenderer>(true).Length > 0;
            if (!hasSkinnedMesh)
            {
                warnings.Add("Outfit has no SkinnedMeshRenderer; Setup Outfit may have nothing to merge.");
            }

            var payload = new JObject
            {
                ["confirmed"] = false,
                ["avatarPath"] = GetTransformPath(avatarRoot),
                ["avatarName"] = descriptor.name,
                ["outfitPath"] = GetTransformPath(outfit),
                ["modularAvatarFound"] = true,
                ["existingMergeArmatures"] = existingMerge,
                ["outfitHasAnimator"] = hasAnimator,
                ["outfitHasSkinnedMesh"] = hasSkinnedMesh,
                ["ready"] = hasSkinnedMesh,
                ["warnings"] = new JArray(warnings),
            };

            if (parameters.confirmSetup != true)
            {
                return payload;
            }

            Selection.activeGameObject = outfit.gameObject;
            string executedMenuPath = null;
            foreach (var menuPath in SetupOutfitMenuPaths)
            {
                if (EditorApplication.ExecuteMenuItem(menuPath))
                {
                    executedMenuPath = menuPath;
                    break;
                }
            }

            if (executedMenuPath == null)
            {
                throw new InvalidOperationException(
                    "Modular Avatar Setup Outfit menu item could not be executed. " +
                    "Check the installed Modular Avatar version and its GameObject menu.");
            }

            var mergeAfter = outfit.GetComponentsInChildren(mergeArmatureType, true).Length;
            var componentTypes = outfit.GetComponentsInChildren<Component>(true)
                .Where(component => component != null)
                .Select(component => component.GetType().Name)
                .Where(name => name.StartsWith("ModularAvatar", StringComparison.Ordinal))
                .GroupBy(name => name)
                .ToDictionary(group => group.Key, group => group.Count());

            EditorSceneManager.MarkSceneDirty(outfit.gameObject.scene);
            if (parameters.saveScene != false)
            {
                EditorSceneManager.SaveOpenScenes();
            }

            payload["confirmed"] = true;
            payload["menuPath"] = executedMenuPath;
            payload["mergeArmaturesBefore"] = existingMerge;
            payload["mergeArmaturesAfter"] = mergeAfter;
            payload["modularAvatarComponents"] = JObject.FromObject(componentTypes);
            payload["sceneSaved"] = parameters.saveScene != false;
            if (mergeAfter <= existingMerge)
            {
                var moreWarnings = (JArray)payload["warnings"];
                moreWarnings.Add("No new ModularAvatarMergeArmature component was detected after Setup Outfit.");
            }

            return payload;
        }

        private static Component ResolveAvatarDescriptor(string avatarPath)
        {
            var descriptorType = FindType("VRC.SDK3.Avatars.Components.VRCAvatarDescriptor")
                ?? throw new InvalidOperationException("VRC SDK avatar descriptor type was not found.");
            var descriptors = Resources.FindObjectsOfTypeAll(descriptorType)
                .OfType<Component>()
                .Where(IsSceneComponent)
                .OrderBy(item => item.name)
                .ToList();
            if (descriptors.Count == 0)
            {
                throw new InvalidOperationException("No scene VRChat avatar descriptor was found.");
            }

            var normalized = NormalizePath(avatarPath);
            if (string.IsNullOrEmpty(normalized))
            {
                return descriptors[0];
            }

            var match = descriptors.FirstOrDefault(item => NormalizePath(GetTransformPath(item.transform)) == normalized)
                ?? descriptors.FirstOrDefault(item => item.name.Equals(avatarPath, StringComparison.OrdinalIgnoreCase));
            if (match == null)
            {
                throw new InvalidOperationException($"Avatar descriptor not found: {avatarPath}");
            }

            return match;
        }

        private static Transform ResolveOutfitTransform(Transform avatarRoot, string outfitPath)
        {
            var normalized = NormalizePath(outfitPath);
            if (string.IsNullOrEmpty(normalized))
            {
                throw new InvalidOperationException("outfitPath is required.");
            }

            var avatarPrefix = NormalizePath(GetTransformPath(avatarRoot));
            var relative = normalized;
            if (normalized.StartsWith(avatarPrefix + "/", StringComparison.OrdinalIgnoreCase))
            {
                relative = normalized.Substring(avatarPrefix.Length + 1);
            }

            var found = avatarRoot.Find(relative);
            if (found == null)
            {
                found = avatarRoot.GetComponentsInChildren<Transform>(true)
                    .FirstOrDefault(item => NormalizePath(GetTransformPath(item)) == normalized
                        || item.name.Equals(outfitPath, StringComparison.OrdinalIgnoreCase));
            }

            if (found == null)
            {
                throw new InvalidOperationException($"Outfit object was not found under the avatar: {outfitPath}");
            }

            return found;
        }

        private static bool IsSceneComponent(Component component)
        {
            return component != null
                && component.gameObject != null
                && component.gameObject.scene.IsValid()
                && !EditorUtility.IsPersistent(component.gameObject);
        }

        private static string GetTransformPath(Transform transform)
        {
            if (transform == null)
            {
                return string.Empty;
            }

            var segments = new List<string>();
            var current = transform;
            while (current != null)
            {
                segments.Insert(0, current.name);
                current = current.parent;
            }

            return string.Join("/", segments);
        }

        private static string NormalizePath(string path)
        {
            return (path ?? string.Empty).Trim().Trim('/').Replace("\\", "/");
        }

        private static Type FindType(string fullName)
        {
            return AppDomain.CurrentDomain.GetAssemblies()
                .Select(assembly =>
                {
                    try
                    {
                        return assembly.GetType(fullName, false);
                    }
                    catch
                    {
                        return null;
                    }
                })
                .FirstOrDefault(type => type != null);
        }
    }
}
