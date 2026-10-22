import type { ConversationItem } from "./chat-types";

/**
 * Messages are grouped by a human readable time marker after this quiet
 * period.  This is a product presentation choice, not a measured runtime
 * threshold; keep it explicit so it can be changed without touching parsing.
 */
export const MESSAGE_DATE_SEPARATOR_THRESHOLD_MS = 30 * 60 * 1000;

export function parseMessageTimestamp(value?: string): number | undefined {
  if (!value) return undefined;
  const parsed = Date.parse(value);
  if (Number.isFinite(parsed)) return parsed;
  const match = value.match(/(?:^|[^0-9])([0-9]{13})(?:[^0-9]|$)/);
  if (!match) return undefined;
  const timestamp = Number(match[1]);
  return Number.isFinite(timestamp) ? timestamp : undefined;
}

export function formatLocalMessageTime(value: string | number | undefined, language?: string): string {
  const timestamp = typeof value === "number" ? value : parseMessageTimestamp(value);
  if (timestamp === undefined || !Number.isFinite(timestamp)) return "";
  return new Intl.DateTimeFormat(language || undefined, { hour: "2-digit", minute: "2-digit" }).format(new Date(timestamp));
}

export function formatMessageDateSeparator(value: string | number | undefined, language?: string): string {
  const timestamp = typeof value === "number" ? value : parseMessageTimestamp(value);
  if (timestamp === undefined || !Number.isFinite(timestamp)) return "";
  const date = new Date(timestamp);
  const today = new Date();
  const startOfDay = (candidate: Date) => new Date(candidate.getFullYear(), candidate.getMonth(), candidate.getDate()).getTime();
  const relativeDays = Math.round((startOfDay(date) - startOfDay(today)) / 86_400_000);
  const time = formatLocalMessageTime(timestamp, language);
  if (relativeDays === 0 || relativeDays === -1) {
    const relative = new Intl.RelativeTimeFormat(language || undefined, { numeric: "auto" }).format(relativeDays, "day");
    return `${relative} ${time}`;
  }
  return new Intl.DateTimeFormat(language || undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function shouldShowMessageDateSeparator(
  previous: number | undefined,
  current: number | undefined,
  thresholdMs = MESSAGE_DATE_SEPARATOR_THRESHOLD_MS,
): boolean {
  if (current === undefined) return false;
  if (previous === undefined) return true;
  const previousDate = new Date(previous);
  const currentDate = new Date(current);
  const crossedLocalDate = previousDate.getFullYear() !== currentDate.getFullYear()
    || previousDate.getMonth() !== currentDate.getMonth()
    || previousDate.getDate() !== currentDate.getDate();
  return crossedLocalDate || current - previous >= thresholdMs;
}

export function conversationItemTimestamp(item: ConversationItem): number | undefined {
  if (item.type === "timeline_event") return parseMessageTimestamp(item.event.timestamp || item.createdAt);
  if (item.type === "agent") return parseMessageTimestamp(item.completedAt || item.response.completedAt);
  return parseMessageTimestamp("createdAt" in item ? item.createdAt : undefined);
}
