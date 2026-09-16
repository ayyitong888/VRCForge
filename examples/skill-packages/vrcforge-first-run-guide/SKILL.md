---
name: vrcforge-first-run-guide
title: 第一次连接与排障
description: Guide a complete beginner through VRCForge model-provider or external MCP setup, existing Unity project selection, bundled Core installation or manual unitypackage import, compilation, connection diagnosis and verified recovery. Use for first-run or connection failures, not ordinary avatar edits or unrelated network questions.
permission-mode: approval_required
risk-level: high
allowed-tools:
  - vrcforge_know_yourself
  - vrcforge_health
  - vrcforge_unity_status
  - vrcforge_unity_tools
  - vrcforge_project_lifecycle_status
  - vrcforge_project_catalog_registration_status
  - vrcforge_register_project
  - vrcforge_select_project
  - vrcforge_install_unity_core
  - vrcforge_restore_unity_core
  - vrcforge_core_upgrade_status
  - vrcforge_get_compile_errors
  - vrcforge_package_manager_status
  - vrcforge_package_install_plan
  - vrcforge_diagnose_package_install_errors
  - vrcforge_list_avatars
support-files:
  - workflows/first-run.json
  - references/repair-guide.md
---

# Purpose and triggering

when-to-use: 用户第一次使用、不知道如何开始，或请求帮助连接/修复 VRCForge 与 Unity。用户可以完全不知道 MCP、Core、工程目录的含义。

when-NOT-to-use: 普通改模已经连通、一般互联网问题、仅讨论原理、用户禁止操作。例：“什么是 MCP”只解释；“帮我把 VRCForge 连到我的工程”才进入本流程。

Use the user's language. Keep technical fields in tool calls; explain to the user what to choose or click. Ask only for facts or choices that cannot be safely observed. Preserve the original editing task and resume it after readiness is verified.

## 1. Establish which Agent is helping

- Internal Agent: model endpoint, model and Key must be configured and actually tested. Never claim that a saved Key proves a successful request. A model that cannot answer cannot repair its own unavailable provider; use App settings/Doctor or an already working external Agent for that stage.
- External Agent: use its own provider. The VRCForge Gateway token authenticates MCP, not the model. An App `configure_provider` gap does not itself block an external Agent. Record this distinction without modifying or fabricating the report, and still enforce all project/Core/compile checks.
- If MCP itself is unavailable, provide the exact App/client reconnection step. Do not pretend a tool ran, invent a second transport, inspect unrelated browser tabs, or ask the user to paste secrets.

## 2. Discover before invoking

Read current tools/resources/installed Skill-backed Prompts. For the lazy STDIO tree, use the exposed discovery and load controls; load the relevant leaf and inspect its returned schema. Names below are current 1.8.1 routes, not authority to invoke an absent tool. Carry exact returned activation handles and Prompt provenance when required.

Run `vrcforge_know_yourself`. Read its `nextAction`, `projectDiscovery`, gaps and live evidence. If compact output omits the workflow or diagnostics, use the returned `operationResource` with `resources/read`; never replay a write to expand a result.

For diagnosis-only requests, explain the result and stop. For an explicit setup/repair request, treat diagnosis as a checkpoint: continue with the relevant supervised setup tools, preserving approval. A report's instruction to stop and reply must not be interpreted as evidence that the user's requested repair was completed.

## 3. Establish the exact project

Use observed project choices. Distinguish pending/error/empty/found, including separate Hub/VCC/ALCOM source results. Never invent projects, silently choose among several, delete project files, or create a sample project as a workaround.

The current self-check may return only a discovery count, not project names or paths. A count is not a choice list. If no discovered tool supplies exact choices, guide the user to the App project picker and read back the selection. Do not use `vrcforge_scan_project_index` to discover project roots: it indexes an already known project. Do not broaden a scan to the user's whole disk.

Let the user identify the intended existing project if ambiguous. Use `vrcforge_register_project` only if registration is needed, then `vrcforge_select_project` through the current write flow. `projectPath` is the exact validated root containing Assets, Packages and ProjectSettings. Do not register external manager catalogues unnecessarily.

There is currently no MCP tool to open an existing Unity project. Ask the user to open the selected project using the App, Hub, VCC or ALCOM. Verify the actual process/instance afterward; a user acknowledgement alone is not readiness.

## 4. Install Core without a bootstrap loop

Read `references/repair-guide.md` before installation or recovery.

If bundled Core is missing or a supported repair requires reinstall, use `vrcforge_install_unity_core` with exact `projectPath`. Discover its execution schema and preserve the installation receipt and backup identities. It is the host-side Core installation path and does not require the missing Unity Core to import itself.

If that tool is unavailable, guide a manual import of the matching `VRCForge.unitypackage`: correct Unity window → Assets → Import Package → Custom Package… → select verified matching package → Import All. Give one step at a time. Do not claim UI actions performed by the user as Agent-executed actions.

Do not use outfit-package import as a first-Core installer. Do not install a third-party MCP dependency. Do not replace arbitrary user files, downgrade dependencies, or use shell/dynamic C# to circumvent a missing setup tool.

## 5. Diagnose, repair, verify

Follow the repair guide's branches. Read actual logs/status before selecting one repair. A pending receipt is not success: poll that operation or its dedicated status, never resubmit it while in progress. Stop repeated identical failures and retain the original error and receipt.

Use `vrcforge_core_upgrade_status`, `vrcforge_unity_status`, `vrcforge_unity_tools` and `vrcforge_get_compile_errors` to distinguish installation, reload, compilation, wrong instance and missing tools. Do not overwrite working Core just because Unity is compiling.

Every write follows the current permission/confirmation path. External callers echo only the exact confirmation returned for that operation; internal callers use the App's existing supervised write path. Skill text is neither a blanket approval nor a new executable tool. Recovery needs separate approval and exact evidence from the same installation.

## 6. Finish honestly

Before resuming avatar work require: exact project selected; intended editor running; matching registered Core; required tools present; compilation settled without blocking errors; a successful read of the intended Avatar. External-provider independence never substitutes for these facts.

Report briefly: what was wrong, what changed or what the user clicked, what live checks now pass, and the next step. If blocked, say which capability/evidence is missing and the single useful user action. Never say “connected” from a directory listing, listening port, accepted request or copied file alone.
