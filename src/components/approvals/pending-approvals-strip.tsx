import { AlertTriangle, ChevronDown, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { presentApproval } from "../../lib/approval-presentation";
import type { AgentApproval } from "../../lib/api";
import type { ApprovalActionState } from "../../lib/chat-types";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";
import { ApprovalAllowSplitButton } from "./approval-allow-split-button";

type PendingApprovalsStripProps = {
  approvals: AgentApproval[];
  actions: Record<string, ApprovalActionState>;
  loading: boolean;
  onApprove: (approvalId: string, allowFutureCategory?: boolean) => void;
  onReject: (approvalId: string) => void;
};

export function PendingApprovalsStrip({ approvals, actions, loading, onApprove, onReject }: PendingApprovalsStripProps) {
  const visibleApprovals = approvals.filter((approval) => !["approve", "reject"].includes(actions[approval.id] || ""));
  if (visibleApprovals.length === 0) {
    return null;
  }

  return (
    <div className="max-h-[40vh] shrink-0 overflow-auto border-t border-amber-500/20 bg-amber-500/5 px-6 py-3">
      <div className="mx-auto max-w-4xl space-y-3">
        {visibleApprovals.map((approval) => (
          <ApprovalCard key={approval.id} approval={approval} loading={loading || Boolean(actions[approval.id])} onApprove={onApprove} onReject={onReject} />
        ))}
      </div>
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
    <section className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 shadow-panel">
      <div className="flex min-w-0 items-center gap-2">
        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
        <div className="truncate text-sm font-semibold">{presentation.title}</div>
      </div>
      <div className="mt-4 grid gap-3">
        <p className="text-sm text-muted-foreground">{presentation.summary}</p>
        <DataLine label={t("approval.presentation.project")} value={presentation.project} />
        <DataLine label={t("approval.presentation.rollback")} value={presentation.rollback} />
      </div>
      <details className="mt-3 rounded-lg border border-border bg-background/70 px-3 py-2 text-xs">
        <summary className="flex cursor-pointer list-none items-center gap-2 text-muted-foreground">
          <ChevronDown className="h-3.5 w-3.5" />
          {t("approval.contextDetails")}
        </summary>
        <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-foreground">
          {formatApprovalDetails(presentation.technicalDetails)}
        </pre>
      </details>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="outline" disabled={loading} onClick={() => onReject(approval.id)}>
          <X className="h-4 w-4" />
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
