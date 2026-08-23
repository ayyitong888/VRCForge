using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.PackageManager;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3A.Editor;
using VRC.SDKBase;
using VRC.SDKBase.Editor;
using VRC.SDKBase.Editor.Api;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_build_test_avatar",
        Summary = "Run the installed VRChat SDK's local Build & Test for one exact scene avatar. This tool never publishes or uploads content.",
        Category = "diagnostics",
        UsesContinuation = true,
        ContinuationAction = "status",
        ContinuationTimeoutSeconds = 900
    )]
    public static class VrchatBuildTestTool
    {
        public const string ToolName = "vrc_build_test_avatar";
        private const string Schema = "vrcforge.vrchat_build_test.v1";
        private const string JobSessionPrefix = "VRCForge.VrchatBuildTest.Job.";
        private const string ActiveJobSessionKey = "VRCForge.VrchatBuildTest.ActiveJob.v1";
        private const string SdkPanelMenuPath = "VRChat SDK/Show Control Panel";
        private const int ConsoleEntryLimit = 120;
        private const int EventLimit = 200;
        private static readonly object JobLock = new object();
        private static readonly Dictionary<string, BuildTestJob> Jobs =
            new Dictionary<string, BuildTestJob>(StringComparer.Ordinal);
        private static string activeJobId = string.Empty;

        private sealed class BuildTestJob
        {
            internal string JobId = string.Empty;
            internal string ProjectPath = string.Empty;
            internal string AvatarPath = string.Empty;
            internal string AvatarGlobalObjectId = string.Empty;
            internal string ScenePath = string.Empty;
            internal string PipelineIdBefore = string.Empty;
            internal string PipelineIdAfter = string.Empty;
            internal string BundlePath = string.Empty;
            internal string SdkVersion = string.Empty;
            internal string SdkError = string.Empty;
            internal string LastProgress = string.Empty;
            internal string Status = "pending";
            internal bool SceneDirtyBefore;
            internal bool SceneDirtyAfter;
            internal bool BuildStarted;
            internal bool BuildTaskCompleted;
            internal bool LocalTestRegistered;
            internal bool LocalBuildProduced;
            internal bool MutationStarted;
            internal long BundleSizeBytes;
            internal DateTime CreatedUtc = DateTime.UtcNow;
            internal DateTime? StartedUtc;
            internal DateTime? CompletedUtc;
            internal JObject ConsoleBefore = new JObject();
            internal JObject CompileBefore = new JObject();
            internal JObject Result;
            internal readonly JArray Events = new JArray();
        }

        public sealed class Parameters
        {
            [VRCForgeInput("Exact active Unity project root for this local Build & Test.", IsRequired = true)]
            public string projectPath { get; set; } = string.Empty;

            [VRCForgeInput("Exact loaded-scene hierarchy path of the VRChat avatar root.", IsRequired = true)]
            public string avatarPath { get; set; } = string.Empty;

            [VRCForgeInput("Existing Build & Test job id to poll. Poll requests contain only this field.", IsRequired = false)]
            public string jobId { get; set; } = string.Empty;
        }

        public static object HandleCommand(JObject @params)
        {
            var parameters = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters();
            JObject payload;
            try
            {
                payload = string.IsNullOrWhiteSpace(parameters.jobId)
                    ? StartJob(@params ?? new JObject(), parameters)
                    : PollJob(parameters.jobId);
            }
            catch (Exception exception)
            {
                payload = BuildPreflightFailure(exception);
            }

            var status = (payload.Value<string>("status") ?? string.Empty).Trim().ToLowerInvariant();
            if (status == "pending" || status == "running" || status == "initializing_sdk")
            {
                return VRCForgeToolResult.Waiting(
                    "VRChat SDK local Build & Test is still running.",
                    1.0,
                    payload);
            }
            if (status == "completed")
            {
                return VRCForgeToolResult.Completed(
                    "VRChat SDK local Build & Test completed.",
                    payload);
            }

            var errorCode = payload.Value<string>("errorCode") ?? "vrchat_build_test_failed";
            var message = payload.Value<string>("error") ?? "VRChat SDK local Build & Test failed.";
            return VRCForgeToolResult.FailedWithCode(errorCode, message, payload);
        }

        private static JObject StartJob(JObject rawParameters, Parameters parameters)
        {
            CheckpointPrepareTool.ValidateProject(rawParameters);
            CheckpointPrepareTool.EnsureEditorReady();
            if (EditorApplication.isUpdating)
            {
                throw new InvalidOperationException("Unity is updating the AssetDatabase.");
            }
            if (BuildPipeline.isBuildingPlayer)
            {
                throw new InvalidOperationException("Another Unity player build is already active.");
            }
            var avatarPath = (parameters.avatarPath ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(avatarPath))
            {
                throw new InvalidOperationException("avatarPath is required and must identify one exact loaded-scene avatar.");
            }

            BuildTestJob existingJob = null;
            lock (JobLock)
            {
                if (!string.IsNullOrWhiteSpace(activeJobId))
                {
                    Jobs.TryGetValue(activeJobId, out existingJob);
                }
            }
            if (existingJob != null && IsPending(existingJob.Status))
            {
                return BuildConcurrentStartRejection(
                    existingJob,
                    "build_test_already_running",
                    "Another VRChat SDK local Build & Test job is already running.");
            }

            MarkPersistedActiveJobInterrupted();
            var descriptor = ResolveExactAvatar(avatarPath);
            var pipeline = descriptor.GetComponent<VRC.Core.PipelineManager>();
            if (pipeline == null)
            {
                throw new InvalidOperationException("The selected avatar has no PipelineManager component.");
            }
            var job = new BuildTestJob
            {
                JobId = Guid.NewGuid().ToString("N"),
                ProjectPath = CheckpointPrepareTool.ProjectRoot(),
                AvatarPath = AvatarAuthoringCrudCore.GetTransformPath(descriptor.transform),
                AvatarGlobalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(descriptor.gameObject).ToString(),
                ScenePath = descriptor.gameObject.scene.path ?? string.Empty,
                PipelineIdBefore = pipeline.blueprintId ?? string.Empty,
                PipelineIdAfter = pipeline.blueprintId ?? string.Empty,
                SceneDirtyBefore = descriptor.gameObject.scene.isDirty,
                SceneDirtyAfter = descriptor.gameObject.scene.isDirty,
                ConsoleBefore = UnityConsoleSnapshotReader.Capture(ConsoleEntryLimit),
                CompileBefore = CompileErrorMonitor.ReadCoreInfoSnapshot(ConsoleEntryLimit),
                SdkVersion = ReadSdkVersion(),
            };
            RecordEvent(job, "job_created", "Local Build & Test job created.");
            lock (JobLock)
            {
                Jobs[job.JobId] = job;
                activeJobId = job.JobId;
            }
            SessionState.SetString(ActiveJobSessionKey, job.JobId);
            PersistJob(job);
            RunJob(job.JobId);
            return BuildCurrentPayload(job, null, null);
        }

        private static async void RunJob(string jobId)
        {
            var job = FindLiveJob(jobId);
            if (job == null || !string.Equals(job.Status, "pending", StringComparison.Ordinal))
            {
                return;
            }

            IVRCSdkAvatarBuilderApi builder = null;
            EventHandler<object> buildStart = null;
            EventHandler<string> buildProgress = null;
            EventHandler<string> buildFinish = null;
            EventHandler<string> buildSuccess = null;
            EventHandler<string> buildError = null;
            EventHandler<SdkBuildState> stateChange = null;
            try
            {
                job.Status = "initializing_sdk";
                job.StartedUtc = DateTime.UtcNow;
                PersistJob(job);

                var descriptor = ResolveExactAvatar(job.AvatarPath);
                var currentIdentity = GlobalObjectId.GetGlobalObjectIdSlow(descriptor.gameObject).ToString();
                if (!string.Equals(currentIdentity, job.AvatarGlobalObjectId, StringComparison.Ordinal))
                {
                    throw new InvalidOperationException("The selected avatar identity changed after the job was authorized.");
                }

                builder = await AcquireBuilderAsync(job);
                builder.SelectAvatar(descriptor.gameObject);
                RecordEvent(job, "sdk_avatar_selected", job.AvatarPath);
                if (builder.BuildState != SdkBuildState.Idle)
                {
                    throw new InvalidOperationException(
                        "The VRChat SDK builder is not idle (state: " + builder.BuildState + ").");
                }

                buildStart = (_, target) =>
                {
                    job.BuildStarted = true;
                    job.MutationStarted = true;
                    job.Status = "running";
                    RecordEvent(job, "build_started", DescribeTarget(target));
                    PersistJob(job);
                };
                buildProgress = (_, progress) =>
                {
                    job.LastProgress = progress ?? string.Empty;
                    RecordEvent(job, "build_progress", job.LastProgress);
                    PersistJob(job);
                };
                buildFinish = (_, message) =>
                {
                    RecordEvent(job, "build_finished", message ?? string.Empty);
                    PersistJob(job);
                };
                buildSuccess = (_, bundlePath) =>
                {
                    UpdateBundleEvidence(job, bundlePath);
                    RecordEvent(job, "build_succeeded", bundlePath ?? string.Empty);
                    PersistJob(job);
                };
                buildError = (_, error) =>
                {
                    job.SdkError = error ?? string.Empty;
                    RecordEvent(job, "build_error", job.SdkError);
                    PersistJob(job);
                };
                stateChange = (_, state) =>
                {
                    RecordEvent(job, "build_state", state.ToString());
                    PersistJob(job);
                };

                builder.OnSdkBuildStart += buildStart;
                builder.OnSdkBuildProgress += buildProgress;
                builder.OnSdkBuildFinish += buildFinish;
                builder.OnSdkBuildSuccess += buildSuccess;
                builder.OnSdkBuildError += buildError;
                builder.OnSdkBuildStateChange += stateChange;

                RecordEvent(job, "build_invoked", "Invoking the public VRChat SDK local Build & Test API.");
                await builder.BuildAndTest(descriptor.gameObject);
                job.BuildTaskCompleted = true;
                job.LocalTestRegistered = true;
                RefreshTargetEvidence(job);
                job.Result = BuildTerminalPayload(job, true, string.Empty, string.Empty);
                CompleteJob(job, "completed");
            }
            catch (Exception exception)
            {
                job.SdkError = string.IsNullOrWhiteSpace(job.SdkError)
                    ? exception.Message ?? string.Empty
                    : job.SdkError;
                RecordEvent(job, "job_failed", exception.GetType().FullName + ": " + exception.Message);
                RefreshTargetEvidence(job);
                job.Result = BuildTerminalPayload(
                    job,
                    false,
                    "vrchat_sdk_build_test_failed",
                    exception.Message ?? "VRChat SDK local Build & Test failed.");
                job.Result["exceptionType"] = exception.GetType().FullName ?? exception.GetType().Name;
                job.Result["exceptionMessage"] = exception.Message ?? string.Empty;
                job.Result["exceptionStack"] = exception.StackTrace ?? string.Empty;
                CompleteJob(job, "error");
            }
            finally
            {
                if (builder != null)
                {
                    if (buildStart != null) builder.OnSdkBuildStart -= buildStart;
                    if (buildProgress != null) builder.OnSdkBuildProgress -= buildProgress;
                    if (buildFinish != null) builder.OnSdkBuildFinish -= buildFinish;
                    if (buildSuccess != null) builder.OnSdkBuildSuccess -= buildSuccess;
                    if (buildError != null) builder.OnSdkBuildError -= buildError;
                    if (stateChange != null) builder.OnSdkBuildStateChange -= stateChange;
                }
                // VRChat SDK Build & Test may replace editor update callbacks
                // while the Core listener remains alive. Restore the main-thread
                // invocation pump after both terminal success and failure.
                VRCForgeMcpCoreServer.ScheduleInvocationPumpRegistration();
            }
        }

        private static async Task<IVRCSdkAvatarBuilderApi> AcquireBuilderAsync(BuildTestJob job)
        {
            IVRCSdkAvatarBuilderApi builder = null;
            if (VRCSdkControlPanel.window != null
                && VRCSdkControlPanel.TryGetBuilder(out builder)
                && builder != null)
            {
                return builder;
            }

            RecordEvent(job, "sdk_panel_requested", SdkPanelMenuPath);
            if (!EditorApplication.ExecuteMenuItem(SdkPanelMenuPath))
            {
                throw new InvalidOperationException(
                    "The installed VRChat SDK control-panel menu could not be opened.");
            }

            var completion = new TaskCompletionSource<IVRCSdkAvatarBuilderApi>();
            var deadline = EditorApplication.timeSinceStartup + 30.0;
            var nextProbe = 0.0;
            EditorApplication.CallbackFunction probe = null;
            probe = () =>
            {
                if (EditorApplication.timeSinceStartup < nextProbe)
                {
                    return;
                }
                nextProbe = EditorApplication.timeSinceStartup + 0.25;
                IVRCSdkAvatarBuilderApi candidate;
                if (VRCSdkControlPanel.window != null
                    && VRCSdkControlPanel.TryGetBuilder(out candidate)
                    && candidate != null)
                {
                    EditorApplication.update -= probe;
                    completion.TrySetResult(candidate);
                    return;
                }
                if (EditorApplication.timeSinceStartup >= deadline)
                {
                    EditorApplication.update -= probe;
                    completion.TrySetException(new InvalidOperationException(
                        "The VRChat SDK avatar builder did not become available within 30 seconds."));
                }
            };
            EditorApplication.update += probe;
            probe();
            return await completion.Task;
        }

        private static JObject PollJob(string rawJobId)
        {
            Guid parsed;
            var jobId = (rawJobId ?? string.Empty).Trim().ToLowerInvariant();
            if (!Guid.TryParseExact(jobId, "N", out parsed))
            {
                throw new InvalidOperationException("jobId is invalid.");
            }

            var live = FindLiveJob(jobId);
            if (live != null)
            {
                if (live.Result != null)
                {
                    return (JObject)live.Result.DeepClone();
                }
                return BuildCurrentPayload(live, null, null);
            }

            var persisted = LoadPersistedJob(jobId);
            if (persisted == null)
            {
                return RejectedJobPayload(
                    jobId,
                    "build_test_job_not_found",
                    "The requested VRChat SDK Build & Test job was not found.");
            }
            var status = persisted.Value<string>("status") ?? string.Empty;
            if (!IsPending(status))
            {
                var result = persisted["result"] as JObject;
                return result == null ? persisted : (JObject)result.DeepClone();
            }

            var mutationStarted = persisted.Value<bool?>("mutationStarted") ?? false;
            var consoleAfter = UnityConsoleSnapshotReader.Capture(ConsoleEntryLimit);
            var interrupted = new JObject
            {
                ["ok"] = false,
                ["schema"] = Schema,
                ["status"] = "interrupted",
                ["jobId"] = jobId,
                ["localOnly"] = true,
                ["uploadAttempted"] = false,
                ["published"] = false,
                ["errorCode"] = "build_test_job_interrupted",
                ["error"] = "The Unity domain reloaded or the Core restarted before this Build & Test job reported a terminal result.",
                ["failureLayer"] = "vrchat_sdk_build_test",
                ["failurePhase"] = "job_continuation_lost",
                ["toolRoutingStarted"] = true,
                ["mutationStarted"] = mutationStarted,
                ["writeOccurred"] = mutationStarted,
                ["committed"] = false,
                ["commitState"] = mutationStarted ? "unknown" : "not_started",
                ["requestMayHaveCommitted"] = mutationStarted,
                ["checkpointRecoveryRequired"] = mutationStarted,
                ["temporaryCleanupRequired"] = false,
                ["consoleBefore"] = persisted["consoleBefore"]?.DeepClone() ?? new JObject(),
                ["consoleAfter"] = consoleAfter,
                ["consoleDelta"] = BuildConsoleDelta(persisted["consoleBefore"] as JObject, consoleAfter),
                ["compileBefore"] = persisted["compileBefore"]?.DeepClone() ?? new JObject(),
                ["compileAfter"] = CompileErrorMonitor.ReadCoreInfoSnapshot(ConsoleEntryLimit),
            };
            persisted["status"] = "interrupted";
            persisted["result"] = interrupted;
            SessionState.SetString(JobSessionPrefix + jobId, persisted.ToString(Newtonsoft.Json.Formatting.None));
            ClearActiveJob(jobId);
            return interrupted;
        }

        private static JObject BuildPreflightFailure(Exception exception)
        {
            var console = UnityConsoleSnapshotReader.Capture(ConsoleEntryLimit);
            return new JObject
            {
                ["ok"] = false,
                ["schema"] = Schema,
                ["status"] = "error",
                ["localOnly"] = true,
                ["uploadAttempted"] = false,
                ["published"] = false,
                ["errorCode"] = "build_test_preflight_failed",
                ["error"] = exception.Message ?? "VRChat SDK local Build & Test preflight failed.",
                ["failureLayer"] = "vrchat_sdk_build_test",
                ["failurePhase"] = "before_job_start",
                ["toolRoutingStarted"] = true,
                ["mutationStarted"] = false,
                ["writeOccurred"] = false,
                ["committed"] = false,
                ["commitState"] = "not_started",
                ["requestMayHaveCommitted"] = false,
                ["checkpointRecoveryRequired"] = false,
                ["temporaryCleanupRequired"] = false,
                ["consoleBefore"] = console.DeepClone(),
                ["consoleAfter"] = console,
                ["consoleDelta"] = BuildConsoleDelta(console, console),
                ["compileBefore"] = CompileErrorMonitor.ReadCoreInfoSnapshot(ConsoleEntryLimit),
                ["compileAfter"] = CompileErrorMonitor.ReadCoreInfoSnapshot(ConsoleEntryLimit),
                ["exceptionType"] = exception.GetType().FullName ?? exception.GetType().Name,
                ["exceptionMessage"] = exception.Message ?? string.Empty,
            };
        }

        private static JObject BuildTerminalPayload(
            BuildTestJob job,
            bool success,
            string errorCode,
            string error)
        {
            var consoleAfter = UnityConsoleSnapshotReader.Capture(ConsoleEntryLimit);
            var persistentSceneChange = !string.Equals(
                    job.PipelineIdBefore,
                    job.PipelineIdAfter,
                    StringComparison.Ordinal)
                || (!job.SceneDirtyBefore && job.SceneDirtyAfter);
            var mutationStarted = job.MutationStarted || persistentSceneChange;
            var payload = BuildCurrentPayload(job, consoleAfter, persistentSceneChange);
            payload["ok"] = success;
            payload["status"] = success ? "completed" : "error";
            payload["mutationStarted"] = mutationStarted;
            payload["writeOccurred"] = mutationStarted || job.LocalBuildProduced;
            payload["committed"] = success && job.LocalTestRegistered;
            payload["commitState"] = success && job.LocalTestRegistered
                ? "committed"
                : job.LocalBuildProduced ? "unknown" : mutationStarted ? "not_committed" : "not_started";
            payload["requestMayHaveCommitted"] = !success && job.LocalBuildProduced;
            payload["checkpointRecoveryRequired"] = !success && persistentSceneChange;
            payload["temporaryCleanupRequired"] = false;
            payload["failureLayer"] = success ? string.Empty : "vrchat_sdk_build_test";
            payload["failurePhase"] = success ? string.Empty : "build_and_test";
            if (!success)
            {
                payload["errorCode"] = errorCode;
                payload["error"] = string.IsNullOrWhiteSpace(job.SdkError) ? error : job.SdkError;
            }
            return payload;
        }

        private static JObject BuildCurrentPayload(
            BuildTestJob job,
            JObject consoleAfter,
            bool? persistentSceneChange)
        {
            var after = consoleAfter ?? UnityConsoleSnapshotReader.Capture(ConsoleEntryLimit);
            return new JObject
            {
                ["ok"] = true,
                ["schema"] = Schema,
                ["status"] = job.Status,
                ["jobId"] = job.JobId,
                ["localOnly"] = true,
                ["uploadAttempted"] = false,
                ["published"] = false,
                ["avatarPath"] = job.AvatarPath,
                ["avatarGlobalObjectId"] = job.AvatarGlobalObjectId,
                ["scenePath"] = job.ScenePath,
                ["sdkVersion"] = job.SdkVersion,
                ["buildStarted"] = job.BuildStarted,
                ["buildTaskCompleted"] = job.BuildTaskCompleted,
                ["localTestRegistered"] = job.LocalTestRegistered,
                ["localBuildProduced"] = job.LocalBuildProduced,
                ["bundlePath"] = job.BundlePath,
                ["bundleExists"] = job.LocalBuildProduced,
                ["bundleSizeBytes"] = job.BundleSizeBytes,
                ["lastProgress"] = job.LastProgress,
                ["sdkError"] = job.SdkError,
                ["pipelineIdBefore"] = job.PipelineIdBefore,
                ["pipelineIdAfter"] = job.PipelineIdAfter,
                ["pipelineIdAssignedDuringBuild"] = !string.IsNullOrWhiteSpace(job.PipelineIdAfter)
                    && string.IsNullOrWhiteSpace(job.PipelineIdBefore),
                ["sceneDirtyBefore"] = job.SceneDirtyBefore,
                ["sceneDirtyAfter"] = job.SceneDirtyAfter,
                ["persistentSceneChange"] = persistentSceneChange.HasValue
                    ? new JValue(persistentSceneChange.Value)
                    : JValue.CreateNull(),
                ["createdAt"] = job.CreatedUtc.ToString("o"),
                ["startedAt"] = job.StartedUtc.HasValue ? job.StartedUtc.Value.ToString("o") : string.Empty,
                ["completedAt"] = job.CompletedUtc.HasValue ? job.CompletedUtc.Value.ToString("o") : string.Empty,
                ["events"] = job.Events.DeepClone(),
                ["consoleBefore"] = job.ConsoleBefore.DeepClone(),
                ["consoleAfter"] = after,
                ["consoleDelta"] = BuildConsoleDelta(job.ConsoleBefore, after),
                ["compileBefore"] = job.CompileBefore.DeepClone(),
                ["compileAfter"] = CompileErrorMonitor.ReadCoreInfoSnapshot(ConsoleEntryLimit),
                ["toolRoutingStarted"] = true,
                ["mutationStarted"] = job.MutationStarted,
                ["writeOccurred"] = job.MutationStarted,
                ["committed"] = false,
                ["commitState"] = job.MutationStarted ? "unknown" : "not_started",
                ["requestMayHaveCommitted"] = job.MutationStarted,
                ["checkpointRecoveryRequired"] = false,
                ["temporaryCleanupRequired"] = false,
            };
        }

        private static JObject BuildConcurrentStartRejection(
            BuildTestJob activeJob,
            string errorCode,
            string error)
        {
            var console = UnityConsoleSnapshotReader.Capture(ConsoleEntryLimit);
            return new JObject
            {
                ["ok"] = false,
                ["schema"] = Schema,
                ["status"] = "error",
                ["localOnly"] = true,
                ["uploadAttempted"] = false,
                ["published"] = false,
                ["errorCode"] = errorCode,
                ["error"] = error,
                ["failureLayer"] = "vrchat_sdk_build_test",
                ["failurePhase"] = "before_job_start",
                ["toolRoutingStarted"] = true,
                ["mutationStarted"] = false,
                ["writeOccurred"] = false,
                ["committed"] = false,
                ["commitState"] = "not_started",
                ["requestMayHaveCommitted"] = false,
                ["checkpointRecoveryRequired"] = false,
                ["temporaryCleanupRequired"] = false,
                ["consoleBefore"] = console.DeepClone(),
                ["consoleAfter"] = console,
                ["consoleDelta"] = BuildConsoleDelta(console, console),
                ["compileBefore"] = CompileErrorMonitor.ReadCoreInfoSnapshot(ConsoleEntryLimit),
                ["compileAfter"] = CompileErrorMonitor.ReadCoreInfoSnapshot(ConsoleEntryLimit),
                ["activeJob"] = new JObject
                {
                    ["jobId"] = activeJob.JobId,
                    ["status"] = activeJob.Status,
                    ["avatarPath"] = activeJob.AvatarPath,
                    ["buildStarted"] = activeJob.BuildStarted,
                    ["lastProgress"] = activeJob.LastProgress,
                    ["localBuildProduced"] = activeJob.LocalBuildProduced,
                    ["createdAt"] = activeJob.CreatedUtc.ToString("o"),
                    ["startedAt"] = activeJob.StartedUtc.HasValue
                        ? activeJob.StartedUtc.Value.ToString("o")
                        : string.Empty,
                },
            };
        }

        private static JObject RejectedJobPayload(string jobId, string errorCode, string error)
        {
            var payload = BuildPreflightFailure(new InvalidOperationException(error));
            payload["jobId"] = jobId;
            payload["errorCode"] = errorCode;
            payload["error"] = error;
            payload["failurePhase"] = "job_poll";
            return payload;
        }

        private static JObject BuildConsoleDelta(JObject before, JObject after)
        {
            var beforeTotal = before?.Value<int?>("totalEntryCount") ?? 0;
            var afterTotal = after?.Value<int?>("totalEntryCount") ?? 0;
            var afterEntries = after?["entries"] as JArray ?? new JArray();
            var reset = afterTotal < beforeTotal;
            var deltaEntries = reset
                ? new JArray(afterEntries.Select(item => item.DeepClone()))
                : new JArray(afterEntries
                    .OfType<JObject>()
                    .Where(item => (item.Value<int?>("consoleIndex") ?? -1) >= beforeTotal)
                    .Select(item => item.DeepClone()));
            return new JObject
            {
                ["schema"] = "vrcforge.unity_console_delta.v1",
                ["baselineTotalEntryCount"] = beforeTotal,
                ["afterTotalEntryCount"] = afterTotal,
                ["consoleWasCleared"] = reset,
                ["newEntryCount"] = deltaEntries.Count,
                ["entries"] = deltaEntries,
            };
        }

        private static void UpdateBundleEvidence(BuildTestJob job, string bundlePath)
        {
            job.BundlePath = (bundlePath ?? string.Empty).Trim();
            job.LocalBuildProduced = !string.IsNullOrWhiteSpace(job.BundlePath)
                && File.Exists(job.BundlePath);
            job.BundleSizeBytes = job.LocalBuildProduced
                ? new FileInfo(job.BundlePath).Length
                : 0L;
        }

        private static void RefreshTargetEvidence(BuildTestJob job)
        {
            try
            {
                var descriptor = ResolveExactAvatar(job.AvatarPath);
                var pipeline = descriptor.GetComponent<VRC.Core.PipelineManager>();
                job.PipelineIdAfter = pipeline == null ? string.Empty : pipeline.blueprintId ?? string.Empty;
                job.SceneDirtyAfter = descriptor.gameObject.scene.isDirty;
            }
            catch (Exception exception)
            {
                RecordEvent(job, "target_readback_failed", exception.Message ?? string.Empty);
            }
            UpdateBundleEvidence(job, job.BundlePath);
        }

        private static VRCAvatarDescriptor ResolveExactAvatar(string avatarPath)
        {
            var normalized = AvatarAuthoringCrudCore.NormalizePath(avatarPath);
            var matches = Resources.FindObjectsOfTypeAll<VRCAvatarDescriptor>()
                .Where(item => item != null
                    && item.gameObject.scene.IsValid()
                    && item.gameObject.scene.isLoaded
                    && !EditorUtility.IsPersistent(item)
                    && string.Equals(
                        AvatarAuthoringCrudCore.NormalizePath(
                            AvatarAuthoringCrudCore.GetTransformPath(item.transform)),
                        normalized,
                        StringComparison.Ordinal))
                .ToArray();
            if (matches.Length != 1)
            {
                throw new InvalidOperationException(
                    matches.Length == 0
                        ? "No loaded-scene VRChat avatar exactly matches avatarPath: " + avatarPath
                        : "avatarPath is ambiguous across loaded scenes: " + avatarPath);
            }
            return matches[0];
        }

        private static string ReadSdkVersion()
        {
            try
            {
                var package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(
                    typeof(IVRCSdkAvatarBuilderApi).Assembly);
                return package == null ? string.Empty : package.version ?? string.Empty;
            }
            catch
            {
                return string.Empty;
            }
        }

        private static string DescribeTarget(object target)
        {
            var gameObject = target as GameObject;
            return gameObject == null
                ? target?.ToString() ?? string.Empty
                : AvatarAuthoringCrudCore.GetTransformPath(gameObject.transform);
        }

        private static void RecordEvent(BuildTestJob job, string eventName, string message)
        {
            if (job == null)
            {
                return;
            }
            while (job.Events.Count >= EventLimit)
            {
                job.Events.RemoveAt(0);
            }
            job.Events.Add(new JObject
            {
                ["at"] = DateTime.UtcNow.ToString("o"),
                ["event"] = eventName ?? string.Empty,
                ["message"] = message ?? string.Empty,
            });
        }

        private static void CompleteJob(BuildTestJob job, string status)
        {
            job.Status = status;
            job.CompletedUtc = DateTime.UtcNow;
            if (job.Result != null)
            {
                job.Result["status"] = status;
                job.Result["completedAt"] = job.CompletedUtc.Value.ToString("o");
            }
            PersistJob(job);
            ClearActiveJob(job.JobId);
        }

        private static BuildTestJob FindLiveJob(string jobId)
        {
            lock (JobLock)
            {
                BuildTestJob job;
                return Jobs.TryGetValue(jobId, out job) ? job : null;
            }
        }

        private static bool IsPending(string status)
        {
            return string.Equals(status, "pending", StringComparison.OrdinalIgnoreCase)
                || string.Equals(status, "running", StringComparison.OrdinalIgnoreCase)
                || string.Equals(status, "initializing_sdk", StringComparison.OrdinalIgnoreCase);
        }

        private static void PersistJob(BuildTestJob job)
        {
            var payload = new JObject
            {
                ["schema"] = Schema,
                ["jobId"] = job.JobId,
                ["status"] = job.Status,
                ["avatarPath"] = job.AvatarPath,
                ["avatarGlobalObjectId"] = job.AvatarGlobalObjectId,
                ["mutationStarted"] = job.MutationStarted,
                ["consoleBefore"] = job.ConsoleBefore.DeepClone(),
                ["compileBefore"] = job.CompileBefore.DeepClone(),
                ["result"] = job.Result == null ? JValue.CreateNull() : job.Result.DeepClone(),
            };
            SessionState.SetString(
                JobSessionPrefix + job.JobId,
                payload.ToString(Newtonsoft.Json.Formatting.None));
        }

        private static JObject LoadPersistedJob(string jobId)
        {
            try
            {
                var raw = SessionState.GetString(JobSessionPrefix + jobId, string.Empty);
                if (string.IsNullOrWhiteSpace(raw))
                {
                    return null;
                }
                var payload = JObject.Parse(raw);
                return string.Equals(payload.Value<string>("jobId"), jobId, StringComparison.Ordinal)
                    ? payload
                    : null;
            }
            catch
            {
                return null;
            }
        }

        private static void MarkPersistedActiveJobInterrupted()
        {
            var persistedActiveId = SessionState.GetString(ActiveJobSessionKey, string.Empty);
            if (string.IsNullOrWhiteSpace(persistedActiveId)
                || FindLiveJob(persistedActiveId) != null)
            {
                return;
            }
            var persisted = LoadPersistedJob(persistedActiveId);
            if (persisted != null && IsPending(persisted.Value<string>("status")))
            {
                // Polling a job whose live task disappeared records the full
                // interrupted result, including before/after Console evidence.
                // It never retries the SDK call or restores user state.
                PollJob(persistedActiveId);
                return;
            }
            SessionState.EraseString(ActiveJobSessionKey);
        }

        private static void ClearActiveJob(string jobId)
        {
            lock (JobLock)
            {
                if (string.Equals(activeJobId, jobId, StringComparison.Ordinal))
                {
                    activeJobId = string.Empty;
                }
            }
            if (string.Equals(
                SessionState.GetString(ActiveJobSessionKey, string.Empty),
                jobId,
                StringComparison.Ordinal))
            {
                SessionState.EraseString(ActiveJobSessionKey);
            }
        }
    }

    internal static class VrchatAvatarUploadShared
    {
        internal const int ConsoleEntryLimit = 120;

        internal sealed class Readiness
        {
            internal VRCAvatarDescriptor Descriptor;
            internal string ProjectPath = string.Empty;
            internal string AvatarPath = string.Empty;
            internal string AvatarGlobalObjectId = string.Empty;
            internal string ScenePath = string.Empty;
            internal string PipelineId = string.Empty;
            internal string SdkUserId = string.Empty;
            internal string SdkUserName = string.Empty;
            internal string Platform = string.Empty;
            internal string SdkVersion = string.Empty;
            internal string UploadMode = string.Empty;
            internal string BuildType = string.Empty;
            internal string MetadataMode = string.Empty;
            internal string ReleaseStatus = string.Empty;
            internal string AvatarName = string.Empty;
            internal string Description = string.Empty;
            internal string PrimaryStyleId = string.Empty;
            internal string PrimaryStyleName = string.Empty;
            internal string SecondaryStyleId = string.Empty;
            internal string SecondaryStyleName = string.Empty;
            internal readonly JArray ContentWarnings = new JArray();
            internal readonly JArray AuthorTags = new JArray();
            internal string ThumbnailMode = string.Empty;
            internal string ThumbnailPath = string.Empty;
            internal string ThumbnailSha256 = string.Empty;
            internal long ThumbnailSizeBytes;
            internal bool CanPublishAvatars;
            internal bool BuilderAvailable;
            internal string BuilderBuildState = string.Empty;
            internal string BuilderUploadState = string.Empty;
            internal bool Ready;
            internal readonly JArray BlockingReasons = new JArray();
            internal string Digest = string.Empty;

            internal JObject ToPayload()
            {
                return new JObject
                {
                    ["ok"] = true,
                    ["schema"] = "vrcforge.vrchat_avatar_upload_readiness.v1",
                    ["operation"] = "build_and_upload_avatar",
                    ["ready"] = Ready,
                    ["projectPath"] = ProjectPath,
                    ["avatarPath"] = AvatarPath,
                    ["avatarGlobalObjectId"] = AvatarGlobalObjectId,
                    ["scenePath"] = ScenePath,
                    ["currentPipelineId"] = PipelineId,
                    ["sdkUserId"] = SdkUserId,
                    ["sdkUserName"] = SdkUserName,
                    ["canPublishAvatars"] = CanPublishAvatars,
                    ["platform"] = Platform,
                    ["sdkVersion"] = SdkVersion,
                    ["uploadMode"] = UploadMode,
                    ["requestedBuildType"] = BuildType,
                    ["effectiveBuildType"] = "publish",
                    ["requestedPlatforms"] = new JArray(Platform),
                    ["metadata"] = new JObject
                    {
                        ["mode"] = MetadataMode,
                        ["name"] = AvatarName,
                        ["description"] = Description,
                        ["visibility"] = ReleaseStatus,
                        ["primaryStyle"] = StylePayload(PrimaryStyleId, PrimaryStyleName),
                        ["secondaryStyle"] = StylePayload(SecondaryStyleId, SecondaryStyleName),
                        ["contentWarnings"] = ContentWarnings.DeepClone(),
                        ["authorTags"] = AuthorTags.DeepClone(),
                    },
                    ["thumbnail"] = new JObject
                    {
                        ["mode"] = ThumbnailMode,
                        ["path"] = ThumbnailPath,
                        ["sha256"] = ThumbnailSha256,
                        ["sizeBytes"] = ThumbnailSizeBytes,
                    },
                    ["builderAvailable"] = BuilderAvailable,
                    ["builderBuildState"] = BuilderBuildState,
                    ["builderUploadState"] = BuilderUploadState,
                    ["blockingReasons"] = BlockingReasons.DeepClone(),
                    ["capabilities"] = CapabilityPayload(),
                    ["sdkPanel"] = new JObject
                    {
                        ["pendingMetadataReadable"] = false,
                        ["buildTypeUiState"] = JValue.CreateNull(),
                        ["platformUiSelection"] = JValue.CreateNull(),
                        ["alerts"] = new JObject
                        {
                            ["coverage"] = "blocking_gate_at_build_only",
                            ["authoritativeForFullPanelAlerts"] = false,
                            ["exactEnumerationAvailable"] = false,
                            ["items"] = JValue.CreateNull(),
                            ["reasonCode"] = "sdk_public_api_unavailable",
                        },
                        ["autoFix"] = new JObject
                        {
                            ["enumerationAvailable"] = false,
                            ["executionAvailable"] = false,
                            ["applied"] = false,
                            ["reasonCode"] = "sdk_public_api_unavailable",
                        },
                    },
                    ["readinessDigest"] = Digest,
                    ["remoteRollbackAvailable"] = false,
                    ["mutationStarted"] = false,
                    ["writeOccurred"] = false,
                    ["committed"] = false,
                    ["commitState"] = "not_started",
                };
            }

            private static JToken StylePayload(string id, string name)
            {
                return string.IsNullOrWhiteSpace(id) && string.IsNullOrWhiteSpace(name)
                    ? (JToken)JValue.CreateNull()
                    : new JObject { ["id"] = id ?? string.Empty, ["name"] = name ?? string.Empty };
            }

            private static JObject CapabilityPayload()
            {
                return new JObject
                {
                    ["selectedAvatar"] = "read_write",
                    ["remoteMetadata"] = "read_write",
                    ["visibility"] = new JArray("private", "public"),
                    ["styleCatalog"] = "read",
                    ["thumbnail"] = "replace_or_keep",
                    ["sdkPanelPendingMetadata"] = "unavailable",
                    ["sdkPanelAlerts"] = "blocking_gate_only",
                    ["sdkPanelAutoFix"] = "unavailable",
                    ["buildType"] = new JArray("build_and_upload"),
                    ["platforms"] = "one_current_platform_per_call",
                };
            }
        }

        internal static Readiness Inspect(JObject raw, bool requireExpectedBindings)
        {
            CheckpointPrepareTool.ValidateProject(raw ?? new JObject());
            var avatarPath = RequiredText(raw, "avatarPath", 1024);
            var uploadMode = RequiredText(raw, "uploadMode", 16).ToLowerInvariant();
            if (uploadMode != "create" && uploadMode != "update")
            {
                throw new InvalidOperationException("uploadMode must be exactly create or update.");
            }

            var descriptor = ResolveExactAvatar(avatarPath);
            var pipeline = descriptor.GetComponent<VRC.Core.PipelineManager>();
            if (pipeline == null)
            {
                throw new InvalidOperationException("The selected avatar has no PipelineManager component.");
            }

            var result = new Readiness
            {
                Descriptor = descriptor,
                ProjectPath = CheckpointPrepareTool.ProjectRoot(),
                AvatarPath = AvatarAuthoringCrudCore.GetTransformPath(descriptor.transform),
                AvatarGlobalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(descriptor.gameObject).ToString(),
                ScenePath = descriptor.gameObject.scene.path ?? string.Empty,
                PipelineId = pipeline.blueprintId ?? string.Empty,
                Platform = EditorUserBuildSettings.activeBuildTarget.ToString(),
                SdkVersion = ReadSdkVersion(),
                UploadMode = uploadMode,
                BuildType = OptionalText(raw, "buildType", 32),
            };

            if (!string.Equals(result.BuildType, "build_and_upload", StringComparison.Ordinal))
            {
                result.BlockingReasons.Add("buildType must be exactly build_and_upload.");
            }
            ParsePlatform(raw, result);
            ParseMetadata(raw, result);

            var user = VRC.Core.APIUser.CurrentUser;
            result.SdkUserId = user == null ? string.Empty : user.id ?? string.Empty;
            result.SdkUserName = user == null ? string.Empty : user.displayName ?? string.Empty;
            result.CanPublishAvatars = user != null && user.canPublishAvatars;

            IVRCSdkAvatarBuilderApi builder = null;
            result.BuilderAvailable = VRCSdkControlPanel.window != null
                && VRCSdkControlPanel.TryGetBuilder(out builder)
                && builder != null;
            if (result.BuilderAvailable)
            {
                result.BuilderBuildState = builder.BuildState.ToString();
                result.BuilderUploadState = builder.UploadState.ToString();
                if (builder.BuildState != SdkBuildState.Idle || builder.UploadState != SdkUploadState.Idle)
                {
                    result.BlockingReasons.Add("The VRChat SDK builder or uploader is not idle.");
                }
            }

            if (EditorApplication.isCompiling || EditorApplication.isUpdating)
            {
                result.BlockingReasons.Add("Unity is compiling or updating the AssetDatabase.");
            }
            if (EditorApplication.isPlayingOrWillChangePlaymode)
            {
                result.BlockingReasons.Add("Unity Play Mode must be stopped before upload.");
            }
            if (BuildPipeline.isBuildingPlayer)
            {
                result.BlockingReasons.Add("Another Unity build is already active.");
            }
            if (EditorUtility.scriptCompilationFailed)
            {
                result.BlockingReasons.Add("Unity reports script compilation failure.");
            }
            if (string.IsNullOrWhiteSpace(result.SdkUserId))
            {
                result.BlockingReasons.Add("The VRChat SDK has no authenticated current user.");
            }
            else if (!result.CanPublishAvatars)
            {
                result.BlockingReasons.Add("The current VRChat SDK user cannot publish avatars.");
            }
            if (uploadMode == "create")
            {
                if (!string.IsNullOrWhiteSpace(result.PipelineId))
                {
                    result.BlockingReasons.Add("Create mode requires an empty PipelineManager blueprint ID.");
                }
                if (result.MetadataMode != "replace") result.BlockingReasons.Add("Create mode requires metadata.mode=replace.");
                if (string.IsNullOrWhiteSpace(result.AvatarName)) result.BlockingReasons.Add("Create mode requires metadata.name.");
                ResolveThumbnail(raw, result, true);
            }
            else
            {
                if (string.IsNullOrWhiteSpace(result.PipelineId))
                {
                    result.BlockingReasons.Add("Update mode requires an existing PipelineManager blueprint ID.");
                }
                ResolveThumbnail(raw, result, false);
            }

            result.Digest = ComputeDigest(result);
            if (requireExpectedBindings)
            {
                RequireExact(raw, "expectedAvatarGlobalObjectId", result.AvatarGlobalObjectId);
                RequireExactAllowEmpty(raw, "expectedCurrentPipelineId", result.PipelineId);
                RequireExact(raw, "expectedSdkUserId", result.SdkUserId);
                RequireExact(raw, "expectedPlatform", result.Platform);
                RequireExact(raw, "readinessDigest", result.Digest);
                var expectedThumbnail = OptionalText(raw?["thumbnail"] as JObject, "sha256", 64).ToLowerInvariant();
                if (!string.Equals(expectedThumbnail, result.ThumbnailSha256, StringComparison.Ordinal))
                {
                    throw new InvalidOperationException("The upload thumbnail changed after readiness was inspected.");
                }
            }

            result.Ready = result.BlockingReasons.Count == 0;
            return result;
        }

        internal static async Task<IVRCSdkAvatarBuilderApi> AcquireBuilderAsync()
        {
            IVRCSdkAvatarBuilderApi builder;
            if (VRCSdkControlPanel.window != null
                && VRCSdkControlPanel.TryGetBuilder(out builder)
                && builder != null)
            {
                return builder;
            }
            if (!EditorApplication.ExecuteMenuItem("VRChat SDK/Show Control Panel"))
            {
                throw new InvalidOperationException("The installed VRChat SDK control-panel menu could not be opened.");
            }
            var completion = new TaskCompletionSource<IVRCSdkAvatarBuilderApi>();
            var deadline = EditorApplication.timeSinceStartup + 30.0;
            EditorApplication.CallbackFunction probe = null;
            probe = () =>
            {
                IVRCSdkAvatarBuilderApi candidate;
                if (VRCSdkControlPanel.window != null
                    && VRCSdkControlPanel.TryGetBuilder(out candidate)
                    && candidate != null)
                {
                    EditorApplication.update -= probe;
                    completion.TrySetResult(candidate);
                    return;
                }
                if (EditorApplication.timeSinceStartup >= deadline)
                {
                    EditorApplication.update -= probe;
                    completion.TrySetException(new InvalidOperationException(
                        "The VRChat SDK avatar builder did not become available within 30 seconds."));
                }
            };
            EditorApplication.update += probe;
            probe();
            return await completion.Task;
        }

        internal static VRCAvatarDescriptor ResolveExactAvatar(string avatarPath)
        {
            var normalized = AvatarAuthoringCrudCore.NormalizePath(avatarPath);
            var matches = Resources.FindObjectsOfTypeAll<VRCAvatarDescriptor>()
                .Where(item => item != null
                    && item.gameObject.scene.IsValid()
                    && item.gameObject.scene.isLoaded
                    && !EditorUtility.IsPersistent(item)
                    && string.Equals(
                        AvatarAuthoringCrudCore.NormalizePath(
                            AvatarAuthoringCrudCore.GetTransformPath(item.transform)),
                        normalized,
                        StringComparison.Ordinal))
                .ToArray();
            if (matches.Length != 1)
            {
                throw new InvalidOperationException(matches.Length == 0
                    ? "No loaded-scene VRChat avatar exactly matches avatarPath: " + avatarPath
                    : "avatarPath is ambiguous across loaded scenes: " + avatarPath);
            }
            return matches[0];
        }

        internal static JObject ConsoleDelta(JObject before, JObject after)
        {
            var beforeTotal = before?.Value<int?>("totalEntryCount") ?? 0;
            var afterTotal = after?.Value<int?>("totalEntryCount") ?? 0;
            var afterEntries = after?["entries"] as JArray ?? new JArray();
            var reset = afterTotal < beforeTotal;
            var entries = reset
                ? new JArray(afterEntries.Select(item => item.DeepClone()))
                : new JArray(afterEntries.OfType<JObject>()
                    .Where(item => (item.Value<int?>("consoleIndex") ?? -1) >= beforeTotal)
                    .Select(item => item.DeepClone()));
            return new JObject
            {
                ["schema"] = "vrcforge.unity_console_delta.v1",
                ["baselineTotalEntryCount"] = beforeTotal,
                ["afterTotalEntryCount"] = afterTotal,
                ["consoleWasCleared"] = reset,
                ["newEntryCount"] = entries.Count,
                ["entries"] = entries,
            };
        }

        internal static string ReadSdkVersion()
        {
            try
            {
                var package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(
                    typeof(IVRCSdkAvatarBuilderApi).Assembly);
                return package == null ? string.Empty : package.version ?? string.Empty;
            }
            catch
            {
                return string.Empty;
            }
        }

        private static void ParsePlatform(JObject raw, Readiness result)
        {
            var platforms = raw?["platforms"] as JArray;
            if (platforms == null || platforms.Count != 1 || platforms[0].Type != JTokenType.String)
            {
                result.BlockingReasons.Add("platforms must contain exactly one current Unity build target.");
                return;
            }
            var requested = ((string)platforms[0] ?? string.Empty).Trim();
            if (!string.Equals(requested, result.Platform, StringComparison.Ordinal))
            {
                result.BlockingReasons.Add("The requested platform must equal Unity's current active build target.");
            }
        }

        private static void ParseMetadata(JObject raw, Readiness result)
        {
            var metadata = raw?["metadata"] as JObject;
            if (metadata == null)
            {
                result.BlockingReasons.Add("metadata must be an object.");
                return;
            }
            result.MetadataMode = OptionalText(metadata, "mode", 32).ToLowerInvariant();
            if (result.MetadataMode != "replace" && result.MetadataMode != "preserve_remote")
            {
                result.BlockingReasons.Add("metadata.mode must be replace or preserve_remote.");
                return;
            }
            if (result.MetadataMode == "preserve_remote")
            {
                if (result.UploadMode == "create") result.BlockingReasons.Add("Create mode cannot preserve remote metadata.");
                return;
            }

            result.AvatarName = OptionalText(metadata, "name", 64);
            result.Description = OptionalText(metadata, "description", 256);
            result.ReleaseStatus = OptionalText(metadata, "visibility", 16).ToLowerInvariant();
            if (result.ReleaseStatus != "private" && result.ReleaseStatus != "public")
            {
                result.BlockingReasons.Add("metadata.visibility must be private or public.");
            }
            ParseStyle(metadata["primaryStyle"], "metadata.primaryStyle", out result.PrimaryStyleId, out result.PrimaryStyleName);
            ParseStyle(metadata["secondaryStyle"], "metadata.secondaryStyle", out result.SecondaryStyleId, out result.SecondaryStyleName);
            if (string.IsNullOrWhiteSpace(result.PrimaryStyleId) && !string.IsNullOrWhiteSpace(result.SecondaryStyleId))
            {
                result.BlockingReasons.Add("A secondary style requires a primary style.");
            }
            if (!string.IsNullOrWhiteSpace(result.PrimaryStyleId)
                && string.Equals(result.PrimaryStyleId, result.SecondaryStyleId, StringComparison.Ordinal))
            {
                result.BlockingReasons.Add("Primary and secondary styles must be different.");
            }
            foreach (var warning in ReadStringArray(metadata, "contentWarnings", 5, 64))
            {
                if (warning != "content_sex" && warning != "content_adult"
                    && warning != "content_violence" && warning != "content_gore"
                    && warning != "content_horror")
                {
                    result.BlockingReasons.Add("metadata.contentWarnings contains an unsupported value: " + warning);
                }
                else if (!result.ContentWarnings.Any(item => string.Equals(item.ToString(), warning, StringComparison.Ordinal)))
                {
                    result.ContentWarnings.Add(warning);
                }
            }
            foreach (var tag in ReadStringArray(metadata, "authorTags", 10, 64))
            {
                var normalized = tag.Trim().ToLowerInvariant().Replace(' ', '_');
                if (normalized.Length == 0 || normalized.StartsWith("author_tag_", StringComparison.Ordinal))
                {
                    result.BlockingReasons.Add("metadata.authorTags must contain plain non-empty tag names without the author_tag_ prefix.");
                }
                else if (normalized.Any(character => !(char.IsLetterOrDigit(character) || character == '_' || character == '-')))
                {
                    result.BlockingReasons.Add("metadata.authorTags contains unsupported characters: " + tag);
                }
                else if (!result.AuthorTags.Any(item => string.Equals(item.ToString(), normalized, StringComparison.Ordinal)))
                {
                    result.AuthorTags.Add(normalized);
                }
            }
        }

        private static void ParseStyle(JToken token, string label, out string id, out string name)
        {
            id = string.Empty;
            name = string.Empty;
            if (token == null || token.Type == JTokenType.Null) return;
            var value = token as JObject;
            if (value == null) throw new InvalidOperationException(label + " must be null or an object.");
            id = OptionalText(value, "id", 128);
            name = OptionalText(value, "name", 128);
            if (string.IsNullOrWhiteSpace(id) || string.IsNullOrWhiteSpace(name))
            {
                throw new InvalidOperationException(label + " must contain both id and name.");
            }
        }

        private static IEnumerable<string> ReadStringArray(JObject raw, string name, int maximumItems, int maximumLength)
        {
            var token = raw?[name];
            if (token == null || token.Type == JTokenType.Null) return Enumerable.Empty<string>();
            var array = token as JArray;
            if (array == null || array.Count > maximumItems || array.Any(item => item.Type != JTokenType.String))
            {
                throw new InvalidOperationException(name + " must be a bounded string array.");
            }
            var values = array.Select(item => ((string)item ?? string.Empty).Trim()).ToArray();
            if (values.Any(value => value.Length == 0 || value.Length > maximumLength))
            {
                throw new InvalidOperationException(name + " contains an empty or overlong value.");
            }
            return values;
        }

        private static void ResolveThumbnail(JObject raw, Readiness result, bool required)
        {
            var thumbnail = raw?["thumbnail"] as JObject;
            if (thumbnail == null)
            {
                result.BlockingReasons.Add("thumbnail must be an object.");
                return;
            }
            result.ThumbnailMode = OptionalText(thumbnail, "mode", 16).ToLowerInvariant();
            if (result.ThumbnailMode != "keep" && result.ThumbnailMode != "replace")
            {
                result.BlockingReasons.Add("thumbnail.mode must be keep or replace.");
                return;
            }
            if (required && result.ThumbnailMode != "replace")
            {
                result.BlockingReasons.Add("Create mode requires thumbnail.mode=replace.");
            }
            if (result.ThumbnailMode == "keep") return;
            var requested = OptionalText(thumbnail, "path", 2048);
            if (string.IsNullOrWhiteSpace(requested))
            {
                if (required)
                {
                    result.BlockingReasons.Add("Create mode requires thumbnailPath.");
                }
                return;
            }
            string fullPath;
            try
            {
                fullPath = Path.GetFullPath(requested);
            }
            catch (Exception)
            {
                result.BlockingReasons.Add("thumbnailPath is invalid.");
                return;
            }
            var extension = Path.GetExtension(fullPath).ToLowerInvariant();
            if ((extension != ".png" && extension != ".jpg" && extension != ".jpeg")
                || !File.Exists(fullPath))
            {
                result.BlockingReasons.Add("thumbnailPath must be one existing PNG or JPEG file.");
                return;
            }
            var info = new FileInfo(fullPath);
            if (info.Length <= 0 || info.Length > 16L * 1024L * 1024L)
            {
                result.BlockingReasons.Add("The thumbnail file must be between 1 byte and 16 MiB.");
                return;
            }
            result.ThumbnailPath = fullPath;
            result.ThumbnailSizeBytes = info.Length;
            result.ThumbnailSha256 = Sha256File(fullPath);
        }

        private static string ComputeDigest(Readiness value)
        {
            var fields = new[]
            {
                value.ProjectPath, value.AvatarPath, value.AvatarGlobalObjectId, value.ScenePath,
                value.PipelineId, value.SdkUserId, value.Platform, value.SdkVersion, value.UploadMode,
                value.BuildType, value.MetadataMode, value.ReleaseStatus, value.AvatarName, value.Description,
                value.PrimaryStyleId, value.PrimaryStyleName, value.SecondaryStyleId, value.SecondaryStyleName,
                value.ContentWarnings.ToString(Newtonsoft.Json.Formatting.None),
                value.AuthorTags.ToString(Newtonsoft.Json.Formatting.None), value.ThumbnailMode,
                value.ThumbnailPath, value.ThumbnailSha256, value.ThumbnailSizeBytes.ToString(),
            };
            return Sha256Text(string.Join("\n", fields.Select(Field)));
        }

        private static string Field(string value)
        {
            var text = value ?? string.Empty;
            return Encoding.UTF8.GetByteCount(text) + ":" + text;
        }

        private static string Sha256Text(string value)
        {
            using (var sha = SHA256.Create())
            {
                return Hex(sha.ComputeHash(Encoding.UTF8.GetBytes(value ?? string.Empty)));
            }
        }

        private static string Sha256File(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var sha = SHA256.Create())
            {
                return Hex(sha.ComputeHash(stream));
            }
        }

        private static string Hex(byte[] bytes)
        {
            return BitConverter.ToString(bytes ?? new byte[0]).Replace("-", string.Empty).ToLowerInvariant();
        }

        private static string RequiredText(JObject raw, string name, int maximum)
        {
            var value = OptionalText(raw, name, maximum);
            if (string.IsNullOrWhiteSpace(value))
            {
                throw new InvalidOperationException(name + " is required.");
            }
            return value;
        }

        private static string OptionalText(JObject raw, string name, int maximum)
        {
            var token = raw?[name];
            if (token == null || token.Type == JTokenType.Null)
            {
                return string.Empty;
            }
            if (token.Type != JTokenType.String)
            {
                throw new InvalidOperationException(name + " must be a string.");
            }
            var value = ((string)token ?? string.Empty).Trim();
            if (value.Length > maximum)
            {
                throw new InvalidOperationException(name + " is too long.");
            }
            return value;
        }

        private static void RequireExact(JObject raw, string name, string actual)
        {
            var expected = RequiredText(raw, name, 2048);
            if (!string.Equals(expected, actual ?? string.Empty, StringComparison.Ordinal))
            {
                throw new InvalidOperationException(name + " no longer matches the current Unity/SDK state.");
            }
        }

        private static void RequireExactAllowEmpty(JObject raw, string name, string actual)
        {
            var token = raw?[name];
            if (token == null || token.Type != JTokenType.String)
            {
                throw new InvalidOperationException(name + " is required and must be a string.");
            }
            var expected = ((string)token ?? string.Empty).Trim();
            if (!string.Equals(expected, actual ?? string.Empty, StringComparison.Ordinal))
            {
                throw new InvalidOperationException(name + " no longer matches the current Unity/SDK state.");
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_avatar_upload_readiness",
        Summary = "when-to-use: inspect one exact loaded avatar and an explicit private-or-public VRChat build-and-upload request, including metadata, styles, content warnings, tags, thumbnail, account, platform, and public-API coverage limits. when-NOT-to-use: do not upload, build, change metadata, sign in, claim full SDK-panel alert enumeration, or treat readiness as publication success. Negative example: do not call it merely because an avatar exists in the scene.",
        Category = "diagnostics",
        Access = VRCForgeCommandAccess.ReadOnly
    )]
    public static class VrchatAvatarUploadReadinessTool
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                return VRCForgeToolResult.Completed(
                    "Inspected VRChat avatar build-and-upload readiness without changing local or remote state.",
                    VrchatAvatarUploadShared.Inspect(@params ?? new JObject(), false).ToPayload());
            }
            catch (Exception exception)
            {
                return VRCForgeToolResult.FailedWithCode(
                    "vrchat_avatar_upload_readiness_failed",
                    exception.Message ?? "VRChat avatar upload readiness failed.",
                    new JObject
                    {
                        ["ok"] = false,
                        ["schema"] = "vrcforge.vrchat_avatar_upload_readiness.v1",
                        ["operation"] = "build_and_upload_avatar",
                        ["failureLayer"] = "vrchat_sdk_upload_readiness",
                        ["failurePhase"] = "inspect",
                        ["mutationStarted"] = false,
                        ["writeOccurred"] = false,
                        ["committed"] = false,
                        ["commitState"] = "not_started",
                        ["requestMayHaveCommitted"] = false,
                        ["remoteRollbackAvailable"] = false,
                        ["exceptionType"] = exception.GetType().FullName ?? exception.GetType().Name,
                        ["exceptionMessage"] = exception.Message ?? string.Empty,
                    });
            }
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_build_and_upload_avatar",
        Summary = "when-to-use: after exact readiness and one visibility-aware user confirmation, build and upload one loaded avatar with explicit private-or-public metadata through the public VRChat SDK, then poll the returned jobId. when-NOT-to-use: do not auto-retry, silently change visibility, upload an ambiguous avatar, invoke SDK-panel private Auto Fix actions, or claim local checkpoints can undo a remote upload. Negative example: do not call it when the user only requested local Build & Test.",
        Category = "avatar",
        UsesContinuation = true,
        ContinuationAction = "status",
        ContinuationTimeoutSeconds = 1800
    )]
    public static class VrchatAvatarUploadTool
    {
        public const string ToolName = "vrc_build_and_upload_avatar";
        private const string Schema = "vrcforge.vrchat_avatar_upload.v1";
        private const string JobPrefix = "VRCForge.VrchatAvatarUpload.Job.";
        private const string ActiveJobKey = "VRCForge.VrchatAvatarUpload.ActiveJob.v1";
        private const int EventLimit = 240;
        private static readonly object Gate = new object();
        private static readonly Dictionary<string, UploadJob> Jobs =
            new Dictionary<string, UploadJob>(StringComparer.Ordinal);
        private static string activeJobId = string.Empty;

        private sealed class UploadJob
        {
            internal string JobId = string.Empty;
            internal string ProjectPath = string.Empty;
            internal string AvatarPath = string.Empty;
            internal string AvatarGlobalObjectId = string.Empty;
            internal string ScenePath = string.Empty;
            internal string SdkVersion = string.Empty;
            internal string SdkUserId = string.Empty;
            internal string SdkUserName = string.Empty;
            internal string Platform = string.Empty;
            internal string UploadMode = string.Empty;
            internal string BuildType = string.Empty;
            internal string MetadataMode = string.Empty;
            internal string ReleaseStatus = string.Empty;
            internal string AvatarName = string.Empty;
            internal string Description = string.Empty;
            internal string PrimaryStyleId = string.Empty;
            internal string PrimaryStyleName = string.Empty;
            internal string SecondaryStyleId = string.Empty;
            internal string SecondaryStyleName = string.Empty;
            internal JArray ContentWarnings = new JArray();
            internal JArray AuthorTags = new JArray();
            internal string ThumbnailMode = string.Empty;
            internal string ThumbnailPath = string.Empty;
            internal string ThumbnailSha256 = string.Empty;
            internal string PipelineIdBefore = string.Empty;
            internal string PipelineIdAfter = string.Empty;
            internal string RemoteAvatarId = string.Empty;
            internal string BundlePath = string.Empty;
            internal string LastProgress = string.Empty;
            internal string SdkBuildError = string.Empty;
            internal string SdkUploadError = string.Empty;
            internal string Status = "pending";
            internal bool SceneDirtyBefore;
            internal bool SceneDirtyAfter;
            internal bool InvocationStarted;
            internal bool BuildStarted;
            internal bool BuildSucceeded;
            internal bool UploadStarted;
            internal bool UploadSucceeded;
            internal bool TaskCompleted;
            internal bool MetadataPostUpdateAttempted;
            internal bool MetadataReadbackMatched;
            internal float UploadProgressPercent;
            internal long BundleSizeBytes;
            internal DateTime CreatedUtc = DateTime.UtcNow;
            internal DateTime? StartedUtc;
            internal DateTime? CompletedUtc;
            internal JObject ConsoleBefore = new JObject();
            internal JObject CompileBefore = new JObject();
            internal JObject RemoteMetadataBefore;
            internal JObject RemoteMetadataRequested;
            internal JObject RemoteMetadataAfter;
            internal JArray StyleCatalog = new JArray();
            internal JObject Result;
            internal readonly JArray Events = new JArray();
        }

        public static object HandleCommand(JObject @params)
        {
            JObject payload;
            try
            {
                var jobId = (@params?["jobId"]?.ToString() ?? string.Empty).Trim();
                payload = string.IsNullOrWhiteSpace(jobId)
                    ? StartJob(@params ?? new JObject())
                    : PollJob(jobId);
            }
            catch (Exception exception)
            {
                payload = PreflightFailure(exception);
            }
            var status = (payload.Value<string>("status") ?? string.Empty).ToLowerInvariant();
            if (status == "pending" || status == "initializing_sdk" || status == "building" || status == "uploading")
            {
                return VRCForgeToolResult.Waiting("VRChat avatar upload is still running.", 1.0, payload);
            }
            if (status == "completed")
            {
                return VRCForgeToolResult.Completed("VRChat avatar upload completed.", payload);
            }
            return VRCForgeToolResult.FailedWithCode(
                payload.Value<string>("errorCode") ?? "vrchat_avatar_upload_failed",
                payload.Value<string>("error") ?? "VRChat avatar upload failed.",
                payload);
        }

        private static JObject StartJob(JObject raw)
        {
            CheckpointPrepareTool.EnsureEditorReady();
            UploadJob existing = null;
            lock (Gate)
            {
                if (!string.IsNullOrWhiteSpace(activeJobId)) Jobs.TryGetValue(activeJobId, out existing);
            }
            if (existing != null && IsPending(existing.Status))
            {
                return ConcurrentFailure(existing);
            }
            MarkInterruptedActiveJob();

            var readiness = VrchatAvatarUploadShared.Inspect(raw, true);
            if (!readiness.Ready)
            {
                var reason = readiness.BlockingReasons.Count == 0
                    ? "VRChat avatar upload readiness did not pass."
                    : string.Join(" ", readiness.BlockingReasons.Select(item => item.ToString()));
                throw new InvalidOperationException(reason);
            }
            var job = new UploadJob
            {
                JobId = Guid.NewGuid().ToString("N"),
                ProjectPath = readiness.ProjectPath,
                AvatarPath = readiness.AvatarPath,
                AvatarGlobalObjectId = readiness.AvatarGlobalObjectId,
                ScenePath = readiness.ScenePath,
                SdkVersion = readiness.SdkVersion,
                SdkUserId = readiness.SdkUserId,
                SdkUserName = readiness.SdkUserName,
                Platform = readiness.Platform,
                UploadMode = readiness.UploadMode,
                BuildType = readiness.BuildType,
                MetadataMode = readiness.MetadataMode,
                ReleaseStatus = readiness.ReleaseStatus,
                AvatarName = readiness.AvatarName,
                Description = readiness.Description,
                PrimaryStyleId = readiness.PrimaryStyleId,
                PrimaryStyleName = readiness.PrimaryStyleName,
                SecondaryStyleId = readiness.SecondaryStyleId,
                SecondaryStyleName = readiness.SecondaryStyleName,
                ContentWarnings = (JArray)readiness.ContentWarnings.DeepClone(),
                AuthorTags = (JArray)readiness.AuthorTags.DeepClone(),
                ThumbnailMode = readiness.ThumbnailMode,
                ThumbnailPath = readiness.ThumbnailPath,
                ThumbnailSha256 = readiness.ThumbnailSha256,
                PipelineIdBefore = readiness.PipelineId,
                PipelineIdAfter = readiness.PipelineId,
                SceneDirtyBefore = readiness.Descriptor.gameObject.scene.isDirty,
                SceneDirtyAfter = readiness.Descriptor.gameObject.scene.isDirty,
                ConsoleBefore = UnityConsoleSnapshotReader.Capture(VrchatAvatarUploadShared.ConsoleEntryLimit),
                CompileBefore = CompileErrorMonitor.ReadCoreInfoSnapshot(VrchatAvatarUploadShared.ConsoleEntryLimit),
            };
            Record(job, "job_created", "Bound one exact VRChat avatar build-and-upload job.");
            lock (Gate)
            {
                Jobs[job.JobId] = job;
                activeJobId = job.JobId;
            }
            SessionState.SetString(ActiveJobKey, job.JobId);
            Persist(job);
            RunJob(job.JobId);
            return CurrentPayload(job, null);
        }

        private static async void RunJob(string jobId)
        {
            var job = Find(jobId);
            if (job == null || job.Status != "pending") return;
            IVRCSdkAvatarBuilderApi builder = null;
            EventHandler<object> buildStart = null;
            EventHandler<string> buildProgress = null;
            EventHandler<string> buildSuccess = null;
            EventHandler<string> buildError = null;
            EventHandler uploadStart = null;
            EventHandler<(string status, float percentage)> uploadProgress = null;
            EventHandler<string> uploadSuccess = null;
            EventHandler<string> uploadError = null;
            EventHandler<string> uploadFinish = null;
            try
            {
                job.Status = "initializing_sdk";
                job.StartedUtc = DateTime.UtcNow;
                Persist(job);
                var descriptor = VrchatAvatarUploadShared.ResolveExactAvatar(job.AvatarPath);
                if (!string.Equals(
                    GlobalObjectId.GetGlobalObjectIdSlow(descriptor.gameObject).ToString(),
                    job.AvatarGlobalObjectId,
                    StringComparison.Ordinal))
                {
                    throw new InvalidOperationException("The selected avatar identity changed after approval.");
                }
                var currentUser = VRC.Core.APIUser.CurrentUser;
                if (currentUser == null
                    || !string.Equals(currentUser.id ?? string.Empty, job.SdkUserId, StringComparison.Ordinal)
                    || !currentUser.canPublishAvatars)
                {
                    throw new InvalidOperationException("The authenticated VRChat SDK user changed or cannot publish avatars.");
                }
                builder = await VrchatAvatarUploadShared.AcquireBuilderAsync();
                builder.SelectAvatar(descriptor.gameObject);
                if (builder.BuildState != SdkBuildState.Idle || builder.UploadState != SdkUploadState.Idle)
                {
                    throw new InvalidOperationException(
                        "The VRChat SDK builder/uploader is not idle (build: " + builder.BuildState
                        + ", upload: " + builder.UploadState + ").");
                }

                buildStart = (_, target) =>
                {
                    job.BuildStarted = true;
                    job.Status = "building";
                    Record(job, "build_started", target?.ToString() ?? string.Empty);
                    Persist(job);
                };
                buildProgress = (_, progress) =>
                {
                    job.LastProgress = progress ?? string.Empty;
                    Record(job, "build_progress", job.LastProgress);
                    Persist(job);
                };
                buildSuccess = (_, bundle) =>
                {
                    job.BuildSucceeded = true;
                    job.BundlePath = bundle ?? string.Empty;
                    if (File.Exists(job.BundlePath)) job.BundleSizeBytes = new FileInfo(job.BundlePath).Length;
                    Record(job, "build_succeeded", job.BundlePath);
                    Persist(job);
                };
                buildError = (_, error) =>
                {
                    job.SdkBuildError = error ?? string.Empty;
                    Record(job, "build_error", job.SdkBuildError);
                    Persist(job);
                };
                uploadStart = (_, __) =>
                {
                    job.UploadStarted = true;
                    job.Status = "uploading";
                    Record(job, "upload_started", "VRChat SDK upload started.");
                    Persist(job);
                };
                uploadProgress = (_, progress) =>
                {
                    job.LastProgress = progress.status ?? string.Empty;
                    job.UploadProgressPercent = progress.percentage;
                    Record(job, "upload_progress", job.LastProgress);
                    Persist(job);
                };
                uploadSuccess = (_, avatarId) =>
                {
                    job.UploadSucceeded = true;
                    job.RemoteAvatarId = avatarId ?? string.Empty;
                    Record(job, "upload_succeeded", job.RemoteAvatarId);
                    Persist(job);
                };
                uploadError = (_, error) =>
                {
                    job.SdkUploadError = error ?? string.Empty;
                    Record(job, "upload_error", job.SdkUploadError);
                    Persist(job);
                };
                uploadFinish = (_, message) =>
                {
                    Record(job, "upload_finished", message ?? string.Empty);
                    Persist(job);
                };
                builder.OnSdkBuildStart += buildStart;
                builder.OnSdkBuildProgress += buildProgress;
                builder.OnSdkBuildSuccess += buildSuccess;
                builder.OnSdkBuildError += buildError;
                builder.OnSdkUploadStart += uploadStart;
                builder.OnSdkUploadProgress += uploadProgress;
                builder.OnSdkUploadSuccess += uploadSuccess;
                builder.OnSdkUploadError += uploadError;
                builder.OnSdkUploadFinish += uploadFinish;

                var styles = await VRCApi.GetAvatarStyles();
                job.StyleCatalog = StyleCatalogPayload(styles);
                ValidateRequestedStyles(job, styles);

                VRCAvatar avatar;
                var pipeline = descriptor.GetComponent<VRC.Core.PipelineManager>();
                if (job.UploadMode == "create")
                {
                    avatar = new VRCAvatar
                    {
                        Name = job.AvatarName,
                        Description = job.Description,
                        Tags = RequestedTags(job),
                        ReleaseStatus = job.ReleaseStatus,
                        Styles = new VRCAvatar.AvatarStyles
                        {
                            Primary = job.PrimaryStyleId,
                            Secondary = job.SecondaryStyleId,
                        },
                    };
                    job.RemoteMetadataRequested = MetadataPayload(avatar);
                }
                else
                {
                    if (pipeline == null
                        || !string.Equals(pipeline.blueprintId ?? string.Empty, job.PipelineIdBefore, StringComparison.Ordinal))
                    {
                        throw new InvalidOperationException("The update target PipelineManager ID changed after approval.");
                    }
                    avatar = await VRCApi.GetAvatar(job.PipelineIdBefore, true, cancellationToken: CancellationToken.None);
                    if (!string.Equals(avatar.AuthorId ?? string.Empty, job.SdkUserId, StringComparison.Ordinal))
                    {
                        throw new InvalidOperationException("The authenticated VRChat SDK user does not own the update target.");
                    }
                    job.RemoteMetadataBefore = MetadataPayload(avatar);
                    if (job.MetadataMode == "replace") ApplyRequestedMetadata(job, ref avatar);
                    else CopyRemoteMetadataIntoJob(job, avatar);
                    job.RemoteMetadataRequested = MetadataPayload(avatar);
                }

                job.InvocationStarted = true;
                job.Status = "building";
                Record(job, "sdk_invoked", "Invoking the public VRChat SDK BuildAndUpload API.");
                Persist(job);
                await builder.BuildAndUpload(
                    descriptor.gameObject,
                    avatar,
                    string.IsNullOrWhiteSpace(job.ThumbnailPath) ? null : job.ThumbnailPath,
                    CancellationToken.None);
                job.TaskCompleted = true;
                Refresh(job);
                if (!job.UploadSucceeded)
                {
                    job.UploadSucceeded = !string.IsNullOrWhiteSpace(job.PipelineIdAfter);
                    job.RemoteAvatarId = job.PipelineIdAfter;
                }
                if (!job.UploadSucceeded || string.IsNullOrWhiteSpace(job.RemoteAvatarId))
                {
                    throw new InvalidOperationException("VRChat SDK BuildAndUpload returned without a confirmed avatar ID.");
                }
                var remoteAfter = await VRCApi.GetAvatar(job.RemoteAvatarId, true, CancellationToken.None);
                if (job.MetadataMode == "replace" && !MetadataMatches(job, remoteAfter))
                {
                    job.MetadataPostUpdateAttempted = true;
                    remoteAfter = await VRCApi.UpdateAvatarInfo(job.RemoteAvatarId, RequestedMetadataRecord(job, remoteAfter), CancellationToken.None);
                    remoteAfter = await VRCApi.GetAvatar(job.RemoteAvatarId, true, CancellationToken.None);
                }
                job.RemoteMetadataAfter = MetadataPayload(remoteAfter);
                job.MetadataReadbackMatched = job.MetadataMode != "replace" || MetadataMatches(job, remoteAfter);
                if (!job.MetadataReadbackMatched)
                {
                    throw new InvalidOperationException("VRChat upload completed, but remote metadata readback does not match the approved request.");
                }
                job.Result = TerminalPayload(job, true, string.Empty, string.Empty);
                Complete(job, "completed");
            }
            catch (Exception exception)
            {
                job.SdkUploadError = string.IsNullOrWhiteSpace(job.SdkUploadError)
                    ? exception.Message ?? string.Empty
                    : job.SdkUploadError;
                Record(job, "job_failed", exception.GetType().FullName + ": " + exception.Message);
                Refresh(job);
                job.Result = TerminalPayload(
                    job,
                    false,
                    "vrchat_avatar_upload_failed",
                    exception.Message ?? "VRChat avatar upload failed.");
                job.Result["exceptionType"] = exception.GetType().FullName ?? exception.GetType().Name;
                job.Result["exceptionMessage"] = exception.Message ?? string.Empty;
                job.Result["exceptionStack"] = exception.StackTrace ?? string.Empty;
                Complete(job, "error");
            }
            finally
            {
                if (builder != null)
                {
                    if (buildStart != null) builder.OnSdkBuildStart -= buildStart;
                    if (buildProgress != null) builder.OnSdkBuildProgress -= buildProgress;
                    if (buildSuccess != null) builder.OnSdkBuildSuccess -= buildSuccess;
                    if (buildError != null) builder.OnSdkBuildError -= buildError;
                    if (uploadStart != null) builder.OnSdkUploadStart -= uploadStart;
                    if (uploadProgress != null) builder.OnSdkUploadProgress -= uploadProgress;
                    if (uploadSuccess != null) builder.OnSdkUploadSuccess -= uploadSuccess;
                    if (uploadError != null) builder.OnSdkUploadError -= uploadError;
                    if (uploadFinish != null) builder.OnSdkUploadFinish -= uploadFinish;
                }
                VRCForgeMcpCoreServer.ScheduleInvocationPumpRegistration();
            }
        }

        private static JArray StyleCatalogPayload(IEnumerable<VRCAvatarStyle> styles)
        {
            return new JArray((styles ?? Enumerable.Empty<VRCAvatarStyle>())
                .OrderBy(item => item.StyleName ?? string.Empty, StringComparer.Ordinal)
                .ThenBy(item => item.ID ?? string.Empty, StringComparer.Ordinal)
                .Select(item => new JObject
                {
                    ["id"] = item.ID ?? string.Empty,
                    ["name"] = item.StyleName ?? string.Empty,
                }));
        }

        private static void ValidateRequestedStyles(UploadJob job, IEnumerable<VRCAvatarStyle> styles)
        {
            if (job.MetadataMode != "replace") return;
            var catalog = (styles ?? Enumerable.Empty<VRCAvatarStyle>()).ToArray();
            ValidateStyle(catalog, job.PrimaryStyleId, job.PrimaryStyleName, "primaryStyle");
            ValidateStyle(catalog, job.SecondaryStyleId, job.SecondaryStyleName, "secondaryStyle");
        }

        private static void ValidateStyle(
            IEnumerable<VRCAvatarStyle> styles,
            string requestedId,
            string requestedName,
            string label)
        {
            if (string.IsNullOrWhiteSpace(requestedId) && string.IsNullOrWhiteSpace(requestedName)) return;
            if (!styles.Any(item => string.Equals(item.ID ?? string.Empty, requestedId, StringComparison.Ordinal)
                && string.Equals(item.StyleName ?? string.Empty, requestedName, StringComparison.Ordinal)))
            {
                throw new InvalidOperationException("The requested " + label + " id/name pair is not in the current VRChat style catalog.");
            }
        }

        private static List<string> RequestedTags(UploadJob job)
        {
            return job.ContentWarnings.Select(item => item.ToString())
                .Concat(job.AuthorTags.Select(item => "author_tag_" + item))
                .Distinct(StringComparer.Ordinal)
                .OrderBy(item => item, StringComparer.Ordinal)
                .ToList();
        }

        private static void ApplyRequestedMetadata(UploadJob job, ref VRCAvatar avatar)
        {
            var existing = avatar.Tags ?? new List<string>();
            if (existing.Contains("admin_content_reviewed"))
            {
                var lockedWarnings = existing.Where(IsContentWarning).ToArray();
                var requestedWarnings = new HashSet<string>(
                    job.ContentWarnings.Select(item => item.ToString()),
                    StringComparer.Ordinal);
                if (lockedWarnings.Any(item => !requestedWarnings.Contains(item)))
                {
                    throw new InvalidOperationException("VRChat has locked one or more reviewed content warnings; the requested metadata would remove a locked warning.");
                }
            }
            var protectedTags = existing.Where(item => !IsContentWarning(item)
                && !item.StartsWith("author_tag_", StringComparison.Ordinal)).ToList();
            avatar.Name = job.AvatarName;
            avatar.Description = job.Description;
            avatar.ReleaseStatus = job.ReleaseStatus;
            avatar.Styles = new VRCAvatar.AvatarStyles
            {
                Primary = job.PrimaryStyleId,
                Secondary = job.SecondaryStyleId,
            };
            avatar.Tags = protectedTags.Concat(RequestedTags(job))
                .Distinct(StringComparer.Ordinal)
                .OrderBy(item => item, StringComparer.Ordinal)
                .ToList();
        }

        private static void CopyRemoteMetadataIntoJob(UploadJob job, VRCAvatar avatar)
        {
            job.AvatarName = avatar.Name ?? string.Empty;
            job.Description = avatar.Description ?? string.Empty;
            job.ReleaseStatus = avatar.ReleaseStatus ?? string.Empty;
            job.PrimaryStyleId = avatar.Styles.Primary ?? string.Empty;
            job.SecondaryStyleId = avatar.Styles.Secondary ?? string.Empty;
            job.ContentWarnings = new JArray((avatar.Tags ?? new List<string>()).Where(IsContentWarning));
            job.AuthorTags = new JArray((avatar.Tags ?? new List<string>())
                .Where(item => item.StartsWith("author_tag_", StringComparison.Ordinal))
                .Select(item => item.Substring("author_tag_".Length)));
        }

        private static VRCAvatar RequestedMetadataRecord(UploadJob job, VRCAvatar remote)
        {
            ApplyRequestedMetadata(job, ref remote);
            return remote;
        }

        private static bool MetadataMatches(UploadJob job, VRCAvatar remote)
        {
            if (!string.Equals(remote.Name ?? string.Empty, job.AvatarName, StringComparison.Ordinal)
                || !string.Equals(remote.Description ?? string.Empty, job.Description, StringComparison.Ordinal)
                || !string.Equals(remote.ReleaseStatus ?? string.Empty, job.ReleaseStatus, StringComparison.Ordinal)
                || !string.Equals(remote.Styles.Primary ?? string.Empty, job.PrimaryStyleId, StringComparison.Ordinal)
                || !string.Equals(remote.Styles.Secondary ?? string.Empty, job.SecondaryStyleId, StringComparison.Ordinal))
            {
                return false;
            }
            var remoteEditable = new HashSet<string>((remote.Tags ?? new List<string>())
                .Where(item => IsContentWarning(item) || item.StartsWith("author_tag_", StringComparison.Ordinal)),
                StringComparer.Ordinal);
            return remoteEditable.SetEquals(RequestedTags(job));
        }

        private static JObject MetadataPayload(VRCAvatar avatar)
        {
            var tags = avatar.Tags ?? new List<string>();
            return new JObject
            {
                ["id"] = avatar.ID ?? string.Empty,
                ["name"] = avatar.Name ?? string.Empty,
                ["description"] = avatar.Description ?? string.Empty,
                ["visibility"] = avatar.ReleaseStatus ?? string.Empty,
                ["primaryStyleId"] = avatar.Styles.Primary ?? string.Empty,
                ["secondaryStyleId"] = avatar.Styles.Secondary ?? string.Empty,
                ["contentWarnings"] = new JArray(tags.Where(IsContentWarning).OrderBy(item => item, StringComparer.Ordinal)),
                ["authorTags"] = new JArray(tags.Where(item => item.StartsWith("author_tag_", StringComparison.Ordinal))
                    .Select(item => item.Substring("author_tag_".Length)).OrderBy(item => item, StringComparer.Ordinal)),
                ["protectedOrUnknownTags"] = new JArray(tags.Where(item => !IsContentWarning(item)
                    && !item.StartsWith("author_tag_", StringComparison.Ordinal)).OrderBy(item => item, StringComparer.Ordinal)),
                ["imageUrl"] = avatar.ImageUrl ?? string.Empty,
                ["thumbnailImageUrl"] = avatar.ThumbnailImageUrl ?? string.Empty,
                ["authorId"] = avatar.AuthorId ?? string.Empty,
                ["authorName"] = avatar.AuthorName ?? string.Empty,
                ["pendingUpload"] = avatar.PendingUpload,
                ["version"] = avatar.Version,
            };
        }

        private static bool IsContentWarning(string value)
        {
            return value == "content_sex" || value == "content_adult" || value == "content_violence"
                || value == "content_gore" || value == "content_horror";
        }

        private static JObject PollJob(string rawJobId)
        {
            Guid parsed;
            var jobId = (rawJobId ?? string.Empty).Trim().ToLowerInvariant();
            if (!Guid.TryParseExact(jobId, "N", out parsed))
            {
                throw new InvalidOperationException("jobId must be exactly 32 lowercase hexadecimal characters.");
            }
            var live = Find(jobId);
            if (live != null) return live.Result == null ? CurrentPayload(live, null) : (JObject)live.Result.DeepClone();
            var persisted = Load(jobId);
            if (persisted == null)
            {
                var missing = PreflightFailure(new InvalidOperationException("The requested VRChat upload job was not found."));
                missing["jobId"] = jobId;
                missing["errorCode"] = "vrchat_avatar_upload_job_not_found";
                missing["failurePhase"] = "job_poll";
                return missing;
            }
            var result = persisted["result"] as JObject;
            if (result != null) return (JObject)result.DeepClone();
            var interrupted = new JObject
            {
                ["ok"] = false,
                ["schema"] = Schema,
                ["operation"] = "build_and_upload_avatar",
                ["status"] = "interrupted",
                ["jobId"] = jobId,
                ["errorCode"] = "vrchat_avatar_upload_interrupted",
                ["error"] = "The Unity domain or Core restarted before the upload reported a terminal result. Inspect VRChat and the PipelineManager ID; do not retry automatically.",
                ["failureLayer"] = "vrchat_sdk_upload",
                ["failurePhase"] = "job_continuation_lost",
                ["mutationStarted"] = persisted.Value<bool?>("invocationStarted") ?? false,
                ["writeOccurred"] = persisted.Value<bool?>("invocationStarted") ?? false,
                ["committed"] = false,
                ["commitState"] = "unknown",
                ["remoteCommitState"] = "unknown",
                ["requestMayHaveCommitted"] = true,
                ["checkpointRecoveryRequired"] = false,
                ["manualRecoveryRequired"] = true,
                ["remoteRollbackAvailable"] = false,
                ["consoleBefore"] = persisted["consoleBefore"]?.DeepClone() ?? new JObject(),
                ["consoleAfter"] = UnityConsoleSnapshotReader.Capture(VrchatAvatarUploadShared.ConsoleEntryLimit),
            };
            persisted["status"] = "interrupted";
            persisted["result"] = interrupted;
            SessionState.SetString(JobPrefix + jobId, persisted.ToString(Newtonsoft.Json.Formatting.None));
            ClearActive(jobId);
            return interrupted;
        }

        private static JObject TerminalPayload(UploadJob job, bool success, string errorCode, string error)
        {
            var payload = CurrentPayload(job, UnityConsoleSnapshotReader.Capture(VrchatAvatarUploadShared.ConsoleEntryLimit));
            payload["ok"] = success;
            payload["status"] = success ? "completed" : "error";
            payload["committed"] = success && job.UploadSucceeded && job.TaskCompleted;
            payload["commitState"] = success ? "committed" : job.InvocationStarted ? "unknown" : "not_started";
            payload["remoteCommitState"] = success ? "committed" : job.InvocationStarted ? "unknown" : "not_started";
            payload["requestMayHaveCommitted"] = !success && job.InvocationStarted;
            payload["manualRecoveryRequired"] = !success && job.InvocationStarted;
            if (!success)
            {
                payload["errorCode"] = errorCode;
                payload["error"] = string.IsNullOrWhiteSpace(job.SdkUploadError) ? error : job.SdkUploadError;
                payload["failureLayer"] = "vrchat_sdk_upload";
                payload["failurePhase"] = job.UploadStarted ? "upload" : job.BuildStarted ? "build" : "pre_upload";
            }
            return payload;
        }

        private static JObject CurrentPayload(UploadJob job, JObject consoleAfter)
        {
            var after = consoleAfter ?? UnityConsoleSnapshotReader.Capture(VrchatAvatarUploadShared.ConsoleEntryLimit);
            return new JObject
            {
                ["ok"] = true,
                ["schema"] = Schema,
                ["operation"] = "build_and_upload_avatar",
                ["status"] = job.Status,
                ["jobId"] = job.JobId,
                ["localOnly"] = false,
                ["uploadAttempted"] = job.InvocationStarted,
                ["uploaded"] = job.UploadSucceeded,
                ["published"] = job.UploadSucceeded,
                ["releasedPublicly"] = job.UploadSucceeded && job.ReleaseStatus == "public",
                ["releaseStatus"] = job.ReleaseStatus,
                ["avatarName"] = job.AvatarName,
                ["avatarPath"] = job.AvatarPath,
                ["avatarGlobalObjectId"] = job.AvatarGlobalObjectId,
                ["scenePath"] = job.ScenePath,
                ["sdkVersion"] = job.SdkVersion,
                ["sdkUserId"] = job.SdkUserId,
                ["sdkUserName"] = job.SdkUserName,
                ["platform"] = job.Platform,
                ["requestedPlatforms"] = new JArray(job.Platform),
                ["effectiveBuildType"] = "publish",
                ["uploadMode"] = job.UploadMode,
                ["metadataMode"] = job.MetadataMode,
                ["remoteMetadataBefore"] = job.RemoteMetadataBefore == null ? JValue.CreateNull() : job.RemoteMetadataBefore.DeepClone(),
                ["remoteMetadataRequested"] = job.RemoteMetadataRequested == null ? JValue.CreateNull() : job.RemoteMetadataRequested.DeepClone(),
                ["remoteMetadataAfter"] = job.RemoteMetadataAfter == null ? JValue.CreateNull() : job.RemoteMetadataAfter.DeepClone(),
                ["styleCatalog"] = job.StyleCatalog.DeepClone(),
                ["metadataPostUpdateAttempted"] = job.MetadataPostUpdateAttempted,
                ["metadataReadbackMatched"] = job.MetadataReadbackMatched,
                ["metadataCommitState"] = job.MetadataReadbackMatched ? "committed" : job.InvocationStarted ? "unknown" : "not_started",
                ["visibilityCommitState"] = job.MetadataReadbackMatched ? "committed" : job.InvocationStarted ? "unknown" : "not_started",
                ["thumbnailPath"] = job.ThumbnailPath,
                ["thumbnailSha256"] = job.ThumbnailSha256,
                ["thumbnailMode"] = job.ThumbnailMode,
                ["pipelineIdBefore"] = job.PipelineIdBefore,
                ["pipelineIdAfter"] = job.PipelineIdAfter,
                ["remoteAvatarId"] = job.RemoteAvatarId,
                ["remoteRecordReserved"] = string.IsNullOrWhiteSpace(job.PipelineIdBefore) && !string.IsNullOrWhiteSpace(job.PipelineIdAfter),
                ["buildStarted"] = job.BuildStarted,
                ["buildSucceeded"] = job.BuildSucceeded,
                ["uploadStarted"] = job.UploadStarted,
                ["uploadSucceeded"] = job.UploadSucceeded,
                ["uploadProgressStatus"] = job.LastProgress,
                ["uploadProgressPercent"] = job.UploadProgressPercent,
                ["sdkBuildError"] = job.SdkBuildError,
                ["sdkUploadError"] = job.SdkUploadError,
                ["bundlePath"] = job.BundlePath,
                ["bundleExists"] = !string.IsNullOrWhiteSpace(job.BundlePath) && File.Exists(job.BundlePath),
                ["bundleSizeBytes"] = job.BundleSizeBytes,
                ["sceneDirtyBefore"] = job.SceneDirtyBefore,
                ["sceneDirtyAfter"] = job.SceneDirtyAfter,
                ["requiresSceneSave"] = job.SceneDirtyAfter,
                ["createdAt"] = job.CreatedUtc.ToString("o"),
                ["startedAt"] = job.StartedUtc.HasValue ? job.StartedUtc.Value.ToString("o") : string.Empty,
                ["completedAt"] = job.CompletedUtc.HasValue ? job.CompletedUtc.Value.ToString("o") : string.Empty,
                ["events"] = job.Events.DeepClone(),
                ["consoleBefore"] = job.ConsoleBefore.DeepClone(),
                ["consoleAfter"] = after,
                ["consoleDelta"] = VrchatAvatarUploadShared.ConsoleDelta(job.ConsoleBefore, after),
                ["compileBefore"] = job.CompileBefore.DeepClone(),
                ["compileAfter"] = CompileErrorMonitor.ReadCoreInfoSnapshot(VrchatAvatarUploadShared.ConsoleEntryLimit),
                ["toolRoutingStarted"] = true,
                ["mutationStarted"] = job.InvocationStarted,
                ["writeOccurred"] = job.InvocationStarted,
                ["committed"] = false,
                ["commitState"] = job.InvocationStarted ? "unknown" : "not_started",
                ["remoteCommitState"] = job.InvocationStarted ? "unknown" : "not_started",
                ["requestMayHaveCommitted"] = job.InvocationStarted,
                ["checkpointRecoveryRequired"] = false,
                ["manualRecoveryRequired"] = false,
                ["remoteRollbackAvailable"] = false,
                ["sdkPanelAlerts"] = new JObject
                {
                    ["coverage"] = job.BuildSucceeded ? "blocking_gate_passed_by_build" : "blocking_gate_at_build_only",
                    ["authoritativeForFullPanelAlerts"] = false,
                    ["exactEnumerationAvailable"] = false,
                    ["items"] = JValue.CreateNull(),
                    ["reasonCode"] = "sdk_public_api_unavailable",
                },
                ["sdkPanelAutoFix"] = new JObject
                {
                    ["enumerationAvailable"] = false,
                    ["executionAvailable"] = false,
                    ["applied"] = false,
                    ["reasonCode"] = "sdk_public_api_unavailable",
                },
            };
        }

        private static JObject PreflightFailure(Exception exception)
        {
            var console = UnityConsoleSnapshotReader.Capture(VrchatAvatarUploadShared.ConsoleEntryLimit);
            return new JObject
            {
                ["ok"] = false,
                ["schema"] = Schema,
                ["operation"] = "build_and_upload_avatar",
                ["status"] = "error",
                ["errorCode"] = "vrchat_avatar_upload_preflight_failed",
                ["error"] = exception.Message ?? "VRChat avatar upload preflight failed.",
                ["failureLayer"] = "vrchat_sdk_upload",
                ["failurePhase"] = "before_job_start",
                ["toolRoutingStarted"] = true,
                ["mutationStarted"] = false,
                ["writeOccurred"] = false,
                ["committed"] = false,
                ["commitState"] = "not_started",
                ["remoteCommitState"] = "not_started",
                ["requestMayHaveCommitted"] = false,
                ["checkpointRecoveryRequired"] = false,
                ["manualRecoveryRequired"] = false,
                ["remoteRollbackAvailable"] = false,
                ["consoleBefore"] = console.DeepClone(),
                ["consoleAfter"] = console,
                ["consoleDelta"] = VrchatAvatarUploadShared.ConsoleDelta(console, console),
                ["compileBefore"] = CompileErrorMonitor.ReadCoreInfoSnapshot(VrchatAvatarUploadShared.ConsoleEntryLimit),
                ["compileAfter"] = CompileErrorMonitor.ReadCoreInfoSnapshot(VrchatAvatarUploadShared.ConsoleEntryLimit),
                ["exceptionType"] = exception.GetType().FullName ?? exception.GetType().Name,
                ["exceptionMessage"] = exception.Message ?? string.Empty,
            };
        }

        private static JObject ConcurrentFailure(UploadJob active)
        {
            var payload = PreflightFailure(new InvalidOperationException("Another VRChat avatar upload job is already running."));
            payload["errorCode"] = "vrchat_avatar_upload_already_running";
            payload["activeJob"] = new JObject
            {
                ["jobId"] = active.JobId,
                ["status"] = active.Status,
                ["avatarPath"] = active.AvatarPath,
                ["uploadMode"] = active.UploadMode,
            };
            return payload;
        }

        private static void Refresh(UploadJob job)
        {
            try
            {
                var descriptor = VrchatAvatarUploadShared.ResolveExactAvatar(job.AvatarPath);
                var pipeline = descriptor.GetComponent<VRC.Core.PipelineManager>();
                job.PipelineIdAfter = pipeline == null ? string.Empty : pipeline.blueprintId ?? string.Empty;
                job.SceneDirtyAfter = descriptor.gameObject.scene.isDirty;
                if (string.IsNullOrWhiteSpace(job.RemoteAvatarId)) job.RemoteAvatarId = job.PipelineIdAfter;
            }
            catch (Exception exception)
            {
                Record(job, "target_readback_failed", exception.Message ?? string.Empty);
            }
        }

        private static void Record(UploadJob job, string name, string message)
        {
            while (job.Events.Count >= EventLimit) job.Events.RemoveAt(0);
            job.Events.Add(new JObject
            {
                ["at"] = DateTime.UtcNow.ToString("o"),
                ["event"] = name ?? string.Empty,
                ["message"] = message ?? string.Empty,
            });
        }

        private static void Complete(UploadJob job, string status)
        {
            job.Status = status;
            job.CompletedUtc = DateTime.UtcNow;
            if (job.Result != null)
            {
                job.Result["status"] = status;
                job.Result["completedAt"] = job.CompletedUtc.Value.ToString("o");
            }
            Persist(job);
            ClearActive(job.JobId);
        }

        private static UploadJob Find(string jobId)
        {
            lock (Gate)
            {
                UploadJob job;
                return Jobs.TryGetValue(jobId, out job) ? job : null;
            }
        }

        private static bool IsPending(string status)
        {
            return status == "pending" || status == "initializing_sdk" || status == "building" || status == "uploading";
        }

        private static void Persist(UploadJob job)
        {
            var payload = new JObject
            {
                ["schema"] = Schema,
                ["jobId"] = job.JobId,
                ["status"] = job.Status,
                ["invocationStarted"] = job.InvocationStarted,
                ["consoleBefore"] = job.ConsoleBefore.DeepClone(),
                ["result"] = job.Result == null ? JValue.CreateNull() : job.Result.DeepClone(),
            };
            SessionState.SetString(JobPrefix + job.JobId, payload.ToString(Newtonsoft.Json.Formatting.None));
        }

        private static JObject Load(string jobId)
        {
            try
            {
                var raw = SessionState.GetString(JobPrefix + jobId, string.Empty);
                return string.IsNullOrWhiteSpace(raw) ? null : JObject.Parse(raw);
            }
            catch
            {
                return null;
            }
        }

        private static void MarkInterruptedActiveJob()
        {
            var id = SessionState.GetString(ActiveJobKey, string.Empty);
            if (string.IsNullOrWhiteSpace(id) || Find(id) != null) return;
            var persisted = Load(id);
            if (persisted != null && IsPending(persisted.Value<string>("status") ?? string.Empty))
            {
                PollJob(id);
                return;
            }
            SessionState.EraseString(ActiveJobKey);
        }

        private static void ClearActive(string jobId)
        {
            lock (Gate)
            {
                if (activeJobId == jobId) activeJobId = string.Empty;
            }
            if (SessionState.GetString(ActiveJobKey, string.Empty) == jobId)
            {
                SessionState.EraseString(ActiveJobKey);
            }
        }
    }
}
