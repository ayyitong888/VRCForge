# VRCForge User Manual / 使用手册

VRCForge is a local desktop agent workspace for VRChat avatar editing.
VRCForge 是一个面向 VRChat Avatar 编辑的本地桌面 Agent 工作区。

This manual explains the public workflow and feature status without project-specific paths or private configuration details.
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
| Agent workspace with multi-chat sessions | Agent 工作台与多会话 | 已可用 / Available |
| Chat persistence and history replay | 会话持久化与历史回放 | 已可用 / Available |
| `/compact` history compaction | `/compact` 历史压缩 | 已可用 / Available |
| Slash-command skill invocation | 斜杠命令直接调用 skill | 已可用 / Available |
| Steering queue and run visualization | 插队队列与运行可视化 | 已可用 / Available |
| Before/after screenshot capture and vision review | 执行前后截图与视觉复核 | 已可用 / Available |
| Modular Avatar and VRCFury read-only scans | Modular Avatar / VRCFury 只读扫描 | 已可用 / Available |
| Outfit setup wrapper and VPM package status/install | Outfit 安装封装与 VPM 包状态/安装 | 已可用 / Available |
| Avatar performance scan | Avatar 性能扫描 | 已可用 / Available |
| Unity compile-error reading | Unity 编译错误读取 | 已可用 / Available |
| Roslyn Advanced Power Mode | Roslyn 高级模式 | 已可用 / Available |
| Generic Unity CRUD tools | 通用 Unity CRUD 工具 | 开发分支 / Development branch |
| Int-exclusive wardrobe scan and existing-wardrobe add-outfit tools | int 互斥衣柜扫描与已有衣柜加衣工具 | 开发分支，Unity 活体验证待跑 / Development branch, Unity live validation pending |
| Parameter usage checks and suggestions | 参数占用检查与建议 | 已可用 / Available |
| Screenshot and multi-view analysis | 截图分析与多视角检查 | 已可用 / Available |
| Batch workflows | 批量工作流 | 计划中 / Planned |

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
| Connection diagnostics | Shows connection results and failure reasons | 连接诊断 | 显示连接结果和失败原因 |

## Agent Workspace / Agent 工作台

The desktop app provides an agent workspace with a project sidebar and multiple chat sessions. Chats are saved locally and survive restarts; when you continue an old chat, the full transcript is replayed to the backend so context is preserved.

桌面应用提供完整的 Agent 工作台：侧栏按项目/临时对话分组，支持多会话切换。会话保存在本地，重启后仍在；继续旧会话时会把完整前文回放给后端，上下文不丢失。

First launch shows a step-by-step setup wizard (core connection → model provider → Unity project) with a progress bar; each step is detected automatically and can be skipped. The sidebar offers a temporary chat and a new-project picker; a project can be chosen from the scanned list or added by typing its folder path, and project rows support right-click collapse, hide, and remove.

首次启动会出现分步设置向导（核心连接 → 模型供应商 → Unity 项目），带进度条；每一步自动检测完成状态，也可以随时跳过。侧栏顶部提供「临时对话」和「新项目」；项目可以从扫描列表选择，也可以手动填入文件夹路径添加；项目行支持右键折叠、隐藏或移除。

Agent replies are chat bubbles that state explicitly what the agent will do next. Command and skill executions appear as collapsed rows that expand to show the full command, output, and duration, and each turn shows its total running time. Messages typed while the agent is busy are queued and sent in order, so an ongoing task can be steered. Selecting text in a reply shows a floating toolbar to copy it, quote it into the composer, or ask about it in a new session.

Agent 回复以对话气泡呈现，并显式说明接下来要做什么；命令与能力执行显示为可展开的折叠行（完整命令、输出、耗时），每轮对话都显示总运行时长。Agent 执行中输入的消息会自动排队、按顺序发送，可用来中途引导任务。选中回复中的文字会弹出浮动工具条：复制、引用到输入框，或在新会话中提问。

Composer commands / 输入框命令：

- Type `/` to open the command autocomplete menu. 输入 `/` 弹出命令补全菜单。
- `/compact` compresses the current chat history into a summary to free context. It prefers an LLM-generated summary and falls back to a local digest when the model is unavailable. `/compact` 会把当前会话历史压缩成摘要以释放上下文；优先使用模型生成摘要，模型不可用时回退本地摘要。
- `/<skill-name> [args]` invokes an enabled skill directly. `/<skill名> [参数]` 可直接调用已启用的 skill。

## Provider Notes / 模型服务说明

VRCForge supports Google AI Studio, OpenAI, Anthropic, Ollama, Google Vertex AI, DeepSeek, OpenRouter, and custom OpenAI-compatible endpoints.
VRCForge 支持 Google AI Studio、OpenAI、Anthropic、Ollama、Google Vertex AI、DeepSeek、OpenRouter 和自定义 OpenAI-compatible endpoint。

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
