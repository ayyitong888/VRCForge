export type SavedProviderKeyConfig = {
  provider?: string;
  apiKeyPresent?: boolean;
  savedKeyProviders?: readonly string[];
};

export function hasSavedProviderKey(
  provider: string,
  ...configs: Array<SavedProviderKeyConfig | null | undefined>
): boolean {
  const normalized = provider.trim().toLowerCase();
  if (!normalized) {
    return false;
  }
  return configs.some((config) => {
    if (!config) {
      return false;
    }
    if (config.savedKeyProviders?.some((entry) => entry.trim().toLowerCase() === normalized)) {
      return true;
    }
    return Boolean(config.apiKeyPresent && (config.provider || "").trim().toLowerCase() === normalized);
  });
}
