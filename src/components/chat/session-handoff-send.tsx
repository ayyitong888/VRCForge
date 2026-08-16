import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { sendSessionHandoff } from "../../lib/api/session-handoff";
import { Button } from "../ui/button";

export type SessionHandoffTargetChat = {
  id: string;
  title: string;
};

export function SessionHandoffSend({
  endpoint,
  sourceChatId,
  targetChats,
  open,
  onOpenChange,
}: {
  endpoint: string;
  sourceChatId: string;
  targetChats: SessionHandoffTargetChat[];
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const [targetChatId, setTargetChatId] = useState("");
  const [goal, setGoal] = useState("");
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const boundedTargets = useMemo(() => {
    const seen = new Set<string>();
    return targetChats.slice(0, 32).filter((target) => {
      const id = target.id.trim();
      if (!id || id === sourceChatId || seen.has(id)) return false;
      seen.add(id);
      return true;
    });
  }, [sourceChatId, targetChats]);
  const selectedTargetId = boundedTargets.some((target) => target.id === targetChatId) ? targetChatId : "";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const boundedGoal = goal.trim().slice(0, 2000);
    if (!sourceChatId || !selectedTargetId || !boundedGoal || status === "sending") return;
    setStatus("sending");
    try {
      await sendSessionHandoff(endpoint, {
        sourceChatId,
        targetChatId: selectedTargetId,
        payload: {
          goal: boundedGoal,
          completed: false,
          decisions: [],
          blockers: [],
          nextAction: "",
          question: "",
        },
      });
      setGoal("");
      setStatus("sent");
    } catch {
      setStatus("failed");
    }
  };

  if (!sourceChatId) return null;
  return (
    <details className="rounded-xl border border-border/70 bg-muted/30 px-3 py-2" data-session-handoff-send open={open} onToggle={(event) => onOpenChange?.(event.currentTarget.open)}>
      <summary className="cursor-pointer text-xs font-medium">
        {t("sessionHandoffSend.title", { defaultValue: "Send handoff" })}
      </summary>
      <form className="mt-3 grid gap-2" onSubmit={submit}>
        <label className="grid gap-1 text-xs text-muted-foreground">
          {t("sessionHandoffSend.targetChat", { defaultValue: "Target chat" })}
          <select
            className="rounded border border-border bg-background px-2 py-1.5 text-foreground"
            value={selectedTargetId}
            required
            disabled={status === "sending" || boundedTargets.length === 0}
            onChange={(event) => { setTargetChatId(event.target.value); setStatus("idle"); }}
          >
            <option value="">{t("sessionHandoffSend.chooseTarget", { defaultValue: "Choose a chat" })}</option>
            {boundedTargets.map((target) => <option key={target.id} value={target.id}>{target.title.slice(0, 120) || target.id.slice(0, 32)}</option>)}
          </select>
        </label>
        <textarea
          className="min-h-16 resize-y rounded border border-border bg-background px-2 py-1.5 text-xs"
          value={goal}
          maxLength={2000}
          required
          disabled={status === "sending" || boundedTargets.length === 0}
          placeholder={t("sessionHandoffSend.goalPlaceholder", { defaultValue: "What should the target chat continue?" })}
          onChange={(event) => { setGoal(event.target.value); setStatus("idle"); }}
        />
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] text-muted-foreground" aria-live="polite">
            {boundedTargets.length === 0
              ? t("sessionHandoffSend.noTarget", { defaultValue: "No other chat is available in this scope." })
              : status === "sent"
                ? t("sessionHandoffSend.sent", { defaultValue: "Handoff sent." })
                : status === "failed"
                  ? t("sessionHandoffSend.unavailable", { defaultValue: "Handoff could not be sent." })
                  : ""}
          </span>
          <Button type="submit" className="h-7 px-2 text-xs" disabled={!selectedTargetId || !goal.trim() || status === "sending"}>
            {status === "sending"
              ? t("sessionHandoffSend.sending", { defaultValue: "Sending…" })
              : t("sessionHandoffSend.send", { defaultValue: "Send" })}
          </Button>
        </div>
      </form>
    </details>
  );
}
