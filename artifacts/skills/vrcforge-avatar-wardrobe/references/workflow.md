# VRChat 衣柜制作：服装、参数、FX、动画和菜单完整流程

这是一个**单独安装即可完整制作衣柜**的 Skill。衣柜菜单、Expression Parameters、FX Animator 和 AnimationClip 都属于衣柜制作本身；不能要求用户额外安装菜单 Skill 或动画 Skill。

## 0. 不确定时先查社区

骨骼适配、Modular Avatar Setup Outfit、Merge Armature、Expression Parameter、VRChat 菜单分页、FX AnyState 或 AnimationClip 行为拿不准时，先查成熟社区教程、服装作者说明和官方文档。精确原子不存在或证据不足时返回 `capabilityGap=true` 并停止；禁止猜测、虚构工具、使用未获授权的付费服装或删除来源 Avatar。

## 1. 固定工程与扫描

1. 用 `vrcforge_list_avatars` 固定用户明确选择的 `projectPath`、场景和 Avatar。
2. 用 `vrcforge_read_avatar_descriptor` 读出实际绑定的 FX、Parameters 和 Menu，不替换 Facial Tracking、原模型资产或其他系统。
3. 依次调用 `vrcforge_scan_wardrobe`、`vrcforge_scan_parameters`、`vrcforge_scan_fx_animator`、`vrcforge_scan_avatar_controls`、`vrcforge_scan_animation_bindings`；记录已用 Int 值、状态、clip、菜单页、别名和对象开关。
4. 用 `vrcforge_scan_modular_avatar` 和 `vrcforge_inspect_skinned_mesh_bone_usage` 确认服装 prefab、Humanoid Hips、SkinnedMeshRenderer、rootBone 和实际受权重使用的骨骼；保留作者 prefab 自带 PhysBone、collider 和 constraint。

## 2. 装衣服并接入衣柜

1. 已在 Avatar 层级的整套服装：`vrcforge_preview_setup_outfit` → 用户当前权限策略 → `vrcforge_setup_outfit`，回读真正生成的 `ModularAvatarMergeArmature`。
2. 未实例化的完整 prefab：使用对应的 `vrcforge_preview_add_outfit` / `vrcforge_add_outfit`，避免重复 Setup Outfit；如果该原子已经接入衣柜，不再重复创建参数、状态或菜单。
3. 现有衣柜优先复用一个 saved、synced 的 `衣柜 : Int`，默认值 `0`；用 `vrcforge_preview_ensure_expression_parameter` / `vrcforge_ensure_expression_parameter` 校验或创建，不把每件衣服做成无关 Bool。
4. 用 `vrcforge_preview_add_wardrobe_outfit` / `vrcforge_add_wardrobe_outfit` 为用户选定的服装分配一个未占用值，保留非连续历史值、已有跨菜单别名和当前默认服装。

## 3. FX 和动画是衣柜的必要组成部分

1. 保留当前 Descriptor 绑定的 FX controller；服装通常使用 `AnyState → 衣柜 == N → State → Motion/Clip`，过渡时间 `0`、`hasExitTime=false`，不要改其他 layer。
2. 使用 `vrcforge_preview_ensure_animator_state` / `vrcforge_ensure_animator_state`，必要时通过 `vrcforge_preview_manage_fx_animator` / `vrcforge_manage_fx_animator` 精确处理已证明的状态或条件。
3. 扫描具体服装的启用、关闭和保持不变对象，分清内衣、袜子、尾巴、高跟与 Body BlendShape；只对有证据的属性使用 `vrcforge_preview_write_animation_curve` / `vrcforge_write_animation_curve`。
4. 换装 keyframe 应写在 `time=0`；不假设每套历史服装都有 OFF clip，也不凭猜测改胸型、鞋跟或原始身体。

## 4. 衣柜菜单同样由本 Skill 完成

1. 保留既有根菜单顺序，例如 `FacialTracking → 换装 → R18`；服装选项放在 `换装 / 衣服`，不重建根菜单或调整面捕。
2. 每页最多 8 个控制项，翻页 `下一页` 本身也占一个槽；空参数的 SubMenu 不是服装选项。
3. 对准确的 `衣柜 = N` 控件使用 `vrcforge_preview_ensure_expression_menu_control` / `vrcforge_ensure_expression_menu_control`；必要时使用现有 expression-menu 管理原子，但如果衣柜原子已经生成控件则不要重复。
4. 保留现有中文、日文、英文、图标、原始拼写、跨页面别名与没有暴露到菜单的参数。

## 5. 权限、检查点和验收

所有写入必须通过 VRCForge 已配置的当前权限策略；需要确认的配置等待确认，自动允许的配置沿用现有安全通道。每次写入仍绑定精确 preview、checkpoint、作用目标和写后回读；回滚需要单独确认。最后重新核对 `菜单 → Int → FX transition/state → clip curve → 服装对象 / 身体补偿`，运行 `vrcforge_run_validation_report` 和 `vrcforge_build_test_readiness`。不上传模型，不修改任务范围外的资产。
