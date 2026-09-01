using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.PackageManager;
using UnityEditor.SceneManagement;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_configure_aao_merge_physbone",
        Summary = "When to use: preview or create one AAO 1.9.x Merge PhysBone component from exact existing VRCPhysBone object paths through AAO's public API. When NOT to use: do not use for automatic avatar optimization, arbitrary AAO components, existing Merge PhysBone edits, or missing/removed PhysBones. Negative example: do not use it to enable Trace and Optimize."
    )]
    public static class AaoMergePhysBoneTool
    {
        public const string ToolName = "vrc_configure_aao_merge_physbone";
        private const string ResultSchema = "vrcforge.aao_merge_physbone_result.v1";
        private const string PreviewDigestSchema = "vrcforge.aao_merge_physbone_preview.v1";
        private const string AaoPackageName = "com.anatawa12.avatar-optimizer";
        private const string MergeTypeName = "Anatawa12.AvatarOptimizer.MergePhysBone";
        private const string PhysBoneBaseTypeName = "VRC.Dynamics.VRCPhysBoneBase";
        private const int MinimumSources = 2;
        private const int MaximumSources = 16;

        private static readonly HashSet<string> RequestKeys = new HashSet<string>(
            new[]
            {
                "scenePath", "gameObjectPath", "sourcePhysBonePaths", "makeParent",
                "preview", "saveScene"
            },
            StringComparer.Ordinal);

        private static readonly HashSet<string> ExpectedKeys = new HashSet<string>(
            new[]
            {
                "expectedProjectPath", "expectedSceneGuid", "expectedSceneHandle",
                "expectedSceneFileDigest", "expectedSceneFileIdentity",
                "expectedSceneMetaDigest", "expectedSceneMetaIdentity",
                "expectedHostObjectId", "expectedAaoPackageVersion",
                "expectedComponentIndex", "expectedSourceDigest",
                "expectedBeforeStateDigest", "expectedTargetStateDigest",
                "expectedPreviewDigest"
            },
            StringComparer.Ordinal);

        public class Parameters
        {
            [VRCForgeInput("Saved scene asset path.", IsRequired = true)] public string scenePath { get; set; } = "";
            [VRCForgeInput("Exact scene object path that receives the AAO component.", IsRequired = true)] public string gameObjectPath { get; set; } = "";
            [VRCForgeInput("Two to sixteen exact scene object paths, each containing exactly one VRCPhysBone.", IsRequired = true)] public string[] sourcePhysBonePaths { get; set; } = new string[0];
            [VRCForgeInput("AAO Make Parent value.", IsRequired = true)] public bool makeParent { get; set; }
            [VRCForgeInput("Return a verified non-mutating plan.", IsRequired = true)] public bool preview { get; set; }
            [VRCForgeInput("Must be false for preview and true for apply.", IsRequired = true)] public bool saveScene { get; set; }
            [VRCForgeInput("Verified project path from preview; required for apply.", IsRequired = false)] public string expectedProjectPath { get; set; } = "";
            [VRCForgeInput("Verified scene GUID from preview; required for apply.", IsRequired = false)] public string expectedSceneGuid { get; set; } = "";
            [VRCForgeInput("Verified scene handle from preview; required for apply.", IsRequired = false)] public int? expectedSceneHandle { get; set; }
            [VRCForgeInput("Verified scene file digest from preview; required for apply.", IsRequired = false)] public string expectedSceneFileDigest { get; set; } = "";
            [VRCForgeInput("Verified scene file identity from preview; required for apply.", IsRequired = false)] public string expectedSceneFileIdentity { get; set; } = "";
            [VRCForgeInput("Verified scene metadata digest from preview; required for apply.", IsRequired = false)] public string expectedSceneMetaDigest { get; set; } = "";
            [VRCForgeInput("Verified scene metadata identity from preview; required for apply.", IsRequired = false)] public string expectedSceneMetaIdentity { get; set; } = "";
            [VRCForgeInput("Verified host object identity from preview; required for apply.", IsRequired = false)] public string expectedHostObjectId { get; set; } = "";
            [VRCForgeInput("Verified AAO package version from preview; required for apply.", IsRequired = false)] public string expectedAaoPackageVersion { get; set; } = "";
            [VRCForgeInput("Verified new component index from preview; required for apply.", IsRequired = false)] public int? expectedComponentIndex { get; set; }
            [VRCForgeInput("Verified exact source identity digest from preview; required for apply.", IsRequired = false)] public string expectedSourceDigest { get; set; } = "";
            [VRCForgeInput("Verified pre-mutation state digest from preview; required for apply.", IsRequired = false)] public string expectedBeforeStateDigest { get; set; } = "";
            [VRCForgeInput("Verified target state digest from preview; required for apply.", IsRequired = false)] public string expectedTargetStateDigest { get; set; } = "";
            [VRCForgeInput("Verified preview digest from preview; required for apply.", IsRequired = false)] public string expectedPreviewDigest { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var parameters = @params ?? throw new InvalidOperationException(
                    "AAO Merge PhysBone arguments are required.");
                var preview = ReadBoolean(parameters, "preview");
                var saveScene = ReadBoolean(parameters, "saveScene");
                if (preview == saveScene)
                {
                    throw new InvalidOperationException(
                        "AAO Merge PhysBone requires preview=true/saveScene=false or preview=false/saveScene=true.");
                }
                ValidateKeys(parameters, preview);
                var request = ParseRequest(parameters);
                var snapshot = BuildPreview(request);
                if (preview)
                {
                    return Success(snapshot.BuildPreviewPayload());
                }
                ValidateExpected(parameters, snapshot);
                return Apply(snapshot);
            }
            catch (Exception exception)
            {
                return Failure(exception.Message, false);
            }
        }

        private static MergeRequest ParseRequest(JObject parameters)
        {
            var paths = parameters["sourcePhysBonePaths"] as JArray
                ?? throw new InvalidOperationException("sourcePhysBonePaths must be an array.");
            if (paths.Count < MinimumSources || paths.Count > MaximumSources)
            {
                throw new InvalidOperationException("sourcePhysBonePaths exceeds its fixed bound.");
            }
            var normalized = paths.Select((token, index) =>
                NormalizeHierarchyPath(
                    token.Type == JTokenType.String ? token.Value<string>() : null,
                    "sourcePhysBonePaths[" + index.ToString(CultureInfo.InvariantCulture) + "]"))
                .ToArray();
            if (normalized.Distinct(StringComparer.Ordinal).Count() != normalized.Length)
            {
                throw new InvalidOperationException("sourcePhysBonePaths must be unique.");
            }
            return new MergeRequest
            {
                ScenePath = NormalizeScenePath(ReadText(parameters, "scenePath", 2048)),
                HostPath = NormalizeHierarchyPath(ReadText(parameters, "gameObjectPath", 2048), "gameObjectPath"),
                SourcePaths = normalized,
                MakeParent = ReadBoolean(parameters, "makeParent")
            };
        }

        private static MergeSnapshot BuildPreview(MergeRequest request)
        {
            var compatibility = ValidateCompatibility();
            var scene = ComponentFeatureWriteCore.ResolveSavedScene(request.ScenePath);
            if (scene.Dirty)
            {
                throw new InvalidOperationException("AAO Merge PhysBone requires a clean saved scene.");
            }
            var host = ComponentFeatureWriteCore.ResolveUniqueGameObject(scene.Scene, request.HostPath, "AAO host");
            var existing = host.GetComponents(compatibility.MergeType);
            if (existing.Length != 0)
            {
                throw new InvalidOperationException("The AAO Merge PhysBone CreateNew target already exists.");
            }
            var sources = request.SourcePaths.Select(path => ResolveSource(scene.Scene, path, compatibility)).ToArray();
            if (sources.Select(source => source.Id).Distinct(StringComparer.Ordinal).Count() != sources.Length)
            {
                throw new InvalidOperationException("Resolved VRCPhysBone identities must be unique.");
            }
            var hostId = ComponentFeatureWriteCore.StableGlobalObjectId(host, "AAO host");
            var sourcePayload = new JArray(sources.Select(source => source.ToPayload()));
            var sourceDigest = Digest(new JObject { ["sources"] = sourcePayload.DeepClone() });
            var before = new JObject
            {
                ["present"] = false,
                ["componentType"] = MergeTypeName
            };
            var target = new JObject
            {
                ["present"] = true,
                ["componentType"] = MergeTypeName,
                ["makeParent"] = request.MakeParent,
                ["sources"] = sourcePayload.DeepClone()
            };
            var snapshot = new MergeSnapshot
            {
                Request = request,
                Compatibility = compatibility,
                Scene = scene,
                Host = host,
                HostId = hostId,
                ComponentIndex = 0,
                Sources = sources,
                SourceDigest = sourceDigest,
                Before = before,
                Target = target,
                BeforeDigest = Digest(before),
                TargetDigest = Digest(target),
                ProjectPath = ComponentFeatureWriteCore.CurrentProjectPath()
            };
            var after = ComponentFeatureWriteCore.ResolveSavedScene(request.ScenePath);
            if (!ComponentFeatureWriteCore.SceneEvidenceMatches(scene, after)
                || after.Dirty
                || host.GetComponents(compatibility.MergeType).Length != 0)
            {
                throw new InvalidOperationException("AAO Merge PhysBone preview changed project state.");
            }
            var payload = snapshot.BuildPreviewPayload();
            snapshot.PreviewDigest = ComputePreviewDigest(payload);
            return snapshot;
        }

        private static object Apply(MergeSnapshot snapshot)
        {
            var immediate = BuildPreview(snapshot.Request);
            if (!SnapshotsMatch(snapshot, immediate))
            {
                throw new InvalidOperationException(
                    "AAO Merge PhysBone state changed after the verified preview.");
            }
            Undo.IncrementCurrentGroup();
            var undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("Configure AAO Merge PhysBone");
            Component created = null;
            try
            {
                created = Undo.AddComponent(snapshot.Host, snapshot.Compatibility.MergeType);
                if (created == null || created.GetType() != snapshot.Compatibility.MergeType)
                {
                    throw new InvalidOperationException("AAO Merge PhysBone component creation failed.");
                }
                InvokeExact(snapshot.Compatibility.InitializeMethod, created, 1);
                snapshot.Compatibility.MakeParentProperty.SetValue(created, snapshot.Request.MakeParent, null);
                var accessor = snapshot.Compatibility.PhysBonesProperty.GetValue(created, null);
                foreach (var source in snapshot.Sources)
                {
                    InvokeExact(snapshot.Compatibility.AccessorAddMethod, accessor, source.Component);
                }
                VerifyReadback(snapshot, ReadConfiguredState(snapshot, snapshot.Host));
                EditorSceneManager.MarkSceneDirty(snapshot.Scene.Scene);
                if (!EditorSceneManager.SaveScene(snapshot.Scene.Scene))
                {
                    throw new InvalidOperationException("AAO Merge PhysBone scene save failed.");
                }
                var after = ComponentFeatureWriteCore.ResolveSavedScene(snapshot.Request.ScenePath);
                if (after.Dirty
                    || after.Guid != snapshot.Scene.Guid
                    || after.Handle != snapshot.Scene.Handle
                    || after.FileDigest == snapshot.Scene.FileDigest
                    || after.MetaDigest != snapshot.Scene.MetaDigest
                    || after.MetaIdentity != snapshot.Scene.MetaIdentity)
                {
                    throw new InvalidOperationException("AAO Merge PhysBone saved scene evidence is invalid.");
                }
                // Independent post-write readback: re-resolve scene, host and component;
                // do not trust the object returned by Undo.AddComponent or the write accessor.
                var readbackHost = ComponentFeatureWriteCore.ResolveUniqueGameObject(
                    after.Scene,
                    snapshot.Request.HostPath,
                    "AAO post-write host");
                var readback = ReadConfiguredState(snapshot, readbackHost);
                VerifyReadback(snapshot, readback);
                Undo.CollapseUndoOperations(undoGroup);
                return Success(new JObject
                {
                    ["schema"] = ResultSchema,
                    ["preview"] = false,
                    ["verified"] = true,
                    ["changed"] = true,
                    ["saved"] = true,
                    ["mutationCount"] = 1,
                    ["projectPath"] = snapshot.ProjectPath,
                    ["scene"] = ScenePayload(snapshot.Scene, after),
                    ["host"] = HostPayload(snapshot),
                    ["before"] = snapshot.Before.DeepClone(),
                    ["target"] = snapshot.Target.DeepClone(),
                    ["readback"] = readback,
                    ["sourceDigest"] = snapshot.SourceDigest,
                    ["beforeStateDigest"] = snapshot.BeforeDigest,
                    ["targetStateDigest"] = snapshot.TargetDigest,
                    ["previewDigest"] = snapshot.PreviewDigest,
                    ["committed"] = true,
                    ["commitState"] = "committed",
                    ["checkpointRestoreRequired"] = false
                });
            }
            catch (Exception exception)
            {
                var restored = TryRestore(snapshot, created, undoGroup);
                return Failure(
                    restored
                        ? "AAO Merge PhysBone apply failed and the original scene was restored: " + exception.Message
                        : "AAO Merge PhysBone apply failed; checkpoint restore is required: " + exception.Message,
                    !restored);
            }
        }

        private static bool TryRestore(MergeSnapshot snapshot, Component created, int undoGroup)
        {
            try
            {
                if (created != null)
                {
                    UnityEngine.Object.DestroyImmediate(created);
                }
                Undo.RevertAllDownToGroup(undoGroup);
                EditorSceneManager.MarkSceneDirty(snapshot.Scene.Scene);
                if (!EditorSceneManager.SaveScene(snapshot.Scene.Scene))
                {
                    return false;
                }
                var after = ComponentFeatureWriteCore.ResolveSavedScene(snapshot.Request.ScenePath);
                var host = ComponentFeatureWriteCore.ResolveUniqueGameObject(
                    after.Scene,
                    snapshot.Request.HostPath,
                    "restored AAO host");
                return !after.Dirty
                    && ComponentFeatureWriteCore.SceneEvidenceMatches(snapshot.Scene, after)
                    && host.GetComponents(snapshot.Compatibility.MergeType).Length == 0;
            }
            catch
            {
                return false;
            }
        }

        private static JObject ReadConfiguredState(MergeSnapshot snapshot, GameObject host)
        {
            var components = host.GetComponents(snapshot.Compatibility.MergeType);
            if (components.Length != 1)
            {
                throw new InvalidOperationException("AAO Merge PhysBone readback is missing or ambiguous.");
            }
            var component = components[0];
            var makeParent = (bool)snapshot.Compatibility.MakeParentProperty.GetValue(component, null);
            var accessor = snapshot.Compatibility.PhysBonesProperty.GetValue(component, null);
            var readbackComponents = ((IEnumerable)accessor).Cast<object>()
                .Select(value => value as Component)
                .ToArray();
            if (readbackComponents.Any(value => value == null))
            {
                throw new InvalidOperationException("AAO Merge PhysBone returned an invalid PhysBone reference.");
            }
            var sourcesById = snapshot.Sources.ToDictionary(source => source.Id, StringComparer.Ordinal);
            var payload = new JArray();
            foreach (var readbackComponent in readbackComponents)
            {
                var id = ComponentFeatureWriteCore.StableGlobalObjectId(readbackComponent, "AAO source PhysBone");
                SourceEvidence source;
                if (!sourcesById.TryGetValue(id, out source))
                {
                    throw new InvalidOperationException("AAO Merge PhysBone returned an unexpected PhysBone reference.");
                }
                payload.Add(source.ToPayload());
            }
            return new JObject
            {
                ["present"] = true,
                ["componentType"] = MergeTypeName,
                ["makeParent"] = makeParent,
                ["sources"] = new JArray(payload.OrderBy(token => token.Value<string>("objectPath"), StringComparer.Ordinal))
            };
        }

        private static void VerifyReadback(MergeSnapshot snapshot, JObject readback)
        {
            var expected = (JObject)snapshot.Target.DeepClone();
            expected["sources"] = new JArray(((JArray)expected["sources"])
                .OrderBy(token => token.Value<string>("objectPath"), StringComparer.Ordinal));
            if (Digest(expected) != Digest(readback))
            {
                throw new InvalidOperationException("AAO Merge PhysBone readback does not match the exact target.");
            }
        }

        private static CompatibilityEvidence ValidateCompatibility()
        {
            var mergeType = RequireUniqueType(MergeTypeName);
            var physBoneType = RequireUniqueType(PhysBoneBaseTypeName);
            if (!typeof(Component).IsAssignableFrom(mergeType)
                || !typeof(Component).IsAssignableFrom(physBoneType))
            {
                throw new InvalidOperationException("AAO or VRCPhysBone public type is not a Unity component.");
            }
            var package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(mergeType.Assembly);
            if (package == null || package.name != AaoPackageName || !IsAao19(package.version))
            {
                throw new InvalidOperationException("AAO 1.9.x is required for Merge PhysBone public API writes.");
            }
            var initialize = RequirePublicMethod(mergeType, "Initialize", typeof(void), typeof(int));
            var makeParent = RequirePublicProperty(mergeType, "MakeParent", typeof(bool), true);
            var physBones = mergeType.GetProperty("PhysBones", BindingFlags.Instance | BindingFlags.Public)
                ?? throw new InvalidOperationException("AAO PhysBones public accessor is unavailable.");
            if (!physBones.CanRead || physBones.GetIndexParameters().Length != 0)
            {
                throw new InvalidOperationException("AAO PhysBones public accessor is unsupported.");
            }
            var add = RequirePublicMethod(physBones.PropertyType, "Add", typeof(void), physBoneType);
            if (!typeof(IEnumerable).IsAssignableFrom(physBones.PropertyType))
            {
                throw new InvalidOperationException("AAO PhysBones public accessor is not enumerable.");
            }
            return new CompatibilityEvidence
            {
                PackageVersion = package.version,
                MergeType = mergeType,
                PhysBoneBaseType = physBoneType,
                InitializeMethod = initialize,
                MakeParentProperty = makeParent,
                PhysBonesProperty = physBones,
                AccessorAddMethod = add
            };
        }

        private static SourceEvidence ResolveSource(
            UnityEngine.SceneManagement.Scene scene,
            string path,
            CompatibilityEvidence compatibility)
        {
            var gameObject = ComponentFeatureWriteCore.ResolveUniqueGameObject(scene, path, "source PhysBone");
            var components = gameObject.GetComponents(compatibility.PhysBoneBaseType);
            if (components.Length != 1)
            {
                throw new InvalidOperationException(
                    "Each sourcePhysBonePaths object must contain exactly one VRCPhysBone.");
            }
            return new SourceEvidence
            {
                Path = path,
                Component = components[0],
                Id = ComponentFeatureWriteCore.StableGlobalObjectId(components[0], "source PhysBone")
            };
        }

        private static void ValidateExpected(JObject parameters, MergeSnapshot snapshot)
        {
            var expectedHandle = ReadInteger(parameters, "expectedSceneHandle");
            var expectedIndex = ReadInteger(parameters, "expectedComponentIndex");
            if (!ComponentFeatureWriteCore.ProjectPathMatches(ReadText(parameters, "expectedProjectPath", 4096))
                || ReadText(parameters, "expectedSceneGuid", 64) != snapshot.Scene.Guid
                || expectedHandle != snapshot.Scene.Handle
                || ReadText(parameters, "expectedSceneFileDigest", 128) != snapshot.Scene.FileDigest
                || ReadText(parameters, "expectedSceneFileIdentity", 128) != snapshot.Scene.FileIdentity
                || ReadText(parameters, "expectedSceneMetaDigest", 128) != snapshot.Scene.MetaDigest
                || ReadText(parameters, "expectedSceneMetaIdentity", 128) != snapshot.Scene.MetaIdentity
                || ReadText(parameters, "expectedHostObjectId", 256) != snapshot.HostId
                || ReadText(parameters, "expectedAaoPackageVersion", 128) != snapshot.Compatibility.PackageVersion
                || expectedIndex != snapshot.ComponentIndex
                || ReadText(parameters, "expectedSourceDigest", 128) != snapshot.SourceDigest
                || ReadText(parameters, "expectedBeforeStateDigest", 128) != snapshot.BeforeDigest
                || ReadText(parameters, "expectedTargetStateDigest", 128) != snapshot.TargetDigest
                || ReadText(parameters, "expectedPreviewDigest", 128) != snapshot.PreviewDigest)
            {
                throw new InvalidOperationException(
                    "AAO Merge PhysBone state changed after the verified preview.");
            }
        }

        private static bool SnapshotsMatch(MergeSnapshot left, MergeSnapshot right)
        {
            return left.ProjectPath == right.ProjectPath
                && ComponentFeatureWriteCore.SceneEvidenceMatches(left.Scene, right.Scene)
                && left.HostId == right.HostId
                && left.Compatibility.PackageVersion == right.Compatibility.PackageVersion
                && left.ComponentIndex == right.ComponentIndex
                && left.SourceDigest == right.SourceDigest
                && left.BeforeDigest == right.BeforeDigest
                && left.TargetDigest == right.TargetDigest
                && left.PreviewDigest == right.PreviewDigest;
        }

        private static void ValidateKeys(JObject parameters, bool preview)
        {
            var allowed = new HashSet<string>(RequestKeys, StringComparer.Ordinal);
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
                throw new InvalidOperationException("AAO Merge PhysBone arguments contain unsupported fields.");
            }
            if (RequestKeys.Any(key => parameters.Property(key, StringComparison.Ordinal) == null)
                || (!preview && ExpectedKeys.Any(key => parameters.Property(key, StringComparison.Ordinal) == null)))
            {
                throw new InvalidOperationException("AAO Merge PhysBone arguments are incomplete.");
            }
        }

        private static JObject ScenePayload(
            ComponentFeatureSceneEvidence before,
            ComponentFeatureSceneEvidence after)
        {
            return before.ToPreviewPayload(after);
        }

        private static JObject HostPayload(MergeSnapshot snapshot)
        {
            return new JObject
            {
                ["objectPath"] = snapshot.Request.HostPath,
                ["objectId"] = snapshot.HostId,
                ["componentType"] = MergeTypeName,
                ["componentIndex"] = snapshot.ComponentIndex
            };
        }

        private static string ComputePreviewDigest(JObject payload)
        {
            var committed = new JObject
            {
                ["schema"] = payload["schema"]?.DeepClone(),
                ["projectPath"] = payload["projectPath"]?.DeepClone(),
                ["compatibility"] = payload["compatibility"]?.DeepClone(),
                ["scene"] = payload["scene"]?.DeepClone(),
                ["host"] = payload["host"]?.DeepClone(),
                ["before"] = payload["before"]?.DeepClone(),
                ["target"] = payload["target"]?.DeepClone(),
                ["sourceDigest"] = payload["sourceDigest"]?.DeepClone(),
                ["beforeStateDigest"] = payload["beforeStateDigest"]?.DeepClone(),
                ["targetStateDigest"] = payload["targetStateDigest"]?.DeepClone(),
                ["wouldChange"] = payload["wouldChange"]?.DeepClone()
            };
            return Digest(new JObject
            {
                ["schema"] = PreviewDigestSchema,
                ["payload"] = committed
            });
        }

        private static object Success(JObject payload)
        {
            return VRCForgeToolResult.Completed(
                payload.Value<bool?>("preview") == true
                    ? "AAO Merge PhysBone preview completed."
                    : "AAO Merge PhysBone component configured and verified.",
                payload);
        }

        private static object Failure(string message, bool checkpointRestoreRequired)
        {
            return VRCForgeToolResult.Failed(
                message,
                new JObject
                {
                    ["schema"] = ResultSchema,
                    ["ok"] = false,
                    ["committed"] = false,
                    ["commitState"] = checkpointRestoreRequired
                        ? "checkpoint_restore_required"
                        : "not_started_or_restored",
                    ["checkpointRestoreRequired"] = checkpointRestoreRequired
                });
        }

        private static Type RequireUniqueType(string fullName)
        {
            var matches = AppDomain.CurrentDomain.GetAssemblies()
                .Select(assembly =>
                {
                    try { return assembly.GetType(fullName, false, false); }
                    catch { return null; }
                })
                .Where(type => type != null)
                .Distinct()
                .ToArray();
            if (matches.Length != 1)
            {
                throw new InvalidOperationException("Required public type is missing or ambiguous: " + fullName);
            }
            return matches[0];
        }

        private static MethodInfo RequirePublicMethod(
            Type type,
            string name,
            Type returnType,
            params Type[] parameterTypes)
        {
            var method = type.GetMethod(
                name,
                BindingFlags.Instance | BindingFlags.Public,
                null,
                parameterTypes,
                null);
            if (method == null || method.ReturnType != returnType || method.IsStatic)
            {
                throw new InvalidOperationException("Required public method is unavailable: " + name);
            }
            return method;
        }

        private static PropertyInfo RequirePublicProperty(
            Type type,
            string name,
            Type propertyType,
            bool writable)
        {
            var property = type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public);
            if (property == null || property.PropertyType != propertyType || !property.CanRead
                || (writable && !property.CanWrite) || property.GetIndexParameters().Length != 0)
            {
                throw new InvalidOperationException("Required public property is unavailable: " + name);
            }
            return property;
        }

        private static object InvokeExact(MethodInfo method, object target, params object[] arguments)
        {
            try
            {
                return method.Invoke(target, arguments);
            }
            catch (TargetInvocationException exception)
            {
                throw new InvalidOperationException(
                    "AAO public API call failed: " + (exception.InnerException?.Message ?? exception.Message));
            }
        }

        private static bool IsAao19(string version)
        {
            var parts = (version ?? string.Empty).Split('.', '-', '+');
            return parts.Length >= 2 && parts[0] == "1" && parts[1] == "9";
        }

        private static bool ReadBoolean(JObject parameters, string key)
        {
            var token = parameters[key];
            if (token == null || token.Type != JTokenType.Boolean)
            {
                throw new InvalidOperationException(key + " must be a boolean.");
            }
            return token.Value<bool>();
        }

        private static int ReadInteger(JObject parameters, string key)
        {
            var token = parameters[key];
            if (token == null || token.Type != JTokenType.Integer)
            {
                throw new InvalidOperationException(key + " must be an integer.");
            }
            return token.Value<int>();
        }

        private static string ReadText(JObject parameters, string key, int maximumLength)
        {
            var token = parameters[key];
            if (token == null || token.Type != JTokenType.String)
            {
                throw new InvalidOperationException(key + " must be text.");
            }
            var value = token.Value<string>() ?? string.Empty;
            if (string.IsNullOrWhiteSpace(value) || value.Length > maximumLength)
            {
                throw new InvalidOperationException(key + " is empty or exceeds its fixed bound.");
            }
            return value.Trim();
        }

        private static string NormalizeScenePath(string value)
        {
            var normalized = value.Replace('\\', '/');
            if (!normalized.StartsWith("Assets/", StringComparison.Ordinal)
                || !normalized.EndsWith(".unity", StringComparison.OrdinalIgnoreCase)
                || normalized.Contains("//") || normalized.Split('/').Any(part => part == "." || part == ".."))
            {
                throw new InvalidOperationException("scenePath must be a canonical saved scene under Assets.");
            }
            return normalized;
        }

        private static string NormalizeHierarchyPath(string value, string label)
        {
            var normalized = (value ?? string.Empty).Trim();
            if (normalized.Length == 0 || normalized.Length > 2048
                || normalized[0] == '/' || normalized[normalized.Length - 1] == '/'
                || normalized.Contains("\\") || normalized.Contains("//")
                || normalized.Split('/').Any(part => part.Length == 0 || part == "." || part == ".."))
            {
                throw new InvalidOperationException(label + " must be a canonical exact hierarchy path.");
            }
            return normalized;
        }

        private static string Digest(JToken token)
        {
            var canonical = CanonicalJson(token);
            using (var sha256 = SHA256.Create())
            {
                return BitConverter.ToString(sha256.ComputeHash(Encoding.UTF8.GetBytes(canonical)))
                    .Replace("-", string.Empty)
                    .ToLowerInvariant();
            }
        }

        private static string CanonicalJson(JToken token)
        {
            if (token == null || token.Type == JTokenType.Null) return "null";
            if (token is JObject obj)
            {
                return "{" + string.Join(",", obj.Properties()
                    .OrderBy(property => property.Name, StringComparer.Ordinal)
                    .Select(property => JsonConvert.ToString(property.Name) + ":" + CanonicalJson(property.Value))) + "}";
            }
            if (token is JArray array) return "[" + string.Join(",", array.Select(CanonicalJson)) + "]";
            if (token.Type == JTokenType.String) return JsonConvert.ToString(token.Value<string>() ?? string.Empty);
            if (token.Type == JTokenType.Boolean) return token.Value<bool>() ? "true" : "false";
            if (token.Type == JTokenType.Integer)
            {
                return Convert.ToString(((JValue)token).Value, CultureInfo.InvariantCulture);
            }
            throw new InvalidOperationException("AAO Merge PhysBone preview contains a non-canonical value.");
        }

        private sealed class MergeRequest
        {
            internal string ScenePath = string.Empty;
            internal string HostPath = string.Empty;
            internal string[] SourcePaths = new string[0];
            internal bool MakeParent;
        }

        private sealed class SourceEvidence
        {
            internal string Path = string.Empty;
            internal Component Component;
            internal string Id = string.Empty;

            internal JObject ToPayload()
            {
                return new JObject
                {
                    ["objectPath"] = Path,
                    ["componentType"] = PhysBoneBaseTypeName,
                    ["componentId"] = Id
                };
            }
        }

        private sealed class CompatibilityEvidence
        {
            internal string PackageVersion = string.Empty;
            internal Type MergeType;
            internal Type PhysBoneBaseType;
            internal MethodInfo InitializeMethod;
            internal PropertyInfo MakeParentProperty;
            internal PropertyInfo PhysBonesProperty;
            internal MethodInfo AccessorAddMethod;

            internal JObject ToPayload()
            {
                return new JObject
                {
                    ["packageName"] = AaoPackageName,
                    ["packageVersion"] = PackageVersion,
                    ["mergeType"] = MergeTypeName,
                    ["physBoneBaseType"] = PhysBoneBaseTypeName,
                    ["initializeSignature"] = "System.Void Initialize(System.Int32)",
                    ["makeParentProperty"] = "System.Boolean MakeParent",
                    ["physBonesAddSignature"] = "System.Void Add(VRC.Dynamics.VRCPhysBoneBase)"
                };
            }
        }

        private sealed class MergeSnapshot
        {
            internal MergeRequest Request;
            internal CompatibilityEvidence Compatibility;
            internal ComponentFeatureSceneEvidence Scene;
            internal GameObject Host;
            internal string HostId = string.Empty;
            internal int ComponentIndex;
            internal SourceEvidence[] Sources;
            internal string SourceDigest = string.Empty;
            internal JObject Before;
            internal JObject Target;
            internal string BeforeDigest = string.Empty;
            internal string TargetDigest = string.Empty;
            internal string ProjectPath = string.Empty;
            internal string PreviewDigest = string.Empty;

            internal JObject BuildPreviewPayload()
            {
                var after = ComponentFeatureWriteCore.ResolveSavedScene(Request.ScenePath);
                var payload = new JObject
                {
                    ["schema"] = ResultSchema,
                    ["preview"] = true,
                    ["verified"] = true,
                    ["changed"] = false,
                    ["saved"] = false,
                    ["mutationCount"] = 0,
                    ["projectPath"] = ProjectPath,
                    ["compatibility"] = Compatibility.ToPayload(),
                    ["scene"] = ScenePayload(Scene, after),
                    ["host"] = HostPayload(this),
                    ["before"] = Before.DeepClone(),
                    ["target"] = Target.DeepClone(),
                    ["sourceDigest"] = SourceDigest,
                    ["beforeStateDigest"] = BeforeDigest,
                    ["targetStateDigest"] = TargetDigest,
                    ["wouldChange"] = true,
                    ["committed"] = false,
                    ["commitState"] = "not_started",
                    ["checkpointRestoreRequired"] = false
                };
                payload["previewDigest"] = string.IsNullOrEmpty(PreviewDigest)
                    ? ComputePreviewDigest(payload)
                    : PreviewDigest;
                return payload;
            }
        }
    }
}
