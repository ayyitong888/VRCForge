using System;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace VRCForge.Editor.Generic
{
    [VRCForgeCommand(
        toolId: "vrc_execute_unity_editor_menu",
        Summary = "when-to-use: after explicit approval, trigger one exact discovered Unity Editor menu item for explicitly identified selected objects; the response reports dispatch only and leaves outcome verification pending. when-NOT-to-use: do not infer that the menu action completed, do not invoke an undiscovered path, and do not use this to run arbitrary code or bypass the approved write lane. Negative example: do not report an opened setup window as a completed avatar setup.",
        Access = VRCForgeCommandAccess.RequiresApproval)]
    public static class UnityEditorMenuExecuteTool
    {
        private const int MaximumSelectionCount = 32;

        public sealed class Parameters
        {
            [VRCForgeInput("Exact menuItemPath previously returned by Unity Editor capability discovery.")]
            public string menuItemPath { get; set; }

            [VRCForgeInput("Zero to 32 exact Unity GlobalObjectIds to make the explicit Editor selection for this menu invocation; an empty array explicitly clears selection.")]
            public string[] selectionGlobalObjectIds { get; set; }
        }

        public static object HandleCommand(JObject parameters)
        {
            var args = (parameters ?? new JObject()).ToObject<Parameters>();
            var menuItemPath = (args?.menuItemPath ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(menuItemPath))
                return VRCForgeToolResult.RejectedBeforeMutation(
                    "unity_editor_menu_path_required", "An exact discovered menuItemPath is required.",
                    "unity_core", "input_validation");

            var ids = args?.selectionGlobalObjectIds;
            if (ids == null || ids.Length > MaximumSelectionCount)
                return VRCForgeToolResult.RejectedBeforeMutation(
                    "unity_editor_menu_selection_invalid",
                    "selectionGlobalObjectIds must be an explicit array containing zero to 32 exact GlobalObjectIds.",
                    "unity_core", "input_validation");

            var handlers = TypeCache.GetMethodsWithAttribute<MenuItem>()
                .Where(method => method != null && method.IsStatic)
                .SelectMany(method => method.GetCustomAttributes(typeof(MenuItem), false)
                    .OfType<MenuItem>()
                    .Select(attribute => new { Method = method, Attribute = attribute }))
                .Where(item => !item.Attribute.validate
                    && string.Equals(item.Attribute.menuItem, menuItemPath, StringComparison.Ordinal))
                .ToArray();
            if (handlers.Length == 0)
                return VRCForgeToolResult.RejectedBeforeMutation(
                    "unity_editor_menu_not_discovered",
                    "The exact menuItemPath is not a currently discovered executable Unity Editor menu item.",
                    "unity_core", "menu_resolution", false, new { menuItemPath });

            var selectedObjects = new UnityEngine.Object[ids.Length];
            var normalizedIds = new string[ids.Length];
            for (var index = 0; index < ids.Length; index++)
            {
                var rawId = (ids[index] ?? string.Empty).Trim();
                GlobalObjectId parsed;
                if (string.IsNullOrWhiteSpace(rawId) || !GlobalObjectId.TryParse(rawId, out parsed))
                    return VRCForgeToolResult.RejectedBeforeMutation(
                        "unity_editor_menu_selection_invalid",
                        "Every selectionGlobalObjectIds entry must be a valid exact GlobalObjectId.",
                        "unity_core", "target_resolution", false, new { menuItemPath, index });

                var target = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(parsed);
                if (target == null || !string.Equals(GlobalObjectId.GetGlobalObjectIdSlow(target).ToString(), parsed.ToString(), StringComparison.Ordinal))
                    return VRCForgeToolResult.RejectedBeforeMutation(
                        "unity_editor_menu_target_unavailable",
                        "A selected GlobalObjectId no longer resolves to that exact Unity object.",
                        "unity_core", "target_resolution", false, new { menuItemPath, index, globalObjectId = parsed.ToString() });

                selectedObjects[index] = target;
                normalizedIds[index] = parsed.ToString();
            }

            if (normalizedIds.Distinct(StringComparer.Ordinal).Count() != normalizedIds.Length)
                return VRCForgeToolResult.RejectedBeforeMutation(
                    "unity_editor_menu_selection_invalid",
                    "selectionGlobalObjectIds must not contain duplicate object identities.",
                    "unity_core", "target_resolution", false, new { menuItemPath });

            try
            {
                // Editor menu callbacks commonly consume the global Selection. Leave the
                // callback's resulting selection intact instead of restoring over it.
                Selection.objects = selectedObjects;
                var triggered = EditorApplication.ExecuteMenuItem(menuItemPath);
                var payload = new
                {
                    menuItemPath,
                    selectionGlobalObjectIds = normalizedIds,
                    triggered,
                    state = triggered ? "invoked" : "not_invoked",
                    resultVerification = "pending",
                    mutationMayHaveStarted = triggered,
                    checkpointRecoveryRequired = false,
                    selectionRestored = false
                };
                if (!triggered)
                    return VRCForgeToolResult.FailedWithCode(
                        "unity_editor_menu_not_invoked",
                        "Unity did not invoke the discovered menu item. The explicit selection remains active.",
                        payload);
                return VRCForgeToolResult.Completed(
                    "Unity Editor menu dispatch returned true; operation outcome remains unverified.", payload);
            }
            catch (Exception exception)
            {
                return VRCForgeToolResult.FailedWithException("unity_editor_menu_invocation_exception",
                    "The Unity Editor menu invocation raised an exception; partial side effects may remain and were not undone or cleaned up.",
                    exception,
                    new
                    {
                        menuItemPath,
                        selectionGlobalObjectIds = normalizedIds,
                        triggered = (bool?)null,
                        state = "invocation_exception",
                        dispatchState = "unknown",
                        resultVerification = "pending",
                        mutationMayHaveStarted = true,
                        checkpointRecoveryRequired = true,
                        exceptionType = exception.GetType().FullName,
                        error = exception.Message,
                        selectionRestored = false
                    });
            }
        }
    }
}
