using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    /// <summary>
    /// Exports a loaded humanoid avatar through UniVRM's VRM 1.0 static API.
    /// The optional dependency is resolved by its pinned public type contract so
    /// projects without UniVRM continue to compile and never receive a fake file.
    /// </summary>
    [McpForUnityTool(
        name: "vrc_export_vrm",
        Description = "Export one loaded humanoid avatar as a validated VRM 1.0 file through an installed compatible UniVRM package."
    )]
    public static class VrmExporter
    {
        public const string ToolName = "vrc_export_vrm";
        public const string DefaultOutputPath = "Assets/VRCForge/Exports/avatar.vrm";

        private const string ExporterTypeName = "UniVRM10.Vrm10Exporter";
        private const string SettingsTypeName = "UniGLTF.GltfExportSettings";
        private const string MetaTypeName = "UniVRM10.VRM10ObjectMeta";
        private const string MaterialExporterTypeName = "UniGLTF.IMaterialExporter";
        private const string TextureSerializerTypeName = "UniGLTF.ITextureSerializer";
        private const uint GlbJsonChunkType = 0x4E4F534A;

        public class Parameters
        {
            [ToolParameter("Exact avatar hierarchy path, or an avatar root name when it is unique. Leave empty only when exactly one VRChat avatar or valid Humanoid model is loaded.", Required = false)]
            public string avatarPath { get; set; } = "";

            [ToolParameter("Required VRM author/creator name written into the exported metadata.", Required = true)]
            public string author { get; set; } = "";

            [ToolParameter("VRM title. Defaults to the selected avatar root name.", Required = false)]
            public string title { get; set; } = "";

            [ToolParameter("Avatar content version written into VRM metadata. Defaults to 1.0.", Required = false)]
            public string version { get; set; } = "1.0";

            [ToolParameter("Must be true to confirm that the user has the rights needed to export and distribute this avatar content.", Required = true)]
            public bool? confirmRights { get; set; } = false;

            [ToolParameter("Output path under Assets/VRCForge/Exports. Must use the .vrm extension.", Required = false)]
            public string outputPath { get; set; } = DefaultOutputPath;

            [ToolParameter("Replace an existing managed .vrm output. Defaults to false.", Required = false)]
            public bool? overwrite { get; set; } = false;

            [ToolParameter("Refresh Unity's AssetDatabase after a validated export.", Required = false)]
            public bool? refreshAssets { get; set; } = true;
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters();
            try
            {
                var result = Export(parameters);
                return new SuccessResponse(
                    $"Exported and validated VRM 1.0 for '{result.avatarPath}'.",
                    result);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"VRM 1.0 export failed: {UnwrapMessage(ex)}");
            }
        }

        private static ExportResult Export(Parameters parameters)
        {
            if (parameters.confirmRights != true)
            {
                throw new InvalidOperationException(
                    "confirmRights=true is required. Export only content you are authorized to use and distribute.");
            }

            var author = RequireText(parameters.author, "author", 256);
            var capability = ResolveCapability();
            if (!capability.available)
            {
                throw new InvalidOperationException(capability.reason);
            }

            var outputPath = ResolveOutputPath(parameters.outputPath);
            if (File.Exists(outputPath) && parameters.overwrite != true)
            {
                throw new InvalidOperationException(
                    $"VRM output already exists. Set overwrite=true after approval to replace it: {ToAssetRelativePath(outputPath)}");
            }

            var avatar = ResolveAvatarRoot(parameters.avatarPath);
            ValidateAvatar(avatar);
            var title = OptionalText(parameters.title, avatar.name, "title", 256);
            var version = OptionalText(parameters.version, "1.0", "version", 64);
            var temporaryPath = outputPath + ".partial";
            var replacementBackupPath = outputPath + ".replace-backup";
            DeleteTemporaryFile(temporaryPath);
            RecoverInterruptedReplacement(outputPath, replacementBackupPath);
            Directory.CreateDirectory(Path.GetDirectoryName(outputPath)
                ?? throw new InvalidOperationException("VRM output directory could not be resolved."));

            try
            {
                var bytes = InvokeExporter(capability, avatar, title, version, author);
                File.WriteAllBytes(temporaryPath, bytes);
                ValidateVrm10Glb(temporaryPath);
                CommitValidatedOutput(temporaryPath, outputPath, replacementBackupPath);
            }
            finally
            {
                DeleteTemporaryFile(temporaryPath);
                if (File.Exists(outputPath))
                {
                    DeleteTemporaryFile(replacementBackupPath);
                }
            }

            if (parameters.refreshAssets != false)
            {
                AssetDatabase.ImportAsset(ToAssetRelativePath(outputPath), ImportAssetOptions.ForceUpdate);
                AssetDatabase.Refresh();
            }

            var info = new FileInfo(outputPath);
            return new ExportResult
            {
                schema = "vrcforge.vrm_export.v1",
                exportedAtUtc = DateTime.UtcNow.ToString("O"),
                avatarPath = GetTransformPath(avatar.transform),
                avatarName = avatar.name,
                outputPath = ToAssetRelativePath(outputPath),
                byteLength = info.Length,
                exporterAssembly = capability.exporterType.Assembly.GetName().Name,
                exporterAssemblyVersion = capability.exporterType.Assembly.GetName().Version?.ToString() ?? "",
                exporterMethod = $"{ExporterTypeName}.Export",
                vrmSpec = "1.0",
                metadataTitle = title,
                metadataVersion = version,
                authorCount = 1,
                licenseProfile = "only_author_personal_non_profit_credit_required_no_redistribution_no_modification",
                validation = "glb_v2_vrm1_extension_valid",
            };
        }

        private static byte[] InvokeExporter(
            VrmCapability capability,
            GameObject avatar,
            string title,
            string version,
            string author)
        {
            var settings = Activator.CreateInstance(capability.settingsType)
                ?? throw new InvalidOperationException("UniVRM export settings could not be created.");
            var meta = Activator.CreateInstance(capability.metaType)
                ?? throw new InvalidOperationException("UniVRM VRM 1.0 metadata could not be created.");
            SetRequiredPublicField(meta, "Name", title, typeof(string));
            SetRequiredPublicField(meta, "Version", version, typeof(string));
            var authorsField = capability.metaType.GetField("Authors", BindingFlags.Public | BindingFlags.Instance)
                ?? throw new InvalidOperationException("Installed UniVRM metadata API has no public Authors field.");
            var authors = authorsField.GetValue(meta) as IList;
            if (authors == null)
            {
                throw new InvalidOperationException("Installed UniVRM metadata API did not initialize Authors.");
            }
            authors.Clear();
            authors.Add(author);

            object exported;
            try
            {
                exported = capability.exportMethod.Invoke(
                    null,
                    new object[] { settings, avatar, null, null, meta });
            }
            catch (TargetInvocationException ex)
            {
                throw new InvalidOperationException(
                    $"UniVRM rejected the avatar: {UnwrapMessage(ex)}",
                    ex.InnerException ?? ex);
            }

            var bytes = exported as byte[];
            if (bytes == null || bytes.Length == 0)
            {
                throw new InvalidOperationException("UniVRM VRM 1.0 exporter returned no bytes.");
            }
            return bytes;
        }

        private static VrmCapability ResolveCapability()
        {
            var exporterType = FindType(ExporterTypeName);
            var settingsType = FindType(SettingsTypeName);
            var metaType = FindType(MetaTypeName);
            if (exporterType == null || settingsType == null || metaType == null)
            {
                return VrmCapability.Unavailable(
                    "UniVRM VRM 1.0 dependency unavailable. Install compatible com.vrmc.gltf and com.vrmc.vrm packages, then retry.");
            }

            var exportMethod = exporterType
                .GetMethods(BindingFlags.Public | BindingFlags.Static)
                .Where(method => string.Equals(method.Name, "Export", StringComparison.Ordinal))
                .Where(method => method.ReturnType == typeof(byte[]))
                .Where(method => IsSupportedExportSignature(method, settingsType, metaType))
                .OrderBy(method => method.ToString(), StringComparer.Ordinal)
                .FirstOrDefault();
            if (exportMethod == null)
            {
                return VrmCapability.Unavailable(
                    $"Installed UniVRM does not expose the supported VRM 1.0 API: byte[] {ExporterTypeName}.Export({SettingsTypeName}, GameObject, {MaterialExporterTypeName}, {TextureSerializerTypeName}, {MetaTypeName}).");
            }

            return new VrmCapability
            {
                available = true,
                exporterType = exporterType,
                settingsType = settingsType,
                metaType = metaType,
                exportMethod = exportMethod,
                reason = "",
            };
        }

        private static bool IsSupportedExportSignature(MethodInfo method, Type settingsType, Type metaType)
        {
            var parameters = method.GetParameters();
            return parameters.Length == 5
                && parameters[0].ParameterType == settingsType
                && parameters[1].ParameterType == typeof(GameObject)
                && string.Equals(parameters[2].ParameterType.FullName, MaterialExporterTypeName, StringComparison.Ordinal)
                && string.Equals(parameters[3].ParameterType.FullName, TextureSerializerTypeName, StringComparison.Ordinal)
                && parameters[4].ParameterType == metaType;
        }

        private static void ValidateAvatar(GameObject avatar)
        {
            if (!avatar.activeInHierarchy)
            {
                throw new InvalidOperationException("The selected avatar root must be active in the loaded scene.");
            }
            var animator = avatar.GetComponent<Animator>();
            if (animator == null || animator.avatar == null)
            {
                throw new InvalidOperationException("The selected avatar root must have an Animator with an assigned Avatar.");
            }
            if (!animator.avatar.isValid || !animator.avatar.isHuman)
            {
                throw new InvalidOperationException("The assigned Unity Avatar must be a valid Humanoid rig before VRM 1.0 export.");
            }
            var hasActiveMesh = avatar.GetComponentsInChildren<Renderer>(false)
                .Any(renderer => renderer != null
                    && renderer.enabled
                    && renderer.gameObject.activeInHierarchy
                    && (renderer is MeshRenderer || renderer is SkinnedMeshRenderer));
            if (!hasActiveMesh)
            {
                throw new InvalidOperationException("The selected avatar must contain at least one active enabled mesh renderer.");
            }
        }

        private static void ValidateVrm10Glb(string outputPath)
        {
            var bytes = File.ReadAllBytes(outputPath);
            if (bytes.Length < 20 || bytes[0] != (byte)'g' || bytes[1] != (byte)'l' || bytes[2] != (byte)'T' || bytes[3] != (byte)'F')
            {
                throw new InvalidOperationException("UniVRM output is not a valid GLB/VRM payload (missing glTF header).");
            }
            if (BitConverter.ToUInt32(bytes, 4) != 2)
            {
                throw new InvalidOperationException("UniVRM output is not GLB version 2.");
            }
            var declaredLength = BitConverter.ToUInt32(bytes, 8);
            if (declaredLength != (uint)bytes.Length)
            {
                throw new InvalidOperationException($"UniVRM output length validation failed (header={declaredLength}, file={bytes.Length}).");
            }

            var jsonLength = BitConverter.ToUInt32(bytes, 12);
            var jsonType = BitConverter.ToUInt32(bytes, 16);
            if (jsonType != GlbJsonChunkType || jsonLength == 0 || jsonLength > (uint)(bytes.Length - 20))
            {
                throw new InvalidOperationException("UniVRM output has no valid first JSON chunk.");
            }
            JObject document;
            try
            {
                var json = Encoding.UTF8.GetString(bytes, 20, checked((int)jsonLength))
                    .TrimEnd('\0', ' ', '\t', '\r', '\n');
                document = JObject.Parse(json);
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException($"UniVRM output JSON chunk is invalid: {ex.Message}");
            }
            if (document["extensions"]?["VRMC_vrm"] == null)
            {
                throw new InvalidOperationException("UniVRM output is generic GLB and does not contain the required VRMC_vrm extension.");
            }
        }

        private static void CommitValidatedOutput(string temporaryPath, string outputPath, string backupPath)
        {
            if (!File.Exists(outputPath))
            {
                File.Move(temporaryPath, outputPath);
                return;
            }

            DeleteTemporaryFile(backupPath);
            File.Move(outputPath, backupPath);
            try
            {
                File.Move(temporaryPath, outputPath);
            }
            catch
            {
                if (!File.Exists(outputPath) && File.Exists(backupPath))
                {
                    File.Move(backupPath, outputPath);
                }
                throw;
            }
            DeleteTemporaryFile(backupPath);
        }

        private static void RecoverInterruptedReplacement(string outputPath, string backupPath)
        {
            if (!File.Exists(backupPath))
            {
                return;
            }
            if (!File.Exists(outputPath))
            {
                File.Move(backupPath, outputPath);
                return;
            }
            DeleteTemporaryFile(backupPath);
        }

        private static void DeleteTemporaryFile(string path)
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }

        private static GameObject ResolveAvatarRoot(string requestedAvatarPath)
        {
            var sceneObjects = Resources.FindObjectsOfTypeAll<Transform>()
                .Where(transform => transform != null
                    && transform.gameObject.scene.IsValid()
                    && transform.gameObject.scene.isLoaded
                    && !EditorUtility.IsPersistent(transform))
                .ToList();
            var requested = NormalizePath(requestedAvatarPath);
            if (!string.IsNullOrEmpty(requested))
            {
                var matches = sceneObjects
                    .Where(transform => string.Equals(NormalizePath(GetTransformPath(transform)), requested, StringComparison.OrdinalIgnoreCase)
                        || string.Equals(transform.name, requested, StringComparison.OrdinalIgnoreCase))
                    .Select(transform => transform.gameObject)
                    .Distinct()
                    .ToList();
                if (matches.Count != 1)
                {
                    throw new InvalidOperationException(matches.Count == 0
                        ? $"Avatar '{requestedAvatarPath}' was not found in the loaded scene."
                        : $"Avatar '{requestedAvatarPath}' is ambiguous; provide its exact hierarchy path.");
                }
                return matches[0];
            }

            var descriptorType = FindType("VRC.SDK3.Avatars.Components.VRCAvatarDescriptor");
            if (descriptorType != null)
            {
                var avatars = Resources.FindObjectsOfTypeAll(descriptorType)
                    .OfType<Component>()
                    .Where(component => component.gameObject.scene.IsValid()
                        && component.gameObject.scene.isLoaded
                        && !EditorUtility.IsPersistent(component))
                    .Select(component => component.gameObject)
                    .Distinct()
                    .ToList();
                if (avatars.Count == 1)
                {
                    return avatars[0];
                }
                if (avatars.Count > 1)
                {
                    throw new InvalidOperationException(
                        "More than one VRChat avatar is loaded; provide avatarPath to choose one deterministically.");
                }
            }

            var humanoids = Resources.FindObjectsOfTypeAll<Animator>()
                .Where(animator => animator != null
                    && animator.gameObject.scene.IsValid()
                    && animator.gameObject.scene.isLoaded
                    && !EditorUtility.IsPersistent(animator)
                    && animator.avatar != null
                    && animator.avatar.isValid
                    && animator.avatar.isHuman)
                .Select(animator => animator.gameObject)
                .Distinct()
                .ToList();
            if (humanoids.Count != 1)
            {
                throw new InvalidOperationException(humanoids.Count == 0
                    ? "No loaded VRChat avatar or valid Humanoid Animator was found; provide avatarPath for a valid avatar root."
                    : "More than one valid Humanoid model is loaded; provide avatarPath to choose one deterministically.");
            }
            return humanoids[0];
        }

        private static string ResolveOutputPath(string requestedPath)
        {
            var path = VRCForgeOutputPathGuard.ResolveManagedProjectPath(
                requestedPath,
                DefaultOutputPath,
                "Assets/VRCForge/Exports",
                "VRM export");
            if (!string.Equals(Path.GetExtension(path), ".vrm", StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("VRM export path must use the .vrm extension.");
            }
            return path;
        }

        private static Type FindType(string fullName)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    var match = assembly.GetType(fullName, false);
                    if (match != null)
                    {
                        return match;
                    }
                }
                catch (ReflectionTypeLoadException)
                {
                    // A partially loaded third-party assembly is not a supported exporter.
                }
            }
            return null;
        }

        private static void SetRequiredPublicField(object target, string name, object value, Type expectedType)
        {
            var field = target.GetType().GetField(name, BindingFlags.Public | BindingFlags.Instance);
            if (field == null || field.FieldType != expectedType)
            {
                throw new InvalidOperationException($"Installed UniVRM metadata API has no supported public {name} field.");
            }
            field.SetValue(target, value);
        }

        private static string RequireText(string value, string field, int maxLength)
        {
            var normalized = (value ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(normalized))
            {
                throw new InvalidOperationException($"{field} is required.");
            }
            if (normalized.Length > maxLength)
            {
                throw new InvalidOperationException($"{field} must be at most {maxLength} characters.");
            }
            return normalized;
        }

        private static string OptionalText(string value, string fallback, string field, int maxLength)
        {
            return RequireText(string.IsNullOrWhiteSpace(value) ? fallback : value, field, maxLength);
        }

        private static string UnwrapMessage(Exception ex)
        {
            var current = ex;
            while (current is TargetInvocationException && current.InnerException != null)
            {
                current = current.InnerException;
            }
            return current.Message;
        }

        private static string GetTransformPath(Transform transform)
        {
            var segments = new Stack<string>();
            for (var current = transform; current != null; current = current.parent)
            {
                segments.Push(current.name);
            }
            return string.Join("/", segments);
        }

        private static string NormalizePath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim().Trim('/');
        }

        private static string ToAssetRelativePath(string absolutePath)
        {
            return VRCForgeOutputPathGuard.ToAssetRelativePath(absolutePath);
        }

        [Serializable]
        private class VrmCapability
        {
            public bool available;
            public Type exporterType;
            public Type settingsType;
            public Type metaType;
            public MethodInfo exportMethod;
            public string reason;

            public static VrmCapability Unavailable(string reason)
            {
                return new VrmCapability
                {
                    available = false,
                    reason = reason,
                };
            }
        }

        [Serializable]
        private class ExportResult
        {
            public string schema;
            public string exportedAtUtc;
            public string avatarPath;
            public string avatarName;
            public string outputPath;
            public long byteLength;
            public string exporterAssembly;
            public string exporterAssemblyVersion;
            public string exporterMethod;
            public string vrmSpec;
            public string metadataTitle;
            public string metadataVersion;
            public int authorCount;
            public string licenseProfile;
            public string validation;
        }
    }
}
