const DESKTOP_BACKEND_PORT = "8757";

export function isDesktopLoopbackApiUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "");
    return (
      parsed.protocol === "http:" &&
      isDesktopBackendLoopbackHost(host) &&
      parsed.port === DESKTOP_BACKEND_PORT &&
      (parsed.pathname === "/api" || parsed.pathname.startsWith("/api/"))
    );
  } catch {
    return false;
  }
}

function isDesktopBackendLoopbackHost(host: string): boolean {
  return (
    host === "127.0.0.1" ||
    host === "localhost" ||
    host === "::1" ||
    host === "::ffff:7f00:1" ||
    host === "::ffff:127.0.0.1" ||
    host === "0:0:0:0:0:ffff:7f00:1"
  );
}
