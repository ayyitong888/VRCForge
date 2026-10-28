using System;
using System.Collections.Generic;
using System.Reflection;
using VRCForge.Core.MCP;
using Newtonsoft.Json.Linq;

namespace VRCForge.Editor
{
    // Read-only diagnostic: report the errors in NDMF's last retained build report.
    // NDMF exposes no report-list read API, so the list is read through
    // reflection. Core dispatches this read on the Unity main thread, where
    // IError.ToMessage() may safely initialize its localization assets.
    [VRCForgeCommand(
        toolId: "vrc_get_ndmf_errors",
        Summary = "Read-only: report errors from NDMF's last retained report, matching the report shown by Show NDMF Console, with plugin, pass, severity and formatted message. Native dialog windows are read by the App-side inspect_unity_editor_dialogs tool instead.",
        Access = VRCForgeCommandAccess.ReadOnly
    )]
    public static class NdmfErrorReader
    {
        public const string ToolName = "vrc_get_ndmf_errors";

        public static object HandleCommand(JObject @params)
        {
            var errors = new JArray();
            var payload = new JObject
            {
                ["readable"] = false,
                ["reportAvailable"] = false,
                ["ndmfErrors"] = errors,
                ["ndmfErrorCount"] = JValue.CreateNull(),
            };
            var stage = "report_discovery";
            try
            {
                var ndmfAssembly = FindAssemblyContaining("nadena.dev.ndmf.ErrorReport");
                if (ndmfAssembly == null)
                {
                    payload["readStatus"] = "not_installed";
                    payload["note"] = "NDMF is not installed; no NDMF report was read.";
                    payload["nextAction"] = "If this project uses NDMF, install or restore its package and wait for Unity compilation, then read the report again. Otherwise this diagnostic does not apply.";
                    return VRCForgeToolResult.Completed("NDMF is not installed; its error state is unavailable.", payload);
                }
                var reportType = ndmfAssembly.GetType("nadena.dev.ndmf.ErrorReport");
                var reportsField = reportType.GetField("Reports", BindingFlags.Static | BindingFlags.NonPublic);
                if (reportsField == null) throw new MissingMemberException(reportType.FullName, "Reports");
                var reports = reportsField.GetValue(null) as System.Collections.IEnumerable;
                if (reports == null) throw new InvalidOperationException("NDMF ErrorReport.Reports is not an enumerable report list.");
                object latestReport = null;
                foreach (var report in reports) latestReport = report;
                if (latestReport == null)
                {
                    payload["readable"] = true;
                    payload["readStatus"] = "no_report";
                    payload["nextAction"] = "NDMF has no retained build report. Read this tool after the intended NDMF build has completed; no report does not prove that the avatar has no errors.";
                    return VRCForgeToolResult.Completed("NDMF has no retained report; no build result was evaluated.", payload);
                }
                payload["reportAvailable"] = true;
                stage = "report_read";
                var reportTypeInstance = latestReport.GetType();
                var avatarName = reportTypeInstance.GetProperty("AvatarName")?.GetValue(latestReport) as string;
                var errorsProperty = reportTypeInstance.GetProperty("Errors");
                if (errorsProperty == null) throw new MissingMemberException(reportTypeInstance.FullName, "Errors");
                var errorList = errorsProperty.GetValue(latestReport) as System.Collections.IEnumerable;
                if (errorList == null) throw new InvalidOperationException("NDMF ErrorReport.Errors is not an enumerable error list.");
                foreach (var context in errorList)
                {
                    if (context == null) throw new InvalidOperationException("NDMF returned a null error context.");
                    var contextType = context.GetType();
                    var errorField = contextType.GetField("TheError");
                    if (errorField == null) throw new MissingMemberException(contextType.FullName, "TheError");
                    var theError = errorField.GetValue(context);
                    if (theError == null) throw new InvalidOperationException("NDMF returned an error context without TheError.");
                    var errorInterface = ndmfAssembly.GetType("nadena.dev.ndmf.IError");
                    var toMessage = errorInterface?.GetMethod("ToMessage");
                    if (toMessage == null) throw new MissingMemberException("nadena.dev.ndmf.IError", "ToMessage");
                    stage = "error_format";
                    var entry = new JObject { ["message"] = (string)toMessage.Invoke(theError, null) };
                    var errorType = theError.GetType();
                    var severity = errorType.GetProperty("Severity")?.GetValue(theError);
                    if (severity != null) entry["severity"] = severity.ToString();
                    var titleKey = errorType.GetProperty("TitleKey")?.GetValue(theError) as string;
                    if (!string.IsNullOrEmpty(titleKey)) entry["titleKey"] = titleKey;
                    var plugin = contextType.GetField("Plugin")?.GetValue(context);
                    if (plugin != null) entry["plugin"] = plugin.GetType().Name;
                    var passName = contextType.GetField("PassName")?.GetValue(context) as string;
                    if (!string.IsNullOrEmpty(passName)) entry["pass"] = passName;
                    if (!string.IsNullOrEmpty(avatarName)) entry["avatar"] = avatarName;
                    errors.Add(entry);
                    stage = "report_read";
                }
                payload["readable"] = true;
                payload["readStatus"] = "read";
                payload["ndmfErrorCount"] = errors.Count;
                return VRCForgeToolResult.Completed("NDMF's last retained report was read.", payload);
            }
            catch (Exception ex)
            {
                var cause = ex;
                while (cause is TargetInvocationException && cause.InnerException != null) cause = cause.InnerException;
                payload["readStatus"] = "unavailable";
                payload["failureLayer"] = "unity_editor_tool";
                payload["failurePhase"] = stage;
                payload["nextAction"] = "Open Show NDMF Console to inspect the retained report and its plugin/pass error. Check the reported NDMF API or formatting exception and package compatibility before retrying; an unavailable read is not a clean build.";
                payload["diagnostics"] = new JObject
                {
                    ["exceptionType"] = cause.GetType().FullName,
                    ["message"] = cause.Message,
                };
                return VRCForgeToolResult.FailedWithCode(
                    cause is MissingMemberException ? "ndmf_report_api_unavailable" : "ndmf_report_read_failed",
                    "NDMF report read failed at " + stage + ": " + cause.GetType().Name + ": " + cause.Message,
                    payload);
            }
        }

        private static Assembly FindAssemblyContaining(string typeName)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    if (assembly.GetType(typeName, false) != null) return assembly;
                }
                catch { /* unloadable assemblies are skipped */ }
            }
            return null;
        }
    }
}
