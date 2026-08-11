import { ChevronDown, Pencil, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { presentApproval } from "../../lib/approval-presentation";
import type { AgentApproval } from "../../lib/api";
import type { ApprovalActionState } from "../../lib/chat-types";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { ApprovalAllowSplitButton } from "./approval-allow-split-button";

type ScopedPendingApprovalCardProps = {
  approvals: AgentApproval[];
  actions: Record<string, ApprovalActionState>;
  disabled: boolean;
  onApprove: (approvalId: string, allowFutureCategory?: boolean) => void;
  onReject: (approvalId: string) => void;
  onModifyApproval: (approval: AgentApproval) => void;
};

export function ScopedPendingApprovalCard({ approvals, actions, disabled, onApprove, onReject, onModifyApproval }: ScopedPendingApprovalCardProps) {
  const { t } = useTranslation();
  const visibleApprovals = approvals.filter(
    (approval) => approval.status === "pending" && !["approve", "reject"].includes(actions[approval.id] || ""),
  );

  if (visibleApprovals.length === 0) {
    return null;
  }

  return (
    <section
      className="overflow-hidden rounded-2xl border border-border bg-card shadow-panel"
      data-scoped-pending-approval
      data-approval-composer-replacement
      aria-labelledby="scoped-pending-approval-title"
      aria-live="polite"
    >
      <div className="flex min-w-0 items-start gap-3 px-4 pb-3 pt-4">
        <div className="min-w-0 flex-1">
          <div id="scoped-pending-approval-title" className="truncate text-sm font-semibold">
            {t("approval.blockingTitle")}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">{t("approval.blockingDescription")}</p>
        </div>
        {visibleApprovals.length > 1 ? (
          <Badge tone="warn" className="shrink-0">
            {visibleApprovals.length}
          </Badge>
        ) : null}
      </div>
      <div className="max-h-[45vh] space-y-3 overflow-auto border-t border-border px-4 py-3">
        {visibleApprovals.map((approval) => {
          const action = actions[approval.id];
          const busy = disabled || Boolean(action);
          const presentation = presentApproval(approval, t);
          return (
            <article key={approval.id} className="rounded-xl border border-border bg-background/70 p-3">
              <div className="text-sm font-medium">{presentation.title}</div>
              <p className="mt-1 text-xs text-muted-foreground">{presentation.summary}</p>
              <dl className="mt-3 grid gap-2 text-xs">
                <div className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-3">
                  <dt className="text-muted-foreground">{t("approval.presentation.project")}</dt>
                  <dd className="min-w-0 truncate">{presentation.project}</dd>
                </div>
                <div className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-3">
                  <dt className="text-muted-foreground">{t("approval.presentation.rollback")}</dt>
                  <dd className="min-w-0 whitespace-pre-wrap break-words">{presentation.rollback}</dd>
                </div>
              </dl>
              <details className="mt-3 rounded-lg border border-border bg-muted/30 px-3 py-2 text-xs">
                <summary className="flex cursor-pointer list-none items-center gap-2 text-muted-foreground">
                  <ChevronDown className="h-3.5 w-3.5" />
                  {t("approval.contextDetails")}
                </summary>
                <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-foreground">
                  {formatApprovalSummary(presentation.technicalDetails)}
                </pre>
              </details>
              <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
                <Button variant="ghost" disabled={busy} onClick={() => onReject(approval.id)}>
                  <X className="h-4 w-4" />
                  {action === "reject" ? t("approval.rejecting") : t("approval.reject")}
                </Button>
                <div className="ml-auto flex flex-wrap justify-end gap-2">
                  {!approval.goalDeliveryId?.trim() ? (
                    <Button variant="outline" disabled={busy} onClick={() => onModifyApproval(approval)}>
                      <Pencil className="h-4 w-4" />
                      {action === "modify" ? t("approval.modifying") : t("approval.modify")}
                    </Button>
                  ) : null}
                  <ApprovalAllowSplitButton
                    approvalId={approval.id}
                    allowFutureEligible={approval.allowFutureEligible === true}
                    disabled={busy}
                    onApprove={onApprove}
                  />
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function formatApprovalSummary(value: unknown): string {
  if (!value || typeof value !== "object") {
    return "-";
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "-";
  }
}
