import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";

export type InputPredictionContext = {
  recent_user_messages: string[];
  assistant_tail: string;
  provider: string;
  model: string;
  reasoning_effort?: string;
};

export async function requestInputPrediction(endpoint: string, context: InputPredictionContext, signal: AbortSignal) {
  const request_id = crypto.randomUUID();
  const body = { ...context, request_id };
  const baseUrl = `${endpoint}/api/app/chat/input-prediction`;
  const desktop = hasTauriInternals();
  const cancel = () => {
    const pendingCancel = desktop
      ? invokeTauriWithAbort("cancel_chat_input_prediction", { request: { body: { request_id } } })
      : requestJson(`${baseUrl}/cancel`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ request_id }),
          timeoutMs: 2000,
        });
    void pendingCancel.catch(() => undefined);
  };
  if (signal.aborted) return null;
  signal.addEventListener("abort", cancel, { once: true });
  try {
    if (desktop) {
      return await invokeTauriWithAbort<{ suggestion: string | null }>("generate_chat_input_prediction", {
        request: { body },
      }, signal);
    }
    return await requestJson<{ suggestion: string | null }>(baseUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
      timeoutMs: 12000,
    });
  } finally {
    signal.removeEventListener("abort", cancel);
  }
}
