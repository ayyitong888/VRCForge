using System;
using Newtonsoft.Json.Linq;
using VRCForge.Core.MCP;

namespace VRCForge.Editor
{
    [VRCForgeCommand(toolId: "vrc_apply_diagnostic_fix",
        Summary = "when-to-use: preview or invoke one current NDMF/SDK native Auto Fix action selected from its diagnostic report, through approval and a full-project checkpoint. when-NOT-to-use: do not guess actions, automatically fix every warning, upload, or claim callback return means the issue disappeared. Negative example: NDMF's retained build report is not a fresh build result.",
        Access = VRCForgeCommandAccess.RequiresApproval)]
    public static class NativeDiagnosticFixTool
    {
        public sealed class Parameters
        {
            [VRCForgeInput("Exact active project root.", IsRequired = true)] public string projectPath { get; set; }
            [VRCForgeInput("Diagnostic source: ndmf or sdk.", IsRequired = true)] public string source { get; set; }
            [VRCForgeInput("Exact current report row index.", IsRequired = true)] public int index { get; set; }
            [VRCForgeInput("Exact original diagnostic message.", IsRequired = true)] public string message { get; set; }
            [VRCForgeInput("Exact SDK selected avatar path; required for sdk.")] public string avatarPath { get; set; }
            [VRCForgeInput("Resolve the selected action without invoking it.")] public bool preview { get; set; }
        }

        public static object HandleCommand(JObject parameters)
        {
            parameters = parameters ?? new JObject();
            var source = (string)parameters["source"];
            Action action;
            try
            {
                CheckpointPrepareTool.ValidateProject(parameters);
                if (source == "ndmf") action = NativeNdmfFixActions.Resolve(parameters);
#if !VRC_CLIENT
                else if (source == "sdk") action = NativeSdkFixActions.Resolve(parameters);
#endif
                else throw new ArgumentException("source must be ndmf or sdk.");
            }
            catch (Exception exception)
            {
                return VRCForgeToolResult.RejectedBeforeMutation("native_fix_unavailable", exception.Message,
                    "unity_core", "native_action_resolution");
            }
            var payload = new JObject
            {
                ["source"] = source, ["index"] = parameters["index"], ["message"] = parameters["message"],
                ["available"] = true, ["preview"] = (bool?)parameters["preview"] == true,
                ["impact"] = "native_callback_scope_unknown", ["triggered"] = false,
                ["mutationStarted"] = false, ["needsRecheck"] = true,
                ["reportBoundary"] = source == "ndmf" ? "retained_build_report_not_refreshed" : "sdk_panel_validation_cache",
                ["nextAction"] = source == "ndmf"
                    ? "After a separately authorized intended build, read the new NDMF report. This action does not build, enter Play or upload."
                    : "Inspect the SDK validation result below; callback return alone does not prove the issue is resolved."
            };
            if ((bool)payload["preview"])
                return VRCForgeToolResult.Completed("The selected native callback is available; preview did not invoke it.", payload);
            try
            {
                payload["mutationStarted"] = true;
                payload["mutationMayHaveStarted"] = true;
                payload["triggered"] = true;
                action();
                payload["state"] = "invoked";
                payload["resultVerification"] = "pending";
                payload["checkpointRecoveryRequired"] = false;
#if !VRC_CLIENT
                if (source == "sdk")
                {
                    var report = (VRCForgeToolResult)VrchatSdkBuilderAlertsTool.HandleCommand(parameters);
                    payload["validationReadback"] = JToken.FromObject(report.Payload);
                    payload["validationReadSucceeded"] = report.IsSuccessful;
                }
#endif
                return VRCForgeToolResult.Completed("The native Auto Fix callback returned. Review the diagnostic result before claiming resolution.", payload);
            }
            catch (Exception exception)
            {
                payload["state"] = "invocation_exception";
                payload["resultVerification"] = "pending";
                payload["checkpointRecoveryRequired"] = true;
                return VRCForgeToolResult.FailedWithException("native_fix_invocation_exception",
                    "The native callback or its SDK validation refresh raised an exception. Partial effects may remain; the current state and checkpoint were preserved for explicit recovery.", exception, payload);
            }
        }
    }
}
