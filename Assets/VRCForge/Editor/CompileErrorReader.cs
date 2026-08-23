using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using VRCForge.Core.MCP;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEngine;

namespace VRCForge.Editor
{
    /// <summary>
    /// Persists C# compiler diagnostics across domain reloads so agents can read the
    /// result of the last compilation pass. SessionState survives domain reload
    /// (which happens on successful compiles) but not editor restarts.
    /// </summary>
    [InitializeOnLoad]
    internal static class CompileErrorMonitor
    {
        private const string SessionKey = "VRCForge.CompileErrors";
        private const string SessionTimestampKey = "VRCForge.CompileErrors.Timestamp";
        private const string SessionCompletedKey = "VRCForge.CompileErrors.Completed";
        private static readonly object SnapshotGate = new object();
        private static JObject coreInfoSnapshot = new JObject();

        static CompileErrorMonitor()
        {
            CompilationPipeline.compilationStarted += OnCompilationStarted;
            CompilationPipeline.assemblyCompilationFinished += OnAssemblyCompilationFinished;
            CompilationPipeline.compilationFinished += OnCompilationFinished;
            RefreshCoreInfoSnapshot(EditorApplication.isCompiling);
        }

        internal static string CapturedAt => SessionState.GetString(SessionTimestampKey, string.Empty);
        internal static bool CaptureComplete => SessionState.GetBool(SessionCompletedKey, false);

        private static void OnCompilationStarted(object context)
        {
            SessionState.SetString(SessionKey, "[]");
            SessionState.SetString(SessionTimestampKey, DateTime.UtcNow.ToString("o"));
            SessionState.SetBool(SessionCompletedKey, false);
            RefreshCoreInfoSnapshot(true);
        }

        private static void OnAssemblyCompilationFinished(string assemblyPath, CompilerMessage[] messages)
        {
            try
            {
                var entries = LoadEntries();
                foreach (var message in messages)
                {
                    if (message.type != CompilerMessageType.Error && message.type != CompilerMessageType.Warning)
                    {
                        continue;
                    }

                    entries.Add(new JObject
                    {
                        ["assembly"] = assemblyPath ?? string.Empty,
                        ["file"] = message.file ?? string.Empty,
                        ["line"] = message.line,
                        ["column"] = message.column,
                        ["message"] = message.message ?? string.Empty,
                        ["severity"] = message.type == CompilerMessageType.Error ? "error" : "warning"
                    });
                }

                SessionState.SetString(SessionKey, new JArray(entries).ToString(Formatting.None));
                SessionState.SetString(SessionTimestampKey, DateTime.UtcNow.ToString("o"));
                RefreshCoreInfoSnapshot(true);
            }
            catch
            {
                // Never let monitoring break a compile pass.
            }
        }

        private static void OnCompilationFinished(object context)
        {
            SessionState.SetBool(SessionCompletedKey, true);
            SessionState.SetString(SessionTimestampKey, DateTime.UtcNow.ToString("o"));
            RefreshCoreInfoSnapshot(false);
        }

        private static void RefreshCoreInfoSnapshot(bool isCompiling)
        {
            var diagnostics = LoadEntries();
            var errors = diagnostics.Where(item =>
                !string.Equals(item.Value<string>("severity"), "warning", StringComparison.OrdinalIgnoreCase)
            ).ToList();
            var warnings = diagnostics.Where(item =>
                string.Equals(item.Value<string>("severity"), "warning", StringComparison.OrdinalIgnoreCase)
            ).ToList();
            var snapshot = new JObject
            {
                ["isCompiling"] = isCompiling,
                ["captureComplete"] = CaptureComplete,
                ["hasErrors"] = errors.Count > 0,
                ["hasWarnings"] = warnings.Count > 0,
                ["errorCount"] = errors.Count,
                ["warningCount"] = warnings.Count,
                ["truncated"] = false,
                ["source"] = "compilation_pipeline",
                ["capturedAt"] = CapturedAt,
                ["errors"] = new JArray(errors),
                ["warnings"] = new JArray(warnings),
            };
            lock (SnapshotGate)
            {
                coreInfoSnapshot = snapshot;
            }
        }

