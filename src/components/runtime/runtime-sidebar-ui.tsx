import { Archive, ChevronDown, ChevronRight, FileText, History, ListChecks } from "lucide-react";
import type { ReactNode } from "react";
import i18n from "../../i18n";
import type { AgentRuntimeRun, WorkspaceDiffSummary } from "../../lib/api";
import type { RuntimeFileReference, RuntimeReviewEvidence, RuntimeScheduleItem } from "../../lib/runtime-ui-types";
import { cn, formatCount } from "../../lib/utils";

export function RuntimeToolButton({ icon, label, onClick }: { icon: ReactNode; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-transparent text-muted-foreground transition-colors hover:border-border hover:bg-muted hover:text-foreground"
    >
      {icon}
    </button>
  );
}



export function StatusDot({ status }: { status: "ok" | "warning" | "error" | "unknown" | string }) {
  return (
    <span
      className={cn(
        "block h-2.5 w-2.5 rounded-full",
        status === "ok"
          ? "bg-emerald-500"
          : status === "warning"
            ? "bg-amber-500"
            : status === "error"
              ? "bg-destructive"
              : "bg-muted-foreground/40",
      )}
    />
  );
}



export function RuntimeInfoRow({
  icon,
  label,
  value,
  suffix,
  muted = false,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  suffix?: ReactNode;
  muted?: boolean;
}) {
  return (
    <div className="grid min-w-0 grid-cols-[18px_minmax(0,1fr)_auto] items-center gap-2 py-1 text-sm">
      <div className={cn("flex items-center justify-center", muted ? "text-muted-foreground/60" : "text-muted-foreground")}>{icon}</div>
      <div className="min-w-0">
        <div className="truncate font-medium">{label}</div>
        {value ? <div className={cn("truncate text-xs", muted ? "text-muted-foreground/60" : "text-muted-foreground")}>{value}</div> : null}
      </div>
      {suffix ? <div className="shrink-0 text-xs">{suffix}</div> : null}
    </div>
  );
}



export function RuntimeRunRow({ run }: { run: AgentRuntimeRun }) {
  const status = String(run.status || run.lastEvent || "unknown");
  const title = run.messageSummary || run.targetTool || run.writeTool || run.skillTool || run.event || i18n.t("workspace.runLedgerItem");
  const provider = run.providerLabel || run.provider || "";
  const model = run.model || "";
  const metaParts = [
    runtimeRunStatusLabel(status),
    run.stepCount ? i18n.t("workspace.runSteps", { count: formatCount(run.stepCount) }) : "",
    provider || model ? `${provider}${provider && model ? " / " : ""}${model}` : "",
  ].filter(Boolean);
  return (
    <div className="grid min-w-0 grid-cols-[14px_minmax(0,1fr)_auto] gap-2 rounded-md px-1 py-1.5 text-xs hover:bg-muted/70">
      <div className="pt-1">
        <span className={cn("block h-3 w-3 rounded-full", runtimeRunStatusClass(status))} />
      </div>
      <div className="min-w-0">
        <div className="truncate font-medium text-foreground" title={title}>
          {title}
        </div>
        <div className="truncate text-muted-foreground">{metaParts.join(" · ") || i18n.t("workspace.runLedgerItem")}</div>
      </div>
      {run.approvalId || run.checkpointId || (run.approvalIds ?? []).length || (run.checkpointIds ?? []).length ? (
        <span className="shrink-0 rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">
          {run.checkpointId || (run.checkpointIds ?? [])[0] ? i18n.t("workspace.proofShort") : i18n.t("workspace.approvalShort")}
        </span>
      ) : null}
    </div>
  );
}



export function RuntimeReviewEvidenceRow({ item }: { item: RuntimeReviewEvidence }) {
  const icon =
    item.kind === "checkpoint" ? (
      <Archive className="h-3.5 w-3.5" />
    ) : item.kind === "diff" ? (
      <FileText className="h-3.5 w-3.5" />
    ) : item.kind === "approval" ? (
      <ListChecks className="h-3.5 w-3.5" />
    ) : (
      <History className="h-3.5 w-3.5" />
    );
  const content = (
    <>
      <span className="flex items-center justify-center text-muted-foreground">{icon}</span>
      <span className="min-w-0">
        <span className="block truncate font-medium text-foreground" title={item.title}>
          {item.title}
        </span>
        <span className="block truncate text-muted-foreground">{item.meta}</span>
      </span>
      <span className="shrink-0 text-muted-foreground">{runtimeRunStatusLabel(item.status || item.kind)}</span>
    </>
  );
  if (item.action) {
    return (
      <button
        type="button"
        className="grid w-full min-w-0 grid-cols-[18px_minmax(0,1fr)_auto] items-center gap-2 rounded-md px-1 py-1.5 text-left text-xs hover:bg-muted/70"
        onClick={item.action}
      >
        {content}
      </button>
    );
  }
  return <div className="grid min-w-0 grid-cols-[18px_minmax(0,1fr)_auto] items-center gap-2 rounded-md px-1 py-1.5 text-xs">{content}</div>;
}



export function RuntimeScheduleRow({ item }: { item: RuntimeScheduleItem }) {
  const tone =
    item.status === "cancelling"
        ? "border-destructive bg-destructive"
        : item.status === "running"
          ? "border-primary bg-primary"
          : "border-muted-foreground bg-transparent";
  return (
    <div className="grid min-w-0 grid-cols-[14px_minmax(0,1fr)] gap-2 py-1.5 text-xs">
      <div className="pt-0.5">
        <span className={cn("block h-3 w-3 rounded-full border", tone, item.status === "running" && "animate-pulse")} />
      </div>
      <div className="min-w-0">
        <div className="truncate font-medium text-foreground">{item.title}</div>
        <div className="truncate text-muted-foreground">{item.meta}</div>
      </div>
    </div>
  );
}



