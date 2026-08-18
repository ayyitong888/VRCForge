import assert from "node:assert/strict";
import fs from "node:fs";

const api = fs.readFileSync("src/lib/api/app-update.ts", "utf8");
const hook = fs.readFileSync("src/hooks/use-app-update.ts", "utf8");
const popup = fs.readFileSync("src/components/ui/app-update-popup.tsx", "utf8");
const settings = fs.readFileSync("src/components/settings/settings-workspace.tsx", "utf8");
const app = fs.readFileSync("src/App.tsx", "utf8");
const commands = fs.readFileSync("src-tauri/src/commands.rs", "utf8");
const tauriMain = fs.readFileSync("src-tauri/src/main.rs", "utf8");
const service = fs.readFileSync("app_update_service.py", "utf8");
const gateway = fs.readFileSync("agent_gateway.py", "utf8");
const preferences = fs.readFileSync("src/lib/app-preferences.ts", "utf8");
const tauriConfig = JSON.parse(fs.readFileSync("src-tauri/tauri.conf.json", "utf8"));

assert.match(api, /check_app_update/);
assert.match(api, /open_app_release_url/);
assert.match(api, /\/api\/app\/update/);
assert.match(api, /timeoutMs: 4000/);
assert.match(api, /refresh/);
assert.doesNotMatch(api, /periodic|download|install/i);

// Automatic checking runs exactly once when enabled and the runtime becomes
// available. Explicit tray checks reuse the same API with a fresh request.
assert.match(hook, /startedRef\.current/);
assert.match(hook, /automaticCheckEnabled/);
assert.match(hook, /checkAppUpdate\(endpoint, controller\.signal, false\)/);
assert.match(hook, /checkAppUpdate\(endpoint, undefined, true\)/);
assert.match(hook, /result\.shouldNotify/);
assert.match(hook, /onUpdateAvailable\(result\)/);
assert.doesNotMatch(hook, /notifyAppUpdate|app-update-notifications|setTimeout|setInterval|useState|retry|periodic/i);

// A successful newer-version result surfaces as an in-app dialog, never a
// Windows system notification and never a permission request.
assert.match(app, /const \[appUpdatePrompt, setAppUpdatePrompt\] = useState<AppUpdatePromptState \| null>\(null\);/);
assert.match(app, /listen\("vrcforge-tray-check-update"/);
assert.match(app, /checkForAppUpdateNow/);
assert.match(app, /setAutomaticUpdateCheckEnabled/);
assert.match(preferences, /AUTOMATIC_UPDATE_CHECK_DISABLED_KEY = "vrcforge_automatic_update_check_disabled"/);
assert.match(preferences, /loadAutomaticUpdateCheckEnabled/);
assert.match(preferences, /persistAutomaticUpdateCheckEnabled/);
assert.match(preferences, /localStorage\.setItem\(AUTOMATIC_UPDATE_CHECK_DISABLED_KEY, "true"\)/);
assert.equal(tauriConfig.identifier, "app.vrcforge.agentic");
assert.equal(fs.existsSync("src/lib/app-update-notifications.ts"), false);
assert.doesNotMatch(popup, /sendNotification|requestPermission|isPermissionGranted|@tauri-apps\/plugin-notification/);
assert.match(popup, /role="dialog"/);
assert.match(popup, /data-vrcforge-app-update-popup/);
assert.match(popup, /data-vrcforge-app-update-dismiss/);
assert.match(popup, /data-vrcforge-app-update-open/);
assert.match(popup, /data-vrcforge-disable-automatic-update-check/);
assert.match(popup, /releaseUrl/);
assert.match(popup, /openAppReleaseUrl/);
assert.doesNotMatch(popup, /target="_blank"/);
assert.match(popup, /onMouseDown=/);
assert.match(popup, /event\.currentTarget === event\.target/);
assert.doesNotMatch(settings, /AppUpdate|app-update|check.*update/i);
assert.equal(fs.existsSync("src/components/settings/app-update-settings.tsx"), false);

assert.match(commands, /"\/api\/app\/update"/);
assert.match(commands, /Some\(4_000\)/);
assert.match(commands, /open_app_release_url/);
assert.match(commands, /validate_app_release_url/);
assert.match(commands, /ShellExecuteW/);
assert.match(commands, /request\.refresh/);
assert.match(tauriMain, /MenuItem::with_id\(app, "open_chat", "前往对话"/);
assert.match(tauriMain, /MenuItem::with_id\(app, "show", "显示主窗口"/);
assert.match(tauriMain, /MenuItem::with_id\(app, "check_update", "检查更新"/);
assert.match(tauriMain, /vrcforge-tray-check-update/);
assert.doesNotMatch(service, /DEFAULT_PERIODIC|start_periodic|_periodic_loop|last_notified|periodic/i);

// Update checks are App infrastructure, never an Agent tool surface.
assert.doesNotMatch(gateway, /app_update|check_app_update/i);

for (const locale of ["en-US", "ja-JP", "zh-CN", "zh-TW"]) {
  const messages = JSON.parse(fs.readFileSync(`src/locales/${locale}.json`, "utf8"));
  for (const key of [
    "dialogTitle",
    "dialogBody",
    "openRelease",
    "upToDateTitle",
    "upToDateBody",
    "failedTitle",
    "failedBody",
    "disableAutomaticCheck",
    "openReleaseFailed",
  ]) {
    assert.equal(typeof messages.appUpdate?.[key], "string", `${locale} appUpdate.${key}`);
    assert.ok(messages.appUpdate[key].length > 0, `${locale} appUpdate.${key} should be nonempty`);
  }
  assert.equal(typeof messages.appUpdate?.notificationTitle, "undefined", `${locale} must not retain notification copy`);
  assert.equal(typeof messages.appUpdate?.notificationBody, "undefined", `${locale} must not retain notification copy`);
}

console.log("automatic and tray app update UI contracts passed");
