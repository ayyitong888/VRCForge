import { invoke } from "@tauri-apps/api/core";
import { isDesktopLoopbackApiUrl } from "../desktop-routing";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail?: unknown,
  ) {
    super(message);
  }
}

let appSessionToken = "";

export function setAppSessionToken(token: string) {
  appSessionToken = token.trim();
}

type JsonRequestInit = RequestInit & { timeoutMs?: number; preferTauriIpc?: boolean };

export async function requestJson<T>(url: string, init: JsonRequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timeoutMs = init.timeoutMs ?? 30000;
  const timeout = timeoutMs > 0 ? window.setTimeout(() => controller.abort(), timeoutMs) : undefined;
  const headers = new Headers(init.headers);
  if (appSessionToken && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${appSessionToken}`);
  }
  let response: Response;
  try {
    const { timeoutMs: _timeoutMs, preferTauriIpc: _preferTauriIpc, ...fetchInit } = init;
    const tauriLocalApiRequest = isTauriLocalApiUrl(url);
    if (tauriLocalApiRequest) {
      throw new ApiError("This desktop route has not been migrated to a typed IPC command.", 0);
    }
    response = await fetch(url, { ...fetchInit, headers, signal: init.signal ?? controller.signal });
  } catch (cause) {
    if (cause instanceof ApiError) {
      throw cause;
    }
    if (cause instanceof DOMException && cause.name === "AbortError") {
      if (init.signal?.aborted) {
        throw new ApiError("Request cancelled.", 0);
      }
      throw new ApiError(`Request timed out after ${timeoutMs / 1000}s`, 0);
    }
    throw new ApiError(
      `VRCForge runtime is not reachable at ${runtimeOriginFromUrl(url)}. Use Retry to start the local backend, or open Doctor for logs and repair steps.`,
      0,
      cause instanceof Error ? cause.message : String(cause),
    );
  } finally {
    if (timeout !== undefined) {
      window.clearTimeout(timeout);
    }
  }
  const text = await response.text();
  let payload: unknown = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      const excerpt = text.slice(0, 300);
      throw new ApiError(`HTTP ${response.status}: response was not JSON`, response.status, excerpt);
    }
  }
  if (!response.ok) {
    const detail = typeof payload === "object" && payload ? (payload as { detail?: unknown }).detail : payload;
    throw new ApiError(typeof detail === "string" ? detail : `HTTP ${response.status}`, response.status, detail);
  }
  return payload as T;
}

function isTauriLocalApiUrl(url: string): boolean {
  return hasTauriInternals() && isDesktopLoopbackApiUrl(url);
}

export function hasTauriInternals(): boolean {
  return typeof window !== "undefined" && Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
}

export function invokeTauriWithAbort<T>(command: string, args: Record<string, unknown>, signal?: AbortSignal): Promise<T> {
  if (!signal) {
    return invoke<T>(command, args);
  }
  if (signal.aborted) {
    return Promise.reject(new ApiError("Request cancelled.", 0));
  }
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const cleanup = () => signal.removeEventListener("abort", onAbort);
    const onAbort = () => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      reject(new ApiError("Request cancelled.", 0));
    };
    signal.addEventListener("abort", onAbort, { once: true });
    invoke<T>(command, args)
      .then((value) => {
        if (!settled) {
          settled = true;
          cleanup();
          resolve(value);
        }
      })
      .catch((error) => {
        if (!settled) {
          settled = true;
          cleanup();
          reject(error);
        }
      });
  });
}

function runtimeOriginFromUrl(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.protocol}//${parsed.host}`;
  } catch {
    return "the configured endpoint";
  }
}
