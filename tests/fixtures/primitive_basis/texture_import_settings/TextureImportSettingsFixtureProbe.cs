using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Editor;

public static class TextureImportSettingsFixtureProbe
{
    private const string ProbeFolder = "Assets/VRCForge/Generated/TextureImportSettingsProbe";
    private const string TexturePath = ProbeFolder + "/real-texture.png";

    public static void Run()
    {
        try
        {
            Require(Application.platform == RuntimePlatform.WindowsEditor, "fixture requires Windows editor");
            CleanupBestEffort();
            VerifyStructuredMutationFailureSignals();
            CreateTexture();

            var projectPath = CurrentProjectPath();
            var sourcePath = AbsoluteAssetPath(TexturePath);
            var metaPath = sourcePath + ".meta";
            var sourceBefore = Sha256(sourcePath);
            var metaBefore = Sha256(metaPath);

            var preview = RequireSuccess(TextureImportSettingsTool.HandleCommand(Request(projectPath, true)));
            Require((bool)preview["preview"], "preview flag");
            Require(!(bool)preview["changed"], "preview changed");
            Require(!(bool)preview["saved"], "preview saved");
            Require(!(bool)preview["reimported"], "preview reimported");
            Require((bool)preview["wouldChange"], "preview wouldChange");
            Require((string)preview["sourceFileDigestBefore"] == sourceBefore, "preview source receipt");
            Require((string)preview["sourceFileDigestAfter"] == sourceBefore, "preview source after");
            Require((string)preview["metaFileDigestBefore"] == metaBefore, "preview meta receipt");
            Require((string)preview["metaFileDigestAfter"] == metaBefore, "preview meta after");
            Require(Sha256(sourcePath) == sourceBefore, "preview source bytes");
            Require(Sha256(metaPath) == metaBefore, "preview meta bytes");
            VerifySourceWriteDeniedByApplyLease(sourcePath, sourceBefore);

            var apply = RequireSuccess(
                TextureImportSettingsTool.HandleCommand(ApprovedRequest(projectPath, preview))
            );
            Require(!(bool)apply["preview"], "apply preview flag");
            Require((bool)apply["changed"], "apply changed");
            Require((bool)apply["saved"], "apply saved");
            Require((bool)apply["reimported"], "apply reimported");
            Require((bool)apply["verified"], "apply verified");
            Require(Sha256(sourcePath) == sourceBefore, "apply source bytes");
            Require(Sha256(metaPath) != metaBefore, "apply meta bytes");
            VerifyImporterReadback();

            var metaAfterApply = Sha256(metaPath);
            var noOpPreview = RequireSuccess(
                TextureImportSettingsTool.HandleCommand(Request(projectPath, true))
            );
            Require(!(bool)noOpPreview["wouldChange"], "no-op preview wouldChange");
            var noOpApply = RequireSuccess(
                TextureImportSettingsTool.HandleCommand(ApprovedRequest(projectPath, noOpPreview))
            );
            Require(!(bool)noOpApply["changed"], "no-op changed");
            Require(!(bool)noOpApply["saved"], "no-op saved");
            Require(!(bool)noOpApply["reimported"], "no-op reimported");
            Require(Sha256(metaPath) == metaAfterApply, "no-op meta bytes");

            var staleRequest = ApprovedRequest(projectPath, noOpPreview);
            staleRequest["expectedMetaFileDigest"] = new string('d', 64);
            var stale = JObject.FromObject(TextureImportSettingsTool.HandleCommand(staleRequest));
            Require(!(bool)stale["success"], "stale precondition accepted");
            Require(Sha256(sourcePath) == sourceBefore, "stale source bytes");
            Require(Sha256(metaPath) == metaAfterApply, "stale meta bytes");
            VerifyImporterReadback();

            VerifyMetadataHardlinkRejected(projectPath, sourcePath, metaPath, sourceBefore, metaAfterApply);
            Require(AssetDatabase.DeleteAsset(ProbeFolder), "fixture cleanup failed");
            Require(!File.Exists(sourcePath), "fixture source residue");
            Require(!File.Exists(metaPath), "fixture metadata residue");

            Debug.Log("VRCFORGE_TEXTURE_IMPORT_SETTINGS_PROBE_OK");
            EditorApplication.Exit(0);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            CleanupBestEffort();
            EditorApplication.Exit(1);
        }
    }

