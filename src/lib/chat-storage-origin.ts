let originId: string | null = null;

/** Stable only for this webview lifetime; used to suppress its own storage echo. */
export function chatStorageOriginId(): string {
  if (originId === null) {
    originId = globalThis.crypto.randomUUID();
  }
  return originId;
}
