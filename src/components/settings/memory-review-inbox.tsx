import { Check, Clock3, Loader2, Pencil, RotateCcw, ShieldX, Trash2, X } from "lucide-react";
import type { TFunction } from "i18next";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type {
  MemoryReviewCandidate,
  MemoryReviewCandidateAction,
} from "../../lib/api/memory-review";
import { Badge, type BadgeTone } from "../ui/badge";
import { Button } from "../ui/button";

function stateTone(state: MemoryReviewCandidate["state"]): BadgeTone {
  if (state === "accepted") return "ok";
  if (state === "conflicting") return "warn";
  if (state === "rejected" || state === "expired") return "muted";
  return "default";
}

function stateKey(state: MemoryReviewCandidate["state"]): string {
  const keys: Record<MemoryReviewCandidate["state"], string> = {
    proposed: "settings.memoryReviewStateProposed",
    accepted: "settings.memoryReviewStateAccepted",
    rejected: "settings.memoryReviewStateRejected",
    deferred: "settings.memoryReviewStateDeferred",
    expired: "settings.memoryReviewStateExpired",
    conflicting: "settings.memoryReviewStateConflicting",
  };
  return keys[state];
}

function kindKey(kind: string): string {
  const keys: Record<string, string> = {
    preference: "settings.memoryReviewKindPreference",
    fact: "settings.memoryReviewKindFact",
    correction: "settings.memoryReviewKindCorrection",
    decision: "settings.memoryReviewKindDecision",
  };
  return keys[kind] || "settings.memoryReviewKindFact";
}

function sourceSummary(candidate: MemoryReviewCandidate, t: TFunction): string {
  const counts = candidate.sourceTypeCounts || {};
  const labels: string[] = [];
  if ((counts.user_chat || 0) > 0) labels.push(t("settings.memoryReviewSourceChat", { count: counts.user_chat }));
  if ((counts.adopted_task || 0) > 0) labels.push(t("settings.memoryReviewSourceTask", { count: counts.adopted_task }));
  if ((counts.validated_project_result || 0) > 0) labels.push(t("settings.memoryReviewSourceValidated", { count: counts.validated_project_result }));
  return labels.join(" · ");
}

