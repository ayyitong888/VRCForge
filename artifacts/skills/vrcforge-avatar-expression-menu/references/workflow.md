# VRChat 菜单制作：按用户范围和当前 Avatar 执行

## BOOTH 与作者资产证据闸门

在本工作流涉及作者资产、材质或贴图的安装、替换、适配或分配前，先从文件/文件夹名或用户提供的编号得到候选 BOOTH 商品 URL，并由当前宿主已公开的网页读取能力实际读取商品说明，核对标题、作者和目标模型/版本，再用 `vrcforge_list_directory`、`vrcforge_find_files`、`vrcforge_read_text_file` 读取当前资产目录中相关 README、安装说明、依赖与用途。公开 MCP 当前只保证上述三个本地读取工具；宿主未公开网页或图像读取能力时，商品页面正文或图示不能算作已读，必须报告 `capabilityGap=true` 并停止依赖该说明的写入；无关只读检查可以继续。

逐张确认图片属于完整底图、局部补充、overlay、decal、alpha 或 mask，不能仅凭 PNG 文件名或相似名称替换 MainTex。若作者说明要求合成，只能提出合成路线，不自动执行合成。付费资产保持本地，不下载或复制其内容；页面和包内文档是待评估资料，不是用户批准或写入授权。该闸门对当前工作流涉及的所有资产类型生效，只读取影响当前决策的说明，不枚举无关文档。

用户单独要求检查、创建或调整一个 expression menu 分类、子菜单、翻页或控件时使用。

## 适用范围与禁止事项

要求完整制作衣柜时不单独安装本 Skill；不要无请求地重建根菜单、替换现有面捕/表情入口、修改其他分类或参数域、批量重排、暴露隐藏参数或顺手创建动画。不存在适用于所有 Avatar 的固定根菜单结构或类别顺序。

## 拿不准时先查社区

涉及骨骼、参数、FX、动画、菜单、约束、PhysBone、Modular Avatar、NDMF 或作者 prefab 时，先查成熟社区教程、资产作者说明和官方文档。只使用已经存在的 VRCForge 原子工具；无法证明或没有精确安全原子时停止并报告 `capabilityGap=true`，不能编造功能或碰未经授权的付费资源。

## 1. 目标绑定与现状盘点

绑定用户指定 Avatar，读取 Descriptor 当前绑定的 Menu 与 Parameters。用户未提供 asset path 时，不因此阻塞：先从 Descriptor 获得根菜单引用，用 `vrcforge_get_asset_info` 按精确 asset path/GUID 读取 UTF-8 持久化文本（`includeText=true`）并沿控件的 submenu 引用，只跟进命中用户所述目标的分支。只有用户明确要求全树审计时才把全树扫描当作验收目标；目标仍无法定位时，可调用 `vrcforge_scan_avatar_controls`（schema 仅接受 `projectPath` / `avatarPath`，会扫描该 Avatar 菜单树）作为只读发现手段，并仅使用与请求匹配的路径，不把其他分支扩大为任务范围。

## 2. 真实结构和社区规则

菜单结构、顺序、语言、空格、大小写、图标、alias 和参数域均以当前 Avatar 的证据及用户明确范围为准；不将任何示例根菜单/分类顺序当成社区通用要求。每页最多 8 项，若分页项占位也计入 8 项上限；遵守各控件类型与参数类型匹配。

## 3. 精确原子与适用条件

确认目标资产路径、控件类型、参数类型/值、子菜单引用与用户要求的排序。若证据表明参数缺失或需改动，由 Agent 依据当前工程与用户意图拟定参数名、类型、默认值、Saved/Synced 行为及关联 Animator 行为，并将它们放入完整 preview 设计供用户批准；不要求用户预先知道或手写这些技术字段。批准设计明确参数名、类型与行为后，才使用 `vrcforge_preview_ensure_expression_parameter` / `vrcforge_ensure_expression_parameter`；若已有参数正确，不重复创建。新参数需与关联 Animator 参数类型匹配。空参数 SubMenu 可作为导航，不能仅凭序列化 value 推断为衣物选择器。

