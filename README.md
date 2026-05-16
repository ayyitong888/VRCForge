# VRCForge

[![Version](https://img.shields.io/badge/version-v0.1.0--alpha-blue)](https://github.com/ayyitong888/VRCForge/releases/tag/v0.1.0-alpha)
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
[USER_MANUAL.md](USER_MANUAL.md)

## License / 许可证

[LICENSE](LICENSE)
