const STORAGE_KEY = "vrcforge.agent-question.notification-sound.v1";
const MAX_DATA_URL_LENGTH = 4 * 1024 * 1024;

export type AgentQuestionNotificationSoundKind = "ordinary" | "blocking";
export type AgentQuestionNotificationSound = { enabled: boolean; dataUrl: string; name: string };
export type AgentQuestionNotificationSounds = Record<AgentQuestionNotificationSoundKind, AgentQuestionNotificationSound>;

const DEFAULT_SOUND: AgentQuestionNotificationSound = { enabled: false, dataUrl: "", name: "" };
export const DEFAULT_BLOCKING_NOTIFICATION_SOUND_URL = new URL("../assets/critical-notification.mp3", import.meta.url).href;
const defaultSound = (kind: AgentQuestionNotificationSoundKind): AgentQuestionNotificationSound => kind === "blocking"
  ? { enabled: true, dataUrl: DEFAULT_BLOCKING_NOTIFICATION_SOUND_URL, name: "" }
  : { ...DEFAULT_SOUND };
const DEFAULT_SOUNDS = (): AgentQuestionNotificationSounds => ({ ordinary: defaultSound("ordinary"), blocking: defaultSound("blocking") });
let activePreview: HTMLAudioElement | null = null;

function parseSound(value: unknown, kind: AgentQuestionNotificationSoundKind): AgentQuestionNotificationSound {
  if (!value || typeof value !== "object") return defaultSound(kind);
  const item = value as Partial<AgentQuestionNotificationSound>;
  if (item.enabled === false) return { ...DEFAULT_SOUND };
  if (item.enabled !== true || typeof item.dataUrl !== "string" || !item.dataUrl.startsWith("data:audio/")) return defaultSound(kind);
  return { enabled: true, dataUrl: item.dataUrl, name: typeof item.name === "string" ? item.name : "" };
}

export function loadAgentQuestionNotificationSounds(): AgentQuestionNotificationSounds {
  const result = DEFAULT_SOUNDS();
  if (typeof window === "undefined") return result;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const value = JSON.parse(raw) as Partial<AgentQuestionNotificationSounds>;
      result.ordinary = parseSound(value.ordinary, "ordinary");
      result.blocking = parseSound(value.blocking, "blocking");
      return result;
    }
  } catch {
    return DEFAULT_SOUNDS();
  }
  return result;
}

export function loadAgentQuestionNotificationSound(kind: AgentQuestionNotificationSoundKind = "blocking") {
  return loadAgentQuestionNotificationSounds()[kind];
}

export function saveAgentQuestionNotificationSounds(sounds: AgentQuestionNotificationSounds): boolean {
  if (typeof window === "undefined") return false;
  try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(sounds)); return true; } catch { return false; }
}

export function saveAgentQuestionNotificationSound(sound: AgentQuestionNotificationSound, kind: AgentQuestionNotificationSoundKind = "blocking"): boolean {
  const sounds = loadAgentQuestionNotificationSounds();
  sounds[kind] = sound.enabled ? sound : { ...DEFAULT_SOUND };
  return saveAgentQuestionNotificationSounds(sounds);
}

export function clearAgentQuestionNotificationSound(kind?: AgentQuestionNotificationSoundKind): boolean {
  if (typeof window === "undefined") return false;
  try {
    if (!kind) window.localStorage.removeItem(STORAGE_KEY);
    else { const sounds: Partial<AgentQuestionNotificationSounds> = loadAgentQuestionNotificationSounds(); delete sounds[kind]; window.localStorage.setItem(STORAGE_KEY, JSON.stringify(sounds)); }
    return true;
  } catch { return false; }
}

export function readAgentQuestionNotificationSoundFile(file: File): Promise<AgentQuestionNotificationSound> {
  return new Promise((resolve, reject) => {
    if (!file.type.startsWith("audio/")) { reject(new Error("Please choose an audio file.")); return; }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Unable to read the audio file."));
    reader.onload = () => {
      const dataUrl = typeof reader.result === "string" ? reader.result : "";
      if (!dataUrl.startsWith("data:audio/") || dataUrl.length > MAX_DATA_URL_LENGTH) { reject(new Error("Choose an audio file up to 3 MB.")); return; }
      resolve({ enabled: true, dataUrl, name: file.name });
    };
    reader.readAsDataURL(file);
  });
}

export async function playAgentQuestionNotificationSound(kind: AgentQuestionNotificationSoundKind = "blocking"): Promise<boolean> {
  const sound = loadAgentQuestionNotificationSound(kind);
  if (!sound.enabled || !sound.dataUrl || typeof Audio === "undefined") return false;
  stopAgentQuestionNotificationSound();
  const audio = new Audio(sound.dataUrl);
  activePreview = audio;
  audio.volume = 1;
  audio.onended = () => { if (activePreview === audio) activePreview = null; };
  try { await audio.play(); return true; } catch { if (activePreview === audio) activePreview = null; return false; }
}

export function stopAgentQuestionNotificationSound(): void {
  if (!activePreview) return;
  activePreview.pause(); activePreview.currentTime = 0; activePreview = null;
}

export const AGENT_QUESTION_NOTIFICATION_SOUND_STORAGE_KEY = STORAGE_KEY;
