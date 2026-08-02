using System;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    public static class UnityPluginUninstaller
    {
        private const string MenuPath = "VRCForge/Uninstall VRCForge Unity Plugin";

        [MenuItem(MenuPath)]
        public static void ConfirmUninstall()
        {
            var confirmed = EditorUtility.DisplayDialog(
                "Uninstall VRCForge Unity Plugin",
                "This will back up Assets/VRCForge to the project .vrcforge/backups folder, then refresh Unity.\n\nUse this before testing a clean install.",
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
                    $"VRCForge Unity-side files were moved out of Assets.\n\nBackups:\n{summary}\n\nLet Unity finish refreshing before running a clean install.",
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

            var assetPath = Path.Combine(projectRoot, "Assets", "VRCForge");
            var assetBackup = MoveDirectoryWithMeta(assetPath, Path.Combine(backupsRoot, $"VRCForge_uninstall_{stamp}"));

            var summary = string.Empty;
            if (!string.IsNullOrWhiteSpace(assetBackup))
            {
                summary += $"\nassets: {assetBackup}";
            }
            return summary;
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
