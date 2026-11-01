# VRCForge 1.10.3

**App 和 Unity 插件都要更新。** 下载 [Windows 安装包](https://github.com/ayyitong888/VRCForge/releases/download/v1.10.3/VRCForge_Offline_Installer_x64.exe)，并在要连接的 Unity 工程中完整导入 [VRCForge.unitypackage](https://github.com/ayyitong888/VRCForge/releases/download/v1.10.3/VRCForge.unitypackage)。

## 安装与上手

- 安装器和卸载器改用统一的单窗口界面，支持中文、日本語和 English；安装设置在窗口内展开，不再使用多页向导。
- 继续沿用已有安装位置、数据目录和卸载入口；升级保留设置和会话，卸载默认保留个人数据。
- 安装失败显示具体原因并提供重试和日志导出。旧版程序保留到新版文件和安装登记完成，再清理备份。
- 入门说明改成可直接照做的短步骤，写清 App 与 Unity 插件安装，以及内置 AI、外部 MCP 的连接方式。

## Unity 与 Skills

- 补上 NDMF 和 SDK 原生 Auto Fix 动作入口，沿用报告提供的修复动作。
- 材质读取补充 Shader 路径和渲染队列；资源与动画工具说明写清查询范围和用法。
- 新增 Slime 改模 Skill，包含颜色、材质、水波、换装过渡与常见问题处理。下载本 Release 的 `VRCForge_Avatar_Slime_*.vsk`，在「能力库」中选择导入技能包；离线安装后的 `skill-packages` 文件夹也有同一包。这是社区开发包，按导入窗口提示确认后使用，不会自动安装或启用。

## 验证边界

具体模型、第三方插件和系统环境的结果仍以实测为准。安装器截图、编译通过或隔离回放不代表某台机器的升级与卸载已验收。
