using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRCForge.Core.MCP;
using VRCForge.Editor;

namespace VRCForge.LocalReview
{
    // Run only in an isolated Editor project with the real NDMF package installed.
    public static class NdmfErrorReaderFixture
    {
        public static void Run()
        {
            const string marker = "ndmf-error-reader-stale-report";
            var result = new JObject();
            FieldInfo reportsField = null;
            object originalReports = null;
            try
            {
                var assembly = AppDomain.CurrentDomain.GetAssemblies()
                    .First(a => a.GetType("nadena.dev.ndmf.ErrorReport", false) != null);
                var reportType = assembly.GetType("nadena.dev.ndmf.ErrorReport");
                reportsField = reportType.GetField("Reports", BindingFlags.Static | BindingFlags.NonPublic);
                Require(reportsField != null, "NDMF ErrorReport.Reports was not found");
                originalReports = reportsField.GetValue(null);
                var reports = (IList)Activator.CreateInstance(reportsField.FieldType);

                var oldReport = CreateReport(reportType, "Old report avatar");
                AddError(assembly, reportType, oldReport, marker);
                reports.Add(oldReport);
                reports.Add(CreateReport(reportType, "Latest empty avatar"));
                reportsField.SetValue(null, reports);

                var response = (VRCForgeToolResult)NdmfErrorReader.HandleCommand(new JObject());
                var payload = (JObject)response.Payload;
                result["staleErrorFollowedByLatestEmpty"] = payload;
                Require(response.IsSuccessful && (bool)payload["readable"] && (bool)payload["reportAvailable"]
                    && (int)payload["ndmfErrorCount"] == 0,
                    "The reader must successfully read the last empty report rather than an older error");
                Require(((JArray)payload["ndmfErrors"]).Count == 0,
                    "The last empty report should produce an empty error list");

                reports = (IList)Activator.CreateInstance(reportsField.FieldType);
                reports.Add(CreateReport(reportType, "Earlier empty avatar"));
                var latestErrorReport = CreateReport(reportType, "Latest error avatar");
                AddError(assembly, reportType, latestErrorReport, "ndmf-error-reader-latest-report");
                reports.Add(latestErrorReport);
                reportsField.SetValue(null, reports);

                response = (VRCForgeToolResult)NdmfErrorReader.HandleCommand(new JObject());
                payload = (JObject)response.Payload;
                result["latestError"] = payload;
                var latestError = (JObject)payload["ndmfErrors"][0];
                Require((int)payload["ndmfErrorCount"] == 1, "The last report's error was not returned");
                Require(((string)latestError["message"]).Contains("ndmf-error-reader-latest-report"),
                    "The last report's error message was not preserved");
                Require((string)latestError["avatar"] == "Latest error avatar",
                    "The last report's avatar context was not preserved");
                Require((string)latestError["pass"] == "fixture-pass",
                    "The last report's pass context was not preserved");

                reports = (IList)Activator.CreateInstance(reportsField.FieldType);
                reportsField.SetValue(null, reports);
                response = (VRCForgeToolResult)NdmfErrorReader.HandleCommand(new JObject());
                payload = (JObject)response.Payload;
                result["noReports"] = payload;
                Require(response.IsSuccessful && (bool)payload["readable"] && !(bool)payload["reportAvailable"]
                    && payload["ndmfErrorCount"].Type == JTokenType.Null
                    && ((JArray)payload["ndmfErrors"]).Count == 0,
                    "No retained report must not be represented as a clean report");
                result["pass"] = true;
            }
            catch (Exception exception)
            {
                result["pass"] = false;
                result["error"] = exception.ToString();
            }
            finally
            {
                if (reportsField != null && originalReports != null)
                    reportsField.SetValue(null, originalReports);
            }
            File.WriteAllText(Path.Combine(Application.dataPath, "../ndmf-error-reader-result.json"), result.ToString());
            if (!(bool)result["pass"]) throw new Exception((string)result["error"]);
        }

        private static object CreateReport(Type reportType, string avatarName)
        {
            var constructor = reportType.GetConstructor(BindingFlags.Instance | BindingFlags.NonPublic,
                null, new[] { typeof(string), typeof(string), typeof(Scene) }, null);
            Require(constructor != null,
                "NDMF 1.14.8 ErrorReport(string, string, Scene) constructor was not found");
            return constructor.Invoke(new object[] { avatarName, avatarName, SceneManager.GetActiveScene() });
        }

        private static void AddError(Assembly assembly, Type reportType, object report, string marker)
        {
            var contextField = reportType.GetField("CurrentContext", BindingFlags.Instance | BindingFlags.NonPublic);
            var context = contextField.GetValue(report);
            context.GetType().GetField("PassName").SetValue(context, "fixture-pass");
            contextField.SetValue(report, context);
            var errorType = assembly.GetType("nadena.dev.ndmf.StackTraceError");
            var error = Activator.CreateInstance(errorType, new object[] {
                new InvalidOperationException(marker), null
            });
            reportType.GetMethod("AddError", BindingFlags.Instance | BindingFlags.NonPublic)
                .Invoke(report, new[] { error });
        }

        private static void Require(bool condition, string message)
        {
            if (!condition) throw new Exception(message);
        }
    }
}
