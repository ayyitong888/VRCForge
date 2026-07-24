using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_create_component_feature",
        Description = "Preview or CreateNew one fixed-schema component feature through the supervised scene-write lane."
    )]
    public static class ComponentFeatureWriterTool
    {
        public const string ToolName = "vrc_create_component_feature";

        private static readonly HashSet<string> CommonRequestKeys = new HashSet<string>(
            new[] { "scenePath", "gameObjectPath", "featureKind", "preview", "saveScene" },
            StringComparer.Ordinal);

        private static readonly HashSet<string> ToggleRequestKeys = new HashSet<string>(
            new[] { "menuPath", "targetObjectPaths", "slider", "defaultOn", "saved", "globalParameter" },
            StringComparer.Ordinal);

        private static readonly HashSet<string> ArmatureRequestKeys = new HashSet<string>(
            new[] { "linkFromPath", "linkTargets", "recursive", "align" },
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
                "expectedHostObjectId",
                "expectedComponentType",
                "expectedComponentIndex",
                "expectedComponentIdentitySeed",
                "expectedBeforeFeatureDigest",
                "expectedTargetFeatureDigest",
                "expectedCompatibilityDigest",
                "expectedPreviewDigest"
            },
            StringComparer.Ordinal);

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var parameters = @params ?? throw new ComponentFeatureWriteException(
                    "Component feature arguments are required.");
                var preview = ReadStrictBoolean(parameters, "preview");
                var saveScene = ReadStrictBoolean(parameters, "saveScene");
                var request = ParseRequest(parameters, preview);
                if (preview && saveScene)
                {
                    throw new ComponentFeatureWriteException("Component feature preview cannot save a scene.");
                }
                if (!preview && !saveScene)
                {
                    throw new ComponentFeatureWriteException("saveScene must be true for component feature apply.");
                }

                var snapshot = ComponentFeatureWriteCore.BuildPreview(request);
                if (preview)
                {
                    return ComponentFeatureWriteCore.Success(
                        snapshot.ToPayload(ComponentFeatureWriteCore.ResolveSavedScene(request.ScenePath)));
                }

                ValidateExpected(parameters, snapshot);
                return Apply(snapshot);
            }
            catch (Exception exception)
            {
                return ComponentFeatureWriteCore.Failure(exception);
            }
        }

        private static object Apply(ComponentFeaturePreviewSnapshot snapshot)
        {
            var immediate = ComponentFeatureWriteCore.ResolveSavedScene(
                snapshot.Request.ScenePath);
            var immediateRoots = ComponentFeatureWriteCore.GetRootComponents(
                snapshot.Host.Host,
                snapshot.Compatibility.RootComponentType);
            if (!ComponentFeatureWriteCore.SceneEvidenceMatches(snapshot.Scene, immediate)
                || immediate.Dirty
                || ComponentFeatureWriteCore.StableGlobalObjectId(
                    snapshot.Host.Host,
                    "feature host") != snapshot.Host.ObjectId
                || immediateRoots.Count != snapshot.Host.ComponentIndex)
            {
                throw new ComponentFeatureWriteException(
                    "Component feature state changed immediately before apply.");
            }
            Undo.IncrementCurrentGroup();
            var undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("Create VRCForge component feature");
            Component created = null;
            var mutationStarted = false;
            try
            {
                mutationStarted = true;
                Undo.RegisterCompleteObjectUndo(
                    snapshot.Host.Host,
                    "Create VRCForge component feature");
                ComponentFeatureWriteCore.InvokePublicCreate(snapshot, out created);
                if (created == null)
                {
                    throw new ComponentFeatureWriteException("The component feature component was not created.");
                }
                Undo.RegisterCreatedObjectUndo(created, "Create VRCForge component feature");
                var inMemoryReadback = ComponentFeatureWriteCore.ReadExactFeature(
                    created,
                    snapshot.Request.FeatureKind);
                ComponentFeatureWriteCore.VerifyReadback(snapshot, created, inMemoryReadback);

                EditorSceneManager.MarkSceneDirty(snapshot.Scene.Scene);
                if (!EditorSceneManager.SaveScene(snapshot.Scene.Scene))
                {
                    throw new ComponentFeatureWriteException("The component feature scene could not be saved.");
                }
                var after = ComponentFeatureWriteCore.ResolveSavedScene(snapshot.Request.ScenePath);
                if (after.Dirty
                    || after.Guid != snapshot.Scene.Guid
                    || after.Handle != snapshot.Scene.Handle
                    || after.FileDigest == snapshot.Scene.FileDigest
                    || after.MetaDigest != snapshot.Scene.MetaDigest
                    || after.MetaIdentity != snapshot.Scene.MetaIdentity)
                {
                    throw new ComponentFeatureWriteException("The component feature saved scene evidence is invalid.");
                }
                var components = ComponentFeatureWriteCore.GetRootComponents(
                    snapshot.Host.Host,
                    snapshot.Compatibility.RootComponentType);
                if (components.Count != snapshot.Host.ComponentIndex + 1
                    || !ReferenceEquals(components[snapshot.Host.ComponentIndex], created))
                {
                    throw new ComponentFeatureWriteException("The component feature persisted component identity changed.");
                }
                var persistedReadback = ComponentFeatureWriteCore.ReadExactFeature(
                    created,
                    snapshot.Request.FeatureKind);
                ComponentFeatureWriteCore.VerifyReadback(snapshot, created, persistedReadback);
                Undo.CollapseUndoOperations(undoGroup);
                return ComponentFeatureWriteCore.Success(
                    ComponentFeatureWriteCore.BuildApplyPayload(
                        snapshot,
                        created,
                        persistedReadback,
                        after));
            }
            catch (Exception)
            {
                if (!mutationStarted)
                {
                    throw;
                }
                var restored = TryRestoreFailedApply(snapshot, created, undoGroup);
                return ComponentFeatureWriteCore.BuildMutationFailure(restored);
            }
        }

        private static bool TryRestoreFailedApply(
            ComponentFeaturePreviewSnapshot snapshot,
            Component created,
            int undoGroup)
        {
            try
            {
                if (created != null)
                {
                    UnityEngine.Object.DestroyImmediate(created);
                }
                else
                {
                    var candidates = ComponentFeatureWriteCore.GetRootComponents(
                        snapshot.Host.Host,
                        snapshot.Compatibility.RootComponentType);
                    var expectedType = snapshot.Request.FeatureKind == ComponentFeatureWriteCore.ToggleKind
                        ? ComponentFeatureWriteCore.ToggleSerializedType
                        : ComponentFeatureWriteCore.ArmatureSerializedType;
                    var createdCandidates = candidates
                        .Skip(snapshot.Host.ComponentIndex)
                        .Where(component => ComponentFeatureWriteCore.ReadManagedReferenceType(component) == expectedType)
                        .ToList();
                    if (createdCandidates.Count == 1)
                    {
                        UnityEngine.Object.DestroyImmediate(createdCandidates[0]);
                    }
                }
                Undo.RevertAllDownToGroup(undoGroup);
                EditorSceneManager.MarkSceneDirty(snapshot.Scene.Scene);
                if (!EditorSceneManager.SaveScene(snapshot.Scene.Scene))
                {
                    return false;
                }
                var after = ComponentFeatureWriteCore.ResolveSavedScene(snapshot.Request.ScenePath);
                var roots = ComponentFeatureWriteCore.GetRootComponents(
                    snapshot.Host.Host,
                    snapshot.Compatibility.RootComponentType);
                return roots.Count == snapshot.Host.ComponentIndex
                    && after.Guid == snapshot.Scene.Guid
                    && after.Handle == snapshot.Scene.Handle
                    && after.FileDigest == snapshot.Scene.FileDigest
                    && after.MetaDigest == snapshot.Scene.MetaDigest
                    && after.MetaIdentity == snapshot.Scene.MetaIdentity
                    && !after.Dirty;
            }
            catch
            {
                return false;
            }
        }

        private static ComponentFeatureRequest ParseRequest(JObject parameters, bool preview)
        {
            var featureKind = ReadRequiredText(parameters, "featureKind", 128);
            if (featureKind != ComponentFeatureWriteCore.ToggleKind
                && featureKind != ComponentFeatureWriteCore.ArmatureLinkKind)
            {
                throw new ComponentFeatureWriteException("featureKind is unsupported.");
            }
            var featureKeys = featureKind == ComponentFeatureWriteCore.ToggleKind
                ? ToggleRequestKeys
                : ArmatureRequestKeys;
            var allowed = new HashSet<string>(CommonRequestKeys, StringComparer.Ordinal);
            allowed.UnionWith(featureKeys);
            if (preview)
            {
                allowed.Add("expectedProjectPath");
            }
            else
            {
                allowed.UnionWith(ExpectedKeys);
            }
            if (parameters.Properties().Any(property => !allowed.Contains(property.Name)))
            {
                throw new ComponentFeatureWriteException(
                    "Component feature arguments contain unsupported fields.");
            }
            if (CommonRequestKeys.Concat(featureKeys)
                .Any(key => parameters.Property(key, StringComparison.Ordinal) == null))
            {
                throw new ComponentFeatureWriteException("Component feature arguments are incomplete.");
            }
            if (!preview && ExpectedKeys.Any(key => parameters.Property(key, StringComparison.Ordinal) == null))
            {
                throw new ComponentFeatureWriteException(
                    "Verified component feature preconditions are required for apply.");
            }

            var request = new ComponentFeatureRequest
            {
                ScenePath = NormalizeScenePath(ReadRequiredText(parameters, "scenePath", 2048)),
                GameObjectPath = NormalizeHierarchyPath(
                    ReadRequiredText(parameters, "gameObjectPath", 2048),
                    "gameObjectPath"),
                FeatureKind = featureKind
            };
            if (featureKind == ComponentFeatureWriteCore.ToggleKind)
            {
                request.MenuPath = NormalizeHierarchyPath(
                    ReadRequiredText(parameters, "menuPath", 2048),
                    "menuPath");
                request.TargetObjectPaths = ReadUniquePathArray(
                    parameters,
                    "targetObjectPaths",
                    1,
                    32);
                request.Slider = ReadStrictBoolean(parameters, "slider");
                request.DefaultOn = ReadStrictBoolean(parameters, "defaultOn");
                request.Saved = ReadStrictBoolean(parameters, "saved");
                request.GlobalParameter = ReadOptionalText(parameters, "globalParameter", 128);
                if (request.GlobalParameter.Any(character =>
                    !((character >= 'A' && character <= 'Z')
                        || (character >= 'a' && character <= 'z')
                        || (character >= '0' && character <= '9')
                        || character == '_'
                        || character == '.'
                        || character == '-')))
                {
                    throw new ComponentFeatureWriteException("globalParameter is invalid.");
                }
                return request;
            }

            request.LinkFromPath = NormalizeHierarchyPath(
                ReadRequiredText(parameters, "linkFromPath", 2048),
                "linkFromPath");
            request.Recursive = ReadStrictBoolean(parameters, "recursive");
            request.Align = ReadStrictBoolean(parameters, "align");
            var links = parameters["linkTargets"] as JArray
                ?? throw new ComponentFeatureWriteException("linkTargets must be an array.");
            if (links.Count < 1 || links.Count > 8)
            {
                throw new ComponentFeatureWriteException("linkTargets exceeds its fixed bound.");
            }
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var token in links)
            {
                var item = token as JObject
                    ?? throw new ComponentFeatureWriteException(
                        "Each armature target must match its fixed schema.");
                if (!new HashSet<string>(
                    item.Properties().Select(property => property.Name),
                    StringComparer.Ordinal).SetEquals(new[] { "targetKind", "target", "offset" }))
                {
                    throw new ComponentFeatureWriteException(
                        "Each armature target must match its fixed schema.");
                }
                var targetKind = ReadRequiredText(item, "targetKind", 128);
                if (targetKind != "humanoid_bone"
                    && targetKind != "game_object"
                    && targetKind != "relative_path")
                {
                    throw new ComponentFeatureWriteException("Armature target kind is unsupported.");
                }
                var target = ReadRequiredText(item, "target", 2048);
                if (targetKind != "humanoid_bone")
                {
                    target = NormalizeHierarchyPath(target, "target");
                }
                else if (!Enum.TryParse(target, false, out HumanBodyBones bone)
                    || bone == HumanBodyBones.LastBone)
                {
                    throw new ComponentFeatureWriteException("The humanoid bone target is unsupported.");
                }
                var offset = ReadOptionalText(item, "offset", 512);
                if (!string.IsNullOrEmpty(offset))
                {
                    offset = NormalizeHierarchyPath(offset, "offset");
                }
                if (targetKind == "relative_path" && !string.IsNullOrEmpty(offset))
                {
                    throw new ComponentFeatureWriteException(
                        "relative_path targets cannot include a second offset.");
                }
                var key = targetKind + "\u001f" + target + "\u001f" + offset;
                if (!seen.Add(key))
                {
                    throw new ComponentFeatureWriteException(
                        "Duplicate armature targets are not supported.");
                }
                request.LinkTargets.Add(new ComponentFeatureLinkRequest
                {
                    TargetKind = targetKind,
                    Target = target,
                    Offset = offset
                });
            }
            return request;
        }

        private static void ValidateExpected(
            JObject parameters,
            ComponentFeaturePreviewSnapshot snapshot)
        {
            if (!ComponentFeatureWriteCore.ProjectPathMatches(
                    ReadRequiredText(parameters, "expectedProjectPath", 32768))
                || ReadRequiredHex(parameters, "expectedSceneGuid", 32) != snapshot.Scene.Guid
                || ReadStrictInteger(parameters, "expectedSceneHandle") != snapshot.Scene.Handle
                || ReadRequiredHex(parameters, "expectedSceneFileDigest", 64) != snapshot.Scene.FileDigest
                || ReadRequiredHex(parameters, "expectedSceneFileIdentity", 64) != snapshot.Scene.FileIdentity
                || ReadRequiredHex(parameters, "expectedSceneMetaDigest", 64) != snapshot.Scene.MetaDigest
                || ReadRequiredHex(parameters, "expectedSceneMetaIdentity", 64) != snapshot.Scene.MetaIdentity
                || ReadRequiredText(parameters, "expectedHostObjectId", 512) != snapshot.Host.ObjectId
                || ReadRequiredText(parameters, "expectedComponentType", 512) != snapshot.Host.ComponentType
                || ReadStrictInteger(parameters, "expectedComponentIndex") != snapshot.Host.ComponentIndex
                || ReadRequiredHex(parameters, "expectedComponentIdentitySeed", 64)
                    != snapshot.Host.ComponentIdentitySeed
                || ReadRequiredHex(parameters, "expectedBeforeFeatureDigest", 64) != snapshot.BeforeDigest
                || ReadRequiredHex(parameters, "expectedTargetFeatureDigest", 64) != snapshot.TargetDigest
                || ReadRequiredHex(parameters, "expectedCompatibilityDigest", 64)
                    != snapshot.Compatibility.Digest
                || ReadRequiredHex(parameters, "expectedPreviewDigest", 64) != snapshot.PreviewDigest)
            {
                throw new ComponentFeatureWriteException(
                    "Component feature state changed after the verified preview.");
            }
        }

        private static string ReadRequiredText(JObject source, string key, int maximum)
        {
            var token = source[key];
            if (token == null || token.Type != JTokenType.String)
            {
                throw new ComponentFeatureWriteException(key + " must be text.");
            }
            var value = token.Value<string>()?.Trim() ?? string.Empty;
            if (value.Length == 0
                || value.Length > maximum
                || value.Any(character => character < 32))
            {
                throw new ComponentFeatureWriteException(key + " is invalid.");
            }
            return value;
        }

        private static string ReadOptionalText(JObject source, string key, int maximum)
        {
            var token = source[key];
            if (token == null || token.Type != JTokenType.String)
            {
                throw new ComponentFeatureWriteException(key + " must be text.");
            }
            var value = token.Value<string>()?.Trim() ?? string.Empty;
            if (value.Length > maximum || value.Any(character => character < 32))
            {
                throw new ComponentFeatureWriteException(key + " is invalid.");
            }
            return value;
        }

        private static bool ReadStrictBoolean(JObject source, string key)
        {
            var token = source[key];
            if (token == null || token.Type != JTokenType.Boolean)
            {
                throw new ComponentFeatureWriteException(key + " must be a boolean.");
            }
            return token.Value<bool>();
        }

        private static int ReadStrictInteger(JObject source, string key)
        {
            var token = source[key];
            if (token == null || token.Type != JTokenType.Integer)
            {
                throw new ComponentFeatureWriteException(key + " must be an integer.");
            }
            var value = token.Value<long>();
            if (value < int.MinValue || value > int.MaxValue)
            {
                throw new ComponentFeatureWriteException(key + " is out of range.");
            }
            return (int)value;
        }

        private static string ReadRequiredHex(JObject source, string key, int length)
        {
            var value = ReadRequiredText(source, key, length).ToLowerInvariant();
            if (value.Length != length || value.Any(character => !Uri.IsHexDigit(character)))
            {
                throw new ComponentFeatureWriteException(key + " is invalid.");
            }
            return value;
        }

        private static List<string> ReadUniquePathArray(
            JObject source,
            string key,
            int minimum,
            int maximum)
        {
            var array = source[key] as JArray
                ?? throw new ComponentFeatureWriteException(key + " must be an array.");
            if (array.Count < minimum || array.Count > maximum)
            {
                throw new ComponentFeatureWriteException(key + " exceeds its fixed bound.");
            }
            var values = array.Select(item =>
            {
                if (item.Type != JTokenType.String)
                {
                    throw new ComponentFeatureWriteException(key + " entries must be text.");
                }
                return NormalizeHierarchyPath(item.Value<string>() ?? string.Empty, key);
            }).ToList();
            if (values.Distinct(StringComparer.Ordinal).Count() != values.Count)
            {
                throw new ComponentFeatureWriteException(key + " entries must be unique.");
            }
            return values;
        }

        private static string NormalizeScenePath(string value)
        {
            var normalized = value.Replace('\\', '/');
            var parts = normalized.Split('/');
            if (!normalized.StartsWith("Assets/", StringComparison.Ordinal)
                || !normalized.EndsWith(".unity", StringComparison.OrdinalIgnoreCase)
                || normalized.StartsWith("/", StringComparison.Ordinal)
                || normalized.EndsWith("/", StringComparison.Ordinal)
                || parts.Any(part => string.IsNullOrWhiteSpace(part) || part == "." || part == ".."))
            {
                throw new ComponentFeatureWriteException(
                    "scenePath must select a saved scene under Assets/.");
            }
            return normalized;
        }

        private static string NormalizeHierarchyPath(string value, string label)
        {
            var normalized = (value ?? string.Empty).Trim();
            var parts = normalized.Split('/');
            if (normalized.Length == 0
                || normalized.Length > 2048
                || normalized.IndexOf('\\') >= 0
                || normalized.StartsWith("/", StringComparison.Ordinal)
                || normalized.EndsWith("/", StringComparison.Ordinal)
                || parts.Any(part => string.IsNullOrWhiteSpace(part) || part == "." || part == ".."))
            {
                throw new ComponentFeatureWriteException(label + " path is invalid.");
            }
            return normalized;
        }
    }
}