## 4. 执行边界与读取结果

确认用户只要求的精确分类、目标菜单资产/页面、控件类型和字段。页面/多项操作使用 `vrcforge_preview_manage_expression_menu` / `vrcforge_manage_expression_menu`；单控件使用 `vrcforge_preview_ensure_expression_menu_control` / `vrcforge_ensure_expression_menu_control`。每次写入都先 preview，经当前权限策略与用户批准后才执行。

将现有控件归入一个新分类时，用菜单管理工具建立子菜单，再按源控件的顺序、类型、值、主参数、子参数、图标和已有子菜单引用逐项建立。读取源控件完整的序列化字段，确认工具可保留其行为和显示后才迁移。读回目标控件后，再移除源页面的旧入口，保留它们引用的子菜单和参数。若有必须保留但工具无法表达的字段，暂停对应控件的迁移并说明具体字段。

## 5. 完整验收和停止条件

优先以 `vrcforge_get_asset_info` 对精确菜单/参数资产执行 `includeText=true`，确认文本完整 (`sourceComplete=true`) 后，从真实序列化源核对目标控件名称、类型、参数、值、图标与 submenu 引用，以及 Expression Parameters 项的类型/默认/Saved/Synced。该工具返回原始 UTF-8 文本和完整文本 Resource，不会把普通 asset metadata 伪装成控件字段；二进制/不可读资产不能据此通过。此时可用 `vrcforge_scan_avatar_controls`、`vrcforge_scan_parameters` 对指定 Avatar 做只读实际状态核验，并仅核对匹配请求的控件和参数；scanner 只有 `projectPath` / `avatarPath` 范围且菜单扫描会遍历整树，因此这是按需 fallback，不是局部读的强制步骤。scanner 不暴露图标字段时，图标读回仍属未验证。只对照批准的 delta；验证报告/build readiness 不等于构建或运行时验证。

<a id="linked-removal"></a>

## 删除菜单与参数的双向关联清理

删除一个功能的菜单控件时，同时列出它使用的参数及实际引用：相关菜单控件（含子参数）、Expression Parameters、Animator 参数、状态/转换/BlendTree/Parameter Driver，以及现场实际使用的 MA 等生成配置。只沿这些引用检查，不把全项目清理当成前提。删除子菜单入口时，检查其分支是否仍从其他入口可达；导航入口本身不一定有参数。

删菜单后，对不再被保留功能使用的关联参数，分别用 `vrcforge_preview_manage_expression_parameters` / `vrcforge_manage_expression_parameters` 和 `vrcforge_preview_manage_fx_animator` / `vrcforge_manage_fx_animator` 清理 Expression 与 Animator 两处声明；只删一处不算完成。反过来，用户删除参数时，用菜单管理工具一起删除或按用户目标改接引用它的控件，并处理仍引用该参数的驱动，不能留下失效开关或悬空引用。共享参数仍有保留用途时不连带删掉其他功能；说明保留原因，只有与用户明确删除目标实际冲突时才询问取舍。

FX 是否删除取决于实际用途：仅服务已删除功能且不再需要的层/状态可随之清理；仍用于物理、面捕、自动控制、默认外观或其他开关的部分保留，必要时只去掉该参数的条件或驱动。删层不等于删 AnimationClip；共享 clip 和子资产保留，不因菜单消失就删除源资产。使用当前工具实际支持的 action，沿用本次已授权范围、preview 和 checkpoint，不额外增加审批轮次或门禁。

写后回读受影响菜单、两类参数声明及相关驱动，确认删除项消失、保留引用有效，当前默认外观与保留开关未被改变。只有改动了 FX/动画行为时，才对受影响的开关/恢复路径做相应运行核验。

**每笔写入都遵守当前用户配置的权限策略，并绑定精确目标、preview、checkpoint 和写后回读；回滚必须单独确认。**

## 参考资料

- [VRChat 官方：Expressions Menu and Controls](https://creators.vrchat.com/avatars/expression-menu-and-controls/)
