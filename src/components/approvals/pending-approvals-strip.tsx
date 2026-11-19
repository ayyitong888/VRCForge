import { AlertTriangle, ChevronDown, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { presentApproval } from "../../lib/approval-presentation";
import type { AgentApproval } from "../../lib/api";
import type { ApprovalActionState } from "../../lib/chat-types";
import { Button } from "../ui/button";
import { ApprovalAllowSplitButton } from "./approval-allow-split-button";

type PendingApprovalsStripProps = {
  approvals: AgentApproval[];
  actions: Record<string, ApprovalActionState>;
  loading: boolean;
  onApprove: (approvalId: string, allowFutureCategory?: boolean) => void;
  onReject: (approvalId: string) => void;
  onClose?: () => void;
};

export function PendingApprovalsStrip({ approvals, actions, loading, onApprove, onReject, onClose }: PendingApprovalsStripProps) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);
  const visibleApprovals = approvals.filter(
    (approval) => approval.status === "pending" && !["approve", "reject"].includes(actions[approval.id] || ""),
  );
  const hasPendingExternalApproval = visibleApprovals.some((approval) => approval.approvalChannel === "external_mcp_confirmation");
  useEffect(() => {
    if (hasPendingExternalApproval || loading || Object.values(actions).some(Boolean)) return undefined;
    const timer = window.setTimeout(() => setCollapsed(true), 30_000);
    return () => window.clearTimeout(timer);
  }, [hasPendingExternalApproval, loading, actions, visibleApprovals.length]);
  if (visibleApprovals.length === 0) {
    return null;
  }
  if (collapsed && !hasPendingExternalApproval) {
    return <div className="shrink-0 border-t border-amber-500/20 bg-amber-500/5 py-2"><Button variant="ghost" onClick={() => setCollapsed(false)} data-approval-reopen>{t("approval.requestTitle")} · {visibleApprovals.length}</Button></div>;
  }

  return (
    <div className="max-h-[40vh] shrink-0 space-y-3 overflow-auto border-t border-amber-500/20 bg-amber-500/5 py-3">
      {onClose ? <div className="flex justify-end"><Button variant="ghost" onClick={onClose}>{t("common.close")}</Button></div> : null}
      <div className="text-sm font-semibold text-foreground">{t("approval.requestTitle")}</div>
      {visibleApprovals.map((approval) => (
        <ApprovalCard key={approval.id} approval={approval} loading={loading || Boolean(actions[approval.id])} onApprove={onApprove} onReject={onReject} />
      ))}
    </div>
  );
}

function ApprovalCard({
  approval,
  loading,
  onApprove,
  onReject,
}: {
  approval: AgentApproval;
  loading: boolean;
  onApprove: (approvalId: string, allowFutureCategory?: boolean) => void;
  onReject: (approvalId: string) => void;
}) {
  const { t } = useTranslation();
  const presentation = presentApproval(approval, t);

  return (
    <section className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-3 shadow-panel">
      <div className="flex min-w-0 items-center gap-2">
        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
        <div className="truncate text-sm font-semibold">{presentation.title}</div>
      </div>
      {approval.approvalChannel === "external_mcp_confirmation" ? (
        <div className="mt-1 text-xs text-muted-foreground">{t("workspace.externalAgent")}</div>
      ) : null}
      <div className="mt-4 grid gap-3">
        <p className="text-sm text-muted-foreground">{presentation.summary}</p>
        {typeof approval.taskContext?.objective === "string" && approval.taskContext.objective.trim() ? (
          <p className="whitespace-pre-wrap break-words text-xs text-muted-foreground">
            <span className="font-medium text-foreground">{t("approval.contextReason")}: </span>
            {approval.taskContext.objective.trim()}
          </p>
        ) : null}
        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
          <dt className="text-muted-foreground">{t("approval.presentation.project")}</dt>
          <dd className="min-w-0 break-words font-medium">{presentation.project}</dd>
          <dt className="text-muted-foreground">{t("approval.presentation.rollback")}</dt>
          <dd className="min-w-0 break-words font-medium">{presentation.rollback}</dd>
        </dl>
      </div>
      <details className="mt-3 rounded-lg border border-border bg-background/70 px-3 py-2 text-xs">
        <summary className="flex cursor-pointer list-none items-center gap-2 text-muted-foreground">
          <ChevronDown className="h-3.5 w-3.5 shrink-0" />
          {t("approval.contextDetails")}
        </summary>
        <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-foreground">
          {formatApprovalDetails(presentation.technicalDetails)}
        </pre>
      </details>
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <Button variant="outline" className="shrink-0 whitespace-nowrap" disabled={loading} onClick={() => onReject(approval.id)}>
          <X className="h-4 w-4 shrink-0" />
          {t("approval.reject")}
        </Button>
        <ApprovalAllowSplitButton
          approvalId={approval.id}
          allowFutureEligible={approval.allowFutureEligible === true}
          disabled={loading}
          onApprove={onApprove}
        />
      </div>
    </section>
  );
}

function formatApprovalDetails(value: Record<string, unknown>): string {
  try {
    return JSON.stringify(value);
  } catch {
    return "-";
  }
}
