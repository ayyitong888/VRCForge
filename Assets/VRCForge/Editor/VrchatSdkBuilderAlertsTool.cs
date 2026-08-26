#if !VRC_CLIENT
using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3A.Editor;
using VRCForge.Core.MCP;
using Object = UnityEngine.Object;

namespace VRCForge.Editor
{
    [VRCForgeCommand(
        toolId: "vrc_read_vrchat_sdk_builder_alerts",
        Summary = "when-to-use: read the already-generated VRChat SDK 3.10.4 Builder Review Any Alerts cache for the exact currently selected avatar without refreshing or acting on it. when-NOT-to-use: do not use it to open the SDK panel, run validations, select an avatar, invoke Select or Auto Fix actions, build, upload, or claim that an unavailable cache is an empty alert list. Negative example: do not call this tool to fix a blocking SDK alert.",
        Category = "diagnostics",
        Access = VRCForgeCommandAccess.ReadOnly
    )]
    public static class VrchatSdkBuilderAlertsTool
    {
        public sealed class Parameters
        {
            [VRCForgeInput("Exact active Unity project root.", IsRequired = true)] public string projectPath { get; set; } = string.Empty;
            [VRCForgeInput("Exact loaded-scene hierarchy path of the avatar root.", IsRequired = true)] public string avatarPath { get; set; } = string.Empty;
        }

        private const string Schema = "vrcforge.vrchat_sdk_builder_alerts.v1";
        private const string SupportedSdkVersion = "3.10.4";

        private sealed class CollectionSpec
        {
            internal readonly string FieldName;
            internal readonly string Category;
            internal readonly string Severity;
            internal readonly string IconKind;
            internal readonly bool Blocker;
            internal readonly bool SortByPerformanceRating;

            internal CollectionSpec(
                string fieldName,
                string category,
                string severity,
                string iconKind,
                bool blocker,
                bool sortByPerformanceRating = false)
            {
                FieldName = fieldName;
                Category = category;
                Severity = severity;
                IconKind = iconKind;
                Blocker = blocker;
                SortByPerformanceRating = sortByPerformanceRating;
            }
        }

        private sealed class AlertLayout
        {
            internal Type IssueType;
            internal FieldInfo IssueText;
            internal FieldInfo ShowAction;
            internal FieldInfo FixAction;
            internal FieldInfo PerformanceRating;
            internal readonly Dictionary<string, IDictionary> Collections =
                new Dictionary<string, IDictionary>(StringComparer.Ordinal);
        }

        private static readonly CollectionSpec[] CollectionOrder =
        {
            new CollectionSpec("GUIErrors", "error", "blocking", "error", true),
            new CollectionSpec("GUIWarnings", "warning", "warning", "warning", false),
            new CollectionSpec("GUIStats", "performance", "warning", "performance", false, true),
            new CollectionSpec("GUIInfos", "info", "informational", "info", false),
            new CollectionSpec("GUILinks", "link", "informational", "link", false),
        };

