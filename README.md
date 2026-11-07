<div align="center">

![VRCForge — AI Agent + MCP for VRChat Avatar Editing](docs/assets/vrcforge-atelier-banner.png)

[![稳定版](https://img.shields.io/github/v/release/ayyitong888/VRCForge?label=稳定版&style=flat-square)](https://github.com/ayyitong888/VRCForge/releases/latest)
![当前版本](https://img.shields.io/badge/当前版本-v1.10.7-d9487c?style=flat-square)
[![许可证 GPL-3.0-only](https://img.shields.io/badge/许可证-GPL--3.0--only-64748b?style=flat-square)](LICENSE)
![平台 Windows x64](https://img.shields.io/badge/平台-Windows%20x64-0ea5e9?style=flat-square)
[![GitHub Stars](https://img.shields.io/github/stars/ayyitong888/VRCForge?style=social)](https://github.com/ayyitong888/VRCForge/stargazers)

**简体中文** · [English](README.en.md) · [日本語](README.ja.md)

**[官网](https://ayyitong888.github.io/VRCForge/)**

</div>

# 用 AI 辅助 VRChat Avatar 改模

VRCForge 是给 VRChat Avatar 创作者使用的开源改模工具。你可以用自然语言说出想改的
脸型、衣服、材质或动画，让助手查看工程、提出修改并实际应用。

> 使用任何会写入 Unity 资产的功能前，请先备份 Unity / VRChat Avatar 工程。

当前正式版：[1.10.7](https://github.com/ayyitong888/VRCForge/releases/tag/v1.10.7) · [更新说明](docs/RELEASE_NOTES_1.10.7.md)

## 下载：App 和 Unity 插件都要装

**下面两项都要下载，使用同一 Release 的配套版本。**

| 必需下载 | 安装到哪里 |
| --- | --- |
| **[App 离线安装包（.exe）](https://github.com/ayyitong888/VRCForge/releases/download/v1.10.7/VRCForge_Offline_Installer_x64.exe)** | 在 Windows 运行安装；也可改用 [在线安装包](https://github.com/ayyitong888/VRCForge/releases/download/v1.10.7/VRCForge_Web_Installer_x64.exe)，两种安装器选一个。 |
| **[Unity Core 插件（VRCForge.unitypackage）](https://github.com/ayyitong888/VRCForge/releases/download/v1.10.7/VRCForge.unitypackage)** | 在每个要连接的 Unity 工程中 **Import All**。只安装 App 无法连接 Unity。 |

升级时也请同时更新 App 和 Unity 插件。

## 第一次使用

1. **备份工程，运行 App 安装包。** 按安装窗口提示完成安装。
2. **打开 Unity 工程，导入插件。** 在 Unity 中选择 `Assets > Import Package > Custom Package…`，打开下载的 `VRCForge.unitypackage`，点击 **Import All**。如提示缺少 VRChat Avatar SDK，按提示补齐，等 Unity 编译完成。
3. **打开 VRCForge，选择这个工程和 Avatar。** 保持 Unity 开着，确认 App 显示已连接。列表里没有工程时，用「新项目」添加工程文件夹（包含 `Assets` 和 `ProjectSettings` 的那层）。
4. **选下面一种 AI 方式。** 连好后先试一句：“检查这个模型，先不要修改。”

还没有 Avatar 工程？可先用 [Creator Companion（VCC）](https://vcc.docs.vrchat.com/guides/getting-started/) 或 ALCOM 创建。连接不上时，先确认 Unity 已打开、插件已导入、Console 没有红色编译错误。

## 选择 AI 入口

**内置 Agent 仍在测试中。** 外部 MCP 的连接和常用工程操作基本稳定，日常改模建议优先使用外部 MCP 客户端。

- **在 VRCForge 里聊天：** 打开「设置 → 模型」，选择模型服务、填写 API Key，保存后选择模型并测试连接。
- **用已有的 MCP Agent：** 打开「设置 → 连接器」，开启「连接外部 Agent」，选择客户端并点击「安装 MCP 配置」；也可复制通用连接说明交给 Agent。保持 VRCForge 和 Unity 开着，重启或重新连接客户端，再让 Agent 检查模型。需要改模时再打开「允许修改项目」。[详细连接步骤](USER_MANUAL.md#external-agent-connectors)

## 修改、恢复与复用

修改前确认目标和方案，修改后检查外观与结果。可选择逐项确认、自动或完全权限模式；自动模式保留高风险操作确认，**恢复检查点始终需要单独确认**。检查点不能代替工程备份。

常用操作可保存为 **Skills**，也可导入、导出 **.vsk 技能包**。[技能使用说明](USER_MANUAL.md#skill-packages-and-path-to-skill)

支持 Windows x64、Unity 2022.3 LTS 和 VRChat SDK3 Avatar 工程。可编辑范围取决于模型自带的调节项和已安装插件；换头、部件移植可能仍需手工处理网格、权重和 UV。更多支持范围与限制见 [用户手册](USER_MANUAL.md) 和 [兼容性矩阵](docs/COMPATIBILITY_MATRIX.md)。

## 文档入口

- [用户手册 / User Manual](USER_MANUAL.md)
- [1.10.7 更新说明](docs/RELEASE_NOTES_1.10.7.md)
- [兼容性矩阵](docs/COMPATIBILITY_MATRIX.md)
- [产品回归契约](docs/PRODUCT_REGRESSION_CONTRACT.md)
- [优化策略](docs/OPTIMIZATION_STRATEGY.md)
- [Unity Package 打包说明](packaging/README.md)
- [依赖与许可证](DEPENDENCIES.md) · [NOTICE](NOTICE) · [SECURITY](SECURITY.md)

## 源码开发

普通用户应优先使用 Release 安装器。源码调试可在仓库根目录运行：

```powershell
python -m pip install -r requirements.txt
start_dashboard.cmd
```

贡献代码前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 隐私与许可证

项目索引、聊天、记忆和检查点默认保存在本机。使用外部模型服务时，发送的内容取决于模型和操作。不要公开密钥、付费资产或私有文件；分享诊断包前请检查内容。[隐私说明](USER_MANUAL.md#privacy-boundary)

项目采用 [GPL-3.0-only](LICENSE)。Unity MCP Core 与工具契约为项目自有实现，公开包不捆绑第三方 Unity MCP 运行时代码。第三方依赖与声明见 [DEPENDENCIES](DEPENDENCIES.md) 和 [NOTICE](NOTICE)。
