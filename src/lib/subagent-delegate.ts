/**
 * /delegate 斜杠指令解析（子代理技能委派域）。
 *
 * 语法：
 * - "/delegate 描述任务……"                         → 旧行为，默认角色
 * - "/delegate vrcforge_xxx 可选的任务描述……"       → 直接工具委派
 * - "/delegate runtime-diagnostics 参数……"          → 已加载 registry skill
 * - "/delegate skill:read-only-avatar-audit 参数……"  → 显式 registry skill
 *
 * 普通首词只有在当前 registry 中存在时才被识别，避免误判任务描述；
 * skill: 前缀可在 registry 尚未加载时显式指定。最终可用性和 allowlist
 * 仍由网关裁决，前端不复制执行策略。
 */
const DELEGATE_TOOL_PATTERN = /^vrcforge_[a-z0-9_]+$/i;
const DELEGATE_SKILL_ID_PATTERN = /^[a-z0-9][a-z0-9_.-]*$/i;
const EXPLICIT_SKILL_PATTERN = /^skill:([a-z0-9][a-z0-9_.-]*)$/i;

export type DelegateCommand = {
  toolName?: string;
  targetKind?: "tool" | "skill";
  task: string;
};

export function parseDelegateCommand(raw: string, knownSkillNames: Iterable<string> = []): DelegateCommand {
  const text = raw.replace(/^\/delegate\s*/i, "").trim();
  if (!text) {
    return { task: "" };
  }
  const [first, ...rest] = text.split(/\s+/);
  if (DELEGATE_TOOL_PATTERN.test(first)) {
    return { toolName: first.toLowerCase(), targetKind: "tool", task: rest.join(" ").trim() };
  }
  const explicitSkill = first.match(EXPLICIT_SKILL_PATTERN)?.[1] || "";
  if (explicitSkill) {
    return { toolName: explicitSkill.toLowerCase(), targetKind: "skill", task: rest.join(" ").trim() };
  }
  const knownSkills = new Set(
    Array.from(knownSkillNames, (name) => String(name || "").trim().toLowerCase()).filter((name) => DELEGATE_SKILL_ID_PATTERN.test(name)),
  );
  if (knownSkills.has(first.toLowerCase())) {
    return { toolName: first.toLowerCase(), targetKind: "skill", task: rest.join(" ").trim() };
  }
  return { task: text };
}
