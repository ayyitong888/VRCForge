import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const settings = readFileSync(new URL("../src/components/settings/settings-workspace.tsx", import.meta.url), "utf8");
const panel = readFileSync(new URL("../src/components/settings/theme-customization-panel.tsx", import.meta.url), "utf8");
const preference = readFileSync(new URL("../src/lib/theme-customization.ts", import.meta.url), "utf8");
const hook = readFileSync(new URL("../src/hooks/use-theme-customization.ts", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/theme.css", import.meta.url), "utf8");

assert.match(app, /useThemeCustomization\(\)/);
assert.match(settings, /<ThemeCustomizationPanel/);
assert.match(panel, /type="color"/);
assert.match(panel, /accept="image\/png,image\/jpeg,image\/webp,image\/gif"/);
assert.match(panel, /MAX_THEME_BACKGROUND_BYTES/);
assert.match(preference, /vrcforge_theme_customization/);
assert.match(preference, /2 \* 1024 \* 1024/);
assert.match(hook, /--primary/);
assert.match(hook, /--vrcforge-background-image/);
assert.match(css, /data-vrcforge-wallpaper="active"/);

console.log("theme customization UI contract: ok");
