import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../ui/button";
import type { SessionHandoffSummary } from "../../lib/api/session-handoff";

export function SessionHandoffCard({ handoff, onAccept, onDismiss, onPause, onResume, onReply, busy }: { handoff: SessionHandoffSummary & { summary?: string }; onAccept: () => void; onDismiss: () => void; onPause?: () => void; onResume?: () => void; onReply?: (text: string) => void; busy?: boolean }) {
  const { t } = useTranslation();
  const [reply, setReply] = useState("");
  const summary = String(handoff.summary || t("sessionHandoff.defaultSummary")).slice(0, 500);
  const kind = t(`sessionHandoff.kind.${handoff.kind}`, { defaultValue: handoff.kind });
  const status = handoff.status === "paused" ? t("sessionHandoff.pause") : t(`sessionHandoff.status.${handoff.status}`, { defaultValue: handoff.status });
  return (
    <section data-vrcforge-session-handoff={handoff.id} className="rounded-xl border border-border bg-muted/40 p-3">
      <div className="text-xs text-muted-foreground">{t("sessionHandoff.kindStatus", { kind, status })}</div>
      <p className="mt-1 text-sm">{summary}</p>
      <div className="mt-1 text-[11px] text-muted-foreground">{t("sessionHandoff.revisionPair", { source: handoff.source_revision, target: handoff.target_revision })}</div>
      {handoff.status === "pending_review" || handoff.status === "claimed" ? (
        <div className="mt-2 flex gap-2"><Button className="h-7 px-2 text-xs" disabled={busy} onClick={onAccept}>{t("sessionHandoff.accept")}</Button><Button className="h-7 px-2 text-xs" variant="outline" disabled={busy} onClick={onDismiss}>{t("sessionHandoff.dismiss")}</Button>{onPause ? <Button className="h-7 px-2 text-xs" variant="outline" disabled={busy} onClick={onPause}>{t("sessionHandoff.pause")}</Button> : null}</div>
      ) : null}
      {handoff.status === "paused" && onResume ? <div className="mt-2 flex gap-2"><Button className="h-7 px-2 text-xs" disabled={busy} onClick={onResume}>{t("sessionHandoff.resume")}</Button></div> : null}
      {handoff.kind === "question" && onReply ? <div className="mt-2 flex gap-2"><input className="min-w-0 flex-1 rounded border border-border bg-background px-2 py-1 text-xs" value={reply} maxLength={2000} disabled={busy} onChange={(event) => setReply(event.target.value)} placeholder={t("sessionHandoff.replyPlaceholder")} /><Button className="h-7 px-2 text-xs" disabled={busy || !reply.trim()} onClick={() => { onReply(reply.trim()); setReply(""); }}>{t("sessionHandoff.reply")}</Button></div> : null}
    </section>
  );
}
