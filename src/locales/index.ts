export const LOCALE_STORAGE_KEY = "vrcforge-locale";
export const DEFAULT_LOCALE = "en-US";

export const SUPPORTED_LOCALES = [
  { code: "zh-CN", label: "\u7b80\u4f53\u4e2d\u6587" },
  { code: "zh-TW", label: "\u7e41\u9ad4\u4e2d\u6587" },
  { code: "ja-JP", label: "\u65e5\u672c\u8a9e" },
  { code: "en-US", label: "English" },
] as const;

export type LocaleCode = (typeof SUPPORTED_LOCALES)[number]["code"];

const LOCALE_LOADERS: Record<LocaleCode, () => Promise<{ default: Record<string, unknown> }>> = {
  "en-US": () => import("./en-US.json"),
  "zh-CN": () => import("./zh-CN.json"),
  "zh-TW": () => import("./zh-TW.json"),
  "ja-JP": () => import("./ja-JP.json"),
};

export function normalizeLocaleCode(value?: string | null): LocaleCode {
  const code = String(value || "").trim();
  const exact = SUPPORTED_LOCALES.find((locale) => locale.code === code);
  if (exact) {
    return exact.code;
  }
  const lower = code.toLowerCase();
  if (lower.startsWith("zh-tw") || lower.startsWith("zh-hk") || lower.startsWith("zh-hant")) {
    return "zh-TW";
  }
  if (lower.startsWith("zh")) {
    return "zh-CN";
  }
  if (lower.startsWith("ja")) {
    return "ja-JP";
  }
  return DEFAULT_LOCALE;
}

export function detectInitialLocale(): LocaleCode {
  try {
    const saved = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    if (saved) {
      return normalizeLocaleCode(saved);
    }
  } catch {
    // Ignore blocked localStorage and fall back to the browser language.
  }
  return normalizeLocaleCode(window.navigator.language);
}

export async function loadLocaleMessages(code: string): Promise<{ code: LocaleCode; messages: Record<string, unknown> }> {
  const locale = normalizeLocaleCode(code);
  const module = await LOCALE_LOADERS[locale]();
  return { code: locale, messages: module.default };
}
