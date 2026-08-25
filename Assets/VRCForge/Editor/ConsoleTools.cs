using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using VRCForge.Core.MCP;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_create_safe_backup",
        Summary = "Create a local VRCForge backup snapshot for selected Unity assets before asset-writing actions."
    )]
    public static class ConsoleTools
    {
        public const string CreateSafeBackupToolName = "vrc_create_safe_backup";
        public const string DefaultBackupRoot = "Library/VRCForge/Backups";

        public class CreateSafeBackupParameters
        {
            [VRCForgeInput("Optional avatar root hierarchy path used to include the avatar prefab source when available.", IsRequired = false)]
            public string avatarPath { get; set; } = "";

            [VRCForgeInput("Asset paths to include. If empty, selected project assets plus open scenes are used.", IsRequired = false)]
            public List<string> assetPaths { get; set; } = new List<string>();

            [VRCForgeInput("Include loaded scene asset files in the snapshot.", IsRequired = false)]
            public bool? includeOpenScenes { get; set; } = true;

            [VRCForgeInput("Project-relative or absolute folder for backup snapshots.", IsRequired = false)]
            public string backupRoot { get; set; } = DefaultBackupRoot;

            [VRCForgeInput("Refresh the Unity AssetDatabase if the backup root is inside Assets.", IsRequired = false)]
            public bool? refreshAssets { get; set; } = false;
        }

        [MenuItem("VRCForge/Create Safe Backup From Selection")]
        public static void CreateSafeBackupFromMenu()
        {
            var payload = CreateBackup(new CreateSafeBackupParameters());
            Debug.Log($"[{CreateSafeBackupToolName}] Backup complete: {payload.backup_path}");
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<CreateSafeBackupParameters>()
                ?? new CreateSafeBackupParameters();

            try
            {
                var payload = CreateBackup(parameters);
                return VRCForgeToolResult.Completed(
                    $"Created safe backup with {payload.summary.fileCount} file(s): {payload.backup_id}",
                    payload);
            }
            catch (SafeBackupTransactionException ex)
            {
                return VRCForgeToolResult.Failed($"Safe backup creation failed: {ex.Message}", ex.Payload);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Safe backup creation failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static SafeBackupPayload CreateBackup(CreateSafeBackupParameters parameters)
        {
            var projectRoot = GetProjectRoot();
            var backupRoot = ResolveProjectPath(parameters.backupRoot, projectRoot);
            var backupId = $"vrcforge_backup_{DateTime.UtcNow:yyyyMMdd_HHmmss}";
            var backupPath = Path.Combine(backupRoot, backupId).Replace("\\", "/");
            var filesRoot = Path.Combine(backupPath, "files").Replace("\\", "/");
            var manifestPath = Path.Combine(backupPath, "backup.json").Replace("\\", "/");
            var warnings = new List<string>();
            var requestedAssets = ResolveRequestedAssetPaths(parameters, warnings);
            var fileMap = new Dictionary<string, BackupFileItem>(StringComparer.OrdinalIgnoreCase);

            foreach (var assetPath in requestedAssets)
            {
                AddAssetPathToBackupMap(projectRoot, assetPath, fileMap, warnings);
            }

            if (fileMap.Count == 0)
            {
                throw new InvalidOperationException("No valid Assets/ files were found to back up.");
            }

            Directory.CreateDirectory(filesRoot);
            foreach (var item in fileMap.Values)
            {
                var sourceFullPath = Path.Combine(projectRoot, item.project_relative_path).Replace("\\", "/");
                var backupFullPath = Path.Combine(backupPath, item.backup_relative_path).Replace("\\", "/");
                var parent = Path.GetDirectoryName(backupFullPath);
                if (!string.IsNullOrEmpty(parent))
                {
                    Directory.CreateDirectory(parent);
                }

                item.before_exists = File.Exists(backupFullPath);
                item.before_sha256 = item.before_exists ? ComputeSha256(backupFullPath) : "";
                try
                {
                    File.Copy(sourceFullPath, backupFullPath, true);
                    item.sha256 = ComputeSha256(sourceFullPath);
                    item.byte_count = new FileInfo(sourceFullPath).Length;
                    item.after_exists = File.Exists(backupFullPath);
                    item.after_sha256 = ComputeSha256(backupFullPath);
                    item.status = "succeeded";
                }
                catch (Exception ex)
                {
                    item.after_exists = File.Exists(backupFullPath);
                    item.after_sha256 = item.after_exists ? ComputeSha256(backupFullPath) : "";
                    item.status = "failed";
                    item.error = ex.Message;
                    throw new SafeBackupTransactionException(
                        $"Failed while copying '{item.project_relative_path}': {ex.Message}",
                        new { transaction = BuildTransaction(fileMap.Values, manifestPath, false, false, "", false, "", "") });
                }
            }

            var payload = new SafeBackupPayload
            {
                type = "vrcforge_safe_backup",
                version = "0.1",
                backup_id = backupId,
                created_at = DateTime.UtcNow.ToString("O"),
                unity_project = Directory.GetParent(Application.dataPath)?.Name ?? "UnknownProject",
                project_identity = BuildProjectIdentity(projectRoot),
                backup_path = backupPath,
                backup_root = backupRoot,
                requested_asset_paths = requestedAssets.OrderBy(path => path, StringComparer.OrdinalIgnoreCase).ToList(),
                files = fileMap.Values.OrderBy(item => item.project_relative_path, StringComparer.OrdinalIgnoreCase).ToList(),
                warnings = warnings,
                restore_hints = new List<string>
                {
                    "Run vrc_restore_safe_backup once without confirmRestore to preview planned overwrites.",
                    "Restore only copies files listed in this manifest; it does not delete assets created after the backup.",
                    "This local snapshot is a safety net for Unity asset writes and is not a replacement for version control."
                },
                summary = new SafeBackupSummary
                {
                    assetPathCount = requestedAssets.Count,
                    fileCount = fileMap.Count,
                    warningCount = warnings.Count,
                    totalBytes = fileMap.Values.Sum(item => item.byte_count)
                }
            };

            var manifestBeforeExists = File.Exists(manifestPath);
            var manifestBeforeSha = manifestBeforeExists ? ComputeSha256(manifestPath) : "";
            File.WriteAllText(
                manifestPath,
                JsonConvert.SerializeObject(payload, Formatting.Indented),
                Encoding.UTF8);
            var manifestAfterExists = File.Exists(manifestPath);
            var manifestAfterSha = ComputeSha256(manifestPath);
            payload.transaction = BuildTransaction(
                fileMap.Values,
                manifestPath,
                true,
                manifestBeforeExists,
                manifestBeforeSha,
                manifestAfterExists,
                manifestAfterSha,
                "succeeded");

            if ((parameters.refreshAssets ?? false) && IsInside(Application.dataPath, backupRoot))
            {
                AssetDatabase.Refresh();
            }

            return payload;
        }

        private static object BuildTransaction(
            IEnumerable<BackupFileItem> files,
            string manifestPath,
            bool includeManifest,
            bool manifestBeforeExists,
            string manifestBeforeSha,
            bool manifestAfterExists,
            string manifestAfterSha,
            string manifestStatus)
        {
            var transactionItems = files
                .Where(item => item.status != "not_attempted")
                .Select(item => (object)new
                {
                    asset = item.backup_relative_path,
                    before = new { exists = item.before_exists, sha256 = item.before_sha256 },
                    after = new { exists = item.after_exists, sha256 = item.after_sha256 },
                    item.status,
                    item.error,
                    rolled_back = false
                })
                .ToList();
            if (includeManifest)
            {
                transactionItems.Add(new
                {
                    asset = "backup.json",
                    before = new { exists = manifestBeforeExists, sha256 = manifestBeforeSha },
                    after = new { exists = manifestAfterExists, sha256 = manifestAfterSha },
                    status = manifestStatus,
                    error = "",
                    rolled_back = false
                });
            }
            return new
            {
                assets_touched = transactionItems.Count,
                items = transactionItems.Take(20).ToArray(),
                handle = manifestPath
            };
        }

        private static List<string> ResolveRequestedAssetPaths(CreateSafeBackupParameters parameters, List<string> warnings)
        {
            var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            foreach (var path in parameters.assetPaths ?? new List<string>())
            {
                AddAssetPath(paths, path);
            }

            foreach (var selected in Selection.objects ?? Array.Empty<UnityEngine.Object>())
            {
                AddAssetPath(paths, AssetDatabase.GetAssetPath(selected));
            }

            if (!string.IsNullOrWhiteSpace(parameters.avatarPath))
            {
                var avatar = ResolveAvatarRoot(parameters.avatarPath);
                var prefabSource = PrefabUtility.GetCorrespondingObjectFromSource(avatar.gameObject);
                var prefabPath = prefabSource != null ? AssetDatabase.GetAssetPath(prefabSource) : "";
                if (!string.IsNullOrWhiteSpace(prefabPath))
                {
                    AddAssetPath(paths, prefabPath);
                }
                else
                {
                    warnings.Add($"Avatar '{parameters.avatarPath}' does not appear to be linked to a prefab asset.");
                }
            }

            if (parameters.includeOpenScenes ?? true)
            {
                for (var index = 0; index < SceneManager.sceneCount; index++)
                {
                    var scene = SceneManager.GetSceneAt(index);
                    if (!scene.IsValid() || string.IsNullOrWhiteSpace(scene.path))
                    {
                        continue;
                    }

                    AddAssetPath(paths, scene.path);
                    if (scene.isDirty)
                    {
                        warnings.Add($"Scene '{scene.path}' has unsaved changes; the backup captures the file currently on disk.");
                    }
                }
            }

            return paths.OrderBy(path => path, StringComparer.OrdinalIgnoreCase).ToList();
        }

        private static void AddAssetPath(HashSet<string> paths, string rawPath)
        {
            var path = NormalizeAssetPath(rawPath);
            if (!string.IsNullOrWhiteSpace(path))
            {
                paths.Add(path);
            }
        }

        private static void AddAssetPathToBackupMap(
            string projectRoot,
            string assetPath,
            Dictionary<string, BackupFileItem> fileMap,
            List<string> warnings)
        {
            var normalizedAssetPath = NormalizeAssetPath(assetPath);
            if (!normalizedAssetPath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase)
                && !string.Equals(normalizedAssetPath, "Assets", StringComparison.OrdinalIgnoreCase))
            {
                warnings.Add($"Skipped non-Assets path: {assetPath}");
                return;
            }

            var fullPath = Path.Combine(projectRoot, normalizedAssetPath).Replace("\\", "/");
            if (File.Exists(fullPath))
            {
                AddFile(projectRoot, normalizedAssetPath, fileMap);
                AddMetaFileIfPresent(projectRoot, normalizedAssetPath, fileMap);
                return;
            }

            if (Directory.Exists(fullPath))
            {
                AddMetaFileIfPresent(projectRoot, normalizedAssetPath, fileMap);
                foreach (var filePath in Directory.GetFiles(fullPath, "*", SearchOption.AllDirectories))
                {
                    var relativePath = ToProjectRelativePath(projectRoot, filePath);
                    AddFile(projectRoot, relativePath, fileMap);
                }
                return;
            }

            warnings.Add($"Skipped missing asset path: {assetPath}");
        }

        private static void AddFile(string projectRoot, string relativePath, Dictionary<string, BackupFileItem> fileMap)
        {
            var fullPath = Path.Combine(projectRoot, relativePath).Replace("\\", "/");
            if (!File.Exists(fullPath) || fileMap.ContainsKey(relativePath))
            {
                return;
            }

            fileMap.Add(relativePath, new BackupFileItem
            {
                project_relative_path = relativePath,
                backup_relative_path = $"files/{relativePath}",
                is_meta = relativePath.EndsWith(".meta", StringComparison.OrdinalIgnoreCase),
                sha256 = "",
                byte_count = 0
            });
        }

        private static void AddMetaFileIfPresent(string projectRoot, string assetPath, Dictionary<string, BackupFileItem> fileMap)
        {
            AddFile(projectRoot, assetPath + ".meta", fileMap);
        }

        private static Transform ResolveAvatarRoot(string avatarPath)
        {
            var normalizedAvatarPath = NormalizePath(avatarPath);
            var descriptorType = FindType("VRC.SDK3.Avatars.Components.VRCAvatarDescriptor");
            if (descriptorType != null)
            {
                foreach (var descriptor in Resources.FindObjectsOfTypeAll(descriptorType).OfType<Component>().Where(IsSceneObject))
                {
                    var path = NormalizePath(GetTransformPath(descriptor.transform));
                    if (string.Equals(path, normalizedAvatarPath, StringComparison.OrdinalIgnoreCase)
                        || path.EndsWith("/" + normalizedAvatarPath, StringComparison.OrdinalIgnoreCase)
                        || descriptor.name.Equals(avatarPath, StringComparison.OrdinalIgnoreCase))
                    {
                        return descriptor.transform;
                    }
                }
            }

            throw new InvalidOperationException($"Avatar descriptor not found: {avatarPath}");
        }

        private static ProjectIdentity BuildProjectIdentity(string projectRoot)
        {
            return new ProjectIdentity
            {
                project_name = Directory.GetParent(Application.dataPath)?.Name ?? "UnknownProject",
                project_root_hash = StableHash(projectRoot.Replace("\\", "/").ToLowerInvariant()),
                unity_version = Application.unityVersion,
                data_path_hash = StableHash(Application.dataPath.Replace("\\", "/").ToLowerInvariant())
            };
        }

        private static bool IsSceneObject(Component component)
        {
            return component != null
                && component.gameObject.scene.IsValid()
                && component.gameObject.scene.isLoaded
                && !EditorUtility.IsPersistent(component);
        }

        private static Type FindType(string fullName)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    var type = assembly.GetType(fullName, false);
                    if (type != null)
                    {
                        return type;
                    }
                }
                catch
                {
                    // Ignore transient reflection failures from editor reloads.
                }
            }

            return null;
        }

        private static string GetTransformPath(Transform transform)
        {
            var segments = new Stack<string>();
            var current = transform;

            while (current != null)
            {
                segments.Push(current.name);
                current = current.parent;
            }

            return string.Join("/", segments);
        }

        private static string NormalizePath(string value)
        {
            return (value ?? string.Empty).Replace("\\", "/").Trim().Trim('/');
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
            return VRCForgeOutputPathGuard.ResolveManagedProjectPath(
                requestedPath,
                DefaultBackupRoot,
                DefaultBackupRoot,
                "Safe backup root");
        }

        private static string ToProjectRelativePath(string projectRoot, string fullPath)
        {
            var normalizedRoot = projectRoot.Replace("\\", "/").TrimEnd('/');
            var normalizedPath = fullPath.Replace("\\", "/");
            if (!normalizedPath.StartsWith(normalizedRoot + "/", StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException($"Path is outside the Unity project: {fullPath}");
            }

            return normalizedPath.Substring(normalizedRoot.Length + 1);
        }

        private static bool IsInside(string parentPath, string childPath)
        {
            var parent = parentPath.Replace("\\", "/").TrimEnd('/') + "/";
            var child = childPath.Replace("\\", "/").TrimEnd('/') + "/";
            return child.StartsWith(parent, StringComparison.OrdinalIgnoreCase);
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
        private class SafeBackupPayload
        {
            public string type;
            public string version;
            public string backup_id;
            public string created_at;
            public string unity_project;
            public ProjectIdentity project_identity;
            public string backup_path;
            public string backup_root;
            public List<string> requested_asset_paths;
            public List<BackupFileItem> files;
            public List<string> warnings;
            public List<string> restore_hints;
            public SafeBackupSummary summary;
            public object transaction;
        }

        [Serializable]
        private class ProjectIdentity
        {
            public string project_name;
            public string project_root_hash;
            public string unity_version;
            public string data_path_hash;
        }

        [Serializable]
        private class BackupFileItem
        {
            public string project_relative_path;
            public string backup_relative_path;
            public bool is_meta;
            public string sha256;
            public long byte_count;
            public bool before_exists;
            public string before_sha256 = "";
            public bool after_exists;
            public string after_sha256 = "";
            public string status = "not_attempted";
            public string error = "";
        }

        [Serializable]
        private class SafeBackupSummary
        {
            public int assetPathCount;
            public int fileCount;
            public int warningCount;
            public long totalBytes;
        }

        private sealed class SafeBackupTransactionException : Exception
        {
            public SafeBackupTransactionException(string message, object payload)
                : base(message)
            {
                Payload = payload;
            }

            public object Payload { get; }
        }
    }
}
