import type { AgentRuntimeResponse } from "./api/types";

export type VisionFailureNotice = {
  kind: "vision";
  message: string;
};

type TranslateNotice = (key: string) => string;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function managedProviderError(response: AgentRuntimeResponse): Record<string, unknown> | null {
  if (response.skill?.tool !== "vrcforge_vision_audit_multi" || !isRecord(response.skill.result)) {
    return null;
  }
  const rows = Array.isArray(response.skill.result.results) ? response.skill.result.results : [];
  for (const row of rows) {
    if (isRecord(row) && isRecord(row.providerError)) {
      return row.providerError;
    }
  }
  return null;
}

function renderFailure(
  failure: Record<string, unknown>,
  translate: TranslateNotice,
): VisionFailureNotice {
  const provider = String(failure.providerLabel || failure.provider || "").trim();
  const model = String(failure.model || "").trim();
  const route = [provider, model].filter(Boolean).join(" · ");
  const error = String(failure.error || translate("notifications.visionFailed")).trim();
  const retry = failure.retryable === true
    ? translate("notifications.visionRetryable")
    : translate("notifications.visionReattach");
  return {
    kind: "vision",
    message: `${route ? `${route}: ` : ""}${error} ${retry}`.trim(),
  };
}

export function projectVisionFailureNotice(
  response: AgentRuntimeResponse,
  translate: TranslateNotice,
): VisionFailureNotice | null {
  if (response.vision?.status === "error") {
    return renderFailure(response.vision as unknown as Record<string, unknown>, translate);
  }
  const providerError = managedProviderError(response);
  return providerError ? renderFailure(providerError, translate) : null;
}
