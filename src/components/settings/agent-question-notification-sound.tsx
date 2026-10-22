import { Music2, RotateCcw, Upload } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  clearAgentQuestionNotificationSound,
  loadAgentQuestionNotificationSounds,
  playAgentQuestionNotificationSound,
  readAgentQuestionNotificationSoundFile,
  saveAgentQuestionNotificationSound,
  type AgentQuestionNotificationSoundKind,
  type AgentQuestionNotificationSounds,
  stopAgentQuestionNotificationSound,
} from "../../lib/agent-question-notification-sound";
import { Button } from "../ui/button";

export function AgentQuestionNotificationSoundSettings() {
  const { t } = useTranslation();
  const [sounds, setSounds] = useState<AgentQuestionNotificationSounds>(loadAgentQuestionNotificationSounds);
  const [message, setMessage] = useState("");

  async function onFileChange(kind: AgentQuestionNotificationSoundKind, file: File | undefined) {
    if (!file) return;
    try {
      const next = await readAgentQuestionNotificationSoundFile(file);
      if (!saveAgentQuestionNotificationSound(next, kind)) throw new Error(t("settings.notificationSoundStorageError"));
      setSounds(loadAgentQuestionNotificationSounds());
      setMessage(t("settings.notificationSoundSaved"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("settings.notificationSoundReadError"));
    }
  }

  function reset(kind: AgentQuestionNotificationSoundKind) {
    stopAgentQuestionNotificationSound();
    if (!clearAgentQuestionNotificationSound(kind)) { setMessage(t("settings.notificationSoundStorageError")); return; }
    setSounds(loadAgentQuestionNotificationSounds());
    setMessage(t("settings.notificationSoundReset"));
  }

  const row = (kind: AgentQuestionNotificationSoundKind, label: string) => {
    const sound = sounds[kind];
    return (
      <div className="rounded-lg border border-border bg-background p-3" data-notification-sound-kind={kind}>
        <div className="text-sm font-medium">{label}</div>
        <div className="mt-1 text-xs text-muted-foreground">{sound.enabled ? sound.name || t("settings.notificationSoundBlockingDefault") : t("settings.notificationSoundWindowsDefault")}</div>
        <div className="mt-3 flex flex-wrap gap-2">
          <label className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-border px-3 py-2 text-sm hover:bg-accent">
            <Upload className="h-4 w-4" />{t("settings.notificationSoundChoose")}
            <input type="file" accept="audio/*" className="sr-only" onChange={(event) => { void onFileChange(kind, event.currentTarget.files?.[0]); event.currentTarget.value = ""; }} />
          </label>
          <Button type="button" variant="outline" disabled={!sound.enabled} onClick={() => { void playAgentQuestionNotificationSound(kind).then((played) => { if (!played) setMessage(t("settings.notificationSoundPreviewError")); }); }}>
            {t("settings.notificationSoundPreview")}
          </Button>
          <Button type="button" variant="outline" disabled={!sound.enabled} onClick={() => reset(kind)}>
            <RotateCcw className="h-4 w-4" />{t("settings.notificationSoundRestore")}
          </Button>
        </div>
      </div>
    );
  };

  return (
    <section aria-labelledby="agent-question-notification-sound-title">
      <h2 id="agent-question-notification-sound-title" className="text-base font-semibold"><Music2 className="mr-1.5 inline-block h-4 w-4 align-text-bottom" />{t("settings.notificationSoundTitle")}</h2>
      <p className="mt-1 text-sm text-muted-foreground">{t("settings.notificationSoundDesc")}</p>
      <div className="mt-4 grid gap-3">
        {row("ordinary", t("settings.notificationSoundOrdinary"))}
        {row("blocking", t("settings.notificationSoundBlocking"))}
      </div>
      {message ? <p role="status" className="mt-2 text-xs text-muted-foreground">{message}</p> : null}
    </section>
  );
}
