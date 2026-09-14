using System;
using System.Globalization;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_duplicate_project_asset",
        Summary = "Create-new copy one supported Unity authoring asset into Assets/VRCForgeGenerated or a classified subfolder without overwriting the source or destination. Supports preview."
    )]
    public static class DuplicateProjectAssetTool
    {
        internal const string ToolName = "vrc_duplicate_project_asset";
        internal const string ResultSchema = "vrcforge.project_asset_copy.v2";
        internal const string Operation = "duplicate_project_asset";
        internal const string GeneratedRoot = "Assets/VRCForgeGenerated";
        private const string AnchorRoot = "Assets";
        private const string LegacyGeneratedRoot = "Assets/VRCForge/Generated";
        private const string PreviewDigestSchema = "vrcforge.project_asset_copy_preview.v2";
        private const int StableReadAttempts = 3;
        private const int StableReadRetryDelayMilliseconds = 75;

        private static readonly string[] AllowedExtensions =
        {
            ".controller",
            ".asset",
            ".anim",
            ".overridecontroller",
            ".mat",
        };

        public class Parameters
        {
            [VRCForgeInput("Optional 1..128 create-new copies; sourceAssetPath/destinationAssetPath per row, all parents existing. Mutually exclusive with single fields. Maximum 512 KiB sealed payload.", IsRequired = false)] public object[] copies { get; set; }
            [VRCForgeInput("Exact full batch preview rows bound by expectedPreviewDigest.", IsRequired = false)] public object[] expectedCopies { get; set; }
            [VRCForgeInput("Existing source asset path below Assets; required for a single copy, mutually exclusive with copies.", IsRequired = false)] public string sourceAssetPath { get; set; } = "";
            [VRCForgeInput("Exact create-new path below Assets/VRCForgeGenerated. Missing parent folders are previewed and created atomically with the copy.", IsRequired = false)] public string destinationAssetPath { get; set; } = "";
            [VRCForgeInput("Return a non-mutating copy preview.", IsRequired = false)] public bool? preview { get; set; } = false;
            [VRCForgeInput("Must remain false; overwrite is unsupported.", IsRequired = false)] public bool? overwrite { get; set; } = false;
            [VRCForgeInput("Expected active Unity project root from preview.", IsRequired = false)] public string expectedProjectPath { get; set; } = "";
            [VRCForgeInput("Expected source GUID.", IsRequired = false)] public string expectedSourceGuid { get; set; } = "";
            [VRCForgeInput("Expected source file SHA-256.", IsRequired = false)] public string expectedSourceFileDigest { get; set; } = "";
            [VRCForgeInput("Expected source file identity.", IsRequired = false)] public string expectedSourceFileIdentity { get; set; } = "";
            [VRCForgeInput("Expected source meta SHA-256.", IsRequired = false)] public string expectedSourceMetaDigest { get; set; } = "";
            [VRCForgeInput("Expected source meta identity.", IsRequired = false)] public string expectedSourceMetaIdentity { get; set; } = "";
            [VRCForgeInput("Expected source main asset type.", IsRequired = false)] public string expectedSourceMainAssetType { get; set; } = "";
            [VRCForgeInput("Expected source Unity object-layout digest.", IsRequired = false)] public string expectedSourceObjectLayoutDigest { get; set; } = "";
            [VRCForgeInput("Whether the generated root existed during preview.", IsRequired = false)] public bool? expectedGeneratedRootExists { get; set; }
            [VRCForgeInput("Expected generated-root GUID when it existed during preview.", IsRequired = false)] public string expectedGeneratedRootGuid { get; set; } = "";
            [VRCForgeInput("Expected generated-root identity when it existed during preview.", IsRequired = false)] public string expectedGeneratedRootIdentity { get; set; } = "";
            [VRCForgeInput("Expected stable anchor-folder GUID.", IsRequired = false)] public string expectedAnchorFolderGuid { get; set; } = "";
            [VRCForgeInput("Expected stable anchor-folder identity.", IsRequired = false)] public string expectedAnchorFolderIdentity { get; set; } = "";
            [VRCForgeInput("Expected destination-parent GUID from preview; empty only for a root created by this copy.", IsRequired = false)] public string expectedDestinationParentFolderGuid { get; set; } = "";
            [VRCForgeInput("Expected destination-parent identity from preview; empty only for a root created by this copy.", IsRequired = false)] public string expectedDestinationParentFolderIdentity { get; set; } = "";
            [VRCForgeInput("Expected destination-absent assertion from preview.", IsRequired = false)] public bool? expectedDestinationAbsent { get; set; }
            [VRCForgeInput("Expected preview digest.", IsRequired = false)] public string expectedPreviewDigest { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            if (@params?["copies"] != null) return UnityProjectAssetCopyBatch.HandleCommand(@params);
            try
            {
                var parameters = @params ?? new JObject();
                var preview = parameters["preview"]?.Value<bool?>() ?? false;
                var overwrite = parameters["overwrite"]?.Value<bool?>() ?? false;
                if (overwrite)
                {
                    throw new ProjectAssetCopyException("Project asset overwrite is not supported.");
                }

                var snapshot = BuildSnapshot(
                    parameters["sourceAssetPath"]?.ToString() ?? string.Empty,
                    parameters["destinationAssetPath"]?.ToString() ?? string.Empty);
                if (preview)
                {
                    return VRCForgeToolResult.Completed(
                        "Preview: would create one independent Unity authoring asset copy.",
                        snapshot.ToPreviewPayload());
                }

                VerifyExpected(parameters, snapshot);
                return Apply(snapshot);
            }
            catch (Exception exception)
            {
                return Failure(exception, false, false, "validation");
            }
        }

        internal static ProjectAssetCopySnapshot BuildSnapshot(string rawSourcePath, string rawDestinationPath)
        {
            var sourcePath = NormalizeSourcePath(rawSourcePath);
            var destinationPath = NormalizeDestinationPath(rawDestinationPath);
            if (string.Equals(sourcePath, destinationPath, StringComparison.Ordinal))
            {
                throw new ProjectAssetCopyException("The source and destination asset paths are identical.");
            }
            if (!string.Equals(
                Path.GetExtension(sourcePath),
                Path.GetExtension(destinationPath),
                StringComparison.OrdinalIgnoreCase))
            {
                throw new ProjectAssetCopyException("Source and destination extensions must match.");
            }
            if (SceneObjectCopyCore.AssetOrMetaExists(destinationPath))
            {
                throw new ProjectAssetCopyException("The destination asset or metadata already exists.");
            }

            var sourceObject = AssetDatabase.LoadMainAssetAtPath(sourcePath);
            var sourceType = AssetDatabase.GetMainAssetTypeAtPath(sourcePath);
            if (sourceObject == null || sourceType == null || AssetDatabase.IsValidFolder(sourcePath))
            {
                throw new ProjectAssetCopyException("The source Unity authoring asset is unavailable.");
            }
            ValidateGeneratedSourceType(sourcePath, sourceType.FullName ?? sourceType.Name);
            var source = SceneObjectCopyCore.ReadStableAssetEvidence(sourcePath, "project asset copy source");
            if (source.File.LinkCount != 1 || source.Meta.LinkCount != 1)
            {
                throw new ProjectAssetCopyException("The source asset must be backed by single-link files.");
            }

            var anchorGuid = SceneObjectCopyCore.ReadAssetGuid(AnchorRoot, "generated asset anchor");
            var anchorIdentity = SceneObjectCopyCore.ReadDirectoryIdentity(AnchorRoot, "generated asset anchor");
            var generatedRootExists = AssetDatabase.IsValidFolder(GeneratedRoot);
            string generatedRootGuid = string.Empty;
            string generatedRootIdentity = string.Empty;
            if (generatedRootExists)
            {
                generatedRootGuid = SceneObjectCopyCore.ReadAssetGuid(GeneratedRoot, "generated asset root");
                generatedRootIdentity = SceneObjectCopyCore.ReadDirectoryIdentity(GeneratedRoot, "generated asset root");
            }
            else if (SceneObjectCopyCore.AssetOrMetaExists(GeneratedRoot))
            {
                throw new ProjectAssetCopyException("The generated asset root path is occupied by an incomplete asset.");
            }

            var parentFolderPath = destinationPath.Substring(0, destinationPath.LastIndexOf('/'));
            var missingFolders = new List<string>();
            var ancestorPath = parentFolderPath;
            while (!AssetDatabase.IsValidFolder(ancestorPath))
            {
                if (!ancestorPath.StartsWith(GeneratedRoot + "/", StringComparison.Ordinal) && ancestorPath != GeneratedRoot)
                    throw new ProjectAssetCopyException("The destination folder chain escaped the generated root.");
                if (SceneObjectCopyCore.AssetOrMetaExists(ancestorPath))
                    throw new ProjectAssetCopyException("A destination folder path or metadata is already occupied.");
                missingFolders.Insert(0, ancestorPath);
                ancestorPath = ancestorPath.Substring(0, ancestorPath.LastIndexOf('/'));
            }
            var ancestorGuid = SceneObjectCopyCore.ReadAssetGuid(ancestorPath, "destination existing ancestor");
            var ancestorIdentity = SceneObjectCopyCore.ReadDirectoryIdentity(ancestorPath, "destination existing ancestor");
            var parentFolderGuid = missingFolders.Count == 0 ? ancestorGuid : string.Empty;
            var parentFolderIdentity = missingFolders.Count == 0 ? ancestorIdentity : string.Empty;

            var snapshot = new ProjectAssetCopySnapshot
            {
                SourcePath = sourcePath,
                DestinationPath = destinationPath,
                SourceEvidence = source,
                SourceMainAssetType = sourceType.FullName ?? sourceType.Name,
                SourceObjectLayoutDigest = ComputeObjectLayoutDigest(sourcePath),
                GeneratedRootExists = generatedRootExists,
                GeneratedRootGuid = generatedRootGuid,
                GeneratedRootIdentity = generatedRootIdentity,
                AnchorFolderGuid = anchorGuid,
                AnchorFolderIdentity = anchorIdentity,
                ParentFolderPath = parentFolderPath,
                ParentFolderGuid = parentFolderGuid,
                ParentFolderIdentity = parentFolderIdentity,
                MissingFolders = missingFolders.ToArray(),
                ExistingAncestorPath = ancestorPath,
                ExistingAncestorGuid = ancestorGuid,
                ExistingAncestorIdentity = ancestorIdentity,
            };
            snapshot.PreviewDigest = ComputePreviewDigest(snapshot);
            return snapshot;
        }

        private static object Apply(ProjectAssetCopySnapshot snapshot)
        {
            var mutationStarted = false;
            var generatedRootCreated = false;
            StableAssetEvidence createdEvidence = null;
            var createdFolders = new List<CreatedFolder>();
            var unverifiedFolderCreation = false;
            var attemptedFolder = string.Empty;
            var failurePhase = "preflight";
            try
            {
                VerifySnapshotCurrent(snapshot);
                foreach (var folderPath in snapshot.MissingFolders)
                {
                    failurePhase = "destination_folder_creation";
                    SceneObjectCopyCore.VerifyFolderIdentity(snapshot.ExistingAncestorPath,
                        snapshot.ExistingAncestorGuid, snapshot.ExistingAncestorIdentity, "destination existing ancestor");
                    foreach (var owned in createdFolders) VerifyCreatedFolder(owned);
                    if (SceneObjectCopyCore.AssetOrMetaExists(folderPath))
                        throw new ProjectAssetCopyException("A planned destination folder changed before creation.");
                    var parent = folderPath.Substring(0, folderPath.LastIndexOf('/'));
                    mutationStarted = true;
                    unverifiedFolderCreation = true;
                    attemptedFolder = folderPath;
                    var createdGuid = AssetDatabase.CreateFolder(parent, Path.GetFileName(folderPath));
                    var normalizedGuid = NormalizeHex(createdGuid, 32, "created destination-folder GUID");
                    var actualPath = (AssetDatabase.GUIDToAssetPath(normalizedGuid) ?? string.Empty).Replace('\\', '/');
                    if (!string.Equals(actualPath, folderPath, StringComparison.Ordinal))
                        throw new ProjectAssetCopyException("The destination folder was not created exactly.");
                    var lease = new StagingFolderLease {
                        RootPath = parent, FolderPath = folderPath, FolderGuid = normalizedGuid,
                        FolderIdentity = SceneObjectCopyCore.ReadDirectoryIdentity(folderPath, "created destination folder")
                    };
                    createdFolders.Add(new CreatedFolder { Lease = lease, MetaDigest = ReadFolderMetaDigest(folderPath) });
                    unverifiedFolderCreation = false;
                    if (folderPath == GeneratedRoot) generatedRootCreated = true;
                }

                if (!string.IsNullOrEmpty(snapshot.ParentFolderGuid))
                {
                    SceneObjectCopyCore.VerifyFolderIdentity(
                        snapshot.ParentFolderPath, snapshot.ParentFolderGuid, snapshot.ParentFolderIdentity,
                        "project asset copy destination parent");
                }
                if (SceneObjectCopyCore.AssetOrMetaExists(snapshot.DestinationPath))
                {
                    throw new ProjectAssetCopyException("The destination changed before the copy started.");
                }
                foreach (var owned in createdFolders) VerifyCreatedFolder(owned);
                failurePhase = "asset_copy";
                if (!AssetDatabase.CopyAsset(snapshot.SourcePath, snapshot.DestinationPath))
                {
                    throw new ProjectAssetCopyException("Unity AssetDatabase refused the create-new asset copy.");
                }
                mutationStarted = true;
                createdEvidence = ReadCreatedEvidenceWithRetry(snapshot.DestinationPath);
                var copiedAsset = AssetDatabase.LoadMainAssetAtPath(snapshot.DestinationPath)
                    ?? throw new ProjectAssetCopyException("The copied asset is unavailable for targeted saving.");
                AssetDatabase.SaveAssetIfDirty(copiedAsset);
                AssetDatabase.ImportAsset(
                    snapshot.DestinationPath,
                    ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

                failurePhase = "created_asset_readback";
                createdEvidence = ReadCreatedEvidenceWithRetry(snapshot.DestinationPath);
                var destinationType = AssetDatabase.GetMainAssetTypeAtPath(snapshot.DestinationPath);
                var destinationObjectLayoutDigest = ComputeObjectLayoutDigest(snapshot.DestinationPath);
                if (createdEvidence.Guid == snapshot.SourceEvidence.Guid
                    || createdEvidence.File.LinkCount != 1
                    || createdEvidence.Meta.LinkCount != 1
                    || destinationType == null
                    || !string.Equals(
                        destinationType.FullName ?? destinationType.Name,
                        snapshot.SourceMainAssetType,
                        StringComparison.Ordinal)
                    || destinationObjectLayoutDigest != snapshot.SourceObjectLayoutDigest)
                {
                    throw new ProjectAssetCopyException("The created project asset copy failed independent Unity-object readback verification.");
                }
                failurePhase = "source_unchanged_readback";
                VerifySourceUnchanged(snapshot);
                foreach (var owned in createdFolders) VerifyCreatedFolder(owned);

                var beforePayload = new
                {
                    source = SourcePayload(snapshot),
                    target = new
                    {
                        assetPath = snapshot.DestinationPath,
                        exists = false
                    }
                };
                var afterPayload = new
                {
                    assetPath = snapshot.DestinationPath,
                    guid = createdEvidence.Guid,
                    fileDigest = createdEvidence.File.Digest,
                    fileIdentity = createdEvidence.File.Identity,
                    metaDigest = createdEvidence.Meta.Digest,
                    metaIdentity = createdEvidence.Meta.Identity,
                    mainAssetType = snapshot.SourceMainAssetType,
                    objectLayoutDigest = destinationObjectLayoutDigest,
                    bytesIdenticalToSource = createdEvidence.File.Digest == snapshot.SourceEvidence.File.Digest,
                    generatedRootPath = GeneratedRoot,
                    generatedRootCreated,
                    createdFolders = createdFolders.Select(item => item.Lease.FolderPath).ToArray(),
                    createNew = true,
                    readbackVerified = true,
                };
                var affectedItems = new[] { snapshot.DestinationPath }.Concat(createdFolders.Select(item => item.Lease.FolderPath)).ToArray();

                return VRCForgeToolResult.Completed(
                    "Created and verified one independent Unity authoring asset copy.",
                    new
                    {
                        schema = ResultSchema,
                        ok = true,
                        operation = Operation,
                        preview = false,
                        verified = true,
                        changed = true,
                        saved = true,
                        mutationCount = 1 + createdFolders.Count,
                        source = SourcePayload(snapshot),
                        target = afterPayload,
                        before = beforePayload,
                        after = afterPayload,
                        affected = new
                        {
                            count = affectedItems.Length,
                            items = affectedItems.Take(20).ToArray(),
                            handle = createdEvidence.Guid
                        },
                        previewDigest = snapshot.PreviewDigest,
                        cleanupRequired = false,
                    });
            }
            catch (Exception exception)
            {
                if (!mutationStarted)
                {
                    return Failure(exception, false, false, failurePhase);
                }
                var restored = CleanupFailedApply(
                    snapshot,
                    createdEvidence,
                    createdFolders,
                    unverifiedFolderCreation && SceneObjectCopyCore.AssetOrMetaExists(attemptedFolder));
                return Failure(exception, true, !restored, failurePhase);
            }
        }

        internal static StableAssetEvidence ReadCreatedEvidenceWithRetry(string assetPath)
        {
            Exception lastError = null;
            for (var attempt = 1; attempt <= StableReadAttempts; attempt++)
            {
                try
                {
                    return SceneObjectCopyCore.ReadStableAssetEvidence(
                        assetPath,
                        "created project asset copy");
                }
                catch (Exception exception)
                {
                    lastError = exception;
                    if (attempt < StableReadAttempts)
                    {
                        Thread.Sleep(StableReadRetryDelayMilliseconds);
                    }
                }
            }
            throw new ProjectAssetCopyException(
                "The created project asset copy could not be read stably after "
                + StableReadAttempts.ToString(CultureInfo.InvariantCulture)
                + " attempts. Last reason: "
                + (lastError?.Message ?? "unknown"));
        }

        private static bool CleanupFailedApply(
            ProjectAssetCopySnapshot snapshot,
            StableAssetEvidence createdEvidence,
            List<CreatedFolder> createdFolders,
            bool unverifiedFolderCreation)
        {
            var assetClean = !SceneObjectCopyCore.AssetOrMetaExists(snapshot.DestinationPath);
            if (!assetClean && createdEvidence != null)
            {
                assetClean = SceneObjectCopyCore.DeleteOwnedAsset(snapshot.DestinationPath, createdEvidence);
            }
            if (!assetClean)
            {
                return false;
            }
            if (unverifiedFolderCreation) return false;
            for (var index = createdFolders.Count - 1; index >= 0; index--)
            {
                try { VerifyCreatedFolder(createdFolders[index]); }
                catch { return false; }
                if (!SceneObjectCopyCore.DeleteOwnedStagingFolder(createdFolders[index].Lease)) return false;
            }
            return true;
        }

        private sealed class CreatedFolder
        {
            internal StagingFolderLease Lease;
            internal string MetaDigest;
        }

        private static string ReadFolderMetaDigest(string folderPath)
        {
            var absolutePath = SceneObjectCopyCore.ToAbsoluteAssetPath(folderPath + ".meta");
            using (var stream = new FileStream(absolutePath, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var sha = SHA256.Create())
                return string.Concat(sha.ComputeHash(stream).Select(item => item.ToString("x2", CultureInfo.InvariantCulture)));
        }

        private static void VerifyCreatedFolder(CreatedFolder folder)
        {
            SceneObjectCopyCore.VerifyOwnedStagingFolder(folder.Lease);
            if (ReadFolderMetaDigest(folder.Lease.FolderPath) != folder.MetaDigest)
                throw new ProjectAssetCopyException("Created destination folder metadata changed; preserve it.");
        }

        private static void VerifyExpected(JObject parameters, ProjectAssetCopySnapshot snapshot)
        {
            if (!SceneObjectCopyCore.MatchesCurrentProject(Required(parameters, "expectedProjectPath"))
                || Required(parameters, "expectedSourceGuid") != snapshot.SourceEvidence.Guid
                || Required(parameters, "expectedSourceFileDigest") != snapshot.SourceEvidence.File.Digest
                || Required(parameters, "expectedSourceFileIdentity") != snapshot.SourceEvidence.File.Identity
                || Required(parameters, "expectedSourceMetaDigest") != snapshot.SourceEvidence.Meta.Digest
                || Required(parameters, "expectedSourceMetaIdentity") != snapshot.SourceEvidence.Meta.Identity
                || Required(parameters, "expectedSourceMainAssetType") != snapshot.SourceMainAssetType
                || Required(parameters, "expectedSourceObjectLayoutDigest") != snapshot.SourceObjectLayoutDigest
                || RequiredBool(parameters, "expectedGeneratedRootExists") != snapshot.GeneratedRootExists
                || Required(parameters, "expectedGeneratedRootGuid") != snapshot.GeneratedRootGuid
                || Required(parameters, "expectedGeneratedRootIdentity") != snapshot.GeneratedRootIdentity
                || Required(parameters, "expectedAnchorFolderGuid") != snapshot.AnchorFolderGuid
                || Required(parameters, "expectedAnchorFolderIdentity") != snapshot.AnchorFolderIdentity
                || Required(parameters, "expectedDestinationParentFolderGuid") != snapshot.ParentFolderGuid
                || Required(parameters, "expectedDestinationParentFolderIdentity") != snapshot.ParentFolderIdentity
                || !RequiredBool(parameters, "expectedDestinationAbsent")
                || Required(parameters, "expectedPreviewDigest") != snapshot.PreviewDigest)
            {
                throw new ProjectAssetCopyException("The project asset copy preview evidence changed before apply.");
            }
        }

        internal static void VerifySnapshotCurrent(ProjectAssetCopySnapshot snapshot)
        {
            var current = BuildSnapshot(snapshot.SourcePath, snapshot.DestinationPath);
            if (current.PreviewDigest != snapshot.PreviewDigest)
            {
                throw new ProjectAssetCopyException("The project asset copy state changed before mutation.");
            }
        }

        internal static void VerifySourceUnchanged(ProjectAssetCopySnapshot snapshot)
        {
            var current = SceneObjectCopyCore.ReadStableAssetEvidence(
                snapshot.SourcePath,
                "project asset copy source readback");
            if (!SceneObjectCopyCore.StableAssetEvidenceMatches(
                snapshot.SourceEvidence,
                current,
                true))
            {
                throw new ProjectAssetCopyException("The source asset changed during the copy.");
            }
        }

        internal static object SourcePayload(ProjectAssetCopySnapshot snapshot)
        {
            return new
            {
                assetPath = snapshot.SourcePath,
                guid = snapshot.SourceEvidence.Guid,
                fileDigest = snapshot.SourceEvidence.File.Digest,
                fileIdentity = snapshot.SourceEvidence.File.Identity,
                metaDigest = snapshot.SourceEvidence.Meta.Digest,
                metaIdentity = snapshot.SourceEvidence.Meta.Identity,
                mainAssetType = snapshot.SourceMainAssetType,
                objectLayoutDigest = snapshot.SourceObjectLayoutDigest,
                unchanged = true,
            };
        }

        internal static string NormalizeSourcePath(string value)
        {
            var path = NormalizeAssetPath(value, "sourceAssetPath");
            if (!path.StartsWith("Assets/", StringComparison.Ordinal))
            {
                throw new ProjectAssetCopyException("The source must be an existing Assets authoring asset.");
            }
            ValidateExtension(path);
            var legacyPrefix = LegacyGeneratedRoot + "/";
            var legacyGenerated = path.StartsWith(legacyPrefix, StringComparison.Ordinal);
            if (legacyGenerated)
            {
                if (!string.Equals(Path.GetExtension(path), ".mat", StringComparison.OrdinalIgnoreCase)
                    || path.Substring(legacyPrefix.Length).Contains("/"))
                    throw new ProjectAssetCopyException("Only an existing root-level legacy generated material may be copied.");
            }
            if (path.StartsWith(GeneratedRoot + "/", StringComparison.OrdinalIgnoreCase)
                && GeneratedSourceType(path) == null)
                throw new ProjectAssetCopyException("Only native material, animation, controller, and override-controller generated assets may be copied.");
            return path;
        }

        private static string GeneratedSourceType(string path)
        {
            switch (Path.GetExtension(path).ToLowerInvariant())
            {
                case ".mat": return "UnityEngine.Material";
                case ".anim": return "UnityEngine.AnimationClip";
                case ".controller": return "UnityEditor.Animations.AnimatorController";
                case ".overridecontroller": return "UnityEngine.AnimatorOverrideController";
                default: return null;
            }
        }

        private static void ValidateGeneratedSourceType(string path, string typeName)
        {
            if (path.StartsWith(GeneratedRoot + "/", StringComparison.OrdinalIgnoreCase)
                && !string.Equals(GeneratedSourceType(path), typeName, StringComparison.Ordinal))
                throw new ProjectAssetCopyException("Generated source asset type does not match its supported native extension.");
        }

        internal static string NormalizeDestinationPath(string value)
        {
            var path = NormalizeAssetPath(value, "destinationAssetPath");
            var expectedPrefix = GeneratedRoot + "/";
            if (!path.StartsWith(expectedPrefix, StringComparison.Ordinal)
                || path.Substring(expectedPrefix.Length).Split('/').Any(segment => segment.StartsWith(".", StringComparison.Ordinal)))
            {
                throw new ProjectAssetCopyException("The destination must be below Assets/VRCForgeGenerated.");
            }
            ValidateExtension(path);
            var fileName = Path.GetFileName(path);
            if (string.IsNullOrWhiteSpace(fileName) || fileName.StartsWith(".", StringComparison.Ordinal))
            {
                throw new ProjectAssetCopyException("The destination filename is reserved.");
            }
            return path;
        }

        private static string NormalizeAssetPath(string value, string label)
        {
            var path = (value ?? string.Empty).Replace('\\', '/');
            if (string.IsNullOrWhiteSpace(path)
                || path != path.Trim()
                || path.StartsWith("/", StringComparison.Ordinal)
                || path.EndsWith("/", StringComparison.Ordinal)
                || path.Contains("//")
                || path.Split('/').Any(segment => string.IsNullOrEmpty(segment)
                    || segment == "."
                    || segment == ".."
                    || segment.Any(char.IsControl)))
            {
                throw new ProjectAssetCopyException(label + " is not a canonical Unity asset path.");
            }
            return path;
        }

        private static void ValidateExtension(string path)
        {
            var extension = Path.GetExtension(path) ?? string.Empty;
            if (!AllowedExtensions.Contains(extension, StringComparer.OrdinalIgnoreCase))
            {
                throw new ProjectAssetCopyException("Only controller, asset, animation, override-controller, and material authoring assets can be copied.");
            }
        }

        private static string ComputePreviewDigest(ProjectAssetCopySnapshot snapshot)
        {
            var value = new StringBuilder();
            foreach (var field in new[]
            {
                PreviewDigestSchema,
                ResultSchema,
                Operation,
                snapshot.SourcePath,
                snapshot.SourceEvidence.Guid,
                snapshot.SourceEvidence.File.Digest,
                snapshot.SourceEvidence.File.Identity,
                snapshot.SourceEvidence.Meta.Digest,
                snapshot.SourceEvidence.Meta.Identity,
                snapshot.SourceMainAssetType,
                snapshot.SourceObjectLayoutDigest,
                snapshot.DestinationPath,
                GeneratedRoot,
                snapshot.GeneratedRootExists ? "true" : "false",
                snapshot.GeneratedRootGuid,
                snapshot.GeneratedRootIdentity,
                AnchorRoot,
                snapshot.AnchorFolderGuid,
                snapshot.AnchorFolderIdentity,
                snapshot.ParentFolderPath,
                snapshot.ParentFolderGuid,
                snapshot.ParentFolderIdentity,
                "destination_absent",
            })
            {
                var text = field ?? string.Empty;
                value.Append(text.Length.ToString(CultureInfo.InvariantCulture));
                value.Append(':');
                value.Append(text);
            }
            if (snapshot.HasClassifiedFolderCreation)
            {
                foreach (var field in new[] { "folder_creation", snapshot.ExistingAncestorPath, snapshot.ExistingAncestorGuid, snapshot.ExistingAncestorIdentity }.Concat(snapshot.MissingFolders))
                {
                    value.Append(field.Length.ToString(CultureInfo.InvariantCulture));
                    value.Append(':');
                    value.Append(field);
                }
            }
            using (var sha = SHA256.Create())
            {
                return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(value.ToString()))
                    .Select(item => item.ToString("x2", CultureInfo.InvariantCulture)));
            }
        }

        private static string Required(JObject parameters, string name)
        {
            var token = parameters[name];
            if (token == null || token.Type != JTokenType.String)
            {
                throw new ProjectAssetCopyException(name + " is required from preview.");
            }
            return token.Value<string>() ?? string.Empty;
        }

        private static bool RequiredBool(JObject parameters, string name)
        {
            var token = parameters[name];
            if (token == null || token.Type != JTokenType.Boolean)
            {
                throw new ProjectAssetCopyException(name + " is required from preview.");
            }
            return token.Value<bool>();
        }

        private static string NormalizeHex(string value, int length, string label)
        {
            var normalized = (value ?? string.Empty).Trim().ToLowerInvariant();
            if (normalized.Length != length || normalized.Any(character => !Uri.IsHexDigit(character)))
            {
                throw new ProjectAssetCopyException(label + " is invalid.");
            }
            return normalized;
        }

        internal static string ComputeObjectLayoutDigest(string assetPath)
        {
            var entries = AssetDatabase.LoadAllAssetsAtPath(assetPath)
                .Where(IsCopyLayoutObject)
                .Select(item => string.Join("\n", new[]
                {
                    item.GetType().AssemblyQualifiedName ?? item.GetType().FullName ?? item.GetType().Name,
                    AssetDatabase.IsMainAsset(item) ? "<main>" : (item.name ?? string.Empty),
                    ((int)item.hideFlags).ToString(CultureInfo.InvariantCulture),
                }))
                .OrderBy(item => item, StringComparer.Ordinal)
                .ToArray();
            if (entries.Length == 0)
            {
                throw new ProjectAssetCopyException("The Unity authoring asset has no loadable objects.");
            }
            var value = new StringBuilder();
            foreach (var entry in entries)
            {
                value.Append(entry.Length.ToString(CultureInfo.InvariantCulture));
                value.Append(':');
                value.Append(entry);
            }
            using (var sha = SHA256.Create())
            {
                return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(value.ToString()))
                    .Select(item => item.ToString("x2", CultureInfo.InvariantCulture)));
            }
        }

        internal static bool IsCopyLayoutObject(UnityEngine.Object item)
        {
            // Unity ImportLog is the sealed container for importer-generated diagnostics,
            // not an authored subasset. Keep every other type, including hidden editor objects.
            // https://docs.unity3d.com/2022.3/Documentation/ScriptReference/AssetImporters.ImportLog.html
            return item != null && !(item is UnityEditor.AssetImporters.ImportLog);
        }

        private static object Failure(
            Exception exception,
            bool mutationStarted,
            bool cleanupRequired,
            string failurePhase)
        {
            return VRCForgeToolResult.FailedWithCode(
                cleanupRequired ? "asset_copy_cleanup_unverified" : "asset_copy_failed",
                exception.Message,
                new
                {
                    schema = ResultSchema,
                    ok = false,
                    operation = Operation,
                    failureLayer = mutationStarted ? "unity_mutation" : "unity_validation",
                    failurePhase,
                    mutationStarted,
                    writeOccurred = mutationStarted,
                    committed = false,
                    commitState = cleanupRequired ? "unknown" : "not_committed",
                    requestMayHaveCommitted = cleanupRequired,
                    cleanupRequired,
                    checkpointRecoveryRequired = cleanupRequired,
                });
        }

        internal sealed class ProjectAssetCopySnapshot
        {
            internal string SourcePath = string.Empty;
            internal string DestinationPath = string.Empty;
            internal StableAssetEvidence SourceEvidence;
            internal string SourceMainAssetType = string.Empty;
            internal string SourceObjectLayoutDigest = string.Empty;
            internal bool GeneratedRootExists;
            internal string GeneratedRootGuid = string.Empty;
            internal string GeneratedRootIdentity = string.Empty;
            internal string AnchorFolderGuid = string.Empty;
            internal string AnchorFolderIdentity = string.Empty;
            internal string ParentFolderPath = string.Empty;
            internal string ParentFolderGuid = string.Empty;
            internal string ParentFolderIdentity = string.Empty;
            internal string[] MissingFolders = Array.Empty<string>();
            internal string ExistingAncestorPath = string.Empty;
            internal string ExistingAncestorGuid = string.Empty;
            internal string ExistingAncestorIdentity = string.Empty;
            internal bool HasClassifiedFolderCreation => MissingFolders.Length > 0 && ParentFolderPath != GeneratedRoot;
            internal object FolderCreationPayload => HasClassifiedFolderCreation ? new {
                ancestorPath = ExistingAncestorPath, ancestorGuid = ExistingAncestorGuid,
                ancestorIdentity = ExistingAncestorIdentity, paths = MissingFolders
            } : (object)null;
            internal string PreviewDigest = string.Empty;

            internal object ToPreviewPayload()
            {
                return new
                {
                    schema = ResultSchema,
                    ok = true,
                    operation = Operation,
                    preview = true,
                    verified = true,
                    changed = false,
                    saved = false,
                    mutationCount = 0,
                    source = SourcePayload(this),
                    target = new
                    {
                        assetPath = DestinationPath,
                        generatedRootPath = GeneratedRoot,
                        generatedRootExists = GeneratedRootExists,
                        generatedRootGuid = GeneratedRootGuid,
                        generatedRootIdentity = GeneratedRootIdentity,
                        anchorFolderPath = AnchorRoot,
                        anchorFolderGuid = AnchorFolderGuid,
                        anchorFolderIdentity = AnchorFolderIdentity,
                        parentFolderPath = ParentFolderPath,
                        parentFolderGuid = ParentFolderGuid,
                        parentFolderIdentity = ParentFolderIdentity,
                        folderCreation = FolderCreationPayload,
                        assetExists = false,
                        metaExists = false,
                        createNew = true,
                    },
                    previewDigest = PreviewDigest,
                    cleanupRequired = false,
                };
            }
        }

        private sealed class ProjectAssetCopyException : InvalidOperationException
        {
            internal ProjectAssetCopyException(string message) : base(message) { }
        }
    }
}
