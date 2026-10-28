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
            var errors = CollectNdmfErrors(out var note);
            var payload = new JObject { ["ndmfErrors"] = errors, ["ndmfErrorCount"] = errors.Count };
            if (!string.IsNullOrEmpty(note)) payload["note"] = note;
            return VRCForgeToolResult.Completed("NDMF's last retained report was read.", payload);
        }

        private static JArray CollectNdmfErrors(out string note)
        {
            note = string.Empty;
            var errors = new JArray();
            try
            {
                var ndmfAssembly = FindAssemblyContaining("nadena.dev.ndmf.ErrorReport");
                if (ndmfAssembly == null)
                {
                    note = "NDMF is not installed; no NDMF errors are collected.";
                    return errors;
                }
                var reportType = ndmfAssembly.GetType("nadena.dev.ndmf.ErrorReport");
                var errorInterface = ndmfAssembly.GetType("nadena.dev.ndmf.IError");
                var toMessage = errorInterface.GetMethod("ToMessage");
                var reportsField = reportType.GetField("Reports", BindingFlags.Static | BindingFlags.NonPublic);
                var reports = reportsField?.GetValue(null) as System.Collections.IEnumerable;
                if (reports == null)
                {
                    note = "NDMF report list not found (NDMF version change); NDMF errors unavailable.";
                    return errors;
                }
                object latestReport = null;
                foreach (var report in reports) latestReport = report;
                if (latestReport != null)
                {
                    var reportTypeInstance = latestReport.GetType();
                    var avatarName = reportTypeInstance.GetProperty("AvatarName")?.GetValue(latestReport) as string;
                    var errorList = reportTypeInstance.GetProperty("Errors")?.GetValue(latestReport) as System.Collections.IEnumerable;
                    if (errorList != null)
                    {
                        foreach (var context in errorList)
                        {
                            if (context == null) continue;
                            var contextType = context.GetType();
                            var theError = contextType.GetField("TheError")?.GetValue(context);
                            if (theError == null) continue;
                            var entry = new JObject();
                            var errorType = theError.GetType();
                            try { entry["message"] = (string)toMessage.Invoke(theError, null); }
                            catch (Exception ex) { entry["message"] = "<error formatting failed: " + ex.GetType().Name + ">"; }
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
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                note = "NDMF error collection failed: " + ex.GetType().Name + " " + ex.Message;
            }
            return errors;
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
