# VRChat 胸部动态骨骼检查：社区实际做法与固定步骤

用户要求只读检查、保护或解释已有胸部 PhysBone、Marshmallow setup、左右骨骼、collider、constraint 和构建期生成证据时使用。

## 适用范围与禁止事项

没有现成专用 PhysBone / Marshmallow 写原子；禁止用通用字段工具冒充修复，禁止创建、删除或改动任何骨骼、物理、约束、prefab 或 Avatar。

## 拿不准时先查社区

涉及骨骼、参数、FX、动画、菜单、约束、PhysBone、Modular Avatar、NDMF 或作者 prefab 时，先查成熟社区教程、资产作者说明和官方文档。只使用已经存在的 VRCForge 原子工具；无法证明或没有精确安全原子时停止并报告 `capabilityGap=true`，不能编造功能或碰未经授权的付费资源。

## 1. 目标绑定与现状盘点

固定 Avatar 和当前 Descriptor，仅用只读原子盘点现有 breast setup、左右骨骼、Body renderer、实际受权重骨骼和 prefab 引用。

## 2. 真实结构和社区规则

扫描现有 PhysBone、collider、constraint source、动画绑定、`MPB_*` 参数与实际引用闭包，保留所有层级和原始对象。

## 3. 精确原子与适用条件

明确区分编辑器 authoring setup、第三方 Modular Avatar / NDMF 构建阶段和最终生成物；当前 FX 没看到生成参数不等于最终构建产物不存在。

## 4. 执行边界与读取结果

左右骨骼、source 引用或最终 build-time FX 无法证明时返回证据缺口；没有专用安全写工具时返回 `capabilityGap=true`，不猜测、不修改。

## 5. 完整验收和停止条件

输出只读检查证据、无法证明项和验证结果；不进入播放模式、不截图写文件、不改参数、不修复。

**本流程全程只读，不创建或恢复检查点，不进入播放，不写任何 Unity 资产。**

