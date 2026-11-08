# VRCForge User Manual / 使用手册

VRCForge is a local desktop agent workspace for VRChat avatar editing.
VRCForge 是一个面向 VRChat Avatar 编辑的本地桌面 Agent 工作区。

This manual explains the public workflow and feature status without project-specific paths or private configuration details.

**第一次使用？从 [安装与入门](README.md#第一次使用) 开始。App 安装包和同版本的 Unity 插件都要下载。**

**New here? Start with the [installation guide](README.en.md#first-run). Download both the App installer and the Unity plugin from the same release.**

Download the matching files from [GitHub Releases](https://github.com/ayyitong888/VRCForge/releases/latest). This manual covers individual features in more detail.

## Feature Status / 功能状态

| Feature | 功能 | Status |
| --- | --- | --- |
| Avatar and facial Blendshape loading | 读取 Avatar 与脸部 Blendshape | 已可用 / Available |
| Manual slider editing and undo | 手动滑块调整与撤销 | 已可用 / Available |
| Natural-language Blendshape planning | 自然语言生成 Blendshape 调整 | 已可用 / Available |
| Reference-image assisted face editing | 参考图辅助捏脸 | 已可用 / Available |
| AI face tuning history | AI 捏脸历史 | 已可用 / Available |
| Saved face tuning presets | 捏脸预设保存与重放 | 已可用 / Available |
| Locked Blendshapes for partial reroll | 锁定形态键后局部重抽 | 已可用 / Available |
| Shader / Material tuning | Semantic material tuning for lilToon, Poiyomi, and conservative Generic fallback | 已可用 / Available |
| Avatar Encryption / Anti-Rip addon | lilToon and Poiyomi scan/plan/preview plus private-addon connector request interfaces; other shader families compatibility-only | Connector preview, approval/checkpoint/rollback required; private module required for execution |
| Agent workspace with multi-chat sessions | Agent 工作台与多会话 | 已可用 / Available |
| First-run resilient normal-agent fallback | Optional Unity/skill/project failures do not block ordinary agent chat | 已可用 / Available |
| Chat persistence and history replay | 会话持久化与历史回放 | 已可用 / Available |
| Automatic and `/compact` history compaction | Exact provider/model usage drives bounded prefire, automatic compaction, and a hard safety gate; manual `/compact`, visible cancellation, deterministic fallback, and restart recovery remain available | Available |
| Slash-command skill invocation | 斜杠命令直接调用 skill | 已可用 / Available |
| Steering queue and run visualization | 插队队列与运行可视化 | 已可用 / Available |
| Scheduled Goals | `/goal` user create/view/pause/resume/clear plus scoped Agent `get_goal` / `create_goal` / `update_goal`, durable background wake/resume, restart recovery and idempotent materialization | Available |
| Agent Memory controls | Explicit user-wide or exact-project memory create, review, and clear controls; local persistence only | Available |
| Explicit Computer Use | User-started desktop turns with protected-target checks, visible activity/Stop controls, cancellation, and capture feedback | Beta, Developer Options and explicit activation required |
| Provider reasoning/thinking trace | API-returned visible reasoning, thinking, or thought-summary items appear as default-collapsed chat rows | 已可用 / Available |
| Project memory / incremental scan | Local index shows added, modified, and deleted project files in the desktop workspace | 已可用 / Available |
| Outfit package import planning | Inspect `.unitypackage`, Booth folders, and loose prefab/texture folders, then request a supervised import; the release `VRCForge.unitypackage` supports fresh-project direct import | Beta, approval/checkpoint required |
| Package/plugin install diagnostics | Read VPM/ALCOM/vrc-get status, install output, and compile-error context before planning repairs | Beta, read-only diagnostics |
| Delegated sub-agent workers | Allowlisted read/plan skill dispatch with durable owner-chat result cards, restart recovery, and revision-checked Adopt/Dismiss review | Beta |
| Tool Registry v1 | Standard tool metadata exposed to desktop, MCP/gateway, and CLI surfaces | Available |
| CLI diagnostics and readiness | Packaged `vrcforge` CLI covers doctor, provider test, Unity/project/avatar scans, validation, Build/Test readiness, checkpoints, skill/tool registry, and request-based apply/rollback | Available |
| Full Validator and Build/Test readiness | `vrcforge.validation.v1` plus Build/Test readiness gates for compile, SDK/plugin/package, expression, animator, material, performance, and generated-residue findings | Available |
| Model Optimization Planner | Read-only optimization planning, baseline scan, target profiles, dependency doctor, VRAM/material/mesh/parameter audits, and step-by-step plan output | Available |
| Optimizer integrations | Planning and guarded apply-request surfaces are available, while live writer and rollback-proof coverage varies by integration; AAO dedicated writers remain deferred, and unsupported VRCFury writes stay blocked until validated | Beta; inspect the integration-specific preview and proof status |
| Before/after screenshot capture and vision review | 执行前后截图与视觉复核 | 已可用 / Available |
| Modular Avatar and VRCFury read-only scans | Modular Avatar / VRCFury 只读扫描 | 已可用 / Available |
| Outfit setup wrapper and VPM package status/install | Outfit 安装封装与 VPM 包状态/安装 | 已可用 / Available |
| Avatar performance scan | Avatar 性能扫描 | 已可用 / Available |
| Unity compile-error reading | Unity 编译错误读取 | 已可用 / Available |
| Roslyn Advanced Power Mode | Roslyn 高级模式 | 已可用 / Available |
| Generic Unity CRUD tools | 通用 Unity CRUD 工具 | Beta, local tests pass |
| Generic avatar authoring primitives | Expression parameters / menu controls / FX animator states | Beta, local tests pass; preview path covered by wardrobe workflow |
| Modular Avatar component writer | MergeArmature / BoneProxy / MenuInstaller / MergeAnimator / Parameters | Beta, Unity live previews pass |
| Integer-parameter wardrobe scan/create/add/manage tools | int wardrobe scan/create/add/remove/rename/reorder/default/delete | Beta, local tests pass; Unity live scan/preview smoke passed |
| Outfit-part writer | Add an int-gated accessory toggle to one wardrobe outfit | Beta, Unity live preview and rollback smoke pass |
| Semantic add-outfit workflow | Prefab search -> instantiate -> Setup Outfit -> scan/create wardrobe if missing -> wardrobe binding | Beta, local tests pass; candidate wardrobe auto-selection guarded |
| Pre-write checkpoint timeline | Git or archive checkpoint before gateway and legacy REST writes, incremental preview/restore UI | Beta, Unity live write/restore smoke passed |
| Face/shader adjustment checkpoint timeline | Manual and automatic face/shader checkpoints with create/read/update/delete, overwrite, A/B selection, preview, and apply through restore approval | Current source line, approval/checkpoint required |
| External agent connector templates and smoke | Built-in and custom JSON/TOML MCP config installation without plaintext tokens, plus supervised write/rollback smoke | 已可用 / Available |
| `.vsk` community skill packages | Signed import/preflight/trust, atomic projection/governance, Path-to-Skill capture, SDK scaffold/lock validation, and searchable package/runtime audit | Available; packaged lifecycle acceptance passed |
| Parameter usage checks and suggestions | 参数占用检查与建议 | 已可用 / Available |
| Screenshot and multi-view analysis | 截图分析与多视角检查 | 已可用 / Available |
| Avatar composition workflows | 带面捕/无面捕换头与来源模型部件移植；只编排现有原子工具、检查点、读回和 GM 多视角验收 | Available |

Wardrobe scan results are split deliberately:
`wardrobes` are high-confidence FX Animator wardrobes that the add-outfit
workflow may use automatically, `wardrobeCandidates` need an explicit
`parameterName`, and `looseControls` are ordinary accessory/clothing-off toggles
that are never treated as wardrobes automatically.

## External Agent Connectors

连接外部 MCP Agent 不需要先配置 VRCForge 内置 AI。

1. 打开 Unity 工程，在 VRCForge 中选中它，确认已连接。
2. 打开「设置 → 连接器」，开启「连接外部 Agent」。
3. 选择你使用的客户端，点击「安装 MCP 配置」。未列出的客户端可用「复制通用连接说明」，交给 Agent 按说明设置。
4. 保持 VRCForge 和 Unity 开着，重启或重新连接客户端，然后让 Agent “检查当前 Avatar，先不要修改”。能读到当前模型才说明连接可用。
5. 需要改模时，打开「允许修改项目」，说明目标和要改的内容，按提示确认。修改后检查效果；恢复检查点也需要确认。

You do not need to configure VRCForge's built-in AI to use an external MCP agent.

1. Open your Unity project, select it in VRCForge, and check that it is connected.
2. Open **Settings → Connectors** and turn on **Connect external Agents**.
3. Select your client and click **Install MCP config**. For other clients, use **Copy general connection instructions** and give them to your agent.
4. Keep VRCForge and Unity open. Restart or reconnect the client, then ask it to “Inspect the current avatar without changing anything.” Reading the current avatar confirms the connection works.
5. When you want to edit, turn on **Allow project changes**, describe the target and changes, and confirm when prompted. Check the result afterward. Restoring a checkpoint also requires confirmation.

<details>
<summary>Advanced connection settings and developer tests / 高级连接设置与开发测试</summary>

Settings > Agent Connectors can generate loopback MCP config for local external
agent clients. Use the HTTP config when VRCForge Desktop is already running.
Use the stdio bridge config when the client needs a local token-free bridge;
generated stdio configs include `--no-start` and require VRCForge to already be
running instead of launching the desktop app from the MCP client. Installed
builds use the packaged backend exe for stdio; source checkouts use the Python
bridge script. Copied configs use environment variables or the local VRCForge
user-data config; plaintext gateway tokens are not written into client config.

The external-agent success path is:

```text
MCP read/plan -> execution exposure (Gateway config external_mcp_exposure_layer=execution) -> supervised write Tool call -> VRCForge approval -> checkpoint -> apply -> validation -> restore request -> VRCForge approval -> rollback proof
```

`vrcforge_request_apply` and the other wrapper Tools are internal to the
VRCForge App agent loop and are never advertised to external clients. External
stdio sessions start in the planning layer; moving them to execution is a
Gateway config decision (`external_mcp_exposure_layer` in the Gateway config
file, or an explicit `--exposure-layer` / `VRCFORGE_MCP_EXPOSURE_LAYER` pin at
bridge launch). Exposure only changes Tool visibility: every write still
requires VRCForge approval and a checkpoint.

Developers and release testers can run:

```powershell
npm run smoke:external-agent
npm run smoke:external-agent:live -- --project-root C:\path\to\UnityProject --parent-path Avatar
```

A passing live smoke must show that `vrcforge_request_apply` stays hidden,
direct apply wrappers are hidden, supervised write Tools are reachable through
the execution catalogue, a checkpoint was created, rollback completed,
the temporary object is gone, and Unity compile errors stayed at zero.
The preflight smoke does not write Unity; the live smoke does.

</details>

## Stable Workflow

先备份工程，让 AI 查看模型，再提出一项小范围修改。确认目标和方案后应用，回到 Unity 检查效果。不满意时可选择相应检查点并确认恢复；检查点不能替代工程备份。下面的功能按需使用，不必全部跑一遍才能开始。

Back up your project, ask the AI to inspect your avatar, then request one small change. Review the target and plan before applying it, and check the result in Unity. If needed, select the relevant checkpoint and confirm the restore. Checkpoints do not replace backups. Use the features below as needed; they are not a mandatory first-run checklist.

### Provider Modes

- BYOK cloud provider: enter your own provider key in Settings, then run the
  built-in provider text/JSON/vision-safe tests before relying on AI-assisted
  writes.
- Local provider / Ollama: configure the local endpoint and model, then use the
  same provider test surface. Local models may be text-only depending on the
  selected model.
- Custom OpenAI-compatible endpoint: configure the base URL, model, and key or
  local token required by that endpoint.
- No-provider / manual read-only: VRCForge can still open, connect Unity, run
  Doctor, scan projects, run validation, show checkpoints, import `.vsk`
  packages, and create supervised non-LLM plans where deterministic tools are
  available. AI-generated chat plans and vision reasoning require a provider.

### First Validation Report

1. Open the Unity project and wait for compilation to finish.
2. In VRCForge, select the project and avatar.
3. Run Doctor if the Unity or SDK status is unclear.
4. Run the Validation Report from the desktop validation/readiness surface or
   use the CLI `validation run` command from the same runtime.
5. Review compile, SDK, parameter, menu, FX, material, wardrobe, performance,
   and generated-residue sections before requesting a write.

### First Rollback

When a write needs approval, its card replaces only the conversation composer;
chat history remains visible. The main button allows once, the chevron contains
the eligible future-category choice, and reject makes no change. Windows
notifications are an additional VRCForge-branded decision surface; the App card
remains the authoritative place to inspect details. Restore always requires a
separate approval.

1. Apply only one small supervised change.
2. Confirm the approval card created a checkpoint id.
3. Open Checkpoints, choose the newest checkpoint, and preview the changed,
   added, and deleted files.
4. Request restore, approve it, then rerun validation.
5. Treat missing checkpoint ids, failed restore, or residue after restore as a
   release blocker for that workflow.

### Face/Shader A/B Adjustments

1. Open Checkpoints and use the Adjustment Timeline for frequent face or shader tuning snapshots.
2. Face and shader writes are indexed automatically; use Face or Shader to create a manual current-state snapshot before experimenting.
3. Rename or delete entries as needed, or overwrite an entry with the current project state.
4. Select two entries as A and B, preview either entry, then Apply the one you want to test through the same restore approval path.
5. After applying an adjustment checkpoint, rerun validation or visual review before continuing.

### Booth Outfit Import

1. Inspect the `.unitypackage`, Booth ZIP/folder, or loose prefab folder first.
2. Review dependency preflight, especially shader support packages such as
   lilToon or Poiyomi that may already be installed.
3. Keep paid asset binaries local. Do not paste FBX, textures, materials, or
   Booth package contents into model prompts, support issues, or `.vsk` output.
4. Request the supervised import only after reviewing the plan.
5. Validate, inspect screenshots when relevant, then roll back if the result is
   not what you expected.
6. Post-import material validation runs automatically. If any imported material
   has a missing or broken shader reference (the magenta / pink render in
   Unity), validation raises a blocking `Error` under "Materials / shaders" and
   lists the affected materials and renderers. The fix is to import the shader
   support package (lilToon, Poiyomi, or the outfit's required shader) before
   the outfit prefab, then re-run the import; magenta means the shader was not
   present when the material loaded, not that the mesh is broken.

### Safe Model Optimization

1. Start with a conservative profile such as PC Conservative.
2. Run the optimization plan and review upload blockers separately from
   performance-rank offenders.
3. Request only one optimizer step at a time.
4. Confirm approval, checkpoint, validation delta, screenshots when available,
   and rollback proof.
5. Leave VRCFury compressor, hidden body cut, aggressive Meshia, and one-click
   Quest optimization in Advanced/Experimental paths until proof exists for the
   specific avatar.

### Skill Packages and Path-to-Skill

1. Import `.vsk` packages through dry-run/preflight first.
2. Keep Safe Mode enabled for medium/high-risk packages until you understand the
   permission request.
3. A valid signature means integrity and signer continuity, not "verified safe".
4. Signed updates must keep the package ID, author identity, and Ed25519 signer;
   downgrades and changed payloads under the same semantic version are rejected.
   The signing private key is never part of the `.vsk` or installed package.
5. Use trust signer, revoke signer, package block, disable, and uninstall when
   reviewing community skills.
6. Path-to-Skill exports should contain variables, detector rules, validation
   gates, and rollback requirements, not private absolute paths, paid assets,
   API keys, or gateway tokens.

### Support Bundle Flow

1. Run Doctor.
2. Export the support bundle from Doctor.
3. Review the bundle locally.
4. Remove API keys, gateway tokens, paid asset contents, private files, and
   any screenshots you do not want to share.
5. Open a GitHub issue and upload or paste the reviewed bundle manually.

### Local Logs and Developer Options

- Logging controls remain available in normal Settings even when Developer
  Options are off. Error / Warn / Info / Debug take effect immediately; Trace
  appears only while Developer Options are enabled and automatically falls
  back to Debug when Developer Options are turned off.
- Error is intentionally non-empty but sparse: it keeps startup/shutdown safety
  posture, high-risk permission-flag changes, and real failures. Warn adds
  recoverable/client-side failures, Info adds user actions and key stages,
  Debug adds user-triggered request/decision summaries, and Trace adds internal
  handoffs, reads, and polling.
- VRCForge writes UTF-8 text logs named from their start time, keeps at most
  five days, and also enforces file-count and size limits. Settings can open
  the log folder directly.
- Paths, credentials, network identifiers, and Avatar Blueprint IDs are
  replaced before an event reaches disk. Stable local aliases let Settings
  identify the related Windows user, Unity project, and avatar without placing
  the private alias map or its key in logs or support bundles.
- Developer Options control additional high-risk diagnostic capabilities. On
  enable, the risk dialog keeps Cancel available immediately and keeps Confirm
  disabled for a server-enforced five-second warning period.
- Support bundles include the current permission/developer/Computer Use safety
  posture without challenge values, credentials, or private identity mappings.
  `ipc` and `direct_http` log labels describe the observed desktop request path
  for troubleshooting; they are diagnostic provenance, not an authorization
  or operating-system identity boundary.

## Masked texture edits

The texture patch tool can use an optional same-size grayscale PNG mask from
the Unity project's `Assets` folder. White pixels receive the requested color,
black or transparent pixels stay unchanged, and gray pixels blend proportionally.
The source texture's alpha is preserved. Colored masks and mismatched sizes are
rejected.

Preview the edit first, then approve it to write a new PNG. The source is never
overwritten. Changing the source, mask, or edit settings invalidates the preview;
preview again before approving. The result reports the actual changed pixel count
and bounds after reading the saved output back.

## Reviewing a support report

In Settings diagnostics, describe the problem and generate a local redacted
preview. You can also preview the newest support bundle; long member excerpts
are marked as truncated, so inspect the full bundle before sharing it. Reading
the preview does not mark the bundle as reviewed. Confirm review yourself, copy
the draft, and manually attach the intended bundle on the feedback page.
VRCForge does not upload the report or attachment automatically.

Japanese setup, troubleshooting, and support boundaries are described in
[日本語サポートガイド](docs/SUPPORT_JA.md).

## Privacy Boundary

VRCForge keeps avatar assets local by default. API key values, gateway token
values, paid asset payloads, Booth package contents, FBX files, textures,
material binaries, and private files should not be sent to model context,
external agents, support bundles, or `.vsk export` packages. Validation
metadata, project structure, screenshots, and Unity logs are user-controlled.
Local application logs and exported support-bundle excerpts are redacted by
default, but should still be reviewed before sharing. The private diagnostic
alias key and identity map stay local and are never bundled.

## Requirements / 运行环境

- Windows
- Unity 2022.3 LTS
- VRChat SDK3 Avatar project
- Windows x64 installer users do not need Python, Git, uv, or manual `pip install`
- Source/debug users need Python, Node.js, Rust/Tauri tooling, and the package dependencies
- The matching `VRCForge.unitypackage` from the same release as the App
- An LLM provider account for AI-assisted features
- Full dependency list: [DEPENDENCIES.md](DEPENDENCIES.md)

You can import VRCForge into an empty Unity project. If the Avatar SDK is
missing, VRCForge shows its setup window while keeping the project compilable.
Install the official VRChat Avatar SDK through VCC or ALCOM, including its
dependencies, and wait for Unity to finish compiling. Reopen the check from
`VRCForge > Setup > Check Dependencies` if needed. Avatar tools become available
after the dependencies are installed.

To uninstall the Unity integration, choose `VRCForge > Uninstall VRCForge...`
inside the project and confirm. This stops the project-owned MCP Core, clears
its versioned auto-connect EditorPrefs key, and removes `Assets/VRCForge`.
Deletion failure is reported without a second destructive retry. This command
does not delete desktop user data.

`1.4.0` is a breaking install boundary. Do not overwrite-install the App or
import its Unity package over `1.3.6`. Close VRCForge and Unity, remove the old
App/runtime and old project integration, then install and import `1.4.0` fresh.
Preserve `%LOCALAPPDATA%\VRCForge\agentic-app`, configured API keys, user-owned
`AGENTS.md`, chats, memories, checkpoints, and unrelated Unity project assets.

- Windows
- Unity 2022.3 LTS
- VRChat SDK3 Avatar 工程
- Windows x64 安装器用户不需要 Python、Git、uv 或手动 `pip install`
- 源码/调试用户需要 Python、Node.js、Rust/Tauri 工具链和项目依赖
- 与 App 来自同一 Release 的 `VRCForge.unitypackage`
- 用于 AI 辅助功能的模型服务账号
- 完整依赖清单：[DEPENDENCIES.md](DEPENDENCIES.md)

空 Unity 工程可以先导入 VRCForge。缺少 Avatar SDK 时，工程仍可正常编译，
VRCForge 设置窗口会显示安装提示。通过 VCC 或 ALCOM 安装官方 VRChat Avatar SDK
及其依赖，等待 Unity 编译完成后即可启用 Avatar 工具。可通过
`VRCForge > Setup > Check Dependencies` 重新检查。

## Start / 启动

Follow the [first-run guide](README.en.md#first-run) to install both parts, connect your Unity project, and choose built-in AI or an external MCP agent.

按 [首次使用指南](README.md#第一次使用) 安装 App 和 Unity 插件，连接工程，再选择内置 AI 或外部 MCP Agent。

## Basic Workflow / 基本流程

1. Open the Unity project and wait for compilation to finish.
2. Confirm that the desktop app shows Unity as connected.
3. Load the target Avatar.
4. Load facial Blendshapes.
5. Make a small manual slider change and verify that undo works.
6. Enter a conservative natural-language instruction.
7. Generate a reviewable AI Blendshape plan.
8. Review Blendshape name, before value, after value, and delta.
9. Apply the plan only after reviewing it.
10. Restore if the result is not useful.
11. Save useful results as presets and reapply them later.
12. Lock Blendshapes you want to keep before generating another candidate.

1. 打开 Unity 工程并等待编译完成。
2. 确认桌面 app 显示 Unity 已连接。
3. 加载目标 Avatar。
4. 加载脸部 Blendshape。
5. 先做一次小幅手动滑块调整，并确认撤销可用。
6. 输入保守的自然语言指令。
7. 生成可审阅的 AI Blendshape 调整方案。
8. 检查 Blendshape 名称、调整前数值、调整后数值和变化量。
9. 审阅后再应用方案。
10. 结果不合适时使用恢复。
11. 满意的结果保存为预设，之后可以重新应用。
12. 重新生成候选结果前，可以锁定想保留的 Blendshape。

## AI Face Tuning History and Presets / AI 捏脸历史与预设

Generated AI Blendshape plans are normally stored in local history when the
history write succeeds. Saved records can be reviewed, reapplied, or converted
to named presets. Presets store the recorded target values, so applying one
sets the Blendshapes to that saved result instead of repeatedly stacking deltas.

AI 生成的 Blendshape 方案在本地历史写入成功时通常会保存。已保存的记录可以查看、重放，
也可以转换为命名预设。预设保存记录的目标值，因此之后应用时会回到该结果，而不是反复叠加变化量。

Use locks when you want to keep part of a good result. Locked Blendshapes are hidden from new AI planning and blocked during apply, so later generations only affect unlocked Blendshapes.

如果想保留某一部分满意结果，可以使用锁定。锁定后的 Blendshape 会从新一轮 AI 规划中排除，并在应用时被拦截，因此后续候选结果只会影响未锁定项目。

## Desktop Areas / 桌面区域

| Area | Purpose | 区域 | 作用 |
| --- | --- | --- | --- |
| Connection status | Shows Unity, provider, model, Avatar, and socket state | 连接状态 | 显示 Unity、模型服务、模型、Avatar 和连接状态 |
| Project and Avatar | Selects the Unity project and current Avatar | 工程与 Avatar | 选择 Unity 工程和当前 Avatar |
| Provider | Configures the AI provider and model | 模型服务 | 配置 AI provider 和模型 |
| Blendshape editor | Edits facial Blendshapes manually or with AI | Blendshape 编辑 | 手动或通过 AI 调整脸部 Blendshape |
| Wardrobe FX | Builds wardrobe toggle assets | 衣柜 FX | 生成衣柜开关相关资产 |
| Parameters | Reviews Expression Parameter usage | 参数 | 查看表达参数占用 |
| Screenshots | Captures and reviews avatar screenshots | 截图 | 捕获并查看 Avatar 截图 |
| Checkpoints | Lists pre-write checkpoints and requests restore | Checkpoints | 查看写入前快照并申请回退 |
| Protection | Chooses a protection level and previews its impact before approval | 保护 | 选择保护级别并在确认前查看影响 |
| Skills | Finds, checks, and configures Agent capabilities | 能力 | 查找、检查和配置 Agent 能力 |
| Connection diagnostics | Shows connection results and failure reasons | 连接诊断 | 显示连接结果和失败原因 |

Startup update checking runs in the background and stays silent unless a newer
release exists. The tray **Check for updates** action always reports one result:
update available, already current, or check failed. The update dialog can turn
off future automatic checks. That choice, themes, custom colours and background
images are personal settings and survive App upgrades.

启动检查更新在后台进行，只有发现新版才提示。托盘的「检查更新」一定会显示
一种结果：有新版、已是最新版或检查失败。更新弹窗可关闭以后自动检查；这个
选择以及主题、自选颜色和背景图都会在 App 更新后保留。

Checkpoint Storage labels protected archives as **Latest retained** or
**Recovery in progress**, so the reason they cannot be deleted is visible.
Checkpoint 存储页会分别标出「最新保留」和「恢复中保留」，直接说明为何不能删除。

## Agent Workspace / Agent 工作台

The desktop app provides an agent workspace with a project sidebar and multiple chat sessions. Chats are saved locally and survive restarts; when you continue an old chat, the full transcript is replayed to the backend so context is preserved.

桌面应用提供完整的 Agent 工作台：侧栏按项目/临时对话分组，支持多会话切换。会话保存在本地，重启后仍在；继续旧会话时会把完整前文回放给后端，上下文不丢失。

First launch shows a step-by-step setup wizard (core connection → model provider → Unity project) with a progress bar; each step is detected automatically and can be skipped. The sidebar offers a temporary chat and a new-project picker; a project can be chosen from the scanned list or added by typing its folder path, and project rows support right-click collapse, hide, and remove.

首次启动会出现分步设置向导（核心连接 → 模型供应商 → Unity 项目），带进度条；每一步自动检测完成状态，也可以随时跳过。侧栏顶部提供「临时对话」和「新项目」；项目可以从扫描列表选择，也可以手动填入文件夹路径添加；项目行支持右键折叠、隐藏或移除。

Agent replies are chat bubbles that state explicitly what the agent will do next. Command and skill executions appear as collapsed rows that expand to show the full command, output, and duration, and each turn shows its total running time. Messages typed while the agent is busy are queued and sent in order, so an ongoing task can be steered. Selecting text in a reply shows a floating toolbar to copy it, quote it into the composer, or ask about it in a new session.

When a Unity project is selected, the workspace can maintain a local project index and show a compact change strip for added, modified, and deleted files. The index covers `Assets`, `Packages`, and `ProjectSettings`; it returns structural paths, sizes, hashes, package fingerprints, and affected scanner-family hints, not paid asset binary contents.

Agent 回复以对话气泡呈现，并显式说明接下来要做什么；命令与能力执行显示为可展开的折叠行（完整命令、输出、耗时），每轮对话都显示总运行时长。Agent 执行中输入的消息会自动排队、按顺序发送，可用来中途引导任务。选中回复中的文字会弹出浮动工具条：复制、引用到输入框，或在新会话中提问。

Composer commands / 输入框命令：

- Type `/` to open the command autocomplete menu. 输入 `/` 弹出命令补全菜单。
- `/compact` compresses the current chat history into a summary to free context. It prefers an LLM-generated summary and falls back to a local digest when the model is unavailable. `/compact` 会把当前会话历史压缩成摘要以释放上下文；优先使用模型生成摘要，模型不可用时回退本地摘要。
- `/<skill-name> [args]` invokes an enabled skill directly. `/<skill名> [参数]` 可直接调用已启用的 skill。

## Provider Notes / 模型服务说明

VRCForge supports Google AI Studio, OpenAI, Anthropic, Ollama, Google Vertex AI, DeepSeek, OpenRouter, and custom OpenAI-compatible endpoints.
VRCForge 支持 Google AI Studio、OpenAI、Anthropic、Ollama、Google Vertex AI、DeepSeek、OpenRouter 和自定义 OpenAI-compatible endpoint。

When a provider returns visible reasoning, thinking, or thought-summary fields, VRCForge passes them through to the chat response as a collapsed row. This includes fields such as DeepSeek `reasoning_content`, OpenRouter `reasoning_details`, Anthropic `thinking` blocks, Gemini thought summaries, and Ollama-style `thinking` fields when the selected model returns them. Opaque or encrypted reasoning continuity items are marked as opaque rather than displayed as plaintext.

For face editing, original/current images and target reference images are both optional. Each group supports pasted images, local image selection, typed image paths, the latest Unity screenshot, or a new Unity screenshot captured from the desktop app. Added images show as removable previews.
捏脸时，原图/当前脸和目标参考图都可以不传。每组都支持粘贴图片、选择本地图片、手填图片路径、使用最近 Unity 截图，或从桌面 app 直接捕获新的 Unity 截图。加入后的图片会显示为可单独删除的预览。

Image input depends on the selected provider and model. If image input is not supported, VRCForge reports the provider error.
图片输入能力取决于所选 provider 和模型。如果模型不支持图片输入，VRCForge 会显示对应错误。

## Safety / 安全建议

- Back up the avatar project before writing Unity assets.
- Start with small Blendshape changes.
- Use dry-run or preview modes when available.
- Stop and check Unity Console if Unity reports compile errors.
- Review generated changes before applying them to important projects.
- Gateway writes and legacy desktop REST write endpoints save Unity state and create a checkpoint first. Git projects use git-backed checkpoints; non-git projects use a compressed local baseline. Restore applies only the incremental file diff and then reloads Unity scenes/assets.
- Raw Unity MCP writes made outside VRCForge cannot be intercepted. Use the supervised gateway or desktop write path when checkpoint rollback is required.

- 写入 Unity 资产前先备份 Avatar 工程。
- 第一次调整使用小幅 Blendshape 改动。
- 有 dry-run 或预览模式时优先使用。
- Unity 出现编译错误时先停止操作并检查 Console。
- 对重要工程执行前先查看生成的改动内容。

## Validation / 验证

```powershell
python -m py_compile dashboard_server.py vrchat_blendshape_agent.py
python -m pytest tests -q
npx tsc --noEmit
npm run build
npm run smoke:agentic
```

## Troubleshooting / 常见问题

| Problem | Check | 问题 | 检查 |
| --- | --- | --- | --- |
| Unity is not connected | The selected project is open, compiled without errors, and VRCForge MCP Core reports Ready | Unity 未连接 | 选中的工程是否已打开、无错误编译，且 VRCForge MCP Core 是否显示 Ready |
| Avatar list is empty | The scene contains a VRChat Avatar descriptor | Avatar 列表为空 | 场景中是否存在 VRChat Avatar descriptor |
| Blendshapes do not load | The selected Avatar has SkinnedMeshRenderer Blendshapes | Blendshape 加载失败 | 选中的 Avatar 是否包含 Blendshape |
| AI planning fails | Provider, model, and credentials are valid | AI 规划失败 | Provider、模型和凭据是否有效 |
| Screenshot analysis is inaccurate | SceneView framing is clear before capture | 截图分析不准确 | 截图前 SceneView 构图是否清晰 |

## Contributing / 贡献

Issues and pull requests are welcome.
欢迎提交 issue 和 pull request。
