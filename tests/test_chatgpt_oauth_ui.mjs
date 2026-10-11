import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../src/features/chatgpt-oauth/settings.tsx", import.meta.url), "utf8");

function resolveLoginAccountId(accounts, selectedAccountId) {
  return accounts?.some((account) => account.id === selectedAccountId) ? selectedAccountId : "";
}

assert.equal(resolveLoginAccountId([], "stale-account"), "");
assert.equal(resolveLoginAccountId(undefined, "stale-account"), "");
assert.equal(resolveLoginAccountId([{ id: "current-account" }], "stale-account"), "");
assert.equal(resolveLoginAccountId([{ id: "current-account" }], "current-account"), "current-account");
assert.equal(resolveLoginAccountId([], ""), "");

const regularLogin = source.match(
  /onClick=\{\(\) => void runAction\(\s*"login",([\s\S]*?)\)\s*\)\}/,
);
assert.ok(regularLogin, "regular ChatGPT login button should have an action");
assert.match(regularLogin[1], /status\?\.accounts\.some\(\(account\) => account\.id === selectedAccountId\)/);
assert.match(regularLogin[1], /\?\s*selectedAccountId\s*:\s*""/);
assert.match(
  source,
  /\(\) => beginChatGptOAuthLogin\(endpoint, ""\)/,
  "add-account button must always start dynamic registration",
);

console.log("ChatGPT OAuth button account-selection state matrix: red/green contract passed");