export function RuntimeDiffFileRow({ file }: { file: WorkspaceDiffSummary["files"][number] }) {
  const statusLabel = workspaceDiffStatusLabel(file.status);
  return (
    <div className="grid min-w-0 grid-cols-[76px_minmax(0,1fr)_auto] items-center gap-2 rounded-md px-1 py-1 text-xs hover:bg-muted/70">
      <span className="truncate text-muted-foreground" title={file.status || statusLabel}>
        {statusLabel}
      </span>
      <span className="truncate font-mono">{file.path}</span>
      <span className="shrink-0 font-mono text-muted-foreground">
        {file.binary ? "bin" : `+${file.additions || 0} -${file.deletions || 0}`}
      </span>
    </div>
  );
}



function workspaceDiffStatusLabel(status?: string) {
  const normalized = (status || "M").trim();
  const labels: Record<string, string> = {
    "??": i18n.t("workspace.statusUntracked"),
    M: i18n.t("workspace.statusModified"),
    A: i18n.t("workspace.statusAdded"),
    D: i18n.t("workspace.statusDeleted"),
    R: i18n.t("workspace.statusRenamed"),
    C: i18n.t("workspace.statusCopied"),
    U: i18n.t("workspace.statusConflict"),
    changed: i18n.t("workspace.statusModified"),
    not_git: i18n.t("workspace.statusNotGit"),
    missing: i18n.t("workspace.statusMissing"),
    error: i18n.t("workspace.statusError"),
  };
  return labels[normalized] || labels[normalized.toUpperCase()] || normalized;
}



export function RuntimeFileReferenceRow({ file }: { file: RuntimeFileReference }) {
  return (
    <div className="grid min-w-0 grid-cols-[18px_minmax(0,1fr)_auto] items-center gap-2 rounded-md px-1 py-1 text-xs hover:bg-muted/70">
      <FileText className="h-3.5 w-3.5 text-muted-foreground" />
      <span className="truncate font-mono" title={file.path}>
        {file.path}
      </span>
      <span className="shrink-0 text-muted-foreground">{runtimeFileSourceLabel(file.source)}</span>
    </div>
  );
}



function runtimeFileSourceLabel(source: RuntimeFileReference["source"]) {
  if (source === "changes") {
    return i18n.t("workspace.fileSourceChanges");
  }
  if (source === "message") {
    return i18n.t("workspace.fileSourceMessage");
  }
  if (source === "shell") {
    return i18n.t("workspace.fileSourceShell");
  }
  if (source === "skill") {
    return i18n.t("workspace.fileSourceSkill");
  }
  return i18n.t("workspace.fileSourceDiff");
}



function runtimeRunStatusLabel(status: string) {
  const normalized = status.trim().toLowerCase();
  const labels: Record<string, string> = {
    running: i18n.t("workspace.runStatusRunning"),
    completed: i18n.t("workspace.runStatusCompleted"),
    failed: i18n.t("workspace.runStatusFailed"),
    cancelled: i18n.t("workspace.runStatusCancelled"),
    cancel_requested: i18n.t("workspace.runStatusCancelRequested"),
    applying: i18n.t("workspace.runStatusApplying"),
    applied: i18n.t("workspace.runStatusApplied"),
    approved: i18n.t("workspace.runStatusApproved"),
    rejected: i18n.t("workspace.runStatusRejected"),
    revision_requested: i18n.t("workspace.runStatusRevisionRequested"),
    pending: i18n.t("workspace.runStatusPending"),
    diff: i18n.t("workspace.gitDiffShort"),
    checkpoint: i18n.t("workspace.checkpointShort"),
    approval: i18n.t("workspace.approvalShort"),
    run: i18n.t("workspace.runShort"),
  };
  return labels[normalized] || status || i18n.t("workspace.runStatusUnknown");
}



function runtimeRunStatusClass(status: string) {
  const normalized = status.trim().toLowerCase();
  if (["running", "applying", "cancel_requested"].includes(normalized)) {
    return "bg-primary animate-pulse";
  }
  if (["completed", "applied", "approved"].includes(normalized)) {
    return "bg-emerald-500";
  }
  if (["failed", "rejected", "cancelled"].includes(normalized)) {
    return "bg-destructive";
  }
  if (["pending", "revision_requested"].includes(normalized)) {
    return "bg-amber-500";
  }
  return "bg-muted-foreground/50";
}



export function RuntimeSection({
  title,
  count,
  action,
  collapsed,
  onToggle,
  children,
}: {
  title: string;
  count?: ReactNode;
  action?: ReactNode;
  collapsed?: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className="border-b border-border py-3">
      <div className="mb-2 flex w-full items-center justify-between gap-2">
        <button type="button" className="flex min-w-0 flex-1 items-center gap-1.5 text-left" onClick={onToggle}>
          {collapsed ? <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" /> : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />}
          <span className="truncate text-xs font-semibold uppercase text-muted-foreground">{title}</span>
        </button>
        <span className="flex shrink-0 items-center gap-1">
          {action}
          {count ? <span className="shrink-0">{count}</span> : null}
        </span>
      </div>
      {collapsed ? null : children}
    </section>
  );
}
