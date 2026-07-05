const DESKTOP_BACKEND_PORT = "8757";

export function isDesktopLoopbackApiUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "");
    const isBackendLoopback = host === "127.0.0.1" || host === "localhost" || host === "::1";
    return (
      parsed.protocol === "http:" &&
      isBackendLoopback &&
      parsed.port === DESKTOP_BACKEND_PORT &&
      (parsed.pathname === "/api" || parsed.pathname.startsWith("/api/"))
    );
  } catch {
    return false;
  }
}
