using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.PackageManager;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_atomic_reference_rename",
        Description = "Preview or atomically migrate one fixed-schema avatar object or parameter reference set."
    )]
    public static class AtomicReferenceRenameTool
    {
        internal const string ToolName = "vrc_atomic_reference_rename";
        private const string ResultSchema = "vrcforge.atomic_reference_rename.v1";
        private const string PlanSchema = "vrcforge.atomic_reference_rename_plan.v1";
        private const string ObjectOperation = "game_object";
        private const string ParameterOperation = "parameter";
        private const string DescriptorType = "VRC.SDK3.Avatars.Components.VRCAvatarDescriptor";
        private const string ExpressionParametersType =
            "VRC.SDK3.Avatars.ScriptableObjects.VRCExpressionParameters";
        private const string ExpressionsMenuType =
            "VRC.SDK3.Avatars.ScriptableObjects.VRCExpressionsMenu";
        private const string ContactReceiverType =
            "VRC.SDK3.Dynamics.Contact.Components.VRCContactReceiver";
        private const string PhysBoneType =
            "VRC.SDK3.Dynamics.PhysBone.Components.VRCPhysBone";
        private const string ParameterDriverType =
            "VRC.SDK3.Avatars.Components.VRCAvatarParameterDriver";
        private const string RegisteredFeatureType = "VF.Model.VRCFury";
        private const int MaxAssets = 4096;
        private const int MaxInventoryAssets = 250000;
        private const int MaxInventoryEntries = 500000;
        private const int MaxOpenProjectScenes = 64;
        private const int MaxRegisteredAssetObjects = 500000;
        private const long MaxInventoryBytes = 16L * 1024L * 1024L * 1024L;
        private const int MaxPackageFiles = 250000;
        private const int MaxReferences = 16384;
        private const long MaxBackupAssetBytes = 64L * 1024L * 1024L;
        private const long MaxBackupTotalBytes = 256L * 1024L * 1024L;

        private static readonly HashSet<string> CommonKeys = new HashSet<string>(
            new[] { "operationKind", "scenePath", "avatarPath", "preview", "saveScene" },
            StringComparer.Ordinal);

        private static readonly HashSet<string> ObjectKeys = new HashSet<string>(
            new[] { "targetObjectPath", "newName" },
            StringComparer.Ordinal);

        private static readonly HashSet<string> ParameterKeys = new HashSet<string>(
            new[] { "oldParameterName", "newParameterName" },
            StringComparer.Ordinal);

        private static readonly HashSet<string> ExpectedKeys = new HashSet<string>(
            new[]
            {
                "expectedProjectPath",
                "expectedSceneGuid",
                "expectedSceneHandle",
                "expectedSceneFileDigest",
                "expectedSceneFileIdentity",
                "expectedSceneMetaDigest",
                "expectedSceneMetaIdentity",
                "expectedAvatarObjectId",
                "expectedTargetIdentityDigest",
                "expectedAssemblySetDigest",
                "expectedAssetInventoryDigest",
                "expectedBeforeStateDigest",
                "expectedTargetStateDigest",
                "expectedPlanDigest"
            },
            StringComparer.Ordinal);

        private static readonly string[] FixedRegisteredStringPaths =
        {
            "content.globalParam",
            "content.driveGlobalParam"
        };

        public static object HandleCommand(JObject @params)
        {
            var mutationStarted = false;
            try
            {
                var parameters = @params
                    ?? throw new AtomicReferenceRenameException(
                        "Atomic reference rename arguments are required.");
                var preview = ReadStrictBoolean(parameters, "preview");
                var saveScene = ReadStrictBoolean(parameters, "saveScene");
                if (preview && saveScene)
                {
                    throw new AtomicReferenceRenameException(
                        "Atomic reference rename preview cannot save a scene.");
                }
                if (!preview && !saveScene)
                {
                    throw new AtomicReferenceRenameException(
                        "saveScene must be true for atomic reference rename apply.");
                }

                var request = ParseRequest(parameters, preview);
                var snapshot = BuildPreview(request);
                if (preview)
                {
                    return new SuccessResponse(
                        "Atomic reference rename preview completed.",
                        snapshot.BuildPreviewPayload());
                }

                ValidateExpected(parameters, snapshot);
                return Apply(snapshot, ref mutationStarted);
            }
            catch (Exception exception)
            {
                return Failure(exception, mutationStarted, !mutationStarted);
            }
        }

        private static RenameRequest ParseRequest(JObject parameters, bool preview)
        {
            var operation = ReadRequiredText(parameters, "operationKind", 32);
            if (operation != ObjectOperation && operation != ParameterOperation)
            {
                throw new AtomicReferenceRenameException("operationKind is unsupported.");
            }

            var operationKeys = operation == ObjectOperation ? ObjectKeys : ParameterKeys;
            var allowed = new HashSet<string>(CommonKeys, StringComparer.Ordinal);
            allowed.UnionWith(operationKeys);
            if (!preview)
            {
                allowed.UnionWith(ExpectedKeys);
            }
            if (parameters.Properties().Any(property => !allowed.Contains(property.Name)))
            {
                throw new AtomicReferenceRenameException(
                    "Atomic reference rename arguments contain unsupported fields.");
            }
            if (CommonKeys.Concat(operationKeys)
                .Any(key => parameters.Property(key, StringComparison.Ordinal) == null))
            {
                throw new AtomicReferenceRenameException(
                    "Atomic reference rename arguments are incomplete.");
            }
            if (!preview
                && ExpectedKeys.Any(key => parameters.Property(key, StringComparison.Ordinal) == null))
            {
                throw new AtomicReferenceRenameException(
                    "Verified atomic reference rename preconditions are required for apply.");
            }

            var request = new RenameRequest
            {
                OperationKind = operation,
                ScenePath = NormalizeScenePath(ReadRequiredText(parameters, "scenePath", 512)),
                AvatarPath = NormalizeHierarchyPath(
                    ReadRequiredText(parameters, "avatarPath", 512),
                    "avatarPath")
            };
            if (operation == ObjectOperation)
            {
                request.TargetObjectPath = NormalizeHierarchyPath(
                    ReadRequiredText(parameters, "targetObjectPath", 512),
                    "targetObjectPath");
                request.NewName = NormalizeObjectName(
                    ReadRequiredText(parameters, "newName", 128));
                if (request.TargetObjectPath == request.AvatarPath
                    || !request.TargetObjectPath.StartsWith(
                        request.AvatarPath + "/",
                        StringComparison.Ordinal))
                {
                    throw new AtomicReferenceRenameException(
                        "The object target must be below the selected avatar.");
                }
                request.OldName = request.TargetObjectPath.Split('/').Last();
                if (request.OldName == request.NewName)
                {
                    throw new AtomicReferenceRenameException("The object name is unchanged.");
                }
                request.TargetAfter = request.TargetObjectPath.Substring(
                    0,
                    request.TargetObjectPath.Length - request.OldName.Length) + request.NewName;
                request.TargetBefore = request.TargetObjectPath;
            }
            else
            {
                request.OldParameterName = NormalizeParameterName(
                    ReadRequiredText(parameters, "oldParameterName", 128),
                    "oldParameterName");
                request.NewParameterName = NormalizeParameterName(
                    ReadRequiredText(parameters, "newParameterName", 128),
                    "newParameterName");
                if (request.OldParameterName == request.NewParameterName)
                {
                    throw new AtomicReferenceRenameException("The parameter name is unchanged.");
                }
                request.TargetBefore = request.OldParameterName;
                request.TargetAfter = request.NewParameterName;
            }
            return request;
        }

        private static RenameSnapshot BuildPreview(RenameRequest request)
        {
            RequireNoDirtyProjectAssets();
            var scene = SceneObjectCopyCore.ResolveSavedScene(
                request.ScenePath,
                "atomic reference rename scene");
            if (scene.Handle == 0)
            {
                throw new AtomicReferenceRenameException("The selected scene handle is invalid.");
            }
            var avatar = SceneObjectCopyCore.ResolveUniqueGameObject(
                scene.Scene,
                request.AvatarPath,
                "selected avatar");
            var descriptor = ResolveExactComponent(avatar, DescriptorType, "selected avatar descriptor");
            var avatarId = StableObjectId(avatar, "selected avatar");
            var assemblyDigestBefore = ComputeAssemblySetDigest();
            var inventoryBefore = BuildInventory();

            var snapshot = new RenameSnapshot
            {
                Request = request,
                Scene = scene,
                Avatar = avatar,
                Descriptor = descriptor,
                AvatarObjectId = avatarId,
                AssemblySetDigest = assemblyDigestBefore,
                Inventory = inventoryBefore,
                AssetInventoryDigest = inventoryBefore.Digest,
                ObjectCount = avatar.GetComponentsInChildren<Transform>(true).Length
            };

            var context = new ScanContext(snapshot, inventoryBefore);
            if (request.OperationKind == ObjectOperation)
            {
                ScanObjectRename(context);
            }
            else
            {
                ScanParameterRename(context);
            }
            FinalizePreview(context);
            RequirePlannedAssetsClean(snapshot);

            var sceneAfter = SceneObjectCopyCore.ResolveSavedScene(
                request.ScenePath,
                "atomic reference rename scene readback");
            var assemblyDigestAfter = ComputeAssemblySetDigest();
            var inventoryAfter = BuildInventory();
            RequireNoDirtyProjectAssets();
            if (!SceneEvidenceMatches(scene, sceneAfter)
                || sceneAfter.Scene.isDirty
                || assemblyDigestAfter != assemblyDigestBefore
                || inventoryAfter.Digest != inventoryBefore.Digest)
            {
                throw new AtomicReferenceRenameException(
                    "Atomic reference rename preview did not preserve exact project state.");
            }
            return snapshot;
        }

        private static void RequireNoDirtyProjectAssets()
        {
            if (SceneManager.sceneCount > MaxOpenProjectScenes)
            {
                throw new AtomicReferenceRenameException(
                    "The open project scene set exceeds the bounded cleanliness scan.");
            }
            for (var index = 0; index < SceneManager.sceneCount; index++)
            {
                var scene = SceneManager.GetSceneAt(index);
                if (!scene.IsValid()
                    || !scene.isLoaded
                    || string.IsNullOrWhiteSpace(scene.path)
                    || scene.path != scene.path.Replace('\\', '/')
                    || !scene.path.StartsWith("Assets/", StringComparison.Ordinal)
                    || !scene.path.EndsWith(".unity", StringComparison.Ordinal)
                    || scene.path.Split('/').Any(part =>
                        string.IsNullOrWhiteSpace(part) || part == "." || part == ".."))
                {
                    throw new AtomicReferenceRenameException(
                        "An open project scene has incomplete persistent state.");
                }
                var sceneAsset = AssetDatabase.LoadAssetAtPath<SceneAsset>(scene.path);
                if (sceneAsset == null
                    || !EditorUtility.IsPersistent(sceneAsset)
                    || AssetDatabase.GetAssetPath(sceneAsset) != scene.path
                    || scene.isDirty)
                {
                    throw new AtomicReferenceRenameException(
                        "All open project scenes must be saved before atomic reference rename.");
                }
            }

            var registered = AssetDatabase.GetAllAssetPaths();
            if (registered == null || registered.Length > MaxInventoryEntries)
            {
                throw new AtomicReferenceRenameException(
                    "The registered asset set exceeds the bounded cleanliness scan.");
            }
            var paths = registered
                .Where(path => !string.IsNullOrWhiteSpace(path)
                    && IsProjectOwnedAssetPath(path))
                .OrderBy(path => path, StringComparer.Ordinal)
                .ToArray();
            if (paths.Length > MaxInventoryAssets
                || paths.Distinct(StringComparer.Ordinal).Count() != paths.Length)
            {
                throw new AtomicReferenceRenameException(
                    "The registered project asset set is incomplete.");
            }

            var objectCount = 0;
            foreach (var path in paths)
            {
                if (path != path.Replace('\\', '/')
                    || path.Split('/').Any(part =>
                        string.IsNullOrWhiteSpace(part) || part == "." || part == ".."))
                {
                    throw new AtomicReferenceRenameException(
                        "A registered project asset path is invalid.");
                }
                var absolute = Path.GetFullPath(Path.Combine(
                    CurrentProjectPath(),
                    path.Replace('/', Path.DirectorySeparatorChar)));
                var isDirectory = Directory.Exists(absolute);
                if (!isDirectory && !File.Exists(absolute))
                {
                    throw new AtomicReferenceRenameException(
                        "A registered project asset is missing from disk.");
                }
                var importer = AssetImporter.GetAtPath(path);
                if (importer == null
                    || string.IsNullOrWhiteSpace(importer.assetPath)
                    || importer.assetPath.Replace('\\', '/') != path
                    || EditorUtility.IsDirty(importer))
                {
                    throw new AtomicReferenceRenameException(
                        "All project asset importers must be saved before atomic reference rename.");
                }
                objectCount++;
                if (objectCount > MaxRegisteredAssetObjects)
                {
                    throw new AtomicReferenceRenameException(
                        "The registered asset object set exceeds the bounded cleanliness scan.");
                }
                if (isDirectory)
                {
                    continue;
                }

                var assets = AssetDatabase.LoadAllAssetsAtPath(path);
                if (assets == null
                    || assets.Length == 0
                    || objectCount > MaxRegisteredAssetObjects - assets.Length)
                {
                    throw new AtomicReferenceRenameException(
                        "A registered project asset has incomplete persistent objects.");
                }
                objectCount += assets.Length;
                foreach (var asset in assets)
                {
                    if (asset == null
                        || !EditorUtility.IsPersistent(asset)
                        || AssetDatabase.GetAssetPath(asset) != path
                        || EditorUtility.IsDirty(asset))
                    {
                        throw new AtomicReferenceRenameException(
                            "All project assets must be saved before atomic reference rename.");
                    }
                }
            }
        }

        private static bool IsProjectOwnedAssetPath(string path)
        {
            if (path.StartsWith("Assets/", StringComparison.Ordinal))
            {
                return true;
            }
            if (!path.StartsWith("Packages/", StringComparison.Ordinal))
            {
                return false;
            }
            var package = UnityEditor.PackageManager.PackageInfo.FindForAssetPath(path);
            if (package == null || string.IsNullOrWhiteSpace(package.resolvedPath))
            {
                return false;
            }
            var projectPackages = Path.GetFullPath(
                Path.Combine(CurrentProjectPath(), "Packages"));
            var resolved = Path.GetFullPath(package.resolvedPath)
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            return resolved.StartsWith(
                projectPackages + Path.DirectorySeparatorChar,
                StringComparison.OrdinalIgnoreCase);
        }

        private static void RequirePlannedAssetsClean(RenameSnapshot snapshot)
        {
            foreach (var asset in snapshot.Assets)
            {
                if (asset.AssetPath == snapshot.Request.ScenePath)
                {
                    if (snapshot.Scene.Scene.isDirty)
                    {
                        throw new AtomicReferenceRenameException(
                            "The planned scene has unsaved changes.");
                    }
                    continue;
                }
                var objects = AssetDatabase.LoadAllAssetsAtPath(asset.AssetPath);
                if (objects == null
                    || objects.Length == 0
                    || objects.Any(item => item == null
                        || AssetDatabase.GetAssetPath(item) != asset.AssetPath))
                {
                    throw new AtomicReferenceRenameException(
                        "A planned asset has an incomplete persistence object set.");
                }
                if (objects.Any(EditorUtility.IsDirty))
                {
                    throw new AtomicReferenceRenameException(
                        "A planned asset has pre-existing unsaved changes.");
                }
            }
        }

        private static void ScanObjectRename(ScanContext context)
        {
            var request = context.Snapshot.Request;
            var scene = context.Snapshot.Scene;
            var avatar = context.Snapshot.Avatar;
            var target = SceneObjectCopyCore.ResolveUniqueGameObject(
                scene.Scene,
                request.TargetObjectPath,
                "atomic rename object target");
            if (!target.transform.IsChildOf(avatar.transform) || ReferenceEquals(target, avatar))
            {
                throw new AtomicReferenceRenameException(
                    "The object target escaped the selected avatar subtree.");
            }
            var siblings = target.transform.parent.Cast<Transform>()
                .Where(candidate => !ReferenceEquals(candidate, target.transform)
                    && string.Equals(candidate.name, request.NewName, StringComparison.Ordinal))
                .ToList();
            if (siblings.Count != 0)
            {
                throw new AtomicReferenceRenameException(
                    "The renamed hierarchy path would be ambiguous.");
            }

            var targetId = StableObjectId(target, "atomic rename object target");
            var parentId = StableObjectId(target.transform.parent.gameObject, "atomic rename parent");
            context.Snapshot.TargetObject = target;
            context.Snapshot.Target = new JObject
            {
                ["kind"] = ObjectOperation,
                ["objectPath"] = request.TargetObjectPath,
                ["objectId"] = targetId,
                ["parentObjectId"] = parentId,
                ["newObjectPath"] = request.TargetAfter,
                ["identityDigest"] = Sha256Fields(
                    "vrcforge.atomic_object_target.v1",
                    scene.Guid,
                    scene.FileIdentity,
                    targetId,
                    parentId,
                    request.TargetObjectPath,
                    request.TargetAfter)
            };
            context.AddReference(
                "hierarchy_object",
                request.ScenePath,
                target,
                "m_Name",
                request.TargetObjectPath,
                request.TargetAfter,
                () =>
                {
                    Undo.RecordObject(target, "Rename VRCForge avatar object");
                    target.name = request.NewName;
                    EditorUtility.SetDirty(target);
                });

            var controllers = ResolveAvatarControllers(context.Snapshot.Descriptor);
            var clips = new HashSet<AnimationClip>();
            var masks = new HashSet<AvatarMask>();
            foreach (var controller in controllers)
            {
                CollectControllerObjectAssets(controller, clips, masks);
            }
            foreach (var clip in clips.OrderBy(AssetPath, StringComparer.Ordinal))
            {
                ScanAnimationClipObjectBindings(context, clip);
            }
            foreach (var mask in masks.OrderBy(AssetPath, StringComparer.Ordinal))
            {
                ScanAvatarMaskPaths(context, mask);
            }
        }

        private static void ScanAnimationClipObjectBindings(ScanContext context, AnimationClip clip)
        {
            RequireMutableAsset(clip, "animation clip");
            var request = context.Snapshot.Request;
            var avatarRelativeTarget = request.TargetObjectPath.Substring(
                request.AvatarPath.Length + 1);
            var avatarRelativeAfter = request.TargetAfter.Substring(request.AvatarPath.Length + 1);
            var floatBindings = AnimationUtility.GetCurveBindings(clip);
            for (var index = 0; index < floatBindings.Length; index++)
            {
                var binding = floatBindings[index];
                if (!PathMatchesPrefix(binding.path, avatarRelativeTarget))
                {
                    continue;
                }
                var beforeRelative = binding.path;
                var afterRelative = ReplacePathPrefix(
                    beforeRelative,
                    avatarRelativeTarget,
                    avatarRelativeAfter);
                var beforeFull = request.AvatarPath + "/" + beforeRelative;
                var afterFull = request.AvatarPath + "/" + afterRelative;
                var captured = binding;
                var curve = AnimationUtility.GetEditorCurve(clip, captured);
                context.AddReference(
                    "animation_binding",
                    AssetPath(clip),
                    clip,
                    "floatBindings[" + index.ToString(CultureInfo.InvariantCulture) + "]|"
                        + captured.type.AssemblyQualifiedName + "|" + captured.propertyName,
                    beforeFull,
                    afterFull,
                    () =>
                    {
                        Undo.RegisterCompleteObjectUndo(clip, "Rename VRCForge animation path");
                        AnimationUtility.SetEditorCurve(clip, captured, null);
                        captured.path = afterRelative;
                        AnimationUtility.SetEditorCurve(clip, captured, curve);
                        EditorUtility.SetDirty(clip);
                    },
                    2);
            }

            var objectBindings = AnimationUtility.GetObjectReferenceCurveBindings(clip);
            for (var index = 0; index < objectBindings.Length; index++)
            {
                var binding = objectBindings[index];
                if (!PathMatchesPrefix(binding.path, avatarRelativeTarget))
                {
                    continue;
                }
                var beforeRelative = binding.path;
                var afterRelative = ReplacePathPrefix(
                    beforeRelative,
                    avatarRelativeTarget,
                    avatarRelativeAfter);
                var beforeFull = request.AvatarPath + "/" + beforeRelative;
                var afterFull = request.AvatarPath + "/" + afterRelative;
                var captured = binding;
                var keys = AnimationUtility.GetObjectReferenceCurve(clip, captured);
                context.AddReference(
                    "animation_binding",
                    AssetPath(clip),
                    clip,
                    "objectBindings[" + index.ToString(CultureInfo.InvariantCulture) + "]|"
                        + captured.type.AssemblyQualifiedName + "|" + captured.propertyName,
                    beforeFull,
                    afterFull,
                    () =>
                    {
                        Undo.RegisterCompleteObjectUndo(clip, "Rename VRCForge animation path");
                        AnimationUtility.SetObjectReferenceCurve(clip, captured, null);
                        captured.path = afterRelative;
                        AnimationUtility.SetObjectReferenceCurve(clip, captured, keys);
                        EditorUtility.SetDirty(clip);
                    });
            }
        }

        private static void ScanAvatarMaskPaths(ScanContext context, AvatarMask mask)
        {
            RequireMutableAsset(mask, "avatar mask");
            var request = context.Snapshot.Request;
            var avatarRelativeTarget = request.TargetObjectPath.Substring(
                request.AvatarPath.Length + 1);
            var avatarRelativeAfter = request.TargetAfter.Substring(request.AvatarPath.Length + 1);
            for (var index = 0; index < mask.transformCount; index++)
            {
                var path = mask.GetTransformPath(index);
                if (!PathMatchesPrefix(path, avatarRelativeTarget))
                {
                    continue;
                }
                var afterRelative = ReplacePathPrefix(
                    path,
                    avatarRelativeTarget,
                    avatarRelativeAfter);
                var beforeFull = request.AvatarPath + "/" + path;
                var afterFull = request.AvatarPath + "/" + afterRelative;
                var capturedIndex = index;
                context.AddReference(
                    "avatar_mask_transform",
                    AssetPath(mask),
                    mask,
                    "transformPaths[" + index.ToString(CultureInfo.InvariantCulture) + "]",
                    beforeFull,
                    afterFull,
                    () =>
                    {
                        Undo.RegisterCompleteObjectUndo(mask, "Rename VRCForge avatar mask path");
                        mask.SetTransformPath(capturedIndex, afterRelative);
                        EditorUtility.SetDirty(mask);
                    });
            }
        }

        private static void ScanParameterRename(ScanContext context)
        {
            var request = context.Snapshot.Request;
            var descriptor = context.Snapshot.Descriptor;
            var descriptorSerialized = new SerializedObject(descriptor);
            var definitions = RequireObjectReference(
                descriptorSerialized,
                "expressionParameters",
                ExpressionParametersType,
                "expression parameter definitions");
            RequireMutableAsset(definitions, "expression parameter definitions");
            var definitionPath = AssetPath(definitions);
            var definitionGuid = AssetDatabase.AssetPathToGUID(definitionPath).ToLowerInvariant();
            ScanStringArrayField(
                context,
                definitions,
                "parameters",
                "name",
                "expression_parameter",
                request.OldParameterName,
                request.NewParameterName,
                true);
            var definitionRefs = context.References.Count(reference =>
                reference.Kind == "expression_parameter");
            if (definitionRefs != 1)
            {
                throw new AtomicReferenceRenameException(
                    "The selected avatar must define the old parameter exactly once.");
            }

            var rootMenu = OptionalObjectReference(
                descriptorSerialized,
                "expressionsMenu",
                ExpressionsMenuType,
                "expressions menu");
            if (rootMenu != null)
            {
                ScanExpressionMenus(context, rootMenu);
            }

            var controllers = ResolveAvatarControllers(descriptor);
            foreach (var controller in controllers)
            {
                ScanAnimatorControllerParameters(context, controller);
            }
            ScanAvatarSceneComponents(context);

            context.Snapshot.Target = new JObject
            {
                ["kind"] = ParameterOperation,
                ["oldParameterName"] = request.OldParameterName,
                ["newParameterName"] = request.NewParameterName,
                ["definitionAssetGuid"] = definitionGuid,
                ["identityDigest"] = Sha256Fields(
                    "vrcforge.atomic_parameter_target.v1",
                    definitionGuid,
                    StableObjectId(definitions, "expression parameter definitions"),
                    request.OldParameterName,
                    request.NewParameterName)
            };
        }

        private static void ScanExpressionMenus(ScanContext context, UnityEngine.Object root)
        {
            var queue = new Queue<UnityEngine.Object>();
            var visited = new HashSet<string>(StringComparer.Ordinal);
            queue.Enqueue(root);
            while (queue.Count != 0)
            {
                var menu = queue.Dequeue();
                RequireMutableAsset(menu, "expressions menu");
                var menuId = StableObjectId(menu, "expressions menu");
                if (!visited.Add(menuId))
                {
                    continue;
                }
                var serialized = new SerializedObject(menu);
                var controls = RequireArray(serialized, "controls", "expressions menu controls");
                for (var controlIndex = 0; controlIndex < controls.arraySize; controlIndex++)
                {
                    var control = controls.GetArrayElementAtIndex(controlIndex);
                    ScanFixedRelativeString(
                        context,
                        menu,
                        control,
                        "parameter.name",
                        "controls[" + controlIndex.ToString(CultureInfo.InvariantCulture)
                            + "].parameter.name",
                        "expression_menu_parameter");
                    var subParameters = control.FindPropertyRelative("subParameters");
                    if (subParameters == null || !subParameters.isArray)
                    {
                        throw new AtomicReferenceRenameException(
                            "The expressions menu sub-parameter layout is unsupported.");
                    }
                    for (var subIndex = 0; subIndex < subParameters.arraySize; subIndex++)
                    {
                        ScanFixedRelativeString(
                            context,
                            menu,
                            subParameters.GetArrayElementAtIndex(subIndex),
                            "name",
                            "controls[" + controlIndex.ToString(CultureInfo.InvariantCulture)
                                + "].subParameters[" + subIndex.ToString(CultureInfo.InvariantCulture)
                                + "].name",
                            "expression_menu_parameter");
                    }
                    var subMenu = control.FindPropertyRelative("subMenu");
                    if (subMenu == null || subMenu.propertyType != SerializedPropertyType.ObjectReference)
                    {
                        throw new AtomicReferenceRenameException(
                            "The expressions menu child layout is unsupported.");
                    }
                    if (subMenu.objectReferenceValue != null)
                    {
                        if (subMenu.objectReferenceValue.GetType().FullName != ExpressionsMenuType)
                        {
                            throw new AtomicReferenceRenameException(
                                "An expressions menu child has an unsupported type.");
                        }
                        queue.Enqueue(subMenu.objectReferenceValue);
                    }
                }
            }
        }

        private static void ScanAnimatorControllerParameters(
            ScanContext context,
            AnimatorController controller)
        {
            RequireMutableAsset(controller, "animator controller");
            var request = context.Snapshot.Request;
            var parameters = controller.parameters;
            for (var index = 0; index < parameters.Length; index++)
            {
                var parameter = parameters[index];
                if (parameter.name == request.NewParameterName)
                {
                    throw new AtomicReferenceRenameException(
                        "The new parameter already exists in an animator controller.");
                }
                if (parameter.name != request.OldParameterName)
                {
                    continue;
                }
                var capturedIndex = index;
                context.AddReference(
                    "animator_parameter",
                    AssetPath(controller),
                    controller,
                    "parameters[" + index.ToString(CultureInfo.InvariantCulture) + "].name",
                    request.OldParameterName,
                    request.NewParameterName,
                    () =>
                    {
                        Undo.RegisterCompleteObjectUndo(controller, "Rename VRCForge animator parameter");
                        var current = controller.parameters;
                        current[capturedIndex].name = request.NewParameterName;
                        controller.parameters = current;
                        EditorUtility.SetDirty(controller);
                    });
            }

            for (var layerIndex = 0; layerIndex < controller.layers.Length; layerIndex++)
            {
                var layer = controller.layers[layerIndex];
                if (layer.stateMachine == null)
                {
                    throw new AtomicReferenceRenameException(
                        "An animator controller layer has no state machine.");
                }
                ScanStateMachine(
                    context,
                    controller,
                    layer.stateMachine,
                    "layers[" + layerIndex.ToString(CultureInfo.InvariantCulture) + "].stateMachine",
                    new HashSet<int>());
            }
        }

        private static void ScanStateMachine(
            ScanContext context,
            AnimatorController controller,
            AnimatorStateMachine stateMachine,
            string propertyPrefix,
            HashSet<int> visited)
        {
            if (!visited.Add(stateMachine.GetInstanceID()))
            {
                return;
            }
            ScanTransitionArray(
                context,
                controller,
                stateMachine.anyStateTransitions,
                propertyPrefix + ".anyStateTransitions");
            ScanTransitionArray(
                context,
                controller,
                stateMachine.entryTransitions,
                propertyPrefix + ".entryTransitions");

            var states = stateMachine.states;
            for (var stateIndex = 0; stateIndex < states.Length; stateIndex++)
            {
                var state = states[stateIndex].state;
                if (state == null)
                {
                    throw new AtomicReferenceRenameException("An animator state is unresolved.");
                }
                var statePrefix = propertyPrefix + ".states["
                    + stateIndex.ToString(CultureInfo.InvariantCulture) + "]";
                ScanTransitionArray(
                    context,
                    controller,
                    state.transitions,
                    statePrefix + ".transitions");
                ScanAnimatorStateFields(context, controller, state, statePrefix);
                ScanMotion(context, controller, state.motion, statePrefix + ".motion", new HashSet<int>());
                ScanStateBehaviours(context, controller, state, statePrefix);
            }

            var children = stateMachine.stateMachines;
            for (var index = 0; index < children.Length; index++)
            {
                var child = children[index].stateMachine;
                if (child == null)
                {
                    throw new AtomicReferenceRenameException(
                        "A child animator state machine is unresolved.");
                }
                ScanStateMachine(
                    context,
                    controller,
                    child,
                    propertyPrefix + ".stateMachines["
                        + index.ToString(CultureInfo.InvariantCulture) + "]",
                    visited);
            }
        }

        private static void ScanTransitionArray(
            ScanContext context,
            AnimatorController controller,
            AnimatorTransitionBase[] transitions,
            string propertyPrefix)
        {
            var request = context.Snapshot.Request;
            for (var transitionIndex = 0; transitionIndex < transitions.Length; transitionIndex++)
            {
                var transition = transitions[transitionIndex];
                if (transition == null)
                {
                    throw new AtomicReferenceRenameException("An animator transition is unresolved.");
                }
                var conditions = transition.conditions;
                for (var conditionIndex = 0; conditionIndex < conditions.Length; conditionIndex++)
                {
                    if (conditions[conditionIndex].parameter != request.OldParameterName)
                    {
                        continue;
                    }
                    var capturedTransition = transition;
                    var capturedConditionIndex = conditionIndex;
                    context.AddReference(
                        "animator_condition",
                        AssetPath(controller),
                        transition,
                        propertyPrefix + "["
                            + transitionIndex.ToString(CultureInfo.InvariantCulture)
                            + "].conditions[" + conditionIndex.ToString(CultureInfo.InvariantCulture)
                            + "].parameter",
                        request.OldParameterName,
                        request.NewParameterName,
                        () =>
                        {
                            Undo.RegisterCompleteObjectUndo(
                                capturedTransition,
                                "Rename VRCForge animator condition");
                            var current = capturedTransition.conditions;
                            var condition = current[capturedConditionIndex];
                            condition.parameter = request.NewParameterName;
                            current[capturedConditionIndex] = condition;
                            capturedTransition.conditions = current;
                            EditorUtility.SetDirty(controller);
                        });
                }
            }
        }

        private static void ScanAnimatorStateFields(
            ScanContext context,
            AnimatorController controller,
            AnimatorState state,
            string propertyPrefix)
        {
            ScanAnimatorStateField(
                context,
                controller,
                state,
                propertyPrefix + ".speedParameter",
                () => state.speedParameter,
                value => state.speedParameter = value);
            ScanAnimatorStateField(
                context,
                controller,
                state,
                propertyPrefix + ".mirrorParameter",
                () => state.mirrorParameter,
                value => state.mirrorParameter = value);
            ScanAnimatorStateField(
                context,
                controller,
                state,
                propertyPrefix + ".cycleOffsetParameter",
                () => state.cycleOffsetParameter,
                value => state.cycleOffsetParameter = value);
            ScanAnimatorStateField(
                context,
                controller,
                state,
                propertyPrefix + ".timeParameter",
                () => state.timeParameter,
                value => state.timeParameter = value);
        }

        private static void ScanAnimatorStateField(
            ScanContext context,
            AnimatorController controller,
            AnimatorState state,
            string propertyPath,
            Func<string> read,
            Action<string> write)
        {
            var request = context.Snapshot.Request;
            if (read() != request.OldParameterName)
            {
                return;
            }
            context.AddReference(
                "animator_state_parameter",
                AssetPath(controller),
                state,
                propertyPath,
                request.OldParameterName,
                request.NewParameterName,
                () =>
                {
                    Undo.RegisterCompleteObjectUndo(state, "Rename VRCForge animator state parameter");
                    write(request.NewParameterName);
                    EditorUtility.SetDirty(controller);
                });
        }

        private static void ScanMotion(
            ScanContext context,
            AnimatorController controller,
            Motion motion,
            string propertyPrefix,
            HashSet<int> visited)
        {
            if (motion == null || !(motion is BlendTree tree) || !visited.Add(tree.GetInstanceID()))
            {
                return;
            }
            var request = context.Snapshot.Request;
            ScanBlendTreeField(
                context,
                controller,
                tree,
                propertyPrefix + ".blendParameter",
                () => tree.blendParameter,
                value => tree.blendParameter = value);
            ScanBlendTreeField(
                context,
                controller,
                tree,
                propertyPrefix + ".blendParameterY",
                () => tree.blendParameterY,
                value => tree.blendParameterY = value);
            var children = tree.children;
            for (var index = 0; index < children.Length; index++)
            {
                if (children[index].directBlendParameter == request.OldParameterName)
                {
                    var capturedIndex = index;
                    context.AddReference(
                        "blend_tree_parameter",
                        AssetPath(controller),
                        tree,
                        propertyPrefix + ".children["
                            + index.ToString(CultureInfo.InvariantCulture)
                            + "].directBlendParameter",
                        request.OldParameterName,
                        request.NewParameterName,
                        () =>
                        {
                            Undo.RegisterCompleteObjectUndo(tree, "Rename VRCForge blend tree parameter");
                            var current = tree.children;
                            var child = current[capturedIndex];
                            child.directBlendParameter = request.NewParameterName;
                            current[capturedIndex] = child;
                            tree.children = current;
                            EditorUtility.SetDirty(controller);
                        });
                }
                ScanMotion(
                    context,
                    controller,
                    children[index].motion,
                    propertyPrefix + ".children["
                        + index.ToString(CultureInfo.InvariantCulture) + "].motion",
                    visited);
            }
        }

        private static void ScanBlendTreeField(
            ScanContext context,
            AnimatorController controller,
            BlendTree tree,
            string propertyPath,
            Func<string> read,
            Action<string> write)
        {
            var request = context.Snapshot.Request;
            if (read() != request.OldParameterName)
            {
                return;
            }
            context.AddReference(
                "blend_tree_parameter",
                AssetPath(controller),
                tree,
                propertyPath,
                request.OldParameterName,
                request.NewParameterName,
                () =>
                {
                    Undo.RegisterCompleteObjectUndo(tree, "Rename VRCForge blend tree parameter");
                    write(request.NewParameterName);
                    EditorUtility.SetDirty(controller);
                });
        }

        private static void ScanStateBehaviours(
            ScanContext context,
            AnimatorController controller,
            AnimatorState state,
            string propertyPrefix)
        {
            var behaviours = state.behaviours;
            for (var behaviourIndex = 0; behaviourIndex < behaviours.Length; behaviourIndex++)
            {
                var behaviour = behaviours[behaviourIndex];
                if (behaviour == null)
                {
                    throw new AtomicReferenceRenameException("An animator state behaviour is unresolved.");
                }
                if (behaviour.GetType().FullName != ParameterDriverType)
                {
                    continue;
                }
                var serialized = new SerializedObject(behaviour);
                var parameters = RequireArray(
                    serialized,
                    "parameters",
                    "state behaviour parameters");
                for (var parameterIndex = 0; parameterIndex < parameters.arraySize; parameterIndex++)
                {
                    var element = parameters.GetArrayElementAtIndex(parameterIndex);
                    ScanFixedRelativeString(
                        context,
                        behaviour,
                        element,
                        "name",
                        propertyPrefix + ".behaviours["
                            + behaviourIndex.ToString(CultureInfo.InvariantCulture)
                            + "].parameters[" + parameterIndex.ToString(CultureInfo.InvariantCulture)
                            + "].name",
                        "state_behaviour_parameter",
                        AssetPath(controller));
                    ScanFixedRelativeString(
                        context,
                        behaviour,
                        element,
                        "source",
                        propertyPrefix + ".behaviours["
                            + behaviourIndex.ToString(CultureInfo.InvariantCulture)
                            + "].parameters[" + parameterIndex.ToString(CultureInfo.InvariantCulture)
                            + "].source",
                        "state_behaviour_parameter",
                        AssetPath(controller));
                }
            }
        }

        private static void ScanAvatarSceneComponents(ScanContext context)
        {
            var avatar = context.Snapshot.Avatar;
            var components = avatar.GetComponentsInChildren<Component>(true)
                .Where(component => component != null)
                .OrderBy(component => StableObjectId(component, "avatar component"), StringComparer.Ordinal)
                .ToList();
            foreach (var component in components)
            {
                var typeName = component.GetType().FullName;
                if (typeName == ContactReceiverType)
                {
                    ScanComponentString(
                        context,
                        component,
                        "parameter",
                        "contact_parameter");
                }
                else if (typeName == PhysBoneType)
                {
                    ScanComponentString(
                        context,
                        component,
                        "parameter",
                        "physbone_parameter");
                }
                else if (typeName == RegisteredFeatureType)
                {
                    ScanRegisteredComponent(context, component);
                }
            }
        }

        private static void ScanComponentString(
            ScanContext context,
            Component component,
            string serializedPath,
            string kind)
        {
            var serialized = new SerializedObject(component);
            var property = serialized.FindProperty(serializedPath);
            if (property == null || property.propertyType != SerializedPropertyType.String)
            {
                throw new AtomicReferenceRenameException(
                    "A fixed avatar component parameter layout is unsupported.");
            }
            AddSerializedStringReference(
                context,
                component,
                property,
                serializedPath,
                kind,
                context.Snapshot.Request.ScenePath);
        }

        private static void ScanRegisteredComponent(ScanContext context, Component component)
        {
            var serialized = new SerializedObject(component);
            var content = serialized.FindProperty("content");
            if (content == null || content.propertyType != SerializedPropertyType.ManagedReference)
            {
                throw new AtomicReferenceRenameException(
                    "A registered component has an unsupported fixed content layout.");
            }
            foreach (var path in FixedRegisteredStringPaths)
            {
                var property = serialized.FindProperty(path);
                if (property == null)
                {
                    continue;
                }
                if (property.propertyType != SerializedPropertyType.String)
                {
                    throw new AtomicReferenceRenameException(
                        "A registered component fixed field has an unsupported type.");
                }
                AddSerializedStringReference(
                    context,
                    component,
                    property,
                    path,
                    "registered_component_parameter",
                    context.Snapshot.Request.ScenePath);
            }
            var globalParams = serialized.FindProperty("content.globalParams");
            if (globalParams != null)
            {
                if (!globalParams.isArray)
                {
                    throw new AtomicReferenceRenameException(
                        "A registered component parameter list has an unsupported type.");
                }
                for (var index = 0; index < globalParams.arraySize; index++)
                {
                    var property = globalParams.GetArrayElementAtIndex(index);
                    if (property.propertyType != SerializedPropertyType.String)
                    {
                        throw new AtomicReferenceRenameException(
                            "A registered component parameter entry has an unsupported type.");
                    }
                    AddSerializedStringReference(
                        context,
                        component,
                        property,
                        "content.globalParams[" + index.ToString(CultureInfo.InvariantCulture) + "]",
                        "registered_component_parameter",
                        context.Snapshot.Request.ScenePath);
                }
            }
        }

        private static void ScanStringArrayField(
            ScanContext context,
            UnityEngine.Object owner,
            string arrayPath,
            string fieldPath,
            string kind,
            string before,
            string after,
            bool rejectAfter)
        {
            var serialized = new SerializedObject(owner);
            var array = RequireArray(serialized, arrayPath, arrayPath);
            for (var index = 0; index < array.arraySize; index++)
            {
                var property = array.GetArrayElementAtIndex(index).FindPropertyRelative(fieldPath);
                if (property == null || property.propertyType != SerializedPropertyType.String)
                {
                    throw new AtomicReferenceRenameException(
                        "A fixed parameter definition layout is unsupported.");
                }
                if (rejectAfter && property.stringValue == after)
                {
                    throw new AtomicReferenceRenameException(
                        "The new parameter is already defined.");
                }
                if (property.stringValue != before)
                {
                    continue;
                }
                AddSerializedStringReference(
                    context,
                    owner,
                    property,
                    arrayPath + "[" + index.ToString(CultureInfo.InvariantCulture) + "]." + fieldPath,
                    kind,
                    AssetPath(owner));
            }
        }

        private static void ScanFixedRelativeString(
            ScanContext context,
            UnityEngine.Object owner,
            SerializedProperty root,
            string relativePath,
            string propertyPath,
            string kind,
            string assetPath = null)
        {
            var property = root.FindPropertyRelative(relativePath);
            if (property == null || property.propertyType != SerializedPropertyType.String)
            {
                throw new AtomicReferenceRenameException(
                    "A fixed parameter reference layout is unsupported.");
            }
            AddSerializedStringReference(
                context,
                owner,
                property,
                propertyPath,
                kind,
                assetPath ?? AssetPath(owner));
        }

        private static void AddSerializedStringReference(
            ScanContext context,
            UnityEngine.Object owner,
            SerializedProperty property,
            string propertyPath,
            string kind,
            string assetPath)
        {
            var request = context.Snapshot.Request;
            if (property.stringValue != request.OldParameterName)
            {
                return;
            }
            var serializedPath = property.propertyPath;
            context.AddReference(
                kind,
                assetPath,
                owner,
                propertyPath,
                request.OldParameterName,
                request.NewParameterName,
                () =>
                {
                    Undo.RegisterCompleteObjectUndo(owner, "Rename VRCForge parameter reference");
                    var serialized = new SerializedObject(owner);
                    var current = serialized.FindProperty(serializedPath);
                    if (current == null || current.propertyType != SerializedPropertyType.String
                        || current.stringValue != request.OldParameterName)
                    {
                        throw new AtomicReferenceRenameException(
                            "A fixed parameter reference changed before apply.");
                    }
                    current.stringValue = request.NewParameterName;
                    if (!serialized.ApplyModifiedPropertiesWithoutUndo())
                    {
                        throw new AtomicReferenceRenameException(
                            "A fixed parameter reference did not accept its mutation.");
                    }
                    EditorUtility.SetDirty(owner);
                });
        }

        private static void FinalizePreview(ScanContext context)
        {
            var snapshot = context.Snapshot;
            if (context.References.Count == 0 || context.References.Count > MaxReferences)
            {
                throw new AtomicReferenceRenameException(
                    "The atomic reference rename mutation set is empty or exceeds its fixed bound.");
            }
            context.References.Sort(RenameReference.Compare);
            if (context.References.Zip(
                    context.References.Skip(1),
                    (left, right) => RenameReference.Compare(left, right) == 0)
                .Any(equal => equal))
            {
                throw new AtomicReferenceRenameException(
                    "The atomic reference rename mutation set contains duplicates.");
            }

            var grouped = context.References
                .GroupBy(reference => reference.AssetPath, StringComparer.Ordinal)
                .OrderBy(group => group.Key, StringComparer.Ordinal)
                .ToList();
            if (grouped.Count == 0 || grouped.Count > MaxAssets)
            {
                throw new AtomicReferenceRenameException(
                    "The atomic reference rename asset set exceeds its fixed bound.");
            }
            foreach (var group in grouped)
            {
                var inventoryAsset = context.Inventory.Assets.FirstOrDefault(asset =>
                    asset.Path == group.Key);
                if (inventoryAsset == null)
                {
                    throw new AtomicReferenceRenameException(
                        "A mutation target is outside the complete project asset inventory.");
                }
                snapshot.Assets.Add(new RenameAssetReceipt
                {
                    AssetPath = inventoryAsset.Path,
                    AssetGuid = inventoryAsset.Evidence.Guid,
                    FileDigest = inventoryAsset.Evidence.File.Digest,
                    FileLength = checked((long)inventoryAsset.Evidence.File.Length),
                    MetaDigest = inventoryAsset.Evidence.Meta.Digest,
                    FileIdentity = inventoryAsset.Evidence.File.Identity,
                    MetaIdentity = inventoryAsset.Evidence.Meta.Identity,
                    MutationCount = group.Count()
                });
            }
            snapshot.Assets.Sort(RenameAssetReceipt.Compare);
            if (snapshot.Assets.Select(asset => asset.AssetGuid).Distinct(StringComparer.Ordinal).Count()
                    != snapshot.Assets.Count
                || snapshot.Assets.Select(asset => asset.FileIdentity)
                    .Distinct(StringComparer.Ordinal).Count() != snapshot.Assets.Count)
            {
                throw new AtomicReferenceRenameException(
                    "The atomic reference rename asset set is aliased.");
            }

            var token = snapshot.Request.OperationKind == ObjectOperation
                ? snapshot.Request.OldName
                : snapshot.Request.OldParameterName;
            var replacement = snapshot.Request.OperationKind == ObjectOperation
                ? snapshot.Request.NewName
                : snapshot.Request.NewParameterName;
            var knownByAsset = context.References
                .GroupBy(reference => reference.AssetPath, StringComparer.Ordinal)
                .ToDictionary(
                    group => group.Key,
                    group => group.Sum(reference => reference.RawOccurrenceCount),
                    StringComparer.Ordinal);
            var unknown = 0;
            var firstUnknown = string.Empty;
            foreach (var asset in context.Inventory.Assets)
            {
                var rawOld = CountRawToken(asset.Path, asset.Evidence, token);
                var rawNew = CountRawToken(asset.Path, asset.Evidence, replacement);
                var known = knownByAsset.TryGetValue(asset.Path, out var value) ? value : 0;
                if (rawOld != known)
                {
                    unknown += Math.Abs(rawOld - known);
                    if (firstUnknown.Length == 0)
                    {
                        firstUnknown = asset.Path + " old-count "
                            + rawOld.ToString(CultureInfo.InvariantCulture) + "/"
                            + known.ToString(CultureInfo.InvariantCulture);
                    }
                }
                if (rawNew != 0 && known != 0)
                {
                    unknown += rawNew;
                    if (firstUnknown.Length == 0)
                    {
                        firstUnknown = asset.Path + " colliding-count "
                            + rawNew.ToString(CultureInfo.InvariantCulture);
                    }
                }
            }
            if (unknown != 0)
            {
                throw new AtomicReferenceRenameException(
                    "An old or colliding reference exists outside the fixed migration schema ("
                    + firstUnknown + ").");
            }
            foreach (var asset in snapshot.Assets)
            {
                var replacementCount = knownByAsset[asset.AssetPath];
                if (replacementCount <= 0 || replacementCount > MaxReferences * 2)
                {
                    throw new AtomicReferenceRenameException(
                        "The target byte replacement set exceeds its fixed bound.");
                }
                var target = ComputeExpectedTargetFile(
                    asset,
                    token,
                    replacement,
                    replacementCount);
                asset.RawReplacementCount = replacementCount;
                asset.TargetFileDigest = target.Digest;
                asset.TargetFileLength = target.Length;
            }

            snapshot.References.AddRange(context.References);
            snapshot.BeforeStateDigest = ComputeStateDigest(snapshot, false);
            snapshot.TargetStateDigest = ComputeStateDigest(snapshot, true);
            if (snapshot.BeforeStateDigest == snapshot.TargetStateDigest)
            {
                throw new AtomicReferenceRenameException(
                    "The atomic reference rename target state is unchanged.");
            }
            snapshot.PlanDigest = ComputePlanDigest(snapshot.BuildCanonicalPlan());
        }

        private static object Apply(RenameSnapshot snapshot, ref bool mutationStarted)
        {
            var immediate = BuildPreview(snapshot.Request);
            if (immediate.PlanDigest != snapshot.PlanDigest
                || immediate.Scene.Handle != snapshot.Scene.Handle
                || immediate.Scene.FileIdentity != snapshot.Scene.FileIdentity
                || immediate.Scene.MetaIdentity != snapshot.Scene.MetaIdentity)
            {
                throw new AtomicReferenceRenameException(
                    "Atomic reference rename state changed immediately before apply.");
            }

            var backups = CaptureBackups(snapshot.Assets);
            RequireNoDirtyProjectAssets();
            var sceneSetup = EditorSceneManager.GetSceneManagerSetup();
            var sceneWillChange = snapshot.Assets.Any(asset =>
                asset.AssetPath == snapshot.Request.ScenePath);
            Undo.IncrementCurrentGroup();
            var undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("Atomic VRCForge reference rename");
            try
            {
                mutationStarted = true;
                foreach (var reference in snapshot.References)
                {
                    reference.Apply();
                }
                SavePlannedAssets(snapshot);
                if (sceneWillChange)
                {
                    EditorSceneManager.MarkSceneDirty(snapshot.Scene.Scene);
                    if (!EditorSceneManager.SaveScene(snapshot.Scene.Scene))
                    {
                        throw new AtomicReferenceRenameException(
                            "The atomic reference rename scene could not be saved.");
                    }
                }
                else if (snapshot.Scene.Scene.isDirty)
                {
                    throw new AtomicReferenceRenameException(
                        "An asset-only atomic rename unexpectedly changed the scene.");
                }
                Undo.CollapseUndoOperations(undoGroup);
                if (snapshot.Scene.Scene.isDirty)
                {
                    if (!sceneWillChange
                        || !EditorSceneManager.SaveScene(snapshot.Scene.Scene))
                    {
                        throw new AtomicReferenceRenameException(
                            "The atomic reference rename scene could not be finalized.");
                    }
                }

                var reverse = BuildPreview(snapshot.Request.Reverse());
                VerifyReverseReadback(snapshot, reverse);
                var afterScene = SceneObjectCopyCore.ResolveSavedScene(
                    snapshot.Request.ScenePath,
                    "atomic reference rename saved scene");
                VerifySavedEvidence(snapshot, reverse, afterScene);
                mutationStarted = false;
                return new SuccessResponse(
                    "Atomic reference rename completed.",
                    BuildApplyPayload(snapshot, reverse, afterScene));
            }
            catch (Exception exception)
            {
                var restored = RestoreFailedApply(
                    snapshot,
                    backups,
                    sceneSetup,
                    undoGroup);
                mutationStarted = false;
                return Failure(exception, true, restored);
            }
        }

        private static void SavePlannedAssets(RenameSnapshot snapshot)
        {
            foreach (var group in snapshot.References
                .Where(reference => reference.AssetPath != snapshot.Request.ScenePath)
                .GroupBy(reference => reference.AssetPath, StringComparer.Ordinal)
                .OrderBy(group => group.Key, StringComparer.Ordinal))
            {
                var owners = group
                    .Select(reference => reference.Owner)
                    .Where(owner => owner != null)
                    .GroupBy(owner => owner.GetInstanceID())
                    .Select(ownerGroup => ownerGroup.First())
                    .ToList();
                if (owners.Count == 0)
                {
                    throw new AtomicReferenceRenameException(
                        "A planned asset has no fixed-schema persistence owner.");
                }
                foreach (var owner in owners)
                {
                    if (!AssetDatabase.Contains(owner)
                        || AssetDatabase.GetAssetPath(owner) != group.Key)
                    {
                        throw new AtomicReferenceRenameException(
                            "A planned persistence owner escaped its approved asset.");
                    }
                }
                var asset = AssetDatabase.LoadMainAssetAtPath(group.Key);
                if (asset == null || AssetDatabase.GetAssetPath(asset) != group.Key)
                {
                    throw new AtomicReferenceRenameException(
                        "A planned asset has no stable main persistence object.");
                }
                AssetDatabase.SaveAssetIfDirty(asset);
                if (EditorUtility.IsDirty(asset) || owners.Any(EditorUtility.IsDirty))
                {
                    throw new AtomicReferenceRenameException(
                        "A planned asset remained dirty after its bounded save.");
                }
            }
        }

        private static void VerifyReverseReadback(RenameSnapshot before, RenameSnapshot reverse)
        {
            if (reverse.References.Count != before.References.Count
                || reverse.Assets.Count != before.Assets.Count)
            {
                throw new AtomicReferenceRenameException(
                    "Atomic reference rename persisted reference coverage drifted.");
            }
            for (var index = 0; index < before.References.Count; index++)
            {
                var expected = before.References[index];
                var actual = reverse.References[index];
                if (expected.Kind != actual.Kind
                    || expected.AssetPath != actual.AssetPath
                    || expected.ObjectId != actual.ObjectId
                    || expected.PropertyPath != actual.PropertyPath
                    || expected.Before != actual.After
                    || expected.After != actual.Before)
                {
                    throw new AtomicReferenceRenameException(
                        "Atomic reference rename persisted readback is not exact.");
                }
            }
            if (reverse.AssemblySetDigest != before.AssemblySetDigest)
            {
                throw new AtomicReferenceRenameException(
                    "The loaded assembly set changed during atomic reference rename.");
            }
            var baselineAssets = before.Inventory.Assets.ToDictionary(
                asset => asset.Path,
                StringComparer.Ordinal);
            for (var index = 0; index < before.Assets.Count; index++)
            {
                var expected = before.Assets[index];
                var actual = reverse.Assets[index];
                if (!baselineAssets.TryGetValue(expected.AssetPath, out var baseline)
                    || expected.AssetPath != actual.AssetPath
                    || expected.AssetGuid != actual.AssetGuid
                    || expected.MetaDigest != actual.MetaDigest
                    || expected.MutationCount != actual.MutationCount
                    || expected.RawReplacementCount != actual.RawReplacementCount
                    || actual.FileDigest != expected.TargetFileDigest
                    || actual.FileLength != expected.TargetFileLength
                    || actual.TargetFileDigest != expected.FileDigest
                    || actual.TargetFileLength != expected.FileLength
                    || actual.TargetFileLength != (long)baseline.Evidence.File.Length
                    || expected.MetaIdentity != actual.MetaIdentity)
                {
                    throw new AtomicReferenceRenameException(
                        "Atomic reference rename persisted asset projection is not exact.");
                }
            }
        }

        private static void VerifySavedEvidence(
            RenameSnapshot before,
            RenameSnapshot reverse,
            SavedSceneSnapshot afterScene)
        {
            VerifyExactInventoryDelta(before, reverse);
            var sceneChanged = before.Assets.Any(asset =>
                asset.AssetPath == before.Request.ScenePath);
            var expectedSceneDigest = sceneChanged
                ? before.Assets.Single(asset =>
                    asset.AssetPath == before.Request.ScenePath).TargetFileDigest
                : before.Scene.FileDigest;
            if (afterScene.Guid != before.Scene.Guid
                || afterScene.Handle != before.Scene.Handle
                || afterScene.MetaDigest != before.Scene.MetaDigest
                || afterScene.MetaIdentity != before.Scene.MetaIdentity
                || afterScene.FileDigest != expectedSceneDigest
                || (!sceneChanged && afterScene.FileDigest != before.Scene.FileDigest)
                || (!sceneChanged && afterScene.FileIdentity != before.Scene.FileIdentity)
                || afterScene.Scene.isDirty)
            {
                throw new AtomicReferenceRenameException(
                    "Atomic reference rename saved scene evidence is invalid (guid="
                    + (afterScene.Guid == before.Scene.Guid ? "same" : "changed")
                    + ",handle=" + (afterScene.Handle == before.Scene.Handle ? "same" : "changed")
                    + ",file=" + (afterScene.FileDigest == before.Scene.FileDigest ? "same" : "changed")
                    + ",fileIdentity="
                    + (afterScene.FileIdentity == before.Scene.FileIdentity ? "same" : "changed")
                    + ",meta=" + (afterScene.MetaDigest == before.Scene.MetaDigest ? "same" : "changed")
                    + ",metaIdentity="
                    + (afterScene.MetaIdentity == before.Scene.MetaIdentity ? "same" : "changed")
                    + ",dirty=" + (afterScene.Scene.isDirty ? "true" : "false") + ").");
            }
            foreach (var asset in before.Assets)
            {
                var after = SceneObjectCopyCore.ReadStableAssetEvidence(
                    asset.AssetPath,
                    "atomic reference rename asset readback");
                if (after.Guid != asset.AssetGuid
                    || after.Meta.Identity != asset.MetaIdentity
                    || after.Meta.Digest != asset.MetaDigest
                    || after.File.Digest != asset.TargetFileDigest
                    || (long)after.File.Length != asset.TargetFileLength)
                {
                    throw new AtomicReferenceRenameException(
                        "Atomic reference rename saved asset evidence is invalid.");
                }
            }
            if (reverse.AssetInventoryDigest == before.AssetInventoryDigest)
            {
                throw new AtomicReferenceRenameException(
                    "Atomic reference rename did not change the complete asset inventory.");
            }
        }

        private static void VerifyExactInventoryDelta(
            RenameSnapshot before,
            RenameSnapshot after)
        {
            if (before.Inventory == null
                || after.Inventory == null
                || before.Inventory.PackageDigest != after.Inventory.PackageDigest
                || before.Inventory.Assets.Count != after.Inventory.Assets.Count)
            {
                throw new AtomicReferenceRenameException(
                    "The complete asset inventory changed outside the approved plan.");
            }
            VerifyExactFileSystemDelta(before, after);
            var planned = before.Assets.ToDictionary(
                asset => asset.AssetPath,
                StringComparer.Ordinal);
            for (var index = 0; index < before.Inventory.Assets.Count; index++)
            {
                var expected = before.Inventory.Assets[index];
                var actual = after.Inventory.Assets[index];
                if (expected.Path != actual.Path)
                {
                    throw new AtomicReferenceRenameException(
                        "The complete asset inventory path set changed outside the approved plan.");
                }
                if (!planned.TryGetValue(expected.Path, out var approved))
                {
                    if (!StableAssetEvidenceMatches(expected.Evidence, actual.Evidence))
                    {
                        throw new AtomicReferenceRenameException(
                            "An unapproved project asset changed during atomic reference rename.");
                    }
                    continue;
                }
                if (expected.Evidence.Guid != actual.Evidence.Guid
                    || actual.Evidence.File.Digest != approved.TargetFileDigest
                    || (long)actual.Evidence.File.Length != approved.TargetFileLength
                    || actual.Evidence.File.LinkCount != 1
                    || expected.Evidence.Meta.Digest != actual.Evidence.Meta.Digest
                    || expected.Evidence.Meta.Identity != actual.Evidence.Meta.Identity
                    || expected.Evidence.Meta.LinkCount != 1
                    || actual.Evidence.Meta.LinkCount != 1
                    || expected.Evidence.Meta.Length != actual.Evidence.Meta.Length)
                {
                    throw new AtomicReferenceRenameException(
                        "An approved project asset changed outside its bounded file body.");
                }
            }
        }

        private static void VerifyExactFileSystemDelta(
            RenameSnapshot before,
            RenameSnapshot after)
        {
            if (before.Inventory.FileSystem == null
                || after.Inventory.FileSystem == null
                || before.Inventory.FileSystem.Entries.Count
                    != after.Inventory.FileSystem.Entries.Count)
            {
                throw new AtomicReferenceRenameException(
                    "The Assets filesystem changed outside the approved plan.");
            }
            var planned = before.Assets.ToDictionary(
                asset => asset.AssetPath,
                StringComparer.Ordinal);
            for (var index = 0; index < before.Inventory.FileSystem.Entries.Count; index++)
            {
                var expected = before.Inventory.FileSystem.Entries[index];
                var actual = after.Inventory.FileSystem.Entries[index];
                if (expected.Kind != actual.Kind || expected.Path != actual.Path)
                {
                    throw new AtomicReferenceRenameException(
                        "The Assets filesystem path set changed outside the approved plan.");
                }
                if (expected.Kind == "file"
                    && planned.TryGetValue(expected.Path, out var approved))
                {
                    if (actual.Digest != approved.TargetFileDigest
                        || actual.Length != approved.TargetFileLength)
                    {
                        throw new AtomicReferenceRenameException(
                            "An approved asset did not match its exact target bytes.");
                    }
                }
                else if (expected.Digest != actual.Digest
                    || expected.Length != actual.Length)
                {
                    throw new AtomicReferenceRenameException(
                        "An unapproved Assets filesystem entry changed.");
                }
            }
        }

        private static List<AssetBackup> CaptureBackups(IEnumerable<RenameAssetReceipt> assets)
        {
            var result = new List<AssetBackup>();
            long total = 0;
            foreach (var asset in assets)
            {
                var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(asset.AssetPath);
                var file = File.ReadAllBytes(absolute);
                var meta = File.ReadAllBytes(absolute + ".meta");
                if (file.LongLength > MaxBackupAssetBytes || meta.LongLength > MaxBackupAssetBytes)
                {
                    throw new AtomicReferenceRenameException(
                        "A mutation target exceeds the fixed rollback bound.");
                }
                total += file.LongLength + meta.LongLength;
                if (total > MaxBackupTotalBytes)
                {
                    throw new AtomicReferenceRenameException(
                        "The rollback set exceeds its fixed bound.");
                }
                var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(
                    asset.AssetPath,
                    "atomic reference rename rollback capture");
                if (evidence.File.Digest != asset.FileDigest
                    || evidence.Meta.Digest != asset.MetaDigest
                    || evidence.File.Identity != asset.FileIdentity
                    || evidence.Meta.Identity != asset.MetaIdentity
                    || evidence.File.LinkCount != 1
                    || evidence.Meta.LinkCount != 1
                    || Sha256Bytes(file) != asset.FileDigest
                    || Sha256Bytes(meta) != asset.MetaDigest)
                {
                    throw new AtomicReferenceRenameException(
                        "A mutation target changed before rollback capture.");
                }
                result.Add(new AssetBackup
                {
                    AssetPath = asset.AssetPath,
                    FileBytes = file,
                    MetaBytes = meta,
                    Evidence = evidence
                });
            }
            return result;
        }

        private static bool RestoreFailedApply(
            RenameSnapshot snapshot,
            IEnumerable<AssetBackup> backups,
            SceneSetup[] sceneSetup,
            int undoGroup)
        {
            try
            {
                Undo.RevertAllDownToGroup(undoGroup);
            }
            catch
            {
                // Byte restoration below is authoritative.
            }
            try
            {
                foreach (var backup in backups)
                {
                    var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(backup.AssetPath);
                    WriteExactFile(absolute, backup.FileBytes);
                    WriteExactFile(absolute + ".meta", backup.MetaBytes);
                }
                foreach (var backup in backups)
                {
                    AssetDatabase.ImportAsset(
                        backup.AssetPath,
                        ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                }
                EditorSceneManager.RestoreSceneManagerSetup(sceneSetup);
                foreach (var backup in backups)
                {
                    var currentBefore = SceneObjectCopyCore.ReadStableAssetEvidence(
                        backup.AssetPath,
                        "atomic reference rename rollback verification");
                    var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(backup.AssetPath);
                    var fileBytes = File.ReadAllBytes(absolute);
                    var metaBytes = File.ReadAllBytes(absolute + ".meta");
                    var currentAfter = SceneObjectCopyCore.ReadStableAssetEvidence(
                        backup.AssetPath,
                        "atomic reference rename rollback verification");
                    if (!StableAssetEvidenceMatches(currentBefore, currentAfter)
                        || !RestoredAssetEvidenceMatches(currentAfter, backup.Evidence)
                        || !fileBytes.SequenceEqual(backup.FileBytes)
                        || !metaBytes.SequenceEqual(backup.MetaBytes)
                        || Sha256Bytes(fileBytes) != backup.Evidence.File.Digest
                        || Sha256Bytes(metaBytes) != backup.Evidence.Meta.Digest)
                    {
                        return false;
                    }
                }
                var scene = SceneObjectCopyCore.ResolveSavedScene(
                    snapshot.Request.ScenePath,
                    "atomic reference rename rollback scene");
                if (scene.Guid != snapshot.Scene.Guid
                    || scene.FileDigest != snapshot.Scene.FileDigest
                    || scene.MetaDigest != snapshot.Scene.MetaDigest
                    || scene.MetaIdentity != snapshot.Scene.MetaIdentity
                    || scene.Scene.isDirty)
                {
                    return false;
                }
                var restored = BuildPreview(snapshot.Request);
                return RestoredStateMatches(snapshot, restored);
            }
            catch
            {
                return false;
            }
        }

        private static bool RestoredStateMatches(RenameSnapshot expected, RenameSnapshot actual)
        {
            if (!RenameRequestsMatch(expected.Request, actual.Request)
                || !RestoredSceneMatches(expected.Scene, actual.Scene)
                || expected.AvatarObjectId != actual.AvatarObjectId
                || expected.AssemblySetDigest != actual.AssemblySetDigest
                || !RestoredInventoryMatches(expected, actual)
                || expected.ObjectCount != actual.ObjectCount
                || !RestoredTargetMatches(expected, actual)
                || expected.References.Count != actual.References.Count
                || expected.Assets.Count != actual.Assets.Count)
            {
                return false;
            }
            for (var index = 0; index < expected.References.Count; index++)
            {
                var left = expected.References[index];
                var right = actual.References[index];
                if (left.Kind != right.Kind
                    || left.AssetPath != right.AssetPath
                    || left.ObjectId != right.ObjectId
                    || left.PropertyPath != right.PropertyPath
                    || left.Before != right.Before
                    || left.After != right.After)
                {
                    return false;
                }
            }
            for (var index = 0; index < expected.Assets.Count; index++)
            {
                var left = expected.Assets[index];
                var right = actual.Assets[index];
                if (left.AssetPath != right.AssetPath
                    || left.AssetGuid != right.AssetGuid
                    || left.FileDigest != right.FileDigest
                    || left.FileLength != right.FileLength
                    || left.TargetFileDigest != right.TargetFileDigest
                    || left.TargetFileLength != right.TargetFileLength
                    || left.MetaDigest != right.MetaDigest
                    || left.MetaIdentity != right.MetaIdentity
                    || left.MutationCount != right.MutationCount
                    || left.RawReplacementCount != right.RawReplacementCount)
                {
                    return false;
                }
            }
            return true;
        }

        private static bool RestoredInventoryMatches(
            RenameSnapshot expected,
            RenameSnapshot actual)
        {
            if (expected?.Inventory == null
                || actual?.Inventory == null
                || expected.Inventory.PackageDigest != actual.Inventory.PackageDigest
                || expected.Inventory.Assets.Count != actual.Inventory.Assets.Count
                || !FileSystemInventoryMatches(
                    expected.Inventory.FileSystem,
                    actual.Inventory.FileSystem))
            {
                return false;
            }
            var planned = new HashSet<string>(
                expected.Assets.Select(asset => asset.AssetPath),
                StringComparer.Ordinal);
            for (var index = 0; index < expected.Inventory.Assets.Count; index++)
            {
                var before = expected.Inventory.Assets[index];
                var after = actual.Inventory.Assets[index];
                if (before.Path != after.Path)
                {
                    return false;
                }
                if (planned.Contains(before.Path))
                {
                    if (!RestoredAssetEvidenceMatches(after.Evidence, before.Evidence))
                    {
                        return false;
                    }
                }
                else if (!StableAssetEvidenceMatches(before.Evidence, after.Evidence))
                {
                    return false;
                }
            }
            return true;
        }

        private static bool FileSystemInventoryMatches(
            FileSystemInventory expected,
            FileSystemInventory actual)
        {
            if (expected == null
                || actual == null
                || expected.Digest != actual.Digest
                || expected.Entries.Count != actual.Entries.Count)
            {
                return false;
            }
            for (var index = 0; index < expected.Entries.Count; index++)
            {
                var before = expected.Entries[index];
                var after = actual.Entries[index];
                if (before.Kind != after.Kind
                    || before.Path != after.Path
                    || before.Digest != after.Digest
                    || before.Length != after.Length)
                {
                    return false;
                }
            }
            return true;
        }

        private static bool RenameRequestsMatch(RenameRequest left, RenameRequest right)
        {
            return left != null
                && right != null
                && left.OperationKind == right.OperationKind
                && left.ScenePath == right.ScenePath
                && left.AvatarPath == right.AvatarPath
                && left.TargetObjectPath == right.TargetObjectPath
                && left.NewName == right.NewName
                && left.OldName == right.OldName
                && left.OldParameterName == right.OldParameterName
                && left.NewParameterName == right.NewParameterName
                && left.TargetBefore == right.TargetBefore
                && left.TargetAfter == right.TargetAfter;
        }

        private static bool RestoredSceneMatches(SavedSceneSnapshot left, SavedSceneSnapshot right)
        {
            return left != null
                && right != null
                && left.Path == right.Path
                && left.Guid == right.Guid
                && left.Handle == right.Handle
                && left.FileDigest == right.FileDigest
                && left.MetaDigest == right.MetaDigest
                && left.MetaIdentity == right.MetaIdentity
                && !right.Scene.isDirty;
        }

        private static bool RestoredTargetMatches(RenameSnapshot expected, RenameSnapshot actual)
        {
            if (expected?.Target == null || actual?.Target == null)
            {
                return false;
            }
            var keys = expected.Request.OperationKind == ObjectOperation
                ? new[]
                {
                    "kind",
                    "objectPath",
                    "objectId",
                    "parentObjectId",
                    "newObjectPath",
                    "identityDigest"
                }
                : new[]
                {
                    "kind",
                    "oldParameterName",
                    "newParameterName",
                    "definitionAssetGuid",
                    "identityDigest"
                };
            if (!expected.Target.Properties().Select(property => property.Name).SequenceEqual(keys)
                || !actual.Target.Properties().Select(property => property.Name).SequenceEqual(keys))
            {
                return false;
            }
            return keys
                .Where(key => expected.Request.OperationKind != ObjectOperation
                    || key != "identityDigest")
                .All(key => JToken.DeepEquals(expected.Target[key], actual.Target[key]));
        }

        private static bool RestoredAssetEvidenceMatches(
            StableAssetEvidence current,
            StableAssetEvidence expected)
        {
            return current != null
                && expected != null
                && current.Guid == expected.Guid
                && current.File != null
                && expected.File != null
                && current.File.Digest == expected.File.Digest
                && current.File.LinkCount == 1
                && expected.File.LinkCount == 1
                && current.File.Length == expected.File.Length
                && current.Meta != null
                && expected.Meta != null
                && current.Meta.Digest == expected.Meta.Digest
                && current.Meta.Identity == expected.Meta.Identity
                && current.Meta.LinkCount == 1
                && expected.Meta.LinkCount == 1
                && current.Meta.Length == expected.Meta.Length;
        }

        private static bool StableAssetEvidenceMatches(
            StableAssetEvidence left,
            StableAssetEvidence right)
        {
            return left != null
                && right != null
                && left.Guid == right.Guid
                && StableFileEvidenceMatches(left.File, right.File)
                && StableFileEvidenceMatches(left.Meta, right.Meta);
        }

        private static bool StableFileEvidenceMatches(
            StableFileEvidence left,
            StableFileEvidence right)
        {
            return left != null
                && right != null
                && left.Digest == right.Digest
                && left.Identity == right.Identity
                && left.LinkCount == right.LinkCount
                && left.Length == right.Length;
        }

        private static void WriteExactFile(string path, byte[] bytes)
        {
            using (var stream = new FileStream(
                path,
                FileMode.Create,
                FileAccess.Write,
                FileShare.None))
            {
                stream.Write(bytes, 0, bytes.Length);
                stream.Flush(true);
            }
        }

        private static JObject BuildApplyPayload(
            RenameSnapshot before,
            RenameSnapshot reverse,
            SavedSceneSnapshot afterScene)
        {
            var scene = new JObject
            {
                ["path"] = before.Scene.Path,
                ["guid"] = before.Scene.Guid,
                ["handle"] = before.Scene.Handle,
                ["fileDigestBefore"] = before.Scene.FileDigest,
                ["fileDigestAfter"] = afterScene.FileDigest,
                ["fileIdentityBefore"] = before.Scene.FileIdentity,
                ["fileIdentityAfter"] = afterScene.FileIdentity,
                ["metaDigestBefore"] = before.Scene.MetaDigest,
                ["metaDigestAfter"] = afterScene.MetaDigest,
                ["metaIdentityBefore"] = before.Scene.MetaIdentity,
                ["metaIdentityAfter"] = afterScene.MetaIdentity,
                ["dirtyBefore"] = false,
                ["dirtyAfter"] = afterScene.Scene.isDirty
            };
            var readback = reverse.BuildCanonicalPlan();
            readback["planDigestSchema"] = PlanSchema;
            readback["planDigest"] = reverse.PlanDigest;
            return new JObject
            {
                ["schema"] = ResultSchema,
                ["ok"] = true,
                ["preview"] = false,
                ["verified"] = true,
                ["changed"] = true,
                ["saved"] = true,
                ["mutationCount"] = before.References.Count,
                ["projectPath"] = CurrentProjectPath(),
                ["operation"] = before.BuildOperationPayload(),
                ["scene"] = scene,
                ["avatar"] = before.BuildAvatarPayload(),
                ["target"] = before.Target.DeepClone(),
                ["references"] = before.BuildReferencesPayload(),
                ["approvedPlan"] = before.BuildCanonicalPlan(),
                ["beforeStateDigest"] = before.BeforeStateDigest,
                ["targetStateDigest"] = before.TargetStateDigest,
                ["planDigestSchema"] = PlanSchema,
                ["planDigest"] = before.PlanDigest,
                ["readback"] = readback,
                ["readbackExact"] = true,
                ["checkpointRestoreRequired"] = false
            };
        }

        private static void ValidateExpected(JObject parameters, RenameSnapshot snapshot)
        {
            if (!SceneObjectCopyCore.MatchesCurrentProject(
                    ReadRequiredText(parameters, "expectedProjectPath", 32768))
                || ReadRequiredHex(parameters, "expectedSceneGuid", 32) != snapshot.Scene.Guid
                || ReadStrictInteger(parameters, "expectedSceneHandle") != snapshot.Scene.Handle
                || ReadRequiredHex(parameters, "expectedSceneFileDigest", 64)
                    != snapshot.Scene.FileDigest
                || ReadRequiredHex(parameters, "expectedSceneFileIdentity", 64)
                    != snapshot.Scene.FileIdentity
                || ReadRequiredHex(parameters, "expectedSceneMetaDigest", 64)
                    != snapshot.Scene.MetaDigest
                || ReadRequiredHex(parameters, "expectedSceneMetaIdentity", 64)
                    != snapshot.Scene.MetaIdentity
                || ReadRequiredText(parameters, "expectedAvatarObjectId", 512)
                    != snapshot.AvatarObjectId
                || ReadRequiredHex(parameters, "expectedTargetIdentityDigest", 64)
                    != snapshot.Target.Value<string>("identityDigest")
                || ReadRequiredHex(parameters, "expectedAssemblySetDigest", 64)
                    != snapshot.AssemblySetDigest
                || ReadRequiredHex(parameters, "expectedAssetInventoryDigest", 64)
                    != snapshot.AssetInventoryDigest
                || ReadRequiredHex(parameters, "expectedBeforeStateDigest", 64)
                    != snapshot.BeforeStateDigest
                || ReadRequiredHex(parameters, "expectedTargetStateDigest", 64)
                    != snapshot.TargetStateDigest
                || ReadRequiredHex(parameters, "expectedPlanDigest", 64) != snapshot.PlanDigest)
            {
                throw new AtomicReferenceRenameException(
                    "Atomic reference rename state changed after the verified preview.");
            }
        }

        private static List<AnimatorController> ResolveAvatarControllers(Component descriptor)
        {
            var serialized = new SerializedObject(descriptor);
            var result = new List<AnimatorController>();
            foreach (var path in new[] { "baseAnimationLayers", "specialAnimationLayers" })
            {
                var layers = RequireArray(serialized, path, path);
                for (var index = 0; index < layers.arraySize; index++)
                {
                    var controllerProperty = layers.GetArrayElementAtIndex(index)
                        .FindPropertyRelative("animatorController");
                    if (controllerProperty == null
                        || controllerProperty.propertyType != SerializedPropertyType.ObjectReference)
                    {
                        throw new AtomicReferenceRenameException(
                            "The avatar animation-layer layout is unsupported.");
                    }
                    var runtimeController = controllerProperty.objectReferenceValue;
                    if (runtimeController == null)
                    {
                        continue;
                    }
                    if (!(runtimeController is AnimatorController controller))
                    {
                        throw new AtomicReferenceRenameException(
                            "Only direct animator controllers are supported by atomic rename.");
                    }
                    RequireMutableAsset(controller, "animator controller");
                    result.Add(controller);
                }
            }
            return result
                .GroupBy(controller => StableObjectId(controller, "animator controller"))
                .Select(group => group.First())
                .OrderBy(AssetPath, StringComparer.Ordinal)
                .ToList();
        }

        private static void CollectControllerObjectAssets(
            AnimatorController controller,
            ISet<AnimationClip> clips,
            ISet<AvatarMask> masks)
        {
            RequireMutableAsset(controller, "animator controller");
            foreach (var layer in controller.layers)
            {
                if (layer.avatarMask != null)
                {
                    masks.Add(layer.avatarMask);
                }
                if (layer.stateMachine == null)
                {
                    throw new AtomicReferenceRenameException(
                        "An animator controller layer has no state machine.");
                }
                CollectStateMachineMotions(layer.stateMachine, clips, new HashSet<int>());
            }
        }

        private static void CollectStateMachineMotions(
            AnimatorStateMachine stateMachine,
            ISet<AnimationClip> clips,
            ISet<int> visited)
        {
            if (!visited.Add(stateMachine.GetInstanceID()))
            {
                return;
            }
            foreach (var childState in stateMachine.states)
            {
                if (childState.state == null)
                {
                    throw new AtomicReferenceRenameException("An animator state is unresolved.");
                }
                CollectMotionClips(childState.state.motion, clips, new HashSet<int>());
            }
            foreach (var childMachine in stateMachine.stateMachines)
            {
                if (childMachine.stateMachine == null)
                {
                    throw new AtomicReferenceRenameException(
                        "A child animator state machine is unresolved.");
                }
                CollectStateMachineMotions(childMachine.stateMachine, clips, visited);
            }
        }

        private static void CollectMotionClips(Motion motion, ISet<AnimationClip> clips, ISet<int> visited)
        {
            if (motion == null)
            {
                return;
            }
            if (motion is AnimationClip clip)
            {
                clips.Add(clip);
                return;
            }
            if (!(motion is BlendTree tree) || !visited.Add(tree.GetInstanceID()))
            {
                return;
            }
            foreach (var child in tree.children)
            {
                CollectMotionClips(child.motion, clips, visited);
            }
        }

        private static InventorySnapshot BuildInventory()
        {
            var registeredPaths = AssetDatabase.GetAllAssetPaths()
                .Where(path => path == "Assets"
                    || path.StartsWith("Assets/", StringComparison.Ordinal))
                .OrderBy(path => path, StringComparer.Ordinal)
                .ToList();
            if (registeredPaths.Count == 0
                || registeredPaths.Count > MaxInventoryEntries
                || registeredPaths.Distinct(StringComparer.Ordinal).Count()
                    != registeredPaths.Count)
            {
                throw new AtomicReferenceRenameException(
                    "The AssetDatabase inventory exceeds its fixed bound or is ambiguous.");
            }
            var folders = registeredPaths
                .Where(AssetDatabase.IsValidFolder)
                .ToList();
            var assets = new List<InventoryAsset>();
            foreach (var path in registeredPaths
                .Where(path => path.StartsWith("Assets/", StringComparison.Ordinal)
                    && !AssetDatabase.IsValidFolder(path)))
            {
                var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(path);
                if (!File.Exists(absolute) || !File.Exists(absolute + ".meta"))
                {
                    throw new AtomicReferenceRenameException(
                        "The complete project asset inventory contains an incomplete asset.");
                }
                assets.Add(new InventoryAsset
                {
                    Path = path,
                    Evidence = SceneObjectCopyCore.ReadStableAssetEvidence(
                        path,
                        "atomic reference rename inventory asset")
                });
            }
            if (assets.Count == 0 || assets.Count > MaxInventoryAssets)
            {
                throw new AtomicReferenceRenameException(
                    "The complete project asset inventory exceeds its fixed bound.");
            }
            var fileSystem = CaptureAssetsFileSystemInventory();
            VerifyRegisteredAssetsInFileSystem(assets, folders, fileSystem);
            var packageDigest = ComputePackageSetDigest();
            var value = new StringBuilder();
            AppendDigestField(value, "vrcforge.atomic_asset_inventory.v1");
            AppendDigestField(value, packageDigest);
            AppendDigestField(value, fileSystem.Digest);
            foreach (var asset in assets)
            {
                AppendDigestField(value, asset.Path);
                AppendDigestField(value, asset.Evidence.Guid);
                AppendDigestField(value, asset.Evidence.File.Digest);
                AppendDigestField(value, asset.Evidence.File.Identity);
                AppendDigestField(value, asset.Evidence.Meta.Digest);
                AppendDigestField(value, asset.Evidence.Meta.Identity);
                AppendDigestField(value, asset.Evidence.File.LinkCount.ToString(CultureInfo.InvariantCulture));
                AppendDigestField(value, asset.Evidence.Meta.LinkCount.ToString(CultureInfo.InvariantCulture));
            }
            return new InventorySnapshot
            {
                Assets = assets,
                FileSystem = fileSystem,
                PackageDigest = packageDigest,
                Digest = Sha256(value.ToString())
            };
        }

        private static FileSystemInventory CaptureAssetsFileSystemInventory()
        {
            FileSystemInventory last = null;
            for (var attempt = 0; attempt < 3; attempt++)
            {
                var first = CaptureAssetsFileSystemInventoryOnce();
                var second = CaptureAssetsFileSystemInventoryOnce();
                if (first.Digest == second.Digest)
                {
                    return second;
                }
                last = second;
            }
            throw new AtomicReferenceRenameException(
                "The Assets filesystem did not remain stable during inventory capture ("
                + (last?.Entries.Count ?? 0).ToString(CultureInfo.InvariantCulture) + ").");
        }

        private static FileSystemInventory CaptureAssetsFileSystemInventoryOnce()
        {
            try
            {
                var root = Path.GetFullPath(Path.Combine(CurrentProjectPath(), "Assets"));
                if (!Directory.Exists(root)
                    || (File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0)
                {
                    throw new AtomicReferenceRenameException(
                        "The Assets filesystem root is unavailable or indirect.");
                }
                var entries = new List<FileSystemEntry>();
                var pending = new Stack<string>();
                pending.Push(root);
                var pathComparison = Application.platform == RuntimePlatform.WindowsEditor
                    ? StringComparison.OrdinalIgnoreCase
                    : StringComparison.Ordinal;
                long totalBytes = 0;
                while (pending.Count != 0)
                {
                    var directory = pending.Pop();
                    if ((File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0)
                    {
                        throw new AtomicReferenceRenameException(
                            "The Assets filesystem contains an indirect directory.");
                    }
                    if (!string.Equals(directory, root, pathComparison))
                    {
                        entries.Add(new FileSystemEntry
                        {
                            Kind = "directory",
                            Path = "Assets/" + RelativePath(root, directory),
                            Digest = string.Empty,
                            Length = 0
                        });
                        RequireInventoryEntryBound(entries.Count, pending.Count);
                    }
                    foreach (var child in Directory.EnumerateDirectories(directory))
                    {
                        pending.Push(Path.GetFullPath(child));
                        RequireInventoryEntryBound(entries.Count, pending.Count);
                    }
                    foreach (var file in Directory.EnumerateFiles(directory))
                    {
                        if ((File.GetAttributes(file) & FileAttributes.ReparsePoint) != 0)
                        {
                            throw new AtomicReferenceRenameException(
                                "The Assets filesystem contains an indirect file.");
                        }
                        var entry = ReadStableAssetsFileEvidence(
                            Path.GetFullPath(file),
                            "Assets/" + RelativePath(root, file),
                            MaxInventoryBytes - totalBytes);
                        totalBytes = checked(totalBytes + entry.Length);
                        if (totalBytes > MaxInventoryBytes)
                        {
                            throw new AtomicReferenceRenameException(
                                "The Assets filesystem exceeds its fixed byte bound.");
                        }
                        entries.Add(entry);
                        RequireInventoryEntryBound(entries.Count, pending.Count);
                    }
                }
                entries = entries
                    .OrderBy(entry => entry.Path, StringComparer.Ordinal)
                    .ThenBy(entry => entry.Kind, StringComparer.Ordinal)
                    .ToList();
                if (entries.Select(entry => entry.Kind + "|" + entry.Path)
                    .Distinct(StringComparer.Ordinal).Count() != entries.Count)
                {
                    throw new AtomicReferenceRenameException(
                        "The Assets filesystem inventory contains duplicate paths.");
                }
                var value = new StringBuilder();
                AppendDigestField(value, "vrcforge.atomic_assets_filesystem.v1");
                foreach (var entry in entries)
                {
                    AppendDigestField(value, entry.Kind);
                    AppendDigestField(value, entry.Path);
                    AppendDigestField(value, entry.Digest);
                    AppendDigestField(
                        value,
                        entry.Length.ToString(CultureInfo.InvariantCulture));
                }
                return new FileSystemInventory
                {
                    Entries = entries,
                    Digest = Sha256(value.ToString())
                };
            }
            catch (AtomicReferenceRenameException)
            {
                throw;
            }
            catch (Exception exception)
            {
                throw new AtomicReferenceRenameException(
                    "The Assets filesystem inventory could not be captured ("
                    + exception.GetType().Name + ").");
            }
        }

        private static void RequireInventoryEntryBound(int entries, int pending)
        {
            if ((long)entries + pending > MaxInventoryEntries)
            {
                throw new AtomicReferenceRenameException(
                    "The Assets filesystem exceeds its fixed entry bound.");
            }
        }

        private static void VerifyRegisteredAssetsInFileSystem(
            IEnumerable<InventoryAsset> assets,
            IEnumerable<string> registeredFolders,
            FileSystemInventory fileSystem)
        {
            var assetList = assets.ToList();
            var folderSet = new HashSet<string>(
                registeredFolders.Where(path => path != "Assets"),
                StringComparer.Ordinal);
            var actualFolders = new HashSet<string>(
                fileSystem.Entries
                    .Where(entry => entry.Kind == "directory")
                    .Select(entry => entry.Path),
                StringComparer.Ordinal);
            if (!actualFolders.SetEquals(folderSet))
            {
                throw new AtomicReferenceRenameException(
                    "The AssetDatabase folder inventory does not match the Assets filesystem.");
            }

            var expectedFiles = new HashSet<string>(StringComparer.Ordinal);
            foreach (var asset in assetList)
            {
                expectedFiles.Add(asset.Path);
                expectedFiles.Add(asset.Path + ".meta");
            }
            foreach (var folder in folderSet)
            {
                expectedFiles.Add(folder + ".meta");
            }
            var files = fileSystem.Entries
                .Where(entry => entry.Kind == "file")
                .ToDictionary(entry => entry.Path, StringComparer.Ordinal);
            if (!expectedFiles.SetEquals(files.Keys))
            {
                throw new AtomicReferenceRenameException(
                    "The AssetDatabase file inventory does not match the Assets filesystem.");
            }
            foreach (var asset in assetList)
            {
                if (!files.TryGetValue(asset.Path, out var body)
                    || !files.TryGetValue(asset.Path + ".meta", out var meta)
                    || body.Digest != asset.Evidence.File.Digest
                    || (ulong)body.Length != asset.Evidence.File.Length
                    || meta.Digest != asset.Evidence.Meta.Digest
                    || (ulong)meta.Length != asset.Evidence.Meta.Length)
                {
                    throw new AtomicReferenceRenameException(
                        "The registered asset inventory does not match the Assets filesystem.");
                }
            }
        }

        private static FileSystemEntry ReadStableAssetsFileEvidence(
            string path,
            string relativePath,
            long remainingBytes)
        {
            using (var stream = new FileStream(
                NativeFileSystemPath(path),
                FileMode.Open,
                FileAccess.Read,
                FileShare.Read))
            {
                var before = new FileInfo(path);
                var length = stream.Length;
                if (remainingBytes < 0 || length > remainingBytes)
                {
                    throw new AtomicReferenceRenameException(
                        "The Assets filesystem exceeds its fixed byte bound.");
                }
                var writeTime = before.LastWriteTimeUtc;
                string digest;
                using (var sha256 = SHA256.Create())
                {
                    digest = BitConverter.ToString(sha256.ComputeHash(stream))
                        .Replace("-", string.Empty)
                        .ToLowerInvariant();
                }
                var after = new FileInfo(path);
                if (stream.Length != length
                    || after.Length != length
                    || after.LastWriteTimeUtc != writeTime
                    || (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                {
                    throw new AtomicReferenceRenameException(
                        "An Assets filesystem file changed during inventory capture.");
                }
                return new FileSystemEntry
                {
                    Kind = "file",
                    Path = relativePath,
                    Digest = digest,
                    Length = length
                };
            }
        }

        private static string ComputePackageSetDigest()
        {
            var value = new StringBuilder();
            AppendDigestField(value, "vrcforge.atomic_package_set.v1");
            var fileCount = 0;
            foreach (var package in UnityEditor.PackageManager.PackageInfo.GetAllRegisteredPackages()
                .OrderBy(item => item.name, StringComparer.Ordinal))
            {
                AppendDigestField(value, package.name ?? string.Empty);
                AppendDigestField(value, package.version ?? string.Empty);
                AppendDigestField(value, package.source.ToString());
                var root = string.IsNullOrWhiteSpace(package.resolvedPath)
                    ? string.Empty
                    : Path.GetFullPath(package.resolvedPath);
                if (string.IsNullOrEmpty(root)
                    || !Directory.Exists(root)
                    || (File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0)
                {
                    throw new AtomicReferenceRenameException(
                        "A registered package root is unavailable or indirect.");
                }
                var source = package.source.ToString();
                PackageTreeEvidence tree;
                if (source == "Registry" || source == "BuiltIn")
                {
                    var manifest = Path.Combine(root, "package.json");
                    tree = new PackageTreeEvidence
                    {
                        FileCount = 1,
                        Digest = ReadStableExternalFileDigest(
                            manifest,
                            (package.name ?? "package") + "/package.json")
                    };
                }
                else
                {
                    tree = CapturePackageTree(root, package.name ?? "package");
                }
                fileCount += tree.FileCount;
                if (fileCount > MaxPackageFiles)
                {
                    throw new AtomicReferenceRenameException(
                        "The complete package inventory exceeds its fixed bound.");
                }
                AppendDigestField(value, tree.FileCount.ToString(CultureInfo.InvariantCulture));
                AppendDigestField(value, tree.Digest);
            }
            foreach (var name in new[] { "manifest.json", "packages-lock.json" })
            {
                var path = Path.Combine(CurrentProjectPath(), "Packages", name);
                if (!File.Exists(path))
                {
                    throw new AtomicReferenceRenameException(
                        "The package inventory is incomplete.");
                }
                AppendDigestField(value, name);
                AppendDigestField(value, ReadStableExternalFileDigest(path, "Packages/" + name));
            }
            return Sha256(value.ToString());
        }

        private static PackageTreeEvidence CapturePackageTree(string root, string packageName)
        {
            AtomicReferenceRenameException last = null;
            for (var attempt = 0; attempt < 3; attempt++)
            {
                try
                {
                    var files = EnumeratePackageFiles(root).ToList();
                    if (!files.Any(path => string.Equals(
                        RelativePath(root, path),
                        "package.json",
                        StringComparison.Ordinal)))
                    {
                        throw new AtomicReferenceRenameException(
                            "A registered package inventory is incomplete.");
                    }
                    var value = new StringBuilder();
                    AppendDigestField(value, "vrcforge.atomic_package_tree.v1");
                    foreach (var path in files)
                    {
                        var relative = RelativePath(root, path);
                        AppendDigestField(value, relative);
                        AppendDigestField(
                            value,
                            ReadStableExternalFileDigest(
                                path,
                                packageName + "/" + relative));
                    }
                    return new PackageTreeEvidence
                    {
                        FileCount = files.Count,
                        Digest = Sha256(value.ToString())
                    };
                }
                catch (AtomicReferenceRenameException exception)
                {
                    last = exception;
                }
            }
            throw last ?? new AtomicReferenceRenameException(
                "A registered package inventory could not be captured.");
        }

        private static IEnumerable<string> EnumeratePackageFiles(string root)
        {
            var pending = new Stack<string>();
            pending.Push(root);
            var files = new List<string>();
            while (pending.Count != 0)
            {
                var directory = pending.Pop();
                if ((File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0)
                {
                    throw new AtomicReferenceRenameException(
                        "A registered package contains an indirect directory.");
                }
                foreach (var child in Directory.EnumerateDirectories(directory))
                {
                    pending.Push(child);
                }
                foreach (var file in Directory.EnumerateFiles(directory))
                {
                    if ((File.GetAttributes(file) & FileAttributes.ReparsePoint) != 0)
                    {
                        throw new AtomicReferenceRenameException(
                            "A registered package contains an indirect file.");
                    }
                    files.Add(Path.GetFullPath(file));
                }
            }
            return files.OrderBy(path => RelativePath(root, path), StringComparer.Ordinal);
        }

        private static string RelativePath(string root, string path)
        {
            var prefix = Path.GetFullPath(root)
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                + Path.DirectorySeparatorChar;
            var full = Path.GetFullPath(path);
            var comparison = Application.platform == RuntimePlatform.WindowsEditor
                ? StringComparison.OrdinalIgnoreCase
                : StringComparison.Ordinal;
            if (!full.StartsWith(prefix, comparison))
            {
                throw new AtomicReferenceRenameException(
                    "A package inventory path escaped its registered root.");
            }
            return full.Substring(prefix.Length).Replace('\\', '/');
        }

        private static string ReadStableExternalFileDigest(string path, string label)
        {
            try
            {
                var fileSystemPath = NativeFileSystemPath(path);
                using (var stream = new FileStream(
                    fileSystemPath,
                    FileMode.Open,
                    FileAccess.Read,
                    FileShare.ReadWrite | FileShare.Delete))
                {
                    var before = new FileInfo(fileSystemPath);
                    var length = stream.Length;
                    var writeTime = before.LastWriteTimeUtc;
                    string digest;
                    using (var sha256 = SHA256.Create())
                    {
                        digest = BitConverter.ToString(sha256.ComputeHash(stream))
                            .Replace("-", string.Empty)
                            .ToLowerInvariant();
                    }
                    var after = new FileInfo(fileSystemPath);
                    if (stream.Length != length
                        || after.Length != length
                        || after.LastWriteTimeUtc != writeTime
                        || (File.GetAttributes(fileSystemPath) & FileAttributes.ReparsePoint) != 0)
                    {
                        throw new AtomicReferenceRenameException(
                            "A package file changed during inventory capture.");
                    }
                    return digest;
                }
            }
            catch (AtomicReferenceRenameException)
            {
                throw;
            }
            catch (Exception exception)
            {
                throw new AtomicReferenceRenameException(
                    "A complete inventory file could not be captured (" + label + ","
                    + exception.GetType().Name + ").");
            }
        }

        private static string NativeFileSystemPath(string path)
        {
            var full = Path.GetFullPath(path);
            if (Application.platform != RuntimePlatform.WindowsEditor
                || full.StartsWith(@"\\?\", StringComparison.Ordinal))
            {
                return full;
            }
            return @"\\?\" + full;
        }

        private static string ComputeAssemblySetDigest()
        {
            var value = new StringBuilder();
            AppendDigestField(value, "vrcforge.atomic_assembly_set.v1");
            var entries = new List<KeyValuePair<string, string>>();
            foreach (var kind in new[]
            {
                UnityEditor.Compilation.AssembliesType.Editor,
                UnityEditor.Compilation.AssembliesType.Player
            })
            {
                foreach (var assembly in UnityEditor.Compilation.CompilationPipeline.GetAssemblies(kind))
                {
                    var path = ResolveAssemblyPath(assembly.outputPath);
                    entries.Add(new KeyValuePair<string, string>(
                        "compiled|" + assembly.name + "|" + NormalizeDigestPath(path),
                        RequiredAssemblyDigest(path)));
                }
            }
            foreach (var rawPath in UnityEditor.Compilation.CompilationPipeline
                .GetPrecompiledAssemblyPaths(
                    UnityEditor.Compilation.CompilationPipeline.PrecompiledAssemblySources.All))
            {
                var path = ResolveAssemblyPath(rawPath);
                entries.Add(new KeyValuePair<string, string>(
                    "precompiled|" + NormalizeDigestPath(path),
                    RequiredAssemblyDigest(path)));
            }
            foreach (var entry in entries
                .OrderBy(item => item.Key, StringComparer.Ordinal)
                .ThenBy(item => item.Value, StringComparer.Ordinal))
            {
                AppendDigestField(value, entry.Key);
                AppendDigestField(value, entry.Value);
            }
            return Sha256(value.ToString());
        }

        private static string RequiredAssemblyDigest(string path)
        {
            if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
            {
                throw new AtomicReferenceRenameException(
                    "The complete assembly inventory contains a missing file.");
            }
            return ReadStableExternalFileDigest(path, NormalizeDigestPath(path));
        }

        private static string ResolveAssemblyPath(string rawPath)
        {
            if (string.IsNullOrWhiteSpace(rawPath))
            {
                return string.Empty;
            }
            return Path.GetFullPath(
                Path.IsPathRooted(rawPath)
                    ? rawPath
                    : Path.Combine(CurrentProjectPath(), rawPath));
        }

        private static string NormalizeDigestPath(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return string.Empty;
            }
            var full = Path.GetFullPath(path);
            var project = CurrentProjectPath() + Path.DirectorySeparatorChar;
            var comparison = Application.platform == RuntimePlatform.WindowsEditor
                ? StringComparison.OrdinalIgnoreCase
                : StringComparison.Ordinal;
            return full.StartsWith(project, comparison)
                ? "<project>/" + full.Substring(project.Length).Replace('\\', '/')
                : Path.GetFileName(full);
        }

        private static int CountRawToken(
            string assetPath,
            StableAssetEvidence evidence,
            string token)
        {
            var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(assetPath);
            var bytes = File.ReadAllBytes(absolute);
            if (Sha256Bytes(bytes) != evidence.File.Digest)
            {
                throw new AtomicReferenceRenameException(
                    "A project asset changed during unknown-reference scanning.");
            }
            var count = CountBytes(bytes, Encoding.UTF8.GetBytes(token));
            var meta = File.ReadAllBytes(absolute + ".meta");
            if (Sha256Bytes(meta) != evidence.Meta.Digest)
            {
                throw new AtomicReferenceRenameException(
                    "Project asset metadata changed during unknown-reference scanning.");
            }
            return count + CountBytes(meta, Encoding.UTF8.GetBytes(token));
        }

        private static ExpectedFileEvidence ComputeExpectedTargetFile(
            RenameAssetReceipt asset,
            string before,
            string after,
            int expectedCount)
        {
            var stableBefore = SceneObjectCopyCore.ReadStableAssetEvidence(
                asset.AssetPath,
                "atomic reference rename target projection");
            if (stableBefore.File.Digest != asset.FileDigest
                || stableBefore.File.Identity != asset.FileIdentity
                || stableBefore.Meta.Digest != asset.MetaDigest
                || stableBefore.Meta.Identity != asset.MetaIdentity
                || stableBefore.File.LinkCount != 1
                || stableBefore.Meta.LinkCount != 1
                || (long)stableBefore.File.Length != asset.FileLength)
            {
                throw new AtomicReferenceRenameException(
                    "A planned asset changed before target projection.");
            }
            var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(asset.AssetPath);
            var bytes = File.ReadAllBytes(absolute);
            if (bytes.LongLength > MaxBackupAssetBytes
                || bytes.LongLength != asset.FileLength
                || Sha256Bytes(bytes) != asset.FileDigest)
            {
                throw new AtomicReferenceRenameException(
                    "A planned asset exceeds the fixed target projection bound.");
            }
            var target = ReplaceBytesExact(
                bytes,
                Encoding.UTF8.GetBytes(before),
                Encoding.UTF8.GetBytes(after),
                expectedCount);
            var stableAfter = SceneObjectCopyCore.ReadStableAssetEvidence(
                asset.AssetPath,
                "atomic reference rename target projection readback");
            if (!StableAssetEvidenceMatches(stableBefore, stableAfter))
            {
                throw new AtomicReferenceRenameException(
                    "A planned asset changed during target projection.");
            }
            return new ExpectedFileEvidence
            {
                Digest = Sha256Bytes(target),
                Length = target.LongLength
            };
        }

        private static byte[] ReplaceBytesExact(
            byte[] source,
            byte[] before,
            byte[] after,
            int expectedCount)
        {
            if (source == null
                || before == null
                || before.Length == 0
                || after == null
                || expectedCount <= 0)
            {
                throw new AtomicReferenceRenameException(
                    "The target byte projection is invalid.");
            }
            var expectedLength = checked(
                source.LongLength + ((long)after.Length - before.Length) * expectedCount);
            if (expectedLength < 0
                || expectedLength > MaxBackupAssetBytes
                || expectedLength > int.MaxValue)
            {
                throw new AtomicReferenceRenameException(
                    "The target byte projection exceeds its fixed bound.");
            }
            using (var output = new MemoryStream((int)expectedLength))
            {
                var count = 0;
                var index = 0;
                while (index < source.Length)
                {
                    if (BytesMatchAt(source, before, index))
                    {
                        output.Write(after, 0, after.Length);
                        index += before.Length;
                        count++;
                        continue;
                    }
                    output.WriteByte(source[index]);
                    index++;
                }
                if (count != expectedCount || output.Length != expectedLength)
                {
                    throw new AtomicReferenceRenameException(
                        "The target byte projection does not match the approved references.");
                }
                return output.ToArray();
            }
        }

        private static bool BytesMatchAt(byte[] source, byte[] target, int index)
        {
            if (index < 0 || index > source.Length - target.Length)
            {
                return false;
            }
            for (var offset = 0; offset < target.Length; offset++)
            {
                if (source[index + offset] != target[offset])
                {
                    return false;
                }
            }
            return true;
        }

        private static int CountBytes(byte[] haystack, byte[] needle)
        {
            if (needle.Length == 0 || haystack.Length < needle.Length)
            {
                return 0;
            }
            var count = 0;
            for (var index = 0; index <= haystack.Length - needle.Length; index++)
            {
                var match = true;
                for (var offset = 0; offset < needle.Length; offset++)
                {
                    if (haystack[index + offset] == needle[offset])
                    {
                        continue;
                    }
                    match = false;
                    break;
                }
                if (match)
                {
                    count++;
                    index += needle.Length - 1;
                }
            }
            return count;
        }

        private static string ComputeStateDigest(RenameSnapshot snapshot, bool after)
        {
            var value = new StringBuilder();
            AppendDigestField(value, "vrcforge.atomic_reference_state.v1");
            AppendDigestField(value, snapshot.Request.OperationKind);
            AppendDigestField(value, snapshot.Scene.Guid);
            AppendDigestField(value, snapshot.Scene.FileIdentity);
            AppendDigestField(value, snapshot.AvatarObjectId);
            AppendDigestField(value, snapshot.Target.Value<string>("identityDigest"));
            AppendDigestField(value, snapshot.AssemblySetDigest);
            AppendDigestField(value, snapshot.AssetInventoryDigest);
            foreach (var reference in snapshot.References)
            {
                AppendDigestField(value, reference.Kind);
                AppendDigestField(value, reference.AssetPath);
                AppendDigestField(value, reference.ObjectId);
                AppendDigestField(value, reference.PropertyPath);
                AppendDigestField(value, after ? reference.After : reference.Before);
            }
            return Sha256(value.ToString());
        }

        private static string ComputePlanDigest(JObject plan)
        {
            var payload = new JObject { ["schema"] = PlanSchema };
            foreach (var property in plan.Properties())
            {
                payload[property.Name] = property.Value.DeepClone();
            }
            var canonical = Canonicalize(payload);
            return Sha256(canonical.ToString(Formatting.None));
        }

        private static JToken Canonicalize(JToken token)
        {
            if (token is JObject obj)
            {
                var result = new JObject();
                foreach (var property in obj.Properties().OrderBy(item => item.Name, StringComparer.Ordinal))
                {
                    result[property.Name] = Canonicalize(property.Value);
                }
                return result;
            }
            if (token is JArray array)
            {
                return new JArray(array.Select(Canonicalize));
            }
            return token.DeepClone();
        }

        private static Component ResolveExactComponent(GameObject host, string typeName, string label)
        {
            var matches = host.GetComponents<Component>()
                .Where(component => component != null && component.GetType().FullName == typeName)
                .ToList();
            if (matches.Count != 1)
            {
                throw new AtomicReferenceRenameException(label + " is missing or ambiguous.");
            }
            return matches[0];
        }

        private static UnityEngine.Object RequireObjectReference(
            SerializedObject serialized,
            string path,
            string typeName,
            string label)
        {
            var value = OptionalObjectReference(serialized, path, typeName, label);
            if (value == null)
            {
                throw new AtomicReferenceRenameException(label + " is missing.");
            }
            return value;
        }

        private static UnityEngine.Object OptionalObjectReference(
            SerializedObject serialized,
            string path,
            string typeName,
            string label)
        {
            var property = serialized.FindProperty(path);
            if (property == null || property.propertyType != SerializedPropertyType.ObjectReference)
            {
                throw new AtomicReferenceRenameException(label + " layout is unsupported.");
            }
            var value = property.objectReferenceValue;
            if (value != null && value.GetType().FullName != typeName)
            {
                throw new AtomicReferenceRenameException(label + " type is unsupported.");
            }
            return value;
        }

        private static SerializedProperty RequireArray(
            SerializedObject serialized,
            string path,
            string label)
        {
            var property = serialized.FindProperty(path);
            if (property == null || !property.isArray)
            {
                throw new AtomicReferenceRenameException(label + " layout is unsupported.");
            }
            return property;
        }

        private static void RequireMutableAsset(UnityEngine.Object target, string label)
        {
            var path = AssetPath(target);
            if (!path.StartsWith("Assets/", StringComparison.Ordinal)
                || AssetDatabase.IsValidFolder(path))
            {
                throw new AtomicReferenceRenameException(
                    label + " must be a mutable asset under Assets/.");
            }
        }

        private static string AssetPath(UnityEngine.Object target)
        {
            var path = (AssetDatabase.GetAssetPath(target) ?? string.Empty).Replace('\\', '/');
            if (string.IsNullOrWhiteSpace(path))
            {
                throw new AtomicReferenceRenameException("A referenced asset path is unavailable.");
            }
            return path;
        }

        private static string StableObjectId(UnityEngine.Object target, string label)
        {
            if (target == null)
            {
                throw new AtomicReferenceRenameException(label + " is unavailable.");
            }
            var globalId = GlobalObjectId.GetGlobalObjectIdSlow(target);
            var id = globalId.ToString();
            if (!id.StartsWith("GlobalObjectId_V1-", StringComparison.Ordinal)
                || globalId.identifierType == 0)
            {
                throw new AtomicReferenceRenameException(label + " has no stable identity.");
            }
            return id;
        }

        private static bool SceneEvidenceMatches(SavedSceneSnapshot left, SavedSceneSnapshot right)
        {
            return left.Path == right.Path
                && left.Guid == right.Guid
                && left.Handle == right.Handle
                && left.FileDigest == right.FileDigest
                && left.FileIdentity == right.FileIdentity
                && left.MetaDigest == right.MetaDigest
                && left.MetaIdentity == right.MetaIdentity;
        }

        private static string NormalizeScenePath(string value)
        {
            var normalized = value.Replace('\\', '/');
            var parts = normalized.Split('/');
            if (!normalized.StartsWith("Assets/", StringComparison.Ordinal)
                || !normalized.EndsWith(".unity", StringComparison.OrdinalIgnoreCase)
                || parts.Any(part => string.IsNullOrWhiteSpace(part) || part == "." || part == ".."))
            {
                throw new AtomicReferenceRenameException(
                    "scenePath must select a saved scene under Assets/.");
            }
            return normalized;
        }

        private static string NormalizeHierarchyPath(string value, string label)
        {
            var normalized = value.Trim(' ', '/');
            var parts = normalized.Split('/');
            if (normalized.Length == 0
                || normalized.Length > 512
                || normalized.IndexOf('\\') >= 0
                || parts.Any(part => string.IsNullOrWhiteSpace(part)
                    || part == "."
                    || part == ".."
                    || part.Length > 128
                    || part.Any(character => char.IsControl(character))))
            {
                throw new AtomicReferenceRenameException(label + " is invalid.");
            }
            return normalized;
        }

        private static string NormalizeObjectName(string value)
        {
            var normalized = value.Trim();
            if (normalized.Length == 0
                || normalized.Length > 128
                || normalized == "."
                || normalized == ".."
                || normalized.IndexOf('/') >= 0
                || normalized.IndexOf('\\') >= 0
                || normalized.Any(character => char.IsControl(character)))
            {
                throw new AtomicReferenceRenameException("newName is invalid.");
            }
            return normalized;
        }

        private static string NormalizeParameterName(string value, string label)
        {
            if (value.Length == 0
                || value.Length > 128
                || value.Any(character => !((character >= 'A' && character <= 'Z')
                    || (character >= 'a' && character <= 'z')
                    || (character >= '0' && character <= '9')
                    || character == '_'
                    || character == '.'
                    || character == '-')))
            {
                throw new AtomicReferenceRenameException(label + " is invalid.");
            }
            return value;
        }

        private static bool PathMatchesPrefix(string path, string prefix)
        {
            return path == prefix || path.StartsWith(prefix + "/", StringComparison.Ordinal);
        }

        private static string ReplacePathPrefix(string value, string before, string after)
        {
            return after + value.Substring(before.Length);
        }

        private static string ReadRequiredText(JObject source, string key, int maximum)
        {
            var token = source[key];
            if (token == null || token.Type != JTokenType.String)
            {
                throw new AtomicReferenceRenameException(key + " must be text.");
            }
            var value = token.Value<string>() ?? string.Empty;
            if (value.Length == 0
                || value.Length > maximum
                || value.Any(character => character < 32))
            {
                throw new AtomicReferenceRenameException(key + " is invalid.");
            }
            return value;
        }

        private static bool ReadStrictBoolean(JObject source, string key)
        {
            var token = source[key];
            if (token == null || token.Type != JTokenType.Boolean)
            {
                throw new AtomicReferenceRenameException(key + " must be a boolean.");
            }
            return token.Value<bool>();
        }

        private static int ReadStrictInteger(JObject source, string key)
        {
            var token = source[key];
            if (token == null || token.Type != JTokenType.Integer)
            {
                throw new AtomicReferenceRenameException(key + " must be an integer.");
            }
            var value = token.Value<long>();
            if (value < int.MinValue || value > int.MaxValue || value == 0)
            {
                throw new AtomicReferenceRenameException(key + " is invalid.");
            }
            return (int)value;
        }

        private static string ReadRequiredHex(JObject source, string key, int length)
        {
            var value = ReadRequiredText(source, key, length);
            if (value.Length != length
                || value.Any(character => !Uri.IsHexDigit(character))
                || value != value.ToLowerInvariant())
            {
                throw new AtomicReferenceRenameException(key + " is invalid.");
            }
            return value;
        }

        private static object Failure(
            Exception exception,
            bool mutationStarted,
            bool restored)
        {
            if (!mutationStarted)
            {
                return new ErrorResponse(SafeError(exception));
            }
            return new ErrorResponse(
                restored
                    ? "Atomic reference rename failed after restoring the verified pre-state."
                    : "Atomic reference rename failed; checkpoint restore is required.",
                new
                {
                    schema = ResultSchema,
                    mutationStarted = true,
                    restored,
                    cleanupVerified = restored,
                    cleanupRequired = !restored,
                    checkpointRestoreRequired = !restored,
                    operationState = restored ? "restored" : "checkpoint_restore_required",
                    detail = SafeError(exception)
                });
        }

        private static string SafeError(Exception exception)
        {
            var message = exception?.Message ?? "Atomic reference rename failed.";
            message = message.Replace(CurrentProjectPath(), "<project>");
            return message.Length <= 1024 ? message : message.Substring(0, 1024);
        }

        private static string CurrentProjectPath()
        {
            return Path.GetFullPath(
                Directory.GetParent(Application.dataPath)?.FullName ?? string.Empty)
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        }

        private static string Sha256Fields(params string[] fields)
        {
            var value = new StringBuilder();
            foreach (var field in fields)
            {
                AppendDigestField(value, field);
            }
            return Sha256(value.ToString());
        }

        private static void AppendDigestField(StringBuilder target, string value)
        {
            var safe = value ?? string.Empty;
            target.Append(safe.Length.ToString(CultureInfo.InvariantCulture))
                .Append(':')
                .Append(safe);
        }

        private static string Sha256(string value)
        {
            return Sha256Bytes(Encoding.UTF8.GetBytes(value ?? string.Empty));
        }

        private static string Sha256Bytes(byte[] value)
        {
            using (var sha256 = SHA256.Create())
            {
                return BitConverter.ToString(sha256.ComputeHash(value))
                    .Replace("-", string.Empty)
                    .ToLowerInvariant();
            }
        }

        private sealed class ScanContext
        {
            internal readonly RenameSnapshot Snapshot;
            internal readonly InventorySnapshot Inventory;
            internal readonly List<RenameReference> References = new List<RenameReference>();

            internal ScanContext(RenameSnapshot snapshot, InventorySnapshot inventory)
            {
                Snapshot = snapshot;
                Inventory = inventory;
            }

            internal void AddReference(
                string kind,
                string assetPath,
                UnityEngine.Object owner,
                string propertyPath,
                string before,
                string after,
                Action apply,
                int rawOccurrenceCount = 1)
            {
                if (References.Count >= MaxReferences)
                {
                    throw new AtomicReferenceRenameException(
                        "The atomic reference rename mutation set exceeds its fixed bound.");
                }
                if (!assetPath.StartsWith("Assets/", StringComparison.Ordinal))
                {
                    throw new AtomicReferenceRenameException(
                        "A reference target is outside mutable project assets.");
                }
                References.Add(new RenameReference
                {
                    Kind = kind,
                    AssetPath = assetPath,
                    Owner = owner,
                    ObjectId = StableObjectId(owner, "atomic rename reference owner"),
                    PropertyPath = propertyPath,
                    Before = before,
                    After = after,
                    Apply = apply,
                    RawOccurrenceCount = rawOccurrenceCount
                });
            }
        }

        private sealed class RenameRequest
        {
            internal string OperationKind = string.Empty;
            internal string ScenePath = string.Empty;
            internal string AvatarPath = string.Empty;
            internal string TargetObjectPath = string.Empty;
            internal string NewName = string.Empty;
            internal string OldName = string.Empty;
            internal string OldParameterName = string.Empty;
            internal string NewParameterName = string.Empty;
            internal string TargetBefore = string.Empty;
            internal string TargetAfter = string.Empty;

            internal RenameRequest Reverse()
            {
                if (OperationKind == ObjectOperation)
                {
                    return new RenameRequest
                    {
                        OperationKind = ObjectOperation,
                        ScenePath = ScenePath,
                        AvatarPath = AvatarPath,
                        TargetObjectPath = TargetAfter,
                        OldName = NewName,
                        NewName = OldName,
                        TargetBefore = TargetAfter,
                        TargetAfter = TargetBefore
                    };
                }
                return new RenameRequest
                {
                    OperationKind = ParameterOperation,
                    ScenePath = ScenePath,
                    AvatarPath = AvatarPath,
                    OldParameterName = NewParameterName,
                    NewParameterName = OldParameterName,
                    TargetBefore = TargetAfter,
                    TargetAfter = TargetBefore
                };
            }
        }

        private sealed class RenameSnapshot
        {
            internal RenameRequest Request;
            internal SavedSceneSnapshot Scene;
            internal GameObject Avatar;
            internal Component Descriptor;
            internal GameObject TargetObject;
            internal string AvatarObjectId = string.Empty;
            internal JObject Target = new JObject();
            internal string AssemblySetDigest = string.Empty;
            internal InventorySnapshot Inventory;
            internal string AssetInventoryDigest = string.Empty;
            internal int ObjectCount;
            internal readonly List<RenameAssetReceipt> Assets = new List<RenameAssetReceipt>();
            internal readonly List<RenameReference> References = new List<RenameReference>();
            internal string BeforeStateDigest = string.Empty;
            internal string TargetStateDigest = string.Empty;
            internal string PlanDigest = string.Empty;

            internal JObject BuildPreviewPayload()
            {
                return new JObject
                {
                    ["schema"] = ResultSchema,
                    ["ok"] = true,
                    ["preview"] = true,
                    ["verified"] = true,
                    ["changed"] = false,
                    ["saved"] = false,
                    ["mutationCount"] = 0,
                    ["projectPath"] = CurrentProjectPath(),
                    ["operation"] = BuildOperationPayload(),
                    ["scene"] = BuildScenePayload(),
                    ["avatar"] = BuildAvatarPayload(),
                    ["target"] = Target.DeepClone(),
                    ["scan"] = BuildScanPayload(),
                    ["assets"] = BuildAssetsPayload(),
                    ["references"] = BuildReferencesPayload(),
                    ["beforeStateDigest"] = BeforeStateDigest,
                    ["targetStateDigest"] = TargetStateDigest,
                    ["planDigestSchema"] = PlanSchema,
                    ["planDigest"] = PlanDigest
                };
            }

            internal JObject BuildCanonicalPlan()
            {
                return new JObject
                {
                    ["operation"] = BuildOperationPayload(),
                    ["scene"] = BuildScenePayload(),
                    ["avatar"] = BuildAvatarPayload(),
                    ["target"] = Target.DeepClone(),
                    ["scan"] = BuildScanPayload(),
                    ["assets"] = BuildAssetsPayload(),
                    ["references"] = BuildReferencesPayload(),
                    ["beforeStateDigest"] = BeforeStateDigest,
                    ["targetStateDigest"] = TargetStateDigest
                };
            }

            internal JObject BuildOperationPayload()
            {
                return new JObject
                {
                    ["kind"] = Request.OperationKind,
                    ["before"] = Request.TargetBefore,
                    ["after"] = Request.TargetAfter
                };
            }

            internal JObject BuildScenePayload()
            {
                return new JObject
                {
                    ["path"] = Scene.Path,
                    ["guid"] = Scene.Guid,
                    ["handle"] = Scene.Handle,
                    ["fileDigestBefore"] = Scene.FileDigest,
                    ["fileDigestAfter"] = Scene.FileDigest,
                    ["fileIdentity"] = Scene.FileIdentity,
                    ["metaDigestBefore"] = Scene.MetaDigest,
                    ["metaDigestAfter"] = Scene.MetaDigest,
                    ["metaIdentity"] = Scene.MetaIdentity,
                    ["dirtyBefore"] = false,
                    ["dirtyAfter"] = false
                };
            }

            internal JObject BuildAvatarPayload()
            {
                return new JObject
                {
                    ["path"] = Request.AvatarPath,
                    ["objectId"] = AvatarObjectId,
                    ["descriptorType"] = DescriptorType
                };
            }

            internal JObject BuildScanPayload()
            {
                return new JObject
                {
                    ["assemblySetDigest"] = AssemblySetDigest,
                    ["assetInventoryDigest"] = AssetInventoryDigest,
                    ["objectCount"] = ObjectCount,
                    ["assetCount"] = Assets.Count,
                    ["knownReferenceCount"] = References.Count,
                    ["unknownReferenceCount"] = 0,
                    ["unresolvedReferenceCount"] = 0
                };
            }

            internal JArray BuildAssetsPayload()
            {
                return new JArray(Assets.Select(asset => asset.ToPayload()));
            }

            internal JArray BuildReferencesPayload()
            {
                return new JArray(References.Select(reference => reference.ToPayload()));
            }
        }

        private sealed class RenameReference
        {
            internal string Kind = string.Empty;
            internal string AssetPath = string.Empty;
            internal UnityEngine.Object Owner;
            internal string ObjectId = string.Empty;
            internal string PropertyPath = string.Empty;
            internal string Before = string.Empty;
            internal string After = string.Empty;
            internal Action Apply;
            internal int RawOccurrenceCount = 1;

            internal JObject ToPayload()
            {
                return new JObject
                {
                    ["kind"] = Kind,
                    ["assetPath"] = AssetPath,
                    ["objectId"] = ObjectId,
                    ["propertyPath"] = PropertyPath,
                    ["before"] = Before,
                    ["after"] = After
                };
            }

            internal static int Compare(RenameReference left, RenameReference right)
            {
                var result = string.CompareOrdinal(left.AssetPath, right.AssetPath);
                if (result != 0) return result;
                result = string.CompareOrdinal(left.ObjectId, right.ObjectId);
                if (result != 0) return result;
                result = string.CompareOrdinal(left.PropertyPath, right.PropertyPath);
                return result != 0 ? result : string.CompareOrdinal(left.Kind, right.Kind);
            }
        }

        private sealed class RenameAssetReceipt
        {
            internal string AssetPath = string.Empty;
            internal string AssetGuid = string.Empty;
            internal string FileDigest = string.Empty;
            internal long FileLength;
            internal string TargetFileDigest = string.Empty;
            internal long TargetFileLength;
            internal string MetaDigest = string.Empty;
            internal string FileIdentity = string.Empty;
            internal string MetaIdentity = string.Empty;
            internal int MutationCount;
            internal int RawReplacementCount;

            internal JObject ToPayload()
            {
                return new JObject
                {
                    ["assetPath"] = AssetPath,
                    ["assetGuid"] = AssetGuid,
                    ["fileDigest"] = FileDigest,
                    ["fileLength"] = FileLength,
                    ["targetFileDigest"] = TargetFileDigest,
                    ["targetFileLength"] = TargetFileLength,
                    ["metaDigest"] = MetaDigest,
                    ["fileIdentity"] = FileIdentity,
                    ["mutationCount"] = MutationCount,
                    ["rawReplacementCount"] = RawReplacementCount
                };
            }

            internal static int Compare(RenameAssetReceipt left, RenameAssetReceipt right)
            {
                var result = string.CompareOrdinal(left.AssetPath, right.AssetPath);
                return result != 0 ? result : string.CompareOrdinal(left.AssetGuid, right.AssetGuid);
            }
        }

        private sealed class InventorySnapshot
        {
            internal List<InventoryAsset> Assets = new List<InventoryAsset>();
            internal FileSystemInventory FileSystem;
            internal string PackageDigest = string.Empty;
            internal string Digest = string.Empty;
        }

        private sealed class InventoryAsset
        {
            internal string Path = string.Empty;
            internal StableAssetEvidence Evidence;
        }

        private sealed class FileSystemInventory
        {
            internal List<FileSystemEntry> Entries = new List<FileSystemEntry>();
            internal string Digest = string.Empty;
        }

        private sealed class FileSystemEntry
        {
            internal string Kind = string.Empty;
            internal string Path = string.Empty;
            internal string Digest = string.Empty;
            internal long Length;
        }

        private sealed class PackageTreeEvidence
        {
            internal int FileCount;
            internal string Digest = string.Empty;
        }

        private sealed class ExpectedFileEvidence
        {
            internal string Digest = string.Empty;
            internal long Length;
        }

        private sealed class AssetBackup
        {
            internal string AssetPath = string.Empty;
            internal byte[] FileBytes;
            internal byte[] MetaBytes;
            internal StableAssetEvidence Evidence;
        }

        private sealed class AtomicReferenceRenameException : Exception
        {
            internal AtomicReferenceRenameException(string message) : base(message)
            {
            }
        }
    }
}
