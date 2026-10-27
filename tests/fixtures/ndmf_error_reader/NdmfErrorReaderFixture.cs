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
            const string marker = "ndmf-error-reader-real-message";
            var result = new JObject();
            FieldInfo reportsField = null;
            object originalReports = null;
            try
            {
                var assembly = AppDomain.CurrentDomain.GetAssemblies()
                    .First(a => a.GetType("nadena.dev.ndmf.ErrorReport", false) != null);
                var reportType = assembly.GetType("nadena.dev.ndmf.ErrorReport");
                reportsField = reportType.GetField("Reports", BindingFlags.Static | BindingFlags.NonPublic);
                originalReports = reportsField.GetValue(null);
                var reports = (IList)Activator.CreateInstance(reportsField.FieldType);
                var constructor = reportType.GetConstructor(BindingFlags.Instance | BindingFlags.NonPublic,
                    null, new[] { typeof(string), typeof(string), typeof(Scene) }, null);
                var report = constructor.Invoke(new object[] {
                    "NDMF fixture avatar", "NDMF fixture avatar", SceneManager.GetActiveScene()
                });
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
                reports.Add(report);
                reportsField.SetValue(null, reports);

                var response = (VRCForgeToolResult)NdmfErrorReader.HandleCommand(new JObject());
                var payload = (JObject)response.Payload;
                result["payload"] = payload;
                var row = (JObject)payload["ndmfErrors"][0];
                Require((int)payload["ndmfErrorCount"] == 1, "Expected one real NDMF error");
                Require(((string)row["message"]).Contains(marker), "Error message contains only a type name or lost its exception");
                Require((string)row["severity"] == "InternalError", "Severity was not read");
                Require((string)row["avatar"] == "NDMF fixture avatar", "Avatar context was not read");
                Require((string)row["pass"] == "fixture-pass", "Pass context was not read");
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

        private static void Require(bool condition, string message)
        {
            if (!condition) throw new Exception(message);
        }
    }
}
