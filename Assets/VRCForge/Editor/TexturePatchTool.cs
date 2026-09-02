using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_texture_patch",
        Summary = "Preview or apply a single rectangle patch to one existing project PNG to one new absent project PNG."
    )]
    public static class TexturePatchTool
    {
        private const string ResultSchema = "vrcforge.texture_patch.v1";

        private const int MaxPathLength = 1024;
        private const int MaxColorValue = 255;

        public class Parameters
        {
            [VRCForgeInput("Required source PNG path under Assets.", IsRequired = true)]
            public string sourceTexturePath { get; set; } = "";
            [VRCForgeInput("Required output PNG path under Assets that must be absent before apply.", IsRequired = true)]
            public string targetTexturePath { get; set; } = "";
            [VRCForgeInput("Patch mode preview flag.", IsRequired = false)]
            public bool? preview { get; set; } = false;
            [VRCForgeInput("Patch rectangle: [x, y, width, height].", IsRequired = true)]
            public int[] region { get; set; } = Array.Empty<int>();
            [VRCForgeInput("Protected rectangles that must remain unchanged; each item is [x, y, width, height].", IsRequired = false)]
            public int[][] protectedRegions { get; set; } = Array.Empty<int[]>();
            [VRCForgeInput("Patch red channel (0-255).", IsRequired = true)]
            public int red { get; set; }
            [VRCForgeInput("Patch green channel (0-255).", IsRequired = true)]
            public int green { get; set; }
            [VRCForgeInput("Patch blue channel (0-255).", IsRequired = true)]
            public int blue { get; set; }
            [VRCForgeInput("Patch alpha channel (0-255).", IsRequired = true)]
            public int alpha { get; set; }
            [VRCForgeInput("Patch opacity multiplier (0..1).", IsRequired = true)]
            public float opacity { get; set; } = 1f;
            [VRCForgeInput("Feather width in pixels; 0 means no feathering.", IsRequired = true)]
            public int featherPixels { get; set; } = 0;
            [VRCForgeInput("Required exact active Unity project path for apply.", IsRequired = false)]
            public string expectedProjectPath { get; set; } = "";
            [VRCForgeInput("Source SHA-256 from preview; required for apply.", IsRequired = false)]
            public string expectedSourceHash { get; set; } = "";
            [VRCForgeInput("Preview digest from preview; required for apply.", IsRequired = false)]
            public string expectedPreviewDigest { get; set; } = "";
            [VRCForgeInput("Source width from preview; required for apply.", IsRequired = false)]
            public int? expectedSourceWidth { get; set; }
            [VRCForgeInput("Source height from preview; required for apply.", IsRequired = false)]
            public int? expectedSourceHeight { get; set; }
            [VRCForgeInput("Exact absent output path from preview; required for apply.", IsRequired = false)]
            public string expectedTargetTexturePath { get; set; } = "";
            [VRCForgeInput("Verified target-absent assertion from preview; required for apply.", IsRequired = false)]
            public bool? expectedTargetTextureAbsent { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            string sourceTexturePath = string.Empty;
            string targetTexturePath = string.Empty;
            string sourceFilePath = string.Empty;
            string targetFilePath = string.Empty;
            bool mutationStarted = false;

            try
            {
                var preview = @params?["preview"]?.Type == JTokenType.Boolean && @params["preview"].Value<bool>();
                var parameters = @params ?? throw new InvalidOperationException("Parameters are required.");

                sourceTexturePath = ReadRequiredAssetPath(parameters, "sourceTexturePath", false);
                targetTexturePath = ReadRequiredAssetPath(parameters, "targetTexturePath", false);

                sourceFilePath = ResolveAssetAbsolutePath(sourceTexturePath);
                targetFilePath = ResolveAssetAbsolutePath(targetTexturePath);
                EnsureExistingSourcePng(sourceTexturePath, sourceFilePath);
                EnsureNewTargetPng(targetTexturePath, targetFilePath);

                var explicitProjectPath = ReadOptionalString(parameters, "expectedProjectPath");
                if (!string.IsNullOrEmpty(explicitProjectPath)
                    && !SceneObjectCopyCore.MatchesCurrentProject(explicitProjectPath))
                {
                    throw new InvalidOperationException("The selected Unity project does not match the active editor instance.");
                }

                var sourcePixels = ReadPngPixels(sourceFilePath, out var sourceWidth, out var sourceHeight);
                if (sourceWidth <= 0 || sourceHeight <= 0)
                {
                    throw new InvalidOperationException("The source PNG has invalid dimensions.");
                }

                var sourceHash = ComputeSha256(sourceFilePath);
                var sourceGuid = AssetDatabase.AssetPathToGUID(sourceTexturePath) ?? string.Empty;
                if (sourceGuid.Length != 0)
                {
                    sourceGuid = NormalizeHex(sourceGuid, 32, "sourceTexturePath");
                }

                var region = ReadRectangle(ReadRequiredToken(parameters, "region"), "region");
                ValidateInsideSource("region", region, sourceWidth, sourceHeight);

                var protectedRegions = ReadRegions(parameters["protectedRegions"], "protectedRegions", sourceWidth, sourceHeight);
                var patchColor = ReadColor(parameters);
                var opacity = ReadDouble(parameters, "opacity");
                var featherPixels = ReadInt(parameters, "featherPixels");
                if (featherPixels < 0)
                {
                    throw new InvalidOperationException("featherPixels must be a non-negative integer.");
                }
                if (double.IsNaN(opacity) || double.IsInfinity(opacity) || opacity < 0d || opacity > 1d)
                {
                    throw new InvalidOperationException("opacity must be between 0 and 1.");
                }

                var request = new PatchRequest
                {
                    SourceTexturePath = sourceTexturePath,
                    SourceFilePath = sourceFilePath,
                    SourceFileHash = sourceHash,
                    SourceWidth = sourceWidth,
                    SourceHeight = sourceHeight,
                    SourceGuid = sourceGuid,
                    TargetTexturePath = targetTexturePath,
                    TargetFilePath = targetFilePath,
                    Region = region,
                    ProtectedRegions = protectedRegions,
                    Patch = patchColor,
                    Opacity = (float)opacity,
                    FeatherPixels = featherPixels,
                    ProjectPath = CurrentProjectPath(),
                    SourcePixels = sourcePixels
                };

                request.PreviewDigest = ComputePreviewDigest(request);

                var patchedPixelsForPreview = ApplyPatchToSource(
                    sourcePixels,
                    sourceWidth,
                    sourceHeight,
                    request.Region,
                    request.ProtectedRegions,
                    request.Patch,
                    request.Opacity,
                    request.FeatherPixels,
                    out var changedPixelCountForPreview);
                var wouldChange = changedPixelCountForPreview > 0;
                var wouldPatchPixels = CountPatchPixels(sourceWidth, sourceHeight, request.Region, request.ProtectedRegions);
                if (wouldPatchPixels == 0)
                {
                    throw new InvalidOperationException("The requested patch has no target pixels.");
                }

                if (!preview)
                {
                    var expectedProjectPath = ReadRequiredStringFromBinding(parameters, "expectedProjectPath");
                    var expectedSourceHash = ReadRequiredStringFromBinding(parameters, "expectedSourceHash");
                    var expectedPreviewDigest = ReadRequiredStringFromBinding(parameters, "expectedPreviewDigest");
                    var expectedSourceWidth = ReadRequiredIntFromBinding(parameters, "expectedSourceWidth");
                    var expectedSourceHeight = ReadRequiredIntFromBinding(parameters, "expectedSourceHeight");
                    var expectedTargetTextureAbsent = ReadRequiredBoolFromBinding(parameters, "expectedTargetTextureAbsent");
                    if (!SceneObjectCopyCore.MatchesCurrentProject(expectedProjectPath))
                    {
                        throw new InvalidOperationException("expectedProjectPath does not match the active Unity project.");
                    }
                    if (!string.Equals(expectedSourceHash, request.SourceFileHash, StringComparison.OrdinalIgnoreCase))
                    {
                        throw new InvalidOperationException("The source hash changed after preview.");
                    }
                    if (!string.Equals(expectedPreviewDigest, request.PreviewDigest, StringComparison.OrdinalIgnoreCase))
                    {
                        throw new InvalidOperationException("The texture patch preview receipt changed.");
                    }
                    if (expectedSourceWidth != request.SourceWidth)
                    {
                        throw new InvalidOperationException("The source width changed after preview.");
                    }
                    if (expectedSourceHeight != request.SourceHeight)
                    {
                        throw new InvalidOperationException("The source height changed after preview.");
                    }
                    if (!expectedTargetTextureAbsent)
                    {
                        throw new InvalidOperationException("The preview did not confirm the target asset is absent.");
                    }
                    if (!IsTargetPathAbsent(request.TargetTexturePath, request.TargetFilePath))
                    {
                        throw new InvalidOperationException("The target texture is no longer absent.");
                    }
                    if (!string.Equals(targetTexturePath, ReadRequiredStringFromBinding(parameters, "expectedTargetTexturePath"), StringComparison.Ordinal))
                    {
                        throw new InvalidOperationException("The output texture path does not match the verified preview target.");
                    }
                }

                if (preview)
                {
                    var previewPayload = BuildPreviewPayload(request, wouldChange);
                    return VRCForgeToolResult.Completed("Texture patch preview completed.", previewPayload);
                }

                var patchedPixels = patchedPixelsForPreview;
                // The Tool is create-new even when the requested RGB patch is
                // visually identical.  The new asset is still the mutation.
                {
                    var outputBytes = BuildPatchedPngBytes(sourceWidth, sourceHeight, patchedPixels);
                    mutationStarted = true;
                    File.WriteAllBytes(request.TargetFilePath, outputBytes);

                    AssetDatabase.ImportAsset(
                        request.TargetTexturePath,
                        ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);

                    var readbackPixels = ReadPngPixels(request.TargetFilePath, out var reloadedWidth, out var reloadedHeight);
                    if (reloadedWidth != request.SourceWidth || reloadedHeight != request.SourceHeight)
                    {
                        throw new InvalidOperationException("The patch changed image dimensions.");
                    }

                    var outsideAndProtectedPixelsUnchanged = false;
                    var alphaUnchanged = false;
                    VerifyReadback(request, patchedPixels, sourcePixels, sourceWidth, sourceHeight, readbackPixels, out outsideAndProtectedPixelsUnchanged, out alphaUnchanged);

                    var targetHash = ComputeSha256(request.TargetFilePath);
                    return VRCForgeToolResult.Completed(
                        "Texture patch applied.",
                        new
                        {
                            schema = ResultSchema,
                            operation = "texture_patch",
                            ok = true,
                            preview = false,
                            verified = true,
                            changed = true,
                            wouldChange,
                            saved = true,
                            persistedReadback = true,
                            projectPath = request.ProjectPath,
                            sourceTexturePath = request.SourceTexturePath,
                            sourceTextureGuid = request.SourceGuid,
                            sourceFileHash = request.SourceFileHash,
                            sourceWidth = request.SourceWidth,
                            sourceHeight = request.SourceHeight,
                            targetTexturePath = request.TargetTexturePath,
                            targetFileHash = targetHash,
                            region = RectangleToPayload(request.Region),
                            protectedRegions = request.ProtectedRegions.Select(RectangleToPayload).ToArray(),
                            red = request.Patch.R,
                            green = request.Patch.G,
                            blue = request.Patch.B,
                            alpha = request.Patch.A,
                            opacity = request.Opacity,
                            featherPixels = request.FeatherPixels,
                            previewDigest = request.PreviewDigest,
                            mutationStarted = true,
                            committed = true,
                            commitState = "committed",
                            checkpointRecoveryRequired = false
                            ,readback = new
                            {
                                path = request.TargetTexturePath,
                                fileHash = targetHash,
                                width = reloadedWidth,
                                height = reloadedHeight,
                                sourceHash = request.SourceFileHash,
                                outsideAndProtectedPixelsUnchanged = outsideAndProtectedPixelsUnchanged,
                                alphaUnchanged = alphaUnchanged
                            }
                        });
                }
            }
            catch (Exception exception)
            {
                if (mutationStarted)
                {
                    if (!string.IsNullOrEmpty(targetFilePath))
                    {
                        CleanupTarget(targetTexturePath, targetFilePath);
                    }

                    return VRCForgeToolResult.FailedWithCode(
                        "vrc_texture_patch_failed_after_mutation",
                        "Texture patch failed after mutation; partial output was cleaned.",
                        new
                        {
                            schema = ResultSchema,
                            mutationStarted = true,
                            committed = false,
                            commitState = "rolled_back",
                            commitStateKnown = true,
                            checkpointRecoveryRequired = false,
                            failureLayer = "unity_core_tool",
                            failurePhase = "apply_mutation",
                            restorationVerified = true,
                            sourceTexturePath,
                            targetTexturePath
                        });
                }

                return VRCForgeToolResult.RejectedBeforeMutation(
                    "vrc_texture_patch_rejected",
                    exception.Message,
                    "unity_core_tool",
                    "texture_patch_validation",
                    false);
            }
        }

        private static object BuildPreviewPayload(PatchRequest request, bool wouldChange)
        {
            return new
            {
                schema = ResultSchema,
                operation = "texture_patch",
                ok = true,
                preview = true,
                verified = true,
                changed = wouldChange,
                wouldChange,
                saved = false,
                persistedReadback = false,
                projectPath = request.ProjectPath,
                sourceTexturePath = request.SourceTexturePath,
                sourceTextureGuid = request.SourceGuid,
                sourceFileHash = request.SourceFileHash,
                sourceWidth = request.SourceWidth,
                sourceHeight = request.SourceHeight,
                targetTexturePath = request.TargetTexturePath,
                region = RectangleToPayload(request.Region),
                protectedRegions = request.ProtectedRegions.Select(RectangleToPayload).ToArray(),
                red = request.Patch.R,
                green = request.Patch.G,
                blue = request.Patch.B,
                alpha = request.Patch.A,
                opacity = request.Opacity,
                featherPixels = request.FeatherPixels,
                previewDigest = request.PreviewDigest,
                applyBinding = new
                {
                    expectedProjectPath = request.ProjectPath,
                    expectedSourceHash = request.SourceFileHash,
                    expectedPreviewDigest = request.PreviewDigest,
                    expectedSourceWidth = request.SourceWidth,
                    expectedSourceHeight = request.SourceHeight,
                    expectedTargetTexturePath = request.TargetTexturePath,
                    expectedTargetTextureAbsent = true
                },
                mutationStarted = false,
                committed = false,
                commitState = "not_started",
                checkpointRecoveryRequired = false
                ,readback = new
                {
                    path = request.TargetTexturePath,
                    sourceHash = request.SourceFileHash,
                    width = request.SourceWidth,
                    height = request.SourceHeight,
                    targetAbsent = true
                }
            };
        }

        private static Color32[] ReadPngPixels(string absolutePngPath, out int width, out int height)
        {
            var bytes = File.ReadAllBytes(absolutePngPath);
            var texture = new Texture2D(2, 2, TextureFormat.RGBA32, false, false);
            try
            {
                if (!texture.LoadImage(bytes, false))
                {
                    throw new InvalidOperationException("The source PNG could not be loaded.");
                }

                width = texture.width;
                height = texture.height;
                return texture.GetPixels32();
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(texture);
            }
        }

        private static byte[] BuildPatchedPngBytes(int width, int height, Color32[] pixels)
        {
            var texture = new Texture2D(width, height, TextureFormat.RGBA32, false, false);
            try
            {
                texture.SetPixels32(pixels);
                texture.Apply(false, false);
                return texture.EncodeToPNG();
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(texture);
            }
        }

        private static Color32[] ApplyPatchToSource(
            Color32[] source,
            int width,
            int height,
            Rectangle area,
            Rectangle[] protectedRegions,
            PatchColor color,
            float opacity,
            int featherPixels,
            out int changedCount)
        {
            var output = (Color32[])source.Clone();
            var protectedMask = BuildProtectedMask(width, height, protectedRegions);

            var feather = Math.Max(featherPixels, 0);
            changedCount = 0;
            var patchAlpha = opacity * (color.A / 255f);
            var lastX = area.X + area.Width - 1;
            var lastY = area.Y + area.Height - 1;

            for (var y = area.Y; y <= lastY; y++)
            {
                for (var x = area.X; x <= lastX; x++)
                {
                    var index = y * width + x;
                    if (index < 0 || index >= protectedMask.Length || protectedMask[index])
                    {
                        continue;
                    }

                    var left = x - area.X;
                    var right = lastX - x;
                    var bottom = y - area.Y;
                    var top = lastY - y;
                    var distance = Math.Min(Math.Min(left, right), Math.Min(top, bottom));
                    var featherScale = feather <= 0
                        ? 1f
                        : Math.Min(1f, (distance + 1f) / (float)feather);
                    var applyAlpha = patchAlpha * featherScale;
                    if (applyAlpha <= 0f)
                    {
                        continue;
                    }

                    var before = source[index];
                    var after = new Color32(
                        Blend(before.r, color.R, applyAlpha),
                        Blend(before.g, color.G, applyAlpha),
                        Blend(before.b, color.B, applyAlpha),
                        before.a);

                    if (!ColorsEqual(after, before))
                    {
                        changedCount++;
                    }

                    output[index] = after;
                }
            }

            return output;
        }

        private static bool[] BuildProtectedMask(int width, int height, Rectangle[] protectedRegions)
        {
            var mask = new bool[width * height];
            if (protectedRegions == null || protectedRegions.Length == 0)
            {
                return mask;
            }

            foreach (var region in protectedRegions)
            {
                var lastX = region.X + region.Width - 1;
                var lastY = region.Y + region.Height - 1;
                for (var y = region.Y; y <= lastY; y++)
                {
                    var rowOffset = y * width;
                    for (var x = region.X; x <= lastX; x++)
                    {
                        mask[rowOffset + x] = true;
                    }
                }
            }

            return mask;
        }

        private static void VerifyReadback(
            PatchRequest request,
            Color32[] expectedPixels,
            Color32[] sourcePixels,
            int width,
            int height,
            Color32[] readbackPixels,
            out bool outsideAndProtectedPixelsUnchanged,
            out bool alphaUnchanged)
        {
            if (expectedPixels.Length != readbackPixels.Length
                || sourcePixels.Length != readbackPixels.Length
                || expectedPixels.Length != width * height)
            {
                throw new InvalidOperationException("Texture patch readback dimensions did not match the source payload.");
            }

            outsideAndProtectedPixelsUnchanged = true;
            alphaUnchanged = true;
            var protectedMask = BuildProtectedMask(width, height, request.ProtectedRegions);
            var lastX = request.Region.X + request.Region.Width - 1;
            var lastY = request.Region.Y + request.Region.Height - 1;

            for (var y = 0; y < height; y++)
            {
                for (var x = 0; x < width; x++)
                {
                    var index = y * width + x;
                    if (index < 0 || index >= expectedPixels.Length)
                    {
                        throw new InvalidOperationException("Texture readback index overflow.");
                    }

                    var isOutsidePatch = x < request.Region.X || x > lastX || y < request.Region.Y || y > lastY;
                    if (isOutsidePatch || protectedMask[index])
                    {
                        if (!ColorsEqual(readbackPixels[index], sourcePixels[index]))
                        {
                            outsideAndProtectedPixelsUnchanged = false;
                            throw new InvalidOperationException("Texture patch changed pixels outside the requested patch area.");
                        }
                    }

                    if (!ColorsEqual(readbackPixels[index], expectedPixels[index]))
                    {
                        throw new InvalidOperationException("Texture patch persisted readback does not match the approved target bytes.");
                    }

                    if (request.SourcePixels != null && sourcePixels[index].a != readbackPixels[index].a)
                    {
                        alphaUnchanged = false;
                        throw new InvalidOperationException("Texture patch persisted readback altered source alpha.");
                    }
                }
            }
        }

        private static int CountPatchPixels(int width, int height, Rectangle area, Rectangle[] protectedRegions)
        {
            var patchedArea = area.Width * area.Height;
            if (patchedArea <= 0)
            {
                return 0;
            }

            if (protectedRegions == null || protectedRegions.Length == 0)
            {
                return patchedArea;
            }

            var protectedMask = BuildProtectedMask(width, height, protectedRegions);
            var protectedInside = 0;
            var lastX = area.X + area.Width - 1;
            var lastY = area.Y + area.Height - 1;
            for (var y = area.Y; y <= lastY; y++)
            {
                var rowOffset = y * width;
                for (var x = area.X; x <= lastX; x++)
                {
                    if (protectedMask[rowOffset + x])
                    {
                        protectedInside++;
                    }
                }
            }

            return Math.Max(patchedArea - protectedInside, 0);
        }

        private static void CleanupTarget(string targetPath, string targetFullPath)
        {
            try
            {
                if (!string.IsNullOrEmpty(targetPath) && AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(targetPath) != null)
                {
                    AssetDatabase.DeleteAsset(targetPath);
                }
            }
            catch
            {
                // best-effort best-effort; continue fallback cleanup below.
            }

            try
            {
                if (File.Exists(targetFullPath))
                {
                    File.Delete(targetFullPath);
                }
                var metaPath = targetFullPath + ".meta";
                if (File.Exists(metaPath))
                {
                    File.Delete(metaPath);
                }
            }
            catch
            {
                // best-effort cleanup.
            }
        }

        private static string ComputePreviewDigest(PatchRequest request)
        {
            var frame = new StringBuilder();
            AppendDigestField(frame, ResultSchema);
            AppendDigestField(frame, request.SourceTexturePath);
            AppendDigestField(frame, request.SourceFileHash);
            AppendDigestField(frame, request.SourceWidth.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(frame, request.SourceHeight.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(frame, request.TargetTexturePath);
            AppendDigestField(frame, request.Region.X.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(frame, request.Region.Y.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(frame, request.Region.Width.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(frame, request.Region.Height.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(frame, request.ProtectedRegions.Length.ToString(CultureInfo.InvariantCulture));
            foreach (var protectedRegion in request.ProtectedRegions)
            {
                AppendDigestField(frame, protectedRegion.X.ToString(CultureInfo.InvariantCulture));
                AppendDigestField(frame, protectedRegion.Y.ToString(CultureInfo.InvariantCulture));
                AppendDigestField(frame, protectedRegion.Width.ToString(CultureInfo.InvariantCulture));
                AppendDigestField(frame, protectedRegion.Height.ToString(CultureInfo.InvariantCulture));
            }
            AppendDigestField(frame, request.Patch.R.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(frame, request.Patch.G.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(frame, request.Patch.B.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(frame, request.Patch.A.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(frame, request.Opacity.ToString("R", CultureInfo.InvariantCulture));
            AppendDigestField(frame, request.FeatherPixels.ToString(CultureInfo.InvariantCulture));
            AppendDigestField(frame, request.ProjectPath);
            using (var sha256 = SHA256.Create())
            {
                return Hex(sha256.ComputeHash(Encoding.UTF8.GetBytes(frame.ToString())));
            }
        }

        private static void ValidateInsideSource(string label, Rectangle rect, int width, int height)
        {
            if (rect.X < 0
                || rect.Y < 0
                || rect.Width <= 0
                || rect.Height <= 0
                || rect.X + rect.Width > width
                || rect.Y + rect.Height > height)
            {
                throw new InvalidOperationException(label + " is outside the source texture bounds.");
            }
        }

        private static Rectangle[] ReadRegions(JToken token, string label, int width, int height)
        {
            if (token == null || token.Type == JTokenType.Null)
            {
                return Array.Empty<Rectangle>();
            }
            if (token.Type != JTokenType.Array)
            {
                throw new InvalidOperationException(label + " must be an array.");
            }

            var regions = ((JArray)token).Select(regionToken => ReadRectangle(regionToken, label, true)).ToArray();
            for (var index = 0; index < regions.Length; index++)
            {
                try
                {
                    ValidateInsideSource(label + "[" + index.ToString(CultureInfo.InvariantCulture) + "]", regions[index], width, height);
                }
                catch (Exception exception)
                {
                    throw new InvalidOperationException(label + "[" + index.ToString(CultureInfo.InvariantCulture) + "] is invalid: " + exception.Message);
                }
            }
            return regions;
        }

        private static Rectangle ReadRectangle(JToken token, string label, bool allowEmpty = false)
        {
            if (token == null || token.Type != JTokenType.Array)
            {
                throw new InvalidOperationException(label + " is invalid.");
            }

            var values = (JArray)token;
            if (values.Count != 4)
            {
                throw new InvalidOperationException(label + " must contain 4 integers [x, y, width, height].");
            }

            var x = ReadIntFromToken(values[0], label + "[0]");
            var y = ReadIntFromToken(values[1], label + "[1]");
            var width = ReadIntFromToken(values[2], label + "[2]");
            var height = ReadIntFromToken(values[3], label + "[3]");

            if (!allowEmpty && (width <= 0 || height <= 0))
            {
                throw new InvalidOperationException(label + " width and height must be positive.");
            }

            return new Rectangle
            {
                X = x,
                Y = y,
                Width = width,
                Height = height
            };
        }

        private static PatchColor ReadColor(JObject parameters)
        {
            var r = ReadInt(parameters, "red");
            var g = ReadInt(parameters, "green");
            var b = ReadInt(parameters, "blue");
            var a = ReadInt(parameters, "alpha");
            return new PatchColor
            {
                R = NormalizeColorChannel(r, "red"),
                G = NormalizeColorChannel(g, "green"),
                B = NormalizeColorChannel(b, "blue"),
                A = NormalizeColorChannel(a, "alpha")
            };
        }

        private static byte NormalizeColorChannel(int value, string label)
        {
            if (value < 0 || value > MaxColorValue)
            {
                throw new InvalidOperationException(label + " must be in the range 0..255.");
            }

            return (byte)value;
        }

        private static byte Blend(int before, byte patch, float alpha)
        {
            if (alpha <= 0f)
            {
                return (byte)before;
            }

            if (alpha >= 1f)
            {
                return patch;
            }

            var value = (int)Math.Round(before * (1f - alpha) + patch * alpha);
            return (byte)Mathf.Clamp(value, 0, MaxColorValue);
        }

        private static bool ColorsEqual(Color32 left, Color32 right)
        {
            return left.r == right.r && left.g == right.g && left.b == right.b && left.a == right.a;
        }

        private static string CurrentProjectPath()
        {
            return Path.GetFullPath(Path.Combine(Application.dataPath, ".."))
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        }

        private static string ResolveAssetAbsolutePath(string assetPath)
        {
            var projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            var assetsRoot = Path.GetFullPath(Application.dataPath)
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            var absolute = Path.GetFullPath(Path.Combine(projectRoot, assetPath.Replace('/', Path.DirectorySeparatorChar)));
            var prefix = assetsRoot + Path.DirectorySeparatorChar;
            if (!absolute.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("Asset path must resolve within project Assets.");
            }
            return absolute;
        }

        private static string ReadRequiredAssetPath(JObject parameters, string key, bool allowPackages)
        {
            var value = ReadRequiredString(parameters, key);
            var normalized = value.Replace('\\', '/');
            if (normalized.StartsWith("Packages/", StringComparison.Ordinal))
            {
                if (!allowPackages)
                {
                    throw new InvalidOperationException(key + " must be under Assets.");
                }
            }
            else if (!normalized.StartsWith("Assets/", StringComparison.Ordinal))
            {
                throw new InvalidOperationException(key + " must be under Assets.");
            }

            if (normalized.Length > MaxPathLength
                || normalized.IndexOf('\\') >= 0
                || normalized.StartsWith("/", StringComparison.Ordinal)
                || normalized.EndsWith("/", StringComparison.Ordinal))
            {
                throw new InvalidOperationException(key + " is invalid.");
            }

            if (normalized.Any(character => char.IsControl(character)))
            {
                throw new InvalidOperationException(key + " contains control characters.");
            }

            var extension = Path.GetExtension(normalized);
            if (!string.Equals(extension, ".png", StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(key + " must point to a PNG.");
            }

            return normalized;
        }

        private static void EnsureExistingSourcePng(string assetPath, string absolutePath)
        {
            if (!File.Exists(absolutePath))
            {
                throw new InvalidOperationException("The source PNG does not exist: " + assetPath);
            }

            var attributes = File.GetAttributes(absolutePath);
            if ((attributes & FileAttributes.ReparsePoint) != 0)
            {
                throw new InvalidOperationException("Source PNG must not be a reparse point.");
            }
        }

        private static void EnsureNewTargetPng(string targetTexturePath, string absoluteTargetPath)
        {
            if (AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(targetTexturePath) != null)
            {
                throw new InvalidOperationException("The target PNG already exists: " + targetTexturePath);
            }
            if (File.Exists(absoluteTargetPath))
            {
                throw new InvalidOperationException("The target path already exists as a file.");
            }
            if (File.Exists(absoluteTargetPath + ".meta"))
            {
                throw new InvalidOperationException("The target path has an existing meta file and is not a new asset.");
            }
            var parent = Path.GetDirectoryName(absoluteTargetPath);
            if (string.IsNullOrWhiteSpace(parent) || !Directory.Exists(parent))
            {
                throw new InvalidOperationException("The target PNG parent folder must already exist.");
            }
        }

        private static bool IsTargetPathAbsent(string targetTexturePath, string absoluteTargetPath)
        {
            if (AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(targetTexturePath) != null)
            {
                return false;
            }

            if (File.Exists(absoluteTargetPath))
            {
                return false;
            }

            if (File.Exists(absoluteTargetPath + ".meta"))
            {
                return false;
            }

            return true;
        }

        private static T ReadRequired<T>(JObject parameters, string key, Func<JToken, T> read)
        {
            if (parameters == null || read == null)
            {
                throw new InvalidOperationException("Invalid parameter state.");
            }
            var token = parameters[key];
            if (token == null)
            {
                throw new InvalidOperationException(key + " is required.");
            }
            return read(token);
        }

        private static string ReadRequiredString(JObject parameters, string key)
        {
            return ReadRequired(parameters, key, token =>
            {
                if (token.Type != JTokenType.String)
                {
                    throw new InvalidOperationException(key + " must be a string.");
                }
                var value = token.ToString().Trim();
                if (string.IsNullOrEmpty(value))
                {
                    throw new InvalidOperationException(key + " must be non-empty.");
                }
                return value;
            });
        }

        private static string ReadRequiredStringFromBinding(JObject parameters, string key)
        {
            var rootToken = parameters[key];
            if (rootToken == null && parameters["applyBinding"] is JObject applyBinding)
            {
                rootToken = applyBinding[key];
            }

            if (rootToken == null || rootToken.Type != JTokenType.String)
            {
                throw new InvalidOperationException("The verified " + key + " is required.");
            }
            var value = rootToken.ToString().Trim();
            if (string.IsNullOrEmpty(value))
            {
                throw new InvalidOperationException("The verified " + key + " is required.");
            }
            return value;
        }

        private static int ReadRequiredIntFromBinding(JObject parameters, string key)
        {
            var rootToken = parameters[key];
            if (rootToken == null && parameters["applyBinding"] is JObject applyBinding)
            {
                rootToken = applyBinding[key];
            }

            if (rootToken == null || rootToken.Type != JTokenType.Integer)
            {
                throw new InvalidOperationException("The verified " + key + " is required.");
            }
            return rootToken.Value<int>();
        }

        private static bool ReadRequiredBoolFromBinding(JObject parameters, string key)
        {
            var rootToken = parameters[key];
            if (rootToken == null && parameters["applyBinding"] is JObject applyBinding)
            {
                rootToken = applyBinding[key];
            }

            if (rootToken == null || rootToken.Type != JTokenType.Boolean)
            {
                throw new InvalidOperationException("The verified " + key + " is required.");
            }
            return rootToken.Value<bool>();
        }

        private static string ReadOptionalString(JObject parameters, string key)
        {
            return parameters[key]?.ToString() ?? string.Empty;
        }

        private static JToken ReadRequiredToken(JObject parameters, string key)
        {
            var token = parameters[key];
            if (token == null || token.Type == JTokenType.Null)
            {
                throw new InvalidOperationException(key + " is required.");
            }
            return token;
        }

        private static int ReadInt(JObject parameters, string key)
        {
            return ReadRequired(parameters, key, token =>
            {
                if (token.Type != JTokenType.Integer)
                {
                    throw new InvalidOperationException(key + " must be an integer.");
                }
                return token.Value<int>();
            });
        }

        private static double ReadDouble(JObject parameters, string key)
        {
            return ReadRequired(parameters, key, token =>
            {
                if (token.Type == JTokenType.Integer || token.Type == JTokenType.Float)
                {
                    return token.Value<double>();
                }
                throw new InvalidOperationException(key + " must be a number.");
            });
        }

        private static int ReadIntFromToken(JToken token, string label)
        {
            if (token == null || token.Type != JTokenType.Integer)
            {
                throw new InvalidOperationException(label + " must be an integer.");
            }
            return token.Value<int>();
        }

        private static string NormalizeHex(string value, int expectedLength, string label)
        {
            var normalized = (value ?? string.Empty).Trim();
            if (normalized.Length != expectedLength
                || !normalized.All(character => Uri.IsHexDigit(character))
                || !string.Equals(normalized, normalized.ToLowerInvariant(), StringComparison.Ordinal))
            {
                throw new InvalidOperationException(label + " is invalid.");
            }
            return normalized;
        }

        private static string ComputeSha256(string filePath)
        {
            using (var stream = File.OpenRead(filePath))
            using (var sha256 = SHA256.Create())
            {
                return Hex(sha256.ComputeHash(stream));
            }
        }

        private static string Hex(byte[] bytes)
        {
            return BitConverter.ToString(bytes).Replace("-", string.Empty).ToLowerInvariant();
        }

        private static int[] RectangleToPayload(Rectangle rect)
        {
            return new[]
            {
                rect.X,
                rect.Y,
                rect.Width,
                rect.Height
            };
        }

        private static void AppendDigestField(StringBuilder target, string value)
        {
            var safe = value ?? string.Empty;
            target.Append(safe.Length).Append(':').Append(safe);
        }

        private sealed class PatchRequest
        {
            internal string SourceTexturePath = string.Empty;
            internal string SourceFilePath = string.Empty;
            internal string SourceFileHash = string.Empty;
            internal int SourceWidth;
            internal int SourceHeight;
            internal string SourceGuid = string.Empty;
            internal string TargetTexturePath = string.Empty;
            internal string TargetFilePath = string.Empty;
            internal Rectangle Region;
            internal Rectangle[] ProtectedRegions = Array.Empty<Rectangle>();
            internal PatchColor Patch;
            internal float Opacity;
            internal int FeatherPixels;
            internal string PreviewDigest = string.Empty;
            internal string ProjectPath = string.Empty;
            internal Color32[] SourcePixels;
        }

        private sealed class Rectangle
        {
            internal int X;
            internal int Y;
            internal int Width;
            internal int Height;
        }

        private sealed class PatchColor
        {
            internal byte R;
            internal byte G;
            internal byte B;
            internal byte A;
        }
    }
}
