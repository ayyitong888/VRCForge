import { getCurrent, onOpenUrl } from "@tauri-apps/plugin-deep-link";
import { useEffect, useRef } from "react";
import { hasTauriInternals } from "../lib/api/http";

/** External links select an existing chat; they never carry commands or paths. */
export function chatIdFromDeepLink(value: string): string | null {
  const match = /^vrcforge:\/\/chat\/([^/?#]+)$/i.exec(value);
  if (!match) return null;
  try {
    const id = decodeURIComponent(match[1]);
    return id && !/[\\/\x00-\x1f\x7f]/.test(id) && id !== "." && id !== ".." ? id : null;
  } catch { return null; }
}

export function useChatDeepLinks(ready: boolean, openChatById: (id: string) => void) {
  const open = useRef(openChatById);
  open.current = openChatById;
  useEffect(() => {
    if (!ready || !hasTauriInternals()) return;
    let active = true;
    let receivedEvent = false;
    let unlisten: (() => void) | undefined;
    const receive = (urls: string[]) => {
      if (!active) return;
      for (const url of urls) {
        const id = chatIdFromDeepLink(url);
        if (id) open.current(id);
      }
    };
    // Subscribe before reading startup URLs. The plugin retains the latest URL
    // while chat storage hydrates; an event racing getCurrent wins over that read.
    void onOpenUrl((urls) => { receivedEvent = true; receive(urls); }).then(async (cleanup) => {
      if (!active) { cleanup(); return; }
      unlisten = cleanup;
      const urls = await getCurrent();
      if (!receivedEvent && urls) receive(urls);
    }).catch((cause) => { if (active) console.error("[vrcforge:deep-link]", cause); });
    return () => { active = false; unlisten?.(); };
  }, [ready]);
}
