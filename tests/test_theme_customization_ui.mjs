import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const app = read("src/App.tsx");
const settings = read("src/components/settings/settings-workspace.tsx");
const panel = read("src/components/settings/theme-customization-panel.tsx");
const preference = read("src/lib/theme-customization.ts");
const background = read("src/lib/theme-background.ts");
const hook = read("src/hooks/use-theme-customization.ts");
const css = read("src/theme.css");
const rust = read("src-tauri/src/theme_background.rs");
const tauriConfig = JSON.parse(read("src-tauri/tauri.conf.json"));
const en = JSON.parse(read("src/locales/en-US.json"));
const zhCn = JSON.parse(read("src/locales/zh-CN.json"));
const zhTw = JSON.parse(read("src/locales/zh-TW.json"));
const ja = JSON.parse(read("src/locales/ja-JP.json"));

assert.match(app, /useThemeCustomization\(\)/);
assert.match(settings, /<ThemeCustomizationPanel/);

// Multi-colour palettes replace the single-accent-only surface while keeping
// an explicit custom option for users upgrading from 1.7.1.
for (const palette of ["ocean", "violet", "sakura", "forest", "sunset", "custom"]) {
  assert.match(preference, new RegExp(`"${palette}"`));
  if (palette !== "custom") assert.match(css, new RegExp(`data-vrcforge-palette="${palette}"`));
}
assert.match(panel, /THEME_PALETTES\.map/);
assert.match(panel, /aria-pressed=\{selected\}/);
assert.match(panel, /value\.palette === "custom"/);

// New backgrounds are selected and copied by the native App. localStorage
// contains only a managed path and preferences, never the selected image.
assert.match(background, /invoke<string \| null>\("pick_theme_background"\)/);
assert.match(background, /convertFileSrc\(path\)/);
assert.match(preference, /backgroundImagePath: string/);
assert.match(hook, /themeBackgroundAssetUrl\(customization\.backgroundImagePath\)/);
assert.match(rust, /persist_theme_background_file/);
assert.match(rust, /remove_managed_backgrounds\(&theme_dir, Some\(&destination\)\)/);
assert.match(rust, /pub\(crate\) fn clear_theme_background/);
assert.doesNotMatch(panel, /FileReader|readAsDataURL|MAX_THEME_BACKGROUND_BYTES/);

// The previous Base64 record is a one-time compatibility input only. Once it
// is copied successfully, the hook persists the managed file path instead.
assert.match(preference, /loadLegacyThemeBackgroundDataUrl/);
assert.match(background, /import_legacy_theme_background/);
assert.match(hook, /backgroundImagePath/);

// Visibility is the full user-facing 0-100% range.
assert.match(panel, /min="0"/);
assert.match(panel, /max="1"/);
assert.match(preference, /Math\.min\(1, Math\.max\(0, opacity\)\)/);

// This action restores all theme settings and removes the managed background;
// it is not presented as the ambiguous "reset theme" action.
assert.equal(en.settings.themeReset, "Restore defaults");
assert.equal(zhCn.settings.themeReset, "恢复默认");
assert.equal(zhTw.settings.themeReset, "恢復預設");
assert.equal(ja.settings.themeReset, "デフォルトに戻す");
for (const locale of [en, zhCn, zhTw, ja]) {
  assert.doesNotMatch(JSON.stringify(locale.settings), /2\s*MB/i);
}

assert.equal(tauriConfig.app.security.assetProtocol.enable, true);
assert.deepEqual(tauriConfig.app.security.assetProtocol.scope, [
  "$LOCALDATA/VRCForge/agentic-app/theme/**/*",
  "$DATA/VRCForge/agentic-app/theme/**/*",
]);
assert.match(tauriConfig.app.security.csp, /asset:/);

console.log("theme customization UI contract: ok");
