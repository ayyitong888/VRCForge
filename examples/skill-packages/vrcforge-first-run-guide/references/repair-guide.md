# Connection and repair

Start with the user's chosen project, Agent and requested outcome. Use installed tool schemas and current receipts to pick the next action. For an incomplete compact result, read its returned resource or stored result. Use the signed-in or external client that already works.

## The selected Unity project

- If the project is ambiguous, use discovery results from the App, Hub, VCC or ALCOM. A pending scan or a count alone cannot identify a directory. Ask which project only if the available evidence leaves several choices. Register or select the exact existing root when needed. Keep the existing project and manager.
- When the Editor is closed, open the selected project through `vrcforge_open_unity_project`. Reuse an already-open or starting instance. A launch receipt tells you the Editor was requested. Continue with the selected project's Core/connection status while Unity imports packages and compiles. Ask for an Editor install or license interaction only if the observed launch failure calls for it.
- When bundled Core is missing, or its files are confirmed incomplete or mixed, use the host-side `vrcforge_install_unity_core` for that project. Keep the installation receipt. Follow `vrcforge_core_upgrade_status` across domain reload and post-install compilation, then check the required tool or handshake. A pending install or reload already has an operation to follow.
- If Core cannot answer, use host-side status, package files, or available Editor logs. Missing runtime or Console data leaves compile status unknown. After Core is ready, `vrcforge_get_compile_errors` can report the actual compiler output. Select a repair for the specific package or script named by the error.
- If the installed release lacks the supervised Core installer, use its matching `VRCForge.unitypackage` in the intended Editor's Assets → Import Package → Custom Package → Import All. For a requested rollback, pass the exact retained backup and installed identities from the same installation to `vrcforge_restore_unity_core` under separate approval. Ordinary outfit-package import uses its own plan and import job after Core connects.

## Packages

Unity lists direct project dependencies in `Packages/manifest.json`. An embedded package lives under `Packages/` and can declare its own Unity registry dependencies in its `package.json`. The top-level list can omit both. A fresh project may have no `Packages/packages-lock.json` until Unity completes resolution. Open the project, then follow Unity’s resolver or compiler result. [Unity project manifest](https://docs.unity3d.com/2022.3/Documentation/Manual/upm-manifestPrj.html) · [Embedded packages](https://docs.unity3d.com/2022.3/Documentation/Manual/upm-embed.html) · [Lock files](https://docs.unity3d.com/2022.3/Documentation/Manual/upm-conflicts-auto.html)

VPM packages list their dependencies in `vpmDependencies`. If a locked package is missing from disk, execute the plan to restore its locked version. If files exist at a different version, inspect both versions and the requested target before changing either. VPM Resolver checks the project on open. Unity registry dependencies go through Unity's own resolver. [VRChat package format](https://vcc.docs.vrchat.com/vpm/packages/) · [VRChat Resolver](https://vcc.docs.vrchat.com/vpm/resolver/)

For a required VPM install, read `vrcforge_package_install_plan`. `use_installed` means keep the installed version. Execute a supported plan through the internal `vrcforge_package_install_request` or external `vrcforge_install_vpm_package` and its returned confirmation. If the current client reports an interaction it cannot perform, give the one action for that exact error and project. [VCC package management](https://vcc.docs.vrchat.com/vpm/) · [ALCOM documentation](https://vrc-get.anatawa12.com/en/alcom/)

For a connection request, a matching Core response establishes contact. For an Avatar task, continue to the requested Avatar. A working SDK and an empty scene can coexist. If the model lacks a Descriptor, handle that model component within the user's approved task. [VRChat Avatar setup](https://creators.vrchat.com/avatars/creating-your-first-avatar/)

## Provider or external connection

For an internal provider failure, use the App's test for the selected configuration and its exact error. A stored key alone cannot confirm connectivity. For an external client, use the configuration generated for that client in App Connectors. After a reload, verify an authenticated MCP call. The external client's model works independently of the App's provider.

Use the user's existing authorization. Keep credentials local. When reporting a blocker, name the failed operation and what the user actually needs to do, such as completing sign-in. Resume the previous task once its prerequisites pass.
