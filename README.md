# VRCForge

[![GitHub stars](https://img.shields.io/github/stars/ayyitong888/VRCForge?style=social)](https://github.com/ayyitong888/VRCForge/stargazers)

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
| Reference-image assisted face editing | 参考图辅助捏脸 | 开发中 / In development |
| Before/after screenshot comparison | 执行前后截图对比 | 开发中 / In development |
| Wardrobe FX scanning and generation | 衣柜 FX 扫描与生成 | 开发中 / In development |
| Parameter usage checks and suggestions | 参数占用检查与建议 | 开发中 / In development |
| Screenshot and multi-view analysis | 截图分析与多视角检查 | 开发中 / In development |
| Presets and batch workflows | 预设系统与批量工作流 | 计划中 / Planned |

## Quick Start / 快速开始

1. Prepare Windows, Unity 2022.3 LTS, a VRChat SDK3 Avatar project, and Python.
   准备 Windows、Unity 2022.3 LTS、VRChat SDK3 Avatar 工程和 Python。
2. Install dependencies: `python -m pip install -r requirements.txt`
   安装依赖：`python -m pip install -r requirements.txt`
3. Start the dashboard: `start_dashboard.cmd`
   启动 dashboard：`start_dashboard.cmd`
4. Start MCP for Unity inside Unity, then select an Avatar and load Blendshapes.
   在 Unity 中启动 MCP for Unity，然后选择 Avatar 并加载 Blendshape。

## Providers / 模型接入

Google AI Studio, OpenAI, Anthropic, Ollama, Google Vertex AI, DeepSeek, OpenRouter, and custom OpenAI-compatible endpoints. Image input depends on the selected model.
支持 Google AI Studio、OpenAI、Anthropic、Ollama、Google Vertex AI、DeepSeek、OpenRouter 和自定义 OpenAI-compatible endpoint。图片输入能力取决于所选模型。

## Documentation / 文档

[USER_MANUAL.md](USER_MANUAL.md)

## License / 许可证

[LICENSE](LICENSE)
