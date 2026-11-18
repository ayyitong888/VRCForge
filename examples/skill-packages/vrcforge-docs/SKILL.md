---
name: vrcforge-docs
description: Answer questions about the installed VRCForge App, Agent and MCP tools using current documentation and local evidence. Route setup and repair requests to the first-run guide.
metadata:
  short-description: "VRCForge documentation, self-knowledge, setup and verified repair"
title: VRCForge Docs
permission-mode: approval_required
risk-level: medium
workflow-domain: diagnostics
required-resources: []
game-only-acceptance: []
allowed-tools:
  - vrcforge_load_internal_tool_block
  - vrcforge_list_installed_skills
  - vrcforge_read_installed_skill
  - vrcforge_read_tool_result
  - vrcforge_ask_user
  - vrcforge_call_user
  - vrcforge_progress_list
  - vrcforge_progress_replace
  - vrcforge_progress_update
  - vrcforge_web_fetch
  - vrcforge_web_search
  - vrcforge_classify_shell
  - vrcforge_execute_shell
  - vrcforge_shell_process
  - vrcforge_execute_approved_shell
  - vrcforge_health
  - vrcforge_skill_manifest
  - vrcforge_skill_check
  - vrcforge_read_recent_logs
  - vrcforge_know_yourself
  - vrcforge_unity_status
  - vrcforge_unity_tools
  - vrcforge_project_lifecycle_status
  - vrcforge_project_catalog_registration_status
  - vrcforge_register_project
  - vrcforge_create_project
  - vrcforge_select_project
  - vrcforge_open_unity_project
  - vrcforge_install_unity_core
  - vrcforge_restore_unity_core
  - vrcforge_core_upgrade_status
  - vrcforge_inspect_unity_editor_dialogs
  - vrcforge_handle_unity_editor_dialog
  - vrcforge_get_compile_errors
  - vrcforge_list_user_unity_tools
  - vrcforge_install_user_unity_tools
  - vrcforge_invoke_user_unity_tool
  - vrcforge_refresh_asset_database
  - vrcforge_package_manager_status
  - vrcforge_package_install_plan
  - vrcforge_package_install_request
  - vrcforge_install_vpm_package
  - vrcforge_diagnose_package_install_errors
  - vrcforge_list_directory
  - vrcforge_read_text_file
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_exit_skill
---

# VRCForge Docs

Use this guide to answer questions about VRCForge features, settings, Skills, permissions, checkpoints, and the App or MCP client the user is using. For an ordinary avatar edit, use the corresponding editing tools.

For “can I do this here?”, check this client's tools, enabled Skills and settings. For “how does it work?”, open the relevant section of the [user manual](https://github.com/ayyitong888/VRCForge/blob/main/USER_MANUAL.md) or [README](https://github.com/ayyitong888/VRCForge/blob/main/README.md). Prefer the installed version or its tagged manual. For a recent change or download, check the matching [release notes](https://github.com/ayyitong888/VRCForge/releases). Follow the first-party page for the product named in the question and cite the specific source that answers it.

For a local failure, inspect the affected project, provider, or Core. Use a narrow status or log tool if the failing layer is known. Use `vrcforge_know_yourself` when several layers are unknown. An external MCP client uses its own model configuration.

For “connect it” or “fix it,” carry out the authorized work. Discover the installed `vrcforge-first-run-guide` and read the relevant file with `vrcforge_read_installed_skill(name, file)`. Its `references/repair-guide.md` covers existing projects, `references/zero-start.md` covers a first installation, and `references/user-tool-author-guide.md` covers custom Unity tools. The exact-file reader lets you fetch just that reference. Keep using the tools provided by the current client and its permission mode. Report the result for the requested project or the specific action that still requires the user.
