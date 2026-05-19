using System;
using System.IO;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    public static class UnityPluginUninstaller
    {
        private const string MenuPath = "VRCForge/Uninstall VRCForge Unity Plugin";
        private const string PackageName = "com.coplaydev.unity-mcp";

        [MenuItem(MenuPath)]
        public static void ConfirmUninstall()
        {
            var confirmed = EditorUtility.DisplayDialog(
                "Uninstall VRCForge Unity Plugin",
                "This will back up Assets/VRCForge and Packages/com.coplaydev.unity-mcp to the project .vrcforge/backups folder, remove the local MCP manifest dependency, then refresh Unity.\n\nUse this before testing a clean install.",
                "Backup and Uninstall",
                "Cancel"
            );
            if (!confirmed)
            {
                return;
            }

            EditorApplication.delayCall += UninstallWithBackup;
        }

        private static void UninstallWithBackup()
        {
            try
            {
                var summary = Uninstall();
                EditorUtility.DisplayDialog(
                    "VRCForge Unity Plugin Uninstalled",
                    $"VRCForge Unity-side files were moved out of Assets and Packages.\n\nBackups:\n{summary}\n\nLet Unity finish refreshing before running a clean install.",
                    "OK"
                );
            }
            catch (Exception ex)
            {
                Debug.LogError($"[VRCForge] Unity plugin uninstall failed: {ex}");
                EditorUtility.DisplayDialog(
                    "VRCForge Uninstall Failed",
                    $"The uninstall stopped before completing.\n\n{ex.Message}",
                    "OK"
                );
            }
            finally
            {
                AssetDatabase.Refresh();
            }
        }

        private static string Uninstall()
        {
            var projectRoot = Directory.GetParent(Application.dataPath)?.FullName;
            if (string.IsNullOrWhiteSpace(projectRoot))
            {
                throw new InvalidOperationException("Could not resolve the Unity project root.");
            }

            var stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
            var backupsRoot = Path.Combine(projectRoot, ".vrcforge", "backups");
            Directory.CreateDirectory(backupsRoot);

            var manifestPath = Path.Combine(projectRoot, "Packages", "manifest.json");
            var manifestBackup = BackupManifest(manifestPath, backupsRoot, stamp);
            RemoveMcpPackageFromManifest(manifestPath);

            var packagePath = Path.Combine(projectRoot, "Packages", PackageName);
            var packageBackup = MoveDirectory(packagePath, Path.Combine(backupsRoot, $"{PackageName}_uninstall_{stamp}"));

            var assetPath = Path.Combine(projectRoot, "Assets", "VRCForge");
            var assetBackup = MoveDirectoryWithMeta(assetPath, Path.Combine(backupsRoot, $"VRCForge_uninstall_{stamp}"));

            var summary = $"manifest: {manifestBackup}";
            if (!string.IsNullOrWhiteSpace(packageBackup))
            {
                summary += $"\npackage: {packageBackup}";
            }
            if (!string.IsNullOrWhiteSpace(assetBackup))
            {
                summary += $"\nassets: {assetBackup}";
            }
            return summary;
        }

        private static string BackupManifest(string manifestPath, string backupsRoot, string stamp)
        {
            if (!File.Exists(manifestPath))
            {
                throw new FileNotFoundException("Packages/manifest.json was not found.", manifestPath);
            }

            var backupPath = Path.Combine(backupsRoot, $"manifest_uninstall_{stamp}.json");
            File.Copy(manifestPath, backupPath, overwrite: false);
            return backupPath;
        }

        private static void RemoveMcpPackageFromManifest(string manifestPath)
        {
            var manifest = JObject.Parse(File.ReadAllText(manifestPath, Encoding.UTF8));
            if (manifest["dependencies"] is JObject dependencies && dependencies.Remove(PackageName))
            {
                File.WriteAllText(manifestPath, JsonConvert.SerializeObject(manifest, Formatting.Indented), new UTF8Encoding(false));
            }
        }

        private static string MoveDirectoryWithMeta(string sourcePath, string destinationPath)
        {
            var movedPath = MoveDirectory(sourcePath, destinationPath);
            var metaPath = sourcePath + ".meta";
            if (File.Exists(metaPath))
            {
                var metaDestination = destinationPath + ".meta";
                if (File.Exists(metaDestination))
                {
                    throw new IOException($"Backup meta already exists: {metaDestination}");
                }
                File.Move(metaPath, metaDestination);
            }
            return movedPath;
        }

        private static string MoveDirectory(string sourcePath, string destinationPath)
        {
            if (!Directory.Exists(sourcePath))
            {
                return string.Empty;
            }
            if (Directory.Exists(destinationPath))
            {
                throw new IOException($"Backup destination already exists: {destinationPath}");
            }

            Directory.Move(sourcePath, destinationPath);
            return destinationPath;
        }
    }
}
