using System;
using System.Globalization;
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
        Summary = "Create-new copy one supported Unity authoring asset into Assets/VRCForge/Generated without overwriting the source or destination. Supports preview."
    )]
    public static class DuplicateProjectAssetTool
    {
        internal const string ToolName = "vrc_duplicate_project_asset";
        internal const string ResultSchema = "vrcforge.project_asset_copy.v2";
        internal const string Operation = "duplicate_project_asset";
        internal const string GeneratedRoot = "Assets/VRCForge/Generated";
        private const string AnchorRoot = "Assets/VRCForge";
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
            [VRCForgeInput("Existing source asset path below Assets.", IsRequired = true)] public string sourceAssetPath { get; set; } = "";
            [VRCForgeInput("Create-new destination directly below Assets/VRCForge/Generated.", IsRequired = true)] public string destinationAssetPath { get; set; } = "";
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
            [VRCForgeInput("Expected destination-absent assertion from preview.", IsRequired = false)] public bool? expectedDestinationAbsent { get; set; }
            [VRCForgeInput("Expected preview digest.", IsRequired = false)] public string expectedPreviewDigest { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
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

        private static ProjectAssetCopySnapshot BuildSnapshot(string rawSourcePath, string rawDestinationPath)
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
            };
            snapshot.PreviewDigest = ComputePreviewDigest(snapshot);
            return snapshot;
        }

        private static object Apply(ProjectAssetCopySnapshot snapshot)
        {
            var mutationStarted = false;
            var generatedRootCreated = false;
            StableAssetEvidence createdEvidence = null;
            StagingFolderLease generatedRootLease = null;
            var failurePhase = "preflight";
            try
            {
                VerifySnapshotCurrent(snapshot);
                if (!snapshot.GeneratedRootExists)
                {
                    failurePhase = "generated_root_creation";
                    var createdGuid = AssetDatabase.CreateFolder(AnchorRoot, "Generated");
                    mutationStarted = true;
                    generatedRootCreated = true;
                    var normalizedGuid = NormalizeHex(createdGuid, 32, "created generated-root GUID");
                    var actualPath = (AssetDatabase.GUIDToAssetPath(normalizedGuid) ?? string.Empty).Replace('\\', '/');
                    if (!string.Equals(actualPath, GeneratedRoot, StringComparison.Ordinal))
                    {
                        throw new ProjectAssetCopyException("The generated asset root was not created exactly.");
                    }
                    generatedRootLease = new StagingFolderLease
                    {
                        RootPath = AnchorRoot,
                        FolderPath = GeneratedRoot,
                        FolderGuid = normalizedGuid,
                        FolderIdentity = SceneObjectCopyCore.ReadDirectoryIdentity(GeneratedRoot, "created generated asset root"),
                    };
                }

                if (SceneObjectCopyCore.AssetOrMetaExists(snapshot.DestinationPath))
                {
                    throw new ProjectAssetCopyException("The destination changed before the copy started.");
                }
                failurePhase = "asset_copy";
                if (!AssetDatabase.CopyAsset(snapshot.SourcePath, snapshot.DestinationPath))
                {
                    throw new ProjectAssetCopyException("Unity AssetDatabase refused the create-new asset copy.");
                }
                mutationStarted = true;
                AssetDatabase.SaveAssets();
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
                    createNew = true,
                    readbackVerified = true,
                };
                var affectedItems = generatedRootCreated
                    ? new[] { snapshot.DestinationPath, GeneratedRoot }
                    : new[] { snapshot.DestinationPath };

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
                        mutationCount = generatedRootCreated ? 2 : 1,
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
                    generatedRootCreated,
                    generatedRootLease);
                return Failure(exception, true, !restored, failurePhase);
            }
        }

        private static StableAssetEvidence ReadCreatedEvidenceWithRetry(string assetPath)
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
            bool generatedRootCreated,
            StagingFolderLease generatedRootLease)
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
            if (generatedRootCreated)
            {
                return SceneObjectCopyCore.DeleteOwnedStagingFolder(generatedRootLease);
            }
            return true;
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
                || !RequiredBool(parameters, "expectedDestinationAbsent")
                || Required(parameters, "expectedPreviewDigest") != snapshot.PreviewDigest)
            {
                throw new ProjectAssetCopyException("The project asset copy preview evidence changed before apply.");
            }
        }

        private static void VerifySnapshotCurrent(ProjectAssetCopySnapshot snapshot)
        {
            var current = BuildSnapshot(snapshot.SourcePath, snapshot.DestinationPath);
            if (current.PreviewDigest != snapshot.PreviewDigest)
            {
                throw new ProjectAssetCopyException("The project asset copy state changed before mutation.");
            }
        }

        private static void VerifySourceUnchanged(ProjectAssetCopySnapshot snapshot)
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

        private static object SourcePayload(ProjectAssetCopySnapshot snapshot)
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

        private static string NormalizeSourcePath(string value)
        {
            var path = NormalizeAssetPath(value, "sourceAssetPath");
            if (!path.StartsWith("Assets/", StringComparison.Ordinal))
            {
                throw new ProjectAssetCopyException("The source must be an existing non-generated Assets authoring asset.");
            }
            ValidateExtension(path);
            var generatedPrefix = GeneratedRoot + "/";
            if (path.StartsWith(generatedPrefix, StringComparison.Ordinal)
                && (!string.Equals(Path.GetExtension(path), ".mat", StringComparison.OrdinalIgnoreCase)
                    || path.Substring(generatedPrefix.Length).Contains("/")))
            {
                throw new ProjectAssetCopyException("Only an existing generated material may be copied from the generated root.");
            }
            return path;
        }

        private static string NormalizeDestinationPath(string value)
        {
            var path = NormalizeAssetPath(value, "destinationAssetPath");
            var expectedPrefix = GeneratedRoot + "/";
            if (!path.StartsWith(expectedPrefix, StringComparison.Ordinal)
                || path.Substring(expectedPrefix.Length).Contains("/"))
            {
                throw new ProjectAssetCopyException("The destination must be a direct child of Assets/VRCForge/Generated.");
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
                "destination_absent",
            })
            {
                var text = field ?? string.Empty;
                value.Append(text.Length.ToString(CultureInfo.InvariantCulture));
                value.Append(':');
                value.Append(text);
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

        private static string ComputeObjectLayoutDigest(string assetPath)
        {
            var entries = AssetDatabase.LoadAllAssetsAtPath(assetPath)
                .Where(item => item != null)
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

        private sealed class ProjectAssetCopySnapshot
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
