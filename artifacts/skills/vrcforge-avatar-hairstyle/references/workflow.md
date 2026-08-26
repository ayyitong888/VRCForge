# VRChat 发型切换：社区实际做法与固定步骤

新增、调整或检查一个独立发型切换，并根据包含默认发型的总数选择既有 Bool 或 Int 习惯。

## 适用范围与禁止事项

整套服装、独立饰品、跨 Avatar 配件移植、面捕替换或未经单独允许的整组参数迁移。

## 拿不准时先查社区

涉及骨骼、参数、FX、动画、菜单、约束、PhysBone、Modular Avatar、NDMF 或作者 prefab 时，先查成熟社区教程、资产作者说明和官方文档。只使用已经存在的 VRCForge 原子工具；无法证明或没有精确安全原子时停止并报告 `capabilityGap=true`，不能编造功能或碰未经授权的付费资源。

## 1. 目标绑定与现状盘点

固定目标 Avatar 并读取 Descriptor 实际绑定的 Parameters、FX、Menu；扫描现有头发、默认发型、选项总数、骨骼、PhysBone、动画和菜单。

## 2. 真实结构和社区规则

总数必须包含默认发型：新增后少于 8 个时保持每个发型一个 saved/synced Bool；达到 8 个时使用一个 saved/synced 发型 Int。跨过阈值时必须单独确认完整旧 Bool 消费者、菜单、FX、动画和回滚点。

## 3. 精确原子与适用条件

实际绑定是 Head Bone Proxy 时，才用对应 Modular Avatar component preview/write；保留发型局部骨链、PhysBone、collider 和 prefab 根，不把独立骨骼盲目合并进主骨架。

## 4. 执行边界与读取结果

按当前权限策略精确处理 parameter、FX transition/state 和 time=0 对象开关曲线；保留默认头发恢复方式、已有 OFF 空 Motion、现有选项顺序与独立 Bool 语义。

## 5. 完整验收和停止条件

在现有 `换装 / 头发` 分类和正确分页追加目标控件；保留原根菜单、图标、拼写和其他发型；回读完整 `菜单 → 参数 → FX → 动画 → 发型对象` 链。

**每笔写入都遵守当前用户配置的权限策略，并绑定精确目标、preview、checkpoint 和写后回读；回滚必须单独确认。**

