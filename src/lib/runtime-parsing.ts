import type { AgentRuntimeResponse, AgentShellResult } from "./api";

export function asRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

export function getHealthDetailNumber(detail: unknown, key: string): number {
  const record = asRecord(detail);
  if (!record) {
    return 0;
  }
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function isAgentShellResult(value: unknown): value is AgentShellResult {
  const payload = asRecord(value) as Partial<AgentShellResult> | null;
  return Boolean(payload && typeof payload.command === "string" && typeof payload.exitCode === "number");
}

export function approvalIdFromResponse(response: AgentRuntimeResponse): string {
  return String(
    response.approval_id ||
      response.approvalId ||
      response.write?.approval_id ||
      response.write?.approvalId ||
      response.shell?.approval_id ||
      response.shell?.approvalId ||
      response.shell?.approval?.id ||
      "",
  ).trim();
}