        internal static JObject ReadCoreInfoSnapshot(int maxEntries)
        {
            JObject snapshot;
            lock (SnapshotGate)
            {
                snapshot = (JObject)coreInfoSnapshot.DeepClone();
            }
            var limit = Math.Max(1, Math.Min(maxEntries, 200));
            var errors = snapshot["errors"] as JArray ?? new JArray();
            var warnings = snapshot["warnings"] as JArray ?? new JArray();
            var returnedErrors = new JArray(errors.Take(limit));
            var remaining = Math.Max(0, limit - returnedErrors.Count);
            var returnedWarnings = new JArray(warnings.Take(remaining));
            snapshot["errors"] = returnedErrors;
            snapshot["warnings"] = returnedWarnings;
            snapshot["truncated"] = errors.Count + warnings.Count > limit;
            return snapshot;
        }

        internal static List<JObject> LoadEntries()
        {
            try
            {
                var raw = SessionState.GetString(SessionKey, "[]");
                return JArray.Parse(raw).OfType<JObject>().ToList();
            }
            catch
            {
                return new List<JObject>();
            }
        }
    }

    /// <summary>
    /// Bounded, read-only Unity Console snapshot shared by tools that must
    /// report the exact editor evidence surrounding an operation. Unity does
    /// not expose Console entries through a public API, so this follows the
    /// same best-effort reflection boundary already used by CompileErrorReader.
    /// </summary>
    internal static class UnityConsoleSnapshotReader
    {
        private const int DefaultMaxEntries = 100;
        private const int MaximumEntries = 200;
        private const int MaximumMessageCharacters = 8000;

        internal static JObject Capture(int maxEntries = DefaultMaxEntries)
        {
            var limit = Math.Max(1, Math.Min(maxEntries, MaximumEntries));
            var snapshot = new JObject
            {
                ["schema"] = "vrcforge.unity_console_snapshot.v1",
                ["capturedAt"] = DateTime.UtcNow.ToString("o"),
                ["readable"] = false,
                ["totalEntryCount"] = 0,
                ["returnedEntryCount"] = 0,
                ["truncated"] = false,
                ["entries"] = new JArray(),
            };
            try
            {
                var editorAssembly = typeof(EditorApplication).Assembly;
                var logEntriesType = editorAssembly.GetType("UnityEditor.LogEntries");
                var logEntryType = editorAssembly.GetType("UnityEditor.LogEntry");
                if (logEntriesType == null || logEntryType == null)
                {
                    snapshot["unavailableReason"] = "unity_console_types_unavailable";
                    return snapshot;
                }

                var start = logEntriesType.GetMethod("StartGettingEntries", BindingFlags.Public | BindingFlags.Static);
                var end = logEntriesType.GetMethod("EndGettingEntries", BindingFlags.Public | BindingFlags.Static);
                var getEntry = logEntriesType.GetMethod("GetEntryInternal", BindingFlags.Public | BindingFlags.Static);
                if (start == null || end == null || getEntry == null)
                {
                    snapshot["unavailableReason"] = "unity_console_methods_unavailable";
                    return snapshot;
                }

                var messageField = logEntryType.GetField("message") ?? logEntryType.GetField("condition");
                var fileField = logEntryType.GetField("file");
                var lineField = logEntryType.GetField("line");
                var modeField = logEntryType.GetField("mode");
                var count = (int)start.Invoke(null, null);
                snapshot["readable"] = true;
                snapshot["totalEntryCount"] = count;
                snapshot["truncated"] = count > limit;
                var entries = new JArray();
                try
                {
                    var firstIndex = Math.Max(0, count - limit);
                    var entry = Activator.CreateInstance(logEntryType);
                    for (var index = firstIndex; index < count; index++)
                    {
                        getEntry.Invoke(null, new[] { (object)index, entry });
                        var rawMessage = messageField?.GetValue(entry)?.ToString() ?? string.Empty;
                        var message = rawMessage.Length <= MaximumMessageCharacters
                            ? rawMessage
                            : rawMessage.Substring(0, MaximumMessageCharacters);
                        var rawMode = modeField?.GetValue(entry);
                        var mode = ToInt(rawMode);
                        var modeText = rawMode?.ToString() ?? string.Empty;
                        entries.Add(new JObject
                        {
                            ["consoleIndex"] = index,
                            ["severity"] = Severity(mode, modeText),
                            ["mode"] = mode,
                            ["modeText"] = modeText,
                            ["file"] = fileField?.GetValue(entry)?.ToString() ?? string.Empty,
                            ["line"] = ToInt(lineField?.GetValue(entry)),
                            ["message"] = message,
                            ["messageTruncated"] = rawMessage.Length > MaximumMessageCharacters,
                        });
                    }
                }
                finally
                {
                    end.Invoke(null, null);
                }
                snapshot["entries"] = entries;
                snapshot["returnedEntryCount"] = entries.Count;
            }
            catch (Exception exception)
            {
                snapshot["readable"] = false;
                snapshot["unavailableReason"] = "unity_console_snapshot_failed";
                snapshot["exceptionType"] = exception.GetType().FullName ?? exception.GetType().Name;
                snapshot["exceptionMessage"] = exception.Message ?? string.Empty;
            }
            return snapshot;
        }

