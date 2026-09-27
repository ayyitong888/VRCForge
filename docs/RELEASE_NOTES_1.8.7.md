# VRCForge 1.8.7

本次更新修复大型 Avatar 和衣柜读取不完整的问题，并改善长结果与聊天历史的分页传递。

## Avatar 与衣柜

- Avatar 清单支持分页读回，避免大型模型超过单次返回范围后漏掉对象。
- 衣柜菜单保留参数值相同的多个入口，避免不同菜单项被合并。
- Unity Core 工具契约更新为 161，声明工具数保持 97；升级时使用同一 Release 的配套 Unity 包。

## Agent 与聊天

- 长工具结果保留完整浅层字段、嵌套读取指针和分页信息，便于继续读取剩余内容。
- 修复网页搜索续页信息与 Shell 执行回执在传递过程中的丢失。
- 大型聊天历史通过桌面桥接分页传输，并校验完整内容。
- 模型请求失败而缺少用量回执时，明确标记统计不完整，保留已知用量。

## 下载

- **Offline Installer**：完整安装包。
- **Web Installer**：安装时下载本版本程序。
- **Windows x64 ZIP**：完整程序文件。
- **VRCForge.unitypackage**：配套 Unity 插件。
- **release-manifest.json**：版本、构建策略与文件校验信息。

1.8.7 已发布附件的清单标记为 `local-acceptance`、`releaseEligible=false`。
这些附件未通过严格发布构建资格；文件校验一致不代表这一门禁已经通过。

Protocol: MCP 2.0 (`2026-07-28`). Windows binaries are not code-signed; verify the release manifest hashes.
