using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor.MCP
{
    /// <summary>
    /// Removes only byte-identical VRCForge-owned files retired by the 1.4 MCP
    /// contract migration. Unknown content is preserved and reported.
    /// </summary>
    [InitializeOnLoad]
    internal static class VRCForgeMcpSourceMigration
    {
        private const string OwnedAssetRoot = "Assets/VRCForge";

        // Keys are SHA-256 digests of the exact, case-sensitive retired asset
        // paths. Keeping path identities as digests lets the release source
        // remove byte-verified VRCForge migration residue without redistributing
        // retired contract or notice names.
        private static readonly IDictionary<string, ISet<string>> RetiredPaths =
            new Dictionary<string, ISet<string>>(StringComparer.Ordinal)
            {
                ["06A0A6E9AF8F3BA602150CCF40BFE2883A6CB42CCA72DB302B5A696128F5072D"] = Digests(
                    "661F97C32ED6301251978E9F056AC24364846B24748E5DECC5478E9E182FC387",
                    "57BF3C5AEE4001E6E1E3FA8954522653FE066EECE83EECEE0636590CD12C545E"),
                ["E07EA03AB464FDAC0B9342ECDB132142F6FE775E3DA14294D2D6AD739F852CE7"] = Digests(
                    "0D2B7D2E437FAAF0C818F49BB17C343E85415B7B5B01C8F1A4CACE3F729B980D",
                    "B7260D61FC9A5577A64F4CA77AB0A20F42D997284F58558439A3321076C55594"),
                ["74D2CFA536AC87771411D736553774D57A6ABEB2924572CD594F4757D6D54811"] = Digests(
                    "5A15577B89BE2ED70D5F814A435A4C7D10EE1B107BE646289ED22090B0CA0BB2",
                    "78C6A8582AF52592DC4DD07B7D1E5B9828B8549B131C8DFC447CD551BFFB1215"),
                ["4A3F43EB1B304A904B9A4B31EC5EB674D3EA04B93EDDA1662E9B52EFAD6E8721"] = Digests(
                    "E9A9EAAC01F49607094069F8828C0CE7D9C18FF276148B1A5B6875612C0C3031",
                    "F503BC31361909734810DFB882664A16D33C38D61834FB6912DC86B436B00BBF"),
                ["5C8589BBC60A3DF3266E59E29681596563BDBBF8D30A74032919E8ECF9DDFA34"] = Digests(
                    "CAB7DA74668A865517419BCFB8176CDB3B65011AA4814E6F3A648D47F57BAE1D"),
                ["4E100D2330FA9B3E5344F541BDD98E4D7B5249A2AC6446595FE6EC8B2B9EA130"] = Digests(
                    "6EFE650C965012AC418238DCD6B9116E4130A5220717EF0DFB539DD159C4245C"),
                ["8B4F83860B0177FD4FC4E22ABE43A0032DBA119ACC392E4D8876486D9AAB2631"] = Digests(
                    "D0F1C4813C64890E1E41F66CA29A5F5348FFEF0BD97EC31B9CACA2D30E6135E3"),
            };

        private static readonly IDictionary<string, ISet<string>> RetiredEmptyFolderPaths =
            new Dictionary<string, ISet<string>>(StringComparer.Ordinal)
            {
                ["D844485412759025DE25DD7494062859B2166AF5C9F54395BED372EDC3BE0F7B"] = Digests(
                    "2781498AB289A981376761B6F8EAAB8FEB348A7CE6B34022AF35691B9D7D0912"),
                ["18332AF1340DC173F4C14CE959E8990ED50538E23B7507AC3017D8BF3905E5AA"] = Digests(
                    "72E813881522CCDF1168FB11D12733D0E1695A60B092BEC2670E2377D3AFF5C6",
                    "51FAE38369E8C111474D1E57CAD876E0A34053103A8C15AA9752131CE5D6F827"),
                ["0550D8394B8723EDCD9335991436F95582C1A4B38E14CF38F73121A82BD6C453"] = Digests(
                    "DC27BE4C0BDBA2103DCDAB6E0993A535D9672DD37F616BD66B71F13CBBD16902",
                    "7C17CF471A29C0B3F9651A2201C17DEFB3200389B6EAA139532FC7C673D144DD"),
                ["DA6406A00C4E819A982D615E17B3AD977459C2A8AE230FFF6816B3689757BDA5"] = Digests(
                    "D31E5A012347BC4797CABAF79CF50B83F2A92BE7BE631349165CEC8054FA9E8A",
                    "8DFC78A36DF5A97080EA95B4B1C04125F4CB7ACC992ED6E0CF6E964780C5C8CC"),
            };

        // Exact hashes of the generated legacy .meta bytes. No retired GUID or
        // source name is carried in the package; only byte-identical orphan
        // metadata at an exact retired path can be removed.
        private static readonly IDictionary<string, ISet<string>> RetiredMetaPaths =
            new Dictionary<string, ISet<string>>(StringComparer.Ordinal)
            {
                ["06A0A6E9AF8F3BA602150CCF40BFE2883A6CB42CCA72DB302B5A696128F5072D"] = Digests(
                    "937E112EAA7F90BE6B94E852A450D1C5B41FD1888FB3493036C9F6616426BA3E"),
                ["E07EA03AB464FDAC0B9342ECDB132142F6FE775E3DA14294D2D6AD739F852CE7"] = Digests(
                    "C0C9878C0167CAD7DC90864BB4FEFB5AB55AFD6B8379F624757D70438FDB6C6B"),
                ["74D2CFA536AC87771411D736553774D57A6ABEB2924572CD594F4757D6D54811"] = Digests(
                    "29F75292920304BFF7F3A76D1C3197338B4FADC855D4DEC858CBE6B61391834D"),
                ["4A3F43EB1B304A904B9A4B31EC5EB674D3EA04B93EDDA1662E9B52EFAD6E8721"] = Digests(
                    "BBC941A41ABF64ED55E64E800A3B574A68099ED89E7A854E9F862117DA2DE681",
                    "E162A0BC8DA0668839F1E38E29A04BE1CF1108A1F06F7AA66B629B6BFE8CEA87"),
                ["5C8589BBC60A3DF3266E59E29681596563BDBBF8D30A74032919E8ECF9DDFA34"] = Digests(
                    "2EEF02BAD30A9A8A9B98137B145E52EE9E0CC77CF6E04C977802D07827DD454F"),
                ["4E100D2330FA9B3E5344F541BDD98E4D7B5249A2AC6446595FE6EC8B2B9EA130"] = Digests(
                    "F5DC3A2D7BF40608129FCC7822309C63EEE35E93976DDAE7DD2E5074E0EC52DC"),
                ["8B4F83860B0177FD4FC4E22ABE43A0032DBA119ACC392E4D8876486D9AAB2631"] = Digests(
                    "14023570F4A64FE0873466B5C91CF3D725AF1180FFB5E29330B5D77039029BC2"),
                ["D844485412759025DE25DD7494062859B2166AF5C9F54395BED372EDC3BE0F7B"] = Digests(
                    "2781498AB289A981376761B6F8EAAB8FEB348A7CE6B34022AF35691B9D7D0912"),
                ["18332AF1340DC173F4C14CE959E8990ED50538E23B7507AC3017D8BF3905E5AA"] = Digests(
                    "72E813881522CCDF1168FB11D12733D0E1695A60B092BEC2670E2377D3AFF5C6",
                    "51FAE38369E8C111474D1E57CAD876E0A34053103A8C15AA9752131CE5D6F827"),
                ["0550D8394B8723EDCD9335991436F95582C1A4B38E14CF38F73121A82BD6C453"] = Digests(
                    "DC27BE4C0BDBA2103DCDAB6E0993A535D9672DD37F616BD66B71F13CBBD16902",
                    "7C17CF471A29C0B3F9651A2201C17DEFB3200389B6EAA139532FC7C673D144DD"),
                ["DA6406A00C4E819A982D615E17B3AD977459C2A8AE230FFF6816B3689757BDA5"] = Digests(
                    "D31E5A012347BC4797CABAF79CF50B83F2A92BE7BE631349165CEC8054FA9E8A",
                    "8DFC78A36DF5A97080EA95B4B1C04125F4CB7ACC992ED6E0CF6E964780C5C8CC"),
            };

        static VRCForgeMcpSourceMigration()
        {
            EditorApplication.delayCall -= RemoveRetiredAssets;
            EditorApplication.delayCall += RemoveRetiredAssets;
        }

        private static ISet<string> Digests(params string[] values)
        {
            return new HashSet<string>(values, StringComparer.Ordinal);
        }

        private static void RemoveRetiredAssets()
        {
            EditorApplication.delayCall -= RemoveRetiredAssets;
            var projectRoot = Directory.GetParent(Application.dataPath)?.FullName;
            if (string.IsNullOrWhiteSpace(projectRoot))
            {
                Debug.LogWarning("[VRCForge] MCP source migration could not resolve the project root.");
                return;
            }

            var removed = new List<string>();
            var editing = false;
            try
            {
                AssetDatabase.StartAssetEditing();
                editing = true;
                ScanOwnedTree(projectRoot, OwnedAssetRoot, removed);
            }
            catch (Exception exception)
            {
                Debug.LogWarning($"[VRCForge] MCP source migration did not complete: {exception.Message}");
            }
            finally
            {
                if (editing)
                {
                    try
                    {
                        AssetDatabase.StopAssetEditing();
                    }
                    catch (Exception exception)
                    {
                        Debug.LogWarning($"[VRCForge] MCP source migration could not finish asset editing: {exception.Message}");
                    }
                }
            }

            if (removed.Count > 0)
            {
                Debug.Log($"[VRCForge] Removed {removed.Count} byte-verified retired asset(s). Import migration complete.");
            }
        }

        private static void ScanOwnedTree(
            string projectRoot,
            string assetDirectory,
            ICollection<string> removed)
        {
            string fullDirectory;
            try
            {
                fullDirectory = ResolveOwnedAsset(projectRoot, assetDirectory);
                if (!Directory.Exists(fullDirectory))
                {
                    return;
                }
            }
            catch (Exception exception)
            {
                Debug.LogWarning($"[VRCForge] Preserved migration directory because it could not be verified: {assetDirectory} ({exception.Message})");
                return;
            }

            string[] files;
            string[] directories;
            try
            {
                files = Directory.EnumerateFiles(fullDirectory, "*", SearchOption.TopDirectoryOnly)
                    .OrderBy(item => item, StringComparer.Ordinal).ToArray();
                directories = Directory.EnumerateDirectories(fullDirectory, "*", SearchOption.TopDirectoryOnly)
                    .OrderBy(item => item, StringComparer.Ordinal).ToArray();
            }
            catch (Exception exception)
            {
                Debug.LogWarning($"[VRCForge] Preserved migration directory because its entries could not be enumerated: {assetDirectory} ({exception.Message})");
                return;
            }

            foreach (var fullPath in files)
            {
                if (fullPath.EndsWith(".meta", StringComparison.OrdinalIgnoreCase))
                {
                    TryRemoveMatchedOrphanMeta(
                        projectRoot,
                        assetDirectory + "/" + Path.GetFileName(fullPath),
                        removed);
                    continue;
                }
                var assetPath = assetDirectory + "/" + Path.GetFileName(fullPath);
                TryRemoveMatchedAsset(projectRoot, assetPath, removed);
            }

            foreach (var childDirectory in directories)
            {
                ScanOwnedTree(projectRoot, assetDirectory + "/" + Path.GetFileName(childDirectory), removed);
            }

            try
            {
                ISet<string> allowedMetaDigests;
                if (!RetiredEmptyFolderPaths.TryGetValue(
                    ComputeTextSha256(assetDirectory), out allowedMetaDigests))
                {
                    return;
                }
                if (Directory.EnumerateFileSystemEntries(fullDirectory).Any())
                {
                    Debug.LogWarning($"[VRCForge] Preserved non-empty retired folder: {assetDirectory}");
                    return;
                }
                var folderMetaPath = fullDirectory + ".meta";
                if (!File.Exists(folderMetaPath)
                    || !allowedMetaDigests.Contains(ComputeSha256(folderMetaPath)))
                {
                    Debug.LogWarning($"[VRCForge] Preserved retired folder with unknown or modified metadata: {assetDirectory}");
                    return;
                }
                if (AssetDatabase.DeleteAsset(assetDirectory))
                {
                    removed.Add(assetDirectory);
                }
                else
                {
                    Debug.LogWarning($"[VRCForge] Could not remove retired empty folder: {assetDirectory}");
                }
            }
            catch (Exception exception)
            {
                Debug.LogWarning($"[VRCForge] Preserved retired empty folder because it could not be verified: {assetDirectory} ({exception.Message})");
            }
        }

        private static void TryRemoveMatchedAsset(
            string projectRoot,
            string assetPath,
            ICollection<string> removed)
        {
            try
            {
                var fullPath = ResolveOwnedAsset(projectRoot, assetPath);
                ISet<string> allowedDigests;
                if (!RetiredPaths.TryGetValue(ComputeTextSha256(assetPath), out allowedDigests))
                {
                    var digest = ComputeSha256(fullPath);
                    var separator = assetPath.LastIndexOf('/');
                    var parentPath = separator > 0 ? assetPath.Substring(0, separator) : string.Empty;
                    if (RetiredPaths.Values.Any(values => values.Contains(digest))
                        || RetiredEmptyFolderPaths.ContainsKey(ComputeTextSha256(parentPath)))
                    {
                        Debug.LogWarning($"[VRCForge] Preserved unknown or renamed retired asset: {assetPath}");
                    }
                    return;
                }
                TryRemoveVerifiedAsset(projectRoot, assetPath, allowedDigests, removed);
            }
            catch (Exception exception)
            {
                Debug.LogWarning($"[VRCForge] Preserved migration candidate because it could not be verified: {assetPath} ({exception.Message})");
            }
        }

        private static void TryRemoveMatchedOrphanMeta(
            string projectRoot,
            string metaAssetPath,
            ICollection<string> removed)
        {
            try
            {
                var assetPath = metaAssetPath.Substring(0, metaAssetPath.Length - ".meta".Length);
                var fullAssetPath = ResolveOwnedAsset(projectRoot, assetPath);
                if (File.Exists(fullAssetPath) || Directory.Exists(fullAssetPath))
                {
                    return;
                }
                ISet<string> allowedDigests;
                if (!RetiredMetaPaths.TryGetValue(ComputeTextSha256(assetPath), out allowedDigests))
                {
                    return;
                }
                var fullMetaPath = ResolveOwnedAsset(projectRoot, metaAssetPath);
                if (!allowedDigests.Contains(ComputeSha256(fullMetaPath)))
                {
                    Debug.LogWarning($"[VRCForge] Preserved modified retired orphan metadata: {metaAssetPath}");
                    return;
                }
                FileUtil.DeleteFileOrDirectory(fullMetaPath);
                if (File.Exists(fullMetaPath))
                {
                    Debug.LogWarning($"[VRCForge] Could not remove retired orphan metadata: {metaAssetPath}");
                    return;
                }
                removed.Add(metaAssetPath);
            }
            catch (Exception exception)
            {
                Debug.LogWarning($"[VRCForge] Preserved retired orphan metadata because it could not be verified: {metaAssetPath} ({exception.Message})");
            }
        }

        private static void TryRemoveVerifiedAsset(
            string projectRoot,
            string assetPath,
            ISet<string> allowedDigests,
            ICollection<string> removed)
        {
            try
            {
                var fullPath = ResolveOwnedAsset(projectRoot, assetPath);
                if (!File.Exists(fullPath))
                {
                    return;
                }
                if (!allowedDigests.Contains(ComputeSha256(fullPath)))
                {
                    Debug.LogWarning($"[VRCForge] Preserved modified retired asset: {assetPath}");
                    return;
                }
                if (!AssetDatabase.DeleteAsset(assetPath))
                {
                    Debug.LogWarning($"[VRCForge] Could not remove retired asset: {assetPath}");
                    return;
                }
                removed.Add(assetPath);
            }
            catch (Exception exception)
            {
                Debug.LogWarning($"[VRCForge] Preserved retired asset because it could not be verified: {assetPath} ({exception.Message})");
            }
        }

        private static string ResolveOwnedAsset(string projectRoot, string assetPath)
        {
            var assetsRoot = Path.GetFullPath(Path.Combine(projectRoot, "Assets"))
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                + Path.DirectorySeparatorChar;
            var fullPath = Path.GetFullPath(Path.Combine(projectRoot, assetPath.Replace('/', Path.DirectorySeparatorChar)));
            if (!fullPath.StartsWith(assetsRoot, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("Retired MCP asset escaped the project Assets root.");
            }
            RejectReparsePoints(
                assetsRoot.TrimEnd(new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar }),
                fullPath);
            return fullPath;
        }

        private static void RejectReparsePoints(string assetsRoot, string fullPath)
        {
            var current = assetsRoot;
            VerifyNotReparsePoint(current);
            var relative = fullPath.Substring(assetsRoot.Length).TrimStart(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar);
            foreach (var segment in relative.Split(
                new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar },
                StringSplitOptions.RemoveEmptyEntries))
            {
                current = Path.Combine(current, segment);
                VerifyNotReparsePoint(current);
            }
        }

        private static void VerifyNotReparsePoint(string path)
        {
            if ((File.Exists(path) || Directory.Exists(path))
                && (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            {
                throw new InvalidOperationException("Retired MCP asset path contains a reparse point.");
            }
        }

        private static string ComputeSha256(string path)
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var sha256 = SHA256.Create())
            {
                return BitConverter.ToString(sha256.ComputeHash(stream)).Replace("-", string.Empty);
            }
        }

        private static string ComputeTextSha256(string value)
        {
            using (var sha256 = SHA256.Create())
            {
                return BitConverter.ToString(sha256.ComputeHash(Encoding.UTF8.GetBytes(value)))
                    .Replace("-", string.Empty);
            }
        }
    }
}
