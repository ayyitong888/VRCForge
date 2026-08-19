import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const app = read("src/App.tsx");
const settings = read("src/components/settings/settings-workspace.tsx");
const panel = read("src/components/settings/theme-customization-panel.tsx");
const colorEditor = read("src/components/settings/theme-color-editor.tsx");
const themeColor = read("src/lib/theme-color.ts");
const preference = read("src/lib/theme-customization.ts");
const background = read("src/lib/theme-background.ts");
const hook = read("src/hooks/use-theme-customization.ts");
const css = read("src/theme.css");
const layoutSplitter = read("src/components/workspace/layout-splitter.tsx");
const rust = read("src-tauri/src/theme_background.rs");
const backend = read("src-tauri/src/backend.rs");
const offlineInstaller = read("installer/VRCForge_Offline_Installer_x64.nsi");
const webInstaller = read("installer/VRCForge_Web_Installer_x64.nsi");
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
assert.match(panel, /themeCustomSurface/);
assert.match(panel, /recentColors = \[color, \.\.\.value\.recentColors/);
assert.match(preference, /surfaceColor: string/);
assert.match(preference, /recentColors: string\[\]/);
assert.match(preference, /\.slice\(0, 3\)/);
assert.match(hook, /recentColors: current\.recentColors/);

// Both custom seeds expose a native palette, editable HEX/RGB/HSL values and
// the platform eyedropper when available. The stored color remains opaque.
assert.match(colorEditor, /type="color"/);
assert.match(colorEditor, /const FORMATS: readonly ThemeColorFormat\[\] = \["hex", "rgb", "hsl"\]/);
assert.match(colorEditor, /window as Window & \{ EyeDropper\?/);
assert.match(colorEditor, /new eyeDropper\(\)\.open\(\)/);
assert.match(colorEditor, /error\.name !== "AbortError"/);
assert.match(colorEditor, /event\.nativeEvent\.isComposing/);
assert.match(colorEditor, /Blur owns the single commit/);
assert.doesNotMatch(colorEditor, /finishDraft\(format\);\s*event\.currentTarget\.blur/);
assert.doesNotMatch(colorEditor, /onPreview/);
assert.doesNotMatch(themeColor, /rgba|hsla/);
assert.match(css, /data-vrcforge-palette="custom"\]\[data-vrcforge-custom-surfaces="active"\]/);
assert.match(css, /--vrcforge-custom-accent-light-foreground/);
assert.match(hook, /readableForegroundForHsl/);

// New backgrounds are selected and copied by the native App. localStorage
// contains only a managed path and preferences, never the selected image.
assert.match(background, /invoke<string \| null>\("pick_theme_background"\)/);
assert.match(background, /convertFileSrc\(path\)/);
assert.match(preference, /backgroundImagePath: string/);
assert.match(preference, /backgroundScope: ThemeBackgroundScope/);
assert.match(preference, /THEME_BACKGROUND_SCOPE_IDS = \["workspace", "app"\]/);
assert.match(preference, /backgroundScope: "workspace"/);
assert.match(hook, /themeBackgroundAssetUrl\(customization\.backgroundImagePath\)/);
assert.match(hook, /root\.dataset\.vrcforgeWallpaperScope = customization\.backgroundScope/);
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

// Users can keep the image in the center workspace or extend one continuous
// background across the whole App, including both sidebars.
assert.match(panel, /BACKGROUND_SCOPE_OPTIONS\.map/);
assert.match(panel, /role="radio"/);
assert.match(panel, /aria-checked=\{selected\}/);
assert.match(css, /data-vrcforge-wallpaper-scope="workspace"\] \.bg-workspace/);
assert.match(css, /data-vrcforge-wallpaper-scope="app"\] #root > main/);
assert.match(css, /data-vrcforge-wallpaper-scope="app"\] \.bg-sidebar/);
assert.match(
  css,
  /data-vrcforge-wallpaper-scope="app"\] #root > main\s*\{\s*background-image:\s*linear-gradient\(\s*hsl\(var\(--workspace\) \/ var\(--vrcforge-wallpaper-scrim\)\),\s*hsl\(var\(--workspace\) \/ var\(--vrcforge-wallpaper-scrim\)\)\s*\),\s*var\(--vrcforge-background-image\)/,
  "the app wallpaper and scrim must be composited once on their shared parent",
);
assert.match(
  css,
  /#root > main > div > \.bg-workspace,\s*html\[data-vrcforge-wallpaper="active"\]\[data-vrcforge-wallpaper-scope="app"\] #root > main > div > \.bg-sidebar,\s*html\[data-vrcforge-wallpaper="active"\]\[data-vrcforge-wallpaper-scope="app"\] #root > main > div > \[data-layout-splitter\]\s*\{\s*background-color: transparent;/,
  "the center, sidebars, and both resize hit areas must reveal the same parent composite",
);
const appPaneTransparencyRule = css.match(
  /#root > main > div > \.bg-workspace,[\s\S]*?#root > main > div > \[data-layout-splitter\]\s*\{([\s\S]*?)\}/,
)?.[1] ?? "";
assert.doesNotMatch(
  appPaneTransparencyRule,
  /background-(?:image|position|repeat|size)\s*:/,
  "resizable panes must never own or reposition the whole-App wallpaper",
);
assert.match(css, /data-vrcforge-wallpaper-scope="app"\] \.bg-sidebar\s*\{\s*border-left-color: transparent;\s*border-right-color: transparent;/);
assert.match(css, /data-vrcforge-wallpaper-scope="app"\] \[data-layout-splitter\] > div\s*\{\s*background-color: transparent;/);
assert.match(layoutSplitter, /data-layout-splitter=\{side\}/);
assert.match(layoutSplitter, /cursor-col-resize touch-none bg-transparent/);
assert.equal(en.settings.themeBackgroundScopeWorkspace, "Center workspace only");
assert.equal(en.settings.themeBackgroundScopeApp, "Entire app (including sidebars)");
assert.equal(zhCn.settings.themeBackgroundScopeWorkspace, "仅中间工作区");
assert.equal(zhCn.settings.themeBackgroundScopeApp, "整个应用（含左右栏）");
assert.equal(en.settings.themeCustomSurface, "Background base color");
assert.equal(zhCn.settings.themeCustomSurface, "背景基色");
assert.equal(zhCn.settings.themeRecentColors, "最近使用");

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

// Theme choices are personal data. Upgrades keep the stable WebView identity
// that owns localStorage and keep the managed background under the persistent
// user-data root. Only the explicit uninstall checkbox may clear that root.
assert.equal(tauriConfig.identifier, "app.vrcforge.agentic");
assert.match(backend, /join\("VRCForge"\)\.join\("agentic-app"\)/);
for (const installer of [offlineInstaller, webInstaller]) {
  assert.match(installer, /!define USER_DATA_RELATIVE "VRCForge\\agentic-app"/);
  assert.match(installer, /\$\{If\} \$ClearUserData == \$\{BST_CHECKED\}[\s\S]*RMDir \/r "\$UserDataRoot"/);
  assert.match(installer, /\$\{NSD_SetState\} \$ClearUserDataCheckbox \$\{BST_UNCHECKED\}/);
}

console.log("theme customization UI contract: ok");
