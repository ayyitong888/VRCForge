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
                    .Select(NormalizeRequestedAssetPath)
                    .Where(path => !string.IsNullOrWhiteSpace(path)),
                StringComparer.OrdinalIgnoreCase);
            var files = manifest["files"] as JArray ?? new JArray();
            foreach (var file in files.OfType<JObject>())
            {
                var rawRelativePath = (string)file["project_relative_path"] ?? "";
                var rawBackupRelativePath = (string)file["backup_relative_path"] ?? "";
                var originalSha = ((string)file["sha256"] ?? "").Trim();
                var relativePathOk = TryNormalizeManifestRelativePath(rawRelativePath, out var relativePath, out var relativePathError);
                var backupRelativePathOk = TryNormalizeManifestRelativePath(rawBackupRelativePath, out var backupRelativePath, out var backupRelativePathError);
                if (!relativePathOk || !backupRelativePathOk)
                {
                    skipped.Add(new RestoreSkippedItem
                    {
                        project_relative_path = NormalizeAssetPath(rawRelativePath),
                        reason = !string.IsNullOrWhiteSpace(relativePathError)
                            ? relativePathError
                            : !string.IsNullOrWhiteSpace(backupRelativePathError)
                                ? backupRelativePathError
                                : "Manifest entry is missing project_relative_path or backup_relative_path."
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

                var backupFilePath = ResolveContainedPath(backupPath, backupRelativePath, "Backup file");
                var targetPath = ResolveContainedPath(projectRoot, relativePath, "Restore target");
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
                    var backupFilePath = ResolveContainedPath(backupPath, item.backup_relative_path, "Backup file");
                    var targetPath = ResolveContainedPath(projectRoot, item.project_relative_path, "Restore target");
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
                return NormalizeFullPath(ResolveProjectPath(parameters.backupPath, projectRoot));
            }

            if (string.IsNullOrWhiteSpace(parameters.backupId))
            {
                throw new InvalidOperationException("backupPath or backupId is required.");
            }

            if (!TryNormalizeManifestRelativePath(parameters.backupId, out var backupId, out var backupIdError))
            {
                throw new InvalidOperationException(backupIdError ?? "backupId must be a safe relative folder name.");
            }

            return ResolveContainedPath(ResolveProjectPath(parameters.backupRoot, projectRoot), backupId, "Backup id");
        }

        private static void EnsureInsideProject(string projectRoot, string targetPath)
        {
            var root = NormalizeFullPath(projectRoot).TrimEnd('/') + "/";
            var target = NormalizeFullPath(targetPath);
            if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException($"Restore target is outside the Unity project: {targetPath}");
            }
        }

        private static string NormalizeAssetPath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim().Trim('/');
        }

        private static string NormalizeRequestedAssetPath(string value)
        {
            return TryNormalizeManifestRelativePath(value, out var normalized, out _) ? normalized : "";
        }

        private static bool TryNormalizeManifestRelativePath(string value, out string normalized, out string error)
        {
            normalized = "";
            error = "";
            var raw = (value ?? string.Empty).Replace("\\", "/").Trim();
            if (string.IsNullOrWhiteSpace(raw))
            {
                error = "Manifest entry is missing project_relative_path or backup_relative_path.";
                return false;
            }

            if (Path.IsPathRooted(raw) || raw.StartsWith("/", StringComparison.Ordinal))
            {
                error = $"Manifest path must be relative: {raw}";
                return false;
            }

            var segments = raw.Trim('/').Split('/');
            var safeSegments = new List<string>();
            foreach (var segment in segments)
            {
                if (string.IsNullOrWhiteSpace(segment) || segment == "." || segment == "..")
                {
                    error = $"Manifest path is not a safe relative path: {raw}";
                    return false;
                }

                safeSegments.Add(segment);
            }

            normalized = string.Join("/", safeSegments);
            return !string.IsNullOrWhiteSpace(normalized);
        }

        private static string ResolveContainedPath(string rootPath, string relativePath, string label)
        {
            var root = NormalizeFullPath(rootPath).TrimEnd('/') + "/";
            var target = NormalizeFullPath(Path.Combine(root, relativePath));
            if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException($"{label} resolved outside its expected root: {relativePath}");
            }

            return target;
        }

        private static string NormalizeFullPath(string path)
        {
            return Path.GetFullPath(path).Replace("\\", "/");
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
            return VRCForgeOutputPathGuard.ResolveManagedProjectPath(
                requestedPath,
                DefaultBackupRoot,
                DefaultBackupRoot,
                "Safe backup root");
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
