<div align="center">

![VRCForge — AI Agent + MCP for VRChat Avatar Editing](docs/assets/vrcforge-atelier-banner.png)

[![Stable](https://img.shields.io/github/v/release/ayyitong888/VRCForge?label=stable&style=flat-square)](https://github.com/ayyitong888/VRCForge/releases/latest)
![Current version](https://img.shields.io/badge/current-v1.8.0-d9487c?style=flat-square)
[![License GPL-3.0-only](https://img.shields.io/badge/license-GPL--3.0--only-64748b?style=flat-square)](LICENSE)
![Platform Windows x64](https://img.shields.io/badge/platform-Windows%20x64-0ea5e9?style=flat-square)
[![GitHub Stars](https://img.shields.io/github/stars/ayyitong888/VRCForge?style=social)](https://github.com/ayyitong888/VRCForge/stargazers)

[简体中文](README.md) · **English** · [日本語](README.ja.md)

🌙 **VRCForge Atelier** · Bring the idea into Unity, and keep every edit under your care ✨

**[🌸 Visit the creator atelier](https://ayyitong888.github.io/VRCForge/en/)**

</div>

# VRCForge: AI-assisted VRChat avatar editor and Unity MCP tools

VRCForge is an open-source VRChat avatar editor with a Unity MCP server for
AI-assisted avatar customization: face and BlendShape editing, outfits,
materials, and optimization checks. It connects a local desktop AI agent,
a local FastAPI runtime, and Unity Editor tools in one supervised workflow
for inspecting an avatar, planning a change, requesting execution, validating
the result, and restoring changes.

You can discuss face shapes and BlendShapes, materials and shaders, wardrobes
and outfits, avatar composition, and performance optimization in natural
language. VRCForge turns the Agent's intent into reviewable Unity operations.
Asset writes follow the selected permission mode and use checkpoints, readback validation,
and restore support. Each avatar, dependency set, and Unity environment still
requires project-specific verification.

> Back up your Unity / VRChat Avatar project before using any asset-writing feature.

This page describes the published [v1.8.0 stable release](https://github.com/ayyitong888/VRCForge/releases/tag/v1.8.0).
Get matching installers and Unity packages from that same Release.

**[Download VRCForge v1.8.0](https://github.com/ayyitong888/VRCForge/releases/tag/v1.8.0)** · [Read the Release Notes](https://github.com/ayyitong888/VRCForge/releases)

## What VRCForge can do

From changing a BlendShape to building a wardrobe or editing Animator FX, version 1.8.0 includes tools that apply changes. Tell your Agent what to edit, let it inspect the project, and review the result.

| Capability | Implemented operations |
| --- | --- |
| BlendShapes and expressions | Read and change existing face, body and clothing BlendShape weights, then preview the result. Face reshaping requires the avatar to have suitable shape keys. |
| Outfits, wardrobes and menus | Bind clothing, create or manage exclusive wardrobes and clothing/accessory toggles, and edit VRChat expression menus and parameters. Outfit integration can use installed Modular Avatar or VRCFury. |
| Animation and Animator FX | Create, edit and batch animation curves, FX layers, states and transitions. Combine object toggles and material properties to author outfit transitions, crossfades and dissolve animations. |
| Materials, shaders and textures | Edit colors, numeric and vector properties, textures and material slots; replace shaders. Use the properties actually exposed by each shader, beyond just lilToon and Poiyomi. |
| Objects, bones and components | Create, copy, move and reparent objects, edit component properties and save prefabs. Configure outfit armature integration, constraints and supported PhysBone components. |
| Optimization and project checks | Inspect VRAM, meshes, materials, parameters and build readiness. Change texture sizes, formats and compression, and configure optimization components when their dependencies are installed. |
| Built-in AI and external MCP | Configure a Provider and API key for the built-in Agent, or connect an external MCP Agent. Both can inspect, plan and edit; your permission mode controls confirmation and automatic execution. |
| Skills and reusable workflows | Use built-in head-swap and part-transplant workflows; import, export and enable .vsk Skills. MCP Tools perform actions, Resources expose state, and Prompts reuse the existing Skills. |
| Inspect, diagnose and restore | Review Scene View screenshots and Gesture Manager parameters and states, read diagnostics, save checkpoints and inspect changes. Restore a checkpoint through a separate confirmation. |

Capabilities depend on the project: expression, viseme or face-tracking keys are not automatically face-reshaping controls. Shader properties must exist and match the supported types. Modular Avatar, VRCFury and AAO integrations require their packages. Writes follow your permission mode; checkpoint restoration requires separate confirmation.

## Find a workflow for your avatar

| Task | Starting point |
| --- | --- |
| Face and expression customization | Scan BlendShapes, preview a small adjustment, and review the result. |
| Outfit and wardrobe editing | Bind clothing, edit wardrobes, menus and parameters, then create and inspect outfit animations. |
| Shader and material editing | Read shader properties, edit materials, textures and slots, then inspect the result. |
| Avatar optimization | Use diagnostics to adjust texture import settings or configure installed optimization plugins. |
| Unity MCP and AI agent workflows | [Connect an MCP client](#connect-codex-claude-code-or-another-mcp-agent) to use the same approval and verification flow. |

## How it works

VRCForge uses this supervised flow for Unity asset writes:

```text
Inspect → Plan → Check permissions → Apply → Read back and inspect → Restore if needed
```

- Project indexes, chats, memory, checkpoints, and connector settings are stored locally by default.
- Choose per-action confirmation, automatic or full permissions. Automatic mode retains confirmation for high-risk operations; checkpoint restoration always requires separate confirmation.
- Write targets are bound to a specific project and Unity Editor instance, then checked through readback or validation results.
- Restore is a separate confirmed operation. A checkpoint does not replace a project backup.
- The content sent to an external model service depends on the Provider, model, and operation you choose.

## Quick start

### 1. Install and connect Unity

Download these files from the
[latest Release](https://github.com/ayyitong888/VRCForge/releases/latest):

- For 1.8.0, use `VRCForge_Web_Installer_x64_Hotfix1.exe`, or `VRCForge_Offline_Installer_x64_Hotfix1.exe` for an offline install. Hotfix1 restores the running-app prompt and Retry flow.
- `VRCForge.unitypackage`

Then connect in three steps:

1. Install VRCForge, but leave the App closed.
2. Open the target Unity 2022.3 LTS / VRChat SDK3 Avatar project, import all of `VRCForge.unitypackage`, and wait for compilation and `[VRCForge MCP] Core Ready`.
3. Start `VRCForge.exe`, select the project, and connect. The App discovers the project-owned VRCForge Core; no separate MCP Server install or MCP Token copy is required.

Read the matching [Release Notes](https://github.com/ayyitong888/VRCForge/releases)
before upgrading an older version. Version `1.4.0` is a breaking install boundary
and cannot overwrite `1.3.6` directly.

### 2. Run a first safe check

1. Select the project and Avatar in VRCForge.
2. Run Doctor and confirm the App, Provider, Unity, and MCP connection status.
3. Start with a read-only scan or Validation Report.
4. Request one small change and review its target, plan, and approval card.
5. After applying it, review the checkpoint, readback, and validation delta. Request restore separately if needed.

See the [User Manual](USER_MANUAL.md) for the complete workflow.

## Connect Codex, Claude Code, or another MCP Agent

VRCForge supports both its built-in Agent and external MCP clients. External
clients use the local MCP + REST gateway and the same public tool contracts.
Planning exposes read and planning capabilities; execution can edit Unity under
your selected permission mode. Operations requiring manual confirmation produce approval requests.

Open **Settings → Connectors → Generic MCP client** in VRCForge:

1. Find the MCP configuration file the client actually uses and identify whether it is JSON, TOML, or YAML.
2. Prefer **STDIO** for a local desktop or CLI client. Use **Streamable HTTP** only when the client explicitly supports it.
3. Keep VRCForge running, restart or reconnect the client, and confirm that a `vrcforge` server and its tool list appear.

Automatic installation accepts the full path to a JSON configuration file.
For TOML or YAML, copy the generated block and add it manually. HTTP also
requires Agent Gateway and the required token in the client process. Never
commit plaintext credentials to the repository.

See [External Agent Connectors](USER_MANUAL.md#external-agent-connectors) for details.

## Command-line tools

With VRCForge Desktop running, the local CLI can run diagnostics, list
checkpoints, and create a Validation Report:

```powershell
# Packaged build
backend\vrcforge_backend.exe --cli doctor
backend\vrcforge_backend.exe --cli checkpoint list --project C:\Path\To\UnityProject

# Source checkout
python tools\vrcforge_cli.py doctor
python tools\vrcforge_cli.py validation run --project C:\Path\To\UnityProject
```

`apply` and `rollback` create approval requests by default. Add `--execute` to
confirm and execute in the terminal through the same backend approval and recovery flow.

## Scope and current boundaries

- The target environment is Windows x64, Unity 2022.3 LTS, and a VRChat SDK3 Avatar project.
- Some read-only checks work without a Provider. AI chat, planning, and vision reasoning require a configured compatible Provider.
- Optimization integrations require compatible installed plugins. Configuring AAO components does not itself perform AAO's build-time optimization. Head-swap and part-transplant workflows cannot replace required mesh seam, weight or UV editing.
- The Avatar protection connector supports inspection, planning and previews; the public package does not include the private protection executor.
- Protocol, source-test, or successful tool-call evidence does not establish visual correctness for every avatar. Verify the result on the avatar being edited and preserve its recovery point.
- Quest/Android support, third-party asset licensing, and paid dependencies depend on the specific Avatar and assets.

## Documentation

- [User Manual / 使用手册](USER_MANUAL.md)
- [v1.8.0 stable release notes](docs/RELEASE_NOTES_1.8.0.md)
- [v1.7.10 stable release notes](docs/RELEASE_NOTES_1.7.10.md)
- [Compatibility matrix](docs/COMPATIBILITY_MATRIX.md)
- [Product regression contract](docs/PRODUCT_REGRESSION_CONTRACT.md)
- [Optimization strategy](docs/OPTIMIZATION_STRATEGY.md)
- [Unity Package guide](packaging/README.md)
- [Dependencies and licenses](DEPENDENCIES.md) · [NOTICE](NOTICE) · [SECURITY](SECURITY.md)

## Develop from source

Normal users should prefer the Release installer. For source development, run
these commands from the repository root:

```powershell
python -m pip install -r requirements.txt
start_dashboard.cmd
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
before contributing.

## Privacy and license

VRCForge follows a local-first design. API keys, Gateway Tokens, paid asset
contents, and private files should stay out of the repository and public
diagnostic material. Review a Support Bundle before sharing it.

The project is licensed under [GPL-3.0-only](LICENSE). The VRCForge Unity MCP
Core, command catalog, input-schema metadata, and tool-result contract are
project-owned implementations. Release gates require the public package to
contain no bundled third-party Unity MCP runtime code.
