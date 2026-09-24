# Current setup and repair routes

This is Agent guidance for existing 1.8.1 tools, not a new automation engine. Always discover the actual schema. Do not infer argument values, confirmation tokens, project identities or package hashes from examples.

| Observed problem | Available action | Evidence required afterward |
| --- | --- | --- |
| Internal model cannot answer/test fails | App model settings and explicit connection test; explain actual auth/endpoint/model/quota/network error | A successful test for that exact configuration, not just saved settings |
| External Agent works, App provider is unconfigured | Keep external provider; report App provider gap as not applicable to that client | Authenticated MCP plus all project/Core/compile checks |
| Gateway cannot list tools | Guide App start, Gateway enable and client configuration reload | Real initialize/tools-list response; no write through a dead connection |
| Project scan pending/error/empty | Read discovery state; refresh in App or ask for exact existing folder | Completed scan or validated manually supplied project root |
| Correct existing project not registered/selected | `vrcforge_register_project` / `vrcforge_select_project` | Independent selected-project readback, preserving previous selection on failure |
| Selected project not running | Ask user to open this exact project; no current MCP open-existing-project tool | Matching running editor and selected instance |
| Bundled Core missing/incomplete | `vrcforge_install_unity_core` with `projectPath`, subject to approval | Installation receipt, then status/compile/required-tool readback |
| Diagnosis confirms incomplete or mixed-version official Core files | Inspect the installed source/compile evidence and bundled repair scope, then request approval through `vrcforge_install_unity_core`; a still-callable old assembly does not prove the files on disk compile | Preserve installation receipt and backup; verify fresh compilation and `vrcforge_core_upgrade_status` before resuming the original task |
| Core copied but waiting | `vrcforge_core_upgrade_status` with `projectPath` | Distinguish core_unreachable, waiting_for_domain_reload, waiting_for_post_install_compile, ready |
| Connection matches wrong editor | Diagnose with `vrcforge_unity_status`; select intended project via supervised path | Same exact project and registered instance, not any live instance |
| SDK/package errors | `vrcforge_package_manager_status`, `vrcforge_package_install_plan`, `vrcforge_diagnose_package_install_errors` | Concrete missing/incompatible package evidence; only use an actually exposed approved installer after reviewing its plan |
| Compilation errors | `vrcforge_get_compile_errors` when Core is callable; otherwise App diagnostics / ask for exact Console error | Errors resolved and compilation finished; identify ownership before touching third-party code |
| Failed Core change needs recovery | `vrcforge_restore_unity_core` after separate approval | Exact `projectPath`, `backupPath`, `backupSha256`, `installedSha256` from the same installation receipt; restore result and fresh readiness |

For tool execution use the current discovered schemas, not this table as a request template. Core installation's known required input is `projectPath`. It installs bundled Core files; it is not evidence that a `.unitypackage` UI import occurred. Restore hashes must never be guessed or taken from a different installation.

Manual package route: obtain the matching VRCForge package from the installed distribution or its matching release, confirm the destination Unity project, then guide Assets → Import Package → Custom Package… → Import All. Preserve package provenance and recheck compilation and connection. Never download an unrelated “MCP for Unity” as a substitute.

After Core is already connected, ordinary content `.unitypackage` imports are a different workflow: inspect/plan, bind the exact execution target, then the exposed outfit-package importer if it supports the requested content. Its presence does not solve first-Core bootstrap. A returned import job ID means poll that job; it does not mean import complete.

Do not repeat writes after a timeout until their operation status is known. Preserve checkpoints and pending receipts. If the required tool is absent, say what is missing and provide one precise manual action, then re-read state. Do not advertise automatic Unity launch, automatic provider credential repair, arbitrary source repair or recovery of every possible third-party error as existing capability.

Keep logs local unless the user authorizes sharing. Never include tokens/Keys, Avatar names or full project paths in public reports. A support bundle still needs review before publication.
