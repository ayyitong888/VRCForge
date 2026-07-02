using System;
using System.IO;
using UnityEngine;

namespace VRCForge.Editor
{
    internal static class VRCForgeOutputPathGuard
    {
        private const string ManagedAssetRoot = "Assets/VRCForge";

        public static string ResolveManagedProjectOutputPath(string requestedPath, string label)
        {
            return ResolveManagedProjectPath(requestedPath, string.Empty, ManagedAssetRoot, label);
        }

        public static string ResolveManagedProjectPath(string requestedPath, string defaultPath, string managedRoot, string label)
        {
            if (string.IsNullOrWhiteSpace(requestedPath))
            {
                requestedPath = defaultPath;
            }

            if (string.IsNullOrWhiteSpace(requestedPath))
            {
                throw new InvalidOperationException($"{label} path is required.");
            }

            var projectRoot = Directory.GetParent(Application.dataPath)?.FullName
                ?? throw new InvalidOperationException("Cannot determine Unity project root.");
            var candidate = Path.IsPathRooted(requestedPath)
                ? requestedPath
                : Path.Combine(projectRoot, requestedPath);
            var fullPath = Path.GetFullPath(candidate);
            var managedRootPath = Path.GetFullPath(Path.Combine(projectRoot, managedRoot));

            if (!IsWithin(fullPath, managedRootPath))
            {
                throw new InvalidOperationException($"{label} path must stay under {managedRoot}.");
            }

            return fullPath.Replace("\\", "/");
        }

        public static string ToAssetRelativePath(string absolutePath)
        {
            var normalized = Path.GetFullPath(absolutePath).Replace("\\", "/");
            var dataPath = Application.dataPath.Replace("\\", "/");
            if (normalized.StartsWith(dataPath + "/", StringComparison.OrdinalIgnoreCase))
            {
                return "Assets" + normalized.Substring(dataPath.Length);
            }

            return normalized;
        }

        private static bool IsWithin(string path, string root)
        {
            var normalizedPath = Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            var normalizedRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            return string.Equals(normalizedPath, normalizedRoot, StringComparison.OrdinalIgnoreCase)
                || normalizedPath.StartsWith(normalizedRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
                || normalizedPath.StartsWith(normalizedRoot + Path.AltDirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
        }
    }
}
