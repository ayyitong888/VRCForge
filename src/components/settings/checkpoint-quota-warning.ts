import type { CheckpointArchiveUsage } from "../../lib/api/connectors";

export type CheckpointQuotaWarning = {
  key: string;
  sizeBytes: number;
  maxSizeMb: number;
  excessBytes: number;
};

export function checkpointQuotaWarning(usage: CheckpointArchiveUsage): CheckpointQuotaWarning | null {
  const sizeBytes = usage.sizeBytes;
  const maxSizeMb = usage.maxSizeMb;
  if (usage.ok !== true || !Number.isFinite(sizeBytes) || !Number.isFinite(maxSizeMb)
    || typeof sizeBytes !== "number" || typeof maxSizeMb !== "number" || maxSizeMb <= 0) {
    return null;
  }
  const excessBytes = sizeBytes - maxSizeMb * 1024 * 1024;
  return excessBytes > 0 ? { key: `${usage.directory || ""}|${maxSizeMb}`, sizeBytes, maxSizeMb, excessBytes } : null;
}

export function shouldShowCheckpointQuotaWarning(next: CheckpointQuotaWarning, acknowledged: CheckpointQuotaWarning | null): boolean {
  return !acknowledged || next.key !== acknowledged.key || next.sizeBytes > acknowledged.sizeBytes;
}
