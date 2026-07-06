export const FALLBACK_ENDPOINT = "http://127.0.0.1:8757";

export function isTauriRuntime() {
  return "__TAURI_INTERNALS__" in window;
}

export function isAbsoluteLocalPath(path?: string): boolean {
  const value = (path || "").trim();
  return /^[a-zA-Z]:[\\/]/.test(value) || value.startsWith("\\\\") || value.startsWith("/");
}

export function isRuntimeSessionVerificationError(message: string): boolean {
  const normalized = message.toLowerCase();
  return (
    normalized.includes("runtime session verification failed") ||
    normalized.includes("local runtime was replaced") ||
    normalized.includes("does not accept this desktop session")
  );
}
