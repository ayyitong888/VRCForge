# VRChat Avatar 检查与验收：社区实际做法与固定步骤

用户要求只读盘点现有 Avatar，或者对已完成的改模做 Descriptor、骨骼、菜单、参数、FX、动画和构建就绪验收时使用。

## 适用范围与禁止事项

不用于写资产、自动修复、切换运行参数、进入播放模式、重排层级、替换面捕、截图保存或上传模型。

## 拿不准时先查社区

涉及骨骼、参数、FX、动画、菜单、约束、PhysBone、Modular Avatar、NDMF 或作者 prefab 时，先查成熟社区教程、资产作者说明和官方文档。只使用已经存在的 VRCForge 原子工具；无法证明或没有精确安全原子时停止并报告 `capabilityGap=true`，不能编造功能或碰未经授权的付费资源。

## 1. 目标绑定与现状盘点

固定唯一 `projectPath`、Unity Core 身份和用户明确选择的 Avatar；使用 `vrcforge_list_avatars` 和 `vrcforge_read_avatar_descriptor` 记录场景与当前绑定资产。

## 2. 真实结构和社区规则

只读扫描层级、原始身体/服装/头发/饰品、实际加权骨骼、Modular Avatar 相关对象与引用闭包；保留系统对象和原始命名。

## 3. 精确原子与适用条件

扫描 expression menu、parameters、FX animator、animation bindings 和已有衣柜，核对 `菜单 → 参数 → FX → clip → 实际对象` 闭环。

## 4. 执行边界与读取结果

检查 compile diagnostics、validation report 与 build readiness，区分已经读回的证据、仅推断项和 SDK 私有面板无法证明的内容。

## 5. 完整验收和停止条件

输出目标身份、现有系统、缺口、异常和验收结果；不创建 checkpoint、不执行 Unity 写入、不上传，也不把 inspection-only 请求升级为修改。

**本流程全程只读，不创建或恢复检查点，不进入播放，不写任何 Unity 资产。**

