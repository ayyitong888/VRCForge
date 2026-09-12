using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    internal static class UnityProjectAssetCopyBatch
    {
        private const int MaxBytes = 512 * 1024;

        internal static JArray ValidateEnvelope(JObject request)
        {
            var allowed = new[] { "copies", "preview", "overwrite", "expectedProjectPath", "expectedPreviewDigest", "expectedCopies" };
            if (request.Properties().Any(p => !allowed.Contains(p.Name)) || request["overwrite"]?.Value<bool>() == true)
                throw new InvalidOperationException("Batch copy does not accept single-copy fields or overwrite.");
            CheckSize(request);
            var rows = request["copies"] as JArray;
            if (rows == null || rows.Count < 1 || rows.Count > 128) throw new InvalidOperationException("copies requires 1..128 rows.");
            foreach (var token in rows)
            {
                var row = token as JObject;
                if (row == null || row.Properties().Count() != 2 || row["sourceAssetPath"]?.Type != JTokenType.String || row["destinationAssetPath"]?.Type != JTokenType.String)
                    throw new InvalidOperationException("Each copy requires only sourceAssetPath and destinationAssetPath.");
                row["sourceAssetPath"] = DuplicateProjectAssetTool.NormalizeSourcePath(row["sourceAssetPath"].Value<string>());
                row["destinationAssetPath"] = DuplicateProjectAssetTool.NormalizeDestinationPath(row["destinationAssetPath"].Value<string>());
            }
            ValidatePaths(rows);
            return (JArray)rows.DeepClone();
        }

        internal static void ValidatePaths(JArray rows)
        {
            var sources = new HashSet<string>(rows.Select(r => r["sourceAssetPath"].Value<string>()), StringComparer.OrdinalIgnoreCase);
            var destinations = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var row in rows)
            {
                var path = row["destinationAssetPath"].Value<string>();
                if (!destinations.Add(path) || sources.Contains(path)) throw new InvalidOperationException("Copy destinations conflict or intersect batch sources.");
            }
        }

        internal static string Digest(JArray previews)
        {
            var text = "vrcforge.project_asset_copy_batch.v1:" + string.Join("", previews.Select(p => p["previewDigest"].Value<string>()));
            using (var sha = SHA256.Create()) return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(text)).Select(b => b.ToString("x2")));
        }

        private static void CheckSize(JToken value)
        {
            if (Encoding.UTF8.GetByteCount(value.ToString(Formatting.None)) > MaxBytes) throw new InvalidOperationException("Sealed copy batch exceeds 512 KiB.");
        }

        // Continue cleanup after one changed entry; never broaden ownership to a path alone.
        internal static bool CleanupOwned(IList<string> attempted, IDictionary<string, StableAssetEvidence> owned)
        {
            var restored = true;
            for (var i = attempted.Count - 1; i >= 0; i--)
            {
                var path = attempted[i];
                try
                {
                    if (!SceneObjectCopyCore.AssetOrMetaExists(path)) continue;
                    StableAssetEvidence evidence;
                    if (!owned.TryGetValue(path, out evidence) || !SceneObjectCopyCore.DeleteOwnedAsset(path, evidence)) restored = false;
                }
                catch { restored = false; }
            }
            return restored;
        }

        internal static object HandleCommand(JObject request)
        {
            var attempted = new List<string>();
            var owned = new Dictionary<string, StableAssetEvidence>(StringComparer.Ordinal);
            var failurePhase = "preflight";
            int? rowIndex = null;
            string sourcePath = null, destinationPath = null;
            JObject failureDetails = null;
            try
            {
                var rows = ValidateEnvelope((JObject)request.DeepClone());
                var snapshots = rows.Select((row, index) =>
                {
                    rowIndex = index;
                    sourcePath = row["sourceAssetPath"].Value<string>();
                    destinationPath = row["destinationAssetPath"].Value<string>();
                    return DuplicateProjectAssetTool.BuildSnapshot(sourcePath, destinationPath);
                }).ToList();
                rowIndex = null; sourcePath = null; destinationPath = null;
                if (snapshots.Any(s => s.MissingFolders.Length != 0)) throw new InvalidOperationException("Every batch destination parent must already exist.");
                var previews = new JArray(snapshots.Select(s => JObject.FromObject(s.ToPreviewPayload())));
                var digest = Digest(previews);
                var preview = new JObject { ["schema"] = DuplicateProjectAssetTool.ResultSchema, ["operation"] = DuplicateProjectAssetTool.Operation,
                    ["batch"] = true, ["ok"] = true, ["preview"] = true, ["verified"] = true, ["changed"] = false, ["saved"] = false,
                    ["cleanupRequired"] = false, ["mutationCount"] = 0, ["copies"] = previews, ["previewDigest"] = digest };
                CheckSize(preview);
                if (request["preview"]?.Value<bool>() == true) return VRCForgeToolResult.Completed("Preview: bounded independent asset copies; all parents exist.", preview);
                if (!SceneObjectCopyCore.MatchesCurrentProject(request["expectedProjectPath"]?.Value<string>() ?? "") || request["expectedPreviewDigest"]?.Value<string>() != digest)
                    throw new InvalidOperationException("The sealed batch copy preview changed before apply.");
                if (!JToken.DeepEquals(request["expectedCopies"], previews)) throw new InvalidOperationException("Sealed batch snapshot differs from current preview.");
                foreach (var snapshot in snapshots)
                {
                    rowIndex = snapshots.IndexOf(snapshot); sourcePath = snapshot.SourcePath; destinationPath = snapshot.DestinationPath;
                    DuplicateProjectAssetTool.VerifySnapshotCurrent(snapshot);
                }
                foreach (var snapshot in snapshots)
                {
                    rowIndex = snapshots.IndexOf(snapshot); sourcePath = snapshot.SourcePath; destinationPath = snapshot.DestinationPath;
                    failurePhase = "copy";
                    DuplicateProjectAssetTool.VerifySnapshotCurrent(snapshot);
                    attempted.Add(snapshot.DestinationPath);
                    if (!AssetDatabase.CopyAsset(snapshot.SourcePath, snapshot.DestinationPath)) throw new InvalidOperationException("Unity failed to copy " + snapshot.DestinationPath);
                    owned.Add(snapshot.DestinationPath, DuplicateProjectAssetTool.ReadCreatedEvidenceWithRetry(snapshot.DestinationPath));
                }
                failurePhase = "save_assets";
                rowIndex = null; sourcePath = null; destinationPath = null;
                AssetDatabase.SaveAssets();
                var results = new JArray();
                var guids = new HashSet<string>(snapshots.Select(s => s.SourceEvidence.Guid), StringComparer.Ordinal);
                foreach (var snapshot in snapshots)
                {
                    rowIndex = snapshots.IndexOf(snapshot); sourcePath = snapshot.SourcePath; destinationPath = snapshot.DestinationPath;
                    failurePhase = "persisted_readback";
                    AssetDatabase.ImportAsset(snapshot.DestinationPath, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                    var current = DuplicateProjectAssetTool.ReadCreatedEvidenceWithRetry(snapshot.DestinationPath);
                    var type = AssetDatabase.GetMainAssetTypeAtPath(snapshot.DestinationPath);
                    var layout = DuplicateProjectAssetTool.ComputeObjectLayoutDigest(snapshot.DestinationPath);
                    var guidAvailable = !guids.Contains(current.Guid);
                    if (!SceneObjectCopyCore.StableAssetEvidenceMatches(owned[snapshot.DestinationPath], current, true) || !guids.Add(current.Guid)
                        || current.File.LinkCount != 1 || current.Meta.LinkCount != 1 || type == null || (type.FullName ?? type.Name) != snapshot.SourceMainAssetType || layout != snapshot.SourceObjectLayoutDigest)
                    {
                        failureDetails = DescribeReadbackFailure(owned[snapshot.DestinationPath], current, guidAvailable,
                            snapshot.SourceMainAssetType, type == null ? null : (type.FullName ?? type.Name), snapshot.SourceObjectLayoutDigest, layout);
                        throw new InvalidOperationException("Copied asset failed independent persisted identity/type/layout readback.");
                    }
                    failurePhase = "source_and_parent_readback";
                    DuplicateProjectAssetTool.VerifySourceUnchanged(snapshot);
                    SceneObjectCopyCore.VerifyFolderIdentity(snapshot.ParentFolderPath, snapshot.ParentFolderGuid, snapshot.ParentFolderIdentity, "batch destination parent");
                    results.Add(JObject.FromObject(new { schema = DuplicateProjectAssetTool.ResultSchema, operation = DuplicateProjectAssetTool.Operation,
                        ok = true, preview = false, verified = true, changed = true, saved = true, cleanupRequired = false, mutationCount = 1,
                        previewDigest = snapshot.PreviewDigest, source = DuplicateProjectAssetTool.SourcePayload(snapshot),
                        target = new { assetPath = snapshot.DestinationPath, guid = current.Guid, fileDigest = current.File.Digest, fileIdentity = current.File.Identity,
                            metaDigest = current.Meta.Digest, metaIdentity = current.Meta.Identity, mainAssetType = snapshot.SourceMainAssetType, objectLayoutDigest = layout,
                            bytesIdenticalToSource = current.File.Digest == snapshot.SourceEvidence.File.Digest, generatedRootPath = DuplicateProjectAssetTool.GeneratedRoot,
                            generatedRootCreated = false, createdFolders = Array.Empty<string>(), createNew = true, readbackVerified = true } }));
                }
                foreach (var snapshot in snapshots)
                {
                    rowIndex = snapshots.IndexOf(snapshot); sourcePath = snapshot.SourcePath; destinationPath = snapshot.DestinationPath;
                    failurePhase = "final_source_and_parent_readback";
                    DuplicateProjectAssetTool.VerifySourceUnchanged(snapshot);
                    SceneObjectCopyCore.VerifyFolderIdentity(snapshot.ParentFolderPath, snapshot.ParentFolderGuid, snapshot.ParentFolderIdentity, "batch final parent");
                    var sourceType = AssetDatabase.GetMainAssetTypeAtPath(snapshot.SourcePath);
                    if (sourceType == null || (sourceType.FullName ?? sourceType.Name) != snapshot.SourceMainAssetType
                        || DuplicateProjectAssetTool.ComputeObjectLayoutDigest(snapshot.SourcePath) != snapshot.SourceObjectLayoutDigest)
                        throw new InvalidOperationException("A source Unity type/layout changed during the batch.");
                    failurePhase = "final_destination_readback";
                    var finalEvidence = DuplicateProjectAssetTool.ReadCreatedEvidenceWithRetry(snapshot.DestinationPath);
                    if (!SceneObjectCopyCore.StableAssetEvidenceMatches(owned[snapshot.DestinationPath], finalEvidence, true))
                    {
                        failureDetails = DescribeReadbackFailure(owned[snapshot.DestinationPath], finalEvidence);
                        throw new InvalidOperationException("A copied asset changed during the final batch readback.");
                    }
                }
                failurePhase = "result_envelope";
                rowIndex = null; sourcePath = null; destinationPath = null;
                var result = new JObject { ["schema"] = DuplicateProjectAssetTool.ResultSchema, ["operation"] = DuplicateProjectAssetTool.Operation, ["batch"] = true,
                    ["ok"] = true, ["preview"] = false, ["verified"] = true, ["changed"] = true, ["saved"] = true, ["cleanupRequired"] = false,
                    ["mutationCount"] = snapshots.Count, ["copies"] = results, ["previewDigest"] = digest };
                CheckSize(result);
                return VRCForgeToolResult.Completed("Created and independently verified every asset copy.", result);
            }
            catch (Exception exception)
            {
                if (failureDetails == null) failureDetails = new JObject();
                failureDetails["rowIndex"] = rowIndex.HasValue ? new JValue(rowIndex.Value) : JValue.CreateNull();
                failureDetails["sourceAssetPath"] = sourcePath;
                failureDetails["destinationAssetPath"] = destinationPath;
                failureDetails["failurePhase"] = failurePhase;
                var restored = CleanupOwned(attempted, owned);
                return VRCForgeToolResult.FailedWithCode("project_asset_copy_batch_failed", exception.Message,
                    new { schema = DuplicateProjectAssetTool.ResultSchema, batch = true, verified = false, ok = false, mutationStarted = attempted.Count > 0,
                        committed = false, saved = false, restored, cleanupRequired = !restored, checkpointRecoveryRequired = !restored,
                        commitState = attempted.Count == 0 ? "not_started" : restored ? "rolled_back" : "unknown", commitStateKnown = restored,
                        failurePhase, failureDetails, attemptedPaths = attempted });
            }
        }

        // Diagnostic projection only: the original rejection and ownership checks remain authoritative.
        internal static JObject DescribeReadbackFailure(StableAssetEvidence expected, StableAssetEvidence actual,
            bool? guidAvailable = null, string expectedType = null, string actualType = null,
            string expectedLayout = null, string actualLayout = null)
        {
            var before = EvidencePayload(expected);
            var after = EvidencePayload(actual);
            var failed = new JArray();
            if (!SceneObjectCopyCore.StableAssetEvidenceMatches(expected, actual, true))
            {
                failed.Add("stableAssetEvidence");
                foreach (var key in new[] { "guid", "fileDigest", "fileIdentity", "fileLinkCount", "metaDigest", "metaIdentity", "metaLinkCount", "metaLength" })
                    if (!JToken.DeepEquals(before[key], after[key])) failed.Add(key);
            }
            if (guidAvailable.HasValue)
            {
                before["guidUniqueAgainstSourcesAndPriorCopies"] = true;
                after["guidUniqueAgainstSourcesAndPriorCopies"] = guidAvailable.Value;
                if (!guidAvailable.Value) failed.Add("guidUniqueAgainstSourcesAndPriorCopies");
                if (actual?.File?.LinkCount != 1) failed.Add("fileSingleLink");
                if (actual?.Meta?.LinkCount != 1) failed.Add("metaSingleLink");
                before["mainAssetType"] = expectedType; after["mainAssetType"] = actualType;
                before["objectLayoutDigest"] = expectedLayout; after["objectLayoutDigest"] = actualLayout;
                if (actualType == null || actualType != expectedType) failed.Add("mainAssetType");
                if (actualLayout != expectedLayout) failed.Add("objectLayoutDigest");
            }
            return new JObject { ["failedPredicates"] = failed, ["expected"] = before, ["actual"] = after };
        }

        private static JObject EvidencePayload(StableAssetEvidence evidence)
        {
            return new JObject
            {
                ["guid"] = evidence?.Guid,
                ["fileDigest"] = evidence?.File?.Digest, ["fileIdentity"] = evidence?.File?.Identity,
                ["fileLinkCount"] = evidence?.File?.LinkCount, ["fileLength"] = evidence?.File?.Length,
                ["metaDigest"] = evidence?.Meta?.Digest, ["metaIdentity"] = evidence?.Meta?.Identity,
                ["metaLinkCount"] = evidence?.Meta?.LinkCount, ["metaLength"] = evidence?.Meta?.Length,
            };
        }
    }
}
