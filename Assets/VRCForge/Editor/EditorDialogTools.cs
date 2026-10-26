using System;
using System.Collections.Generic;
using System.Reflection;
using Newtonsoft.Json.Linq;

namespace VRCForge.Editor
{
    // Read-only diagnostic: report the NDMF build errors that Modular Avatar,
    // VRCFury and every other NDMF plugin surface through ErrorReportWindow.
    // NDMF exposes no read API, so the internal report list is read through
    // reflection; ObjectReference and SimpleError.ToString() return cached
    // strings, which keeps the read safe from the TCP service thread even
    // while a modal dialog blocks the editor update loop.
    [VRCForgeCommand(
        toolId: "vrc_get_ndmf_errors",
        Summary = "Read-only: report NDMF build errors (Modular Avatar, VRCFury, and every other NDMF plugin) with plugin, pass, severity and formatted message. Native dialog windows are read by the App-side inspect_unity_editor_dialogs tool instead.",
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
            return VRCForgeToolResult.Completed("NDMF errors enumerated.", payload);
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
                var reportsField = reportType.GetField("Reports", BindingFlags.Static | BindingFlags.NonPublic);
                var reports = reportsField?.GetValue(null) as System.Collections.IEnumerable;
                if (reports == null)
                {
                    note = "NDMF report list not found (NDMF version change); NDMF errors unavailable.";
                    return errors;
                }
                foreach (var report in reports)
                {
                    if (report == null) continue;
                    var reportTypeInstance = report.GetType();
                    var avatarName = reportTypeInstance.GetProperty("AvatarName")?.GetValue(report) as string;
                    var errorList = reportTypeInstance.GetProperty("Errors")?.GetValue(report) as System.Collections.IEnumerable;
                    if (errorList == null) continue;
                    foreach (var context in errorList)
                    {
                        if (context == null) continue;
                        var contextType = context.GetType();
                        var theError = contextType.GetField("TheError")?.GetValue(context);
                        if (theError == null) continue;
                        var entry = new JObject();
                        var errorType = theError.GetType();
                        try { entry["message"] = theError.ToString(); }
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
