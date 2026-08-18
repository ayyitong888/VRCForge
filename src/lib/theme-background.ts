import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { isTauriRuntime } from "./app-runtime";

const LEGACY_DATA_URL_PATTERN = /^data:image\/(png|jpeg|webp|gif);base64,/i;

export async function pickThemeBackground(): Promise<string | null> {
  if (!isTauriRuntime()) {
    throw new Error("Background images require the desktop App.");
  }
  return invoke<string | null>("pick_theme_background");
}

export async function clearThemeBackground(): Promise<void> {
  if (!isTauriRuntime()) return;
  await invoke("clear_theme_background");
}

export async function migrateLegacyThemeBackground(dataUrl: string): Promise<string> {
  const match = dataUrl.match(LEGACY_DATA_URL_PATTERN);
  if (!match || !isTauriRuntime()) {
    throw new Error("The previous background image cannot be migrated in this environment.");
  }
  const response = await fetch(dataUrl);
  const bytes = Array.from(new Uint8Array(await response.arrayBuffer()));
  return invoke<string>("import_legacy_theme_background", {
    bytes,
    extension: match[1].toLowerCase(),
  });
}

export function themeBackgroundAssetUrl(path: string): string {
  return path && isTauriRuntime() ? convertFileSrc(path) : "";
}
