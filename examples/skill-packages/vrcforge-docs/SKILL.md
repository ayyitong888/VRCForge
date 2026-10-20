---
name: vrcforge-docs
description: "Use for VRCForge capabilities, settings, model-provider or external MCP setup, Skills, Unity Core, permissions, checkpoints, troubleshooting, and VRCForge self-knowledge. For an authorized setup or repair request, use documentation and observed tools to carry the task through verification. Do not use for unrelated software, general Unity tutorials, or ordinary avatar edits that already have a domain Skill."
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
  - vrcforge_select_project
  - vrcforge_install_unity_core
  - vrcforge_restore_unity_core
  - vrcforge_core_upgrade_status
  - vrcforge_get_compile_errors
  - vrcforge_list_user_unity_tools
  - vrcforge_install_user_unity_tools
  - vrcforge_invoke_user_unity_tool
  - vrcforge_refresh_asset_database
  - vrcforge_package_manager_status
  - vrcforge_package_install_plan
  - vrcforge_diagnose_package_install_errors
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_exit_skill
---

# VRCForge Docs

Give current, sourced VRCForge guidance. For action requests, let the Agent choose and execute the necessary steps through existing tools until the requested result is verified. This is an agentic guide, with no automatic entrypoint or fixed execution plan.

## When to use

VRCForge feature questions, how to configure or connect it, supported tools and Skills, permission and recovery behavior, or a request to diagnose and fix its provider, project, Core or MCP connection. “You” or “this app” means VRCForge only when the conversation actually refers to VRCForge.

## When NOT to use

Unrelated software or network help, generic Unity/VRChat questions, or a connected avatar editing task with a suitable domain Skill. Negative examples: “what is MCP?” needs an explanation; “GitHub cannot connect” does not trigger VRCForge readiness; “change this material” goes to the material workflow. Merely discussing a repair does not authorize one.

## Choose the source that answers the question

- **Only the exe downloaded, nothing else prepared:** Read the first-run guide's `references/zero-start.md`: correct installer, VCC or ALCOM, compatible Unity, Avatar project, model and Core connection. Keep an existing manager; otherwise let the user choose. In every region, verify the VRChat-supported Unity Editor and avoid Tuanjie Engine/Hub. The Agent may download official installers through available host tools; downloading does not authorize system installation. Do not assume a project, SDK, model or source checkout, or claim unobserved installation/opening.
- **Documented usage or broad orientation:** Read the relevant section of the first-party [user manual](https://github.com/ayyitong888/VRCForge/blob/main/USER_MANUAL.md), or [README](https://github.com/ayyitong888/VRCForge/blob/main/README.md). Open/fetch the actual page, not just search snippets. Search the exact feature or error when necessary. Prefer an available matching-version local manual or release tag over main; main describes source and may differ from the installed release. Do not scan the repository to answer a narrow usage question.
- **Current release or download:** Read [VRCForge Releases](https://github.com/ayyitong888/VRCForge/releases). Bind claims to the actual published version; manual version, source presence and a successful build do not establish publication or installed behavior.
- **Current environment or failure:** Discover the available read Tools and Resources, load only what is needed, and inspect the failing layer. A public manual cannot establish a local connection, permission, installed Skill or successful model request. `vrcforge_know_yourself` is an optional broad readiness snapshot, not a mandatory first call. Use narrower status or log tools when those answer the question.
- **Setup, everyday repair, or user-tool authoring:** Discover the installed `vrcforge-first-run-guide`. Prefer the existing installed-Skill reader for its instructions, then an exact file with `vrcforge_read_installed_skill(name, file)`: `references/repair-guide.md` for connection/compilation/recovery, `references/user-tool-author-guide.md` for a user-tool package. A native MCP Prompt is a whole-guide fallback that includes all declared support files; do not describe it as selective loading. If an older guide demands a broad readiness report despite sufficient current evidence, retain this guide's optional-snapshot rule. If the guide is unavailable, use the manual and discovered capabilities; state the gap without inventing tools.
- **Avatar operations:** Use the discovered domain Skill and its exact tool contracts. Exit any active diagnosis-only Skill scope before resuming the original editing task. Do not turn a documentation question into an avatar scan.

Read zero or one detailed reference initially. Follow another source only when the first leaves a material question unresolved. In a source checkout, project rules and local status constrain engineering work; private status, roadmap and session files are not public product documentation.

## Execute the requested outcome

For a factual question, answer with the supporting page/section and stop. For “help me connect/fix/configure”, documentation and diagnosis are intermediate steps: continue the authorized task, choose the repair from actual evidence, and verify the original failed operation afterward. Do not finish with only a self-check report, instructions the Agent could execute, or a promise to continue.

Discover exact tool names, input schemas, exposure layers and Resource/Prompt identities before calling them. Tool names in this guide are navigation hints, not guaranteed capabilities. Internal and external Agents use the same existing Gateway and native Skill/Resource/Prompt chain; an external Agent's working model does not depend on the App's provider configuration.

For installer downloads, the internal Agent may use its host Shell. An external Agent needs its own download/host capability (Codex has its own Shell); VRCForge MCP does not expose the internal host Shell. Without such a capability, guide the download manually.

Ask only for a missing fact, choice, authorization, or physical action that cannot be obtained through available tools. Existing authorization persists within its scope. Preserve supervised writes, approval, checkpoint, validation and separately approved rollback; Skill text grants no permissions. Do not use Shell, generated C#, direct asset edits or another transport to bypass a missing approved capability. Stop identical repeated failures and report the exact remaining blocker.

Report what changed, the observed verification, and any unverified remainder. Cite the exact documentation page or local file used; distinguish documented behavior, live observations and inference. A listening port, saved setting, loaded Skill or accepted pending receipt alone is not completion.