        public static object HandleCommand(JObject @params)
        {
            var sdkVersion = string.Empty;
            var state = NewCacheState();
            try
            {
                CheckpointPrepareTool.ValidateProject(@params ?? new JObject());
                sdkVersion = VrchatAvatarUploadShared.ReadSdkVersion();
                state["sdkVersionMatched"] = string.Equals(
                    sdkVersion,
                    SupportedSdkVersion,
                    StringComparison.Ordinal);
                if (!(bool)state["sdkVersionMatched"])
                {
                    return CompletedUnavailable(
                        sdkVersion,
                        "unsupported_sdk_version",
                        "Exact cached SDK Builder alert enumeration is supported only for VRChat SDK 3.10.4.",
                        state);
                }

                var avatarPath = RequiredText(@params, "avatarPath");
                if (string.IsNullOrWhiteSpace(avatarPath))
                {
                    return CompletedUnavailable(
                        sdkVersion,
                        "avatar_path_required",
                        "avatarPath is required to bind the cached alerts to one exact avatar.",
                        state);
                }

                var advertisedPanel = VRCSdkControlPanel.window;
                state["panelOpen"] = advertisedPanel != null;
                if (advertisedPanel == null)
                {
                    return CompletedUnavailable(
                        sdkVersion,
                        "sdk_panel_not_open",
                        "The VRChat SDK panel is not open, so no existing Builder alert cache can be read.",
                        state);
                }

                IVRCSdkAvatarBuilderApi builder = null;
                state["builderAvailable"] = VRCSdkControlPanel.TryGetBuilder(out builder) && builder != null;
                if (!(bool)state["builderAvailable"])
                {
                    return CompletedUnavailable(
                        sdkVersion,
                        "sdk_avatar_builder_unavailable",
                        "The open VRChat SDK panel has no available Avatar Builder.",
                        state);
                }

                VRCSdkControlPanel panel;
                GameObject selectedAvatar;
                string builderStateReason;
                if (!TryReadBuilderState(builder, out panel, out selectedAvatar, out builderStateReason))
                {
                    return CompletedUnavailable(
                        sdkVersion,
                        "unsupported_sdk_avatar_builder_layout",
                        builderStateReason,
                        state);
                }
                state["builderStateLayoutMatched"] = true;
                state["panelBindingMatched"] = ReferenceEquals(panel, advertisedPanel);
                if (!(bool)state["panelBindingMatched"])
                {
                    return CompletedUnavailable(
                        sdkVersion,
                        "sdk_panel_builder_mismatch",
                        "The active SDK Avatar Builder is not bound to the advertised SDK panel.",
                        state);
                }
                state["checkedForIssues"] = panel.CheckedForIssues;
                state["selectedAvatarAvailable"] = selectedAvatar != null;

                VRCAvatarDescriptor descriptor;
                try
                {
                    descriptor = VrchatAvatarUploadShared.ResolveExactAvatar(avatarPath);
                }
                catch (Exception exception)
                {
                    return CompletedUnavailable(
                        sdkVersion,
                        "avatar_not_found",
                        exception.Message ?? "The requested avatar was not found.",
                        state);
                }

                state["selectedAvatarMatched"] = selectedAvatar != null
                    && selectedAvatar == descriptor.gameObject;
                if (!(bool)state["selectedAvatarAvailable"])
                {
                    return CompletedUnavailable(
                        sdkVersion,
                        "sdk_selected_avatar_unavailable",
                        "The VRChat SDK Builder has no selected avatar.",
                        state);
                }
                if (!(bool)state["selectedAvatarMatched"])
                {
                    return CompletedUnavailable(
                        sdkVersion,
                        "sdk_selected_avatar_mismatch",
                        "The requested avatar does not match the avatar currently selected in the VRChat SDK Builder.",
                        state,
                        BuildObjectTarget(selectedAvatar));
                }

                AlertLayout layout;
                string layoutReason;
                if (!TryReadLayout(panel, out layout, out layoutReason))
                {
                    return CompletedUnavailable(
                        sdkVersion,
                        "unsupported_sdk_alert_layout",
                        layoutReason,
                        state);
                }
                state["layoutMatched"] = true;

                var alerts = new JArray();
                var categoryCounts = NewCategoryCounts();
                var projectCount = AppendScopeAlerts(
                    alerts,
                    categoryCounts,
                    layout,
                    "project",
                    panel,
                    BuildProjectTarget());
                var avatarTarget = BuildObjectTarget(descriptor);
                var avatarCount = AppendScopeAlerts(
                    alerts,
                    categoryCounts,
                    layout,
                    "avatar",
                    descriptor,
                    avatarTarget);
                var sdkReportedAvatarAlertCount = panel.GUIAlertCount(descriptor);
                if (sdkReportedAvatarAlertCount != avatarCount)
                {
                    return CompletedUnavailable(
                        sdkVersion,
                        "sdk_alert_cache_count_mismatch",
                        "The cached SDK avatar alert count does not match the complete five-collection enumeration.",
                        state,
                        avatarTarget);
                }

                state["cachePopulated"] = alerts.Count > 0;
                state["cacheAcceptedFromPopulatedCollections"] =
                    !panel.CheckedForIssues && avatarCount > 0;
                if (!panel.CheckedForIssues && avatarCount == 0)
                {
                    return CompletedUnavailable(
                        sdkVersion,
                        "sdk_alert_cache_unchecked",
                        "The VRChat SDK Builder has not completed its cached validation pass and no retained avatar alerts prove a readable cache for the selected avatar.",
                        state,
                        avatarTarget);
                }

                var payload = BasePayload(sdkVersion, state);
                payload["available"] = true;
                payload["exact"] = true;
                payload["authoritativeForCurrentCachedPanelAlerts"] = true;
                payload["freshValidationClaimed"] = false;
                payload["reasonCode"] = JValue.CreateNull();
                payload["reason"] = JValue.CreateNull();
                payload["selectedAvatar"] = avatarTarget;
                payload["sdkReportedAvatarAlertCount"] = sdkReportedAvatarAlertCount;
                payload["projectAlertCount"] = projectCount;
                payload["avatarAlertCount"] = avatarCount;
                payload["returnedItemCount"] = alerts.Count;
                payload["countsByCategory"] = CategoryCountsPayload(categoryCounts);
                payload["blockingAlertCount"] = categoryCounts["error"];
                payload["alerts"] = alerts;
                return VRCForgeToolResult.Completed(
                    "Read the current cached VRChat SDK Builder alerts without refreshing or executing any action.",
                    payload);
            }
            catch (Exception exception)
            {
                return CompletedUnavailable(
                    sdkVersion,
                    "sdk_alert_cache_read_failed",
                    exception.Message ?? "The cached VRChat SDK Builder alerts could not be read.",
                    state);
            }
        }

