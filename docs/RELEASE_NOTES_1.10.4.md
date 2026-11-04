# VRCForge 1.10.4

**App 和 Unity 插件都要更新。** 下载 [Windows 安装包](https://github.com/ayyitong888/VRCForge/releases/download/v1.10.4/VRCForge_Offline_Installer_x64.exe)，并在要连接的 Unity 工程中完整导入 [VRCForge.unitypackage](https://github.com/ayyitong888/VRCForge/releases/download/v1.10.4/VRCForge.unitypackage)。

## 内置助手

- 修复对话压缩与新消息同时到达时的续话问题，保留未完成任务和待处理消息；中断后继续执行时正确衔接工具回执。
- 长内容先提供一段正文，同时说明总量、已读范围和是否还有下文，助手可按需继续读取。
- 修复审批后的截图回执，使截图内容能继续传给助手查看；已完成操作不会仅因未提供资产保存证明而被提示需要恢复。

## 项目检查与恢复

- 补充 Avatar 层级与引用诊断，区分表情菜单配置和场景中的候选控制对象；复制 Descriptor 时正确重映射眼动、口型等对象引用。
- 增加工程文件、Unity 日志和 SDK 布局检查，便于定位引用、编译和项目状态问题。
- 提供手动工程检查点；关闭自动备份后仍可主动创建，Unity 关闭时也可按确认流程恢复文件。

## 工具回执

- 外部工具明确展示已有写入确认参数；会话中的旧目标引用失效时，返回重新选择和绑定的指引。
- BlendShape 预览返回当前权重，便于助手比较修改前后的状态。
