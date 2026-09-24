import { asRecord } from "./runtime-parsing";

type HealthComponentLike = {
  status?: string;
  detail?: unknown;
} | null | undefined;

export function isVrcForgeUnityToolsReady(
  runtimeConnected: boolean,
  tools: HealthComponentLike,
): boolean {
  if (!runtimeConnected || tools?.status !== "ok") {
    return false;
  }
  const detail = asRecord(tools.detail) ?? {};
  const readiness = asRecord(detail.readiness);
  return readiness?.ready === true;
}
