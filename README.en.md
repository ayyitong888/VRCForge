<div align="center">

![VRCForge — AI Agent + MCP for VRChat Avatar Editing](docs/assets/social-preview.svg)

[![Stable](https://img.shields.io/badge/stable-v1.7.10-22c55e?style=flat-square)](https://github.com/ayyitong888/VRCForge/releases/latest)
![Development target](https://img.shields.io/badge/development-v1.8.0-4f46e5?style=flat-square)
[![License GPL-3.0-only](https://img.shields.io/badge/license-GPL--3.0--only-64748b?style=flat-square)](LICENSE)
![Platform Windows x64](https://img.shields.io/badge/platform-Windows%20x64-0ea5e9?style=flat-square)
[![GitHub Stars](https://img.shields.io/github/stars/ayyitong888/VRCForge?style=social)](https://github.com/ayyitong888/VRCForge/stargazers)

[简体中文](README.md) · **English**

</div>

# VRCForge: AI Agent + MCP tools for VRChat Avatar editing

VRCForge gives VRChat (VRC) Avatar creators a local AI Agent and MCP toolset for
avatar editing. It connects a desktop Agent, a local FastAPI runtime, and Unity
Editor tools in one supervised workflow for inspecting an avatar, planning a
change, requesting execution, validating the result, and restoring changes.

You can discuss face shapes and BlendShapes, materials and shaders, wardrobes
and outfits, avatar composition, and performance optimization in natural
language. VRCForge turns the Agent's intent into reviewable Unity operations.
Asset writes show an approval step and use checkpoints, readback validation,
and restore support. Each avatar, dependency set, and Unity environment still
requires project-specific verification.

> Back up your Unity / VRChat Avatar project before using any asset-writing feature.

The current published stable release is
[v1.7.10](https://github.com/ayyitong888/VRCForge/releases/tag/v1.7.10).
Version `v1.8.0` is under development and testing and has not been formally
released. Use the published Releases for installation and upgrades.

## What VRCForge can do

| Capability | Status | Purpose |
| --- | --- | --- |
| AI Agent-assisted editing | Available | Inspect an Avatar, discuss a plan in natural language, and pass confirmed operations to supervised tools in the desktop workspace. |
| VRChat Avatar editing | Available | Scan BlendShapes, assist with face tuning, inspect lilToon, Poiyomi, and generic materials, and review Gesture Manager screenshots. |
| Wardrobe and outfit workflows | Beta | Scan integer-parameter wardrobes, inspect `.unitypackage`, Booth folders, or loose prefabs, and prepare import and binding plans; writes still require approval and project validation. |
| Avatar composition workflows | Available; verify per project | Built-in Skills orchestrate face-tracked or gesture-only head swaps and part transplants using checkpoints, readback, motion, and multi-view checks. |
| Optimization and diagnostics | Available / some writes Beta | Audit VRAM, materials, meshes, parameters, and build readiness, then prepare a conservative step-by-step optimization plan. |
| MCP 2.0 and external Agents | Available | Local MCP clients such as Codex and Claude Code can read, plan, and submit write requests; VRCForge Desktop approves the actual writes. |
| `.vsk` skill packages | Available | Preflight packages, manage signatures and trust, atomically import and project Skills, capture Path-to-Skill workflows, and scaffold packages with the SDK. |
| Avatar Encryption / Anti-Rip | Connector preview | The public build provides scan, plan, and preview entry points. Execution is not included in the public repository and is not counted as a completed public feature. |

“Available” means that the public product path exists. It does not guarantee
hands-off success for every Avatar. Project-specific rigs, menus, FX layers,
shaders, paid dependencies, and visual results need checks before and after a write.

## How it works

VRCForge uses this supervised flow for Unity asset writes:

```text
Scan → Plan → Preview → Approval → Checkpoint → Apply → Validate → Restore
```

- Project indexes, chats, memory, checkpoints, and connector settings are stored locally by default.
- Unity asset writes require explicit approval in the normal permission mode.
- Write targets are bound to a specific project and Unity Editor instance, then checked through readback or validation results.
- Restore is a separate confirmed operation. A checkpoint does not replace a project backup.
- The content sent to an external model service depends on the Provider, model, and operation you choose.

## Quick start

### 1. Install and connect Unity

Download these files from the
[latest Release](https://github.com/ayyitong888/VRCForge/releases/latest):

- `VRCForge_Web_Installer_x64.exe`, or `VRCForge_Offline_Installer_x64.exe` for an offline install
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
Planning exposes read and planning capabilities; write requests are handed to
the desktop approval flow.

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

Write commands such as `apply` and `rollback` create approval requests. Actual
writes still pass through the desktop approval flow.

## Scope and current boundaries

- The target environment is Windows x64, Unity 2022.3 LTS, and a VRChat SDK3 Avatar project.
- Some read-only checks work without a Provider. AI chat, planning, and vision reasoning require a configured compatible Provider.
- Outfit import, generic Unity CRUD, some optimization writes, and community Skills are Beta paths. Preview them first and validate in a copied or backed-up project.
- `v1.8.0` is a source test candidate. Protocol, source-test, or successful tool-call evidence does not replace complete real-avatar, visual, restore, and packaged-build acceptance.
- Quest/Android support, third-party asset licensing, and paid dependencies depend on the specific Avatar and assets.

## Documentation

- [User Manual / 使用手册](USER_MANUAL.md)
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
