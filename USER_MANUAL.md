# VRCForge User Manual / 使用手册

VRCForge is a local desktop agent workspace for VRChat avatar editing.
VRCForge 是一个面向 VRChat Avatar 编辑的本地桌面 Agent 工作区。

This manual explains the public workflow and feature status without project-specific paths or private configuration details.

Current source version: `1.0.1`. Latest published stable release:
`1.0.0`. The 1.0.1 source line starts a read-only Avatar Encryption /
Anti-Rip addon preview for lilToon and Poiyomi. It does not expose
apply/remove writes yet.
本手册只说明公开使用流程和功能状态，不包含项目私有路径或本地配置细节。

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
| Avatar Encryption / Anti-Rip addon preview | lilToon and Poiyomi shader-encryption research/scan/plan/preview; other shader families compatibility-only | 1.0.1 read-only preview, no apply/remove writes |
| Agent workspace with multi-chat sessions | Agent 工作台与多会话 | 已可用 / Available |
| First-run resilient normal-agent fallback | Optional Unity/skill/project failures do not block ordinary agent chat | 已可用 / Available |
| Chat persistence and history replay | 会话持久化与历史回放 | 已可用 / Available |
| `/compact` history compaction | `/compact` 历史压缩 | 已可用 / Available |
| Slash-command skill invocation | 斜杠命令直接调用 skill | 已可用 / Available |
| Steering queue and run visualization | 插队队列与运行可视化 | 已可用 / Available |
| Provider reasoning/thinking trace | API-returned visible reasoning, thinking, or thought-summary items appear as default-collapsed chat rows | 已可用 / Available |
| Project memory / incremental scan | Local index shows added, modified, and deleted project files in the desktop workspace | 已可用 / Available |
| Outfit package import planning | Inspect `.unitypackage`, Booth folders, and loose prefab/texture folders, then request a supervised import; VRCForge's fallback Unity package supports fresh-project direct import | Beta, approval/checkpoint required |
| Package/plugin install diagnostics | Read VPM/ALCOM/vrc-get status, install output, and compile-error context before planning repairs | Beta, read-only diagnostics |
| Delegated sub-agent workers | Independent read-only/plan workers with lifecycle state, cancel/retry/inspect, redacted logs, and parent-thread summaries | Beta |
| Tool Registry v1 | Standard tool metadata exposed to desktop, MCP/gateway, and CLI surfaces | Available |
| CLI diagnostics and readiness | Packaged `vrcforge` CLI covers doctor, provider test, Unity/project/avatar scans, validation, Build/Test readiness, checkpoints, skill/tool registry, and request-based apply/rollback | Available |
| Full Validator and Build/Test readiness | `vrcforge.validation.v1` plus Build/Test readiness gates for compile, SDK/plugin/package, expression, animator, material, performance, and generated-residue findings | Available |
| Model Optimization Planner | Read-only optimization dashboard, baseline scan, target profiles, dependency doctor, VRAM/material/mesh/parameter audits, and step-by-step plan output | Available |
| Optimizer proof release | LAC, AAO, TTT, MA2BT-Pro, and Meshia use named apply-request tools with approval, checkpoint, validation delta, rollback proof, and screenshot evidence; VRCFury risky writers return blocked previews until validated | Beta, approval/checkpoint required |
| Before/after screenshot capture and vision review | 执行前后截图与视觉复核 | 已可用 / Available |
| Modular Avatar and VRCFury read-only scans | Modular Avatar / VRCFury 只读扫描 | 已可用 / Available |
| Outfit setup wrapper and VPM package status/install | Outfit 安装封装与 VPM 包状态/安装 | 已可用 / Available |
| Avatar performance scan | Avatar 性能扫描 | 已可用 / Available |
| Unity compile-error reading | Unity 编译错误读取 | 已可用 / Available |
| Roslyn Advanced Power Mode | Roslyn 高级模式 | 已可用 / Available |
| Generic Unity CRUD tools | 通用 Unity CRUD 工具 | Beta, local tests pass |
| Generic avatar authoring primitives | Expression parameters / menu controls / FX animator states | Beta, local tests pass; preview path covered by wardrobe workflow |
| Modular Avatar component writer | MergeArmature / BoneProxy / MenuInstaller / MergeAnimator / Parameters | Beta, Unity live previews pass |
| Int-exclusive wardrobe scan/create/add/manage tools | int wardrobe scan/create/add/remove/rename/reorder/default/delete | Beta, local tests pass; Unity live scan/preview smoke passed |
| Outfit-part writer | Add an int-gated accessory toggle to one wardrobe outfit | Beta, Unity live preview and rollback smoke pass |
| Semantic add-outfit workflow | Prefab search -> instantiate -> Setup Outfit -> scan/create wardrobe if missing -> wardrobe binding | Beta, local tests pass; candidate wardrobe auto-selection guarded |
| Pre-write checkpoint timeline | Git or archive checkpoint before gateway and legacy REST writes, incremental preview/restore UI | Beta, Unity live write/restore smoke passed |
| Face/shader adjustment checkpoint timeline | Manual and automatic face/shader checkpoints with create/read/update/delete, overwrite, A/B selection, preview, and apply through restore approval | 1.0.1 source line, approval/checkpoint required |
| External agent connector templates and smoke | HTTP + stdio MCP snippets without plaintext tokens, plus supervised write/rollback smoke | 已可用 / Available |
| `.vsk` community skill packages | Import/export/verify shareable skill packages | 已可用 / Available |
| Parameter usage checks and suggestions | 参数占用检查与建议 | 已可用 / Available |
| Screenshot and multi-view analysis | 截图分析与多视角检查 | 已可用 / Available |
| Batch workflows | 批量工作流 | 计划中 / Planned |

