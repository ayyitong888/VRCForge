import type { AvatarEncryptionPlanResult } from "./api";

export function protectionPlanPayload(result: AvatarEncryptionPlanResult | null): Record<string, unknown> {
  const plan = result?.plan;
  return plan && typeof plan === "object" && !Array.isArray(plan) ? (plan as Record<string, unknown>) : {};
}
