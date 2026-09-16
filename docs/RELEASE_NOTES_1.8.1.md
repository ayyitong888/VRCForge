# VRCForge 1.8.1

这次更新修复背景图片和工程发现问题，并把 AI 连接、首次使用及日常排障串成一条更清楚的流程。

## 修复

- 选择本地背景图片后可以正常显示，重新启动后仍保留。
- 工程列表读取 Unity Hub、VCC、ALCOM 和手动登记的工程；区分正在扫描、没有找到和读取失败。
- 可以添加、修改和移除工程登记；路径填写错误时保留原记录，移除登记不删除工程文件。
- 外部 MCP 连接自检适配当前工具接口，不再因找不到旧工具而误报未就绪。
- 修复内部 Agent 按引导调用工具时，正确的工具名称或合法参数被误报为错误的问题；Skill 的工具允许／禁止清单与实际执行保持一致。

## AI 连接与排障

- 新手引导先选择使用内置 AI，或连接已有的外部 AI 客户端。
- 内置 AI 引导填写服务商、Key 和模型，保存后测试连接；外部 AI 不需要额外配置一份模型 Key。
- 同一套 MCP 可配置给多个客户端，保留其他服务器配置，并分别显示最近的安装自检结果。
- 默认提供“连接引导与日常排障”Skill，内外 Agent 都能发现、读取完整步骤和参考资料。
- 指引覆盖工程选择、Unity 插件安装、编译与连接检查；已正常工作的配置会保留，修复仍遵循所选权限模式。

## 下载与升级

- **Offline Installer**：完整安装包，适合直接安装。
- **Web Installer**：小体积安装器，安装时下载本版本程序。
- **Windows x64 ZIP**：完整程序文件。
- **VRCForge.unitypackage**：Unity 插件。
- **release-manifest.json**：文件版本与校验信息。

App 版本为 1.8.1；Unity Core 保持 1.8.0。已经正常连接的工程无需为本次 App 更新重新导入 Core。安装时若程序仍在运行，按提示关闭后点击重试。

Agent 会依据当前日志和连接状态排障；需要用户打开 Unity 或手动导入时，会提供具体步骤。连接自检成功不代表外部客户端持续在线，也不代表任意第三方工程问题都能自动修好。

---

**English:** Fixes custom backgrounds and Unity project discovery, adds internal/external AI setup choices, and includes a shared setup and everyday troubleshooting Skill. Multiple MCP clients can coexist; connection self-tests now support the current read-only tool interface. App 1.8.1 uses the unchanged Unity Core 1.8.0.

**日本語:** 背景画像とUnityプロジェクト検出を修正しました。内蔵AI・外部AIの接続案内と、初期設定・日常のトラブル対応Skillを追加しています。複数のMCPクライアントを併用でき、現行ツールの接続テストに対応しました。Appは1.8.1、Unity Coreは変更なく1.8.0です。
