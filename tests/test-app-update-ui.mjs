import assert from "node:assert/strict";
import fs from "node:fs";

const api = fs.readFileSync("src/lib/api/app-update.ts", "utf8");
const hook = fs.readFileSync("src/hooks/use-app-update.ts", "utf8");
const popup = fs.readFileSync("src/components/ui/app-update-popup.tsx", "utf8");
const settings = fs.readFileSync("src/components/settings/settings-workspace.tsx", "utf8");
const app = fs.readFileSync("src/App.tsx", "utf8");
const commands = fs.readFileSync("src-tauri/src/commands.rs", "utf8");
const service = fs.readFileSync("app_update_service.py", "utf8");
const gateway = fs.readFileSync("agent_gateway.py", "utf8");

assert.match(api, /check_app_update/);
assert.match(api, /\/api\/app\/update/);
assert.match(api, /timeoutMs: 4000/);
assert.doesNotMatch(api, /manual|periodic|searchParams|download|install/i);

// The check runs exactly once when the runtime becomes available.
assert.match(hook, /startedRef\.current/);
assert.match(hook, /checkAppUpdate\(endpoint, controller\.signal\)/);
assert.match(hook, /result\.shouldNotify/);
assert.match(hook, /onUpdateAvailable\(result\)/);
assert.doesNotMatch(hook, /notifyAppUpdate|app-update-notifications|setTimeout|setInterval|checkManually|useState|retry|manual|periodic/i);

// A successful newer-version result surfaces as an in-app dialog, never a
// Windows system notification and never a permission request.
assert.match(app, /const \[appUpdatePrompt, setAppUpdatePrompt\] = useState<AppUpdateResult \| null>\(null\);/);
assert.match(app, /useAppUpdate\(endpoint, runtimeConnected, setAppUpdatePrompt\)/);
assert.match(app, /<AppUpdatePopup result=\{appUpdatePrompt\} onDismiss=\{\(\) => setAppUpdatePrompt\(null\)\} \/>/);
assert.doesNotMatch(app, /appUpdateResult|checkingAppUpdate|checkAppUpdateManually|onCheckAppUpdate/);
assert.equal(fs.existsSync("src/lib/app-update-notifications.ts"), false);
assert.doesNotMatch(popup, /sendNotification|requestPermission|isPermissionGranted|@tauri-apps\/plugin-notification/);
assert.match(popup, /role="dialog"/);
assert.match(popup, /data-vrcforge-app-update-popup/);
assert.match(popup, /data-vrcforge-app-update-dismiss/);
assert.match(popup, /data-vrcforge-app-update-open/);
assert.match(popup, /releaseUrl/);
assert.match(popup, /onMouseDown=/);
assert.match(popup, /event\.currentTarget === event\.target/);
assert.doesNotMatch(settings, /AppUpdate|app-update|check.*update/i);
assert.equal(fs.existsSync("src/components/settings/app-update-settings.tsx"), false);

assert.match(commands, /"\/api\/app\/update"/);
assert.match(commands, /Some\(4_000\)/);
assert.doesNotMatch(commands, /App update check mode|mode=periodic|mode=manual/);
assert.doesNotMatch(service, /DEFAULT_PERIODIC|start_periodic|_periodic_loop|last_notified|manual|periodic/i);

// Update checks are App infrastructure, never an Agent tool surface.
assert.doesNotMatch(gateway, /app_update|check_app_update/i);

for (const locale of ["en-US", "ja-JP", "zh-CN", "zh-TW"]) {
  const messages = JSON.parse(fs.readFileSync(`src/locales/${locale}.json`, "utf8"));
  for (const key of ["dialogTitle", "dialogBody", "openRelease"]) {
    assert.equal(typeof messages.appUpdate?.[key], "string", `${locale} appUpdate.${key}`);
    assert.ok(messages.appUpdate[key].length > 0, `${locale} appUpdate.${key} should be nonempty`);
  }
  assert.equal(typeof messages.appUpdate?.notificationTitle, "undefined", `${locale} must not retain notification copy`);
  assert.equal(typeof messages.appUpdate?.notificationBody, "undefined", `${locale} must not retain notification copy`);
}

console.log("startup-only in-app app update UI contracts passed");
