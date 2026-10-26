# VRCForge 问题与修复记录

当前总览：[本轮完整复盘](../local-review/conversation-closeout-20261023/CONVERSATION_LESSONS.md)。1.9.15 已正式发布，模型保持暂停；下方追加状态按各自日期理解。

内部维护资料，按 Bug 类型拆分。每条只记录问题、原因、修复方式和修复版本。

历史文档中只有防护约定、没有具体事故或根因的条目保持这一含义；修复版本缺失时标为“未记录”，不把最近验证版本当成修复版本。

| Bug 类型 | 记录数 |
|---|---:|
| [聊天与保存](regression/chat.md) | 18 |
| [图片与视觉](regression/images.md) | 5 |
| [模型服务、协议与重试](regression/providers.md) | 16 |
| [Agent 循环与续接](regression/agent.md) | 23 |
| [MCP 与工具调用](regression/mcp.md) | 5 |
| [审批、取消与恢复](regression/approval.md) | 12 |
| [界面与交互](regression/interface.md) | 22 |
| [启动与进程生命周期](regression/lifecycle.md) | 3 |
| [Unity 与 Core](regression/unity.md) | 13 |
| [安装、升级与发布](regression/release.md) | 12 |

共 129 个二级分类记录（含历史约定）；各条保留修复与验收层级，条目数不代表全链路通过。

[整理前完整原文](regression/history/PRODUCT_REGRESSION_CONTRACT.before-reorganization.md)保留原始约束和历史细节；[原发布验收记录](RELEASE_REGRESSION_GATES.md)保留原位。

本次分类整理及补充仅保留在主工作区本地，未提交、未放入安装包。
