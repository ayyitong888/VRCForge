using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.PackageManager;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_import_unitypackage",
        Description = "Import a local .unitypackage through Unity AssetDatabase. Intended for VRCForge supervised outfit imports."
    )]
    public static class UnityPackageImporterTool
    {
        public class ImportUnityPackageParameters
        {
            [ToolParameter("Absolute path to the .unitypackage file.", Required = true)]
            public string unityPackagePath { get; set; } = "";

            [ToolParameter("Expected active Unity project root.", Required = false)]
            public string projectPath { get; set; } = "";

            [ToolParameter("When true, Unity may show the package import UI. VRCForge uses false.", Required = false)]
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

                AssetDatabase.ImportPackage(packagePath, parameters.interactive ?? false);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                return new SuccessResponse(
                    "Imported UnityPackage through Unity AssetDatabase.",
                    new
                    {
                        ok = true,
                        projectPath = CheckpointPrepareTool.ProjectRoot(),
                        unityPackagePath = packagePath.Replace("\\", "/"),
                        interactive = parameters.interactive ?? false
                    });
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"UnityPackage import failed: {ex.Message}");
            }
        }
    }

    [McpForUnityTool(
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
