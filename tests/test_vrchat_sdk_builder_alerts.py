from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "Assets" / "VRCForge" / "Editor" / "VrchatSdkBuilderAlertsTool.cs"
CONTRACT_PATH = ROOT / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpToolContract.cs"
TOOL = TOOL_PATH.read_text(encoding="utf-8-sig")
CONTRACT = CONTRACT_PATH.read_text(encoding="utf-8-sig")


def test_sdk_builder_alert_atom_is_registered_as_an_exact_read_only_core_tool() -> None:
    assert 'toolId: "vrc_read_vrchat_sdk_builder_alerts"' in TOOL
    assert "Access = VRCForgeCommandAccess.ReadOnly" in TOOL
    assert (
        '{ "vrc_read_vrchat_sdk_builder_alerts", '
        '"VRCForge.Editor.VrchatSdkBuilderAlertsTool" }'
    ) in CONTRACT

    read_only = CONTRACT[
        CONTRACT.index("private static readonly HashSet<string> ExpectedReadOnlyNames") :
        CONTRACT.index("private static readonly HashSet<string> ExpectedPlanningNames")
    ]
    assert '"vrc_read_vrchat_sdk_builder_alerts"' in read_only


def test_sdk_builder_alert_atom_reads_the_five_cached_collections_with_runtime_shape_checks() -> None:
    assert 'private const string SupportedSdkVersion' not in TOOL
    assert 'state["sdkVersionAvailable"] = !string.IsNullOrWhiteSpace(sdkVersion);' in TOOL
    assert "unsupported_sdk_version" not in TOOL
    assert re.findall(
        r'new CollectionSpec\("(GUI\w+)"',
        TOOL,
    ) == ["GUIErrors", "GUIWarnings", "GUIStats", "GUIInfos", "GUILinks"]
    assert 'GetNestedType(\n                "Issue"' in TOOL
    for field in ("issueText", "showThisIssue", "fixThisIssue", "performanceRating"):
        assert f'"{field}"' in TOOL
    assert 'FindBuilderField(builderType, "_builder")' in TOOL
    assert 'FindBuilderField(builderType, "_selectedAvatar")' in TOOL
    assert "BindingFlags.Instance | BindingFlags.NonPublic" in TOOL


def test_sdk_builder_alert_atom_never_opens_refreshes_selects_or_invokes() -> None:
    forbidden = (
        "EditorApplication.ExecuteMenuItem",
        "ShowControlPanel(",
        ".CreateValidationsGUI(",
        ".SelectAvatar(",
        "DynamicInvoke(",
        "MethodInfo.Invoke(",
        "Selection.",
        "EditorUtility.SetDirty",
        "EditorSceneManager",
        "MarkSceneDirty",
        "AssetDatabase.SaveAssets",
        "Undo.",
    )
    for marker in forbidden:
        assert marker not in TOOL
    assert "builder.SelectedAvatar" not in TOOL
    assert "selectedAvatarField.GetValue(null) as Component" in TOOL
    assert "showAction != null" in TOOL
    assert "row.FixAction != null" in TOOL
    assert '["actionsExecuted"] = false' in TOOL


def test_sdk_builder_alert_atom_fails_closed_for_every_unavailable_boundary() -> None:
    for reason in (
        "avatar_path_required",
        "sdk_panel_not_open",
        "sdk_avatar_builder_unavailable",
        "unsupported_sdk_avatar_builder_layout",
        "sdk_panel_builder_mismatch",
        "sdk_alert_cache_unchecked",
        "avatar_not_found",
        "sdk_selected_avatar_unavailable",
        "sdk_selected_avatar_mismatch",
        "unsupported_sdk_alert_layout",
        "sdk_alert_cache_read_failed",
    ):
        assert f'"{reason}"' in TOOL
    assert 'payload["available"] = false;' in TOOL
    assert 'payload["exact"] = false;' in TOOL
    assert 'payload["authoritativeForCurrentCachedPanelAlerts"] = false;' in TOOL
    assert 'payload["alerts"] = new JArray();' in TOOL


