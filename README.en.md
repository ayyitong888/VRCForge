<div align="center">

![VRCForge — AI Agent + MCP for VRChat Avatar Editing](docs/assets/vrcforge-atelier-banner.png)

[![Stable](https://img.shields.io/github/v/release/ayyitong888/VRCForge?label=stable&style=flat-square)](https://github.com/ayyitong888/VRCForge/releases/latest)
![Current version](https://img.shields.io/badge/current-v1.8.1-d9487c?style=flat-square)
[![License GPL-3.0-only](https://img.shields.io/badge/license-GPL--3.0--only-64748b?style=flat-square)](LICENSE)
![Platform Windows x64](https://img.shields.io/badge/platform-Windows%20x64-0ea5e9?style=flat-square)
[![GitHub Stars](https://img.shields.io/github/stars/ayyitong888/VRCForge?style=social)](https://github.com/ayyitong888/VRCForge/stargazers)

[简体中文](README.md) · **English** · [日本語](README.ja.md)

🌙 **VRCForge Atelier** · Bring the idea into Unity, and keep every edit under your care ✨

**[🌸 Visit the creator atelier](https://ayyitong888.github.io/VRCForge/en/)**

</div>

# VRCForge: AI-assisted VRChat avatar editor and Unity MCP tools

VRCForge is an open-source tool for VRChat avatar creators. Describe the face,
clothing, material, or animation change you want in natural language, and the
assistant can inspect your project, suggest a change, and apply it.

From avatar checks and outfit changes to material and animation editing, including
head-swap and part-transplant work, VRCForge shows what it is about to change and
helps you check the result afterward. Choose confirmation before each change, or
let eligible changes run automatically; restores always ask for confirmation.
Support depends on your avatar, installed plugins, and Unity
project, so verify the result in your own project.

> Back up your Unity / VRChat Avatar project before using any asset-writing feature.

This page describes the published [v1.8.1 stable release](https://github.com/ayyitong888/VRCForge/releases/tag/v1.8.1).
Get matching installers and Unity packages from that same Release.

**[Download VRCForge v1.8.1](https://github.com/ayyitong888/VRCForge/releases/tag/v1.8.1)** · [Read the Release Notes](https://github.com/ayyitong888/VRCForge/releases)

## What VRCForge can do

From small details to a complete look, let AI handle repetitive steps so you can spend more time creating.

| What you want to do | How VRCForge helps |
| --- | --- |
| Shape and expression | Adjust the face, body and expression controls already included in your avatar, and preview the result as you go. |
| Clothes and accessories | Fit clothing and accessories to your avatar and organize the outfit menu you use in game. |
| Outfit animations | Create animated outfit changes with effects such as fades and dissolves. |
| Colors and materials | Change colors, textures and materials on clothes, hair and accessories to bring your look together. |
| Combine avatar parts | Follow head-swap and part-transplant workflows to combine avatar parts and check how they fit together. |
| Lighten your avatar | Find textures and parts using more resources, adjust texture size and compression, and use installed plugins for further optimization. |
| Use your preferred AI | Enter an API key for the built-in AI, or connect your usual AI assistant through MCP. Choose to confirm steps or allow automatic editing. |
| Reuse your favorite workflows | Save common operations as Skills to use again, or import and export .vsk Skill packs. |
| Preview and restore | Check the changed appearance and animations, save checkpoints, and choose a saved checkpoint to restore after confirmation. |

Available edits depend on your avatar. Face reshaping needs suitable controls in the model, and some outfit and optimization features need additional plugins.

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

- For 1.8.1, use `VRCForge_Web_Installer_x64.exe`, or `VRCForge_Offline_Installer_x64.exe` for an offline install. Hotfix1 restores the running-app prompt and Retry flow.
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
