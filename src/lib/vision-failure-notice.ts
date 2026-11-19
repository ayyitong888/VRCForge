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
  let reason = "notifications.visionFailed";
  switch (failure.errorType) {
    case "input_invalid": reason = "notifications.visionInput"; break;
    case "provider_rejected": reason = "notifications.visionRejected"; break;
    case "transient_provider_failure": reason = "notifications.visionBusy"; break;
  }
  const message = [translate(reason)];
  if (failure.retainImages === false) message.push(translate("notifications.visionReattach"));
  else if (failure.retainImages === true && failure.retryable === true) message.push(translate("notifications.visionRetryable"));
  return {
    kind: "vision",
    message: message.join(" "),
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
