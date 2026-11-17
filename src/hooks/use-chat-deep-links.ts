import { getCurrent, onOpenUrl } from "@tauri-apps/plugin-deep-link";
import { useEffect, useRef, useState } from "react";
import { hasTauriInternals } from "../lib/api/http";
import { chatIdFromDeepLink } from "../lib/chat-deep-link";

export function useChatDeepLinks(ready: boolean, openChatById: (id: string) => void) {
  const open = useRef(openChatById);
  open.current = openChatById;
  const [pending, setPending] = useState<string[]>([]);

  useEffect(() => {
    if (!hasTauriInternals()) return;
    let active = true;
    let receivedEvent = false;
    let unlisten: (() => void) | undefined;
    const receive = (urls: string[]) => {
      if (!active) return false;
      const ids = urls.map(chatIdFromDeepLink).filter(id => id !== null);
      if (!ids.length) return false;
      setPending(ids);
      return true;
    };
    // Read the startup URL once. Project changes can hydrate the catalog again.
    void onOpenUrl((urls) => { if (receive(urls)) receivedEvent = true; }).then(async (cleanup) => {
      if (!active) { cleanup(); return; }
      unlisten = cleanup;
      const urls = await getCurrent();
      if (!receivedEvent && urls) receive(urls);
    }).catch((cause) => { if (active) console.error("[vrcforge:deep-link]", cause); });
    return () => { active = false; unlisten?.(); };
  }, []);

  useEffect(() => {
    if (!ready || !pending.length) return;
    setPending([]);
    pending.forEach(id => open.current(id));
  }, [ready, pending]);
}