        private static bool TryReadBuilderState(
            IVRCSdkAvatarBuilderApi builder,
            out VRCSdkControlPanel panel,
            out GameObject selectedAvatar,
            out string reason)
        {
            panel = null;
            selectedAvatar = null;
            reason = string.Empty;
            if (builder == null)
            {
                reason = "The SDK Avatar Builder instance is unavailable.";
                return false;
            }

            var builderType = builder.GetType();
            var panelField = FindBuilderField(builderType, "_builder");
            var selectedAvatarField = FindBuilderField(builderType, "_selectedAvatar");
            if (panelField == null || panelField.IsStatic
                || !typeof(VRCSdkControlPanel).IsAssignableFrom(panelField.FieldType)
                || selectedAvatarField == null || !selectedAvatarField.IsStatic
                || !typeof(Component).IsAssignableFrom(selectedAvatarField.FieldType))
            {
                reason = "The SDK Avatar Builder state layout does not match VRChat SDK 3.10.4.";
                return false;
            }

            panel = panelField.GetValue(builder) as VRCSdkControlPanel;
            var selectedDescriptor = selectedAvatarField.GetValue(null) as Component;
            selectedAvatar = selectedDescriptor == null ? null : selectedDescriptor.gameObject;
            if (panel == null)
            {
                reason = "The SDK Avatar Builder has no bound control panel.";
                return false;
            }
            return true;
        }

        private static FieldInfo FindBuilderField(Type type, string fieldName)
        {
            while (type != null)
            {
                var field = type.GetField(
                    fieldName,
                    BindingFlags.Instance | BindingFlags.Static
                    | BindingFlags.Public | BindingFlags.NonPublic
                    | BindingFlags.DeclaredOnly);
                if (field != null)
                {
                    return field;
                }
                type = type.BaseType;
            }
            return null;
        }

