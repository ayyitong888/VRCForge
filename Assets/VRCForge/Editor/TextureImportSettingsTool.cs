using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32.SafeHandles;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    [McpForUnityTool(
        name: "vrc_set_texture_import_settings",
        Description = "Preview or update one persistent project texture importer through the supervised project-write lane."
    )]
    public static class TextureImportSettingsTool
    {
        private const string ResultSchema = "vrcforge.texture_import_settings.v1";
        private const string SettingsDigestSchema = "vrcforge.texture_import_settings_digest.v1";
        private const string FileIdentityDigestSchema = "vrcforge.texture_file_identity.v1";
        private const uint NativeFileShareRead = 0x00000001;
        private const uint NativeFileShareWrite = 0x00000002;
        private const uint NativeFileShareDelete = 0x00000004;
        private const uint NativeOpenExisting = 3;
        private const uint NativeFileAttributeNormal = 0x00000080;

        private static readonly HashSet<int> AllowedMaxTextureSizes = new HashSet<int>
        {
            32,
            64,
            128,
            256,
            512,
            1024,
            2048,
            4096,
            8192
        };

        private static readonly Dictionary<string, TextureImporterCompression> CompressionValues =
            new Dictionary<string, TextureImporterCompression>(StringComparer.Ordinal)
            {
                { "uncompressed", TextureImporterCompression.Uncompressed },
                { "normal", TextureImporterCompression.Compressed },
                { "high", TextureImporterCompression.CompressedHQ },
                { "low", TextureImporterCompression.CompressedLQ }
            };

        private static readonly Dictionary<string, TextureImporterFormat> FormatValues =
            new Dictionary<string, TextureImporterFormat>(StringComparer.Ordinal)
            {
                { "automatic", TextureImporterFormat.Automatic },
                { "rgb24", TextureImporterFormat.RGB24 },
                { "rgba32", TextureImporterFormat.RGBA32 },
                { "dxt1", TextureImporterFormat.DXT1 },
                { "dxt5", TextureImporterFormat.DXT5 },
                { "dxt1_crunched", TextureImporterFormat.DXT1Crunched },
                { "dxt5_crunched", TextureImporterFormat.DXT5Crunched },
                { "bc7", TextureImporterFormat.BC7 },
                { "etc_rgb4", TextureImporterFormat.ETC_RGB4 },
                { "etc2_rgb4", TextureImporterFormat.ETC2_RGB4 },
                { "etc2_rgba8", TextureImporterFormat.ETC2_RGBA8 },
                { "etc_rgb4_crunched", TextureImporterFormat.ETC_RGB4Crunched },
                { "etc2_rgba8_crunched", TextureImporterFormat.ETC2_RGBA8Crunched },
                { "astc_4x4", TextureImporterFormat.ASTC_4x4 },
                { "astc_6x6", TextureImporterFormat.ASTC_6x6 },
                { "astc_8x8", TextureImporterFormat.ASTC_8x8 },
                { "pvrtc_rgb4", TextureImporterFormat.PVRTC_RGB4 },
                { "pvrtc_rgba4", TextureImporterFormat.PVRTC_RGBA4 }
            };

        private static readonly Dictionary<string, HashSet<string>> FormatsByPlatform =
            new Dictionary<string, HashSet<string>>(StringComparer.Ordinal)
            {
                {
                    "default",
                    new HashSet<string>(new[] { "automatic", "rgb24", "rgba32" }, StringComparer.Ordinal)
                },
                {
                    "standalone",
                    new HashSet<string>(
                        new[]
                        {
                            "automatic",
                            "rgb24",
                            "rgba32",
                            "dxt1",
                            "dxt5",
                            "dxt1_crunched",
                            "dxt5_crunched",
                            "bc7"
                        },
                        StringComparer.Ordinal
                    )
                },
                {
                    "android",
                    new HashSet<string>(
                        new[]
                        {
                            "automatic",
                            "rgb24",
                            "rgba32",
                            "etc_rgb4",
                            "etc2_rgb4",
                            "etc2_rgba8",
                            "etc_rgb4_crunched",
                            "etc2_rgba8_crunched",
                            "astc_4x4",
                            "astc_6x6",
                            "astc_8x8"
                        },
                        StringComparer.Ordinal
                    )
                },
                {
                    "ios",
                    new HashSet<string>(
                        new[]
                        {
                            "automatic",
                            "rgb24",
                            "rgba32",
                            "pvrtc_rgb4",
                            "pvrtc_rgba4",
                            "astc_4x4",
                            "astc_6x6",
                            "astc_8x8"
                        },
                        StringComparer.Ordinal
                    )
                }
            };

        private static readonly HashSet<string> CrunchedFormats = new HashSet<string>(
            new[] { "dxt1_crunched", "dxt5_crunched", "etc_rgb4_crunched", "etc2_rgba8_crunched" },
            StringComparer.Ordinal
        );

        private static readonly Dictionary<string, HashSet<string>> CrunchFormatsByPlatform =
            new Dictionary<string, HashSet<string>>(StringComparer.Ordinal)
            {
                { "default", new HashSet<string>(new[] { "automatic" }, StringComparer.Ordinal) },
                {
                    "standalone",
                    new HashSet<string>(new[] { "automatic", "dxt1_crunched", "dxt5_crunched" }, StringComparer.Ordinal)
                },
                {
                    "android",
                    new HashSet<string>(
                        new[] { "automatic", "etc_rgb4_crunched", "etc2_rgba8_crunched" },
                        StringComparer.Ordinal
                    )
                },
                { "ios", new HashSet<string>(StringComparer.Ordinal) }
            };

        private static readonly HashSet<string> UncompressedFormats = new HashSet<string>(
            new[] { "rgb24", "rgba32" },
            StringComparer.Ordinal
        );

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var textureAssetPath = NormalizeAssetPath(ReadRequiredString(@params, "textureAssetPath"));
                var platform = ReadCanonicalChoice(@params, "platform", FormatsByPlatform.Keys);
                var maxTextureSize = ReadStrictInt(@params, "maxTextureSize");
                var formatName = ReadCanonicalChoice(@params, "format", FormatsByPlatform[platform]);
                var compressionName = ReadCanonicalChoice(@params, "compression", CompressionValues.Keys);
                var crunch = ReadStrictBool(@params, "crunch");
                var quality = ReadStrictInt(@params, "quality");
                var preview = ReadOptionalBool(@params, "preview", false);
                var saveAndReimport = ReadOptionalBool(@params, "saveAndReimport", false);
                var expectedProjectPath = (@params?["expectedProjectPath"]?.ToString() ?? string.Empty).Trim();

                ValidateRequestedSettings(
                    platform,
                    maxTextureSize,
                    formatName,
                    compressionName,
                    crunch,
                    quality
                );
                if (!MatchesCurrentProject(expectedProjectPath))
                {
                    return new ErrorResponse("The selected Unity project does not match the active editor instance.");
                }
                if (!preview && !saveAndReimport)
                {
                    return new ErrorResponse("saveAndReimport must be true for apply.");
                }
                if (!preview && !HasApplyPreconditions(@params))
                {
                    return new ErrorResponse("Verified texture importer preconditions are required for apply.");
                }

                var platformSpec = PlatformSpec.Create(platform);
                var format = FormatValues[formatName];
                var compression = CompressionValues[compressionName];
                var evidence = InspectTextureAsset(textureAssetPath);
                ValidateFormatForImporter(evidence.importer, platformSpec, format);

                var beforeSettings = ReadSettings(evidence.importer, platformSpec);
                var importerType = CanonicalImporterType(evidence.importer.textureType);
                var beforeSettingsDigest = ComputeSettingsDigest(importerType, beforeSettings);
                var targetSettings = BuildTargetSettings(
                    platformSpec,
                    maxTextureSize,
                    formatName,
                    compressionName,
                    crunch,
                    quality
                );
                var targetSettingsDigest = ComputeSettingsDigest(importerType, targetSettings);
                var wouldChange = !SettingsEqual(beforeSettings, targetSettings);

                if (!preview)
                {
                    ValidateApplyPreconditions(
                        @params,
                        expectedProjectPath,
                        evidence,
                        importerType,
                        beforeSettingsDigest,
                        targetSettingsDigest
                    );
                }

                var sourceFileDigestAfter = ComputeFileSha256(evidence.sourceFilePath);
                var metaFileDigestAfter = ComputeFileSha256(evidence.metaFilePath);
                var importerSettingsDigestAfter = ComputeSettingsDigest(
                    importerType,
                    ReadSettings(evidence.importer, platformSpec)
                );
                var importerDirtyAfter = EditorUtility.IsDirty(evidence.importer);

                if (preview)
                {
                    if (sourceFileDigestAfter != evidence.sourceFileDigest
                        || metaFileDigestAfter != evidence.metaFileDigest
                        || importerSettingsDigestAfter != beforeSettingsDigest
                        || importerDirtyAfter)
                    {
                        throw new InvalidOperationException("Preview changed texture importer state.");
                    }
                    return BuildSuccessResponse(
                        preview: true,
                        changed: false,
                        wouldChange: wouldChange,
                        saved: false,
                        reimported: false,
                        evidence: evidence,
                        importerType: importerType,
                        beforeSettings: beforeSettings,
                        targetSettings: targetSettings,
                        beforeSettingsDigest: beforeSettingsDigest,
                        importerSettingsDigestAfter: importerSettingsDigestAfter,
                        targetSettingsDigest: targetSettingsDigest,
                        sourceFileDigestAfter: sourceFileDigestAfter,
                        metaFileDigestAfter: metaFileDigestAfter,
                        importerDirtyAfter: importerDirtyAfter
                    );
                }

                if (!wouldChange)
                {
                    var unchangedEvidence = InspectTextureAsset(textureAssetPath);
                    var unchangedSettings = ReadSettings(unchangedEvidence.importer, platformSpec);
                    var unchangedSettingsDigest = ComputeSettingsDigest(importerType, unchangedSettings);
                    if (unchangedEvidence.assetGuid != evidence.assetGuid
                        || unchangedEvidence.sourceFileDigest != evidence.sourceFileDigest
                        || unchangedEvidence.metaFileDigest != evidence.metaFileDigest
                        || unchangedSettingsDigest != beforeSettingsDigest
                        || EditorUtility.IsDirty(unchangedEvidence.importer))
                    {
                        throw new InvalidOperationException("Texture importer changed during no-op verification.");
                    }
                    return BuildSuccessResponse(
                        preview: false,
                        changed: false,
                        wouldChange: false,
                        saved: false,
                        reimported: false,
                        evidence: evidence,
                        importerType: importerType,
                        beforeSettings: beforeSettings,
                        targetSettings: targetSettings,
                        beforeSettingsDigest: beforeSettingsDigest,
                        importerSettingsDigestAfter: unchangedSettingsDigest,
                        targetSettingsDigest: targetSettingsDigest,
                        sourceFileDigestAfter: unchangedEvidence.sourceFileDigest,
                        metaFileDigestAfter: unchangedEvidence.metaFileDigest,
                        importerDirtyAfter: false
                    );
                }

                var beforeNativeSettings = platformSpec.isDefault
                    ? evidence.importer.GetDefaultPlatformTextureSettings()
                    : evidence.importer.GetPlatformTextureSettings(platformSpec.platformName);
                using (
                    var sourceLease = HoldStableFile(
                        evidence.sourceFilePath,
                        evidence.sourceFileIdentity,
                        evidence.sourceFileDigest,
                        allowReplacement: false
                    )
                )
                using (
                    var metaLease = HoldStableFile(
                        evidence.metaFilePath,
                        evidence.metaFileIdentity,
                        evidence.metaFileDigest,
                        allowReplacement: true
                    )
                )
                {
                    VerifyPathMatchesLease(
                        evidence.sourceFilePath,
                        sourceLease,
                        evidence.sourceFileDigest
                    );
                    VerifyPathMatchesLease(
                        evidence.metaFilePath,
                        metaLease,
                        evidence.metaFileDigest
                    );
                    if (ComputeSettingsDigest(importerType, ReadSettings(evidence.importer, platformSpec)) != beforeSettingsDigest)
                    {
                        return new ErrorResponse("Texture importer state changed after the verified preview.");
                    }

                    var mutationStarted = false;
                    try
                    {
                        var nativeSettings = platformSpec.isDefault
                            ? evidence.importer.GetDefaultPlatformTextureSettings()
                            : evidence.importer.GetPlatformTextureSettings(platformSpec.platformName);
                        nativeSettings.name = platformSpec.platformName;
                        nativeSettings.overridden = !platformSpec.isDefault;
                        nativeSettings.ignorePlatformSupport = false;
                        nativeSettings.maxTextureSize = maxTextureSize;
                        nativeSettings.format = format;
                        nativeSettings.textureCompression = compression;
                        nativeSettings.crunchedCompression = crunch;
                        nativeSettings.compressionQuality = quality;
                        mutationStarted = true;
                        evidence.importer.SetPlatformTextureSettings(nativeSettings);
                        evidence.importer.SaveAndReimport();

                        var readbackEvidence = InspectTextureAsset(textureAssetPath);
                        var readbackImporterType = CanonicalImporterType(readbackEvidence.importer.textureType);
                        var readbackSettings = ReadSettings(readbackEvidence.importer, platformSpec);
                        var readbackSettingsDigest = ComputeSettingsDigest(readbackImporterType, readbackSettings);
                        if (readbackEvidence.assetPath != evidence.assetPath
                            || readbackEvidence.assetGuid != evidence.assetGuid
                            || readbackEvidence.sourceFileDigest != evidence.sourceFileDigest
                            || !FileIdentityEquals(readbackEvidence.sourceFileIdentity, sourceLease.identity)
                            || readbackEvidence.metaFileDigest == evidence.metaFileDigest
                            || readbackImporterType != importerType
                            || readbackSettingsDigest != targetSettingsDigest
                            || !SettingsEqual(readbackSettings, targetSettings)
                            || EditorUtility.IsDirty(readbackEvidence.importer))
                        {
                            throw new InvalidOperationException("Texture importer readback did not match the approved settings.");
                        }

                        return BuildSuccessResponse(
                            preview: false,
                            changed: true,
                            wouldChange: true,
                            saved: true,
                            reimported: true,
                            evidence: evidence,
                            importerType: importerType,
                            beforeSettings: beforeSettings,
                            targetSettings: targetSettings,
                            beforeSettingsDigest: beforeSettingsDigest,
                            importerSettingsDigestAfter: readbackSettingsDigest,
                            targetSettingsDigest: targetSettingsDigest,
                            sourceFileDigestAfter: readbackEvidence.sourceFileDigest,
                            metaFileDigestAfter: readbackEvidence.metaFileDigest,
                            importerDirtyAfter: false
                        );
                    }
                    catch (Exception) when (mutationStarted)
                    {
                        var restored = TryRestoreBeforeSettings(
                            textureAssetPath,
                            platformSpec,
                            beforeNativeSettings,
                            evidence,
                            importerType,
                            beforeSettings,
                            beforeSettingsDigest,
                            sourceLease,
                            metaLease
                        );
                        return BuildMutationFailure(restored);
                    }
                }
            }
            catch (Exception)
            {
                return new ErrorResponse("Texture importer settings operation failed.");
            }
        }

        private static object BuildSuccessResponse(
            bool preview,
            bool changed,
            bool wouldChange,
            bool saved,
            bool reimported,
            TextureAssetEvidence evidence,
            string importerType,
            ImportSettingsEvidence beforeSettings,
            ImportSettingsEvidence targetSettings,
            string beforeSettingsDigest,
            string importerSettingsDigestAfter,
            string targetSettingsDigest,
            string sourceFileDigestAfter,
            string metaFileDigestAfter,
            bool importerDirtyAfter
        )
        {
            return new SuccessResponse(
                preview ? "Texture importer preview completed." : "Texture importer settings verified.",
                new
                {
                    schema = ResultSchema,
                    ok = true,
                    preview,
                    verified = true,
                    changed,
                    wouldChange,
                    saved,
                    reimported,
                    projectPath = CurrentProjectPath(),
                    textureAssetPath = evidence.assetPath,
                    textureAssetGuid = evidence.assetGuid,
                    sourceFileDigestBefore = evidence.sourceFileDigest,
                    sourceFileDigestAfter,
                    sourceFileIdentityDigest = evidence.sourceFileIdentityDigest,
                    sourceFileLinkCount = evidence.sourceFileIdentity.numberOfLinks,
                    metaFileDigestBefore = evidence.metaFileDigest,
                    metaFileDigestAfter,
                    metaFileIdentityDigest = evidence.metaFileIdentityDigest,
                    metaFileLinkCount = evidence.metaFileIdentity.numberOfLinks,
                    importerType,
                    beforeSettings,
                    targetSettings,
                    importerSettingsDigestBefore = beforeSettingsDigest,
                    importerSettingsDigestAfter,
                    targetSettingsDigest,
                    importerDirtyBefore = false,
                    importerDirtyAfter
                }
            );
        }

        private static object BuildMutationFailure(bool restored)
        {
            var message = restored
                ? "Texture importer settings operation failed after restoring the verified pre-state."
                : "Texture importer settings operation failed; checkpoint restore is required.";
            return new ErrorResponse(
                message,
                new
                {
                    mutationStarted = true,
                    restored,
                    cleanupRequired = !restored,
                    checkpointRestoreRequired = !restored,
                    operationState = restored ? "restored" : "checkpoint_restore_required"
                }
            );
        }

        private static bool TryRestoreBeforeSettings(
            string textureAssetPath,
            PlatformSpec platform,
            TextureImporterPlatformSettings beforeNativeSettings,
            TextureAssetEvidence beforeEvidence,
            string importerType,
            ImportSettingsEvidence beforeSettings,
            string beforeSettingsDigest,
            StableFileLease sourceLease,
            StableFileLease metaLease
        )
        {
            try
            {
                var importer = AssetImporter.GetAtPath(textureAssetPath) as TextureImporter;
                if (importer == null
                    || beforeNativeSettings == null
                    || !LeaseFileObjectMatches(sourceLease)
                    || !LeaseFileObjectMatches(metaLease))
                {
                    return false;
                }
                if (!platform.isDefault && !beforeNativeSettings.overridden)
                {
                    importer.ClearPlatformTextureSettings(platform.platformName);
                }
                else
                {
                    importer.SetPlatformTextureSettings(beforeNativeSettings);
                }
                importer.SaveAndReimport();

                var restoredEvidence = InspectTextureAsset(textureAssetPath);
                var restoredImporterType = CanonicalImporterType(restoredEvidence.importer.textureType);
                var restoredSettings = ReadSettings(restoredEvidence.importer, platform);
                var restoredSettingsDigest = ComputeSettingsDigest(restoredImporterType, restoredSettings);
                return restoredEvidence.assetPath == beforeEvidence.assetPath
                    && restoredEvidence.assetGuid == beforeEvidence.assetGuid
                    && restoredEvidence.sourceFileDigest == beforeEvidence.sourceFileDigest
                    && restoredEvidence.metaFileDigest == beforeEvidence.metaFileDigest
                    && FileIdentityEquals(restoredEvidence.sourceFileIdentity, sourceLease.identity)
                    && restoredImporterType == importerType
                    && restoredSettingsDigest == beforeSettingsDigest
                    && SettingsEqual(restoredSettings, beforeSettings)
                    && !EditorUtility.IsDirty(restoredEvidence.importer);
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static void ValidateRequestedSettings(
            string platform,
            int maxTextureSize,
            string format,
            string compression,
            bool crunch,
            int quality
        )
        {
            if (!AllowedMaxTextureSizes.Contains(maxTextureSize))
            {
                throw new InvalidOperationException("maxTextureSize is not supported.");
            }
            if (quality < 0 || quality > 100)
            {
                throw new InvalidOperationException("quality is out of range.");
            }
            if (crunch && !CrunchFormatsByPlatform[platform].Contains(format))
            {
                throw new InvalidOperationException("Crunch is incompatible with the requested platform or format.");
            }
            if (!crunch && CrunchedFormats.Contains(format))
            {
                throw new InvalidOperationException("A crunched format requires crunch.");
            }
            if (compression == "uncompressed"
                && (crunch || (!UncompressedFormats.Contains(format) && format != "automatic")))
            {
                throw new InvalidOperationException("Uncompressed mode is incompatible with the requested format.");
            }
            if (UncompressedFormats.Contains(format) && compression != "uncompressed")
            {
                throw new InvalidOperationException("An uncompressed format requires uncompressed mode.");
            }
            if (!UncompressedFormats.Contains(format) && format != "automatic" && compression == "uncompressed")
            {
                throw new InvalidOperationException("A compressed format cannot use uncompressed mode.");
            }
        }

        private static void ValidateFormatForImporter(
            TextureImporter importer,
            PlatformSpec platform,
            TextureImporterFormat format
        )
        {
            var valid = platform.isDefault
                ? TextureImporter.IsDefaultPlatformTextureFormatValid(importer.textureType, format)
                : TextureImporter.IsPlatformTextureFormatValid(importer.textureType, platform.buildTarget, format);
            if (!valid)
            {
                throw new InvalidOperationException("The requested texture format is invalid for this importer and platform.");
            }
        }

        private static bool HasApplyPreconditions(JObject @params)
        {
            var required = new[]
            {
                "expectedProjectPath",
                "expectedTextureAssetPath",
                "expectedTextureAssetGuid",
                "expectedSourceFileDigest",
                "expectedSourceFileIdentityDigest",
                "expectedMetaFileDigest",
                "expectedMetaFileIdentityDigest",
                "expectedImporterType",
                "expectedImporterSettingsDigest",
                "expectedTargetSettingsDigest"
            };
            return required.All(key => @params?[key]?.Type == JTokenType.String && !string.IsNullOrWhiteSpace(@params[key].ToString()));
        }

        private static void ValidateApplyPreconditions(
            JObject @params,
            string expectedProjectPath,
            TextureAssetEvidence evidence,
            string importerType,
            string beforeSettingsDigest,
            string targetSettingsDigest
        )
        {
            var expectedTextureAssetPath = NormalizeAssetPath(ReadRequiredString(@params, "expectedTextureAssetPath"));
            var expectedTextureAssetGuid = NormalizeHex(ReadRequiredString(@params, "expectedTextureAssetGuid"), 32);
            var expectedSourceFileDigest = NormalizeHex(ReadRequiredString(@params, "expectedSourceFileDigest"), 64);
            var expectedSourceFileIdentityDigest = NormalizeHex(
                ReadRequiredString(@params, "expectedSourceFileIdentityDigest"),
                64
            );
            var expectedMetaFileDigest = NormalizeHex(ReadRequiredString(@params, "expectedMetaFileDigest"), 64);
            var expectedMetaFileIdentityDigest = NormalizeHex(
                ReadRequiredString(@params, "expectedMetaFileIdentityDigest"),
                64
            );
            var expectedImporterType = ReadRequiredString(@params, "expectedImporterType");
            var expectedImporterSettingsDigest = NormalizeHex(
                ReadRequiredString(@params, "expectedImporterSettingsDigest"),
                64
            );
            var expectedTargetSettingsDigest = NormalizeHex(
                ReadRequiredString(@params, "expectedTargetSettingsDigest"),
                64
            );

            if (!MatchesCurrentProject(expectedProjectPath)
                || expectedTextureAssetPath != evidence.assetPath
                || expectedTextureAssetGuid != evidence.assetGuid
                || expectedSourceFileDigest != evidence.sourceFileDigest
                || expectedSourceFileIdentityDigest != evidence.sourceFileIdentityDigest
                || expectedMetaFileDigest != evidence.metaFileDigest
                || expectedMetaFileIdentityDigest != evidence.metaFileIdentityDigest
                || expectedImporterType != importerType
                || expectedImporterSettingsDigest != beforeSettingsDigest
                || expectedTargetSettingsDigest != targetSettingsDigest)
            {
                throw new InvalidOperationException("Texture importer state no longer matches the verified preview.");
            }
        }

        private static TextureAssetEvidence InspectTextureAsset(string textureAssetPath)
        {
            var importer = TextureImporter.GetAtPath(textureAssetPath) as TextureImporter;
            var asset = AssetDatabase.LoadMainAssetAtPath(textureAssetPath) as Texture;
            if (importer == null || asset == null || !AssetDatabase.Contains(asset) || !AssetDatabase.IsMainAsset(asset))
            {
                throw new InvalidOperationException("Texture target must be a persistent imported texture asset.");
            }
            var resolvedAssetPath = NormalizeAssetPath(AssetDatabase.GetAssetPath(asset));
            if (resolvedAssetPath != textureAssetPath || EditorUtility.IsDirty(importer))
            {
                throw new InvalidOperationException("Texture importer identity is unstable or dirty.");
            }
            if (!AssetDatabase.IsOpenForEdit(importer, StatusQueryOptions.UseCachedIfPossible))
            {
                throw new InvalidOperationException("Texture importer is not writable.");
            }

            var projectRoot = CurrentProjectPath();
            var assetsRoot = Path.GetFullPath(Application.dataPath);
            var sourceFilePath = Path.GetFullPath(
                Path.Combine(projectRoot, resolvedAssetPath.Replace('/', Path.DirectorySeparatorChar))
            );
            var assetsPrefix = assetsRoot.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                + Path.DirectorySeparatorChar;
            if (!sourceFilePath.StartsWith(assetsPrefix, PathComparison()))
            {
                throw new InvalidOperationException("Texture source resolved outside the project Assets directory.");
            }
            var metaFilePath = sourceFilePath + ".meta";
            EnsureNoReparseBoundary(assetsRoot, sourceFilePath);
            EnsureNoReparseBoundary(assetsRoot, metaFilePath);
            if (!File.Exists(sourceFilePath) || !File.Exists(metaFilePath))
            {
                throw new InvalidOperationException("Texture source or metadata file is missing.");
            }
            var sourceAttributes = File.GetAttributes(sourceFilePath);
            var metaAttributes = File.GetAttributes(metaFilePath);
            if ((sourceAttributes & FileAttributes.ReparsePoint) != 0
                || (metaAttributes & (FileAttributes.ReadOnly | FileAttributes.ReparsePoint)) != 0)
            {
                throw new InvalidOperationException("Texture files are read-only or cross a reparse boundary.");
            }

            var sourceFileIdentity = CaptureFileIdentity(sourceFilePath);
            var metaFileIdentity = CaptureFileIdentity(metaFilePath);
            return new TextureAssetEvidence
            {
                importer = importer,
                assetPath = resolvedAssetPath,
                assetGuid = NormalizeHex(AssetDatabase.AssetPathToGUID(resolvedAssetPath), 32),
                sourceFilePath = sourceFilePath,
                metaFilePath = metaFilePath,
                sourceFileDigest = ComputeFileSha256(sourceFilePath),
                metaFileDigest = ComputeFileSha256(metaFilePath),
                sourceFileIdentity = sourceFileIdentity,
                sourceFileIdentityDigest = ComputeFileIdentityDigest(sourceFileIdentity),
                metaFileIdentity = metaFileIdentity,
                metaFileIdentityDigest = ComputeFileIdentityDigest(metaFileIdentity)
            };
        }

        private static ImportSettingsEvidence ReadSettings(TextureImporter importer, PlatformSpec platform)
        {
            var settings = platform.isDefault
                ? importer.GetDefaultPlatformTextureSettings()
                : importer.GetPlatformTextureSettings(platform.platformName);
            if (settings == null || settings.name != platform.platformName)
            {
                throw new InvalidOperationException("Texture importer platform identity is invalid.");
            }
            if (!AllowedMaxTextureSizes.Contains(settings.maxTextureSize))
            {
                throw new InvalidOperationException("Current maxTextureSize is unsupported.");
            }
            var format = CanonicalFormat(settings.format);
            if (!FormatsByPlatform[platform.canonicalName].Contains(format))
            {
                throw new InvalidOperationException("Current texture format is unsupported for the selected platform.");
            }
            return new ImportSettingsEvidence
            {
                platform = platform.canonicalName,
                platformName = platform.platformName,
                overridden = settings.overridden,
                maxTextureSize = settings.maxTextureSize,
                format = format,
                compression = CanonicalCompression(settings.textureCompression),
                crunch = settings.crunchedCompression,
                quality = settings.compressionQuality,
                ignorePlatformSupport = settings.ignorePlatformSupport
            };
        }

        private static ImportSettingsEvidence BuildTargetSettings(
            PlatformSpec platform,
            int maxTextureSize,
            string format,
            string compression,
            bool crunch,
            int quality
        )
        {
            return new ImportSettingsEvidence
            {
                platform = platform.canonicalName,
                platformName = platform.platformName,
                overridden = !platform.isDefault,
                maxTextureSize = maxTextureSize,
                format = format,
                compression = compression,
                crunch = crunch,
                quality = quality,
                ignorePlatformSupport = false
            };
        }

        private static bool SettingsEqual(ImportSettingsEvidence left, ImportSettingsEvidence right)
        {
            return left.platform == right.platform
                && left.platformName == right.platformName
                && left.overridden == right.overridden
                && left.maxTextureSize == right.maxTextureSize
                && left.format == right.format
                && left.compression == right.compression
                && left.crunch == right.crunch
                && left.quality == right.quality
                && left.ignorePlatformSupport == right.ignorePlatformSupport;
        }

        private static string ComputeSettingsDigest(string importerType, ImportSettingsEvidence settings)
        {
            var builder = new StringBuilder();
            AppendDigestField(builder, SettingsDigestSchema);
            AppendDigestField(builder, importerType);
            AppendDigestField(builder, settings.platform);
            AppendDigestField(builder, settings.platformName);
            AppendDigestField(builder, settings.overridden ? "true" : "false");
            AppendDigestField(builder, settings.maxTextureSize.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(builder, settings.format);
            AppendDigestField(builder, settings.compression);
            AppendDigestField(builder, settings.crunch ? "true" : "false");
            AppendDigestField(builder, settings.quality.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(builder, settings.ignorePlatformSupport ? "true" : "false");
            using (var sha256 = SHA256.Create())
            {
                return BitConverter.ToString(sha256.ComputeHash(Encoding.UTF8.GetBytes(builder.ToString())))
                    .Replace("-", string.Empty)
                    .ToLowerInvariant();
            }
        }

        private static void AppendDigestField(StringBuilder builder, string value)
        {
            var safeValue = value ?? string.Empty;
            builder.Append(safeValue.Length).Append(':').Append(safeValue);
        }

        private static string CanonicalImporterType(TextureImporterType importerType)
        {
            switch (importerType)
            {
                case TextureImporterType.Default:
                    return "Default";
                case TextureImporterType.NormalMap:
                    return "NormalMap";
                case TextureImporterType.GUI:
                    return "GUI";
                case TextureImporterType.Sprite:
                    return "Sprite";
                case TextureImporterType.Cursor:
                    return "Cursor";
                case TextureImporterType.Cookie:
                    return "Cookie";
                case TextureImporterType.Lightmap:
                    return "Lightmap";
                case TextureImporterType.SingleChannel:
                    return "SingleChannel";
                default:
                    throw new InvalidOperationException("Texture importer type is unsupported.");
            }
        }

        private static string CanonicalFormat(TextureImporterFormat format)
        {
            foreach (var item in FormatValues)
            {
                if (item.Value == format)
                {
                    return item.Key;
                }
            }
            throw new InvalidOperationException("Texture format is unsupported.");
        }

        private static string CanonicalCompression(TextureImporterCompression compression)
        {
            foreach (var item in CompressionValues)
            {
                if (item.Value == compression)
                {
                    return item.Key;
                }
            }
            throw new InvalidOperationException("Texture compression is unsupported.");
        }

        private static string ReadRequiredString(JObject @params, string key)
        {
            var token = @params?[key];
            if (token == null || token.Type != JTokenType.String)
            {
                throw new InvalidOperationException(key + " is required.");
            }
            var value = token.ToString().Trim();
            if (string.IsNullOrWhiteSpace(value) || value.Length > 32768 || value.Any(character => character < 32))
            {
                throw new InvalidOperationException(key + " is invalid.");
            }
            return value;
        }

        private static int ReadStrictInt(JObject @params, string key)
        {
            var token = @params?[key];
            if (token == null || token.Type != JTokenType.Integer)
            {
                throw new InvalidOperationException(key + " must be an integer.");
            }
            return token.Value<int>();
        }

        private static bool ReadStrictBool(JObject @params, string key)
        {
            var token = @params?[key];
            if (token == null || token.Type != JTokenType.Boolean)
            {
                throw new InvalidOperationException(key + " must be a boolean.");
            }
            return token.Value<bool>();
        }

        private static bool ReadOptionalBool(JObject @params, string key, bool fallback)
        {
            var token = @params?[key];
            if (token == null)
            {
                return fallback;
            }
            if (token.Type != JTokenType.Boolean)
            {
                throw new InvalidOperationException(key + " must be a boolean.");
            }
            return token.Value<bool>();
        }

        private static string ReadCanonicalChoice(JObject @params, string key, IEnumerable<string> allowed)
        {
            var value = ReadRequiredString(@params, key).ToLowerInvariant();
            if (!allowed.Contains(value, StringComparer.Ordinal))
            {
                throw new InvalidOperationException(key + " is unsupported.");
            }
            return value;
        }

        private static string NormalizeAssetPath(string value)
        {
            var normalized = (value ?? string.Empty).Replace("\\", "/").Trim();
            if (!normalized.StartsWith("Assets/", StringComparison.Ordinal)
                || normalized.StartsWith("/", StringComparison.Ordinal)
                || normalized.EndsWith("/", StringComparison.Ordinal)
                || normalized.Length > 2048
                || normalized.Split('/').Any(part => part == "." || part == ".." || string.IsNullOrWhiteSpace(part)))
            {
                throw new InvalidOperationException("Texture asset path is outside Assets/.");
            }
            return normalized;
        }

        private static string NormalizeHex(string value, int expectedLength)
        {
            var normalized = (value ?? string.Empty).Trim().ToLowerInvariant();
            if (normalized.Length != expectedLength || normalized.Any(character => !Uri.IsHexDigit(character)))
            {
                throw new InvalidOperationException("Verification identifier is invalid.");
            }
            return normalized;
        }

        private static FileIdentity CaptureFileIdentity(string filePath)
        {
            using (
                var stream = new FileStream(
                    filePath,
                    FileMode.Open,
                    FileAccess.Read,
                    FileShare.ReadWrite | FileShare.Delete
                )
            )
            {
                var identity = ReadFileIdentity(stream.SafeFileHandle);
                if (identity.numberOfLinks != 1)
                {
                    throw new InvalidOperationException("Texture files must have exactly one filesystem link.");
                }
                return identity;
            }
        }

        private static StableFileLease HoldStableFile(
            string filePath,
            FileIdentity expectedIdentity,
            string expectedDigest,
            bool allowReplacement
        )
        {
            if (!allowReplacement)
            {
                var stream = new FileStream(filePath, FileMode.Open, FileAccess.Read, FileShare.Read);
                try
                {
                    var identity = ReadFileIdentity(stream.SafeFileHandle);
                    if (identity.numberOfLinks != 1 || !FileIdentityEquals(identity, expectedIdentity))
                    {
                        throw new InvalidOperationException("Texture file identity changed after the verified preview.");
                    }
                    if (ComputeStreamSha256(stream) != expectedDigest)
                    {
                        throw new InvalidOperationException("Texture file content changed after the verified preview.");
                    }
                    return new StableFileLease(stream, identity);
                }
                catch
                {
                    stream.Dispose();
                    throw;
                }
            }

            var identityHandle = CreateFile(
                filePath,
                desiredAccess: 0,
                shareMode: NativeFileShareRead | NativeFileShareWrite | NativeFileShareDelete,
                securityAttributes: IntPtr.Zero,
                creationDisposition: NativeOpenExisting,
                flagsAndAttributes: NativeFileAttributeNormal,
                templateFile: IntPtr.Zero
            );
            if (identityHandle.IsInvalid)
            {
                identityHandle.Dispose();
                throw new InvalidOperationException("Texture file identity lease could not be opened.");
            }
            try
            {
                var identity = ReadFileIdentity(identityHandle);
                if (identity.numberOfLinks != 1 || !FileIdentityEquals(identity, expectedIdentity))
                {
                    throw new InvalidOperationException("Texture file identity changed after the verified preview.");
                }
                var lease = new StableFileLease(identityHandle, identity);
                VerifyPathMatchesLease(filePath, lease, expectedDigest);
                return lease;
            }
            catch
            {
                identityHandle.Dispose();
                throw;
            }
        }

        private static void VerifyPathMatchesLease(
            string filePath,
            StableFileLease lease,
            string expectedDigest
        )
        {
            using (
                var stream = new FileStream(
                    filePath,
                    FileMode.Open,
                    FileAccess.Read,
                    FileShare.ReadWrite | FileShare.Delete
                )
            )
            {
                var pathIdentity = ReadFileIdentity(stream.SafeFileHandle);
                if (pathIdentity.numberOfLinks != 1 || !FileIdentityEquals(pathIdentity, lease.identity))
                {
                    throw new InvalidOperationException("Texture file path changed after the verified preview.");
                }
                if (ComputeStreamSha256(stream) != expectedDigest)
                {
                    throw new InvalidOperationException("Texture file content changed after the verified preview.");
                }
            }
        }

        private static bool LeaseFileObjectMatches(StableFileLease lease)
        {
            try
            {
                var current = ReadFileIdentity(lease.handle);
                return current.volumeSerialNumber == lease.identity.volumeSerialNumber
                    && current.fileIndex == lease.identity.fileIndex;
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static FileIdentity ReadFileIdentity(SafeFileHandle handle)
        {
            if (Application.platform != RuntimePlatform.WindowsEditor)
            {
                throw new InvalidOperationException("Stable texture file identity is unavailable on this editor platform.");
            }
            ByHandleFileInformation information;
            if (!GetFileInformationByHandle(handle, out information))
            {
                throw new InvalidOperationException("Texture file identity could not be verified.");
            }
            return new FileIdentity
            {
                volumeSerialNumber = information.volumeSerialNumber,
                fileIndex = ((ulong)information.fileIndexHigh << 32) | information.fileIndexLow,
                numberOfLinks = information.numberOfLinks
            };
        }

        private static bool FileIdentityEquals(FileIdentity left, FileIdentity right)
        {
            return left != null
                && right != null
                && left.volumeSerialNumber == right.volumeSerialNumber
                && left.fileIndex == right.fileIndex
                && left.numberOfLinks == right.numberOfLinks;
        }

        private static string ComputeFileIdentityDigest(FileIdentity identity)
        {
            if (identity == null || identity.numberOfLinks != 1)
            {
                throw new InvalidOperationException("Texture file identity is invalid.");
            }
            var builder = new StringBuilder();
            AppendDigestField(builder, FileIdentityDigestSchema);
            AppendDigestField(builder, identity.volumeSerialNumber.ToString("x8", CultureInfo.InvariantCulture));
            AppendDigestField(builder, identity.fileIndex.ToString("x16", CultureInfo.InvariantCulture));
            AppendDigestField(builder, identity.numberOfLinks.ToString(CultureInfo.InvariantCulture));
            using (var sha256 = SHA256.Create())
            {
                return BitConverter.ToString(sha256.ComputeHash(Encoding.UTF8.GetBytes(builder.ToString())))
                    .Replace("-", string.Empty)
                    .ToLowerInvariant();
            }
        }

        private static string ComputeStreamSha256(FileStream stream)
        {
            stream.Position = 0;
            using (var sha256 = SHA256.Create())
            {
                var digest = BitConverter.ToString(sha256.ComputeHash(stream)).Replace("-", string.Empty).ToLowerInvariant();
                stream.Position = 0;
                return digest;
            }
        }

        private static string ComputeFileSha256(string filePath)
        {
            using (var sha256 = SHA256.Create())
            using (var stream = new FileStream(filePath, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                return BitConverter.ToString(sha256.ComputeHash(stream)).Replace("-", string.Empty).ToLowerInvariant();
            }
        }

        private static void EnsureNoReparseBoundary(string assetsRoot, string filePath)
        {
            var root = new DirectoryInfo(assetsRoot);
            if ((root.Attributes & FileAttributes.ReparsePoint) != 0)
            {
                throw new InvalidOperationException("Project Assets directory cannot be a reparse point.");
            }
            var current = new DirectoryInfo(Path.GetDirectoryName(filePath) ?? string.Empty);
            while (current != null)
            {
                if ((current.Attributes & FileAttributes.ReparsePoint) != 0)
                {
                    throw new InvalidOperationException("Texture path crosses a reparse boundary.");
                }
                if (string.Equals(current.FullName, root.FullName, PathComparison()))
                {
                    return;
                }
                current = current.Parent;
            }
            throw new InvalidOperationException("Texture path did not resolve below the project Assets directory.");
        }

        private static bool MatchesCurrentProject(string expectedProjectPath)
        {
            if (string.IsNullOrWhiteSpace(expectedProjectPath) || !Path.IsPathRooted(expectedProjectPath))
            {
                return false;
            }
            var expected = Path.GetFullPath(expectedProjectPath)
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            return string.Equals(expected, CurrentProjectPath(), PathComparison());
        }

        private static string CurrentProjectPath()
        {
            return Path.GetFullPath(Path.Combine(Application.dataPath, ".."))
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        }

        private static StringComparison PathComparison()
        {
            return Application.platform == RuntimePlatform.WindowsEditor
                ? StringComparison.OrdinalIgnoreCase
                : StringComparison.Ordinal;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetFileInformationByHandle(
            SafeFileHandle fileHandle,
            out ByHandleFileInformation fileInformation
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFile(
            string fileName,
            uint desiredAccess,
            uint shareMode,
            IntPtr securityAttributes,
            uint creationDisposition,
            uint flagsAndAttributes,
            IntPtr templateFile
        );

        [StructLayout(LayoutKind.Sequential)]
        private struct ByHandleFileInformation
        {
            public uint fileAttributes;
            public uint creationTimeLow;
            public uint creationTimeHigh;
            public uint lastAccessTimeLow;
            public uint lastAccessTimeHigh;
            public uint lastWriteTimeLow;
            public uint lastWriteTimeHigh;
            public uint volumeSerialNumber;
            public uint fileSizeHigh;
            public uint fileSizeLow;
            public uint numberOfLinks;
            public uint fileIndexHigh;
            public uint fileIndexLow;
        }

        private sealed class PlatformSpec
        {
            public string canonicalName = string.Empty;
            public string platformName = string.Empty;
            public bool isDefault;
            public BuildTarget buildTarget;

            public static PlatformSpec Create(string platform)
            {
                switch (platform)
                {
                    case "default":
                        return new PlatformSpec
                        {
                            canonicalName = platform,
                            platformName = "DefaultTexturePlatform",
                            isDefault = true,
                            buildTarget = BuildTarget.NoTarget
                        };
                    case "standalone":
                        return new PlatformSpec
                        {
                            canonicalName = platform,
                            platformName = "Standalone",
                            buildTarget = BuildTarget.StandaloneWindows64
                        };
                    case "android":
                        return new PlatformSpec
                        {
                            canonicalName = platform,
                            platformName = "Android",
                            buildTarget = BuildTarget.Android
                        };
                    case "ios":
                        return new PlatformSpec
                        {
                            canonicalName = platform,
                            platformName = "iPhone",
                            buildTarget = BuildTarget.iOS
                        };
                    default:
                        throw new InvalidOperationException("Texture platform is unsupported.");
                }
            }
        }

        private sealed class TextureAssetEvidence
        {
            public TextureImporter importer;
            public string assetPath = string.Empty;
            public string assetGuid = string.Empty;
            public string sourceFilePath = string.Empty;
            public string metaFilePath = string.Empty;
            public string sourceFileDigest = string.Empty;
            public string metaFileDigest = string.Empty;
            public FileIdentity sourceFileIdentity;
            public string sourceFileIdentityDigest = string.Empty;
            public FileIdentity metaFileIdentity;
            public string metaFileIdentityDigest = string.Empty;
        }

        private sealed class FileIdentity
        {
            public uint volumeSerialNumber;
            public ulong fileIndex;
            public uint numberOfLinks;
        }

        private sealed class StableFileLease : IDisposable
        {
            private readonly IDisposable owner;
            public readonly SafeFileHandle handle;
            public readonly FileIdentity identity;

            public StableFileLease(FileStream stream, FileIdentity identity)
            {
                owner = stream;
                handle = stream.SafeFileHandle;
                this.identity = identity;
            }

            public StableFileLease(SafeFileHandle handle, FileIdentity identity)
            {
                owner = handle;
                this.handle = handle;
                this.identity = identity;
            }

            public void Dispose()
            {
                owner.Dispose();
            }
        }

        private sealed class ImportSettingsEvidence
        {
            public string platform = string.Empty;
            public string platformName = string.Empty;
            public bool overridden;
            public int maxTextureSize;
            public string format = string.Empty;
            public string compression = string.Empty;
            public bool crunch;
            public int quality;
            public bool ignorePlatformSupport;
        }
    }
}
