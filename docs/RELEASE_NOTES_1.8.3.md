# VRCForge 1.8.3

这次更新主要改善 Unity 工程对话中的工具权限、错误处理、流式回复和退出保存。

## Unity 工程对话

- 选择 Unity 工程后，General 工具仍保留原有权限；主机命令继续使用主机工作目录默认值。
- 读取外部源码时，工具参数里的 `projectPath` 只用于定位 General 读取内容，不会被误当成 Unity 工程或写入目标。

## 错误处理

- 明确的权限拒绝会停止重复尝试，并保留清楚的失败提示。
- Unity Shell 的工程范围被拒绝时允许一次范围校正；校正后仍被拒绝就停止，不再循环尝试。
- 模型返回空响应或无效 JSON 时最多请求一次格式纠正；格式纠正期间不重放已经执行的工具。

## 流式回复

- 流式显示只展示 action 回复中的 `reply` 内容，避免把同一响应里的 `summary` 短暂显示出来后又被替换。
- 修复旧页面快照覆盖最新聊天状态的问题，避免快速流式更新时文字交替缺失，并让保存读取最新内容。

## 聊天保存与退出

- 审批完成、拒绝和取消等终态可以正常保存到本地聊天记录。
- 从托盘选择退出时，先保存当前聊天；保存失败会保留窗口并显示重试提示，保存成功后才确认退出。
- 确认退出后先隐藏窗口，再在后台停止 backend，重复的退出请求不会重复启动清理流程。

## 下载与升级

- **Offline Installer**：完整安装包，适合直接安装。
- **Web Installer**：小体积安装器，安装时下载本版本程序。
- **Windows x64 ZIP**：完整程序文件。
- **VRCForge.unitypackage**：Unity 插件。
- **release-manifest.json**：文件版本与校验信息。

App 版本为 1.8.3；Unity Core 是否需要重新导入，请以本次发布包中的 manifest 和实际升级结果为准。

---

**English:** Improves tool permissions in Unity project chats, explicit permission-denial handling, empty or invalid planner response correction, streaming reply display, and chat persistence during tray exit. General tools keep their access in a selected Unity project; an external source `projectPath` is not treated as the Unity write target. Streaming shows the action `reply` without briefly flashing its `summary`. Tray exit saves chats before confirmation, keeps the window available when saving fails, then hides the window and stops the backend in the background after a successful save.

**日本語:** Unity プロジェクトのチャットにおけるツール権限、明示的な権限拒否、空または不正なプランナー応答の補正、ストリーミング表示、トレイ終了時のチャット保存を改善しました。選択した Unity プロジェクトでも General ツールの権限を維持し、外部ソースの `projectPath` を Unity の書き込み対象として扱いません。ストリーミングでは action の `reply` のみを表示し、`summary` が一瞬表示されることを防ぎます。トレイ終了ではチャット保存を先に行い、保存に失敗した場合は再試行できる状態を保ち、成功後にウィンドウを隠して backend をバックグラウンドで停止します。