        private static int ToInt(object value)
        {
            try
            {
                return Convert.ToInt32(value ?? 0);
            }
            catch
            {
                return 0;
            }
        }

        private static string Severity(int mode, string modeText)
        {
            var normalized = modeText ?? string.Empty;
            if (normalized.IndexOf("Error", StringComparison.OrdinalIgnoreCase) >= 0
                || normalized.IndexOf("Exception", StringComparison.OrdinalIgnoreCase) >= 0
                || normalized.IndexOf("Assert", StringComparison.OrdinalIgnoreCase) >= 0
                || (mode & (1 | 2 | 16 | 64 | 256 | 2048)) != 0)
            {
                return "error";
            }
            if (normalized.IndexOf("Warning", StringComparison.OrdinalIgnoreCase) >= 0
                || (mode & (128 | 512 | 4096)) != 0)
            {
                return "warning";
            }
            return "log";
        }
    }

    [VRCForgeCommand(
        toolId: "vrc_get_compile_errors",
        Summary = "Read-only: report C# compiler errors and warnings from the last compilation pass (CompilationPipeline capture with Unity Console fallback).",
        Access = VRCForgeCommandAccess.ReadOnly
    )]
    public static class CompileErrorReader
    {
        public const string ToolName = "vrc_get_compile_errors";
        private const int DefaultMaxErrors = 50;
        private const int MaxMaxErrors = 200;
        private static PrimitiveBasisLiveGuard.ProcessIdentity currentProcessIdentity;

        public class Parameters
        {
            [VRCForgeInput("Maximum number of compiler diagnostics to return. Clamped to 1-200.", IsRequired = false)]
            public int? maxErrors { get; set; } = DefaultMaxErrors;

            [VRCForgeInput("If true (default), fall back to scanning the Unity Console for 'error CS' and 'warning CS' entries when no pipeline capture exists.", IsRequired = false)]
            public bool? includeConsoleFallback { get; set; } = true;
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var identity = PrimitiveBasisLiveGuard.RequireBoundRequest(@params)
                    ?? InspectCurrentProcessIdentity();
                var parameters = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters();
                var maxErrors = Math.Max(1, Math.Min(parameters.maxErrors ?? DefaultMaxErrors, MaxMaxErrors));
                var includeConsoleFallback = parameters.includeConsoleFallback ?? true;

