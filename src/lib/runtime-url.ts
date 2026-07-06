export function isInternalRuntimeUrl(url: string): boolean {
  const normalizedPath = normalizeRuntimePath(url);
  if (/^\/(?:api|mcp)(?:\/|$)/i.test(normalizedPath)) {
    return true;
  }
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase();
    const loopbackHost = host === "127.0.0.1" || host === "localhost" || host === "::1" || host === "[::1]";
    return parsed.protocol === "http:" && loopbackHost && parsed.port === "8757" && /^\/(?:api|mcp)(?:\/|$)/i.test(normalizeRuntimePath(parsed.pathname));
  } catch {
    return false;
  }
}

function normalizeRuntimePath(path: string): string {
  try {
    return decodeURIComponent(path);
  } catch {
    return path;
  }
}
