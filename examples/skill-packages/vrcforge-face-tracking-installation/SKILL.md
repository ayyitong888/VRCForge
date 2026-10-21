---
name: vrcforge-face-tracking-installation
title: VRChat 面捕安装与配置
description: Install ordinary VRChat face tracking from the author's supplied Patch and documentation, reusing an existing Scene or Variant without routing into full head transplant.
permission-mode: approval_required
risk-level: medium
entrypoint-tool: vrcforge_list_directory
allowed-tools:
  - vrcforge_list_directory
  - vrcforge_find_files
  - vrcforge_read_text_file
  - vrcforge_get_asset_info
  - vrcforge_list_execution_targets
  - vrcforge_bind_execution_target
  - vrcforge_read_avatar_descriptor
  - vrcforge_scan_blendshapes
  - vrcforge_scan_parameters
  - vrcforge_scan_fx_animator
  - vrcforge_scan_animation_bindings
  - vrcforge_instantiate_prefab
  - vrcforge_unpack_prefab
  - vrcforge_scene_save
  - vrcforge_set_property
---

# 普通面捕安装

仅处理作者已经提供 Scene、Variant、安装说明或其他可读依据的普通面捕安装。按需读取随包 README、PDF、图片说明；如果作者另有 FT Patch，再先读取并核对该 Patch。文件缺失或不可读时只报告该项能力缺口。脚本、删除、外部命令和作者要求的额外操作仍需当前用户明确授权。

作者要求 Patch 时，Patch 必须先被实际读取并验证，再使用作者说明中的现成 Scene 或 Variant。仅看到同名文件、路径或资产存在，不证明 Patch 已应用或面捕已配置。已有 Variant 已满足作者约定时，按其作者路径执行必要的 instantiate、完全 unpack、身份绑定、读对象和 scene save；不重新制作完整 Mesh/FX/Parameters/Menu 四件套。

这是编辑态配置路径：按实际说明读取 Descriptor、BlendShapes、Parameters、FX、绑定和目标字段；每个获批写入都要 checkpoint、精确回读，并将 serialized configuration 与 Play/Gesture Manager、视觉或 NDMF 运行验收分开报告。默认不进入 Play Mode，不调用 Gesture Manager，不做全换头，不加载完整换头指南。