        private static bool TryReadLayout(
            VRCSdkControlPanel panel,
            out AlertLayout layout,
            out string reason)
        {
            layout = new AlertLayout();
            reason = string.Empty;
            var panelType = typeof(VRCSdkControlPanel);
            var issueType = panelType.GetNestedType(
                "Issue",
                BindingFlags.Public | BindingFlags.NonPublic);
            if (issueType == null)
            {
                reason = "The SDK Builder Issue type is unavailable.";
                return false;
            }

            layout.IssueType = issueType;
            layout.IssueText = issueType.GetField(
                "issueText",
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            layout.ShowAction = issueType.GetField(
                "showThisIssue",
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            layout.FixAction = issueType.GetField(
                "fixThisIssue",
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            layout.PerformanceRating = issueType.GetField(
                "performanceRating",
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (layout.IssueText == null || layout.IssueText.FieldType != typeof(string)
                || layout.ShowAction == null || !typeof(Delegate).IsAssignableFrom(layout.ShowAction.FieldType)
                || layout.FixAction == null || !typeof(Delegate).IsAssignableFrom(layout.FixAction.FieldType)
                || layout.PerformanceRating == null || !layout.PerformanceRating.FieldType.IsEnum)
            {
                reason = "The SDK Builder Issue field layout does not match VRChat SDK 3.10.4.";
                return false;
            }

            foreach (var spec in CollectionOrder)
            {
                var field = panelType.GetField(
                    spec.FieldName,
                    BindingFlags.Instance | BindingFlags.NonPublic);
                if (field == null)
                {
                    reason = "The SDK Builder alert cache field '" + spec.FieldName + "' is unavailable.";
                    return false;
                }
                var dictionary = field.GetValue(panel) as IDictionary;
                if (dictionary == null)
                {
                    reason = "The SDK Builder alert cache field '" + spec.FieldName + "' has an unsupported layout.";
                    return false;
                }
                layout.Collections.Add(spec.FieldName, dictionary);
            }
            return true;
        }

        private static int AppendScopeAlerts(
            JArray alerts,
            IDictionary<string, int> categoryCounts,
            AlertLayout layout,
            string scope,
            Object subject,
            JObject target)
        {
            var startCount = alerts.Count;
            foreach (var spec in CollectionOrder)
            {
                var dictionary = layout.Collections[spec.FieldName];
                if (!dictionary.Contains(subject))
                {
                    continue;
                }
                var rawList = dictionary[subject] as IEnumerable;
                if (rawList == null)
                {
                    throw new InvalidOperationException(
                        "The SDK Builder alert cache field '" + spec.FieldName + "' contains an unsupported value.");
                }

                var issues = new List<object>();
                foreach (var rawIssue in rawList)
                {
                    if (rawIssue == null || !layout.IssueType.IsInstanceOfType(rawIssue))
                    {
                        throw new InvalidOperationException(
                            "The SDK Builder alert cache contains an unsupported Issue value.");
                    }
                    issues.Add(rawIssue);
                }
                if (spec.SortByPerformanceRating)
                {
                    issues = issues
                        .OrderByDescending(issue => ReadRatingValue(layout, issue))
                        .ToList();
                }

                foreach (var issue in issues)
                {
                    var message = layout.IssueText.GetValue(issue) as string;
                    if (string.IsNullOrWhiteSpace(message))
                    {
                        continue;
                    }
                    var showAction = layout.ShowAction.GetValue(issue);
                    var fixAction = layout.FixAction.GetValue(issue);
                    if ((showAction != null && !(showAction is Delegate))
                        || (fixAction != null && !(fixAction is Delegate)))
                    {
                        throw new InvalidOperationException(
                            "The SDK Builder alert action capability has an unsupported layout.");
                    }

                    var rating = layout.PerformanceRating.GetValue(issue);
                    var alert = new JObject
                    {
                        ["index"] = alerts.Count,
                        ["scope"] = scope,
                        ["category"] = spec.Category,
                        ["severity"] = spec.Severity,
                        ["iconKind"] = spec.IconKind,
                        ["performanceRating"] = spec.SortByPerformanceRating && rating != null
                            ? (JToken)rating.ToString()
                            : JValue.CreateNull(),
                        ["title"] = JValue.CreateNull(),
                        ["titleAvailable"] = false,
                        ["message"] = message,
                        ["target"] = target.DeepClone(),
                        ["blocker"] = spec.Blocker,
                        ["selectable"] = showAction != null,
                        ["fixable"] = fixAction != null,
                        ["autoFixAvailable"] = fixAction != null,
                        ["actionsExecuted"] = false,
                        ["source"] = JValue.CreateNull(),
                        ["sourceAvailable"] = false,
                        ["sdkStableId"] = JValue.CreateNull(),
                        ["sdkStableIdAvailable"] = false,
                    };
                    alerts.Add(alert);
                    categoryCounts[spec.Category]++;
                }
            }
            return alerts.Count - startCount;
        }

        private static int ReadRatingValue(AlertLayout layout, object issue)
        {
            var rating = layout.PerformanceRating.GetValue(issue);
            return rating == null ? 0 : Convert.ToInt32(rating, CultureInfo.InvariantCulture);
        }

        private static JObject BuildProjectTarget()
        {
            return new JObject
            {
                ["kind"] = "project",
                ["objectType"] = typeof(VRCSdkControlPanel).FullName,
                ["name"] = JValue.CreateNull(),
                ["path"] = JValue.CreateNull(),
                ["scenePath"] = JValue.CreateNull(),
                ["assetPath"] = JValue.CreateNull(),
                ["globalObjectId"] = JValue.CreateNull(),
            };
        }

        private static JObject BuildObjectTarget(Object subject)
        {
            if (subject == null)
            {
                return new JObject
                {
                    ["kind"] = "unknown",
                    ["objectType"] = JValue.CreateNull(),
                    ["name"] = JValue.CreateNull(),
                    ["path"] = JValue.CreateNull(),
                    ["scenePath"] = JValue.CreateNull(),
                    ["assetPath"] = JValue.CreateNull(),
                    ["globalObjectId"] = JValue.CreateNull(),
                };
            }

            Transform transform = null;
            if (subject is Component component)
            {
                transform = component.transform;
            }
            else if (subject is GameObject gameObject)
            {
                transform = gameObject.transform;
            }

            string assetPath;
            string globalObjectId;
            try
            {
                assetPath = AssetDatabase.GetAssetPath(subject) ?? string.Empty;
            }
            catch
            {
                assetPath = string.Empty;
            }
            try
            {
                globalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(subject).ToString();
            }
            catch
            {
                globalObjectId = string.Empty;
            }

            return new JObject
            {
                ["kind"] = transform == null ? "object" : "scene_object",
                ["objectType"] = subject.GetType().FullName ?? subject.GetType().Name,
                ["name"] = subject.name ?? string.Empty,
                ["path"] = transform == null
                    ? (JToken)JValue.CreateNull()
                    : AvatarAuthoringCrudCore.GetTransformPath(transform),
                ["scenePath"] = transform == null
                    ? (JToken)JValue.CreateNull()
                    : transform.gameObject.scene.path ?? string.Empty,
                ["assetPath"] = string.IsNullOrWhiteSpace(assetPath)
                    ? (JToken)JValue.CreateNull()
                    : assetPath,
                ["globalObjectId"] = string.IsNullOrWhiteSpace(globalObjectId)
                    ? (JToken)JValue.CreateNull()
                    : globalObjectId,
            };
        }

        private static object CompletedUnavailable(
            string sdkVersion,
            string reasonCode,
            string reason,
            JObject state,
            JObject selectedAvatar = null)
        {
            var payload = BasePayload(sdkVersion, state);
            payload["available"] = false;
            payload["exact"] = false;
            payload["authoritativeForCurrentCachedPanelAlerts"] = false;
            payload["freshValidationClaimed"] = false;
            payload["reasonCode"] = reasonCode;
            payload["reason"] = reason ?? string.Empty;
            payload["selectedAvatar"] = selectedAvatar == null
                ? (JToken)JValue.CreateNull()
                : selectedAvatar;
            payload["sdkReportedAvatarAlertCount"] = JValue.CreateNull();
            payload["projectAlertCount"] = JValue.CreateNull();
            payload["avatarAlertCount"] = JValue.CreateNull();
            payload["returnedItemCount"] = 0;
            payload["countsByCategory"] = CategoryCountsPayload(NewCategoryCounts());
            payload["blockingAlertCount"] = JValue.CreateNull();
            payload["alerts"] = new JArray();
            return VRCForgeToolResult.Completed(
                "The cached VRChat SDK Builder alerts are unavailable without changing editor or scene state.",
                payload);
        }

        private static JObject BasePayload(string sdkVersion, JObject state)
        {
            return new JObject
            {
                ["ok"] = true,
                ["schema"] = Schema,
                ["operation"] = "read_vrchat_sdk_builder_alerts",
                ["supportedSdkVersion"] = SupportedSdkVersion,
                ["sdkVersion"] = sdkVersion ?? string.Empty,
                ["cacheState"] = state == null ? NewCacheState() : state.DeepClone(),
                ["cacheTimestampAvailable"] = false,
                ["cacheTimestamp"] = JValue.CreateNull(),
                ["readOnly"] = true,
                ["mutationStarted"] = false,
                ["writeOccurred"] = false,
                ["committed"] = false,
                ["commitState"] = "not_started",
                ["requestMayHaveCommitted"] = false,
            };
        }

        private static JObject NewCacheState()
        {
            return new JObject
            {
                ["sdkVersionMatched"] = false,
                ["panelOpen"] = false,
                ["builderAvailable"] = false,
                ["builderStateLayoutMatched"] = false,
                ["panelBindingMatched"] = false,
                ["checkedForIssues"] = false,
                ["selectedAvatarAvailable"] = false,
                ["selectedAvatarMatched"] = false,
                ["layoutMatched"] = false,
                ["cachePopulated"] = false,
                ["cacheAcceptedFromPopulatedCollections"] = false,
            };
        }

        private static Dictionary<string, int> NewCategoryCounts()
        {
            return new Dictionary<string, int>(StringComparer.Ordinal)
            {
                ["error"] = 0,
                ["warning"] = 0,
                ["performance"] = 0,
                ["info"] = 0,
                ["link"] = 0,
            };
        }

        private static JObject CategoryCountsPayload(IDictionary<string, int> counts)
        {
            return new JObject
            {
                ["error"] = counts["error"],
                ["warning"] = counts["warning"],
                ["performance"] = counts["performance"],
                ["info"] = counts["info"],
                ["link"] = counts["link"],
            };
        }

        private static string RequiredText(JObject raw, string name)
        {
            var token = raw?[name];
            return token == null || token.Type != JTokenType.String
                ? string.Empty
                : ((string)token ?? string.Empty).Trim();
        }
    }
}
#endif
