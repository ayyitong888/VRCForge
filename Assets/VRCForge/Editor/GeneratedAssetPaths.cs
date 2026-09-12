using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;

namespace VRCForge.Editor
{
    // Output selection is read-only, so preview and apply share the same policy.
    // Existing asset references are resolved by their owners and are never relocated.
    internal static class GeneratedAssetPaths
    {
        internal const string Root = "Assets/VRCForgeGenerated";
        internal const string Animations = "Animations";
        internal const string Controllers = "Controllers";
        internal const string Menus = "Menus";
        internal const string Parameters = "Parameters";

        internal static string ResolveDirectory(
            string requestedDirectory, string avatarName, string category,
            string domain = "", bool categorizeExplicit = false)
        {
            if (string.IsNullOrWhiteSpace(requestedDirectory))
            {
                var avatar = new string((avatarName ?? "").Select(
                    c => char.IsLetterOrDigit(c) || c == '_' ? c : '_').ToArray()).Trim('_');
                if (string.IsNullOrEmpty(avatar)) avatar = "Avatar";
                return ValidateNewAssetPath($"{Root}/{avatar}/{(string.IsNullOrEmpty(domain) ? "" : domain + "/")}{category}");
            }

            var directory = ValidateNewAssetPath(requestedDirectory);
            if (categorizeExplicit && !directory.EndsWith("/" + category, StringComparison.OrdinalIgnoreCase))
            {
                var lastSeparator = directory.LastIndexOf('/');
                var lastSegment = directory.Substring(lastSeparator + 1);
                if (new[] { Animations, Controllers, Menus, Parameters }.Contains(lastSegment, StringComparer.OrdinalIgnoreCase))
                    directory = directory.Substring(0, lastSeparator);
                directory = ValidateNewAssetPath(directory + "/" + category);
            }
            return directory;
        }

        internal static string ValidateNewAssetPath(string value)
        {
            var normalized = (value ?? "").Replace("\\", "/").Trim().TrimEnd('/');
            var segments = normalized.Split('/');
            if (segments.Length == 0 || segments[0] != "Assets"
                || segments.Any(segment => string.IsNullOrEmpty(segment) || segment == "." || segment == ".."
                    || segment.EndsWith(".", StringComparison.Ordinal) || segment.EndsWith(" ", StringComparison.Ordinal)
                    || segment.Any(c => char.IsControl(c) || "<>:\"|?*".IndexOf(c) >= 0)))
            {
                throw new InvalidOperationException($"Generated output must use an Assets-relative path without dot segments or invalid names: {value}");
            }
            if (segments.Length > 1 && segments[1].Equals("VRCForge", StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException($"Generated output cannot be written inside the VRCForge plugin directory. Use {Root} or another Assets directory: {value}");
            }
            return normalized;
        }

        internal static string UniqueAssetPath(string requestedPath)
        {
            var path = ValidateNewAssetPath(requestedPath);
            var separator = path.LastIndexOf('/');
            var directory = separator > 0 ? path.Substring(0, separator) : "Assets";
            if (!AssetDatabase.IsValidFolder(directory))
            {
                return path;
            }

            var candidate = AssetDatabase.GenerateUniqueAssetPath(path);
            if (string.IsNullOrWhiteSpace(candidate))
            {
                throw new InvalidOperationException($"Unity returned an empty generated asset path for: {path}");
            }
            return ValidateNewAssetPath(candidate);
        }

        internal static string ReserveAssetPath(string requestedPath, List<string> reservedPaths, Func<string, string> uniquePath)
        {
            var path = ValidateNewAssetPath(requestedPath);
            var candidate = uniquePath(path);
            if (string.IsNullOrWhiteSpace(candidate))
            {
                throw new InvalidOperationException($"Generated asset path is empty for: {path}");
            }
            var suffix = 2;
            while (reservedPaths.Contains(candidate, StringComparer.OrdinalIgnoreCase))
            {
                var extension = Path.GetExtension(path);
                candidate = uniquePath(path.Substring(0, path.Length - extension.Length) + "_" + suffix++ + extension);
            }
            candidate = ValidateNewAssetPath(candidate);
            reservedPaths.Add(candidate);
            return candidate;
        }
    }
}
