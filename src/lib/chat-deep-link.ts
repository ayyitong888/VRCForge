import { createContext } from "react";

export const OpenChatLinkContext = createContext<((chatId: string) => void) | null>(null);
export const ChatLinkTitlesContext = createContext<ReadonlyMap<string, string> | null>(null);

export const CHAT_LINK_CLASS = "text-blue-600 dark:text-blue-400 underline underline-offset-2";

const CHAT_LINK_SOURCE = String.raw`(?<![^\s(<\[{"“‘：，。！？、（【「『])vrcforge:\/\/chat\/[a-z\d._~%-]+(?![a-z\d._~%/?#-])`;

export function chatLinksInText(text: string, titles: ReadonlyMap<string, string>) {
  return Array.from(text.matchAll(new RegExp(CHAT_LINK_SOURCE, "gi"))).flatMap(match => {
    const token = match[0];
    const href = titles.has(chatIdFromDeepLink(token) || "") ? token : token.replace(/\.+$/, "");
    const chatId = chatIdFromDeepLink(href);
    const title = chatId ? titles.get(chatId) : undefined;
    return chatId && title ? [{ start: match.index, href, chatId, title }] : [];
  });
}

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
