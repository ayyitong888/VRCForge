# VRCForge 1.10.3

**App 和 Unity 插件都要更新。** 下载 [Windows 安装包](https://github.com/ayyitong888/VRCForge/releases/download/v1.10.3/VRCForge_Offline_Installer_x64.exe)，并在要连接的 Unity 工程中完整导入 [VRCForge.unitypackage](https://github.com/ayyitong888/VRCForge/releases/download/v1.10.3/VRCForge.unitypackage)。

## 安装与上手

- 安装器和卸载器改用统一的单窗口界面，支持中文、日本語和 English；安装设置在窗口内展开，不再使用多页向导。
- 继续沿用已有安装位置、数据目录和卸载入口；升级保留设置和会话，卸载默认保留个人数据。
- 安装失败显示具体原因并提供重试和日志导出。旧版程序保留到新版文件和安装登记完成，再清理备份。
- 入门说明改成可直接照做的短步骤，写清 App 与 Unity 插件安装，以及内置 AI、外部 MCP 的连接方式。

## 界面响应

- 技能库、优化、检查点和配置等页面等待请求时，不再占住窗口线程；主题图片读写也改到后台。
- 技能开关直接更新对应卡片，减少重复检查和整页刷新；开关样式与行内按钮对齐。
- 右侧只显示一条 Agent 状态，跟随最近成功连通的一方切换；MCP 连接变化即时更新，不再因 90 秒没有调用而误报断开。

## Unity 与 Skills

- 补上 NDMF 和 SDK 原生 Auto Fix 动作入口，沿用报告提供的修复动作。
- 材质检查提供 Shader 路径、Shader 默认队列、材质覆盖值和有效队列；工具说明写清字段含义与查询范围。
- 随包提供十个正式签名的 Avatar 技能包，包括换装、动画、发型、配饰、表情菜单、体检、胸部物理、换头、部件移植和 Slime。首次打开会导入当前能力库，默认关闭，点击开启后才会使用；升级已有包保留原开关。
- Slime Skill 包含颜色、材质、水波、换装过渡与常见问题处理，也可单独下载本 Release 的 `VRCForge_Avatar_Slime_*.vsk`，在「能力库」中导入。Slime 同样提供正式签名包，保留已有包 ID，支持直接升级。

## 验证边界

具体模型、第三方插件和系统环境的结果仍以实测为准。安装器截图、编译通过或隔离回放不代表某台机器的升级与卸载已验收。
