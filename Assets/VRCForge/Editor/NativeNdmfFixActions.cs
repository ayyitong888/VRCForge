using System;
using System.Collections;
using System.Reflection;
using Newtonsoft.Json.Linq;

namespace VRCForge.Editor
{
    // Narrow adapter for NDMF's editor-internal native Auto Fix callback.
    // The action is opaque and runs on Unity's main thread through the normal
    // supervised write dispatcher; this adapter does not inspect its footprint.
    internal static class NativeNdmfFixActions
    {
        private const string AutofixTypeName = "nadena.dev.ndmf.InlineErrorWithAutofix";

        internal static object GetLatestReport(Assembly ndmfAssembly)
        {
            if (ndmfAssembly == null) return null;
            var reportType = ndmfAssembly.GetType("nadena.dev.ndmf.ErrorReport");
            if (reportType == null) return null;
            var reportsField = reportType.GetField("Reports", BindingFlags.Static | BindingFlags.NonPublic);
            if (reportsField == null) throw new MissingMemberException(reportType.FullName, "Reports");
            var reports = reportsField.GetValue(null) as IEnumerable;
            if (reports == null) throw new InvalidOperationException("NDMF ErrorReport.Reports is not an enumerable report list.");
            object latestReport = null;
            foreach (var report in reports) latestReport = report;
            return latestReport;
        }

        internal static JObject Describe(object latestReport, object error, int index)
        {
            var action = GetAction(error);
            if (latestReport == null || action == null) return null;

            var errorType = error.GetType();
            var message = errorType.GetMethod("ToMessage", Type.EmptyTypes)?.Invoke(error, null) as string;
            var descriptor = new JObject
            {
                ["source"] = "ndmf",
                ["index"] = index,
                ["message"] = message ?? string.Empty,
                ["impact"] = "native_callback_scope_unknown",
            };
            var titleKey = errorType.GetProperty("TitleKey")?.GetValue(error) as string;
            if (!string.IsNullOrEmpty(titleKey)) descriptor["titleKey"] = titleKey;
            var label = errorType.GetMethod("FormatTitle", Type.EmptyTypes)?.Invoke(error, null) as string;
            if (!string.IsNullOrEmpty(label)) descriptor["label"] = label;
            return descriptor;
        }

        internal static Action Resolve(JObject selector)
        {
            if (selector == null) throw new ArgumentNullException(nameof(selector));
            var indexToken = selector["index"];
            var message = (string)selector["message"];
            if (indexToken == null || !indexToken.Type.Equals(JTokenType.Integer) || message == null)
                throw new ArgumentException("NDMF Auto Fix selection requires its current report row index and exact message.", nameof(selector));
            var index = (int)indexToken;
            if (index < 0) throw new ArgumentOutOfRangeException(nameof(selector), "NDMF report row index must be non-negative.");

            var ndmfAssembly = FindAssemblyContaining("nadena.dev.ndmf.ErrorReport");
            var latestReport = GetLatestReport(ndmfAssembly);
            if (latestReport == null) throw new InvalidOperationException("NDMF has no currently retained report; reread NDMF diagnostics before selecting an Auto Fix.");
            var errorsProperty = latestReport.GetType().GetProperty("Errors");
            if (errorsProperty == null) throw new MissingMemberException(latestReport.GetType().FullName, "Errors");
            var errors = errorsProperty.GetValue(latestReport) as IEnumerable;
            if (errors == null) throw new InvalidOperationException("NDMF ErrorReport.Errors is not enumerable.");

            var currentIndex = 0;
            foreach (var context in errors)
            {
                if (currentIndex++ != index) continue;
                var error = context?.GetType().GetField("TheError")?.GetValue(context);
                if (error == null) throw new InvalidOperationException("The selected NDMF report row no longer contains an error.");
                var actualMessage = error.GetType().GetMethod("ToMessage", Type.EmptyTypes)?.Invoke(error, null) as string;
                if (!string.Equals(message, actualMessage, StringComparison.Ordinal))
                    throw new InvalidOperationException("The selected NDMF report row changed; reread NDMF diagnostics and select the current action.");
                var action = GetAction(error);
                if (action == null) throw new InvalidOperationException("The selected NDMF report row has no native Auto Fix callback.");
                return action;
            }
            throw new InvalidOperationException("The selected NDMF report row is no longer present; reread NDMF diagnostics.");
        }

        private static Action GetAction(object error)
        {
            if (error == null || error.GetType().FullName != AutofixTypeName) return null;
            var field = error.GetType().GetField("_autofix", BindingFlags.Instance | BindingFlags.NonPublic);
            return field?.GetValue(error) as Action;
        }

        private static Assembly FindAssemblyContaining(string typeName)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    if (assembly.GetType(typeName, false) != null) return assembly;
                }
                catch { /* skip unloadable assemblies */ }
            }
            return null;
        }
    }
}
