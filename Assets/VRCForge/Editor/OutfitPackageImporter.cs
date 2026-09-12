using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.PackageManager;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [InitializeOnLoad]
    [VRCForgeCommand(
        toolId: "vrc_import_unitypackage",
        Summary = "Import a local .unitypackage through Unity AssetDatabase. Intended for VRCForge supervised outfit imports."
    )]
    public static class UnityPackageImporterTool
    {
        private const string JobSessionPrefix = "VRCForge.UnityPackageImport.Job.";
        private const string ActiveJobSessionKey = "VRCForge.UnityPackageImport.ActiveJob";
        private const int MaxCompletedReadbackFailures = 3;
        private static readonly object JobLock = new object();
        private static readonly Dictionary<string, ImportJob> Jobs = new Dictionary<string, ImportJob>();
        private static string activeJobId = "";
        private static string importInvocationJobId = "";

        private sealed class ImportJob
        {
            public string jobId { get; set; } = "";
            public string projectPath { get; set; } = "";
            public string unityPackagePath { get; set; } = "";
            public string expectedSha256 { get; set; } = "";
            public long expectedSize { get; set; }
            public List<string> expectedAssetPaths { get; set; } = new List<string>();
            public string expectedEventPackageName { get; set; } = "";
            public bool mutationStarted { get; set; }
            public bool startedForThisJob { get; set; }
            public string importEventPackageName { get; set; } = "";
            public string status { get; set; } = "pending";
            public DateTime createdUtc { get; set; } = DateTime.UtcNow;
            public DateTime? completedUtc { get; set; }
            public JObject result { get; set; }
            public bool restoredAfterDomainReload { get; set; }
            public DateTime? restoredUtc { get; set; }
            public string readbackFailurePath { get; set; } = "";
            public string readbackFailureCode { get; set; } = "";
            public string readbackFailureReason { get; set; } = "";
            public DateTime? readbackAttemptedUtc { get; set; }
            public bool importCompletedObserved { get; set; }
            public DateTime? importCompletedObservedUtc { get; set; }
            public string importCompletionEvidence { get; set; } = "";
            public int completedReadbackFailureCount { get; set; }
            public bool selectedItemsObserved { get; set; }
            public List<string> selectedItems { get; set; }
            public DateTime? selectedItemsObservedUtc { get; set; }
        }

        static UnityPackageImporterTool()
        {
            AssetDatabase.importPackageStarted += OnImportStarted;
            AssetDatabase.importPackageCompleted += OnImportCompleted;
            AssetDatabase.importPackageFailed += OnImportFailed;
            AssetDatabase.importPackageCancelled += OnImportCancelled;
            AssetDatabase.onImportPackageItemsCompleted += OnImportPackageItemsCompleted;
            RestorePersistedActiveJob();
        }

        public class ImportUnityPackageParameters
        {
            [VRCForgeInput("Absolute path to the .unitypackage file.", IsRequired = true)]
            public string unityPackagePath { get; set; } = "";

            [VRCForgeInput("Expected active Unity project root.", IsRequired = false)]
            public string projectPath { get; set; } = "";

            [VRCForgeInput("Approval-bound SHA-256 of the UnityPackage bytes.", IsRequired = true)]
            public string expectedSha256 { get; set; } = "";

            [VRCForgeInput("Approval-bound byte length of the UnityPackage.", IsRequired = true)]
            public long expectedSize { get; set; } = -1;

            [VRCForgeInput("Exact Unity asset paths expected after this import and refresh.", IsRequired = false)]
            public List<string> expectedAssetPaths { get; set; } = new List<string>();

            [VRCForgeInput("When true, Unity may show the package import UI. VRCForge uses false.", IsRequired = false)]
            public bool? interactive { get; set; } = false;

            [VRCForgeInput("Existing VRCForge UnityPackage import job id to poll.", IsRequired = false)]
            public string jobId { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<ImportUnityPackageParameters>()
                ?? new ImportUnityPackageParameters();
            var failureCode = "unitypackage_project_preflight_failed";
            try
            {
                if (!string.IsNullOrWhiteSpace(parameters.jobId))
                {
                    var jobState = PollJob(parameters.jobId);
                    return jobState["pending"]?.Value<bool>() == true
                        ? VRCForgeToolResult.Waiting("UnityPackage import is pending in Unity.", 0.5, jobState)
                        : VRCForgeToolResult.Completed("Read UnityPackage import job state.", jobState);
                }
                CheckpointPrepareTool.ValidateProject(@params);
                CheckpointPrepareTool.EnsureEditorReady();

                failureCode = "unitypackage_identity_failed";
                var packagePath = Path.GetFullPath(parameters.unityPackagePath ?? "");
                if (!File.Exists(packagePath))
                {
                    throw new InvalidOperationException($"UnityPackage not found: {packagePath}");
                }
                if (!string.Equals(Path.GetExtension(packagePath), ".unitypackage", StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException("Only .unitypackage files can be imported by this tool.");
                }
                var expectedSha256 = (parameters.expectedSha256 ?? "").Trim().ToLowerInvariant();
                if (expectedSha256.Length != 64 || !IsLowerHex(expectedSha256))
                {
                    throw new InvalidOperationException("expectedSha256 must be exactly 64 hexadecimal characters.");
                }
                if (parameters.expectedSize < 0)
                {
                    throw new InvalidOperationException("expectedSize must be non-negative.");
                }
                var expectedAssetPaths = ValidateExpectedAssetPaths(parameters.expectedAssetPaths);

                // The handle pins the approved bytes while Unity accepts the import request.
                // Managed-peer and one-use execution context remain the authority boundary.
                using (var packageHandle = new FileStream(packagePath, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    if (packageHandle.Length != parameters.expectedSize)
                    {
                        throw new InvalidOperationException("UnityPackage size changed after approval.");
                    }
                    string actualSha256;
                    using (var hasher = SHA256.Create())
                    {
                        actualSha256 = BitConverter.ToString(hasher.ComputeHash(packageHandle)).Replace("-", "").ToLowerInvariant();
                    }
                    if (!string.Equals(actualSha256, expectedSha256, StringComparison.Ordinal))
                    {
                        throw new InvalidOperationException("UnityPackage SHA-256 changed after approval.");
                    }
                    var job = new ImportJob
                    {
                        jobId = Guid.NewGuid().ToString("N"),
                        projectPath = CheckpointPrepareTool.ProjectRoot(),
                        unityPackagePath = packagePath.Replace("\\", "/"),
                        expectedSha256 = expectedSha256,
                        expectedSize = parameters.expectedSize,
                        expectedAssetPaths = expectedAssetPaths,
                        expectedEventPackageName = Path.GetFileNameWithoutExtension(packagePath),
                    };
                    lock (JobLock)
                    {
                        if (!string.IsNullOrEmpty(activeJobId))
                        {
                            throw new InvalidOperationException("Another VRCForge UnityPackage import is still active.");
                        }
                        activeJobId = job.jobId;
                        Jobs[job.jobId] = job;
                        SessionState.SetString(ActiveJobSessionKey, job.jobId);
                    }
                    PersistJob(job);
                    failureCode = "unitypackage_import_failed";
                    job.mutationStarted = true;
                    PersistJob(job);
                    try
                    {
                        lock (JobLock)
                        {
                            importInvocationJobId = job.jobId;
                        }
                        AssetDatabase.ImportPackage(packagePath, parameters.interactive ?? false);
                    }
                    catch
                    {
                        CompleteFailedJob(job, "unitypackage_import_start_failed");
                        throw;
                    }
                    finally
                    {
                        lock (JobLock)
                        {
                            if (string.Equals(importInvocationJobId, job.jobId, StringComparison.Ordinal))
                            {
                                importInvocationJobId = "";
                            }
                        }
                    }
                    lock (JobLock)
                    {
                        if (job.result != null)
                        {
                            return VRCForgeToolResult.Completed(
                                "UnityPackage import reached a terminal state.",
                                (JObject)job.result.DeepClone());
                        }
                    }
                    return VRCForgeToolResult.Waiting(
                        "UnityPackage import is pending in Unity.",
                        0.5,
                        BuildPendingPayload(job));
                }
            }
            catch (Exception)
            {
                return VRCForgeToolResult.Failed(failureCode);
            }
        }

        private static JObject BuildPendingPayload(ImportJob job)
        {
            return new JObject
            {
                ["ok"] = true,
                ["pending"] = true,
                ["status"] = job.status,
                ["jobId"] = job.jobId,
                ["projectPath"] = job.projectPath,
                ["unityPackagePath"] = job.unityPackagePath,
                ["expectedSha256"] = job.expectedSha256,
                ["expectedSize"] = job.expectedSize,
                ["expectedAssetPaths"] = JArray.FromObject(job.expectedAssetPaths),
                ["mutationStarted"] = job.mutationStarted,
                ["startedForThisJob"] = job.startedForThisJob,
                ["restoredAfterDomainReload"] = job.restoredAfterDomainReload,
                ["expectedAssetCount"] = job.expectedAssetPaths?.Count ?? 0,
                ["readbackFailurePath"] = job.readbackFailurePath,
                ["readbackFailureCode"] = job.readbackFailureCode,
                ["readbackFailureReason"] = job.readbackFailureReason,
                ["readbackAttemptedUtc"] = job.readbackAttemptedUtc?.ToString("O"),
                ["importCompletedObserved"] = job.importCompletedObserved,
                ["importCompletedObservedUtc"] = job.importCompletedObservedUtc?.ToString("O"),
                ["importCompletionEvidence"] = job.importCompletionEvidence,
                ["completedReadbackFailureCount"] = job.completedReadbackFailureCount,
                ["selectedItemsEvidence"] = BuildSelectedItemsEvidence(job),
                ["createdUtc"] = job.createdUtc.ToString("O"),
            };
        }

        private static JObject PollJob(string rawJobId)
        {
            var jobId = (rawJobId ?? "").Trim().ToLowerInvariant();
            Guid parsed;
            if (!Guid.TryParseExact(jobId, "N", out parsed))
            {
                throw new InvalidOperationException("jobId is invalid.");
            }
            ImportJob activeJob = null;
            lock (JobLock)
            {
                Jobs.TryGetValue(jobId, out activeJob);
            }
            if (activeJob != null)
            {
                TryCompletePendingReadback(activeJob);
                lock (JobLock)
                {
                    return activeJob.result == null
                        ? BuildPendingPayload(activeJob)
                        : (JObject)activeJob.result.DeepClone();
                }
            }
            var persisted = LoadPersistedJob(jobId);
            if (persisted != null)
            {
                var result = persisted["result"] as JObject;
                if (result != null)
                {
                    return (JObject)result.DeepClone();
                }
                var mutationStarted = persisted["mutationStarted"]?.Value<bool>() == true;
                return new JObject
                {
                    ["ok"] = false,
                    ["pending"] = false,
                    ["status"] = "unavailable",
                    ["jobId"] = jobId,
                    ["reason"] = "editor_reloaded_during_unitypackage_import",
                    ["retryable"] = false,
                    ["mutationStarted"] = mutationStarted,
                    ["committed"] = mutationStarted,
                    ["commitState"] = mutationStarted ? "unknown" : "not_started",
                    ["checkpointRecoveryRequired"] = mutationStarted,
                };
            }
            return new JObject
            {
                ["ok"] = false,
                ["pending"] = false,
                ["status"] = "unavailable",
                ["jobId"] = jobId,
                ["reason"] = "unitypackage_import_job_not_found",
                ["retryable"] = false,
            };
        }

        private static void OnImportStarted(string packageName)
        {
            lock (JobLock)
            {
                ImportJob job;
                if (!string.IsNullOrEmpty(activeJobId)
                    && Jobs.TryGetValue(activeJobId, out job)
                    && !job.startedForThisJob
                    && job.mutationStarted
                    && string.Equals(importInvocationJobId, job.jobId, StringComparison.Ordinal)
                    && !string.IsNullOrWhiteSpace(packageName))
                {
                    // Unity reports the package's embedded display name here,
                    // which is not guaranteed to equal the source filename.
                    // Bind it only while this job's exact ImportPackage call is
                    // on the stack, then require that exact event identity for
                    // every later terminal callback.
                    job.startedForThisJob = true;
                    job.importEventPackageName = packageName ?? "";
                    job.status = "running";
                    PersistJob(job);
                }
            }
        }

        private static void OnImportPackageItemsCompleted(string[] items)
        {
            lock (JobLock)
            {
                ImportJob job;
                if (string.IsNullOrEmpty(activeJobId)
                    || !Jobs.TryGetValue(activeJobId, out job)
                    || !job.startedForThisJob
                    || job.result != null)
                {
                    return;
                }
                // Unity supplies selected items without a package name/job id.
                // Preserve this observation, but never treat it as written assets
                // or independent proof that the callback belongs to this import.
                job.selectedItemsObserved = true;
                job.selectedItems = items == null ? null : new List<string>(items);
                job.selectedItemsObservedUtc = DateTime.UtcNow;
                PersistJob(job);
            }
        }

        private static JObject BuildSelectedItemsEvidence(ImportJob job)
        {
            return new JObject
            {
                ["observed"] = job.selectedItemsObserved,
                ["items"] = job.selectedItems == null ? null : JArray.FromObject(job.selectedItems),
                ["observedUtc"] = job.selectedItemsObservedUtc?.ToString("O"),
                ["meaning"] = "selected_items_not_written_assets",
                ["attribution"] = "active_started_job_without_callback_identity",
            };
        }

        private static void OnImportCompleted(string packageName)
        {
            ImportJob job = ActiveJobForEvent(packageName);
            if (job == null)
            {
                return;
            }
            // Persist the matched Unity terminal event before refresh can reload Core.
            job.importCompletedObserved = true;
            job.importCompletedObservedUtc = job.importCompletedObservedUtc ?? DateTime.UtcNow;
            job.importCompletionEvidence = "matched_import_package_completed_event";
            job.status = "readback_pending";
            job.readbackAttemptedUtc = DateTime.UtcNow;
            PersistJob(job);
            try
            {
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                if (EditorApplication.isCompiling || EditorApplication.isUpdating)
                {
                    return;
                }
                var expectedAssets = ReadExpectedAssets(job.expectedAssetPaths);
                CompleteJob(job, "completed", new JObject
                {
                    ["ok"] = true,
                    ["pending"] = false,
                    ["status"] = "completed",
                    ["jobId"] = job.jobId,
                    ["projectPath"] = job.projectPath,
                    ["unityPackagePath"] = job.unityPackagePath,
                    ["expectedSha256"] = job.expectedSha256,
                    ["expectedSize"] = job.expectedSize,
                    ["expectedAssetPaths"] = JArray.FromObject(job.expectedAssetPaths),
                    ["expectedAssets"] = JArray.FromObject(expectedAssets),
                    ["mutationStarted"] = true,
                    ["committed"] = true,
                    ["commitState"] = "complete",
                    ["checkpointRecoveryRequired"] = false,
                });
            }
            catch (Exception exception)
            {
                RecordPendingReadbackFailure(job, "unitypackage_async_readback_failed", exception);
            }
        }

        private static void OnImportFailed(string packageName, string errorMessage)
        {
            var job = ActiveJobForEvent(packageName);
            if (job != null)
            {
                CompleteFailedJob(job, "unitypackage_async_failed");
            }
        }

        private static void OnImportCancelled(string packageName)
        {
            var job = ActiveJobForEvent(packageName);
            if (job != null)
            {
                CompleteFailedJob(job, "unitypackage_async_cancelled");
            }
        }

        private static ImportJob ActiveJobForEvent(string packageName)
        {
            lock (JobLock)
            {
                ImportJob job;
                return !string.IsNullOrEmpty(activeJobId)
                    && Jobs.TryGetValue(activeJobId, out job)
                    && job.startedForThisJob
                    && string.Equals(job.importEventPackageName, packageName ?? "", StringComparison.Ordinal)
                    ? job
                    : null;
            }
        }

        private static void CompleteFailedJob(ImportJob job, string reason)
        {
            CompleteJob(job, "error", new JObject
            {
                ["ok"] = false,
                ["pending"] = false,
                ["status"] = "error",
                ["jobId"] = job.jobId,
                ["reason"] = reason,
                ["retryable"] = false,
                ["mutationStarted"] = job.mutationStarted,
                ["committed"] = job.mutationStarted,
                ["commitState"] = job.mutationStarted ? "unknown" : "not_started",
                ["checkpointRecoveryRequired"] = job.mutationStarted,
            });
        }

        private static void CompleteJob(ImportJob job, string status, JObject result)
        {
            lock (JobLock)
            {
                job.status = status;
                job.completedUtc = DateTime.UtcNow;
                result["createdUtc"] = job.createdUtc.ToString("O");
                result["completedUtc"] = job.completedUtc.Value.ToString("O");
                result["importCompletedObserved"] = job.importCompletedObserved;
                result["importCompletedObservedUtc"] = job.importCompletedObservedUtc?.ToString("O");
                result["importCompletionEvidence"] = job.importCompletionEvidence;
                result["completedReadbackFailureCount"] = job.completedReadbackFailureCount;
                result["selectedItemsEvidence"] = BuildSelectedItemsEvidence(job);
                job.result = result;
                if (string.Equals(activeJobId, job.jobId, StringComparison.Ordinal))
                {
                    activeJobId = "";
                    SessionState.EraseString(ActiveJobSessionKey);
                }
            }
            PersistJob(job);
        }

        private static void RestorePersistedActiveJob()
        {
            var jobId = (SessionState.GetString(ActiveJobSessionKey, "") ?? "")
                .Trim()
                .ToLowerInvariant();
            Guid parsed;
            if (!Guid.TryParseExact(jobId, "N", out parsed))
            {
                SessionState.EraseString(ActiveJobSessionKey);
                return;
            }
            var persisted = LoadPersistedJob(jobId);
            ImportJob job;
            try
            {
                job = persisted == null ? null : persisted.ToObject<ImportJob>();
            }
            catch
            {
                job = null;
            }
            if (job == null
                || !string.Equals(job.jobId, jobId, StringComparison.Ordinal)
                || job.result != null
                || !job.mutationStarted
                || (job.status != "pending"
                    && job.status != "running"
                    && job.status != "readback_pending")
                || !string.Equals(job.projectPath, CheckpointPrepareTool.ProjectRoot(), StringComparison.Ordinal)
                || string.IsNullOrWhiteSpace(job.expectedEventPackageName))
            {
                SessionState.EraseString(ActiveJobSessionKey);
                return;
            }
            lock (JobLock)
            {
                // In the previous implementation, only a matched completed callback
                // could start async readback before any domain reload. Do not infer
                // completion for restored/running jobs whose event may have been lost.
                if (!job.importCompletedObserved
                    && !job.restoredAfterDomainReload
                    && job.status == "readback_pending"
                    && job.startedForThisJob
                    && !string.IsNullOrWhiteSpace(job.importEventPackageName)
                    && job.readbackAttemptedUtc.HasValue
                    && !string.IsNullOrWhiteSpace(job.readbackFailurePath)
                    && job.expectedAssetPaths != null
                    && job.expectedAssetPaths.Contains(job.readbackFailurePath)
                    && (job.readbackFailureCode == "unitypackage_async_readback_failed"
                        || job.readbackFailureCode == "unitypackage_async_readback_pending"))
                {
                    job.importCompletedObserved = true;
                    // This is the time evidence was recovered, not a fabricated
                    // timestamp for the historical completed callback.
                    job.importCompletionEvidence = "legacy_non_restored_async_readback";
                    job.completedReadbackFailureCount = 0;
                }
                job.restoredAfterDomainReload = true;
                job.restoredUtc = DateTime.UtcNow;
                activeJobId = job.jobId;
                Jobs[job.jobId] = job;
            }
            PersistJob(job);
        }

        private static void TryCompletePendingReadback(ImportJob job)
        {
            if (!job.mutationStarted
                || job.result != null
                || job.expectedAssetPaths == null
                || job.expectedAssetPaths.Count == 0
                || EditorApplication.isCompiling
                || EditorApplication.isUpdating)
            {
                return;
            }
            var now = DateTime.UtcNow;
            var restoredReadbackReady = job.restoredAfterDomainReload
                && job.restoredUtc.HasValue
                && now - job.restoredUtc.Value >= TimeSpan.FromSeconds(2);
            var pendingReadbackReady = string.Equals(job.status, "readback_pending", StringComparison.Ordinal)
                && job.readbackAttemptedUtc.HasValue
                && now - job.readbackAttemptedUtc.Value >= TimeSpan.FromMilliseconds(500);
            if (!restoredReadbackReady && !pendingReadbackReady)
            {
                return;
            }
            try
            {
                var expectedAssets = ReadExpectedAssets(job.expectedAssetPaths);
                CompleteJob(job, "completed", new JObject
                {
                    ["ok"] = true,
                    ["pending"] = false,
                    ["status"] = "completed",
                    ["jobId"] = job.jobId,
                    ["projectPath"] = job.projectPath,
                    ["unityPackagePath"] = job.unityPackagePath,
                    ["expectedSha256"] = job.expectedSha256,
                    ["expectedSize"] = job.expectedSize,
                    ["expectedAssetPaths"] = JArray.FromObject(job.expectedAssetPaths),
                    ["expectedAssets"] = JArray.FromObject(expectedAssets),
                    ["mutationStarted"] = true,
                    ["committed"] = true,
                    ["commitState"] = "complete",
                    ["checkpointRecoveryRequired"] = false,
                    ["completionSource"] = restoredReadbackReady
                        ? "restored_expected_asset_readback"
                        : "pending_expected_asset_readback",
                });
            }
            catch (Exception exception)
            {
                RecordPendingReadbackFailure(
                    job,
                    restoredReadbackReady
                        ? "unitypackage_restored_readback_pending"
                        : "unitypackage_async_readback_pending",
                    exception);
            }
        }

        private static void RecordPendingReadbackFailure(ImportJob job, string code, Exception exception)
        {
            lock (JobLock)
            {
                job.status = "readback_pending";
                job.readbackAttemptedUtc = DateTime.UtcNow;
                job.readbackFailureCode = code ?? "unitypackage_readback_pending";
                job.readbackFailureReason = exception?.Message ?? "UnityPackage asset readback is incomplete.";
                var prefix = "Expected imported asset readback failed for '";
                if (job.readbackFailureReason.StartsWith(prefix, StringComparison.Ordinal))
                {
                    var end = job.readbackFailureReason.IndexOf("':", prefix.Length, StringComparison.Ordinal);
                    job.readbackFailurePath = end > prefix.Length
                        ? job.readbackFailureReason.Substring(prefix.Length, end - prefix.Length)
                        : "";
                }
                if (job.importCompletedObserved
                    && !EditorApplication.isCompiling && !EditorApplication.isUpdating)
                {
                    job.completedReadbackFailureCount++;
                }
            }
            PersistJob(job);
            if (job.importCompletedObserved
                && job.completedReadbackFailureCount >= MaxCompletedReadbackFailures)
            {
                // Unity has finished this import. Failed verification does not mean
                // zero changes, successful commit, cancellation, or approval to retry.
                var failure = BuildPendingPayload(job);
                failure["ok"] = false;
                failure["pending"] = false;
                failure["status"] = "error";
                failure["reason"] = "unitypackage_completed_readback_failed";
                failure["retryable"] = false;
                failure["committed"] = JValue.CreateNull();
                failure["commitState"] = "unknown";
                failure["checkpointRecoveryRequired"] = true;
                CompleteJob(job, "error", failure);
            }
        }

        private static void PersistJob(ImportJob job)
        {
            var payload = new JObject
            {
                ["schema"] = "vrcforge.unitypackage-import-job.v1",
                ["jobId"] = job.jobId,
                ["projectPath"] = job.projectPath,
                ["unityPackagePath"] = job.unityPackagePath,
                ["expectedSha256"] = job.expectedSha256,
                ["expectedSize"] = job.expectedSize,
                ["expectedAssetPaths"] = JArray.FromObject(job.expectedAssetPaths),
                ["expectedEventPackageName"] = job.expectedEventPackageName,
                ["mutationStarted"] = job.mutationStarted,
                ["startedForThisJob"] = job.startedForThisJob,
                ["importEventPackageName"] = job.importEventPackageName,
                ["status"] = job.status,
                ["createdUtc"] = job.createdUtc.ToString("O"),
                ["completedUtc"] = job.completedUtc?.ToString("O"),
                ["result"] = job.result == null ? null : job.result.DeepClone(),
                ["restoredAfterDomainReload"] = job.restoredAfterDomainReload,
                ["restoredUtc"] = job.restoredUtc?.ToString("O"),
                ["readbackFailurePath"] = job.readbackFailurePath,
                ["readbackFailureCode"] = job.readbackFailureCode,
                ["readbackFailureReason"] = job.readbackFailureReason,
                ["readbackAttemptedUtc"] = job.readbackAttemptedUtc?.ToString("O"),
                ["importCompletedObserved"] = job.importCompletedObserved,
                ["importCompletedObservedUtc"] = job.importCompletedObservedUtc?.ToString("O"),
                ["importCompletionEvidence"] = job.importCompletionEvidence,
                ["completedReadbackFailureCount"] = job.completedReadbackFailureCount,
                ["selectedItemsObserved"] = job.selectedItemsObserved,
                ["selectedItems"] = job.selectedItems == null ? null : JArray.FromObject(job.selectedItems),
                ["selectedItemsObservedUtc"] = job.selectedItemsObservedUtc?.ToString("O"),
            };
            SessionState.SetString(JobSessionPrefix + job.jobId, payload.ToString(Newtonsoft.Json.Formatting.None));
        }

        private static JObject LoadPersistedJob(string jobId)
        {
            var raw = SessionState.GetString(JobSessionPrefix + jobId, "");
            if (string.IsNullOrWhiteSpace(raw))
            {
                return null;
            }
            try
            {
                var payload = JObject.Parse(raw);
                return string.Equals(payload["schema"]?.ToString(), "vrcforge.unitypackage-import-job.v1", StringComparison.Ordinal)
                    && string.Equals(payload["jobId"]?.ToString(), jobId, StringComparison.Ordinal)
                    ? payload
                    : null;
            }
            catch
            {
                return null;
            }
        }

        private static bool IsLowerHex(string value)
        {
            foreach (var character in value)
            {
                if ((character < '0' || character > '9') && (character < 'a' || character > 'f'))
                {
                    return false;
                }
            }
            return true;
        }

        private static List<string> ValidateExpectedAssetPaths(IEnumerable<string> expectedPaths)
        {
            var paths = new List<string>();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var rawPath in expectedPaths ?? Array.Empty<string>())
            {
                var assetPath = (rawPath ?? string.Empty).Replace("\\", "/").Trim();
                if (assetPath.Length == 0 || !assetPath.StartsWith("Assets/", StringComparison.Ordinal)
                    || assetPath.Contains("../") || assetPath.Contains("//") || !seen.Add(assetPath))
                {
                    throw new InvalidOperationException("expectedAssetPaths contains an invalid or duplicate Assets path.");
                }
                paths.Add(assetPath);
            }
            return paths;
        }

        private static List<object> ReadExpectedAssets(IEnumerable<string> expectedPaths)
        {
            var receipts = new List<object>();
            foreach (var assetPath in expectedPaths ?? Array.Empty<string>())
            {
                var assetType = AssetDatabase.GetMainAssetTypeAtPath(assetPath);
                var guid = (AssetDatabase.AssetPathToGUID(assetPath) ?? string.Empty).Trim().ToLowerInvariant();
                if (assetType == null || guid.Length != 32 || !IsLowerHex(guid))
                {
                    throw new InvalidOperationException(
                        $"Expected imported asset readback failed for '{assetPath}': "
                        + $"assetType={(assetType == null ? "missing" : assetType.FullName ?? assetType.Name)}, "
                        + $"guidLength={guid.Length}.");
                }
                receipts.Add(new { assetPath, guid, assetType = assetType.FullName ?? assetType.Name });
            }
            return receipts;
        }
    }

    [InitializeOnLoad]
    [VRCForgeCommand(
        toolId: "vrc_refresh_asset_database",
        Summary = "When to use: refresh assets, reimport exact files, or explicitly restore a null ScriptedImporter reference using exact metadata hashes and a bound script GUID. When not to use: reading state, editing source, replacing a non-null script reference, or guessing importer identities. Returns a pollable completion job.",
        UsesContinuation = true,
        ContinuationAction = "vrc_poll_job",
        ContinuationTimeoutSeconds = 300
    )]
    public static class AssetDatabaseRefreshTool
    {
        internal const string ToolName = "vrc_refresh_asset_database";
        private const double RefreshResponseGraceSeconds = 0.25d;
        private static bool refreshScheduled;
        private static double refreshNotBefore;
        private static string scheduledRequestId = string.Empty;

        static AssetDatabaseRefreshTool()
        {
            var jobId = UnityAsyncJobRegistry.GetActive(ToolName);
            if (string.IsNullOrEmpty(jobId))
            {
                return;
            }
            var current = UnityAsyncJobRegistry.Poll(jobId);
            var status = current?.Value<string>("status") ?? string.Empty;
            if (status == "queued")
            {
                refreshScheduled = true;
                refreshNotBefore = EditorApplication.timeSinceStartup + RefreshResponseGraceSeconds;
                scheduledRequestId = jobId;
                EditorApplication.update -= RunScheduledRefresh;
                EditorApplication.update += RunScheduledRefresh;
            }
            else if (status == "running")
            {
                EditorApplication.update -= TryCompleteScheduledRefresh;
                EditorApplication.update += TryCompleteScheduledRefresh;
            }
        }

        public class Parameters
        {
            [VRCForgeInput("Optional exact active Unity project root.", IsRequired = false)] public string projectPath { get; set; } = "";
            [VRCForgeInput("Resolve pending Package Manager dependencies before refresh.", IsRequired = false)] public bool? resolvePackages { get; set; } = false;
            [VRCForgeInput("Bounded Package Manager resolve timeout in seconds.", IsRequired = false)] public int? packageResolveTimeoutSeconds { get; set; } = 120;
            [VRCForgeInput("Optional 1..16 exact existing Assets files or embedded package C# scripts to force reimport. Requires source hash, GUID, and matching importer, except explicitly verified null-reference restoration. Cannot combine with resolvePackages.", IsRequired = false)]
            public ReimportAsset[] reimportAssets { get; set; }
        }

        public class ReimportAsset
        {
            [VRCForgeInput("Exact Assets file or embedded Packages/<packageId> C# script path. Package source, metadata and manifest must remain unchanged.", IsRequired = true)] public string assetPath { get; set; }
            [VRCForgeInput("Exact asset GUID.", IsRequired = true)] public string guid { get; set; }
            [VRCForgeInput("SHA256 of the unchanged source file.", IsRequired = true)] public string expectedSourceSha256 { get; set; }
            [VRCForgeInput("Exact registered override importer, or default importer when no override is set.", IsRequired = true)] public string expectedImporterType { get; set; }
            [VRCForgeInput("Optional exact restoration of a null ScriptedImporter reference on an Assets file; requires both metadata hashes and a currently bound MonoScript GUID.", IsRequired = false)] public ScriptedImporterReference restoreScriptedImporterReference { get; set; }
        }

        public class ScriptedImporterReference
        {
            [VRCForgeInput("GUID of the MonoScript whose loaded class is the registered importer.", IsRequired = true)] public string scriptGuid { get; set; }
            [VRCForgeInput("SHA256 of current metadata containing exactly one null script reference.", IsRequired = true)] public string expectedMetadataSha256 { get; set; }
            [VRCForgeInput("SHA256 after replacing only the null reference with the specified script GUID.", IsRequired = true)] public string restoredMetadataSha256 { get; set; }
        }

        private static JArray PrepareReimportAssets(JToken token, bool persisted = false)
        {
            if (token == null) return null;
            if (token.Type != JTokenType.Array || token.Count() < 1 || token.Count() > 16)
                throw new InvalidOperationException("reimportAssets must contain 1..16 items.");
            var root = CheckpointPrepareTool.ProjectRoot();
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var result = new JArray();
            foreach (var item in token.Children<JObject>())
            {
                if (item.Properties().Any(property => property.Name != "assetPath" && property.Name != "guid"
                    && property.Name != "expectedSourceSha256" && property.Name != "expectedImporterType" && property.Name != "restoreScriptedImporterReference"
                    && !(persisted && property.Name == "before")))
                    throw new InvalidOperationException("reimportAssets item contains an unknown field.");
                foreach (var key in new[] { "assetPath", "guid", "expectedSourceSha256", "expectedImporterType" })
                    if (item[key]?.Type != JTokenType.String)
                        throw new InvalidOperationException($"reimportAssets {key} must be a string.");
                var path = item.Value<string>("assetPath") ?? string.Empty;
                var guid = (item.Value<string>("guid") ?? string.Empty).Trim().ToLowerInvariant();
                var expected = (item.Value<string>("expectedSourceSha256") ?? string.Empty).Trim().ToLowerInvariant();
                var expectedImporter = item.Value<string>("expectedImporterType");
                var packageScript = path.StartsWith("Packages/", StringComparison.Ordinal);
                if ((!path.StartsWith("Assets/", StringComparison.Ordinal) && !packageScript) || path.Contains("\\") || path.Contains("..")
                    || path.Contains(":") || path.Contains("//") || path.Contains("/./") || path.IndexOf('\0') >= 0
                    || path.EndsWith(".meta", StringComparison.OrdinalIgnoreCase) || (packageScript && !path.EndsWith(".cs", StringComparison.Ordinal))
                    || expectedImporter.Length < 1 || expectedImporter.Length > 512)
                    throw new InvalidOperationException("reimportAssets requires exact Assets files or embedded Packages C# script paths.");
                if (!seen.Add(path) || guid.Length != 32 || expected.Length != 64 || !IsLowerHex(guid) || !IsLowerHex(expected))
                    throw new InvalidOperationException("reimportAssets contains a duplicate or invalid identity.");
                var absolute = Path.GetFullPath(Path.Combine(root, path.Replace('/', Path.DirectorySeparatorChar)));
                if (!absolute.StartsWith(Path.GetFullPath(root) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) || !File.Exists(absolute))
                    throw new InvalidOperationException($"reimportAssets source is unavailable: {path}");
                MaterialShaderTool.EnsureNoReparseBoundary(Path.GetFullPath(Path.Combine(root, packageScript ? "Packages" : "Assets")), absolute);
                EnsureOrdinarySource(absolute);
                var packageIdentity = ReadPackageScriptIdentity(root, path, absolute);
                var actualGuid = (AssetDatabase.AssetPathToGUID(path) ?? string.Empty).Trim().ToLowerInvariant();
                var type = AssetDatabase.GetMainAssetTypeAtPath(path);
                var importer = AssetImporter.GetAtPath(path);
                var registered = AssetDatabase.GetImporterOverride(path) ?? AssetDatabase.GetDefaultImporter(path);
                var reference = ReadScriptedImporterReference(path, absolute, guid, registered, item["restoreScriptedImporterReference"], false, out _);
                if (actualGuid != guid || type == null || registered == null || registered.FullName != expectedImporter
                    || !typeof(AssetImporter).IsAssignableFrom(registered)
                    || (reference == null ? importer == null || importer.GetType() != registered : importer != null)
                    || SourceSha256(absolute) != expected)
                    throw new InvalidOperationException($"reimportAssets preflight identity or source hash failed: {path}");
                if (packageScript && (type != typeof(MonoScript) || !(importer is MonoImporter)))
                    throw new InvalidOperationException($"Embedded package reimport requires a MonoScript and its MonoImporter: {path}");
                if (persisted && (item["before"] is not JObject prior || prior.Value<string>("guid") != guid
                    || prior.Value<string>("sourceSha256") != expected || prior.Value<string>("assetType") != type.FullName
                    || prior.Value<string>("registeredImporterType") != expectedImporter
                    || !JToken.DeepEquals(prior["scriptedImporterReference"] as JObject, reference)
                    || !JToken.DeepEquals(prior["packageScriptIdentity"] as JObject, packageIdentity)))
                    throw new InvalidOperationException($"reimportAssets queued identity drifted: {path}");
                var prepared = new JObject { ["assetPath"] = path, ["guid"] = guid, ["expectedSourceSha256"] = expected,
                    ["expectedImporterType"] = expectedImporter, ["before"] = persisted ? item["before"].DeepClone()
                        : new JObject { ["guid"] = actualGuid, ["sourceSha256"] = expected, ["assetType"] = type.FullName,
                            ["importerType"] = importer?.GetType().FullName, ["registeredImporterType"] = expectedImporter,
                            ["scriptedImporterReference"] = reference, ["packageScriptIdentity"] = packageIdentity } };
                if (reference != null) prepared["restoreScriptedImporterReference"] = item["restoreScriptedImporterReference"].DeepClone();
                result.Add(prepared);
            }
            if (result.Count != token.Count()) throw new InvalidOperationException("reimportAssets items must be objects.");
            return result;
        }

        private static string SourceSha256(string path)
        {
            using (var stream = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", string.Empty).ToLowerInvariant();
        }

        private static JObject ReadScriptedImporterReference(string path, string absolute, string assetGuid, Type registered,
            JToken request, bool restored, out byte[] replacement)
        {
            replacement = null;
            if (request == null) return null;
            var keys = new[] { "scriptGuid", "expectedMetadataSha256", "restoredMetadataSha256" };
            if (request is not JObject spec || spec.Count != 3 || keys.Any(key => spec[key]?.Type != JTokenType.String)
                || !path.StartsWith("Assets/", StringComparison.Ordinal) || registered == null
                || !typeof(UnityEditor.AssetImporters.ScriptedImporter).IsAssignableFrom(registered)
                || AssetDatabase.GetImporterOverride(path) != null)
                throw new InvalidOperationException("Reference restoration requires an Assets file and its default ScriptedImporter.");
            var scriptGuid = spec.Value<string>("scriptGuid");
            var beforeHash = spec.Value<string>("expectedMetadataSha256");
            var afterHash = spec.Value<string>("restoredMetadataSha256");
            if (scriptGuid.Length != 32 || beforeHash.Length != 64 || afterHash.Length != 64
                || !IsLowerHex(scriptGuid) || !IsLowerHex(beforeHash) || !IsLowerHex(afterHash) || beforeHash == afterHash)
                throw new InvalidOperationException("Reference restoration identities are invalid.");
            var scriptPath = AssetDatabase.GUIDToAssetPath(scriptGuid);
            var script = AssetDatabase.LoadAssetAtPath<MonoScript>(scriptPath);
            if (script == null || script.GetClass() != registered || AssetDatabase.AssetPathToGUID(scriptPath) != scriptGuid)
                throw new InvalidOperationException("Reference restoration requires the exact bound MonoScript class.");
            var bytes = ReadMetadataBytes(absolute + ".meta");
            var metadataHash = BytesSha256(bytes);
            if (metadataHash != (restored ? afterHash : beforeHash)) throw new InvalidOperationException("Metadata hash changed.");
            var encoding = new UTF8Encoding(false, true);
            var text = encoding.GetString(bytes);
            var lines = text.Split('\n').Select(line => line.TrimEnd('\r')).ToArray();
            var nullLine = "  script: {instanceID: 0}";
            var restoredLine = "  script: {fileID: 11500000, guid: " + scriptGuid + ", type: 3}";
            if (lines.Count(line => line == "guid: " + assetGuid) != 1 || lines.Count(line => line == "ScriptedImporter:") != 1
                || lines.Count(line => line.StartsWith("  script:", StringComparison.Ordinal)) != 1
                || !lines.Contains(restored ? restoredLine : nullLine))
                throw new InvalidOperationException("Only an exact null ScriptedImporter reference may be restored.");
            if (!restored)
            {
                var match = System.Text.RegularExpressions.Regex.Match(text, @"(?m)^  script: \{instanceID: 0\}(?=\r?$)");
                if (!match.Success) throw new InvalidOperationException("The null reference is not an exact metadata line.");
                replacement = encoding.GetBytes(text.Substring(0, match.Index) + restoredLine + text.Substring(match.Index + match.Length));
                if (BytesSha256(replacement) != afterHash) throw new InvalidOperationException("Restored metadata hash does not match the request.");
            }
            return new JObject { ["scriptGuid"] = scriptGuid, ["scriptPath"] = scriptPath,
                ["scriptClass"] = registered.AssemblyQualifiedName, ["metadataSha256"] = metadataHash,
                ["restoredMetadataSha256"] = afterHash };
        }

        private static string BytesSha256(byte[] bytes)
        {
            using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private static byte[] ReadMetadataBytes(string path)
        {
            using (var stream = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var reader = new BinaryReader(stream))
            {
                if (stream.Length < 1 || stream.Length > 65536) throw new InvalidOperationException("Metadata exceeds the restoration limit.");
                return reader.ReadBytes((int)stream.Length);
            }
        }

        private static void ReplaceMetadata(string path, byte[] bytes, string expectedHash)
        {
            // Exact adjacent temporary file belongs to this authenticated main-thread job and is always removed.
            var temporary = path + ".vrcforge-" + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                using (var stream = File.Open(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None)) stream.Write(bytes, 0, bytes.Length);
                EnsureOrdinarySource(path.Substring(0, path.Length - 5));
                if (SourceSha256(path) != expectedHash) throw new InvalidOperationException("Metadata changed before atomic replacement.");
                File.Replace(temporary, path, null);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }

        private static void RestoreScriptedImporterReferences(JArray targets)
        {
            var written = new List<(string path, byte[] original, string replacementHash)>();
            try
            {
                foreach (var item in targets)
                {
                    if (item["restoreScriptedImporterReference"] == null) continue;
                    var path = item.Value<string>("assetPath");
                    var absolute = Path.Combine(CheckpointPrepareTool.ProjectRoot(), path);
                    var reference = ReadScriptedImporterReference(path, absolute, item.Value<string>("guid"),
                        AssetDatabase.GetDefaultImporter(path), item["restoreScriptedImporterReference"], false, out var bytes);
                    var original = ReadMetadataBytes(absolute + ".meta");
                    if (BytesSha256(original) != reference.Value<string>("metadataSha256")) throw new InvalidOperationException("Metadata changed before restoration.");
                    ReplaceMetadata(absolute + ".meta", bytes, reference.Value<string>("metadataSha256"));
                    written.Add((absolute + ".meta", original, reference.Value<string>("restoredMetadataSha256")));
                }
            }
            catch
            {
                // Compensate only our exact metadata writes before any native import has started.
                foreach (var entry in written.AsEnumerable().Reverse()) ReplaceMetadata(entry.path, entry.original, entry.replacementHash);
                throw;
            }
        }

        private static JArray CaptureReferenceRollback(JArray targets)
        {
            var records = new JArray();
            foreach (var item in targets ?? new JArray())
            {
                if (item["restoreScriptedImporterReference"] is not JObject spec) continue;
                var path = item.Value<string>("assetPath");
                var bytes = ReadMetadataBytes(Path.Combine(CheckpointPrepareTool.ProjectRoot(), path) + ".meta");
                if (BytesSha256(bytes) != spec.Value<string>("expectedMetadataSha256")) throw new InvalidOperationException("Metadata changed before rollback capture.");
                records.Add(new JObject { ["assetPath"] = path, ["originalBase64"] = Convert.ToBase64String(bytes),
                    ["beforeSha256"] = spec["expectedMetadataSha256"], ["restoredSha256"] = spec["restoredMetadataSha256"] });
            }
            // Private async operation storage survives domain reload; bytes are excluded from public job payloads.
            return records;
        }

        private static void FailRefresh(string jobId, string code, Exception failure)
        {
            var records = UnityAsyncJobRegistry.ReadOperation(jobId)?["reference_rollback"] as JArray;
            var recovery = new JObject { ["metadataCompensated"] = true, ["checkpointRecoveryRequired"] = records != null && records.Count > 0 };
            var results = new JArray();
            foreach (var record in (records ?? new JArray()).Reverse())
            {
                var entry = new JObject { ["assetPath"] = record["assetPath"] };
                try
                {
                    var path = Path.Combine(CheckpointPrepareTool.ProjectRoot(), record.Value<string>("assetPath")) + ".meta";
                    var original = Convert.FromBase64String(record.Value<string>("originalBase64"));
                    if (BytesSha256(original) != record.Value<string>("beforeSha256")) throw new InvalidOperationException("Rollback bytes failed verification.");
                    var current = SourceSha256(path);
                    if (current == record.Value<string>("restoredSha256")) ReplaceMetadata(path, original, current);
                    else if (current != record.Value<string>("beforeSha256")) throw new InvalidOperationException("Metadata differs from both known hashes; preserved for checkpoint recovery.");
                    entry["metadataSha256"] = SourceSha256(path);
                    entry["restoredOriginal"] = entry.Value<string>("metadataSha256") == record.Value<string>("beforeSha256");
                    if (!entry.Value<bool>("restoredOriginal")) throw new InvalidOperationException("Metadata compensation readback failed.");
                }
                catch (Exception error) { recovery["metadataCompensated"] = false; entry["error"] = error.Message; }
                results.Add(entry);
            }
            recovery["files"] = results;
            UnityAsyncJobRegistry.Fail(jobId, code, failure.Message, false, () =>
            {
                var snapshot = ReadSnapshot();
                if (records != null && records.Count > 0) snapshot["referenceRestorationRecovery"] = recovery;
                return snapshot;
            });
        }

        private static JObject ReadPackageScriptIdentity(string root, string path, string absolute)
        {
            if (!path.StartsWith("Packages/", StringComparison.Ordinal)) return null;
            var parts = path.Split('/');
            if (parts.Length < 3 || !path.EndsWith(".cs", StringComparison.Ordinal))
                throw new InvalidOperationException("Only C# scripts in physical embedded packages can be reimported.");
            var manifest = Path.Combine(root, "Packages", parts[1], "package.json");
            if ((File.GetAttributes(manifest) & (FileAttributes.ReparsePoint | FileAttributes.Directory)) != 0)
                throw new InvalidOperationException("Embedded package manifest must be an ordinary file.");
            JObject package;
            string manifestHash;
            using (var stream = File.Open(manifest, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                if (stream.Length < 1 || stream.Length > 65536)
                    throw new InvalidOperationException("Embedded package manifest must contain 1..65536 bytes.");
                using (var sha = SHA256.Create()) manifestHash = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", string.Empty).ToLowerInvariant();
                stream.Position = 0;
                using (var reader = new StreamReader(stream)) package = JObject.Parse(reader.ReadToEnd());
            }
            if (package["name"]?.Type != JTokenType.String || package.Value<string>("name") != parts[1])
                throw new InvalidOperationException("Embedded package manifest name must match its project directory.");
            return new JObject { ["packageName"] = parts[1], ["manifestSha256"] = manifestHash,
                ["metadataSha256"] = SourceSha256(absolute + ".meta") };
        }

        private static void EnsureOrdinarySource(string path)
        {
            foreach (var file in new[] { path, path + ".meta" })
                if ((File.GetAttributes(file) & (FileAttributes.ReparsePoint | FileAttributes.Directory)) != 0)
                    throw new InvalidOperationException("Reimport source and metadata must be ordinary files.");
        }

        private static bool IsLowerHex(string value)
        {
            foreach (var c in value) if (!(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f')) return false;
            return true;
        }

        private static JArray ReadReimportResults(JArray targets)
        {
            var results = new JArray();
            foreach (var item in targets ?? new JArray())
            {
                var path = item.Value<string>("assetPath");
                var absolute = Path.GetFullPath(Path.Combine(CheckpointPrepareTool.ProjectRoot(), path.Replace('/', Path.DirectorySeparatorChar)));
                var type = AssetDatabase.GetMainAssetTypeAtPath(path);
                var guid = (AssetDatabase.AssetPathToGUID(path) ?? string.Empty).Trim().ToLowerInvariant();
                MaterialShaderTool.EnsureNoReparseBoundary(Path.GetFullPath(Path.Combine(CheckpointPrepareTool.ProjectRoot(), path.StartsWith("Packages/", StringComparison.Ordinal) ? "Packages" : "Assets")), absolute);
                EnsureOrdinarySource(absolute);
                var packageIdentity = ReadPackageScriptIdentity(CheckpointPrepareTool.ProjectRoot(), path, absolute);
                var sourceSha = SourceSha256(absolute);
                var importer = AssetImporter.GetAtPath(path);
                var registered = AssetDatabase.GetImporterOverride(path) ?? AssetDatabase.GetDefaultImporter(path);
                var reference = ReadScriptedImporterReference(path, absolute, guid, registered, item["restoreScriptedImporterReference"], true, out _);
                if (reference != null && (item["before"]?["scriptedImporterReference"] is not JObject priorReference
                    || new[] { "scriptGuid", "scriptPath", "scriptClass", "restoredMetadataSha256" }.Any(key => !JToken.DeepEquals(priorReference[key], reference[key]))))
                    throw new InvalidOperationException("Restored script identity changed after native import.");
                var expected = item.Value<string>("expectedSourceSha256");
                if (guid != item.Value<string>("guid") || sourceSha != expected || type == null || importer == null
                    || item["before"] == null || item["before"]["guid"]?.Value<string>() != guid
                    || item["before"]["assetType"]?.Value<string>() != type.FullName
                    || !JToken.DeepEquals(item["before"]["packageScriptIdentity"] as JObject, packageIdentity)
                    || registered == null || registered.FullName != item.Value<string>("expectedImporterType")
                    || item.Value<string>("expectedImporterType") != importer.GetType().FullName)
                    throw new InvalidOperationException($"reimportAssets readback verification failed: {path}");
                results.Add(new JObject { ["assetPath"] = path, ["before"] = item["before"], ["after"] = new JObject { ["guid"] = guid, ["sourceSha256"] = sourceSha, ["expectedSourceSha256"] = expected, ["assetType"] = type.FullName, ["importerType"] = importer.GetType().FullName, ["registeredImporterType"] = registered.FullName, ["scriptedImporterReference"] = reference, ["packageScriptIdentity"] = packageIdentity } });
            }
            return results;
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                CheckpointPrepareTool.ValidateProject(@params);
                CheckpointPrepareTool.EnsureEditorReady();
                var reimportAssets = PrepareReimportAssets(@params?["reimportAssets"]);
                var resolvePackages = @params?["resolvePackages"]?.Value<bool?>() ?? false;
                if (resolvePackages && reimportAssets != null)
                    throw new InvalidOperationException("resolvePackages cannot be combined with reimportAssets.");
                var packageResolveTimeoutSeconds = Math.Max(
                    5,
                    Math.Min(@params?["packageResolveTimeoutSeconds"]?.Value<int?>() ?? 120, 300));
                object packageResolve = new { requested = false };
                if (resolvePackages)
                {
                    var startedAt = DateTime.UtcNow;
                    Client.Resolve();
                    packageResolve = new
                    {
                        requested = true,
                        completed = false,
                        status = "started",
                        error = "",
                        startedAt = startedAt.ToString("O"),
                        timeoutSeconds = packageResolveTimeoutSeconds
                    };
                }

                var existingJobId = UnityAsyncJobRegistry.GetActive(ToolName);
                if (!string.IsNullOrEmpty(existingJobId))
                {
                    return VRCForgeToolResult.RejectedBeforeMutation(
                        "asset_database_refresh_already_running",
                        "Another AssetDatabase refresh job is already active.",
                        "unity_asset_database",
                        "refresh_precondition",
                        true,
                        new { job_id = existingJobId });
                }

                var job = UnityAsyncJobRegistry.Create(
                    ToolName,
                    CheckpointPrepareTool.ProjectRoot(),
                    ReadSnapshot(),
                    new JObject
                    {
                        ["resolve_packages"] = resolvePackages,
                        ["package_resolve_timeout_seconds"] = packageResolveTimeoutSeconds,
                        ["reimport_assets"] = reimportAssets,
                        ["reference_rollback"] = CaptureReferenceRollback(reimportAssets),
                    },
                    TimeSpan.FromSeconds(300));
                var jobId = job.Value<string>("job_id");
                UnityAsyncJobRegistry.SetActive(ToolName, jobId);
                refreshScheduled = true;
                refreshNotBefore = EditorApplication.timeSinceStartup + RefreshResponseGraceSeconds;
                scheduledRequestId = jobId;
                EditorApplication.update -= RunScheduledRefresh;
                EditorApplication.update += RunScheduledRefresh;

                var scheduled = new
                {
                    status = "scheduled",
                    completionKnown = false,
                    verificationTool = "vrc_get_compile_errors"
                };
                job["requestId"] = jobId;
                job["projectPath"] = CheckpointPrepareTool.ProjectRoot();
                job["packageResolve"] = JToken.FromObject(packageResolve);
                job["scheduledStatus"] = scheduled.status;
                job["completionKnown"] = scheduled.completionKnown;
                job["verificationTool"] = scheduled.verificationTool;
                return VRCForgeToolResult.Waiting(
                    "Scheduled a Unity AssetDatabase refresh after the tool response is released.",
                    RefreshResponseGraceSeconds,
                    job);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"AssetDatabase refresh failed: {ex.Message}");
            }
        }

        private static void RunScheduledRefresh()
        {
            if (!refreshScheduled || EditorApplication.timeSinceStartup < refreshNotBefore)
            {
                return;
            }

            EditorApplication.update -= RunScheduledRefresh;
            var requestId = scheduledRequestId;
            refreshScheduled = false;
            refreshNotBefore = 0d;
            scheduledRequestId = string.Empty;
            try
            {
                UnityAsyncJobRegistry.MarkRunning(requestId);
                // A refresh may compile and domain-reload this same Core. It must
                // therefore run only after the MCP response has been released;
                // otherwise the caller and Unity can wait on each other forever.
                var operation = UnityAsyncJobRegistry.ReadOperation(requestId);
                var reimportAssets = operation?["reimport_assets"] as JArray;
                if (reimportAssets != null)
                {
                    var checkedTargets = PrepareReimportAssets(reimportAssets, true);
                    RestoreScriptedImporterReferences(checkedTargets);
                    foreach (var item in checkedTargets)
                        AssetDatabase.ImportAsset(item.Value<string>("assetPath"), ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
                }
                else
                {
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh();
                }
                UnityEngine.Debug.Log($"[VRCForge] Scheduled AssetDatabase refresh completed ({requestId}).");
                EditorApplication.update -= TryCompleteScheduledRefresh;
                EditorApplication.update += TryCompleteScheduledRefresh;
            }
            catch (Exception ex)
            {
                FailRefresh(requestId, "asset_database_refresh_failed", ex);
                UnityEngine.Debug.LogError(
                    $"[VRCForge] Scheduled AssetDatabase refresh failed ({requestId}): "
                    + $"{ex.GetType().FullName}: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static void TryCompleteScheduledRefresh()
        {
            EditorApplication.update -= TryCompleteScheduledRefresh;
            var jobId = UnityAsyncJobRegistry.GetActive(ToolName);
            if (string.IsNullOrEmpty(jobId))
            {
                return;
            }
            var current = UnityAsyncJobRegistry.Poll(jobId);
            if (current == null || current.Value<string>("status") == "expired")
            {
                UnityAsyncJobRegistry.ClearActive(ToolName, jobId);
                return;
            }
            if (EditorApplication.isCompiling || EditorApplication.isUpdating)
            {
                EditorApplication.update += TryCompleteScheduledRefresh;
                return;
            }
            var operation = UnityAsyncJobRegistry.ReadOperation(jobId);
            var reimportAssets = operation?["reimport_assets"] as JArray;
            try
            {
                UnityAsyncJobRegistry.Complete(jobId, () => ReadSnapshot(reimportAssets));
            }
            catch (Exception ex)
            {
                FailRefresh(jobId, "asset_database_reimport_readback_failed", ex);
            }
        }

        internal static JObject ReadSnapshot() { return ReadSnapshot(null); }

        internal static JObject ReadSnapshot(JArray reimportAssets)
        {
            var assetPaths = AssetDatabase.GetAllAssetPaths() ?? new string[0];
            Array.Sort(assetPaths, StringComparer.Ordinal);
            string digest;
            using (var sha256 = SHA256.Create())
            {
                digest = BitConverter.ToString(
                        sha256.ComputeHash(Encoding.UTF8.GetBytes(string.Join("\n", assetPaths))))
                    .Replace("-", string.Empty)
                    .ToLowerInvariant();
            }
            var snapshot = new JObject
            {
                ["project_path"] = CheckpointPrepareTool.ProjectRoot(),
                ["is_compiling"] = EditorApplication.isCompiling,
                ["is_updating"] = EditorApplication.isUpdating,
                ["asset_path_count"] = assetPaths.Length,
                ["asset_path_digest"] = digest,
                ["compile"] = CompileErrorMonitor.ReadCoreInfoSnapshot(120),
            };
            if (reimportAssets != null) snapshot["reimportResults"] = ReadReimportResults(reimportAssets);
            return snapshot;
        }
    }
}
