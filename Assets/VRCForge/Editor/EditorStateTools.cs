using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_select_scene_object",
        Summary = "Select and ping one exact loaded-scene GameObject in the Unity Editor without changing scene data."
    )]
    public static class SelectSceneObjectTool
    {
        public class Parameters
        {
            [VRCForgeInput("Exact loaded-scene hierarchy path.")] public string gameObjectPath { get; set; } = "";
        }

        public static object HandleCommand(JObject @params)
        {
            var requestedPath = (@params?["gameObjectPath"]?.ToString() ?? string.Empty).Trim();
            if (string.IsNullOrEmpty(requestedPath))
            {
                return VRCForgeToolResult.FailedWithCode(
                    "gameobject_path_required",
                    "gameObjectPath is required.",
                    NoMutation());
            }

            GameObject target;
            try
            {
                target = ComponentCrudCore.ResolveGameObject(requestedPath);
            }
            catch (ComponentCrudCore.GameObjectNotFoundException exception)
            {
                return VRCForgeToolResult.FailedWithCode(
                    "gameobject_not_found",
                    exception.Message,
                    NoMutation(new { gameObjectPath = requestedPath }));
            }
            catch (Exception exception)
            {
                return VRCForgeToolResult.FailedWithCode(
                    "scene_object_selection_rejected",
                    exception.Message,
                    NoMutation(new { gameObjectPath = requestedPath }));
            }

            var canonicalPath = ComponentCrudCore.GetHierarchyPath(target.transform);
            var before = Selection.activeGameObject
                ? ComponentCrudCore.GetHierarchyPath(Selection.activeGameObject.transform)
                : string.Empty;
            var hierarchyReveal = ClearHierarchySearchFilters();
            Selection.activeGameObject = target;
            EditorGUIUtility.PingObject(target);
            var after = Selection.activeGameObject
                ? ComponentCrudCore.GetHierarchyPath(Selection.activeGameObject.transform)
                : string.Empty;
            if (!string.Equals(after, canonicalPath, StringComparison.Ordinal))
            {
                return VRCForgeToolResult.FailedWithCode(
                    "scene_object_selection_readback_failed",
                    $"Unity did not retain the requested editor selection: {canonicalPath}",
                    new
                    {
                        gameObjectPath = canonicalPath,
                        selectedBefore = before,
                        selectedAfter = after,
                        mutationStarted = true,
                        committed = false,
                        commitState = "unknown"
                    });
            }

            if (hierarchyReveal.FilterWasPresent && !hierarchyReveal.FilterCleared)
            {
                return VRCForgeToolResult.FailedWithCode(
                    "scene_object_hierarchy_reveal_failed",
                    "Unity selected the requested object, but an existing Hierarchy search filter could not be cleared; the object may remain hidden in the editor UI.",
                    new
                    {
                        gameObjectPath = canonicalPath,
                        selectedBefore = before,
                        selectedAfter = after,
                        hierarchyReveal = hierarchyReveal.ToResult(),
                        persistent = false,
                        sceneDirty = false,
                        mutationStarted = true,
                        committed = false,
                        commitState = "editor_state_partial"
                    });
            }

            return VRCForgeToolResult.Completed(
                hierarchyReveal.FilterCleared
                    ? $"Selected and revealed scene object: {canonicalPath}"
                    : $"Selected scene object: {canonicalPath}",
                new
                {
                    gameObjectPath = canonicalPath,
                    selectedBefore = before,
                    selectedAfter = after,
                    hierarchyReveal = hierarchyReveal.ToResult(),
                    persistent = false,
                    sceneDirty = false,
                    mutationStarted = true,
                    committed = true,
                    commitState = "editor_state_applied"
                });
        }

        private static HierarchyRevealResult ClearHierarchySearchFilters()
        {
            var result = new HierarchyRevealResult();
            try
            {
                var editorAssembly = typeof(EditorWindow).Assembly;
                var hierarchyWindowType = editorAssembly.GetType("UnityEditor.SceneHierarchyWindow", throwOnError: false);
                var searchableWindowType = editorAssembly.GetType("UnityEditor.SearchableEditorWindow", throwOnError: false);
                if (hierarchyWindowType == null || searchableWindowType == null)
                {
                    result.Error = "Unity Hierarchy search types are unavailable in this Editor version.";
                    return result;
                }

                var flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
                var searchFilterProperty = searchableWindowType.GetProperty("searchFilter", flags);
                var searchFilterField = searchableWindowType.GetField("m_SearchFilter", flags);
                var setSearchFilter = searchableWindowType
                    .GetMethods(flags)
                    .Where(method => method.Name == "SetSearchFilter")
                    .Where(method =>
                    {
                        var parameters = method.GetParameters();
                        return parameters.Length > 0 && parameters[0].ParameterType == typeof(string);
                    })
                    .OrderBy(method => method.GetParameters().Length)
                    .FirstOrDefault();

                if (setSearchFilter == null)
                {
                    result.Error = "Unity Hierarchy search setter is unavailable in this Editor version.";
                    return result;
                }

                foreach (var candidate in Resources.FindObjectsOfTypeAll(hierarchyWindowType))
                {
                    if (!(candidate is EditorWindow window))
                    {
                        continue;
                    }

                    result.WindowCount++;
                    var before = ReadSearchFilter(window, searchFilterProperty, searchFilterField);
                    if (string.IsNullOrEmpty(before))
                    {
                        continue;
                    }

                    result.FilterWasPresent = true;
                    result.FilterBefore = before;
                    var parameters = setSearchFilter.GetParameters();
                    var arguments = new object[parameters.Length];
                    arguments[0] = string.Empty;
                    for (var index = 1; index < parameters.Length; index++)
                    {
                        arguments[index] = DefaultArgument(parameters[index]);
                    }

                    setSearchFilter.Invoke(window, arguments);
                    window.Repaint();
                    var after = ReadSearchFilter(window, searchFilterProperty, searchFilterField);
                    result.FilterAfter = after;
                    result.FilterCleared = string.IsNullOrEmpty(after);
                    if (!result.FilterCleared)
                    {
                        result.Error = $"Hierarchy search filter remained active after clear request: {after}";
                        return result;
                    }
                }

                return result;
            }
            catch (Exception exception)
            {
                result.Error = exception.GetBaseException().Message;
                return result;
            }
        }

        private static string ReadSearchFilter(EditorWindow window, PropertyInfo property, FieldInfo field)
        {
            if (property != null && property.PropertyType == typeof(string))
            {
                return property.GetValue(window, null) as string ?? string.Empty;
            }

            if (field != null && field.FieldType == typeof(string))
            {
                return field.GetValue(window) as string ?? string.Empty;
            }

            return string.Empty;
        }

        private static object DefaultArgument(ParameterInfo parameter)
        {
            if (parameter.HasDefaultValue)
            {
                return parameter.DefaultValue;
            }

            if (parameter.ParameterType == typeof(bool))
            {
                return false;
            }

            if (parameter.ParameterType.IsEnum)
            {
                var names = Enum.GetNames(parameter.ParameterType);
                var all = names.FirstOrDefault(name => string.Equals(name, "All", StringComparison.OrdinalIgnoreCase));
                return Enum.Parse(parameter.ParameterType, all ?? names[0]);
            }

            return parameter.ParameterType.IsValueType
                ? Activator.CreateInstance(parameter.ParameterType)
                : null;
        }

        private sealed class HierarchyRevealResult
        {
            internal int WindowCount;
            internal bool FilterWasPresent;
            internal bool FilterCleared;
            internal string FilterBefore = string.Empty;
            internal string FilterAfter = string.Empty;
            internal string Error = string.Empty;

            internal object ToResult()
            {
                return new
                {
                    hierarchyWindowCount = WindowCount,
                    filterWasPresent = FilterWasPresent,
                    filterCleared = FilterCleared,
                    filterBefore = FilterBefore,
                    filterAfter = FilterAfter,
                    error = Error
                };
            }
        }

        private static object NoMutation(object details = null)
        {
            return new
            {
                details,
                mutationStarted = false,
                committed = false,
                commitState = "not_started"
            };
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_set_play_mode",
        Summary = "Request one explicit Unity Editor Play Mode state and report whether a transition was scheduled."
    )]
    public static class SetPlayModeTool
    {
        public class Parameters
        {
            [VRCForgeInput("True to enter Play Mode; false to exit Play Mode.")] public bool? isPlaying { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            var targetToken = @params?["isPlaying"];
            if (targetToken == null || targetToken.Type == JTokenType.Null)
            {
                return VRCForgeToolResult.RejectedBeforeMutation(
                    "play_mode_target_required",
                    "isPlaying is required.",
                    "unity_editor_state",
                    "argument_validation");
            }

            bool requested;
            try
            {
                requested = targetToken.Value<bool>();
            }
            catch (Exception)
            {
                return VRCForgeToolResult.RejectedBeforeMutation(
                    "play_mode_target_invalid",
                    "isPlaying must be a boolean.",
                    "unity_editor_state",
                    "argument_validation");
            }

            var before = EditorApplication.isPlaying;
            var isTransitioning = EditorApplication.isPlayingOrWillChangePlaymode != before;
            var entryBlockedByEditorWork = requested && (EditorApplication.isCompiling || EditorApplication.isUpdating);
            if (entryBlockedByEditorWork || isTransitioning)
            {
                return VRCForgeToolResult.RejectedBeforeMutation(
                    "editor_play_mode_busy",
                    requested
                        ? "Unity is compiling, updating, or already changing Play Mode; wait and read status before retrying."
                        : "Unity is already changing Play Mode; wait and read status before retrying.",
                    "unity_editor_state",
                    "play_mode_precondition",
                    true,
                    new
                    {
                        requested,
                        isPlaying = before,
                        isPlayingOrWillChangePlaymode = EditorApplication.isPlayingOrWillChangePlaymode,
                        isCompiling = EditorApplication.isCompiling,
                        isUpdating = EditorApplication.isUpdating
                    });
            }

            if (before == requested)
            {
                return VRCForgeToolResult.Completed(
                    requested ? "Unity is already in Play Mode." : "Unity is already outside Play Mode.",
                    new
                    {
                        requested,
                        before,
                        transitionScheduled = false,
                        verificationRequired = false,
                        persistent = false,
                        sceneDirty = false,
                        mutationStarted = false,
                        committed = true,
                        commitState = "no_change"
                    });
            }

            if (requested)
            {
                EditorApplication.EnterPlaymode();
            }
            else
            {
                EditorApplication.ExitPlaymode();
            }

            return VRCForgeToolResult.Completed(
                requested ? "Unity Play Mode entry was scheduled." : "Unity Play Mode exit was scheduled.",
                new
                {
                    requested,
                    before,
                    transitionScheduled = true,
                    verificationRequired = true,
                    persistent = false,
                    sceneDirty = false,
                    mutationStarted = true,
                    committed = false,
                    commitState = "transition_scheduled"
                });
        }

        private static object NoMutation(object details = null)
        {
            return new
            {
                details,
                mutationStarted = false,
                committed = false,
                commitState = "not_started"
            };
        }
    }

    [InitializeOnLoad]
    internal static class UnityAsyncJobRegistry
    {
        private const string Schema = "vrcforge.async-job.v1";
        private const string IndexKey = "VRCForge.AsyncJob.Index.v1";
        private const string RecordPrefix = "VRCForge.AsyncJob.Record.v1.";
        private const string ActivePrefix = "VRCForge.AsyncJob.Active.v1.";
        private static readonly TimeSpan TerminalRetention = TimeSpan.FromMinutes(15);
        private static readonly object Sync = new object();
        private static readonly Dictionary<string, JObject> McpWriteSnapshots = new Dictionary<string, JObject>(StringComparer.Ordinal);

        static UnityAsyncJobRegistry()
        {
            Sweep(DateTime.UtcNow);
            RecoverMcpWriteSnapshots();
        }

        internal static JObject CreateMcpWrite(string toolName, string executionId, string projectPath)
        {
            if (string.IsNullOrWhiteSpace(executionId)) throw new ArgumentException("executionId is required.", nameof(executionId));
            lock (Sync)
            {
                SweepLocked(DateTime.UtcNow);
                var existing = FindMcpWriteByExecutionIdLocked(executionId);
                if (existing != null) return PublicMcpWriteSnapshot(existing);
                var jobId = Guid.NewGuid().ToString("N").ToLowerInvariant();
                var now = DateTime.UtcNow;
                var record = new JObject
                {
                    ["schema"] = Schema,
                    ["job_id"] = jobId,
                    ["tool"] = toolName,
                    ["project_path"] = projectPath ?? string.Empty,
                    ["status"] = "queued",
                    ["before"] = new JObject(),
                    ["after"] = JValue.CreateNull(),
                    ["error"] = JValue.CreateNull(),
                    ["created_utc"] = now.ToString("O"),
                    ["started_utc"] = JValue.CreateNull(),
                    ["completed_utc"] = JValue.CreateNull(),
                    ["expires_utc"] = now.AddMinutes(15).ToString("O"),
                    ["purge_after_utc"] = JValue.CreateNull(),
                    ["operation"] = new JObject
                    {
                        ["kind"] = "mcp_write",
                        ["execution_id"] = executionId,
                    },
                };
                SaveRecordLocked(record);
                return PublicMcpWriteSnapshot(record);
            }
        }

        internal static JObject ReadMcpWriteSnapshot(string executionId)
        {
            lock (Sync)
            {
                var record = McpWriteSnapshots.TryGetValue(executionId ?? string.Empty, out var value) ? value : null;
                if (record != null && IsTerminal(record.Value<string>("status"))
                    && TryReadUtc(record.Value<string>("purge_after_utc"), out var purgeAfter)
                    && DateTime.UtcNow >= purgeAfter)
                {
                    McpWriteSnapshots.Remove(executionId ?? string.Empty);
                    record = null;
                }
                return record == null ? null : PublicMcpWriteSnapshot(record);
            }
        }

        internal static void MarkMcpWriteUnknown(string executionId, string reason)
        {
            lock (Sync)
            {
                if (!McpWriteSnapshots.TryGetValue(executionId ?? string.Empty, out var cached) || cached == null) return;
                cached["status"] = "unknown";
                cached["after"] = JValue.CreateNull();
                cached["error"] = new JObject
                {
                    ["code"] = "result_persistence_unknown",
                    ["message"] = reason ?? "The write result could not be persisted; execution state is unknown.",
                };
                cached["completed_utc"] = DateTime.UtcNow.ToString("O");
                cached["purge_after_utc"] = DateTime.UtcNow.Add(TerminalRetention).ToString("O");
            }
        }

        internal static JObject Create(
            string toolName,
            string projectPath,
            JObject before,
            JObject operation,
            TimeSpan timeout)
        {
            if (string.IsNullOrWhiteSpace(toolName))
            {
                throw new ArgumentException("toolName is required.", nameof(toolName));
            }
            if (before == null)
            {
                throw new ArgumentNullException(nameof(before));
            }
            if (timeout <= TimeSpan.Zero)
            {
                throw new ArgumentOutOfRangeException(nameof(timeout));
            }

            lock (Sync)
            {
                SweepLocked(DateTime.UtcNow);
                string jobId = null;
                for (var attempt = 0; attempt < 3; attempt += 1)
                {
                    var candidate = Guid.NewGuid().ToString("N").ToLowerInvariant();
                    if (LoadRecordLocked(candidate) == null)
                    {
                        jobId = candidate;
                        break;
                    }
                }
                if (string.IsNullOrEmpty(jobId))
                {
                    throw new InvalidOperationException("Unable to allocate a unique async job id.");
                }

                var now = DateTime.UtcNow;
                var record = new JObject
                {
                    ["schema"] = Schema,
                    ["job_id"] = jobId,
                    ["tool"] = toolName,
                    ["project_path"] = projectPath ?? string.Empty,
                    ["status"] = "queued",
                    ["before"] = before.DeepClone(),
                    ["after"] = JValue.CreateNull(),
                    ["error"] = JValue.CreateNull(),
                    ["created_utc"] = now.ToString("O"),
                    ["started_utc"] = JValue.CreateNull(),
                    ["completed_utc"] = JValue.CreateNull(),
                    ["expires_utc"] = now.Add(timeout).ToString("O"),
                    ["purge_after_utc"] = JValue.CreateNull(),
                    ["operation"] = operation == null ? new JObject() : operation.DeepClone(),
                };
                SaveRecordLocked(record);
                return PublicPayload(record);
            }
        }

        internal static JObject MarkRunning(string jobId)
        {
            lock (Sync)
            {
                var record = LoadRequiredMutableLocked(jobId);
                if (IsTerminal(record.Value<string>("status")))
                {
                    return PublicPayload(record);
                }
                if (string.Equals(record.Value<string>("status"), "queued", StringComparison.Ordinal))
                {
                    record["status"] = "running";
                    record["started_utc"] = DateTime.UtcNow.ToString("O");
                    SaveRecordLocked(record);
                }
                return PublicPayload(record);
            }
        }

        internal static JObject CompleteMcpWrite(string jobId, JObject originalToolResult)
        {
            if (originalToolResult == null) throw new ArgumentNullException(nameof(originalToolResult));
            return SetTerminal(jobId, "done", originalToolResult, null);
        }

        internal static JObject Complete(string jobId, Func<JObject> readAfter)
        {
            if (readAfter == null)
            {
                throw new ArgumentNullException(nameof(readAfter));
            }
            var after = readAfter();
            if (after == null)
            {
                throw new InvalidOperationException("Async job after readback returned null.");
            }
            return SetTerminal(jobId, "done", after, null);
        }

        internal static JObject Fail(
            string jobId,
            string code,
            string message,
            bool retryable,
            Func<JObject> readAfter = null)
        {
            JObject after = null;
            if (readAfter != null)
            {
                try
                {
                    after = readAfter();
                }
                catch
                {
                    after = null;
                }
            }
            var error = new JObject
            {
                ["code"] = string.IsNullOrWhiteSpace(code) ? "async_job_failed" : code,
                ["message"] = message ?? string.Empty,
                ["retryable"] = retryable,
            };
            return SetTerminal(jobId, "failed", after, error);
        }

        internal static JObject Poll(string jobId)
        {
            lock (Sync)
            {
                SweepLocked(DateTime.UtcNow);
                var record = LoadRecordLocked(NormalizeJobId(jobId));
                return record == null ? null : PublicPayload(record);
            }
        }

        internal static JObject ReadOperation(string jobId)
        {
            lock (Sync)
            {
                var record = LoadRecordLocked(NormalizeJobId(jobId));
                return record?["operation"] is JObject operation
                    ? (JObject)operation.DeepClone()
                    : null;
            }
        }

        internal static void SetActive(string toolName, string jobId)
        {
            SessionState.SetString(ActivePrefix + toolName, NormalizeJobId(jobId));
        }

        internal static string GetActive(string toolName)
        {
            return NormalizeJobId(SessionState.GetString(ActivePrefix + toolName, string.Empty));
        }

        internal static void ClearActive(string toolName, string jobId)
        {
            var key = ActivePrefix + toolName;
            if (string.Equals(SessionState.GetString(key, string.Empty), NormalizeJobId(jobId), StringComparison.Ordinal))
            {
                SessionState.EraseString(key);
            }
        }

        internal static void Sweep(DateTime utcNow)
        {
            lock (Sync)
            {
                SweepLocked(utcNow);
            }
        }

        internal static bool IsValidJobId(string value)
        {
            Guid parsed;
            return !string.IsNullOrWhiteSpace(value)
                && Guid.TryParseExact(value.Trim(), "N", out parsed);
        }

        private static JObject SetTerminal(string jobId, string status, JObject after, JObject error)
        {
            lock (Sync)
            {
                var record = LoadRequiredMutableLocked(jobId);
                if (IsTerminal(record.Value<string>("status")))
                {
                    return PublicPayload(record);
                }
                var now = DateTime.UtcNow;
                record["status"] = status;
                record["after"] = after == null ? JValue.CreateNull() : after.DeepClone();
                record["error"] = error == null ? JValue.CreateNull() : error.DeepClone();
                record["completed_utc"] = now.ToString("O");
                record["purge_after_utc"] = now.Add(TerminalRetention).ToString("O");
                SaveRecordLocked(record);
                ClearActive(record.Value<string>("tool"), record.Value<string>("job_id"));
                return PublicPayload(record);
            }
        }

        private static JObject LoadRequiredMutableLocked(string jobId)
        {
            var normalized = NormalizeJobId(jobId);
            var record = LoadRecordLocked(normalized);
            if (record == null)
            {
                throw new InvalidOperationException("Async job was not found: " + normalized);
            }
            return record;
        }

        private static void SweepLocked(DateTime utcNow)
        {
            var retained = new List<string>();
            foreach (var jobId in LoadIndexLocked())
            {
                var record = LoadRecordLocked(jobId);
                if (record == null)
                {
                    continue;
                }
                var status = record.Value<string>("status") ?? string.Empty;
                var isMcpWrite = string.Equals((string)record["operation"]?["kind"], "mcp_write", StringComparison.Ordinal);
                if (!isMcpWrite && !IsTerminal(status)
                    && TryReadUtc(record.Value<string>("expires_utc"), out var deadline)
                    && utcNow >= deadline)
                {
                    record["status"] = "expired";
                    record["after"] = JValue.CreateNull();
                    record["error"] = new JObject
                    {
                        ["code"] = "job_expired",
                        ["message"] = "Async job exceeded its bounded execution window.",
                        ["retryable"] = false,
                    };
                    record["completed_utc"] = utcNow.ToString("O");
                    record["purge_after_utc"] = utcNow.Add(TerminalRetention).ToString("O");
                    SaveRecordValue(record);
                    ClearActive(record.Value<string>("tool"), jobId);
                    status = "expired";
                }
                if (IsTerminal(status)
                    && TryReadUtc(record.Value<string>("purge_after_utc"), out var purgeAfter)
                    && utcNow >= purgeAfter)
                {
                    var executionId = (string)record["operation"]?["execution_id"];
                    if (!string.IsNullOrEmpty(executionId)) McpWriteSnapshots.Remove(executionId);
                    SessionState.EraseString(RecordPrefix + jobId);
                    continue;
                }
                retained.Add(jobId);
            }
            SaveIndexLocked(retained);
        }

        private static JObject LoadRecordLocked(string jobId)
        {
            if (!IsValidJobId(jobId))
            {
                return null;
            }
            var raw = SessionState.GetString(RecordPrefix + jobId, string.Empty);
            if (string.IsNullOrWhiteSpace(raw))
            {
                return null;
            }
            try
            {
                var record = JObject.Parse(raw);
                var status = record.Value<string>("status") ?? string.Empty;
                return string.Equals(record.Value<string>("schema"), Schema, StringComparison.Ordinal)
                    && string.Equals(record.Value<string>("job_id"), jobId, StringComparison.Ordinal)
                    && (status == "queued" || status == "running" || IsTerminal(status))
                    ? record
                    : null;
            }
            catch
            {
                return null;
            }
        }

        private static void SaveRecordLocked(JObject record)
        {
            SaveRecordValue(record);
            var jobId = record.Value<string>("job_id");
            var index = LoadIndexLocked();
            if (!index.Contains(jobId))
            {
                index.Add(jobId);
                SaveIndexLocked(index);
            }
        }

        private static void SaveRecordValue(JObject record)
        {
            SessionState.SetString(
                RecordPrefix + record.Value<string>("job_id"),
                record.ToString(Newtonsoft.Json.Formatting.None));
            var executionId = (string)record["operation"]?["execution_id"];
            if (string.Equals((string)record["operation"]?["kind"], "mcp_write", StringComparison.Ordinal)
                && !string.IsNullOrEmpty(executionId))
            {
                McpWriteSnapshots[executionId] = (JObject)record.DeepClone();
            }
        }

        private static void RecoverMcpWriteSnapshots()
        {
            lock (Sync)
            {
                foreach (var jobId in LoadIndexLocked())
                {
                    var record = LoadRecordLocked(jobId);
                    var operation = record?["operation"] as JObject;
                    if (operation == null || !string.Equals((string)operation["kind"], "mcp_write", StringComparison.Ordinal)) continue;
                    var executionId = (string)operation["execution_id"];
                    if (string.IsNullOrEmpty(executionId)) continue;
                    if (record.Value<string>("status") == "queued" || record.Value<string>("status") == "running")
                    {
                        record["status"] = "unknown";
                        record["error"] = new JObject { ["code"] = "editor_domain_reloaded", ["message"] = "The Editor domain reloaded before the write result was persisted; execution state is unknown." };
                        record["completed_utc"] = DateTime.UtcNow.ToString("O");
                        record["purge_after_utc"] = DateTime.UtcNow.Add(TerminalRetention).ToString("O");
                        SaveRecordValue(record);
                    }
                    else
                    {
                        McpWriteSnapshots[executionId] = (JObject)record.DeepClone();
                    }
                }
            }
        }

        private static JObject FindMcpWriteByExecutionIdLocked(string executionId)
        {
            foreach (var jobId in LoadIndexLocked())
            {
                var record = LoadRecordLocked(jobId);
                if (record != null
                    && string.Equals((string)record["operation"]?["kind"], "mcp_write", StringComparison.Ordinal)
                    && string.Equals((string)record["operation"]?["execution_id"], executionId, StringComparison.Ordinal)) return record;
            }
            return null;
        }

        private static JObject PublicMcpWriteSnapshot(JObject record)
        {
            var result = new JObject
            {
                ["jobId"] = record["job_id"]?.DeepClone() ?? JValue.CreateNull(),
                ["executionId"] = record["operation"]?["execution_id"]?.DeepClone() ?? JValue.CreateNull(),
                ["status"] = record["status"]?.DeepClone() ?? new JValue("unknown"),
                ["toolName"] = record["tool"]?.DeepClone() ?? JValue.CreateNull(),
            };
            if (string.Equals((string)record["status"], "done", StringComparison.Ordinal)) result["result"] = record["after"]?.DeepClone() ?? JValue.CreateNull();
            if (record["error"] != null && record["error"].Type != JTokenType.Null) result["error"] = record["error"].DeepClone();
            return result;
        }

        private static List<string> LoadIndexLocked()
        {
            var raw = SessionState.GetString(IndexKey, string.Empty);
            if (string.IsNullOrWhiteSpace(raw))
            {
                return new List<string>();
            }
            try
            {
                return JArray.Parse(raw)
                    .Values<string>()
                    .Select(NormalizeJobId)
                    .Where(IsValidJobId)
                    .Distinct(StringComparer.Ordinal)
                    .ToList();
            }
            catch
            {
                SessionState.EraseString(IndexKey);
                return new List<string>();
            }
        }

        private static void SaveIndexLocked(IEnumerable<string> jobIds)
        {
            var retained = jobIds.Where(IsValidJobId).Distinct(StringComparer.Ordinal).ToArray();
            if (retained.Length == 0)
            {
                SessionState.EraseString(IndexKey);
                return;
            }
            SessionState.SetString(IndexKey, JArray.FromObject(retained).ToString(Newtonsoft.Json.Formatting.None));
        }

        private static JObject PublicPayload(JObject record)
        {
            var payload = new JObject
            {
                ["job_id"] = record["job_id"]?.DeepClone() ?? JValue.CreateNull(),
                ["before"] = record["before"]?.DeepClone() ?? new JObject(),
                ["after"] = record["after"]?.DeepClone() ?? JValue.CreateNull(),
                ["status"] = record["status"]?.DeepClone() ?? new JValue("failed"),
            };
            if (record["error"] != null && record["error"].Type != JTokenType.Null)
            {
                payload["error"] = record["error"].DeepClone();
            }
            return payload;
        }

        private static bool IsTerminal(string status)
        {
            return status == "done" || status == "failed" || status == "expired" || status == "unknown";
        }

        private static string NormalizeJobId(string value)
        {
            return (value ?? string.Empty).Trim().ToLowerInvariant();
        }

        private static bool TryReadUtc(string value, out DateTime parsed)
        {
            return DateTime.TryParse(
                value,
                CultureInfo.InvariantCulture,
                DateTimeStyles.RoundtripKind,
                out parsed);
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_poll_job",
        Summary = "When to use: poll a job_id returned by an asynchronous VRCForge Unity tool. When NOT to use: do not start or retry writes, and do not treat a missing job after Editor restart as completion.",
        Access = VRCForgeCommandAccess.ReadOnly,
        Category = "diagnostics"
    )]
    public static class AsyncJobPollTool
    {
        public class Parameters
        {
            [VRCForgeInput("Exact async job id returned by the initiating tool.")]
            public string job_id { get; set; } = string.Empty;
        }

        public static object HandleCommand(JObject @params)
        {
            var jobId = (@params?["job_id"]?.ToString() ?? string.Empty).Trim().ToLowerInvariant();
            if (!UnityAsyncJobRegistry.IsValidJobId(jobId))
            {
                return VRCForgeToolResult.FailedWithCode(
                    "job_id_invalid",
                    "job_id must be exactly 32 hexadecimal characters.",
                    new { job_id = jobId });
            }

            var payload = UnityAsyncJobRegistry.Poll(jobId);
            if (payload == null)
            {
                payload = new JObject
                {
                    ["job_id"] = jobId,
                    ["before"] = JValue.CreateNull(),
                    ["after"] = JValue.CreateNull(),
                    ["status"] = "failed",
                    ["error"] = new JObject
                    {
                        ["code"] = "job_not_found",
                        ["message"] = "Async job was not found in this Unity Editor session.",
                        ["retryable"] = false,
                    },
                };
            }
            return VRCForgeToolResult.Completed("Read async Unity job state.", payload);
        }
    }
}
