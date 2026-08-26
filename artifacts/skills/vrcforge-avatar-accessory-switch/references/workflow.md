# VRChat 饰品安装与开关：社区实际做法与固定步骤

给目标 Avatar 安装或调整一个用户明确指定的饰品，并制作该饰品独立开关；与跨 Avatar 提取/移植现有配件不同。

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

为该饰品创建一个独立 saved/synced Bool；按当前权限策略创建准确 FX state、time=0 根对象激活曲线和 `换装 / 配饰` 控件，保留已有 OFF 空 Motion 和初始场景基线。

## 5. 完整验收和停止条件

回读 `绑定/骨骼 → 独立 Bool → FX → ON Clip → 菜单`；不把饰品塞入衣柜 Int，也不改其他服装、发型和饰品。

**每笔写入都遵守当前用户配置的权限策略，并绑定精确目标、preview、checkpoint 和写后回读；回滚必须单独确认。**