def test_sdk_builder_alert_atom_preserves_scope_message_and_action_capabilities() -> None:
    assert '"project",\n                    panel' in TOOL
    assert '"avatar",\n                    descriptor' in TOOL
    assert '["message"] = row.Message' in TOOL
    assert '["blocker"] = row.Spec.Blocker' in TOOL
    assert '["selectable"] = row.ShowAction != null' in TOOL
    assert '["fixable"] = row.FixAction != null' in TOOL
    assert '["autoFixAvailable"] = row.FixAction != null' in TOOL
    assert 'new CollectionSpec("GUIErrors", "error", "blocking", "error", true)' in TOOL
    assert 'new CollectionSpec("GUIWarnings", "warning", "warning", "warning", false)' in TOOL
    assert 'new CollectionSpec("GUIStats", "performance", "warning", "performance", false, true)' in TOOL
    assert "sdkReportedAvatarAlertCount = panel.GUIAlertCount(descriptor);" in TOOL
    assert 'payload["sdkReportedAvatarAlertCount"] = sdkReportedAvatarAlertCount;' in TOOL
    assert 'payload["returnedItemCount"] = alerts.Count;' in TOOL
    assert "if (sdkReportedAvatarAlertCount != avatarCount)" not in TOOL


def test_sdk_builder_alert_atom_accepts_only_a_proven_populated_avatar_cache() -> None:
    assert 'state["checkedForIssues"] = panel.CheckedForIssues;' in TOOL
    assert 'state["cachePopulated"] = alerts.Count > 0;' in TOOL
    assert "!panel.CheckedForIssues && avatarCount > 0" in TOOL
    assert "if (!panel.CheckedForIssues && avatarCount == 0)" in TOOL
    assert "!panel.CheckedForIssues && alerts.Count > 0" not in TOOL
    assert "sdkReportedAvatarAlertCount != avatarCount" not in TOOL
    assert 'state["cacheAcceptedFromPopulatedCollections"] =' in TOOL
    assert 'payload["freshValidationClaimed"] = false;' in TOOL


def test_sdk_native_auto_fix_is_a_current_row_selector_with_sdk_button_post_action_behavior() -> None:
    assert '["fixActionAvailable"] = row.FixAction != null' in TOOL
    assert '["autoFix"] = autoFix' in TOOL
    assert '["source"] = "sdk", ["index"] = index, ["message"] = row.Message' in TOOL
    assert '"available"] = row.FixAction != null' in TOOL
    assert "EnumerateScopeIssues(layout, subject)" in TOOL
    assert "EnumerateScopeIssues(layout, panel)" in TOOL
    assert "EnumerateScopeIssues(layout, descriptor)" in TOOL
    assert "layout.FixAction.FieldType != typeof(Action)" in TOOL
    assert "The selected SDK alert row changed" in TOOL
    assert "The selected SDK alert row is no longer present" in TOOL

    adapter_path = ROOT / "Assets" / "VRCForge" / "Editor" / "NativeSdkFixActions.cs"
    adapter = adapter_path.read_text(encoding="utf-8-sig")
    assert "internal static Action Resolve(JObject selector)" in adapter
    assert "VrchatSdkBuilderAlertsTool.ResolveNativeFixBinding(selector)" in adapter
    assert "binding.FixAction();" in adapter
    assert "EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());" in adapter
    assert "EditorUtility.SetDirty(binding.Subject);" in adapter
    assert '"RunValidations"' in adapter
    assert "throw exception.InnerException;" in adapter


def test_sdk_builder_alert_atom_does_not_invent_sdk_metadata() -> None:
    assert '["title"] = JValue.CreateNull()' in TOOL
    assert '["titleAvailable"] = false' in TOOL
    assert '["source"] = JValue.CreateNull()' in TOOL
    assert '["sourceAvailable"] = false' in TOOL
    assert '["sdkStableId"] = JValue.CreateNull()' in TOOL
    assert '["sdkStableIdAvailable"] = false' in TOOL
    assert '["cacheTimestampAvailable"] = false' in TOOL
    assert '["freshValidationClaimed"] = false' in TOOL


def test_sdk_builder_alert_atom_reports_explicit_no_write_facts() -> None:
    for fact in (
        '["readOnly"] = true',
        '["mutationStarted"] = false',
        '["writeOccurred"] = false',
        '["committed"] = false',
        '["commitState"] = "not_started"',
        '["requestMayHaveCommitted"] = false',
    ):
        assert fact in TOOL
