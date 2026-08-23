using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
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
        }

        static UnityPackageImporterTool()
        {
            AssetDatabase.importPackageStarted += OnImportStarted;
            AssetDatabase.importPackageCompleted += OnImportCompleted;
            AssetDatabase.importPackageFailed += OnImportFailed;
            AssetDatabase.importPackageCancelled += OnImportCancelled;
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

        private static void OnImportCompleted(string packageName)
        {
            ImportJob job = ActiveJobForEvent(packageName);
            if (job == null)
            {
                return;
            }
            try
            {
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
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
                job.restoredAfterDomainReload = true;
                job.restoredUtc = DateTime.UtcNow;
                activeJobId = job.jobId;
                Jobs[job.jobId] = job;
            }
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
            }
            PersistJob(job);
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

    [VRCForgeCommand(
        toolId: "vrc_refresh_asset_database",
        Summary = "Refresh Unity AssetDatabase after VRCForge copied supervised outfit assets."
    )]
    public static class AssetDatabaseRefreshTool
    {
        private const double RefreshResponseGraceSeconds = 0.25d;
        private static bool refreshScheduled;
        private static double refreshNotBefore;
        private static string scheduledRequestId = string.Empty;

        public class Parameters
        {
            [VRCForgeInput("Optional exact active Unity project root.", IsRequired = false)] public string projectPath { get; set; } = "";
            [VRCForgeInput("Resolve pending Package Manager dependencies before refresh.", IsRequired = false)] public bool? resolvePackages { get; set; } = false;
            [VRCForgeInput("Bounded Package Manager resolve timeout in seconds.", IsRequired = false)] public int? packageResolveTimeoutSeconds { get; set; } = 120;
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                CheckpointPrepareTool.ValidateProject(@params);
                CheckpointPrepareTool.EnsureEditorReady();
                var resolvePackages = @params?["resolvePackages"]?.Value<bool?>() ?? false;
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

                if (!refreshScheduled)
                {
                    refreshScheduled = true;
                    refreshNotBefore = EditorApplication.timeSinceStartup + RefreshResponseGraceSeconds;
                    scheduledRequestId = Guid.NewGuid().ToString("N");
                    EditorApplication.update -= RunScheduledRefresh;
                    EditorApplication.update += RunScheduledRefresh;
                }
                return VRCForgeToolResult.Completed(
                    "Scheduled a Unity AssetDatabase refresh after the tool response is released.",
                    new
                    {
                        ok = true,
                        status = "scheduled",
                        requestId = scheduledRequestId,
                        projectPath = CheckpointPrepareTool.ProjectRoot(),
                        packageResolve,
                        completionKnown = false,
                        verificationTool = "vrc_get_compile_errors"
                    });
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
                // A refresh may compile and domain-reload this same Core. It must
                // therefore run only after the MCP response has been released;
                // otherwise the caller and Unity can wait on each other forever.
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();
                UnityEngine.Debug.Log($"[VRCForge] Scheduled AssetDatabase refresh completed ({requestId}).");
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.LogError(
                    $"[VRCForge] Scheduled AssetDatabase refresh failed ({requestId}): "
                    + $"{ex.GetType().FullName}: {ex.Message}\n{ex.StackTrace}");
            }
        }
    }
}