export function MemoryReviewInbox({
  candidates,
  busyKey,
  runtimeConnected,
  onDecision,
}: {
  candidates: MemoryReviewCandidate[];
  busyKey: string;
  runtimeConnected: boolean;
  onDecision: (
    candidateId: string,
    action: MemoryReviewCandidateAction,
    editedText?: string,
  ) => Promise<boolean>;
}) {
  const { t } = useTranslation();
  const [editingId, setEditingId] = useState("");
  const [editedText, setEditedText] = useState("");
  const [confirmEraseId, setConfirmEraseId] = useState("");

  const firstUnreadId = candidates.find(
    (candidate) => candidate.unread && !candidate.eraseOnly,
  )?.candidateId || "";
  useEffect(() => {
    if (!firstUnreadId || !runtimeConnected || busyKey) {
      return;
    }
    void onDecision(firstUnreadId, "read");
  }, [busyKey, firstUnreadId, onDecision, runtimeConnected]);

  async function decide(
    candidate: MemoryReviewCandidate,
    action: MemoryReviewCandidateAction,
    text?: string,
  ) {
    const accepted = await onDecision(candidate.candidateId, action, text);
    if (accepted) {
      setEditingId("");
      setEditedText("");
      setConfirmEraseId("");
    }
  }

  if (!candidates.length) {
    return (
      <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
        {t("settings.memoryReviewEmpty")}
      </div>
    );
  }

  return (
    <div className="space-y-3" data-memory-review-inbox>
      {candidates.map((candidate) => {
        const isBusy = busyKey.startsWith(`candidate:${candidate.candidateId}:`);
        const actionable = !candidate.eraseOnly && (
          candidate.state === "proposed"
          || candidate.state === "deferred"
          || candidate.state === "conflicting"
        );
        const canAccept = actionable && candidate.state !== "conflicting";
        const sources = sourceSummary(candidate, t);
        const editing = editingId === candidate.candidateId;
        const confirmingErase = confirmEraseId === candidate.candidateId;
        return (
          <article
            key={candidate.candidateId}
            className="rounded-lg border border-border bg-card p-4"
            data-memory-review-candidate={candidate.candidateId}
          >
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={stateTone(candidate.state)}>{t(stateKey(candidate.state))}</Badge>
              <Badge tone="muted">
                {candidate.scope === "user"
                  ? t("settings.memoryReviewScopeUser")
                  : t("settings.memoryReviewScopeProject")}
              </Badge>
              {!candidate.eraseOnly ? <Badge tone="muted">{t(kindKey(candidate.kind))}</Badge> : null}
              {candidate.unread ? <span className="h-2 w-2 rounded-full bg-primary" aria-label={t("settings.memoryReviewUnread")} /> : null}
              {candidate.eraseOnly ? (
                <Badge tone="warn">{t("settings.memoryReviewProjectUnavailable")}</Badge>
              ) : (
                <span className="ml-auto text-xs text-muted-foreground">
                  {t("settings.memoryReviewEvidenceCount", { count: candidate.evidenceCount })}
                </span>
              )}
            </div>

            {editing ? (
              <div className="mt-3">
                <label className="text-xs font-medium text-muted-foreground" htmlFor={`memory-review-edit-${candidate.candidateId}`}>
                  {t("settings.memoryReviewEditLabel")}
                </label>
                <textarea
                  id={`memory-review-edit-${candidate.candidateId}`}
                  value={editedText}
                  onChange={(event) => setEditedText(event.target.value)}
                  className="mt-1 min-h-24 w-full resize-y rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
                  maxLength={2_000}
                />
              </div>
            ) : candidate.eraseOnly ? (
              <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
                {t("settings.memoryReviewEraseOnlyDescription")}
              </p>
            ) : (
              <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground">
                {candidate.proposedText}
              </p>
            )}

            {(candidate.conflictCount || 0) > 0 ? (
              <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
                {t(`settings.memoryReviewConflict_${candidate.conflictExplanation || "mixed"}`, { count: candidate.conflictCount || 0 })}
              </p>
            ) : null}
            {!candidate.eraseOnly && sources ? (
              <p className="mt-2 text-xs text-muted-foreground">
                {t("settings.memoryReviewSourceSummary", { sources, score: candidate.confidenceScore || 0 })}
              </p>
            ) : null}

            <div className="mt-4 flex flex-wrap gap-2">
              {actionable && !editing ? (
                <>
                  {canAccept ? (
                    <>
                      <Button
                        type="button"
                        className="h-9 px-3"
                        disabled={!runtimeConnected || Boolean(busyKey)}
                        onClick={() => void decide(candidate, "accept")}
                      >
                        {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                        {t("settings.memoryReviewAccept")}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-9 px-3"
                        disabled={!runtimeConnected || Boolean(busyKey)}
                        onClick={() => {
                          setEditingId(candidate.candidateId);
                          setEditedText(candidate.proposedText);
                          setConfirmEraseId("");
                        }}
                      >
                        <Pencil className="h-4 w-4" />
                        {t("settings.memoryReviewAcceptEdited")}
                      </Button>
                    </>
                  ) : null}
                  <Button
                    type="button"
                    variant="outline"
                    className="h-9 px-3"
                    disabled={!runtimeConnected || Boolean(busyKey) || candidate.state === "deferred"}
                    onClick={() => void decide(candidate, "defer")}
                  >
                    <Clock3 className="h-4 w-4" />
                    {t("settings.memoryReviewDefer")}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    className="h-9 px-3"
                    disabled={!runtimeConnected || Boolean(busyKey)}
                    onClick={() => void decide(candidate, "reject")}
                  >
                    <ShieldX className="h-4 w-4" />
                    {t("settings.memoryReviewReject")}
                  </Button>
                </>
              ) : null}

              {editing ? (
                <>
                  <Button
                    type="button"
                    className="h-9 px-3"
                    disabled={!runtimeConnected || Boolean(busyKey) || !editedText.trim()}
                    onClick={() => void decide(candidate, "accept", editedText)}
                  >
                    {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                    {t("settings.memoryReviewAcceptEdited")}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    className="h-9 px-3"
                    disabled={Boolean(busyKey)}
                    onClick={() => {
                      setEditingId("");
                      setEditedText("");
                    }}
                  >
                    <X className="h-4 w-4" />
                    {t("common.cancel")}
                  </Button>
                </>
              ) : null}

              {candidate.state === "accepted" && !candidate.eraseOnly ? (
                <Button
                  type="button"
                  variant="outline"
                  className="h-9 px-3"
                  disabled={!runtimeConnected || Boolean(busyKey)}
                  onClick={() => void decide(candidate, "undo")}
                >
                  {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
                  {t("settings.memoryReviewUndo")}
                </Button>
              ) : null}

              <Button
                type="button"
                variant={confirmingErase ? "danger" : "ghost"}
                className="h-9 px-3"
                disabled={!runtimeConnected || Boolean(busyKey)}
                onClick={() => {
                  if (confirmingErase) {
                    void decide(candidate, "erase");
                    return;
                  }
                  setConfirmEraseId(candidate.candidateId);
                  setEditingId("");
                  setEditedText("");
                }}
              >
                {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                {confirmingErase
                  ? t("settings.memoryReviewConfirmErase")
                  : t("settings.memoryReviewPermanentErase")}
              </Button>
            </div>
          </article>
        );
      })}
    </div>
  );
}
