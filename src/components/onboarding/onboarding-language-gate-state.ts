import { LOCALE_STORAGE_KEY, SUPPORTED_LOCALES } from "../../locales";
import { ONBOARDING_FLAG_KEY } from "../../lib/app-preferences";

export const ONBOARDING_LANGUAGE_GATE_FLAG_KEY = "vrcforge_onboarding_language_gate_completed";

type OnboardingStorage = Pick<Storage, "getItem" | "setItem">;

export type OnboardingStoredState = {
  onboardingComplete: boolean;
  hasSavedLocale: boolean;
  languageGateComplete: boolean;
};

export type OnboardingLaunchState = {
  showOnboarding: boolean;
  showLanguageGate: boolean;
  migrateLanguageGateCompletion: boolean;
};

export function readOnboardingStoredState(
  getStorage: () => OnboardingStorage = () => window.localStorage,
): OnboardingStoredState {
  try {
    const storage = getStorage();
    const savedLocale = storage.getItem(LOCALE_STORAGE_KEY);
    return {
      onboardingComplete: storage.getItem(ONBOARDING_FLAG_KEY) === "true",
      hasSavedLocale: SUPPORTED_LOCALES.some((locale) => locale.code === savedLocale),
      languageGateComplete: storage.getItem(ONBOARDING_LANGUAGE_GATE_FLAG_KEY) === "true",
    };
  } catch {
    return {
      onboardingComplete: false,
      hasSavedLocale: false,
      languageGateComplete: false,
    };
  }
}

export function resolveOnboardingLaunchState(
  stored: OnboardingStoredState,
  smokeMode: boolean,
): OnboardingLaunchState {
  if (smokeMode) {
    return {
      showOnboarding: false,
      showLanguageGate: false,
      migrateLanguageGateCompletion: false,
    };
  }
  const showOnboarding = !stored.onboardingComplete;
  return {
    showOnboarding,
    showLanguageGate: showOnboarding && !stored.hasSavedLocale && !stored.languageGateComplete,
    migrateLanguageGateCompletion:
      !stored.languageGateComplete && (stored.hasSavedLocale || stored.onboardingComplete),
  };
}

export function persistOnboardingLanguageGateCompletion(
  getStorage: () => OnboardingStorage = () => window.localStorage,
): boolean {
  try {
    getStorage().setItem(ONBOARDING_LANGUAGE_GATE_FLAG_KEY, "true");
    return true;
  } catch {
    return false;
  }
}
