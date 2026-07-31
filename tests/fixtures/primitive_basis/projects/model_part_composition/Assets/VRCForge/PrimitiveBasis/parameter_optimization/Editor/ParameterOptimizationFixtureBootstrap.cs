using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;
using VRC.SDKBase;

namespace VRCForge.PrimitiveBasisFixtures
{
    [InitializeOnLoad]
    public static class ParameterOptimizationFixtureBootstrap
    {
        public const string GeneratedRoot =
            "Assets/VRCForge/PrimitiveBasis/RuntimeParameterOptimization";
        public const string ScenePath = GeneratedRoot + "/ParameterOptimization.unity";
        public const string ParametersPath = GeneratedRoot + "/Parameters.asset";
        public const string MenuRootPath = GeneratedRoot + "/MenuRoot.asset";
        public const string FxControllerPath = GeneratedRoot + "/FixtureFX.controller";
        public const string MotionClipPath = GeneratedRoot + "/SafeToggleMotion.anim";
        public const string RunIdEnvironment =
            "VRCFORGE_PRIMITIVE_PARAMETER_OPTIMIZATION_RUN_ID";
        public const string ReadyMarkerPath =
            "Library/VRCForge/primitive-basis-parameter-optimization-ready.json";
        public const int SafeToggleCount = 260;

        private static readonly string LiveRunId =
            Environment.GetEnvironmentVariable(RunIdEnvironment) ?? string.Empty;

        static ParameterOptimizationFixtureBootstrap()
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
                    "RuntimeParameterOptimization");
                if (string.IsNullOrWhiteSpace(createdGuid))
                {
                    throw new InvalidOperationException("Fixture runtime root could not be created.");
                }

                var scene = EditorSceneManager.NewScene(
                    NewSceneSetup.EmptyScene,
                    NewSceneMode.Single);
                var avatar = new GameObject("Avatar");
                var descriptor = avatar.AddComponent<VRCAvatarDescriptor>();
                avatar.AddComponent<Animator>();
                descriptor.baseAnimationLayers = new[]
                {
                    DefaultLayer(VRCAvatarDescriptor.AnimLayerType.Base),
                    DefaultLayer(VRCAvatarDescriptor.AnimLayerType.Additive),
                    DefaultLayer(VRCAvatarDescriptor.AnimLayerType.Gesture),
                    DefaultLayer(VRCAvatarDescriptor.AnimLayerType.Action),
                    DefaultLayer(VRCAvatarDescriptor.AnimLayerType.FX)
                };
                descriptor.specialAnimationLayers = new[]
                {
                    DefaultLayer(VRCAvatarDescriptor.AnimLayerType.Sitting),
                    DefaultLayer(VRCAvatarDescriptor.AnimLayerType.TPose),
                    DefaultLayer(VRCAvatarDescriptor.AnimLayerType.IKPose)
                };

                var parameters = ScriptableObject.CreateInstance<VRCExpressionParameters>();
                var rows = new List<VRCExpressionParameters.Parameter>();
                for (var index = 0; index < SafeToggleCount; index++)
                {
                    rows.Add(Parameter(
                        "SafeToggle" + index.ToString("000"),
                        VRCExpressionParameters.ValueType.Bool,
                        true));
                }
                rows.Add(Parameter(
                    "FT/JawOpen",
                    VRCExpressionParameters.ValueType.Float,
                    false));
                rows.Add(Parameter(
                    "Puppet/X",
                    VRCExpressionParameters.ValueType.Float,
                    false));
                rows.Add(Parameter(
                    "OSC/Raw",
                    VRCExpressionParameters.ValueType.Int,
                    false));
                parameters.parameters = rows.ToArray();
                AssetDatabase.CreateAsset(parameters, ParametersPath);

                var menuRoot = CreateMenuTree(out var menuAssetCount);
                descriptor.customExpressions = true;
                descriptor.expressionParameters = parameters;
                descriptor.expressionsMenu = menuRoot;

                var layers = descriptor.baseAnimationLayers.ToArray();
                var fxIndex = Array.FindIndex(
                    layers,
                    layer => layer.type == VRCAvatarDescriptor.AnimLayerType.FX);
                if (fxIndex < 0)
                {
                    throw new InvalidOperationException("Fixture FX layer is missing.");
                }
                var fxLayer = layers[fxIndex];
                fxLayer.isDefault = false;
                fxLayer.animatorController = CreateFxController();
                layers[fxIndex] = fxLayer;
                descriptor.customizeAnimationLayers = true;
                descriptor.baseAnimationLayers = layers;
                CreatePackageFeature(avatar);

