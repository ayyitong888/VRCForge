using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace VRCForge.PrimitiveBasisFixtures
{
    [InitializeOnLoad]
    public static class ComponentFeatureApplicationFixtureBootstrap
    {
        public const string GeneratedRoot =
            "Assets/VRCForge/PrimitiveBasis/RuntimeComponentFeatureApplication";
        public const string ScenePath = GeneratedRoot + "/ComponentFeatureApplication.unity";
        public const string RunIdEnvironment =
            "VRCFORGE_PRIMITIVE_COMPONENT_FEATURE_APPLICATION_RUN_ID";
        public const string ReadyMarkerPath =
            "Library/VRCForge/primitive-basis-component-feature-application-ready.json";

        private static readonly string LiveRunId =
            Environment.GetEnvironmentVariable(RunIdEnvironment) ?? string.Empty;

        static ComponentFeatureApplicationFixtureBootstrap()
        {
            if (Application.isBatchMode || string.IsNullOrWhiteSpace(LiveRunId))
            {
                return;
            }

            EditorApplication.delayCall += BuildPinnedFixtureForLiveRun;
        }

        private static void BuildPinnedFixtureForLiveRun()
        {
            if (EditorApplication.isCompiling || EditorApplication.isUpdating)
            {
                EditorApplication.delayCall += BuildPinnedFixtureForLiveRun;
                return;
            }

            try
            {
                RequireGeneratedRootAbsent();
                var createdGuid = AssetDatabase.CreateFolder(
                    "Assets/VRCForge/PrimitiveBasis",
                    "RuntimeComponentFeatureApplication");
                if (string.IsNullOrWhiteSpace(createdGuid))
                {
                    throw new InvalidOperationException("Fixture runtime root could not be created.");
                }

                var scene = EditorSceneManager.NewScene(
                    NewSceneSetup.EmptyScene,
                    NewSceneMode.Single);
                var avatar = new GameObject("Avatar");
                var featureHost = AddChild(avatar.transform, "FeatureHost");
                var hat = AddChild(avatar.transform, "Hat");
                AddChild(hat.transform, "Charm");
                var armatureFeatureHost = AddChild(avatar.transform, "ArmatureFeatureHost");
                AddChild(avatar.transform, "PropRoot");
                AddChild(avatar.transform, "ChestTarget");
                var armature = AddChild(avatar.transform, "Armature");
                var hips = AddChild(armature.transform, "Hips");
                AddChild(hips.transform, "Spine");

                SceneManager.SetActiveScene(scene);
                if (!EditorSceneManager.SaveScene(scene, ScenePath))
                {
                    throw new InvalidOperationException("Fixture scene could not be saved.");
                }

                AssetDatabase.ImportAsset(
                    ScenePath,
                    ImportAssetOptions.ForceSynchronousImport |
                    ImportAssetOptions.ForceUpdate);
                AssetDatabase.SaveAssets();
                if (scene.isDirty)
                {
                    throw new InvalidOperationException("Fixture scene remained dirty after save.");
                }

                var featureComponentCount = featureHost
                    .GetComponents<Component>()
                    .Count(component =>
                        component != null &&
                        component.GetType().FullName == "VF.Model.VRCFury");
                var armatureComponentCount = armatureFeatureHost
                    .GetComponents<Component>()
                    .Count(component =>
                        component != null &&
                        component.GetType().FullName == "VF.Model.VRCFury");
                if (featureComponentCount != 0 || armatureComponentCount != 0)
                {
                    throw new InvalidOperationException(
                        "Fixture baseline unexpectedly contains feature components.");
                }

                WriteReadyMarker(new ReadyMarker
                {
                    schema = "vrcforge.primitive_basis_fixture_ready.v1",
                    scenarioId = "component_feature_application",
                    runIdDigest = Sha256Hex(LiveRunId),
                    sceneGuid = RequireAssetGuid(ScenePath),
                    scenePath = ScenePath,
                    avatarPath = "Avatar",
                    featureHostPath = "Avatar/FeatureHost",
                    armatureFeatureHostPath = "Avatar/ArmatureFeatureHost",
                    targetPaths = new[]
                    {
                        "Avatar/PropRoot",
                        "Avatar/ChestTarget"
                    },
                    baselineFeatureComponentCount = 0
                });
                Debug.Log("[VRCForge Fixture] Component feature fixture is ready.");
            }
            catch (Exception exception)
            {
                Debug.LogException(exception);
            }
        }

        private static void RequireGeneratedRootAbsent()
        {
            if (AssetDatabase.IsValidFolder(GeneratedRoot) ||
                Directory.Exists(Path.GetFullPath(GeneratedRoot)) ||
                File.Exists(Path.GetFullPath(ReadyMarkerPath)) ||
                File.Exists(Path.GetFullPath(ReadyMarkerPath + ".tmp")))
            {
                throw new InvalidOperationException("Fixture runtime state was not clean.");
            }
        }

        private static GameObject AddChild(Transform parent, string name)
        {
            var child = new GameObject(name);
            child.transform.SetParent(parent, false);
            return child;
        }

        private static string RequireAssetGuid(string assetPath)
        {
            var guid = AssetDatabase.AssetPathToGUID(assetPath);
            if (guid.Length != 32)
            {
                throw new InvalidOperationException("Fixture asset GUID is unavailable.");
            }
            return guid;
        }

        private static void WriteReadyMarker(ReadyMarker marker)
        {
            var finalPath = Path.GetFullPath(ReadyMarkerPath);
            var directory = Path.GetDirectoryName(finalPath);
            if (string.IsNullOrWhiteSpace(directory))
            {
                throw new InvalidOperationException("Fixture marker directory is invalid.");
            }
            Directory.CreateDirectory(directory);
            var temporaryPath = finalPath + ".tmp";
            var payload = Encoding.UTF8.GetBytes(JsonUtility.ToJson(marker, false) + "\n");
            using (var stream = new FileStream(
                temporaryPath,
                FileMode.CreateNew,
                FileAccess.Write,
                FileShare.None))
            {
                stream.Write(payload, 0, payload.Length);
                stream.Flush(true);
            }
            File.Move(temporaryPath, finalPath);
        }

        private static string Sha256Hex(string value)
        {
            using (var sha256 = SHA256.Create())
            {
                var digest = sha256.ComputeHash(Encoding.UTF8.GetBytes(value ?? string.Empty));
                return string.Concat(digest.Select(item => item.ToString("x2")));
            }
        }

        [Serializable]
        private sealed class ReadyMarker
        {
            public string schema = string.Empty;
            public string scenarioId = string.Empty;
            public string runIdDigest = string.Empty;
            public string sceneGuid = string.Empty;
            public string scenePath = string.Empty;
            public string avatarPath = string.Empty;
            public string featureHostPath = string.Empty;
            public string armatureFeatureHostPath = string.Empty;
            public string[] targetPaths = Array.Empty<string>();
            public int baselineFeatureComponentCount;
        }
    }
}
