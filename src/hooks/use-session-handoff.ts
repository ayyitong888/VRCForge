import { useCallback, useState } from "react";
import { actOnSessionHandoff, consumeSessionHandoffContext } from "../lib/api/session-handoff";

export function useSessionHandoff(endpoint: string, targetChatId: string) {
  const [busyId, setBusyId] = useState("");
  const act = useCallback(async (handoffId: string, action: "accept" | "dismiss" | "cancel" | "pause" | "resume") => {
    setBusyId(handoffId);
    try { return await actOnSessionHandoff(endpoint, handoffId, action); }
    finally { setBusyId(""); }
  }, [endpoint]);
  const consume = useCallback((clientTurnId: string) => consumeSessionHandoffContext(endpoint, targetChatId, clientTurnId), [endpoint, targetChatId]);
  return { busyId, accept: (id: string) => act(id, "accept"), dismiss: (id: string) => act(id, "dismiss"), cancel: (id: string) => act(id, "cancel"), pause: (id: string) => act(id, "pause"), resume: (id: string) => act(id, "resume"), consume };
}
