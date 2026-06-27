import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

import enUS from "./locales/en-US.json";
import zhCN from "./locales/zh-CN.json";
import zhTW from "./locales/zh-TW.json";
import jaJP from "./locales/ja-JP.json";

export const SUPPORTED_LOCALES = [
  { code: "zh-CN", label: "简体中文" },
  { code: "zh-TW", label: "繁體中文" },
  { code: "ja-JP", label: "日本語" },
  { code: "en-US", label: "English" },
] as const;

export type LocaleCode = (typeof SUPPORTED_LOCALES)[number]["code"];

const LOCALE_STORAGE_KEY = "vrcforge-locale";

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      "zh-CN": { translation: zhCN },
      "zh-TW": { translation: zhTW },
      "ja-JP": { translation: jaJP },
      "en-US": { translation: enUS },
    },
    fallbackLng: "en-US",
    interpolation: { escapeValue: false },
    detection: {
      order: ["localStorage"],
      lookupLocalStorage: LOCALE_STORAGE_KEY,
      caches: ["localStorage"],
    },
  });

export function setLocale(code: string) {
  window.localStorage.setItem(LOCALE_STORAGE_KEY, code);
  i18n.changeLanguage(code);
}

export default i18n;
