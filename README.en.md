<div align="center">

![VRCForge — AI Agent + MCP for VRChat Avatar Editing](docs/assets/vrcforge-atelier-banner.png)

[![Stable](https://img.shields.io/github/v/release/ayyitong888/VRCForge?label=stable&style=flat-square)](https://github.com/ayyitong888/VRCForge/releases/latest)
![Current version](https://img.shields.io/badge/current-v1.10.2-d9487c?style=flat-square)
[![License GPL-3.0-only](https://img.shields.io/badge/license-GPL--3.0--only-64748b?style=flat-square)](LICENSE)
![Platform Windows x64](https://img.shields.io/badge/platform-Windows%20x64-0ea5e9?style=flat-square)
[![GitHub Stars](https://img.shields.io/github/stars/ayyitong888/VRCForge?style=social)](https://github.com/ayyitong888/VRCForge/stargazers)

[简体中文](README.md) · **English** · [日本語](README.ja.md)


**[Website](https://ayyitong888.github.io/VRCForge/en/)**

</div>

# VRCForge: AI-assisted VRChat avatar editor and Unity MCP tools

VRCForge is an open-source tool for VRChat avatar creators. Describe the face,
clothing, material, or animation change you want in natural language, and the
assistant can inspect your project, suggest a change, and apply it.

> Back up your Unity / VRChat Avatar project before using any asset-writing feature.

Current release: [1.10.2](https://github.com/ayyitong888/VRCForge/releases/tag/v1.10.2) · [Release notes](docs/RELEASE_NOTES_1.10.2.md)

## Download: install both the App and the Unity plugin

**Download both items below from the same Release.**

| Required download | Where to install |
| --- | --- |
| **[App offline installer (.exe)](https://github.com/ayyitong888/VRCForge/releases/download/v1.10.2/VRCForge_Offline_Installer_x64.exe)** | Run on Windows. You may use the [web installer](https://github.com/ayyitong888/VRCForge/releases/download/v1.10.2/VRCForge_Web_Installer_x64.exe) instead; choose one installer. |
| **[Unity Core plugin (VRCForge.unitypackage)](https://github.com/ayyitong888/VRCForge/releases/download/v1.10.2/VRCForge.unitypackage)** | **Import All** into each Unity project you want to connect. Installing the App alone does not connect Unity. |

Update both the App and the Unity plugin when upgrading.

## Start in three steps

1. **Back up your project and install the App.** Leave the App closed for now.
2. **Import the Unity plugin.** Choose Import All in your Unity 2022.3 LTS project. Follow the setup window to add missing dependencies, including the VRChat Avatar SDK. Wait for compilation and `[VRCForge MCP] Core Ready`.
3. **Open the App and select your project and Avatar.** Set up one AI option below. Start with a read-only inspection, then try a small change and check its result.

You can create Avatar projects with [Creator Companion (VCC)](https://vcc.docs.vrchat.com/guides/getting-started/) or ALCOM. Existing projects do not need a different project manager. Read the [release notes](https://github.com/ayyitong888/VRCForge/releases) before upgrading older versions; `1.4.0` cannot overwrite `1.3.6` directly.

## Choose your AI

- **Built-in AI:** In AI settings, enter your model service's API key, select a model and test the connection. Then describe your task in chat.
- **External MCP agent:** In Settings → Connectors, select your client and install its configuration, or give the generated configuration to your agent. Keep VRCForge running, restart or reconnect the client, and ask the agent to test the connection. An installed configuration does not confirm a connected client. [Connection details](USER_MANUAL.md#external-agent-connectors)

## Edit, restore and reuse

Review the target and plan before editing, then inspect the appearance and result. Choose per-action confirmation, automatic or full permissions. Automatic mode retains confirmation for high-risk operations; **restoring a checkpoint always requires separate confirmation**. Checkpoints do not replace project backups.

Save common workflows as **Skills**, or import and export **.vsk Skill packs**. [Skill guide](USER_MANUAL.md#skill-packages-and-path-to-skill)

The supported environment is Windows x64, Unity 2022.3 LTS and a VRChat SDK3 Avatar project. Available edits depend on your avatar's controls and installed plugins. Head swaps and part transplants may still need manual mesh, weight or UV work. See the [User Manual](USER_MANUAL.md) and [compatibility matrix](docs/COMPATIBILITY_MATRIX.md) for support and limitations.

## Documentation

- [User Manual / 使用手册](USER_MANUAL.md)
- [1.10.2 release notes](docs/RELEASE_NOTES_1.10.2.md)
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

Project indexes, chats, memory and checkpoints are stored locally by default. Content sent to external model services depends on your model and operation. Keep keys, paid assets and private files out of public material, and review diagnostic bundles before sharing. [Privacy details](USER_MANUAL.md#privacy-boundary)

VRCForge is licensed under [GPL-3.0-only](LICENSE). Its Unity MCP Core and tool contracts are project-owned implementations; the public package does not bundle third-party Unity MCP runtime code. See [DEPENDENCIES](DEPENDENCIES.md) and [NOTICE](NOTICE) for third-party dependencies and notices.