                SceneManager.SetActiveScene(scene);
                if (!EditorSceneManager.SaveScene(scene, ScenePath))
                {
                    throw new InvalidOperationException("Fixture scene could not be saved.");
                }
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh(
                    ImportAssetOptions.ForceSynchronousImport |
                    ImportAssetOptions.ForceUpdate);
                if (scene.isDirty || parameters.parameters.Length != SafeToggleCount + 3)
                {
                    throw new InvalidOperationException("Fixture baseline readback failed.");
                }

                var featureCount = avatar
                    .GetComponents<Component>()
                    .Count(component =>
                        component != null &&
                        component.GetType().FullName == "VF.Model.VRCFury");
                if (featureCount != 1)
                {
                    throw new InvalidOperationException(
                        "Fixture baseline requires one supported build feature.");
                }

                WriteReadyMarker(new ReadyMarker
                {
                    schema = "vrcforge.primitive_basis_fixture_ready.v1",
                    scenarioId = "parameter_optimization",
                    runIdDigest = Sha256Hex(LiveRunId),
                    sceneGuid = RequireAssetGuid(ScenePath),
                    scenePath = ScenePath,
                    avatarPath = "Avatar",
                    parametersPath = ParametersPath,
                    menuRootPath = MenuRootPath,
                    fxControllerPath = FxControllerPath,
                    parameterCount = parameters.parameters.Length,
                    safeNetworkedBoolCount = SafeToggleCount,
                    eligibleCostBits = SafeToggleCount,
                    budgetBits = 256,
                    menuAssetCount = menuAssetCount,
                    excludedParameterNames = new[]
                    {
                        "FT/JawOpen",
                        "Puppet/X",
                        "OSC/Raw"
                    },
                    baselineFeatureComponentCount = featureCount
                });
                Debug.Log("[VRCForge Fixture] Parameter optimization fixture is ready.");
            }
            catch (Exception exception)
            {
                Debug.LogException(exception);
            }
        }

        private static VRCAvatarDescriptor.CustomAnimLayer DefaultLayer(
            VRCAvatarDescriptor.AnimLayerType type)
        {
            return new VRCAvatarDescriptor.CustomAnimLayer
            {
                type = type,
                isDefault = true,
                animatorController = null
            };
        }

        private static AnimatorController CreateFxController()
        {
            var clip = new AnimationClip
            {
                name = "Safe Toggle Motion",
                frameRate = 30f
            };
            AnimationUtility.SetEditorCurve(
                clip,
                EditorCurveBinding.FloatCurve(
                    string.Empty,
                    typeof(Transform),
                    "m_LocalPosition.x"),
                AnimationCurve.Constant(0f, 1f, 0f));
            AssetDatabase.CreateAsset(clip, MotionClipPath);

            var controller = AnimatorController.CreateAnimatorControllerAtPath(
                FxControllerPath);
            if (controller == null)
            {
                throw new InvalidOperationException("Fixture controller could not be created.");
            }
            controller.AddParameter(
                "SafeToggle000",
                AnimatorControllerParameterType.Bool);
            controller.AddParameter(
                "SafeToggle001",
                AnimatorControllerParameterType.Bool);
            controller.AddParameter(
                "FT/JawOpen",
                AnimatorControllerParameterType.Float);
            var machine = controller.layers[0].stateMachine;
            var idle = machine.AddState("Idle");
            var active = machine.AddState("Active");
            active.motion = clip;
            machine.defaultState = idle;
            var transition = idle.AddTransition(active);
            transition.hasExitTime = false;
            transition.AddCondition(
                AnimatorConditionMode.If,
                0f,
                "SafeToggle000");
            var driver = active.AddStateMachineBehaviour<VRCAvatarParameterDriver>();
            driver.parameters = new List<VRC_AvatarParameterDriver.Parameter>
            {
                new VRC_AvatarParameterDriver.Parameter
                {
                    source = "SafeToggle000",
                    name = "SafeToggle001",
                    type = VRC_AvatarParameterDriver.ChangeType.Copy
                }
            };
            EditorUtility.SetDirty(controller);
            return controller;
        }

        private static VRCExpressionsMenu CreateMenuTree(out int assetCount)
        {
            var root = CreateMenu(MenuRootPath);
            assetCount = 1;
            var safeIndex = 0;
            for (var groupIndex = 0; groupIndex < 5; groupIndex++)
            {
                var group = CreateMenu(
                    GeneratedRoot + "/MenuGroup" + groupIndex + ".asset");
                assetCount++;
                root.controls.Add(Submenu("Group " + groupIndex, group));
                for (var leafIndex = 0; leafIndex < 7; leafIndex++)
                {
                    var leafPath = GeneratedRoot +
                        "/MenuLeaf" + groupIndex + "_" + leafIndex + ".asset";
                    var leaf = CreateMenu(leafPath);
                    assetCount++;
                    group.controls.Add(Submenu("Leaf " + leafIndex, leaf));
                    for (var slot = 0; slot < 8 && safeIndex < SafeToggleCount; slot++)
                    {
                        leaf.controls.Add(Toggle(
                            "Safe " + safeIndex,
                            "SafeToggle" + safeIndex.ToString("000")));
                        safeIndex++;
                    }
                }
            }
            if (safeIndex != SafeToggleCount)
            {
                throw new InvalidOperationException("Fixture menu coverage is incomplete.");
            }
            var puppetLeaf = AssetDatabase.LoadAssetAtPath<VRCExpressionsMenu>(
                GeneratedRoot + "/MenuLeaf4_6.asset");
            if (puppetLeaf == null || puppetLeaf.controls.Count >= 8)
            {
                throw new InvalidOperationException("Fixture puppet menu has no capacity.");
            }
            puppetLeaf.controls.Add(new VRCExpressionsMenu.Control
            {
                name = "Excluded Puppet",
                type = VRCExpressionsMenu.Control.ControlType.RadialPuppet,
                subParameters = new[]
                {
                    new VRCExpressionsMenu.Control.Parameter { name = "Puppet/X" }
                }
            });
            EditorUtility.SetDirty(puppetLeaf);
            return root;
        }

        private static VRCExpressionsMenu CreateMenu(string path)
        {
            var menu = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
            menu.controls = new List<VRCExpressionsMenu.Control>();
            AssetDatabase.CreateAsset(menu, path);
            return menu;
        }

        private static VRCExpressionsMenu.Control Submenu(
            string name,
            VRCExpressionsMenu menu)
        {
            return new VRCExpressionsMenu.Control
            {
                name = name,
                type = VRCExpressionsMenu.Control.ControlType.SubMenu,
                subMenu = menu
            };
        }

        private static VRCExpressionsMenu.Control Toggle(
            string label,
            string parameter)
        {
            return new VRCExpressionsMenu.Control
            {
                name = label,
                type = VRCExpressionsMenu.Control.ControlType.Toggle,
                parameter = new VRCExpressionsMenu.Control.Parameter { name = parameter },
                value = 1f
            };
        }

        private static VRCExpressionParameters.Parameter Parameter(
            string name,
            VRCExpressionParameters.ValueType type,
            bool networkSynced)
        {
            var parameter = new VRCExpressionParameters.Parameter
            {
                name = name,
                valueType = type,
                defaultValue = 0,
                saved = false
            };
            var field = typeof(VRCExpressionParameters.Parameter).GetField(
                "networkSynced",
                BindingFlags.Public |
                BindingFlags.NonPublic |
                BindingFlags.Instance);
            if (field == null || field.FieldType != typeof(bool))
            {
                throw new InvalidOperationException(
                    "Fixture parameter synchronization field is unavailable.");
            }
            field.SetValue(parameter, networkSynced);
            return parameter;
        }

        private static void CreatePackageFeature(GameObject avatar)
        {
            var type = AppDomain.CurrentDomain.GetAssemblies()
                .Where(assembly => assembly.GetName().Name == "com.vrcfury.api")
                .Select(assembly =>
                    assembly.GetType("com.vrcfury.api.FuryComponents", false))
                .SingleOrDefault(value => value != null);
            if (type == null || !type.IsPublic)
            {
                throw new InvalidOperationException(
                    "Required fixture package API is unavailable.");
            }
            var create = type.GetMethod(
                "CreateToggle",
                BindingFlags.Public | BindingFlags.Static);
            var feature = create?.Invoke(null, new object[] { avatar });
            var setMenuPath = feature?.GetType().GetMethod(
                "SetMenuPath",
                BindingFlags.Public | BindingFlags.Instance);
            if (feature == null || setMenuPath == null)
            {
                throw new InvalidOperationException(
                    "Required fixture package feature could not be created.");
            }
            setMenuPath.Invoke(feature, new object[] { "Fixture/Build Marker" });
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
            public string parametersPath = string.Empty;
            public string menuRootPath = string.Empty;
            public string fxControllerPath = string.Empty;
            public int parameterCount;
            public int safeNetworkedBoolCount;
            public int eligibleCostBits;
            public int budgetBits;
            public int menuAssetCount;
            public string[] excludedParameterNames = Array.Empty<string>();
            public int baselineFeatureComponentCount;
        }
    }
}
