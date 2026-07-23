using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace VRCForge.PrimitiveBasisFixtures
{
    [InitializeOnLoad]
    public static class ModelPartCompositionFixtureBootstrap
    {
        public const string ScenePath = "Assets/VRCForge/PrimitiveBasis/model_part_composition/ModelPartComposition.unity";
        public const string RunIdEnvironment = "VRCFORGE_PRIMITIVE_BASIS_RUN_ID";
        public const string ReadyMarkerPath = "Library/VRCForge/primitive-basis-model-part-ready.json";

        private static readonly string LiveRunId = Environment.GetEnvironmentVariable(RunIdEnvironment) ?? string.Empty;

        static ModelPartCompositionFixtureBootstrap()
        {
            if (Application.isBatchMode || string.IsNullOrWhiteSpace(LiveRunId))
            {
                return;
            }

            EditorApplication.delayCall += OpenPinnedSceneForLiveRun;
        }

        public static void BuildFixtureForRepository()
        {
            if (File.Exists(ScenePath))
            {
                throw new InvalidOperationException("The fixed model-part fixture scene already exists.");
            }

            var sceneDirectory = Path.GetDirectoryName(ScenePath);
            if (string.IsNullOrWhiteSpace(sceneDirectory))
            {
                throw new InvalidOperationException("The fixed fixture scene directory is invalid.");
            }
            Directory.CreateDirectory(sceneDirectory);

            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var avatar = new GameObject("FixtureAvatar");
            avatar.AddComponent<nadena.dev.ndmf.runtime.components.NDMFAvatarRoot>();
            var baseArmature = AddChild(avatar.transform, "Armature");
            AddChild(baseArmature.transform, "Hips");

            var part = AddChild(avatar.transform, "Part");
            var partArmature = AddChild(part.transform, "Armature");
            var partHips = AddChild(partArmature.transform, "Hips");
            var rendererProbe = AddChild(part.transform, "RendererProbe");
            var renderer = rendererProbe.AddComponent<SkinnedMeshRenderer>();
            renderer.rootBone = partHips.transform;
            renderer.bones = new[] { partHips.transform };
            renderer.sharedMesh = null;
            renderer.sharedMaterials = Array.Empty<Material>();
            renderer.updateWhenOffscreen = false;

            SceneManager.SetActiveScene(scene);
            if (!EditorSceneManager.SaveScene(scene, ScenePath))
            {
                throw new InvalidOperationException("Unity could not save the fixed model-part fixture scene.");
            }

            AssetDatabase.ImportAsset(ScenePath, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            AssetDatabase.SaveAssets();
            Debug.Log("[VRCForge Fixture] Fixed model-part composition scene created.");
        }

        private static GameObject AddChild(Transform parent, string name)
        {
            var child = new GameObject(name);
            child.transform.SetParent(parent, false);
            return child;
        }

        private static void OpenPinnedSceneForLiveRun()
        {
            if (EditorApplication.isCompiling || EditorApplication.isUpdating)
            {
                EditorApplication.delayCall += OpenPinnedSceneForLiveRun;
                return;
            }
            if (!File.Exists(ScenePath))
            {
                Debug.LogError("[VRCForge Fixture] Fixed model-part composition scene is missing.");
                return;
            }

            var scene = EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
            if (!scene.IsValid() || !scene.isLoaded)
            {
                Debug.LogError("[VRCForge Fixture] Fixed model-part composition scene did not open.");
                return;
            }

            var avatar = GameObject.Find("FixtureAvatar");
            var componentHost = GameObject.Find("FixtureAvatar/Part/Armature");
            var mergeTarget = GameObject.Find("FixtureAvatar/Armature");
            if (avatar == null || componentHost == null || mergeTarget == null)
            {
                Debug.LogError("[VRCForge Fixture] Fixed model-part composition hierarchy is incomplete.");
                return;
            }

            var readyPath = Path.GetFullPath(ReadyMarkerPath);
            var readyDirectory = Path.GetDirectoryName(readyPath);
            if (string.IsNullOrWhiteSpace(readyDirectory))
            {
                Debug.LogError("[VRCForge Fixture] Ready marker directory is invalid.");
                return;
            }
            Directory.CreateDirectory(readyDirectory);

            var marker = new ReadyMarker
            {
                schema = "vrcforge.primitive_basis_fixture_ready.v1",
                runIdDigest = Sha256Hex(LiveRunId),
                sceneGuid = AssetDatabase.AssetPathToGUID(ScenePath),
                avatarPath = "FixtureAvatar",
                componentHostPath = "FixtureAvatar/Part/Armature",
                mergeTargetPath = "FixtureAvatar/Armature"
            };
            var temporaryPath = readyPath + ".tmp";
            File.WriteAllText(temporaryPath, JsonUtility.ToJson(marker, false) + "\n", new UTF8Encoding(false));
            if (File.Exists(readyPath))
            {
                File.Delete(readyPath);
            }
            File.Move(temporaryPath, readyPath);
            Debug.Log("[VRCForge Fixture] Fixed model-part composition fixture is ready.");
        }

        private static string Sha256Hex(string value)
        {
            using (var sha256 = SHA256.Create())
            {
                var digest = sha256.ComputeHash(Encoding.UTF8.GetBytes(value ?? string.Empty));
                var result = new StringBuilder(digest.Length * 2);
                foreach (var item in digest)
                {
                    result.Append(item.ToString("x2"));
                }
                return result.ToString();
            }
        }

        [Serializable]
        private sealed class ReadyMarker
        {
            public string schema = string.Empty;
            public string runIdDigest = string.Empty;
            public string sceneGuid = string.Empty;
            public string avatarPath = string.Empty;
            public string componentHostPath = string.Empty;
            public string mergeTargetPath = string.Empty;
        }
    }
}
