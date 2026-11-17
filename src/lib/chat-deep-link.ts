import { createContext } from "react";

export const OpenChatLinkContext = createContext<((chatId: string) => void) | null>(null);

/** A link identifies an existing chat and carries no command or path. */
export function chatIdFromDeepLink(value: string): string | null {
  const match = /^vrcforge:\/\/chat\/([^/?#]+)$/i.exec(value);
  if (!match) return null;
  try {
    const id = decodeURIComponent(match[1]);
    return id && !/[\\/\x00-\x1f\x7f]/.test(id) && id !== "." && id !== ".." ? id : null;
  } catch {
    return null;
  }
}

export function chatDeepLink(chatId: string): string {
  const encoded = encodeURIComponent(chatId).replace(/[!'()*]/g, character =>
    "%" + character.charCodeAt(0).toString(16).toUpperCase(),
  );
  return `vrcforge://chat/${encoded}`;
}

export function isCodexChatDeepLink(value: string): boolean {
  return /^codex:\/\/threads\/[\da-fA-F]{8}(?:-[\da-fA-F]{4}){3}-[\da-fA-F]{12}$/.test(value);
}