    private static JObject Request(string projectPath, bool preview)
    {
        return new JObject
        {
            ["textureAssetPath"] = TexturePath,
            ["platform"] = "standalone",
            ["maxTextureSize"] = 1024,
            ["format"] = "dxt5_crunched",
            ["compression"] = "high",
            ["crunch"] = true,
            ["quality"] = 82,
            ["preview"] = preview,
            ["saveAndReimport"] = !preview,
            ["expectedProjectPath"] = projectPath
        };
    }

    private static JObject ApprovedRequest(string projectPath, JObject preview)
    {
        var request = Request(projectPath, false);
        request["expectedTextureAssetPath"] = preview["textureAssetPath"];
        request["expectedTextureAssetGuid"] = preview["textureAssetGuid"];
        request["expectedSourceFileDigest"] = preview["sourceFileDigestBefore"];
        request["expectedSourceFileIdentityDigest"] = preview["sourceFileIdentityDigest"];
        request["expectedMetaFileDigest"] = preview["metaFileDigestBefore"];
        request["expectedMetaFileIdentityDigest"] = preview["metaFileIdentityDigest"];
        request["expectedImporterType"] = preview["importerType"];
        request["expectedImporterSettingsDigest"] = preview["importerSettingsDigestBefore"];
        request["expectedTargetSettingsDigest"] = preview["targetSettingsDigest"];
        return request;
    }

    private static JObject RequireSuccess(object response)
    {
        var serialized = JObject.FromObject(response);
        Require((bool)serialized["success"], "tool returned an error response");
        return (JObject)serialized["data"];
    }

    private static void VerifyImporterReadback()
    {
        var importer = AssetImporter.GetAtPath(TexturePath) as TextureImporter;
        Require(importer != null, "readback importer");
        var settings = importer.GetPlatformTextureSettings("Standalone");
        Require(settings.name == "Standalone", "readback platform");
        Require(settings.overridden, "readback override");
        Require(!settings.ignorePlatformSupport, "readback support guard");
        Require(settings.maxTextureSize == 1024, "readback max size");
        Require(settings.format == TextureImporterFormat.DXT5Crunched, "readback format");
        Require(
            settings.textureCompression == TextureImporterCompression.CompressedHQ,
            "readback compression"
        );
        Require(settings.crunchedCompression, "readback crunch");
        Require(settings.compressionQuality == 82, "readback quality");
        Require(!EditorUtility.IsDirty(importer), "readback dirty");
    }

    private static void VerifySourceWriteDeniedByApplyLease(string sourcePath, string sourceDigest)
    {
        var flags = BindingFlags.NonPublic | BindingFlags.Static;
        var captureIdentity = typeof(TextureImportSettingsTool).GetMethod("CaptureFileIdentity", flags);
        var holdStableFile = typeof(TextureImportSettingsTool).GetMethod("HoldStableFile", flags);
        Require(captureIdentity != null, "source identity helper");
        Require(holdStableFile != null, "source lease helper");

        var identity = captureIdentity.Invoke(null, new object[] { sourcePath });
        var lease = holdStableFile.Invoke(
            null,
            new object[] { sourcePath, identity, sourceDigest, false }
        ) as IDisposable;
        Require(lease != null, "source lease acquisition");
        var writeDenied = false;
        try
        {
            try
            {
                using (
                    var writeAttempt = new FileStream(
                        sourcePath,
                        FileMode.Open,
                        FileAccess.Write,
                        FileShare.ReadWrite | FileShare.Delete
                    )
                )
                {
                }
            }
            catch (IOException)
            {
                writeDenied = true;
            }
            catch (UnauthorizedAccessException)
            {
                writeDenied = true;
            }
        }
        finally
        {
            lease.Dispose();
        }
        Require(writeDenied, "source lease allowed concurrent write");
        Require(Sha256(sourcePath) == sourceDigest, "source lease changed bytes");
    }

