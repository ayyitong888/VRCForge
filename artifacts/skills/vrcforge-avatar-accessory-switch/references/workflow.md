# VRChat 饰品安装与开关：社区建议与适用条件

给目标 Avatar 安装或调整一个用户明确指定的饰品开关；新建按用户确认的参数、菜单及动画设计，已有 Avatar 的增删改查按现场证据保留既有结构，不套用固定参数/路径/时序模板。与跨 Avatar 提取/移植现有配件不同。

## 适用范围与禁止事项

整套服装 Setup Outfit、盲目替换已经有效的 constraint、未经授权的外部模型资源、自动暴露隐藏参数或跨 Avatar 批量复制。

## 拿不准时先查社区

涉及骨骼、参数、FX、动画、菜单、约束、PhysBone、Modular Avatar、NDMF 或作者 prefab 时，先查成熟社区教程、资产作者说明和官方文档。只使用已经存在的 VRCForge 原子工具；无法证明或没有精确安全原子时停止并报告 `capabilityGap=true`，不能编造功能或碰未经授权的付费资源。

## 1. 目标绑定与现状盘点

扫描目标饰品完整子树、实际组件、target bone、source、weight、offset、世界坐标、局部骨链、PhysBone、collider 和引用闭包。

## 2. 真实结构和社区规则

按真实结构区分 Modular Avatar Bone Proxy、Unity ParentConstraint、VRChat VRCParentConstraint、已有 MA constraint 或实际受权重骨骼；不能仅因为某条路线常见就替换现有绑定。

## 3. 精确原子与适用条件

只有明确请求 Bone Proxy 时使用对应 MA component 原子；只有明确请求修改现有 VRChat constraint source 时使用 `vrcforge_preview_constraint_sources` / `vrcforge_set_constraint_sources`；Unity constraint 转换必须另行明确授权；缺少 MA constraint 写原子时返回 `capabilityGap=true`。

## 4. 执行边界与读取结果

新建时按用户确认的参数类型/同步方式、菜单路径与动画时序制作开关；不得默认强制独立 Bool、固定 `换装 / 配饰` 路径或 time=0。维护现有饰品时按 Write Defaults、恢复路径及动画证据保留有效参数、菜单路径、FX/动画时序和 OFF 空 Motion；若证实其中有错误，只在用户批准的精确差异范围内修复。初始场景基线及未涉及对象保持不变。

## 5. 完整验收和停止条件

回读 `绑定/骨骼 → 用户确认的选择器 → FX → 动画 → 菜单`；不把饰品未经用户设计并入其他选择器，也不改其他服装、发型和饰品。FX、恢复状态与 OFF 动画是否保留/修复按同一证据和批准差异规则处理。

**每笔写入都遵守当前用户配置的权限策略，并绑定精确目标、preview、checkpoint 和写后回读；回滚必须单独确认。**
