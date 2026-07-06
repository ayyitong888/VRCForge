import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import {
  DEFAULT_LOCALE,
  LOCALE_STORAGE_KEY,
  SUPPORTED_LOCALES,
  detectInitialLocale,
  loadLocaleMessages,
  normalizeLocaleCode,
  type LocaleCode,
} from "./locales";

export { DEFAULT_LOCALE, LOCALE_STORAGE_KEY, SUPPORTED_LOCALES, type LocaleCode };

const loadedLocales = new Set<string>();

async function ensureLocaleLoaded(code: string): Promise<LocaleCode> {
  const locale = normalizeLocaleCode(code);
  if (!loadedLocales.has(locale)) {
    const payload = await loadLocaleMessages(locale);
    i18n.addResourceBundle(payload.code, "translation", payload.messages, true, true);
    loadedLocales.add(payload.code);
  }
  return locale;
}

export async function initializeI18n(): Promise<typeof i18n> {
  if (!i18n.isInitialized) {
    const fallback = await loadLocaleMessages(DEFAULT_LOCALE);
    loadedLocales.add(fallback.code);
    await i18n.use(initReactI18next).init({
      fallbackLng: DEFAULT_LOCALE,
      interpolation: { escapeValue: false },
      resources: { [fallback.code]: { translation: fallback.messages } },
      lng: DEFAULT_LOCALE,
    });
  }

  const initial = await ensureLocaleLoaded(detectInitialLocale());
  if (i18n.language !== initial) {
    await i18n.changeLanguage(initial);
  }
  return i18n;
}

export async function setLocale(code: string): Promise<void> {
  const locale = await ensureLocaleLoaded(code);
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Ignore blocked storage; the in-memory language still changes.
  }
  await i18n.changeLanguage(locale);
}

export default i18n;
