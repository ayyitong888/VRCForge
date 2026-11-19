---
name: vrcforge-first-run-guide
title: 连接引导与日常排障
description: Set up VRCForge on a Windows PC even with no project, Unity Hub, Editor, VCC or ALCOM installed. Install the missing prerequisites and connect the intended Unity project. Also diagnose existing connections and set up Unity user tools.
permission-mode: approval_required
risk-level: high
workflow-domain: diagnostics
required-resources: []
game-only-acceptance: []
allowed-tools:
  - vrcforge_load_internal_tool_block
  - vrcforge_read_installed_skill
  - vrcforge_read_tool_result
  - vrcforge_ask_user
  - vrcforge_call_user
  - vrcforge_progress_list
  - vrcforge_progress_replace
  - vrcforge_progress_update
  - vrcforge_web_search
  - vrcforge_web_fetch
  - vrcforge_read_text_file
  - vrcforge_list_directory
  - vrcforge_read_recent_logs
  - vrcforge_classify_shell
  - vrcforge_execute_shell
  - vrcforge_shell_process
  - vrcforge_execute_approved_shell
  - vrcforge_know_yourself
  - vrcforge_health
  - vrcforge_unity_status
  - vrcforge_unity_tools
  - vrcforge_project_lifecycle_status
  - vrcforge_project_create_plan
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
  - vrcforge_exit_skill
  - vrcforge_package_manager_status
  - vrcforge_package_install_plan
  - vrcforge_package_install_request
  - vrcforge_install_vpm_package
  - vrcforge_diagnose_package_install_errors
  - vrcforge_list_avatars
support-files:
  - workflows/first-run.json
  - references/zero-start.md
  - references/repair-guide.md
  - references/user-tool-author-guide.md
---

# Connect or repair VRCForge

when-to-use: Set up an internal or external Agent, connect a Unity project, diagnose a connection failure, or prepare a supported Unity user tool.

when-NOT-to-use: For questions about how the App works, use `vrcforge-docs`. Continue ordinary avatar work with its own tools once connected.

For “什么都没装，帮我连上工程” start in the current chat, even without a selected project. Read `references/zero-start.md` with `vrcforge_read_installed_skill` before trying Unity tools. Inspect the host, install missing software, then create or open the intended project. Use the international Unity Hub and global Editor. In a projectless App chat, discover host project setup in `project_environment/assets_packages`. Start with `unity_project_lifecycle_status`, then load only the tool needed for the user's choice. A working external Agent already supplies its own model.

For “connect this project,” start with that project. Call the narrowest status tool for the failing layer, or use `vrcforge_know_yourself` if several layers are unknown. If the user only asked for a diagnosis or a download, stop at that outcome.

When Unity is closed, discover `vrcforge_open_unity_project` (internal `unity_open_project`) and open the selected project in its matching Editor. If it is already open or starting, follow that instance through dependency resolution, compilation and the Core handshake. A new project's first import can take time. Use the actual package or compile error to decide whether a repair is needed.

Use `vrcforge_read_installed_skill(name, file)` to read the reference for the task.

- [Connection and repair](references/repair-guide.md) covers provider setup, project selection, Core installation and recovery, package resolution, and compilation.
- [Starting with only a download](references/zero-start.md) covers official installers, a compatible Unity Editor and creating a first project.
- [Unity user tools](references/user-tool-author-guide.md) covers creating a descriptor, packaging, installing and invoking it through the public Gateway tools.

Carry out each tool-supported step, including installation. Submit the tool request if approval is required. Ask the user only for the particular choice, sign-in, or interaction that still needs them. Report the outcome in their language. For a connection request, verify that the intended project's Core answers. Read an Avatar only when the original task needs one. If the guide was activated, call `vrcforge_exit_skill` with its name and the reason before resuming another task or authoring user-tool files outside this scope.
