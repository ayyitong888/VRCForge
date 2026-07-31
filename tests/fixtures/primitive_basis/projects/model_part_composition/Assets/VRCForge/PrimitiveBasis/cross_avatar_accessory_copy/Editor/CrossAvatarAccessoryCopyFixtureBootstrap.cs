using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.SceneManagement;

namespace VRCForge.PrimitiveBasisFixtures
{
    [InitializeOnLoad]
    public static class CrossAvatarAccessoryCopyFixtureBootstrap
    {
        public const string GeneratedRoot =
            "Assets/VRCForge/PrimitiveBasis/RuntimeCrossAvatarAccessoryCopy";
        public const string ScenePath = GeneratedRoot + "/CrossAvatarAccessoryCopy.unity";
        public const string AnimationPath = GeneratedRoot + "/AccessoryMotion.anim";
        public const string ControllerPath = GeneratedRoot + "/Accessory.controller";
        public const string PrefabPath = GeneratedRoot + "/AccessoryCopy.prefab";
        public const string RunIdEnvironment =
            "VRCFORGE_PRIMITIVE_CROSS_AVATAR_ACCESSORY_COPY_RUN_ID";
        public const string ReadyMarkerPath =
            "Library/VRCForge/primitive-basis-cross-avatar-accessory-copy-ready.json";

        private static readonly string LiveRunId =
            Environment.GetEnvironmentVariable(RunIdEnvironment) ?? string.Empty;

        static CrossAvatarAccessoryCopyFixtureBootstrap()
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
                    "RuntimeCrossAvatarAccessoryCopy");
                if (string.IsNullOrWhiteSpace(createdGuid))
                {
                    throw new InvalidOperationException("Fixture runtime root could not be created.");
                }

                var clip = new AnimationClip
                {
                    name = "Accessory Motion",
                    frameRate = 30f
                };
                AnimationUtility.SetEditorCurve(
                    clip,
                    EditorCurveBinding.FloatCurve(
                        "Accessory",
                        typeof(Transform),
                        "m_LocalPosition.y"),
                    AnimationCurve.Linear(0f, 0f, 1f, 0.1f));
                AssetDatabase.CreateAsset(clip, AnimationPath);
                var controller = AnimatorController.CreateAnimatorControllerAtPath(
                    ControllerPath);
                if (controller == null)
                {
                    throw new InvalidOperationException("Fixture controller could not be created.");
                }
                var state = controller.layers[0].stateMachine.AddState("Accessory Motion");
                state.motion = clip;
                controller.layers[0].stateMachine.defaultState = state;
                EditorUtility.SetDirty(controller);

                var scene = EditorSceneManager.NewScene(
                    NewSceneSetup.EmptyScene,
                    NewSceneMode.Single);
                var sourceAvatar = new GameObject("AvatarA");
                var sourceAnimator = sourceAvatar.AddComponent<Animator>();
                sourceAnimator.runtimeAnimatorController = controller;
                var anchor = AddChild(sourceAvatar.transform, "AccessoryAnchor");
                var accessory = AddChild(sourceAvatar.transform, "Accessory");
                accessory.AddComponent<MeshFilter>();
                var renderer = accessory.AddComponent<MeshRenderer>();
                renderer.sharedMaterials = Array.Empty<Material>();
                accessory.AddComponent<BoxCollider>();
                var constraint = accessory.AddComponent<ParentConstraint>();
                constraint.AddSource(new ConstraintSource
                {
                    sourceTransform = anchor.transform,
                    weight = 1f
                });
                constraint.SetTranslationOffset(0, Vector3.zero);
                constraint.SetRotationOffset(0, Vector3.zero);
                constraint.locked = true;
                constraint.constraintActive = true;

                var targetAvatar = new GameObject("AvatarB");
                targetAvatar.transform.localPosition = new Vector3(1f, 2f, 3f);

                SceneManager.SetActiveScene(scene);
                if (!EditorSceneManager.SaveScene(scene, ScenePath))
                {
                    throw new InvalidOperationException("Fixture scene could not be saved.");
                }
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh(
                    ImportAssetOptions.ForceSynchronousImport |
                    ImportAssetOptions.ForceUpdate);
                if (scene.isDirty ||
                    accessory.GetComponent<MeshRenderer>() == null ||
                    accessory.GetComponent<ParentConstraint>() == null ||
                    controller.animationClips.Length != 1 ||
                    AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath) != null)
                {
                    throw new InvalidOperationException("Fixture baseline readback failed.");
                }

                WriteReadyMarker(new ReadyMarker
                {
                    schema = "vrcforge.primitive_basis_fixture_ready.v1",
                    scenarioId = "cross_avatar_accessory_copy",
                    runIdDigest = Sha256Hex(LiveRunId),
                    sceneGuid = RequireAssetGuid(ScenePath),
                    scenePath = ScenePath,
                    sourceAvatarPath = "AvatarA",
                    sourceObjectPath = "AvatarA/Accessory",
                    constraintSourcePath = "AvatarA/AccessoryAnchor",
                    targetAvatarPath = "AvatarB",
                    targetParentPath = "AvatarB",
                    animationPath = AnimationPath,
                    controllerPath = ControllerPath,
                    prefabPath = PrefabPath,
                    rendererCount = 1,
                    constraintSourceCount = constraint.sourceCount,
                    baselineTargetCopyCount = 0,
                    baselinePrefabCount = 0
                });
                Debug.Log("[VRCForge Fixture] Cross-avatar accessory fixture is ready.");
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
            public string sourceAvatarPath = string.Empty;
            public string sourceObjectPath = string.Empty;
            public string constraintSourcePath = string.Empty;
            public string targetAvatarPath = string.Empty;
            public string targetParentPath = string.Empty;
            public string animationPath = string.Empty;
            public string controllerPath = string.Empty;
            public string prefabPath = string.Empty;
            public int rendererCount;
            public int constraintSourceCount;
            public int baselineTargetCopyCount;
            public int baselinePrefabCount;
        }
    }
}
