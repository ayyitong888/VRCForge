using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Compilation;

namespace VRCForge.Editor
{
    /// <summary>
    /// Persists C# compile errors across domain reloads so agents can read the
    /// result of the last compilation pass. SessionState survives domain reload
    /// (which happens on successful compiles) but not editor restarts.
    /// </summary>
    [InitializeOnLoad]
    internal static class CompileErrorMonitor
    {
        private const string SessionKey = "VRCForge.CompileErrors";
        private const string SessionTimestampKey = "VRCForge.CompileErrors.Timestamp";

        static CompileErrorMonitor()
        {
            CompilationPipeline.compilationStarted += OnCompilationStarted;
            CompilationPipeline.assemblyCompilationFinished += OnAssemblyCompilationFinished;
        }

        internal static string CapturedAt => SessionState.GetString(SessionTimestampKey, string.Empty);

        private static void OnCompilationStarted(object context)
        {
            SessionState.SetString(SessionKey, "[]");
            SessionState.SetString(SessionTimestampKey, DateTime.UtcNow.ToString("o"));
        }

        private static void OnAssemblyCompilationFinished(string assemblyPath, CompilerMessage[] messages)
        {
            try
            {
                var entries = LoadEntries();
                foreach (var message in messages)
                {
                    if (message.type != CompilerMessageType.Error)
                    {
                        continue;
                    }

                    entries.Add(new JObject
                    {
                        ["assembly"] = assemblyPath ?? string.Empty,
                        ["file"] = message.file ?? string.Empty,
                        ["line"] = message.line,
                        ["column"] = message.column,
                        ["message"] = message.message ?? string.Empty
                    });
                }

                SessionState.SetString(SessionKey, new JArray(entries).ToString(Formatting.None));
                SessionState.SetString(SessionTimestampKey, DateTime.UtcNow.ToString("o"));
            }
            catch
            {
                // Never let monitoring break a compile pass.
            }
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

    [McpForUnityTool(
        name: "vrc_get_compile_errors",
        Description = "Read-only: report C# compile errors from the last compilation pass (CompilationPipeline capture with Unity Console fallback)."
    )]
    public static class CompileErrorReader
    {
        public const string ToolName = "vrc_get_compile_errors";
        private const int DefaultMaxErrors = 50;
        private const int MaxMaxErrors = 200;

        public class Parameters
        {
            [ToolParameter("Maximum number of errors to return. Clamped to 1-200.", Required = false)]
            public int? maxErrors { get; set; } = DefaultMaxErrors;

            [ToolParameter("If true (default), fall back to scanning the Unity Console for 'error CS' entries when no pipeline capture exists.", Required = false)]
            public bool? includeConsoleFallback { get; set; } = true;
        }

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var identity = PrimitiveBasisLiveGuard.RequireBoundRequest(@params);
                var parameters = (@params ?? new JObject()).ToObject<Parameters>() ?? new Parameters();
                var maxErrors = Math.Max(1, Math.Min(parameters.maxErrors ?? DefaultMaxErrors, MaxMaxErrors));
                var includeConsoleFallback = parameters.includeConsoleFallback ?? true;

                var payload = BuildPayload(maxErrors, includeConsoleFallback, identity);
                return new SuccessResponse("Compile errors checked.", payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Compile error check failed: {ex.Message}\n{ex.StackTrace}");
            }
        }

        private static object BuildPayload(
            int maxErrors,
            bool includeConsoleFallback,
            PrimitiveBasisLiveGuard.ProcessIdentity identity)
        {
            var pipelineErrors = CompileErrorMonitor.LoadEntries();
            var source = "compilation_pipeline";
            var errors = pipelineErrors;

            if (errors.Count == 0 && includeConsoleFallback)
            {
                var consoleErrors = ReadConsoleCompileErrors(maxErrors);
                if (consoleErrors.Count > 0)
                {
                    source = "console_log";
                    errors = consoleErrors;
                }
                else
                {
                    source = "none";
                }
            }
            else if (errors.Count == 0)
            {
                source = "none";
            }

            var truncated = errors.Count > maxErrors;
            if (truncated)
            {
                errors = errors.Take(maxErrors).ToList();
            }

            return new
            {
                ok = true,
                isCompiling = EditorApplication.isCompiling,
                hasErrors = errors.Count > 0,
                errorCount = errors.Count,
                truncated,
                source,
                capturedAt = CompileErrorMonitor.CapturedAt,
                errors = new JArray(errors),
                unityProcessId = identity?.ProcessId,
                unityProcessStartedAtUtc = identity?.StartedAtUtc,
                unityExecutableDigest = identity?.ExecutableDigest,
                projectPathDigest = identity?.ProjectPathDigest
            };
        }

        private static List<JObject> ReadConsoleCompileErrors(int maxEntries)
        {
            var results = new List<JObject>();
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
                try
                {
                    var entry = Activator.CreateInstance(logEntryType);
                    for (var i = 0; i < count && results.Count < maxEntries; i++)
                    {
                        getEntry.Invoke(null, new object[] { i, entry });
                        var message = messageField?.GetValue(entry)?.ToString() ?? string.Empty;
                        if (message.IndexOf("error CS", StringComparison.OrdinalIgnoreCase) < 0)
                        {
                            continue;
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
                            ["message"] = FirstLine(message)
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
