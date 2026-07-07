import type { OptimizationPlannerReport } from "./api";

export type OptimizationActionCardItem = NonNullable<OptimizationPlannerReport["actionCards"]>[number];

export type OptimizationActionOptions = {
  atlasTargetMaterials?: string;
  rendererPath?: string;
  relativeVertexCount?: string;
};

function optimizationRequestSignature(card: OptimizationActionCardItem): string {
  return `${card.id || ""} ${card.requestTool || ""} ${card.title || ""}`.toLowerCase();
}

export function isTttOptimizationRequest(card: OptimizationActionCardItem): boolean {
  const signature = optimizationRequestSignature(card);
  return signature.includes("ttt") || signature.includes("textrans") || signature.includes("atlas");
}

export function isMeshiaOptimizationRequest(card: OptimizationActionCardItem): boolean {
  return optimizationRequestSignature(card).includes("meshia");
}

function splitOptimizationOptionLines(value?: string): string[] {
  return String(value || "")
    .split(/[\n,;]+/g)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function buildOptimizationRequestOptions(card: OptimizationActionCardItem, options: OptimizationActionOptions): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  if (isTttOptimizationRequest(card)) {
    payload.atlasTargetMaterials = splitOptimizationOptionLines(options.atlasTargetMaterials);
  }
  if (isMeshiaOptimizationRequest(card)) {
    payload.rendererPath = String(options.rendererPath || "").trim();
    const ratio = Number(options.relativeVertexCount || "0.9");
    if (Number.isFinite(ratio)) {
      payload.relativeVertexCount = ratio;
    }
  }
  return payload;
}

export function optimizationActionMissingRequiredOptions(card: OptimizationActionCardItem, options: OptimizationActionOptions): boolean {
  if (isTttOptimizationRequest(card)) {
    return splitOptimizationOptionLines(options.atlasTargetMaterials).length === 0;
  }
  if (isMeshiaOptimizationRequest(card)) {
    return !String(options.rendererPath || "").trim();
  }
  return false;
}