                var payload = BuildPayload(maxErrors, includeConsoleFallback, identity);
                return VRCForgeToolResult.Completed("Compile errors checked.", payload);
            }
            catch (Exception ex)
            {
                return VRCForgeToolResult.Failed($"Compile error check failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static object BuildPayload(
            int maxErrors,
            bool includeConsoleFallback,
            PrimitiveBasisLiveGuard.ProcessIdentity identity)
        {
            var pipelineDiagnostics = CompileErrorMonitor.LoadEntries();
            var captureComplete = CompileErrorMonitor.CaptureComplete;
            var source = "compilation_pipeline";
            var diagnostics = pipelineDiagnostics;
            var diagnosticsTruncated = false;

            if (!captureComplete && includeConsoleFallback)
            {
                bool consoleReadable;
                bool consoleTruncated;
                var consoleDiagnostics = ReadConsoleCompileDiagnostics(
                    maxErrors,
                    out consoleReadable,
                    out consoleTruncated);
                if (consoleReadable)
                {
                    source = "console_log";
                    diagnostics = consoleDiagnostics;
                    diagnosticsTruncated = consoleTruncated;
                    captureComplete = !EditorApplication.isCompiling;
                }
                else
                {
                    source = "unavailable";
                }
            }
            else if (!captureComplete)
            {
                source = "unavailable";
            }

            var truncated = diagnosticsTruncated || diagnostics.Count > maxErrors;
            if (truncated)
            {
                diagnostics = diagnostics.Take(maxErrors).ToList();
            }
            var errors = diagnostics.Where(item =>
                !string.Equals(item.Value<string>("severity"), "warning", StringComparison.OrdinalIgnoreCase)
            ).ToList();
            var warnings = diagnostics.Where(item =>
                string.Equals(item.Value<string>("severity"), "warning", StringComparison.OrdinalIgnoreCase)
            ).ToList();

            return new
            {
                ok = true,
                isCompiling = EditorApplication.isCompiling,
                captureComplete,
                hasErrors = errors.Count > 0,
                hasWarnings = warnings.Count > 0,
                errorCount = errors.Count,
                warningCount = warnings.Count,
                truncated,
                source,
                capturedAt = CompileErrorMonitor.CapturedAt,
                errors = new JArray(errors),
                warnings = new JArray(warnings),
                unityProcessId = identity?.ProcessId,
                unityProcessStartedAtUtc = identity?.StartedAtUtc,
                unityExecutableDigest = identity?.ExecutableDigest,
                projectPathDigest = identity?.ProjectPathDigest
            };
        }

        private static PrimitiveBasisLiveGuard.ProcessIdentity InspectCurrentProcessIdentity()
        {
            if (currentProcessIdentity != null)
            {
                return currentProcessIdentity;
            }
            using (var process = System.Diagnostics.Process.GetCurrentProcess())
            {
                var executablePath = process.MainModule?.FileName;
                if (string.IsNullOrWhiteSpace(executablePath) || !File.Exists(executablePath))
                {
                    throw new InvalidOperationException("The Unity process executable is unavailable.");
                }
                var projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
                currentProcessIdentity = new PrimitiveBasisLiveGuard.ProcessIdentity
                {
                    ProcessId = process.Id,
                    StartedAtUtc = process.StartTime.ToUniversalTime().ToString("O"),
                    ExecutableDigest = PrimitiveBasisLiveGuard.Sha256File(executablePath),
                    ProjectPathDigest = PrimitiveBasisLiveGuard.Sha256Text(
                        PrimitiveBasisLiveGuard.NormalizeProjectRoot(projectRoot))
                };
                return currentProcessIdentity;
            }
        }

        private static List<JObject> ReadConsoleCompileDiagnostics(
            int maxEntries,
            out bool readable,
            out bool truncated)
        {
            var results = new List<JObject>();
            readable = false;
            truncated = false;
            try
            {
                var editorAssembly = typeof(EditorApplication).Assembly;
                var logEntriesType = editorAssembly.GetType("UnityEditor.LogEntries");
                var logEntryType = editorAssembly.GetType("UnityEditor.LogEntry");
                if (logEntriesType == null || logEntryType == null)
                {
                    return results;
                }

                var start = logEntriesType.GetMethod("StartGettingEntries", BindingFlags.Public | BindingFlags.Static);
                var end = logEntriesType.GetMethod("EndGettingEntries", BindingFlags.Public | BindingFlags.Static);
                var getEntry = logEntriesType.GetMethod("GetEntryInternal", BindingFlags.Public | BindingFlags.Static);
                if (start == null || end == null || getEntry == null)
                {
                    return results;
                }
                var messageField = logEntryType.GetField("message") ?? logEntryType.GetField("condition");
                var fileField = logEntryType.GetField("file");
                var lineField = logEntryType.GetField("line");

                var count = (int)start.Invoke(null, null);
                readable = true;
                try
                {
                    var entry = Activator.CreateInstance(logEntryType);
                    for (var i = 0; i < count; i++)
                    {
                        getEntry.Invoke(null, new object[] { i, entry });
                        var message = messageField?.GetValue(entry)?.ToString() ?? string.Empty;
                        var isError = message.IndexOf("error CS", StringComparison.OrdinalIgnoreCase) >= 0;
                        var isWarning = message.IndexOf("warning CS", StringComparison.OrdinalIgnoreCase) >= 0;
                        if (!isError && !isWarning)
                        {
                            continue;
                        }
                        if (results.Count >= maxEntries)
                        {
                            truncated = true;
                            break;
                        }

                        var line = 0;
                        try
                        {
                            line = Convert.ToInt32(lineField?.GetValue(entry) ?? 0);
                        }
                        catch
                        {
                            // Line info is best-effort only.
                        }

                        results.Add(new JObject
                        {
                            ["assembly"] = string.Empty,
                            ["file"] = fileField?.GetValue(entry)?.ToString() ?? string.Empty,
                            ["line"] = line,
                            ["column"] = 0,
                            ["message"] = FirstLine(message),
                            ["severity"] = isError ? "error" : "warning"
                        });
                    }
                }
                finally
                {
                    end.Invoke(null, null);
                }
            }
            catch
            {
                // Console reflection is a best-effort fallback only.
            }

            return results;
        }

        private static string FirstLine(string text)
        {
            var index = text.IndexOf('\n');
            return index >= 0 ? text.Substring(0, index).TrimEnd('\r') : text;
        }
    }
}