    private static void VerifyMetadataHardlinkRejected(
        string projectPath,
        string sourcePath,
        string metaPath,
        string sourceDigest,
        string metaDigest
    )
    {
        var outsideMetaLink = Path.Combine(projectPath, "texture-import-settings-meta-hardlink.probe");
        if (File.Exists(outsideMetaLink))
        {
            File.Delete(outsideMetaLink);
        }
        Require(CreateHardLinkW(outsideMetaLink, metaPath, IntPtr.Zero), "create metadata hardlink");
        try
        {
            var linked = JObject.FromObject(
                TextureImportSettingsTool.HandleCommand(Request(projectPath, true))
            );
            Require(!(bool)linked["success"], "metadata hardlink accepted");
            Require(Sha256(sourcePath) == sourceDigest, "hardlink source bytes");
            Require(Sha256(metaPath) == metaDigest, "hardlink meta bytes");
        }
        finally
        {
            File.Delete(outsideMetaLink);
        }
    }

    private static void VerifyStructuredMutationFailureSignals()
    {
        var builder = typeof(TextureImportSettingsTool).GetMethod(
            "BuildMutationFailure",
            BindingFlags.NonPublic | BindingFlags.Static
        );
        Require(builder != null, "mutation failure builder");

        var restoreRequired = JObject.FromObject(builder.Invoke(null, new object[] { false }));
        Require(!(bool)restoreRequired["success"], "restore-required success flag");
        var restoreRequiredData = (JObject)restoreRequired["data"];
        Require((bool)restoreRequiredData["mutationStarted"], "restore-required mutation flag");
        Require(!(bool)restoreRequiredData["restored"], "restore-required restored flag");
        Require((bool)restoreRequiredData["cleanupRequired"], "restore-required cleanup flag");
        Require(
            (bool)restoreRequiredData["checkpointRestoreRequired"],
            "restore-required checkpoint flag"
        );
        Require(
            (string)restoreRequiredData["operationState"] == "checkpoint_restore_required",
            "restore-required operation state"
        );

        var restored = JObject.FromObject(builder.Invoke(null, new object[] { true }));
        Require(!(bool)restored["success"], "restored success flag");
        var restoredData = (JObject)restored["data"];
        Require((bool)restoredData["mutationStarted"], "restored mutation flag");
        Require((bool)restoredData["restored"], "restored state flag");
        Require(!(bool)restoredData["cleanupRequired"], "restored cleanup flag");
        Require(!(bool)restoredData["checkpointRestoreRequired"], "restored checkpoint flag");
        Require((string)restoredData["operationState"] == "restored", "restored operation state");
    }

    private static void CreateTexture()
    {
        Directory.CreateDirectory(ProbeFolder);
        var texture = new Texture2D(4, 4, TextureFormat.RGBA32, false);
        var pixels = new Color[16];
        for (var index = 0; index < pixels.Length; index++)
        {
            pixels[index] = index % 2 == 0 ? Color.magenta : Color.cyan;
        }
        texture.SetPixels(pixels);
        texture.Apply(false, false);
        File.WriteAllBytes(TexturePath, texture.EncodeToPNG());
        UnityEngine.Object.DestroyImmediate(texture);
        AssetDatabase.ImportAsset(TexturePath, ImportAssetOptions.ForceSynchronousImport);
    }

    private static string CurrentProjectPath()
    {
        return Path.GetFullPath(Path.Combine(Application.dataPath, ".."))
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
    }

    private static string AbsoluteAssetPath(string assetPath)
    {
        return Path.Combine(
            CurrentProjectPath(),
            assetPath.Replace('/', Path.DirectorySeparatorChar)
        );
    }

    private static void CleanupBestEffort()
    {
        try
        {
            var outsideMetaLink = Path.Combine(
                CurrentProjectPath(),
                "texture-import-settings-meta-hardlink.probe"
            );
            if (File.Exists(outsideMetaLink))
            {
                File.Delete(outsideMetaLink);
            }
            if (AssetDatabase.IsValidFolder(ProbeFolder))
            {
                AssetDatabase.DeleteAsset(ProbeFolder);
            }
        }
        catch (Exception)
        {
        }
    }

    private static string Sha256(string path)
    {
        using (var sha256 = SHA256.Create())
        using (var stream = File.OpenRead(path))
        {
            return BitConverter.ToString(sha256.ComputeHash(stream)).Replace("-", string.Empty)
                .ToLowerInvariant();
        }
    }

    private static void Require(bool value, string label)
    {
        if (!value)
        {
            throw new InvalidOperationException("Probe failed: " + label);
        }
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CreateHardLinkW(
        string newFileName,
        string existingFileName,
        IntPtr securityAttributes
    );
}
