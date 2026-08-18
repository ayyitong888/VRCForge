import assert from "node:assert/strict";
import {
  formatThemeColor,
  parseThemeColor,
  readableForegroundForHsl,
  themeColorToHsl,
} from "../src/lib/theme-color.ts";

assert.equal(parseThemeColor("#D97757"), "#d97757");
assert.equal(parseThemeColor("#abc"), "#aabbcc");
assert.equal(parseThemeColor("rgb(217, 119, 87)"), "#d97757");
assert.equal(parseThemeColor("217 119 87"), "#d97757");
assert.equal(parseThemeColor("hsl(0 100% 50%)"), "#ff0000");
assert.equal(parseThemeColor("hsl(360deg, 100%, 50%)"), "#ff0000");
assert.equal(parseThemeColor("rgb(300, 0, 0)"), null);
assert.equal(parseThemeColor("hsl(0 101% 50%)"), null);
assert.equal(parseThemeColor("rgba(1, 2, 3, 0.5)"), null);

assert.equal(formatThemeColor("#d97757", "hex"), "#D97757");
assert.equal(formatThemeColor("#d97757", "rgb"), "rgb(217, 119, 87)");
assert.match(formatThemeColor("#d97757", "hsl"), /^hsl\(.+% .+%\)$/);

const terracotta = themeColorToHsl("#d97757");
assert.ok(terracotta);
assert.ok(terracotta.h >= 0 && terracotta.h <= 360);
assert.equal(readableForegroundForHsl({ h: 60, s: 100, l: 50 }), "0 0% 8%");
assert.equal(readableForegroundForHsl({ h: 240, s: 100, l: 30 }), "0 0% 100%");

console.log("theme color conversion contract: ok");
