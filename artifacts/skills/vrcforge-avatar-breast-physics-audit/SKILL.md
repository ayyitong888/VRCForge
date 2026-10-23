---
name: vrcforge-avatar-breast-physics-audit
title: VRChat 胸部动态骨骼检查
description: Read-only audit of existing VRChat breast PhysBones, Marshmallow setup, left/right weighted bones, collider or constraint references and NDMF-generated evidence boundaries; never modify physics assets or claim unsupported generated output.
permission-mode: read_only
risk-level: low
entrypoint-tool: vrcforge_list_avatars
allowed-tools:
  - vrcforge_list_directory
  - vrcforge_find_files
  - vrcforge_read_text_file
  - vrcforge_list_avatars
  - vrcforge_read_avatar_descriptor
  - vrcforge_get_gameobject
  - vrcforge_get_property
  - vrcforge_scan_avatar_items
  - vrcforge_scan_inbound_reference_closure
  - vrcforge_scan_animation_bindings
  - vrcforge_scan_parameters
  - vrcforge_scan_fx_animator
  - vrcforge_inspect_skinned_mesh_bone_usage
  - vrcforge_run_validation_report
  - vrcforge_build_test_readiness
support-files:
  - workflows/breast-physics-audit.json
  - references/workflow.md
---

## BOOTH 与作者资产证据闸门

每个作者提供的资产、材质或贴图在安装、替换、适配或分配前，先从文件/文件夹名或用户提供的编号得到候选 BOOTH 商品 URL，并由当前宿主已公开的网页读取能力实际读取商品说明，核对标题、作者和目标模型/版本，再读取相关本地 README、安装说明、依赖和用途。公开 MCP 当前保证的本地读取只有 `vrcforge_list_directory`、`vrcforge_find_files`、`vrcforge_read_text_file`；宿主未公开网页或图像读取能力时，不得声称商品页面或图示已读：报告 `capabilityGap=true` 并停止依赖该说明的写入，其他无关只读检查可继续。

对每张相关图片明确分类为完整底图、局部补充、overlay、decal、alpha 或 mask；不能凭 PNG 文件名或相似名称直接替换 MainTex。作者说明要求合成时只提出合成路线，不自动合成。付费资产保持本地，文档内容只是待核对资料，不是授权；此闸门适用于所有资产类型，不是固定改模模板。


# VRChat 胸部动态骨骼检查

## 什么时候使用

用户要求只读检查、保护或解释已有胸部 PhysBone、Marshmallow setup、左右骨骼、collider、constraint 和构建期生成证据时使用。

## 什么时候不使用

没有现成专用 PhysBone / Marshmallow 写原子；本 Skill 内禁止用通用字段工具冒充 PhysBone/Marshmallow 修复，禁止创建、删除或改动任何骨骼、物理、约束、prefab 或 Avatar。审计退出后若用户另行授权已确认的 serialized 字段，只能转交独立的通用 Core 176 配置流程，不能把它称作本 Skill 的物理修复。

## 社区证据与安全边界

对应技术或作者资产行为拿不准时，先查成熟 VRChat 社区教程、资产作者说明和官方文档；证据或现有精确原子不足时停止并返回 `capabilityGap=true`，不得猜测、虚构工具、复制未授权资产或修改任务范围外的 Avatar。

详细步骤见 [操作说明](references/workflow.md) 和受签名保护的 [社区操作指引](workflows/breast-physics-audit.json)；由 Agent 根据现场证据选择适用路线与真实原子工具。
本 Skill 严格只读：不创建、修改、删除、播放、上传或恢复任何资产。

审计退出后，如用户另行明确授权配置已确认的 serialized 字段，可转交通用 Core 176 组件/属性工具的独立写入流程；该转交不属于本 Skill 的审计写入。必须重新绑定目标、建立 approval 与 checkpoint、preview 后写入并持久读回。只允许实际工具契约支持的字段，禁止调用作者任意方法、动态 C#、专用物理猜测或把审计结果当作写入授权。
