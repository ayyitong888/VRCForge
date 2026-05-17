using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_restore_safe_backup",
        Description = "Preview or restore files from a VRCForge-created backup snapshot with project identity and overwrite checks."
    )]
    public static class PrefabTools
    {
        public const string RestoreSafeBackupToolName = "vrc_restore_safe_backup";
        public const string DefaultBackupRoot = "Library/VRCForge/Backups";

        public class RestoreSafeBackupParameters
        {
            [ToolParameter("Absolute or project-relative path to a VRCForge backup folder.", Required = false)]
            public string backupPath { get; set; } = "";

            [ToolParameter("Backup id under backupRoot. Used when backupPath is empty.", Required = false)]
            public string backupId { get; set; } = "";

            [ToolParameter("Project-relative or absolute folder containing backup snapshots.", Required = false)]
            public string backupRoot { get; set; } = DefaultBackupRoot;

            [ToolParameter("Optional subset of asset paths to restore from the manifest.", Required = false)]
            public List<string> assetPaths { get; set; } = new List<string>();

            [ToolParameter("Must be true to actually copy files. False returns a restore preview.", Required = false)]
            public bool? confirmRestore { get; set; } = false;

            [ToolParameter("Allow restore when the project identity does not match the backup manifest.", Required = false)]
            public bool? allowProjectMismatch { get; set; } = false;

            [ToolParameter("Allow overwriting files that changed since the backup was created.", Required = false)]
            public bool? allowOverwriteChanged { get; set; } = false;

            [ToolParameter("Refresh the Unity AssetDatabase after files are restored.", Required = false)]
            public bool? refreshAssets { get; set; } = true;
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<RestoreSafeBackupParameters>()
                ?? new RestoreSafeBackupParameters();

            try
            {
                var payload = PreviewOrRestore(parameters);
                var action = payload.confirmed ? "Restored" : "Previewed";
                return new SuccessResponse(
                    $"{action} safe backup '{payload.backup_id}': {payload.summary.restoredCount} restored, {payload.summary.skippedCount} skipped.",
                    payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Safe backup restore failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static RestorePayload PreviewOrRestore(RestoreSafeBackupParameters parameters)
        {
            var projectRoot = GetProjectRoot();
            var backupPath = ResolveBackupPath(parameters, projectRoot);
            var manifestPath = Path.Combine(backupPath, "backup.json").Replace("\\", "/");
            if (!File.Exists(manifestPath))
            {
                throw new InvalidOperationException($"VRCForge backup manifest was not found: {manifestPath}");
            }

            var manifest = JObject.Parse(File.ReadAllText(manifestPath, Encoding.UTF8));
            if (!string.Equals((string)manifest["type"], "vrcforge_safe_backup", StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("Backup manifest is not a VRCForge safe backup.");
            }

            var warnings = new List<string>();
            var skipped = new List<RestoreSkippedItem>();
            var planned = new List<RestorePlanItem>();
            var restored = new List<RestorePlanItem>();
            var backupProjectHash = (string)manifest["project_identity"]?["project_root_hash"] ?? "";
            var currentProjectHash = StableHash(projectRoot.Replace("\\", "/").ToLowerInvariant());
            var projectMatches = string.Equals(backupProjectHash, currentProjectHash, StringComparison.OrdinalIgnoreCase);

            if (!projectMatches)
            {
                var message = "Backup project identity does not match the currently open Unity project.";
                warnings.Add(message);
                if (!(parameters.allowProjectMismatch ?? false))
                {
                    throw new InvalidOperationException(message + " Re-run with allowProjectMismatch=true only after manual review.");
                }
            }

            var requestedSubset = new HashSet<string>(
                (parameters.assetPaths ?? new List<string>())
                    .Where(path => !string.IsNullOrWhiteSpace(path))
                    .Select(NormalizeAssetPath),
                StringComparer.OrdinalIgnoreCase);
            var files = manifest["files"] as JArray ?? new JArray();
            foreach (var file in files.OfType<JObject>())
            {
                var relativePath = NormalizeAssetPath((string)file["project_relative_path"] ?? "");
                var backupRelativePath = NormalizeAssetPath((string)file["backup_relative_path"] ?? "");
                var originalSha = ((string)file["sha256"] ?? "").Trim();
                if (string.IsNullOrWhiteSpace(relativePath) || string.IsNullOrWhiteSpace(backupRelativePath))
                {
                    skipped.Add(new RestoreSkippedItem
                    {
                        project_relative_path = relativePath,
                        reason = "Manifest entry is missing project_relative_path or backup_relative_path."
                    });
                    continue;
                }

                if (!ShouldRestore(relativePath, requestedSubset))
                {
                    continue;
                }

                if (!relativePath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase)
                    && !string.Equals(relativePath, "Assets", StringComparison.OrdinalIgnoreCase))
                {
                    skipped.Add(new RestoreSkippedItem
                    {
                        project_relative_path = relativePath,
                        reason = "Only Assets/ files can be restored by this tool."
                    });
                    continue;
                }

                var backupFilePath = Path.Combine(backupPath, backupRelativePath).Replace("\\", "/");
                var targetPath = Path.Combine(projectRoot, relativePath).Replace("\\", "/");
                EnsureInsideProject(projectRoot, targetPath);

                if (!File.Exists(backupFilePath))
                {
                    skipped.Add(new RestoreSkippedItem
                    {
                        project_relative_path = relativePath,
                        reason = "Backup file is missing from the snapshot."
                    });
                    continue;
                }

                var targetExists = File.Exists(targetPath);
                var currentSha = targetExists ? ComputeSha256(targetPath) : "";
                var changedSinceBackup = targetExists
                    && !string.IsNullOrWhiteSpace(originalSha)
                    && !string.Equals(currentSha, originalSha, StringComparison.OrdinalIgnoreCase);
                if (changedSinceBackup && !(parameters.allowOverwriteChanged ?? false))
                {
                    skipped.Add(new RestoreSkippedItem
                    {
                        project_relative_path = relativePath,
                        reason = "Current file differs from the backup source hash. Re-run with allowOverwriteChanged=true after review."
                    });
                    continue;
                }

                planned.Add(new RestorePlanItem
                {
                    project_relative_path = relativePath,
                    backup_relative_path = backupRelativePath,
                    target_exists = targetExists,
                    changed_since_backup = changedSinceBackup,
                    current_sha256 = currentSha,
                    backup_sha256 = ComputeSha256(backupFilePath)
                });
            }

            var confirmed = parameters.confirmRestore ?? false;
            if (confirmed)
            {
                foreach (var item in planned)
                {
                    var backupFilePath = Path.Combine(backupPath, item.backup_relative_path).Replace("\\", "/");
                    var targetPath = Path.Combine(projectRoot, item.project_relative_path).Replace("\\", "/");
                    var directory = Path.GetDirectoryName(targetPath);
                    if (!string.IsNullOrEmpty(directory))
                    {
                        Directory.CreateDirectory(directory);
                    }

                    File.Copy(backupFilePath, targetPath, true);
                    restored.Add(item);
                }

                if (parameters.refreshAssets ?? true)
                {
                    AssetDatabase.Refresh();
                }
            }
            else
            {
                warnings.Add("Restore preview only. Set confirmRestore=true to copy the planned files.");
            }

            return new RestorePayload
            {
                type = "vrcforge_safe_backup_restore",
                version = "0.1",
                backup_id = (string)manifest["backup_id"] ?? Path.GetFileName(backupPath),
                backup_path = backupPath,
                confirmed = confirmed,
                requires_confirmation = !confirmed,
                project_identity_matches = projectMatches,
                planned = planned,
                restored = restored,
                skipped = skipped,
                warnings = warnings,
                summary = new RestoreSummary
                {
                    plannedCount = planned.Count,
                    restoredCount = restored.Count,
                    skippedCount = skipped.Count,
                    warningCount = warnings.Count
                }
            };
        }

        private static bool ShouldRestore(string relativePath, HashSet<string> requestedSubset)
        {
            if (requestedSubset.Count == 0)
            {
                return true;
            }

            return requestedSubset.Any(requested =>
                string.Equals(relativePath, requested, StringComparison.OrdinalIgnoreCase)
                || string.Equals(relativePath, requested + ".meta", StringComparison.OrdinalIgnoreCase)
                || relativePath.StartsWith(requested.TrimEnd('/') + "/", StringComparison.OrdinalIgnoreCase));
        }

        private static string ResolveBackupPath(RestoreSafeBackupParameters parameters, string projectRoot)
        {
            if (!string.IsNullOrWhiteSpace(parameters.backupPath))
            {
                return ResolveProjectPath(parameters.backupPath, projectRoot);
            }

            if (string.IsNullOrWhiteSpace(parameters.backupId))
            {
                throw new InvalidOperationException("backupPath or backupId is required.");
            }

            return Path.Combine(ResolveProjectPath(parameters.backupRoot, projectRoot), parameters.backupId).Replace("\\", "/");
        }

        private static void EnsureInsideProject(string projectRoot, string targetPath)
        {
            var root = projectRoot.Replace("\\", "/").TrimEnd('/') + "/";
            var target = targetPath.Replace("\\", "/");
            if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException($"Restore target is outside the Unity project: {targetPath}");
            }
        }

        private static string NormalizeAssetPath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim().Trim('/');
        }

        private static string GetProjectRoot()
        {
            var projectRoot = Directory.GetParent(Application.dataPath);
            if (projectRoot == null)
            {
                throw new InvalidOperationException("Cannot determine Unity project root.");
            }

            return projectRoot.FullName.Replace("\\", "/");
        }

        private static string ResolveProjectPath(string requestedPath, string projectRoot)
        {
            var path = string.IsNullOrWhiteSpace(requestedPath) ? DefaultBackupRoot : requestedPath;
            if (Path.IsPathRooted(path))
            {
                return path.Replace("\\", "/");
            }

            return Path.Combine(projectRoot, path).Replace("\\", "/");
        }

        private static string ComputeSha256(string fullPath)
        {
            using (var sha256 = SHA256.Create())
            using (var stream = File.OpenRead(fullPath))
            {
                var bytes = sha256.ComputeHash(stream);
                return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();
            }
        }

        private static string StableHash(string value)
        {
            using (var sha256 = SHA256.Create())
            {
                var bytes = sha256.ComputeHash(Encoding.UTF8.GetBytes(value ?? ""));
                return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();
            }
        }

        [Serializable]
        private class RestorePayload
        {
            public string type;
            public string version;
            public string backup_id;
            public string backup_path;
            public bool confirmed;
            public bool requires_confirmation;
            public bool project_identity_matches;
            public List<RestorePlanItem> planned;
            public List<RestorePlanItem> restored;
            public List<RestoreSkippedItem> skipped;
            public List<string> warnings;
            public RestoreSummary summary;
        }

        [Serializable]
        private class RestorePlanItem
        {
            public string project_relative_path;
            public string backup_relative_path;
            public bool target_exists;
            public bool changed_since_backup;
            public string current_sha256;
            public string backup_sha256;
        }

        [Serializable]
        private class RestoreSkippedItem
        {
            public string project_relative_path;
            public string reason;
        }

        [Serializable]
        private class RestoreSummary
        {
            public int plannedCount;
            public int restoredCount;
            public int skippedCount;
            public int warningCount;
        }
    }
}
