using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_setup_outfit",
        Description = "Validate or run Modular Avatar Setup Outfit on an outfit object parented under a VRChat avatar."
    )]
    public static class SetupOutfitTool
    {
        public const string ToolName = "vrc_setup_outfit";

        private const string SetupOutfitTypeName =
            "nadena.dev.modular_avatar.core.editor.SetupOutfit";
        private const string SetupOutfitErrorWindowTypeName =
            "nadena.dev.modular_avatar.core.editor.ESOErrorWindow";
        private static readonly object JobLock = new object();
        private static readonly Dictionary<string, SetupOutfitJob> Jobs = new Dictionary<string, SetupOutfitJob>();
        private static readonly TimeSpan CompletedJobRetention = TimeSpan.FromMinutes(10);

        private sealed class SetupOutfitJob
        {
            public string jobId { get; set; } = "";
            public string avatarPath { get; set; } = "";
            public string outfitPath { get; set; } = "";
            public bool saveScene { get; set; }
            public string status { get; set; } = "pending";
            public JObject result { get; set; }
            public DateTime createdUtc { get; set; } = DateTime.UtcNow;
            public DateTime? startedUtc { get; set; }
            public DateTime? completedUtc { get; set; }
        }

        public class SetupOutfitParameters
        {
            [ToolParameter("Avatar root hierarchy path or avatar name.", Required = false)]
            public string avatarPath { get; set; } = "";

            [ToolParameter("Hierarchy path of the outfit object under the avatar root.", Required = true)]
            public string outfitPath { get; set; } = "";

            [ToolParameter("Must be true to actually run Setup Outfit. False returns a readiness preview.", Required = false)]
            public bool? confirmSetup { get; set; } = false;

            [ToolParameter("Save the target outfit scene after a confirmed setup.", Required = false)]
            public bool? saveScene { get; set; } = true;

            [ToolParameter("Existing Setup Outfit job id to poll.", Required = false)]
            public string jobId { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<SetupOutfitParameters>()
                ?? new SetupOutfitParameters();

            try
            {
                var payload = string.IsNullOrWhiteSpace(parameters.jobId)
                    ? PreviewOrSetup(parameters)
                    : PollJob(parameters.jobId);
                var action = DescribeAction(payload);
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
            var mergeArmatureType = FindType("nadena.dev.modular_avatar.core.ModularAvatarMergeArmature");
            if (mergeArmatureType == null)
            {
                throw new InvalidOperationException(
                    "Modular Avatar runtime types were not found. Install the Modular Avatar package first.");
            }

            var payload = BuildPreviewPayload(parameters, mergeArmatureType, out _, out _);
            if (parameters.confirmSetup != true)
            {
                return payload;
            }

            return StartSetupJob(parameters, payload);
        }

        private static JObject StartSetupJob(SetupOutfitParameters parameters, JObject previewPayload)
        {
            PruneCompletedJobs();

            var jobId = Guid.NewGuid().ToString("N");
            var job = new SetupOutfitJob
            {
                jobId = jobId,
                avatarPath = previewPayload["avatarPath"]?.ToString() ?? parameters.avatarPath ?? "",
                outfitPath = previewPayload["outfitPath"]?.ToString() ?? parameters.outfitPath ?? "",
                saveScene = parameters.saveScene != false,
            };

            lock (JobLock)
            {
                Jobs[jobId] = job;
            }

            EditorApplication.delayCall += () => RunSetupJob(jobId);

            var payload = (JObject)previewPayload.DeepClone();
            payload["confirmed"] = true;
            payload["pending"] = true;
            payload["jobId"] = jobId;
            payload["status"] = "pending";
            payload["createdUtc"] = job.createdUtc.ToString("o");
            payload["sceneSaved"] = false;
            return payload;
        }

        private static void RunSetupJob(string jobId)
        {
            SetupOutfitJob job;
            lock (JobLock)
            {
                if (!Jobs.TryGetValue(jobId, out job))
                {
                    return;
                }
                if (job.status != "pending")
                {
                    return;
                }
                job.status = "running";
                job.startedUtc = DateTime.UtcNow;
            }

            try
            {
                var payload = ExecuteConfirmedSetup(job);
                payload["ok"] = true;
                payload["pending"] = false;
                payload["status"] = "completed";
                payload["jobId"] = jobId;
                CompleteJob(jobId, "completed", payload);
            }
            catch (Exception ex)
            {
                var payload = new JObject
                {
                    ["ok"] = false,
                    ["confirmed"] = true,
                    ["pending"] = false,
                    ["status"] = "error",
                    ["jobId"] = jobId,
                    ["avatarPath"] = job.avatarPath,
                    ["outfitPath"] = job.outfitPath,
                    ["error"] = ex.Message,
                    ["stackTrace"] = ex.StackTrace ?? "",
                };
                CompleteJob(jobId, "error", payload);
            }
        }

        private static JObject ExecuteConfirmedSetup(SetupOutfitJob job)
        {
            var parameters = new SetupOutfitParameters
            {
                avatarPath = job.avatarPath,
                outfitPath = job.outfitPath,
                confirmSetup = true,
                saveScene = job.saveScene,
            };

            var mergeArmatureType = FindType("nadena.dev.modular_avatar.core.ModularAvatarMergeArmature")
                ?? throw new InvalidOperationException(
                    "Modular Avatar runtime types were not found. Install the Modular Avatar package first.");
            var payload = BuildPreviewPayload(parameters, mergeArmatureType, out var outfit, out var existingMerge);
            var setupEntryPoint = ExecuteSetupOutfit(outfit.gameObject);

            var mergeAfter = outfit.GetComponentsInChildren(mergeArmatureType, true).Length;
            var componentTypes = outfit.GetComponentsInChildren<Component>(true)
                .Where(component => component != null)
                .Select(component => component.GetType().Name)
                .Where(name => name.StartsWith("ModularAvatar", StringComparison.Ordinal))
                .GroupBy(name => name)
                .ToDictionary(group => group.Key, group => group.Count());

            EditorSceneManager.MarkSceneDirty(outfit.gameObject.scene);
            var sceneSaved = parameters.saveScene != false && SaveTargetScene(outfit.gameObject.scene);

            payload["confirmed"] = true;
            payload["setupEntryPoint"] = setupEntryPoint;
            payload["mergeArmaturesBefore"] = existingMerge;
            payload["mergeArmaturesAfter"] = mergeAfter;
            payload["modularAvatarComponents"] = JObject.FromObject(componentTypes);
            payload["sceneSaved"] = sceneSaved;
            if (mergeAfter <= existingMerge && existingMerge == 0)
            {
                throw new InvalidOperationException(
                    "Modular Avatar Setup Outfit returned without adding a MergeArmature. " +
                    "The outfit armature could not be matched to the avatar.");
            }

            return payload;
        }

        private static JObject BuildPreviewPayload(
            SetupOutfitParameters parameters,
            Type mergeArmatureType,
            out Transform outfit,
            out int existingMerge)
        {
            var warnings = new List<string>();
            var descriptor = ResolveAvatarDescriptor(parameters.avatarPath ?? "");
            var avatarRoot = descriptor.transform;
            outfit = ResolveOutfitTransform(avatarRoot, parameters.outfitPath ?? "");
            if (outfit == avatarRoot)
            {
                throw new InvalidOperationException("outfitPath must point to an outfit object, not the avatar root.");
            }

            existingMerge = outfit.GetComponentsInChildren(mergeArmatureType, true).Length;
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

            return new JObject
            {
                ["confirmed"] = false,
                ["pending"] = false,
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
        }

        private static bool SaveTargetScene(Scene scene)
        {
            if (!scene.IsValid())
            {
                throw new InvalidOperationException("Outfit target scene is not valid; cannot save it.");
            }
            if (string.IsNullOrWhiteSpace(scene.path))
            {
                throw new InvalidOperationException(
                    "Outfit target scene has not been saved to disk; save the scene once before running Setup Outfit with saveScene=true.");
            }
            if (!EditorSceneManager.SaveScene(scene))
            {
                throw new InvalidOperationException($"Could not save target scene: {scene.path}");
            }
            return true;
        }

        private static void CompleteJob(string jobId, string status, JObject payload)
        {
            lock (JobLock)
            {
                if (!Jobs.TryGetValue(jobId, out var job))
                {
                    return;
                }
                job.status = status;
                job.completedUtc = DateTime.UtcNow;
                payload["completedUtc"] = job.completedUtc.Value.ToString("o");
                if (job.startedUtc.HasValue)
                {
                    payload["startedUtc"] = job.startedUtc.Value.ToString("o");
                }
                payload["createdUtc"] = job.createdUtc.ToString("o");
                job.result = payload;
            }
        }

        private static JObject PollJob(string jobId)
        {
            PruneCompletedJobs();

            lock (JobLock)
            {
                if (!Jobs.TryGetValue(jobId, out var job))
                {
                    return new JObject
                    {
                        ["ok"] = false,
                        ["pending"] = false,
                        ["status"] = "error",
                        ["jobId"] = jobId,
                        ["outfitPath"] = "",
                        ["error"] = $"Setup Outfit job was not found: {jobId}",
                    };
                }

                if (job.result != null)
                {
                    return (JObject)job.result.DeepClone();
                }

                var payload = new JObject
                {
                    ["ok"] = true,
                    ["confirmed"] = true,
                    ["pending"] = true,
                    ["status"] = job.status,
                    ["jobId"] = job.jobId,
                    ["avatarPath"] = job.avatarPath,
                    ["outfitPath"] = job.outfitPath,
                    ["createdUtc"] = job.createdUtc.ToString("o"),
                };
                if (job.startedUtc.HasValue)
                {
                    payload["startedUtc"] = job.startedUtc.Value.ToString("o");
                }
                return payload;
            }
        }

        private static void PruneCompletedJobs()
        {
            var cutoff = DateTime.UtcNow - CompletedJobRetention;
            lock (JobLock)
            {
                var stale = Jobs
                    .Where(pair => pair.Value.completedUtc.HasValue && pair.Value.completedUtc.Value < cutoff)
                    .Select(pair => pair.Key)
                    .ToList();
                foreach (var jobId in stale)
                {
                    Jobs.Remove(jobId);
                }
            }
        }

        private static string DescribeAction(JObject payload)
        {
            var status = payload["status"]?.ToString();
            if (!string.IsNullOrWhiteSpace(payload["jobId"]?.ToString()))
            {
                return $"Job {status ?? "status"}";
            }
            return payload["confirmed"]?.Value<bool>() == true ? "Ran" : "Previewed";
        }

        private static string ExecuteSetupOutfit(GameObject outfit)
        {
            var setupType = FindType(SetupOutfitTypeName)
                ?? throw new InvalidOperationException(
                    $"Modular Avatar Setup Outfit API was not found: {SetupOutfitTypeName}.");
            var method = setupType.GetMethod(
                "SetupOutfitUI",
                BindingFlags.Public | BindingFlags.Static,
                null,
                new[] { typeof(GameObject) },
                null);
            if (method == null)
            {
                throw new InvalidOperationException(
                    "Modular Avatar SetupOutfit.SetupOutfitUI(GameObject) is unavailable in the installed version.");
            }

            // The menu item depends on MenuCommand.context. Invoke MA's public
            // API directly with the resolved outfit instead of losing context
            // through EditorApplication.ExecuteMenuItem.
            var errorWindowType = FindType(SetupOutfitErrorWindowTypeName);
            var suppressField = errorWindowType?.GetField(
                "Suppress",
                BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Static);
            var previousSuppress = suppressField != null && (bool)suppressField.GetValue(null);
            try
            {
                suppressField?.SetValue(null, true);
                method.Invoke(null, new object[] { outfit });
            }
            catch (TargetInvocationException ex)
            {
                throw new InvalidOperationException(
                    $"Modular Avatar Setup Outfit failed: {(ex.InnerException ?? ex).Message}",
                    ex.InnerException ?? ex);
            }
            finally
            {
                suppressField?.SetValue(null, previousSuppress);
            }
            return SetupOutfitTypeName + ".SetupOutfitUI";
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
