import { AlertTriangle, Check, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { AgentApproval } from "../../lib/api";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";

type PendingApprovalsStripProps = {
  approvals: AgentApproval[];
  loading: boolean;
  onApprove: (approvalId: string) => void;
  onReject: (approvalId: string) => void;
};

export function PendingApprovalsStrip({ approvals, loading, onApprove, onReject }: PendingApprovalsStripProps) {
  if (approvals.length === 0) {
    return null;
  }

  return (
    <div className="max-h-[40vh] shrink-0 overflow-auto border-t border-amber-500/20 bg-amber-500/5 px-6 py-3">
      <div className="mx-auto max-w-4xl space-y-3">
        {approvals.map((approval) => (
          <ApprovalCard key={approval.id} approval={approval} loading={loading} onApprove={onApprove} onReject={onReject} />
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
  onApprove: (approvalId: string) => void;
  onReject: (approvalId: string) => void;
}) {
  const { t } = useTranslation();

  return (
    <section className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 shadow-panel">
      <div className="flex min-w-0 items-center gap-2">
        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
        <div className="truncate text-sm font-semibold">{t("header.pendingApprovals")}</div>
        <Badge tone="warn" className="ml-auto shrink-0">
          {approval.riskLevel || "high"}
        </Badge>
      </div>
      <div className="mt-4 grid gap-3">
        <DataLine label={t("approval.command")} value={approval.preview?.command || "-"} mono />
        <DataLine label={t("approval.directory")} value={approval.preview?.cwd || "-"} />
        <DataLine label={t("approval.reason")} value={approval.reason || "-"} />
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="outline" disabled={loading} onClick={() => onReject(approval.id)}>
          <X className="h-4 w-4" />
          {t("approval.reject")}
        </Button>
        <Button variant="primary" disabled={loading} onClick={() => onApprove(approval.id)}>
          <Check className="h-4 w-4" />
          {t("approval.approve")}
        </Button>
      </div>
    </section>
  );
}
