import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";
import type { AgentSkill, AgentSkillCheck, AgentSkillRegistry } from "./types";

export async function fetchSkills(endpoint: string): Promise<AgentSkillRegistry> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentSkillRegistry>("fetch_skills", {});
  }
  return requestJson<AgentSkillRegistry>(`${endpoint}/api/app/skills`);
}

export async function checkSkills(endpoint: string): Promise<AgentSkillCheck> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentSkillCheck>("check_skills", {});
  }
  return requestJson<AgentSkillCheck>(`${endpoint}/api/app/skills/check`);
}

export async function createSkill(endpoint: string, skill: Partial<AgentSkill>): Promise<AgentSkillRegistry & { skill: AgentSkill }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentSkillRegistry & { skill: AgentSkill }>("create_skill", {
      request: { body: skill, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/skills`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(skill),
  });
}

export async function updateSkill(endpoint: string, skillId: string, skill: Partial<AgentSkill>): Promise<AgentSkillRegistry & { skill: AgentSkill }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentSkillRegistry & { skill: AgentSkill }>("update_skill", {
      request: { id: skillId, body: skill, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/skills/${encodeURIComponent(skillId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(skill),
  });
}

export async function deleteSkill(endpoint: string, skillId: string): Promise<AgentSkillRegistry> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentSkillRegistry>("delete_skill", {
      request: { id: skillId, body: {}, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/skills/${encodeURIComponent(skillId)}`, {
    method: "DELETE",
  });
}
