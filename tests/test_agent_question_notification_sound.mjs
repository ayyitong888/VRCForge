import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function makeStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

test("ordinary defaults to Drop 003 and each custom sound can be reset independently", async () => {
  globalThis.window = { localStorage: makeStorage() };
  const module = await import(pathToFileURL(path.join(root, "src/lib/agent-question-notification-sound.ts")).href);
  const ordinaryDefault = module.loadAgentQuestionNotificationSound("ordinary");
  assert.equal(ordinaryDefault.enabled, true);
  assert.match(ordinaryDefault.dataUrl, /\/drop-003\.ogg$/);
  assert.equal(module.loadAgentQuestionNotificationSound().dataUrl, module.DEFAULT_BLOCKING_NOTIFICATION_SOUND_URL);
  const sound = { enabled: true, dataUrl: "data:audio/wav;base64,AAAA", name: "local.wav" };
  module.saveAgentQuestionNotificationSound(sound, "ordinary");
  assert.deepEqual(module.loadAgentQuestionNotificationSound("ordinary"), sound);
  module.saveAgentQuestionNotificationSound(sound, "blocking");
  module.clearAgentQuestionNotificationSound("blocking");
  assert.deepEqual(module.loadAgentQuestionNotificationSound("ordinary"), sound);
  assert.equal(module.loadAgentQuestionNotificationSound("blocking").dataUrl, module.DEFAULT_BLOCKING_NOTIFICATION_SOUND_URL);
  module.saveAgentQuestionNotificationSound(sound, "blocking");
  module.clearAgentQuestionNotificationSound("ordinary");
  assert.deepEqual(module.loadAgentQuestionNotificationSound("ordinary"), ordinaryDefault);
  assert.deepEqual(module.loadAgentQuestionNotificationSound("blocking"), sound);
  module.clearAgentQuestionNotificationSound();
  assert.equal(module.loadAgentQuestionNotificationSound().enabled, true);
  assert.deepEqual(module.loadAgentQuestionNotificationSound("ordinary"), ordinaryDefault);
  assert.match(module.AGENT_QUESTION_NOTIFICATION_SOUND_STORAGE_KEY, /notification-sound/);
  window.localStorage.setItem(module.AGENT_QUESTION_NOTIFICATION_SOUND_STORAGE_KEY, JSON.stringify({
    ordinary: { enabled: false, dataUrl: "", name: "" },
    blocking: sound,
  }));
  assert.deepEqual(module.loadAgentQuestionNotificationSound("ordinary"), ordinaryDefault);
  assert.deepEqual(module.loadAgentQuestionNotificationSound("blocking"), sound);
});

test("background playback stops the previous custom preview and tolerates autoplay rejection", async () => {
  globalThis.window = { localStorage: makeStorage() };
  const module = await import(pathToFileURL(path.join(root, "src/lib/agent-question-notification-sound.ts")).href);
  module.saveAgentQuestionNotificationSound({ enabled: true, dataUrl: "data:audio/wav;base64,AAAA", name: "local.wav" });
  const audios = [];
  globalThis.Audio = class {
    constructor() { this.pauseCount = 0; audios.push(this); }
    play() { return Promise.reject(new Error("autoplay blocked")); }
    pause() { this.pauseCount += 1; }
    currentTime = 0;
  };
  const first = module.playAgentQuestionNotificationSound();
  const second = module.playAgentQuestionNotificationSound();
  await Promise.all([first, second]);
  assert.equal(audios.length, 2);
  assert.equal(audios[0].pauseCount, 1);
});
