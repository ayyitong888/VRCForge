/**
 * /delegate 斜杠指令解析（子代理技能委派域）。
 *
 * 语法：
 * - "/delegate 描述任务……"                         → 旧行为，默认角色
 * - "/delegate vrcforge_xxx 可选的任务描述……"       → skill_delegate 角色，
 *   首个以 vrcforge_ 开头的 token 视为技能名，其余作为任务描述。
 *
 * 只识别 vrcforge_ 前缀，避免把普通英文首词误判成技能名；
 * 技能是否存在/是否被 allowlist 放行由网关裁决，前端不复制策略。
 */
const DELEGATE_TOOL_PATTERN = /^vrcforge_[a-z0-9_]+$/i;

export type DelegateCommand = {
  toolName?: string;
  task: string;
};

export function parseDelegateCommand(raw: string): DelegateCommand {
  const text = raw.replace(/^\/delegate\s*/i, "").trim();
  if (!text) {
    return { task: "" };
  }
  const [first, ...rest] = text.split(/\s+/);
  if (DELEGATE_TOOL_PATTERN.test(first)) {
    return { toolName: first.toLowerCase(), task: rest.join(" ").trim() };
  }
  return { task: text };
}
