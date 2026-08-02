using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.PackageManager;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeTool(
        name: "vrc_import_unitypackage",
        Description = "Import a local .unitypackage through Unity AssetDatabase. Intended for VRCForge supervised outfit imports."
    )]
    public static class UnityPackageImporterTool
    {
        public class ImportUnityPackageParameters
        {
            [VRCForgeParameter("Absolute path to the .unitypackage file.", Required = true)]
            public string unityPackagePath { get; set; } = "";

            [VRCForgeParameter("Expected active Unity project root.", Required = false)]
            public string projectPath { get; set; } = "";

            [VRCForgeParameter("Approval-bound SHA-256 of the UnityPackage bytes.", Required = true)]
            public string expectedSha256 { get; set; } = "";

            [VRCForgeParameter("Approval-bound byte length of the UnityPackage.", Required = true)]
            public long expectedSize { get; set; } = -1;

            [VRCForgeParameter("Exact Unity asset paths expected after this import and refresh.", Required = false)]
            public List<string> expectedAssetPaths { get; set; } = new List<string>();

            [VRCForgeParameter("When true, Unity may show the package import UI. VRCForge uses false.", Required = false)]
            public bool? interactive { get; set; } = false;
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<ImportUnityPackageParameters>()
                ?? new ImportUnityPackageParameters();
            try
            {
                CheckpointPrepareTool.ValidateProject(@params);
                CheckpointPrepareTool.EnsureEditorReady();

                var packagePath = Path.GetFullPath(parameters.unityPackagePath ?? "");
                if (!File.Exists(packagePath))
                {
                    throw new InvalidOperationException($"UnityPackage not found: {packagePath}");
                }
                if (!string.Equals(Path.GetExtension(packagePath), ".unitypackage", StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException("Only .unitypackage files can be imported by this tool.");
                }
                var expectedSha256 = (parameters.expectedSha256 ?? "").Trim().ToLowerInvariant();
                if (expectedSha256.Length != 64 || !IsLowerHex(expectedSha256))
                {
                    throw new InvalidOperationException("expectedSha256 must be exactly 64 hexadecimal characters.");
                }
                if (parameters.expectedSize < 0)
                {
                    throw new InvalidOperationException("expectedSize must be non-negative.");
                }

                // The package is held read-only for this single Core tool call.  FileShare.Read
                // denies writers/replacers while Unity imports the exact hashed bytes.  This
                // handle has no authentication role: managed-peer and one-use execution context
                // remain the authority boundary in VRCForgeMcpCoreServer.
                using (var packageHandle = new FileStream(packagePath, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    if (packageHandle.Length != parameters.expectedSize)
                    {
                        throw new InvalidOperationException("UnityPackage size changed after approval.");
                    }
                    string actualSha256;
                    using (var hasher = SHA256.Create())
                    {
                        actualSha256 = BitConverter.ToString(hasher.ComputeHash(packageHandle)).Replace("-", "").ToLowerInvariant();
                    }
                    if (!string.Equals(actualSha256, expectedSha256, StringComparison.Ordinal))
                    {
                        throw new InvalidOperationException("UnityPackage SHA-256 changed after approval.");
                    }
                    AssetDatabase.ImportPackage(packagePath, parameters.interactive ?? false);
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                    var expectedAssets = ReadExpectedAssets(parameters.expectedAssetPaths);
                    return new SuccessResponse(
                        "Imported UnityPackage through Unity AssetDatabase.",
                        new
                        {
                            ok = true,
                            projectPath = CheckpointPrepareTool.ProjectRoot(),
                            unityPackagePath = packagePath.Replace("\\", "/"),
                            expectedSha256 = expectedSha256,
                            expectedSize = parameters.expectedSize,
                            expectedAssets = expectedAssets,
                            interactive = parameters.interactive ?? false
                        });
                }
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"UnityPackage import failed: {ex.Message}");
            }
        }

        private static bool IsLowerHex(string value)
        {
            foreach (var character in value)
            {
                if ((character < '0' || character > '9') && (character < 'a' || character > 'f'))
                {
                    return false;
                }
            }
            return true;
        }

        private static List<object> ReadExpectedAssets(IEnumerable<string> expectedPaths)
        {
            var receipts = new List<object>();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var rawPath in expectedPaths ?? Array.Empty<string>())
            {
                var assetPath = (rawPath ?? string.Empty).Replace("\\", "/").Trim();
                if (assetPath.Length == 0 || !assetPath.StartsWith("Assets/", StringComparison.Ordinal)
                    || assetPath.Contains("../") || assetPath.Contains("//") || !seen.Add(assetPath))
                {
                    throw new InvalidOperationException("expectedAssetPaths contains an invalid or duplicate Assets path.");
                }
                var assetType = AssetDatabase.GetMainAssetTypeAtPath(assetPath);
                var guid = (AssetDatabase.AssetPathToGUID(assetPath) ?? string.Empty).Trim().ToLowerInvariant();
                if (assetType == null || guid.Length != 32 || !IsLowerHex(guid))
                {
                    throw new InvalidOperationException($"Expected imported asset was not found after refresh: {assetPath}");
                }
                receipts.Add(new { assetPath, guid, assetType = assetType.FullName ?? assetType.Name });
            }
            return receipts;
        }
    }

    [VRCForgeTool(
        name: "vrc_refresh_asset_database",
        Description = "Refresh Unity AssetDatabase after VRCForge copied supervised outfit assets."
    )]
    public static class AssetDatabaseRefreshTool
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                CheckpointPrepareTool.ValidateProject(@params);
                CheckpointPrepareTool.EnsureEditorReady();
                var resolvePackages = @params?["resolvePackages"]?.Value<bool?>() ?? false;
                var packageResolveTimeoutSeconds = Math.Max(
                    5,
                    Math.Min(@params?["packageResolveTimeoutSeconds"]?.Value<int?>() ?? 120, 300));
                object packageResolve = new { requested = false };
                AssetDatabase.SaveAssets();
                if (resolvePackages)
                {
                    var startedAt = DateTime.UtcNow;
                    Client.Resolve();
                    packageResolve = new
                    {
                        requested = true,
                        completed = false,
                        status = "started",
                        error = "",
                        startedAt = startedAt.ToString("O"),
                        timeoutSeconds = packageResolveTimeoutSeconds
                    };
                }
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                return new SuccessResponse(
                    "Refreshed Unity AssetDatabase.",
                    new { ok = true, projectPath = CheckpointPrepareTool.ProjectRoot(), packageResolve });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"AssetDatabase refresh failed: {ex.Message}");
            }
        }
    }
}
