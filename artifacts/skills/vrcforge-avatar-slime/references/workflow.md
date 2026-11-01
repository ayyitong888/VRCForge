# 操作说明

## 1. 当前目标与资产
读取项目、场景和 Avatar；执行已获授权的本次修改，不因仍有无关问题就扩大范围。复用已有目标绑定，发生真实 Editor/domain reload 或目标重建时按产品机制重新绑定。读取 Renderer 的全部材质槽与共享用途、Shader 来源及有效渲染队列；原始队列 -1 表示采用 Shader 默认，不能拿 -1 排序。

按实际网格区分：身体表面、头部皮肤、眼球/睫毛/高光、口腔/牙齿、隐藏备用表情、头发和 FakeShadow、尾巴和饰品。查看作者原始配置与相邻状态，不把某一模型的路径、数字或骨骼名当作通用表。

用户资产先读随附说明；有商品编号且本次操作依赖商品说明时读作者商品页，确认适配与用途。贴图区分底图、overlay、alpha、mask 与法线；不凭文件名替换 MainTex，不自动购买或分发付费素材。需要新水波贴图时先复用已授权的平铺法线；没有合适素材时如实说明。texture_patch 只支持有限 PNG 局部补丁，不生成任意图像；新纹理制作由宿主已有图像能力承担，再经正式 MCP 导入/分配，不用 Shell 写 Unity 文件。

## 2. 材质、光泽与水波
使用已有资产复制入口创建独立工作材质；确认真实 Shader 类型后选透明模式，保留正常外观还原路线。肤色蓝色等颜色按当前用户要求选择，#66CCFF 只是可选示例，不是默认强制色。

用实际声明的 smoothness、reflection/rim、normal 强度与 UV 参数调节湿润胶体感；优先一个可平铺法线和缓慢滚动，限制重复层、过强法线和高频闪动。不要为水波直接采用 GrabPass/模糊折射；需要真实折射时先查看当前 Shader 实现、移动/VR 支持及实际开销。头发保留发束轮廓与集中高光，尾巴保留原 alpha 轮廓，眼睛保留清晰五官。

具体部位选择、UV 分离、水波循环和材质/动画交接，按 [表面、过渡与问题处理](surface-and-transition.md) 操作。不要滚动面部底图来制造水波，也不要把改变表面法线当作一定能看见内部物体的证明。

头部透明时要从正面、斜侧、后脑和下方按疑点看备用眼片、口腔与牙齿。优先利用实际子材质、局部 alpha、Stencil 与深度设计，让正常表情仍能出现；不可把整个面捕/表情系统关掉作为修复。

## 3. 透明叠层诊断
先读发生冲突的各层队列、ZWrite/ZTest、Cull、Stencil 和局部 alpha，读 Shader/preset 的确切文本补足语义。改变与当前缺陷有关的最小相干范围，不把所有透明材质统一排到同一个大数值。

- 粉色断面、缺失刘海：检查 FakeShadow 的投影和深度写入是否先于晚画的头发，参考作者原始顺序与 Stencil 清除规则。修复应保持投影只落在适当表面，而不是擅自删除假阴影。
- 眼镜遮挡头发或脸：分辨折射采样与普通透明叠层；不需要光学扭曲时可为当前镜片创建普通透明工作材质，保留适度高光，按实际队列及深度测试检查效果。
- 头内备用表情露出：查清备用几何与真实表情的驱动、面部深度覆盖及遮罩；不能只调 opacity 掩盖，也不能用后脑单角度通过代替正面与客户端验证。

所有数字均从当前模型、作者配置和已验证改动推导；本说明不规定固定 Render Queue、Stencil ID 或 Shader 私有字段。

## 4. 正常/Slime 与平滑过渡
读取现有 MA、FX、参数、菜单与动画绑定。选择一条可维护的驱动链：已有正常/胶体材质配置可以复用；用户要求平滑切换时，材质选择与连续属性过渡需要配合，避免只瞬间换材质。不为已可用效果再复制整套常驻身体网格。

创建/复用用户约定的开关及菜单位置，参数的默认、saved/synced 按当前约定；新功能默认保留正常外观。不用“R18”或某个参数名当成平台要求。MA 引用与嵌套字段走现有专用/序列化入口，驱动关系按现场决定，避免多条 FX 同时写同一属性。

通过精确 clipPaths + bindingSelectors 读取将修改的绑定；多条已确认修改可用真实 schema 支持的原子批次。Color/Vector 动画把全部需保留的分量写全；保留现有时间线与明确的双向恢复端点。在 GM 中测试开、关、过渡中途反向、来回换装，不用静态材质值声称 PropertyBlock/动画值正确。

与衣柜组合测试时检查身体仍显示、胸形/脚跟/袜口/吊带补偿跟随实际衣服；仅 Slime 修改不要重写这些补偿。用户明确要求删除功能时，菜单与 Expression/Animator 参数双向清理，FX 根据真实共享引用决定，不顺手删公共资产。

## 5. 验收与交付
选取能看清问题的多角度和局部近景，包含头发、脸、后脑、身体与尾巴；按用户既有验收约定覆盖，复用有效图片，不固定重复所有截图。为“内部能否看见”放置已授权的正常测试物或临时比较头像，实际穿入胶体并拍轮廓；这证明渲染可见性，不证明 VRChat 同步碰撞。临时对象用受监督工具处理，结束恢复正常默认及测试前的编辑状态；用户未授权的删除/回滚不自行执行。

读取 Console、NDMF 和 SDK 各自的报告。NDMF no_report、SDK panel_not_open 都不等于零错误；缓存报告不是新构建验证。报告若提供原生 Auto Fix，先读取当前 action 与作用对象、预览，再在本次授权内执行；只执行确实相关的动作，不自动修复所有警告。当前 NDMF 只支持 InlineErrorWithAutofix，自定义 error UI 动作不能假装可调用。回调完成与警告消失分开报告，不自动 build/上传来补证据。

给用户实际可见的截图/附件及准确结论：已写入、运行检查、视觉结果、性能证据、客户端待验分别说明。当前捕获可能覆盖 managed latest PNG，使用已有宿主/产品附件机制保留交付副本；只做项目外图片归档，不用它绕过 Unity 资产写入。仅在用户另有上传授权时上传。

## 参考
按当前 Shader/插件版本核对，复用仍适用的已读资料。
- lilToon 基本设置：https://lilxyzw.github.io/lilToon/ja_JP/base/base.html
- lilToon FakeShadow：https://lilxyzw.github.io/lilToon/ja_JP/base/fakeshadow.html
- lilToon Stencil：https://lilxyzw.github.io/lilToon/ja_JP/advanced/stencil.html
- lilToon 折射：https://lilxyzw.github.io/lilToon/ja_JP/advanced/refraction.html
- Unity Renderer.GetPropertyBlock：https://docs.unity3d.com/2022.3/Documentation/ScriptReference/Renderer.GetPropertyBlock.html
- Unity Mipmap Streaming：https://docs.unity3d.com/2022.3/Documentation/Manual/TextureStreaming.html

