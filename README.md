# VRCForge

[![Version](https://img.shields.io/badge/version-v0.3.1--alpha-blue)](https://github.com/ayyitong888/VRCForge/releases/tag/v0.3.1-alpha)
[![GitHub stars](https://img.shields.io/github/stars/ayyitong888/VRCForge?style=social)](https://github.com/ayyitong888/VRCForge/stargazers)

## Official Repository / 官方仓库

Official repository: https://github.com/ayyitong888/VRCForge

VRCForge is distributed under GPL-3.0. If you obtained VRCForge from any third-party source, please verify that the copyright notice, GPL-3.0 license, and source code access are preserved.

Unofficial paid copies or modified versions are not endorsed by the original author unless they clearly preserve the GPL-3.0 license, copyright notice, and source code availability.

VRCForge 官方仓库为：https://github.com/ayyitong888/VRCForge

VRCForge 使用 GPL-3.0 协议发布。如果你从第三方渠道获得本项目，请确认其保留了版权声明、GPL-3.0 许可证以及源码获取方式。

未经官方确认的付费副本或修改版本，不代表原作者认可；除非其清楚保留 GPL-3.0 许可证、版权声明以及源码获取方式。

> **WIP / 开发中**
>
> VRCForge is under active development. Back up your avatar project before writing Unity assets.
> VRCForge 仍在开发中。写入 Unity 资产前，请先备份 Avatar 工程。

If VRCForge helps your avatar workflow, please star the repo.
如果 VRCForge 对你的 Avatar 工作流有帮助，欢迎点一个 Star。

VRCForge is a local Unity dashboard for VRChat avatar editing.
VRCForge 是一个面向 VRChat Avatar 编辑的本地 Unity dashboard。

## Features / 功能状态

| Feature | 功能 | Status |
| --- | --- | --- |
| Avatar and facial Blendshape loading | 读取 Avatar 与脸部 Blendshape | 已可用 / Available |
| Manual slider editing and undo | 手动滑块调整与撤销 | 已可用 / Available |
| Natural-language Blendshape planning | 自然语言生成 Blendshape 调整 | 已可用 / Available |
| Reference-image assisted face editing | 参考图辅助捏脸 | 已可用 / Available |
| AI face tuning history | AI 捏脸历史 | 已可用 / Available |
| Saved face tuning presets | 捏脸预设保存与重放 | 已可用 / Available |
| Locked Blendshapes for partial reroll | 锁定形态键后局部重抽 | 已可用 / Available |
| AI Shader / Material tuning MVP | AI 材质参数调校 MVP | 开发中 / In development |
| Before/after screenshot comparison | 执行前后截图对比 | 开发中 / In development |
| Wardrobe FX scanning and generation | 衣柜 FX 扫描与生成 | 开发中 / In development |
| Parameter usage checks and suggestions | 参数占用检查与建议 | 开发中 / In development |
| Screenshot and multi-view analysis | 截图分析与多视角检查 | 开发中 / In development |
| Batch workflows | 批量工作流 | 计划中 / Planned |

## AI Face Tuning History and Presets / AI 捏脸历史与预设

VRCForge supports iterative AI-assisted face tuning. Each generated Blendshape adjustment plan can be reviewed, applied, restored, saved as a preset, and reapplied later. Users can explore multiple tuning candidates while keeping good results under their control.

VRCForge 支持迭代式 AI 辅助捏脸。每次生成的 Blendshape 调整方案都可以先审阅，再应用、恢复、保存为预设，并在之后重新应用。用户可以多次尝试不同候选结果，同时保留满意的调整。

Locked Blendshapes are excluded from new planning and blocked during apply, which provides a foundation for keeping good regions and rerolling only unsatisfactory parts.

锁定的 Blendshape 会从新一轮规划中排除，并在应用时再次拦截。这为“保留满意部位，只重抽不满意部位”的流程提供基础。

## AI Shader / Material Tuning MVP / AI 材质调校 MVP

VRCForge supports AI-assisted, reviewable, user-controlled material parameter tuning for lilToon and Poiyomi. It scans avatar items, meshes, renderers, and material slots, builds a safe inventory for AI, and can optionally use screenshots or reference images for Vision-assisted tuning. AI returns a structured JSON plan, and VRCForge applies only whitelisted semantic material parameters through shader adapters. The workflow supports Apply, Restore, Save Preset, Reapply, material locks, and optional Vision review.

VRCForge 支持面向 lilToon 和 Poiyomi 的 AI 辅助材质参数调校。它会扫描 Avatar 物品、Mesh、Renderer 与材质槽，生成安全的材质清单，并可选配合截图或参考图进行识图辅助调校。AI 只返回可审阅的 JSON 计划，VRCForge 只通过 shader adapter 写入白名单语义参数。流程支持应用、恢复、保存预设、重新应用、材质锁定和可选识图复核。

The MVP does not edit shader code, texture files, mesh data, render queue, stencil, culling, blend mode, or shader assignment. Back up Unity / VRChat avatar projects before using asset-writing features.

MVP 不会编辑 shader 代码、贴图文件、Mesh 数据、渲染队列、模板、剔除、混合模式或 shader 指派。使用任何写入 Unity 资产的功能前，请先备份 Unity / VRChat Avatar 工程。

## Vision Review Workflow / 识图复核工作流

For accurate visual review, prefer Gesture Manager Play Mode screenshots. Unity Scene view can differ from VRChat because lighting, camera perspective, and shader execution are not the same. When Unity is in Play Mode, VRCForge captures the current Game View, so adjust the Gesture Manager / Game View camera to the desired front-face angle before clicking Capture.

为了让识图复核更接近 VRChat 游戏内效果，建议使用 Gesture Manager 的 Play Mode 截图。Unity Scene 视图会受到光照、相机焦距和 shader 运行状态差异影响。Unity 进入 Play Mode 后，VRCForge 会截取当前 Game View；请先在 Gesture Manager / Game View 中把 Avatar 正脸角度调好，再点击“捕获截图”。

Recommended workflow:
1. Apply blendshape or shader/material changes.
2. Enter Play Mode with Gesture Manager active.
3. Adjust Gesture Manager / Game View to the desired front-face preview.
4. Capture Before / After screenshots from the Vision Review panel.
5. Run Vision Review and use the result as an advisory check.

推荐流程：
1. 先应用 Blendshape 或 shader/material 改动。
2. 进入 Play Mode，并启动 Gesture Manager。
3. 在 Gesture Manager / Game View 中调整到需要的正脸预览角度。
4. 在 Vision Review 面板捕获调整前 / 调整后截图。
5. 运行识图复核，把结果作为审查建议。

If the dashboard detects that Unity is not in Gesture Manager Play Mode, it will show a reminder but will not block capture. Outside Play Mode, the Vision Review panel keeps the original Scene View screenshot behavior; in Play Mode it uses Game View capture.

如果 dashboard 检测到当前不是 Gesture Manager Play Mode，会弹窗提醒但不会强制阻止截图。未进入 Play Mode 时，Vision Review 面板保留原来的 Scene View 截图行为；进入 Play Mode 后会使用 Game View 截图。

## Quick Start / 快速开始

1. Prepare Windows, Unity 2022.3 LTS, a VRChat SDK3 Avatar project, and Python.
   准备 Windows、Unity 2022.3 LTS、VRChat SDK3 Avatar 工程和 Python。
2. Install dependencies: `python -m pip install -r requirements.txt`
   安装依赖：`python -m pip install -r requirements.txt`
3. Start the dashboard: `start_dashboard.cmd`
   启动 dashboard：`start_dashboard.cmd`
4. Start MCP for Unity inside Unity, then select an Avatar and load Blendshapes.
   在 Unity 中启动 MCP for Unity，然后选择 Avatar 并加载 Blendshape。

Dependency details: [DEPENDENCIES.md](DEPENDENCIES.md)
依赖清单：[DEPENDENCIES.md](DEPENDENCIES.md)

## Providers / 模型接入

Google AI Studio, OpenAI, Anthropic, Ollama, Google Vertex AI, DeepSeek, OpenRouter, and custom OpenAI-compatible endpoints. Face editing can send optional original/current images and optional target images; each group supports paste, local image selection, typed paths, or Unity screenshots. Image input depends on the selected model.
支持 Google AI Studio、OpenAI、Anthropic、Ollama、Google Vertex AI、DeepSeek、OpenRouter 和自定义 OpenAI-compatible endpoint。捏脸可选传原图/当前脸和目标参考图，每组都支持粘贴图片、选择本地图片、手填路径或 Unity 截图。图片输入能力取决于所选模型。

## Documentation / 文档

[NOTICE](NOTICE)
[DEPENDENCIES.md](DEPENDENCIES.md)
[docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)
[docs/FACE_TUNING_ACCEPTANCE_TEST.md](docs/FACE_TUNING_ACCEPTANCE_TEST.md)
[SHADER_TUNING_PLAN.md](SHADER_TUNING_PLAN.md)
[docs/SHADER_TUNING_CHECKPOINTS.md](docs/SHADER_TUNING_CHECKPOINTS.md)
[USER_MANUAL.md](USER_MANUAL.md)

## License / 许可证

[LICENSE](LICENSE)
