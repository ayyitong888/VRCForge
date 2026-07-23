using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
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
        name: "vrc_inspect_primitive_basis_fixture",
        Description = "Read the fixed primitive-basis fixture identity, active scene, ready marker, and project binding without writing."
    )]
    public static class PrimitiveBasisFixtureInspector
    {
        public const string ToolName = "vrc_inspect_primitive_basis_fixture";
        private const string ScenarioId = "model_part_composition";
        private const string PrimitiveId = "non_destructive_part_composition";
        private const string ScenePath = "Assets/VRCForge/PrimitiveBasis/model_part_composition/ModelPartComposition.unity";
        private const string ContractPath = "Assets/VRCForge/PrimitiveBasis/model_part_composition/fixture-contract.json";
        private const string BaselinePath = "Assets/VRCForge/PrimitiveBasis/model_part_composition/baseline.json";
        private const string ReadyMarkerPath = "Library/VRCForge/primitive-basis-model-part-ready.json";

        public static object HandleCommand(JObject parameters)
        {
            try
            {
                var expectedRunIdDigest = (parameters?["expectedRunIdDigest"]?.ToString() ?? string.Empty).Trim();
                if (!PrimitiveBasisLiveGuard.IsSha256(expectedRunIdDigest))
                {
                    return new ErrorResponse("expectedRunIdDigest must be a lowercase SHA-256 digest.");
                }
                var processIdentity = PrimitiveBasisLiveGuard.InspectBootstrap(expectedRunIdDigest);

                var projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
                var sceneFullPath = RequireProjectFile(projectRoot, ScenePath);
                var contractFullPath = RequireProjectFile(projectRoot, ContractPath);
                var baselineFullPath = RequireProjectFile(projectRoot, BaselinePath);
                var markerFullPath = RequireProjectFile(projectRoot, ReadyMarkerPath);
                var marker = JObject.Parse(File.ReadAllText(markerFullPath, Encoding.UTF8));
                var readyRunIdDigest = (marker["runIdDigest"]?.ToString() ?? string.Empty).Trim();
                if (!string.Equals(readyRunIdDigest, expectedRunIdDigest, StringComparison.Ordinal))
                {
                    return new ErrorResponse("The fixture ready marker belongs to another live run.");
                }

                var activeScene = UnityEngine.SceneManagement.SceneManager.GetActiveScene();
                var activeScenePath = (activeScene.path ?? string.Empty).Replace("\\", "/");
                var activeSceneGuid = AssetDatabase.AssetPathToGUID(activeScenePath);
                if (!activeScene.IsValid()
                    || !activeScene.isLoaded
                    || activeScene.isDirty
                    || !string.Equals(activeScenePath, ScenePath, StringComparison.Ordinal)
                    || string.IsNullOrWhiteSpace(activeSceneGuid))
                {
                    return new ErrorResponse("The fixed primitive-basis scene is not the active loaded scene.");
                }
                var hierarchy = RequireFixedHierarchy(activeScene);

                return new SuccessResponse(
                    "Inspected the fixed primitive-basis fixture without writing.",
                    new
                    {
                        ok = true,
                        schema = "vrcforge.primitive_basis_unity_fixture.v1",
                        scenarioId = ScenarioId,
                        primitiveId = PrimitiveId,
                        projectPathDigest = processIdentity.ProjectPathDigest,
                        unityProcessId = processIdentity.ProcessId,
                        unityProcessStartedAtUtc = processIdentity.StartedAtUtc,
                        unityExecutableDigest = processIdentity.ExecutableDigest,
                        unityVersion = Application.unityVersion,
                        batchMode = Application.isBatchMode,
                        sceneDirty = false,
                        activeScenePath,
                        activeSceneGuid,
                        readyMarkerDigest = ComputeSha256(markerFullPath),
                        readyRunIdDigest,
                        contractDigest = ComputeSha256(contractFullPath),
                        baselineManifestDigest = ComputeSha256(baselineFullPath),
                        sceneDigest = ComputeSha256(sceneFullPath),
                        avatarRootType = hierarchy.AvatarRootType,
                        transformPaths = hierarchy.TransformPaths,
                        rendererPath = hierarchy.RendererPath,
                        rendererRootBonePath = hierarchy.RendererRootBonePath,
                        rendererBonePaths = hierarchy.RendererBonePaths,
                        componentHostPath = marker["componentHostPath"]?.ToString() ?? string.Empty,
                        mergeTargetPath = marker["mergeTargetPath"]?.ToString() ?? string.Empty
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Primitive-basis fixture inspection failed: {ex.Message}");
            }
        }

        internal static object HandleReloadCommand(JObject parameters)
        {
            try
            {
                var identity = PrimitiveBasisLiveGuard.RequireBoundRequest(parameters);
                if (identity == null)
                {
                    return new ErrorResponse("The fixed live transport binding is required.");
                }
                var activeScene = SceneManager.GetActiveScene();
                if (!activeScene.IsValid()
                    || !activeScene.isLoaded
                    || activeScene.isDirty
                    || !string.Equals((activeScene.path ?? string.Empty).Replace("\\", "/"), ScenePath, StringComparison.Ordinal))
                {
                    return new ErrorResponse("The fixed primitive-basis scene cannot be reloaded from its current state.");
                }
                var reloaded = EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
                if (!reloaded.IsValid() || !reloaded.isLoaded || reloaded.isDirty)
                {
                    return new ErrorResponse("The fixed primitive-basis scene did not reload cleanly.");
                }
                RequireFixedHierarchy(reloaded);
                return new SuccessResponse(
                    "Reloaded the fixed primitive-basis scene from its saved bytes.",
                    new
                    {
                        ok = true,
                        schema = "vrcforge.primitive_basis_scene_reload.v1",
                        reloaded = true,
                        sceneDirty = false,
                        scenePath = ScenePath,
                        unityProcessId = identity.ProcessId,
                        unityProcessStartedAtUtc = identity.StartedAtUtc,
                        unityExecutableDigest = identity.ExecutableDigest,
                        projectPathDigest = identity.ProjectPathDigest
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Primitive-basis fixture reload failed: {ex.Message}");
            }
        }

        private sealed class HierarchyIdentity
        {
            internal string AvatarRootType;
            internal string[] TransformPaths;
            internal string RendererPath;
            internal string RendererRootBonePath;
            internal string[] RendererBonePaths;
        }

        private static HierarchyIdentity RequireFixedHierarchy(Scene activeScene)
        {
            var roots = activeScene.GetRootGameObjects();
            if (roots.Length != 1 || !string.Equals(roots[0].name, "FixtureAvatar", StringComparison.Ordinal))
            {
                throw new InvalidOperationException("The fixed fixture root hierarchy changed.");
            }
            var avatar = roots[0];
            var rootType = FindType("nadena.dev.ndmf.runtime.components.NDMFAvatarRoot");
            if (rootType == null || avatar.GetComponent(rootType) == null)
            {
                throw new InvalidOperationException("The fixed fixture avatar root marker is missing.");
            }
            var transforms = avatar.GetComponentsInChildren<Transform>(true)
                .Select(item => TransformPath(item))
                .OrderBy(item => item, StringComparer.Ordinal)
                .ToArray();
            var expectedTransforms = new[]
            {
                "FixtureAvatar",
                "FixtureAvatar/Armature",
                "FixtureAvatar/Armature/Hips",
                "FixtureAvatar/Part",
                "FixtureAvatar/Part/Armature",
                "FixtureAvatar/Part/Armature/Hips",
                "FixtureAvatar/Part/RendererProbe"
            }.OrderBy(item => item, StringComparer.Ordinal).ToArray();
            if (!transforms.SequenceEqual(expectedTransforms, StringComparer.Ordinal))
            {
                throw new InvalidOperationException("The fixed fixture transform hierarchy changed.");
            }
            var rendererTransform = avatar.transform.Find("Part/RendererProbe");
            var renderer = rendererTransform != null ? rendererTransform.GetComponent<SkinnedMeshRenderer>() : null;
            if (renderer == null
                || renderer.rootBone == null
                || !string.Equals(TransformPath(renderer.rootBone), "FixtureAvatar/Part/Armature/Hips", StringComparison.Ordinal)
                || renderer.bones == null
                || renderer.bones.Length != 1
                || renderer.bones[0] == null
                || !string.Equals(TransformPath(renderer.bones[0]), "FixtureAvatar/Part/Armature/Hips", StringComparison.Ordinal))
            {
                throw new InvalidOperationException("The fixed fixture renderer binding changed.");
            }
            return new HierarchyIdentity
            {
                AvatarRootType = rootType.FullName,
                TransformPaths = transforms,
                RendererPath = TransformPath(rendererTransform),
                RendererRootBonePath = TransformPath(renderer.rootBone),
                RendererBonePaths = renderer.bones.Select(TransformPath).ToArray()
            };
        }

        private static string TransformPath(Transform transform)
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

        private static Type FindType(string fullName)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                var type = assembly.GetType(fullName, false);
                if (type != null) { return type; }
            }
            return null;
        }

        private static string RequireProjectFile(string projectRoot, string relativePath)
        {
            var candidate = Path.GetFullPath(Path.Combine(projectRoot, relativePath.Replace('/', Path.DirectorySeparatorChar)));
            var normalizedRoot = projectRoot.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                + Path.DirectorySeparatorChar;
            if (!candidate.StartsWith(normalizedRoot, StringComparison.OrdinalIgnoreCase) || !File.Exists(candidate))
            {
                throw new InvalidOperationException("A required fixed fixture file is missing.");
            }
            return candidate;
        }

        private static string ComputeSha256(string path)
        {
            using (var sha256 = SHA256.Create())
            using (var stream = File.OpenRead(path))
            {
                return Hex(sha256.ComputeHash(stream));
            }
        }

        private static string Hex(byte[] bytes)
        {
            var builder = new StringBuilder(bytes.Length * 2);
            foreach (var item in bytes) { builder.Append(item.ToString("x2")); }
            return builder.ToString();
        }
    }

    [McpForUnityTool(
        name: "vrc_reload_primitive_basis_fixture",
        Description = "Reload the fixed live primitive-basis scene from its saved bytes after validating the one-shot process binding."
    )]
    public static class PrimitiveBasisFixtureReloader
    {
        public const string ToolName = "vrc_reload_primitive_basis_fixture";

        public static object HandleCommand(JObject parameters)
        {
            return PrimitiveBasisFixtureInspector.HandleReloadCommand(parameters);
        }
    }
}
