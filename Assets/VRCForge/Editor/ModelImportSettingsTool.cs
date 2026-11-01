#if !VRC_CLIENT
using System;
using System.IO;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_set_model_import_settings",
        Summary = "When to use: preview or apply the isReadable setting on one exact model asset imported by Unity ModelImporter; apply requires the approved preview and reimports that asset. When NOT to use: do not pass a mesh subasset, TextureImporter asset, folder, scene object, or multiple models; do not use it for Streaming Mip Maps. Negative example: do not change every model importer to the same value without reviewing each asset.",
        Access = VRCForgeCommandAccess.RequiresApproval
    )]
    public static class ModelImportSettingsTool
    {
        private const string ResultSchema = "vrcforge.model_import_settings.v1";

        public sealed class Parameters
        {
            [VRCForgeInput("Exact persistent Unity model asset path under Assets/.", IsRequired = true)]
            public string modelAssetPath { get; set; } = string.Empty;

            [VRCForgeInput("Whether imported model meshes should be readable by scripts.", IsRequired = true)]
            public bool? isReadable { get; set; }

            [VRCForgeInput("Return a read-only preview without changing importer settings.", IsRequired = false)]
            public bool? preview { get; set; } = false;

            [VRCForgeInput("Save and reimport during the approved apply.", IsRequired = false)]
            public bool? saveAndReimport { get; set; } = false;

            [VRCForgeInput("Exact active Unity project root from the approved preview.", IsRequired = false)]
            public string expectedProjectPath { get; set; } = string.Empty;

        }

        public static object HandleCommand(JObject @params)
        {
            var mutationStarted = false;
            var assetPath = string.Empty;
            try
            {
                var parameters = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters();
                assetPath = NormalizeAssetPath(parameters.modelAssetPath);
                if (!parameters.isReadable.HasValue)
                {
                    throw new InvalidOperationException("isReadable must be provided as a boolean.");
                }
                var preview = parameters.preview ?? false;
                var expectedProjectPath = NormalizeProjectPath(parameters.expectedProjectPath);
                var currentProjectPath = CurrentProjectPath();
                if (!string.Equals(expectedProjectPath, currentProjectPath, StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException("The active Unity project does not match the model import preview.");
                }

                var importer = AssetImporter.GetAtPath(assetPath) as ModelImporter;
                if (importer == null || !string.Equals(importer.assetPath.Replace('\\', '/'), assetPath, StringComparison.Ordinal))
                {
                    throw new InvalidOperationException("The exact asset path is not imported by Unity ModelImporter.");
                }
                var importerType = importer.GetType().FullName ?? importer.GetType().Name;
                var beforeReadable = importer.isReadable;
                var targetReadable = parameters.isReadable.Value;
                var wouldChange = beforeReadable != targetReadable;

                if (!preview)
                {
                    if (!(parameters.saveAndReimport ?? false))
                    {
                        throw new InvalidOperationException("saveAndReimport must be true for approved apply.");
                    }
                    if (wouldChange)
                    {
                        mutationStarted = true;
                        importer.isReadable = targetReadable;
                        importer.SaveAndReimport();
                    }

                    var readbackImporter = wouldChange
                        ? AssetImporter.GetAtPath(assetPath) as ModelImporter
                        : importer;
                    var verified = readbackImporter != null && readbackImporter.isReadable == targetReadable;
                    if (!verified)
                    {
                        throw new InvalidOperationException("Unity did not read back the requested isReadable value.");
                    }

                    return VRCForgeToolResult.Completed(
                        "Model asset '" + assetPath + "' isReadable setting was read back successfully.",
                        new
                        {
                            schema = ResultSchema,
                            ok = true,
                            preview = false,
                            verified = true,
                            changed = wouldChange,
                            saved = wouldChange,
                            reimported = wouldChange,
                            mutationStarted = wouldChange,
                            committed = true,
                            commitState = wouldChange ? "complete" : "no_change",
                            persistenceState = wouldChange ? "persisted" : "no_change",
                            persistedReadback = wouldChange,
                            readbackState = "verified",
                            projectPath = currentProjectPath,
                            modelAssetPath = assetPath,
                            importerType,
                            beforeSettings = new { isReadable = beforeReadable },
                            targetSettings = new { isReadable = targetReadable },
                            readbackSettings = new { isReadable = readbackImporter.isReadable },
                            wouldChange
                        });
                }

                return VRCForgeToolResult.Completed(
                    "Model importer isReadable preview completed without mutation.",
                    new
                    {
                        schema = ResultSchema,
                        ok = true,
                        preview = true,
                        verified = true,
                        changed = false,
                        saved = false,
                        reimported = false,
                        projectPath = currentProjectPath,
                        modelAssetPath = assetPath,
                        importerType,
                        beforeSettings = new { isReadable = beforeReadable },
                        targetSettings = new { isReadable = targetReadable },
                        readbackSettings = new { isReadable = beforeReadable },
                        wouldChange
                    });
            }
            catch (Exception exception) when (mutationStarted)
            {
                return VRCForgeToolResult.Failed(
                    "Model asset '" + assetPath + "' isReadable apply failed after mutation started: "
                    + (exception.Message ?? exception.GetType().Name)
                    + " Inspect the model importer and restore the approved checkpoint if project files need recovery.",
                    new
                    {
                        mutationStarted = true,
                        committed = false,
                        commitState = "unknown",
                        requestMayHaveCommitted = true,
                        checkpointRestoreRequired = true,
                        exceptionType = exception.GetType().Name,
                        error = exception.Message ?? string.Empty
                    });
            }
            catch (Exception exception)
            {
                return VRCForgeToolResult.RejectedBeforeMutation(
                    "model_import_settings_rejected",
                    (string.IsNullOrEmpty(assetPath) ? "Model import settings request" : "Model asset '" + assetPath + "'")
                    + " was rejected: " + (exception.Message ?? "invalid request")
                    + " Check that the exact Assets path resolves to a Unity ModelImporter.",
                    "unity_core_tool",
                    "pre_mutation_validation",
                    false,
                    new { exceptionType = exception.GetType().Name },
                    exception: exception);
            }
        }

        private static string NormalizeAssetPath(string value)
        {
            var path = (value ?? string.Empty).Trim();
            if (!path.StartsWith("Assets/", StringComparison.Ordinal)
                || path.IndexOf('\\') >= 0
                || path.EndsWith("/", StringComparison.Ordinal))
            {
                throw new InvalidOperationException("modelAssetPath must be one exact Assets/... asset path using forward slashes.");
            }
            foreach (var segment in path.Split('/'))
            {
                if (string.IsNullOrWhiteSpace(segment) || segment == "." || segment == "..")
                {
                    throw new InvalidOperationException("modelAssetPath cannot contain empty or traversal path segments.");
                }
            }
            return path;
        }

        private static string NormalizeProjectPath(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                throw new InvalidOperationException("expectedProjectPath is required.");
            }
            return Path.GetFullPath(value.Trim()).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        }

        private static string CurrentProjectPath()
        {
            return Path.GetFullPath(Path.Combine(Application.dataPath, ".."))
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        }
    }
}
#endif
