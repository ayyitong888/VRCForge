# VRChat 菜单制作：社区实际做法与固定步骤

用户单独要求检查、创建或调整一个 expression menu 分类、子菜单、翻页或控件时使用。

## 适用范围与禁止事项

要求完整制作衣柜时不单独安装本 Skill；不要重建根菜单、替换 FT、修改 R18 域、批量重排、暴露隐藏参数或顺手创建动画。

## 拿不准时先查社区

涉及骨骼、参数、FX、动画、菜单、约束、PhysBone、Modular Avatar、NDMF 或作者 prefab 时，先查成熟社区教程、资产作者说明和官方文档。只使用已经存在的 VRCForge 原子工具；无法证明或没有精确安全原子时停止并报告 `capabilityGap=true`，不能编造功能或碰未经授权的付费资源。

## 1. 目标绑定与现状盘点

固定 Avatar，读取 Descriptor 当前绑定 Menu、Parameters 和 FX，完整扫描现有菜单树、类型、图标、名称、参数、值、submenu、页面和 alias。

## 2. 真实结构和社区规则

保留 `FacialTracking → 换装 → R18` 与 `换装 / 衣服 → 头发 → 配饰` 原有顺序；保留中文、英文、日文、空格、大小写和作者原始拼写。

## 3. 精确原子与适用条件

每页最多 8 个控制项，`下一页` 也占一项；参数为空的 SubMenu 是导航，不因为序列化 value 非零就当成服装或发型选项。

## 4. 执行边界与读取结果

确认用户只要求的精确分类、目标页、控件类型、参数类型和值；用 `vrcforge_preview_manage_expression_menu` / `vrcforge_manage_expression_menu` 处理页面，或 `vrcforge_preview_ensure_expression_menu_control` / `vrcforge_ensure_expression_menu_control` 处理单个控件。

## 5. 完整验收和停止条件

遵从当前权限策略并回读唯一预期 delta；保留现有跨菜单 alias、不连续衣柜值、面捕入口、图标和未暴露参数。

**每笔写入都遵守当前用户配置的权限策略，并绑定精确目标、preview、checkpoint 和写后回读；回滚必须单独确认。**

