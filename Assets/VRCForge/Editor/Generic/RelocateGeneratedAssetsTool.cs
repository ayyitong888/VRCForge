using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using UnityEditor;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_relocate_generated_assets",
        Summary = "when-to-use: Preview or relocate an explicit, hash-bound list from Assets/VRCForge/Generated to Assets/VRCForgeGenerated. when-NOT-to-use: Do not use for ordinary asset creation, arbitrary moves, renaming, overwrites, or cleanup without an exact inventory.",
        Access = VRCForgeCommandAccess.RequiresApproval)]
    public static class RelocateGeneratedAssetsTool
    {
        public const string ToolName = "vrc_relocate_generated_assets";
        private const string Schema = "vrcforge.generated_asset_relocation.v1";
        private const string SourceRoot = "Assets/VRCForge/Generated/";
        private const string DestinationRoot = "Assets/VRCForgeGenerated/";
        private const int MaxFiles = 500;
        private static readonly Regex Hex32 = new Regex("^[0-9a-fA-F]{32}$", RegexOptions.Compiled);
        private static readonly Regex Hex64 = new Regex("^[0-9a-fA-F]{64}$", RegexOptions.Compiled);

        public sealed class Parameters
        {
            [VRCForgeInput("Exact source/destination generated asset bindings.", IsRequired = true)] public Entry[] entries { get; set; } = new Entry[0];
            [VRCForgeInput("Exact Unity project path bound during preview.", IsRequired = true)] public string expectedProjectPath { get; set; } = "";
            [VRCForgeInput("Return the verified plan without mutation (default true).", IsRequired = false, DefaultLiteral = "true")] public bool? preview { get; set; } = true;
        }

        public sealed class Entry
        {
            public string sourceAssetPath { get; set; } = "";
            public string destinationAssetPath { get; set; } = "";
            public string expectedGuid { get; set; } = "";
            public string expectedAssetSha256 { get; set; } = "";
            public string expectedMetaSha256 { get; set; } = "";
        }

        private sealed class Binding
        {
            internal Entry Input;
            internal bool Attempted;
            internal bool Moved;
            internal string RollbackState = "not_attempted";
            internal string VerificationState = "preflight_verified";
            internal string Error = "";
        }

        private sealed class FolderRecord
        {
            internal string Path;
            internal string Guid;
            internal string MetaHash;
        }

        public static object HandleCommand(JObject @params)
        {
            var bindings = new List<Binding>();
            var createdFolders = new List<FolderRecord>();
            var mutationStarted = false;
            Parameters p = null;
            try
            {
                p = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters();
                var projectPath = ResolveProjectPath();
                if (!string.Equals(NormalizeAbsolute(projectPath), NormalizeAbsolute(p.expectedProjectPath), StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("expectedProjectPath does not match the current Unity project.");
                var inputs = p.entries ?? new Entry[0];
                if (inputs.Length < 1 || inputs.Length > MaxFiles) throw new InvalidOperationException("entries must contain between 1 and 500 files.");
                var sourceSet = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                var destinationSet = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (var input in inputs)
                {
                    var b = ValidateAndPreflight(input, projectPath, sourceSet, destinationSet);
                    bindings.Add(b);
                }
                foreach (var binding in bindings)
                    ValidateDestinationAncestors(binding.Input.destinationAssetPath, destinationSet);
                var preview = p.preview ?? true;
                var payload = BuildPayload(projectPath, bindings, preview, false, false, "not_started", "not_started", "not_started", "not_applicable", false, false, true);
                if (preview) return VRCForgeToolResult.Completed("Generated asset relocation preflight verified; no files were changed.", payload);

                foreach (var binding in bindings)
                {
                    RejectDirtyAsset(binding.Input.sourceAssetPath);
                    var current = SceneObjectCopyCore.ReadStableAssetEvidence(binding.Input.sourceAssetPath, "generated source revalidation");
                    if (!MatchesExpected(binding.Input, current) || SceneObjectCopyCore.AssetOrMetaExists(binding.Input.destinationAssetPath))
                        throw new InvalidOperationException("source or destination changed after preflight.");
                    EnsureDestinationFolders(binding.Input.destinationAssetPath, createdFolders, ref mutationStarted);
                    if (SceneObjectCopyCore.AssetOrMetaExists(binding.Input.destinationAssetPath)) throw new InvalidOperationException("destination appeared before move.");
                    mutationStarted = true;
                    binding.Attempted = true;
                    var error = AssetDatabase.MoveAsset(binding.Input.sourceAssetPath, binding.Input.destinationAssetPath);
                    if (!string.IsNullOrEmpty(error)) throw new InvalidOperationException(error);
                    binding.Moved = true;
                }
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                VerifyFinal(bindings);
                return VRCForgeToolResult.Completed("Generated assets relocated and verified.", BuildPayload(projectPath, bindings, false, true, true, "committed", "persisted", "verified", "complete", false, true, true));
            }
            catch (Exception ex)
            {
                if (!mutationStarted)
                {
                    var failedPreview = BuildPayload(ResolveProjectPathSafe(), bindings, p == null || p.preview == null || p.preview.Value, false, false, "not_started", "not_started", "not_started", "not_applicable", false, false, false);
                    return VRCForgeToolResult.FailedWithCode("generated_asset_relocation_failed", "Generated asset relocation failed: " + ex.Message, failedPreview);
                }
                var recoveryRequired = false;
                var rollbackAttempted = bindings.Any(item => item.Attempted) || createdFolders.Count > 0;
                if (rollbackAttempted)
                {
                    foreach (var binding in bindings.Where(item => item.Attempted).Reverse())
                    {
                        try
                        {
                            if (SceneObjectCopyCore.AssetOrMetaExists(binding.Input.sourceAssetPath))
                            {
                                var unchanged = SceneObjectCopyCore.ReadStableAssetEvidence(binding.Input.sourceAssetPath, "relocation unchanged source");
                                if (!MatchesExpected(binding.Input, unchanged) || SceneObjectCopyCore.AssetOrMetaExists(binding.Input.destinationAssetPath))
                                    throw new InvalidOperationException("source or destination changed during the failed move");
                                binding.RollbackState = "unchanged";
                                continue;
                            }
                            var current = SceneObjectCopyCore.ReadStableAssetEvidence(binding.Input.destinationAssetPath, "relocation rollback asset");
                            if (!MatchesExpected(binding.Input, current)) throw new InvalidOperationException("destination evidence changed before rollback");
                            var error = AssetDatabase.MoveAsset(binding.Input.destinationAssetPath, binding.Input.sourceAssetPath);
                            if (!string.IsNullOrEmpty(error)) throw new InvalidOperationException(error);
                            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                            var restored = SceneObjectCopyCore.ReadStableAssetEvidence(binding.Input.sourceAssetPath, "relocation restored source");
                            if (!MatchesExpected(binding.Input, restored) || SceneObjectCopyCore.AssetOrMetaExists(binding.Input.destinationAssetPath)) throw new InvalidOperationException("restored source readback did not prove exact recovery");
                            binding.RollbackState = "rolled_back";
                        }
                        catch (Exception rollbackEx)
                        {
                            binding.RollbackState = "failed";
                            binding.Error = "Rollback failed: " + rollbackEx.Message;
                            recoveryRequired = true;
                        }
                    }
                    try { AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport); } catch { recoveryRequired = true; }
                }
                if (!recoveryRequired)
                {
                    foreach (var folder in createdFolders.AsEnumerable().Reverse())
                    {
                        try
                        {
                            var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(folder.Path);
                            if (!AssetDatabase.IsValidFolder(folder.Path)
                                || !string.Equals(AssetDatabase.AssetPathToGUID(folder.Path), folder.Guid, StringComparison.OrdinalIgnoreCase)
                                || !string.Equals(Sha256(absolute + ".meta"), folder.MetaHash, StringComparison.OrdinalIgnoreCase)
                                || Directory.EnumerateFileSystemEntries(absolute).Any())
                            {
                                recoveryRequired = true;
                                continue;
                            }
                            if (!AssetDatabase.DeleteAsset(folder.Path)
                                || AssetDatabase.IsValidFolder(folder.Path)
                                || File.Exists(absolute + ".meta")) recoveryRequired = true;
                        }
                        catch { recoveryRequired = true; }
                    }
                    try { AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport); } catch { recoveryRequired = true; }
                }
                if (!rollbackAttempted) recoveryRequired = true;
                var payload = BuildPayload(ResolveProjectPathSafe(), bindings, p == null || p.preview == null || p.preview.Value, true, false,
                    rollbackAttempted && !recoveryRequired ? "rolled_back" : rollbackAttempted ? "unknown" : "not_started",
                    rollbackAttempted && !recoveryRequired ? "persisted" : "unknown", "failed",
                    recoveryRequired ? "failed" : rollbackAttempted ? "complete" : "not_applicable", recoveryRequired, false, false);
                return VRCForgeToolResult.FailedWithCode("generated_asset_relocation_failed", "Generated asset relocation failed: " + ex.Message, payload);
            }
        }

        private static Binding ValidateAndPreflight(Entry input, string projectPath, ISet<string> sources, ISet<string> destinations)
        {
            if (input == null) throw new InvalidOperationException("entries cannot contain null items.");
            var source = NormalizeAssetPath(input.sourceAssetPath);
            var destination = NormalizeAssetPath(input.destinationAssetPath);
            if (!source.StartsWith(SourceRoot, StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("sourceAssetPath must be under Assets/VRCForge/Generated/.");
            if (!destination.StartsWith(DestinationRoot, StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("destinationAssetPath must be under Assets/VRCForgeGenerated/.");
            if (Path.GetFileName(source) != Path.GetFileName(destination)) throw new InvalidOperationException("source and destination must retain the same filename and extension.");
            if (!sources.Add(source) || !destinations.Add(destination)) throw new InvalidOperationException("duplicate source or destination entries are not allowed.");
            if (!Hex32.IsMatch(input.expectedGuid ?? "") || !Hex64.IsMatch(input.expectedAssetSha256 ?? "") || !Hex64.IsMatch(input.expectedMetaSha256 ?? "")) throw new InvalidOperationException("expectedGuid and expected SHA-256 values have invalid format.");
            SceneObjectCopyCore.ToAbsoluteAssetPath(source);
            SceneObjectCopyCore.ToAbsoluteAssetPath(destination);
            if (SceneObjectCopyCore.AssetOrMetaExists(destination)) throw new InvalidOperationException("destination asset or metadata already exists; overwrite is refused.");
            RejectDirtyAsset(source);
            var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(source, "generated source asset");
            if (!MatchesExpected(input, evidence)) throw new InvalidOperationException("source GUID or byte hashes do not match the expected binding.");
            if (evidence.File.LinkCount != 1 || evidence.Meta.LinkCount != 1) throw new InvalidOperationException("hard-linked source or metadata is not allowed.");
            return new Binding { Input = new Entry { sourceAssetPath = source, destinationAssetPath = destination, expectedGuid = input.expectedGuid.ToLowerInvariant(), expectedAssetSha256 = input.expectedAssetSha256.ToLowerInvariant(), expectedMetaSha256 = input.expectedMetaSha256.ToLowerInvariant() } };
        }

        private static bool MatchesExpected(Entry input, StableAssetEvidence evidence)
        {
            return evidence != null && string.Equals(evidence.Guid, input.expectedGuid, StringComparison.OrdinalIgnoreCase)
                && evidence.File != null && string.Equals(evidence.File.Digest, input.expectedAssetSha256, StringComparison.OrdinalIgnoreCase)
                && evidence.Meta != null && string.Equals(evidence.Meta.Digest, input.expectedMetaSha256, StringComparison.OrdinalIgnoreCase)
                && evidence.File.LinkCount == 1 && evidence.Meta.LinkCount == 1;
        }

        private static void RejectDirtyAsset(string path)
        {
            if (AssetDatabase.LoadAllAssetsAtPath(path).Any(asset => asset != null && EditorUtility.IsDirty(asset))
                || (AssetImporter.GetAtPath(path) is AssetImporter importer && EditorUtility.IsDirty(importer)))
                throw new InvalidOperationException("Source asset has unsaved changes; save it explicitly and refresh the inventory before relocation.");
        }

        private static void ValidateDestinationAncestors(string assetPath, ISet<string> destinations)
        {
            var parent = Path.GetDirectoryName(assetPath).Replace('\\', '/');
            while (parent != "Assets")
            {
                var absolute = SceneObjectCopyCore.ToAbsoluteAssetPath(parent);
                if (destinations.Contains(parent) || (!AssetDatabase.IsValidFolder(parent)
                    && (SceneObjectCopyCore.AssetOrMetaExists(parent) || Directory.Exists(absolute))))
                    throw new InvalidOperationException("destination folder ancestor is occupied or is an unregistered folder.");
                parent = Path.GetDirectoryName(parent).Replace('\\', '/');
            }
        }

        private static void VerifyFinal(IEnumerable<Binding> bindings)
        {
            foreach (var binding in bindings)
            {
                if (SceneObjectCopyCore.AssetOrMetaExists(binding.Input.sourceAssetPath)) throw new InvalidOperationException("source path remains after relocation.");
                var evidence = SceneObjectCopyCore.ReadStableAssetEvidence(binding.Input.destinationAssetPath, "relocated asset readback");
                if (!MatchesExpected(binding.Input, evidence) || evidence.File.LinkCount != 1 || evidence.Meta.LinkCount != 1) throw new InvalidOperationException("relocated asset readback does not match exact expected evidence.");
                binding.VerificationState = "verified";
            }
        }

        private static void EnsureDestinationFolders(string assetPath, ICollection<FolderRecord> created, ref bool mutationStarted)
        {
            var parent = Path.GetDirectoryName(assetPath.Replace('\\', '/')).Replace('\\', '/');
            var parts = parent.Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries);
            var current = parts[0];
            for (var i = 1; i < parts.Length; i++)
            {
                var next = current + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next))
                {
                    if (SceneObjectCopyCore.AssetOrMetaExists(next)) throw new InvalidOperationException("destination folder ancestor is occupied by a file or metadata.");
                    mutationStarted = true;
                    var guid = AssetDatabase.CreateFolder(current, parts[i]);
                    if (string.IsNullOrEmpty(guid)) throw new InvalidOperationException("Could not create destination folder.");
                    var actualPath = AssetDatabase.GUIDToAssetPath(guid);
                    var record = new FolderRecord { Path = actualPath, Guid = guid };
                    created.Add(record);
                    record.MetaHash = Sha256(SceneObjectCopyCore.ToAbsoluteAssetPath(actualPath) + ".meta");
                    if (!string.Equals(actualPath, next, StringComparison.Ordinal)
                        || !string.Equals(guid, AssetDatabase.AssetPathToGUID(next), StringComparison.OrdinalIgnoreCase))
                        throw new InvalidOperationException("Unity created a folder with an unexpected identity.");
                }
                current = next;
            }
        }

        private static object BuildPayload(string projectPath, IEnumerable<Binding> bindings, bool preview, bool mutationStarted, bool committed, string commitState, string persistenceState, string readbackState, string cleanupState, bool recovery, bool changed, bool success)
        {
            return new { schema = Schema, operation = "relocate_generated_assets", ok = success, verified = success, expectedProjectPath = projectPath, preview, changed, mutationCount = bindings.Count(item => item.Moved), mutationStarted, committed, commitState, persistenceState, readbackState, cleanupState, checkpointRecoveryRequired = recovery,
                entries = bindings.Select(item => new { item.Input.sourceAssetPath, item.Input.destinationAssetPath, item.Input.expectedGuid, item.Input.expectedAssetSha256, item.Input.expectedMetaSha256 }).ToArray(),
                items = bindings.Select(item => new { item.Input.sourceAssetPath, item.Input.destinationAssetPath, moved = item.Moved, rollbackState = item.RollbackState, verificationState = item.VerificationState, error = item.Error }).ToArray() };
        }

        private static string NormalizeAssetPath(string value)
        {
            if (string.IsNullOrWhiteSpace(value) || value.IndexOf('\0') >= 0) throw new InvalidOperationException("asset paths must be non-empty and NUL-free.");
            var path = value.Replace('\\', '/');
            if (path.StartsWith("/", StringComparison.Ordinal) || Path.IsPathRooted(path) || path.Split('/').Any(item => item == ".." || item == "." || item.Length == 0)
                || path.IndexOf(':') >= 0 || path != path.Trim() || path.EndsWith(".meta", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("absolute, traversal, sidecar, and non-canonical asset paths are not allowed.");
            return path;
        }

        private static string ResolveProjectPath() { var value = Directory.GetParent(UnityEngine.Application.dataPath)?.FullName; if (string.IsNullOrWhiteSpace(value)) throw new InvalidOperationException("Unity project path is unavailable."); return value; }
        private static string ResolveProjectPathSafe() { try { return ResolveProjectPath(); } catch { return ""; } }
        private static string NormalizeAbsolute(string value) { return string.IsNullOrWhiteSpace(value) ? "" : Path.GetFullPath(value).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar); }
        private static string Sha256(string path) { using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read)) using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant(); }
    }
}