Wardrobe scan results are split deliberately:
`wardrobes` are high-confidence FX Animator wardrobes that the add-outfit
workflow may use automatically, `wardrobeCandidates` need an explicit
`parameterName`, and `looseControls` are ordinary accessory/clothing-off toggles
that are never treated as wardrobes automatically.

## External Agent Connectors

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
MCP read/plan -> vrcforge_request_apply -> VRCForge approval -> checkpoint -> apply -> validation -> restore request -> VRCForge approval -> rollback proof
```

Developers and release testers can run:

```powershell
npm run smoke:external-agent
npm run smoke:external-agent:live -- --project-root C:\path\to\UnityProject
```

A passing live smoke must show that `vrcforge_request_apply` is available,
direct apply tools are hidden, a checkpoint was created, rollback completed,
the temporary object is gone, and Unity compile errors stayed at zero.
The preflight smoke does not write Unity; the live smoke does.

## 1.0 Stable Workflow

1.0.0 is organized around stable proof instead of feature promises. Use it when
you want to verify a whole workflow and keep the evidence needed for support.

The 1.0 stable checklist is:

1. Install and first run.
2. Connect Unity.
3. Configure Provider / BYOK / local-only / no-provider mode.
4. Run Doctor.
5. Create the first validation report.
6. Perform the first rollback.
7. Plan and supervise one Booth outfit workflow.
8. Run one safe model optimization step.
9. Use external agents only through read/plan/write-request.
10. Import, dry-run, disable, uninstall, and optionally export a `.vsk`.
11. Export a support bundle and attach it manually to an issue after review.

Recommended flow:

1. Run Doctor and fix any startup, Unity, provider, or gateway blockers.
2. Run the Golden Path Matrix or follow its same order manually: connect Unity,
   scan the avatar, run validation, request one supervised edit, review the
   checkpoint, apply, validate, and roll back.
3. Open the proof viewer for the run and review screenshots, validation deltas,
   checkpoint ids, rollback status, and any skipped rows.
4. Export a Doctor support bundle if the run fails or behaves unexpectedly.
5. Open a GitHub issue and upload or paste the relevant support bundle artifact.
   VRCForge does not attach the bundle automatically; review it first and
   remove secrets, tokens, paid asset contents, and private files.

The release evidence and proof matrix are the final acceptance record. Any row
marked pending needs a real artifact path, size, hash, or explicit not-run /
blocked reason before it can be treated as stable.

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
4. Use trust signer, revoke signer, package block, disable, and uninstall when
   reviewing community skills.
5. Path-to-Skill exports should contain variables, detector rules, validation
   gates, and rollback requirements, not private absolute paths, paid assets,
   API keys, or gateway tokens.

### Support Bundle Flow

1. Run Doctor.
2. Export the support bundle from Doctor.
3. Review the bundle locally.
4. Remove API keys, gateway tokens, paid asset contents, private files, and
   any screenshots you do not want to share.
5. Open a GitHub issue and upload or paste the reviewed bundle manually.

## Privacy Boundary

VRCForge keeps avatar assets local by default. API key values, gateway token
values, paid asset payloads, Booth package contents, FBX files, textures,
material binaries, and private files should not be sent to model context,
external agents, support bundles, or `.vsk export` packages. Validation
metadata, project structure, screenshots, and Unity logs are user-controlled
and should be redacted or reviewed before sharing.

## Requirements / 运行环境

- Windows
- Unity 2022.3 LTS
- VRChat SDK3 Avatar project
- Windows x64 installer users do not need Python, Git, uv, or manual `pip install`
- Source/debug users need Python, Node.js, Rust/Tauri tooling, and the package dependencies
- MCP for Unity, installed automatically into the selected Unity project by VRCForge
- An LLM provider account for AI-assisted features
- Full dependency list: [DEPENDENCIES.md](DEPENDENCIES.md)

- Windows
- Unity 2022.3 LTS
- VRChat SDK3 Avatar 工程
- Windows x64 安装器用户不需要 Python、Git、uv 或手动 `pip install`
- 源码/调试用户需要 Python、Node.js、Rust/Tauri 工具链和项目依赖
- MCP for Unity，由 VRCForge 自动安装到选中的 Unity 工程
- 用于 AI 辅助功能的模型服务账号
- 完整依赖清单：[DEPENDENCIES.md](DEPENDENCIES.md)

## Start / 启动

1. Install VRCForge from the Windows x64 installer.
2. Start `VRCForge.exe` from the desktop or Start Menu.
3. Complete the first-run setup: core connection, provider/model, and Unity project.
4. Open the avatar project in Unity and let compilation finish.
5. Select the project/chat in VRCForge, then use agent messages or slash skills.

If Unity discovery, project scanning, skill loading, or user-data `AGENTS.md` setup fails on first launch, the desktop should still open as a normal agent workspace when the backend is online. Fix the shown setup action before using Unity-writing features.

1. 使用 Windows x64 安装器安装 VRCForge。
2. 从桌面或开始菜单启动 `VRCForge.exe`。
3. 完成首次引导：核心连接、模型/provider、Unity 工程。
4. 在 Unity 中打开 Avatar 工程并等待编译完成。
5. 在 VRCForge 中选择项目/会话，然后使用 agent 消息或斜杠 skill。

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

Every generated AI Blendshape plan is saved to history. History records can be reviewed, reapplied, or saved as named presets. Presets store the saved after values, so applying a preset later sets the Blendshapes back to the recorded result instead of repeatedly stacking deltas.

每次 AI 生成的 Blendshape 方案都会进入历史记录。历史记录可以查看、重放，也可以保存为命名预设。预设保存的是调整后的目标值，因此之后应用预设时会回到记录的结果，而不是反复叠加变化量。

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
| Connection diagnostics | Shows connection results and failure reasons | 连接诊断 | 显示连接结果和失败原因 |

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
| Unity is not connected | Unity is open, compiled, and MCP for Unity is running | Unity 未连接 | Unity 是否打开、编译完成、MCP for Unity 是否运行 |
| Avatar list is empty | The scene contains a VRChat Avatar descriptor | Avatar 列表为空 | 场景中是否存在 VRChat Avatar descriptor |
| Blendshapes do not load | The selected Avatar has SkinnedMeshRenderer Blendshapes | Blendshape 加载失败 | 选中的 Avatar 是否包含 Blendshape |
| AI planning fails | Provider, model, and credentials are valid | AI 规划失败 | Provider、模型和凭据是否有效 |
| Screenshot analysis is inaccurate | SceneView framing is clear before capture | 截图分析不准确 | 截图前 SceneView 构图是否清晰 |

## Contributing / 贡献

Issues and pull requests are welcome.
欢迎提交 issue 和 pull request。
