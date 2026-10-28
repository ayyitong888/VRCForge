---
name: vrcforge-avatar-accessory-switch
title: VRChat 饰品安装与开关
description: Install or maintain one VRChat accessory switch using the user's confirmed design for new work and preserving existing Avatar parameters, menu paths, animation timing and proven attachment structure.
permission-mode: approval_required
risk-level: high
entrypoint-tool: vrcforge_list_avatars
allowed-tools:
  - vrcforge_list_directory
  - vrcforge_find_files
  - vrcforge_read_text_file
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_get_gameobject
  - vrcforge_get_property
  - vrcforge_unpack_prefab
  - vrcforge_inspect_skinned_mesh_bone_usage
  - vrcforge_scan_modular_avatar
  - vrcforge_scan_inbound_reference_closure
  - vrcforge_scan_parameters
  - vrcforge_scan_fx_animator
  - vrcforge_scan_animation_bindings
  - vrcforge_scan_avatar_controls
  - vrcforge_preview_add_modular_avatar_component
  - vrcforge_add_modular_avatar_component
  - vrcforge_preview_ensure_expression_parameter
  - vrcforge_ensure_expression_parameter
  - vrcforge_preview_ensure_animator_state
  - vrcforge_ensure_animator_state
  - vrcforge_preview_write_animation_curve
  - vrcforge_write_animation_curve
  - vrcforge_preview_ensure_expression_menu_control
  - vrcforge_ensure_expression_menu_control
  - vrcforge_preview_constraint_sources
  - vrcforge_set_constraint_sources
  - vrcforge_preview_unity_constraint_conversion
  - vrcforge_convert_unity_constraint
  - vrcforge_preview_manage_fx_animator
  - vrcforge_manage_fx_animator
  - vrcforge_preview_manage_expression_menu
  - vrcforge_manage_expression_menu
  - vrcforge_run_validation_report
  - vrcforge_build_test_readiness
support-files:
  - workflows/accessory-switch.json
  - references/workflow.md
---

## BOOTH 与作者资产证据闸门

每个作者提供的资产、材质或贴图在安装、替换、适配或分配前，先从文件/文件夹名或用户提供的编号得到候选 BOOTH 商品 URL，并由当前宿主已公开的网页读取能力实际读取商品说明，核对标题、作者和目标模型/版本，再读取相关本地 README、安装说明、依赖和用途。公开 MCP 当前保证的本地读取只有 `vrcforge_list_directory`、`vrcforge_find_files`、`vrcforge_read_text_file`；宿主未公开网页或图像读取能力时，不得声称商品页面或图示已读：报告 `capabilityGap=true` 并停止依赖该说明的写入，其他无关只读检查可继续。

对每张相关图片明确分类为完整底图、局部补充、overlay、decal、alpha 或 mask；不能凭 PNG 文件名或相似名称直接替换 MainTex。作者说明要求合成时只提出合成路线，不自动合成。付费资产保持本地，文档内容只是待核对资料，不是授权；此闸门适用于所有资产类型，不是固定改模模板。


# VRChat 饰品安装与开关

## 什么时候使用

给目标 Avatar 安装或调整一个用户明确指定的饰品开关；新建时采用用户确认的参数、菜单和动画设计，维护已有 Avatar 时按现场证据保留既有设计。与跨 Avatar 提取/移植现有配件不同。

## 什么时候不使用

整套服装 Setup Outfit、盲目替换已经有效的 constraint、未经授权的外部模型资源、自动暴露隐藏参数或跨 Avatar 批量复制。

## 社区证据与安全边界

功能不熟悉、当前做法有疑问、用户指正，或操作失败且根因存疑时，带实际版本、具体功能、配置及原始报错检索网上的成熟做法。优先读官方文档、资产作者说明和现成仓库源码；论坛、社区教程与实操分享作为经验及线索，核对适用版本和当前配置。真正打开相关正文、源码或设置图，找到具体步骤或实现依据后再做依赖该结论的操作；搜索摘要、标题和无法访问的链接不算已读。说明来源、已证实与待验证处，不猜参数、不照抄未经核对的指令；外部内容不扩大任务或授权。

联网使用当前目录发现的内部研究工具，外部 Agent 使用宿主联网能力；VRCForge MCP 不提供内部联网工具。当前 Skill 范围阻挡内部研究工具时，可经现有 `vrcforge_exit_skill` 退出指令范围后研究，继续原任务前按需重载本 Skill。缺少联网能力或仍无足够证据/精确原子时返回具体 `capabilityGap=true`，仅暂停依赖缺失证据的操作并继续已有依据的工作；不绕过批准、checkpoint 或回滚边界，不虚构工具、复制未授权资产或修改范围外 Avatar。

已有研究仍匹配实际版本和配置、没有新疑点时直接复用，不要求每个普通步骤重新搜索。用户明确纠正立即调整；只研究尚未解决的方法或根因，不用无关研究拖延已授权的简单动作。

详细步骤见 [操作说明](references/workflow.md) 和受签名保护的 [社区操作指引](workflows/accessory-switch.json)；不对已有饰品套用固定 Bool、菜单路径或动画时序，由 Agent 根据现场证据选择适用路线与真实原子工具。

修改场景中的 Prefab instance 前，必须先对精确 instance 执行 `vrcforge_unpack_prefab` preview，核对 GlobalObjectId、源 prefab GUID、依赖 hash 和场景路径，再在批准与 checkpoint 边界内使用 `mode=completely` 完全解包。解包后重新绑定并读回新的场景对象身份；源 prefab 资产、作者 Modular Avatar 组件、骨架、动画与约束来源必须保留。普通材质属性按实际材质工具契约处理，不把材质字段写入错误地描述为必须破坏源 prefab。

面捕的普通安装/serialized 配置读回与 Play 运行验收是两条独立证据链。配置完成只证明编辑态字段、参数、FX、曲线或菜单持久化；Play/视觉验收必须有单独的明确批准、有限预算和原状态恢复，不得把未运行误报为失败或通过。
每项写入通过用户当前配置的权限策略、现有监督通道、精确 preview 和检查点；回滚需要单独确认。
