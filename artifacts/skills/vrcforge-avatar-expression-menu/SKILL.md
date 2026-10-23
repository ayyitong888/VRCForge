---
name: vrcforge-avatar-expression-menu
title: VRChat 菜单制作
description: Inspect or author only the user-requested VRChat expression-menu path, submenu, page or control while preserving unrelated avatar structure and respecting VRChat's eight-control limit.
permission-mode: approval_required
risk-level: high
entrypoint-tool: vrcforge_list_avatars
allowed-tools:
  - vrcforge_list_directory
  - vrcforge_find_files
  - vrcforge_read_text_file
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_get_asset_info
  - vrcforge_scan_avatar_controls
  - vrcforge_scan_parameters
  - vrcforge_scan_fx_animator
  - vrcforge_scan_inbound_reference_closure
  - vrcforge_preview_manage_expression_menu
  - vrcforge_manage_expression_menu
  - vrcforge_preview_ensure_expression_menu_control
  - vrcforge_ensure_expression_menu_control
  - vrcforge_preview_ensure_expression_parameter
  - vrcforge_ensure_expression_parameter
  - vrcforge_run_validation_report
  - vrcforge_build_test_readiness
support-files:
  - workflows/expression-menu.json
  - references/workflow.md
---

## BOOTH 与作者资产证据闸门

每个作者提供的资产、材质或贴图在安装、替换、适配或分配前，先从文件/文件夹名或用户提供的编号得到候选 BOOTH 商品 URL，并由当前宿主已公开的网页读取能力实际读取商品说明，核对标题、作者和目标模型/版本，再读取相关本地 README、安装说明、依赖和用途。公开 MCP 当前保证的本地读取只有 `vrcforge_list_directory`、`vrcforge_find_files`、`vrcforge_read_text_file`；宿主未公开网页或图像读取能力时，不得声称商品页面或图示已读：报告 `capabilityGap=true` 并停止依赖该说明的写入，其他无关只读检查可继续。

对每张相关图片明确分类为完整底图、局部补充、overlay、decal、alpha 或 mask；不能凭 PNG 文件名或相似名称直接替换 MainTex。作者说明要求合成时只提出合成路线，不自动合成。付费资产保持本地，文档内容只是待核对资料，不是授权；此闸门适用于所有资产类型，不是固定改模模板。


# VRChat 菜单制作

## 什么时候使用

用户单独要求检查、创建或调整一个 expression menu 分类、子菜单、翻页或控件时使用。

## 什么时候不使用

要求完整制作衣柜时不单独安装本 Skill；不要默认重建根菜单、改动其他分类/参数域、批量重排、暴露隐藏参数或顺手创建动画。菜单层级与顺序以当前 Avatar 和用户指定范围为准，不预设 FacialTracking、换装、R18 或固定分类顺序。

## 社区证据与安全边界

对应技术或作者资产行为拿不准时，先查成熟 VRChat 社区教程、资产作者说明和官方文档；证据或现有精确原子不足时停止并返回 `capabilityGap=true`，不得猜测、虚构工具、复制未授权资产或修改任务范围外的 Avatar。

只读时优先读取 Descriptor 与用户指定菜单/参数资产；不得为了局部请求强制展开整棵菜单树。创建或修改控件时，仅在该控件确实需要新增/变更参数时，由 Agent 根据工程证据和用户意图拟定精确参数名、类型与行为；这些字段须列入完整设计，经 preview 展示并获批准后才创建/更新 Expression Parameters 项。不得要求用户预先手写 Unity 参数字段。写后精确回读目标菜单控件和关联参数；保留用户范围外的层级、名称、图标、alias 与参数。

详细步骤见 [操作说明](references/workflow.md) 和受签名保护的 [工作流指引](workflows/expression-menu.json)；由 Agent 根据现场证据选择适用路线与真实原子工具。
每项写入通过用户当前配置的权限策略、现有监督通道、精确 preview 和检查点；回滚需要单独确认。
