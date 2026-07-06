import { AlertTriangle, Archive, Check, Download, Eye, History, Loader2, Pencil, Plus, RefreshCw, RotateCcw, Sparkles, Trash2 } from "lucide-react";
import i18n from "../../i18n";
import type { AdjustmentCheckpoint, AgentCheckpoint, AgentCheckpointPreview, InterruptedApplyRecovery, InterruptedApplyRecoveryPreview } from "../../lib/api";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";
import { OutputBlock } from "../ui/output-block";

export type AdjustmentCheckpointPreview = AgentCheckpointPreview & { adjustmentCheckpoint?: AdjustmentCheckpoint };

export function CheckpointWorkspace({
  checkpoints,
  interruptedRecoveries,
  adjustmentCheckpoints,
  selectedProjectPath,
  preview,
  recoveryPreview,
  adjustmentPreview,
  loading,
  restoringId,
  recoveryBusyId,
  adjustmentBusyId,
  message,
  recoveryMessage,
  adjustmentMessage,
  onRefresh,
  onPreview,
  onRestore,
  onPreviewRecovery,
  onRestoreRecovery,
  onExportRecoveryBundle,
  onResolveRecovery,
  onCreateAdjustment,
  onPreviewAdjustment,
  onSelectAdjustment,
  onApplyAdjustment,
  onOverwriteAdjustment,
  onRenameAdjustment,
  onDeleteAdjustment,
}: {
  checkpoints: AgentCheckpoint[];
  interruptedRecoveries: InterruptedApplyRecovery[];
  adjustmentCheckpoints: AdjustmentCheckpoint[];
  selectedProjectPath: string;
  preview: AgentCheckpointPreview | null;
  recoveryPreview: InterruptedApplyRecoveryPreview | null;
  adjustmentPreview: AdjustmentCheckpointPreview | null;
  loading: boolean;
  restoringId: string;
  recoveryBusyId: string;
  adjustmentBusyId: string;
  message: string;
  recoveryMessage: string;
  adjustmentMessage: string;
  onRefresh: () => void;
  onPreview: (checkpointId: string) => void;
  onRestore: (checkpointId: string) => void;
  onPreviewRecovery: (recoveryId: string) => void;
  onRestoreRecovery: (recoveryId: string) => void;
  onExportRecoveryBundle: (recoveryId: string) => void;
  onResolveRecovery: (recoveryId: string) => void;
  onCreateAdjustment: (kind: "face" | "shader") => void;
  onPreviewAdjustment: (checkpointId: string) => void;
  onSelectAdjustment: (checkpointId: string, slot: "A" | "B") => void;
  onApplyAdjustment: (checkpointId: string) => void;
  onOverwriteAdjustment: (checkpointId: string) => void;
  onRenameAdjustment: (checkpoint: AdjustmentCheckpoint) => void;
  onDeleteAdjustment: (checkpointId: string) => void;
}) {
  const selectedId = preview?.checkpoint?.id || "";
  const selectedRecoveryId = recoveryPreview?.recovery?.id || "";
  const selectedAdjustmentId = adjustmentPreview?.adjustmentCheckpoint?.id || "";
  const changedFiles = preview?.changedFiles || [];
  const workingTreeStatus = preview?.workingTreeStatus || [];
  const recoveryCheckpointPreview = recoveryPreview?.checkpointPreview || null;
  const recoveryChangedFiles = recoveryCheckpointPreview?.changedFiles || [];
  const recoveryWorkingTreeStatus = recoveryCheckpointPreview?.workingTreeStatus || [];
  const adjustmentChangedFiles = adjustmentPreview?.changedFiles || [];
  const adjustmentWorkingTreeStatus = adjustmentPreview?.workingTreeStatus || [];
  return (
    <div className="min-h-0 flex-1 overflow-auto px-6 py-8">
      <div className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-[380px_minmax(0,1fr)]">
        <div className="grid min-w-0 gap-6">
          <section className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
            <div className="mb-4 flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
              <div className="truncate text-sm font-semibold">{i18n.t("checkpoint.interruptedWrites")}</div>
              <Badge tone={interruptedRecoveries.length > 0 ? "warn" : "muted"} className="ml-auto shrink-0">
                {interruptedRecoveries.length}
              </Badge>
            </div>
            <div className="max-h-[24vh] space-y-2 overflow-auto pr-1">
              {interruptedRecoveries.length === 0 ? (
                <div className="rounded-md border border-dashed border-border px-3 py-5 text-center text-xs text-muted-foreground">
                  {i18n.t("checkpoint.noInterruptedWrites")}
                </div>
              ) : null}
              {interruptedRecoveries.map((recovery) => {
                const busy =
                  recoveryBusyId === recovery.id ||
                  recoveryBusyId.endsWith(`:${recovery.id}`) ||
                  recoveryBusyId.startsWith(`${recovery.id}:`);
                const status = recovery.status || "needs_recovery";
                return (
                  <div
                    key={recovery.id}
                    className={cn(
                      "grid min-w-0 gap-2 rounded-md border px-3 py-2 text-sm transition-colors",
                      selectedRecoveryId === recovery.id ? "border-primary bg-primary/5" : "border-border",
                    )}
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="min-w-0 flex-1 truncate font-mono text-xs">{recovery.id}</span>
                      <Badge tone={status === "applying" ? "warn" : status === "needs_recovery" ? "danger" : "muted"} className="h-6 shrink-0">
                        {status}
                      </Badge>
                      {busy ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" /> : null}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">{recovery.targetTool || "-"}</div>
                    <div className="truncate font-mono text-xs text-muted-foreground">{recovery.checkpointId || "-"}</div>
                    <div className="flex min-w-0 flex-wrap gap-1.5">
                      <Button
                        type="button"
                        variant="outline"
                        className="h-7 px-2 text-xs"
                        onClick={() => onPreviewRecovery(recovery.id)}
                        disabled={Boolean(recoveryBusyId)}
                      >
                        <Eye className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        type="button"
                        variant="danger"
                        className="h-7 px-2 text-xs"
                        onClick={() => onRestoreRecovery(recovery.id)}
                        disabled={Boolean(recoveryBusyId)}
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                        {i18n.t("checkpoint.restore")}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-7 px-2 text-xs"
                        onClick={() => onExportRecoveryBundle(recovery.id)}
                        disabled={Boolean(recoveryBusyId)}
                      >
                        <Download className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        className="h-7 px-2 text-xs"
                        onClick={() => onResolveRecovery(recovery.id)}
                        disabled={Boolean(recoveryBusyId)}
                      >
                        <Check className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
            <div className="mb-4 flex items-center gap-2">
              <History className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("checkpoint.checkpoints")}</div>
              <Badge tone="muted" className="ml-auto shrink-0">
                {checkpoints.length}
              </Badge>
              <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={onRefresh} disabled={loading}>
                {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              </Button>
            </div>
            {selectedProjectPath ? <div className="mb-3 truncate text-xs text-muted-foreground">{selectedProjectPath}</div> : null}
            <div className="max-h-[34vh] space-y-2 overflow-auto pr-1">
              {checkpoints.length === 0 ? (
                <div className="rounded-md border border-dashed border-border px-3 py-6 text-center text-xs text-muted-foreground">
                  {i18n.t("checkpoint.noCheckpoints")}
                </div>
              ) : null}
              {checkpoints.map((checkpoint) => (
                <button
                  key={checkpoint.id}
                  type="button"
                  onClick={() => onPreview(checkpoint.id)}
                  className={cn(
                    "grid w-full min-w-0 gap-1 rounded-md border px-3 py-2 text-left text-sm transition-colors",
                    selectedId === checkpoint.id
                      ? "border-primary bg-primary/5"
                      : "border-border hover:border-primary/40 hover:bg-muted/60",
                  )}
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="min-w-0 flex-1 truncate font-mono text-xs">{checkpoint.id}</span>
                    <Badge tone={checkpoint.ok ? "ok" : "warn"} className="h-6 shrink-0">
                      {checkpoint.status || (checkpoint.ok ? i18n.t("connector.ready") : "unavailable")}
                    </Badge>
                  </div>
                  <div className="truncate text-xs text-muted-foreground">{checkpoint.targetTool || "-"}</div>
                  <div className="truncate text-xs text-muted-foreground">{formatCheckpointTime(checkpoint.createdAt)}</div>
                </button>
              ))}
            </div>
          </section>

          <section className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
            <div className="mb-4 flex items-center gap-2">
              <Sparkles className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("checkpoint.adjustmentTimeline")}</div>
              <Badge tone="muted" className="ml-auto shrink-0">
                {adjustmentCheckpoints.length}
              </Badge>
            </div>
            <div className="mb-3 flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                className="h-7 px-2 text-xs"
                onClick={() => onCreateAdjustment("face")}
                disabled={Boolean(adjustmentBusyId) || loading}
              >
                <Plus className="h-3.5 w-3.5" />
                {i18n.t("checkpoint.face")}
              </Button>
              <Button
                type="button"
                variant="outline"
                className="h-7 px-2 text-xs"
                onClick={() => onCreateAdjustment("shader")}
                disabled={Boolean(adjustmentBusyId) || loading}
              >
                <Plus className="h-3.5 w-3.5" />
                {i18n.t("checkpoint.shader")}
              </Button>
            </div>
            <div className="max-h-[40vh] space-y-2 overflow-auto pr-1">
              {adjustmentCheckpoints.length === 0 ? (
                <div className="rounded-md border border-dashed border-border px-3 py-6 text-center text-xs text-muted-foreground">
                  {i18n.t("checkpoint.noAdjustments")}
                </div>
              ) : null}
              {adjustmentCheckpoints.map((checkpoint) => {
                const slots = checkpoint.selectedSlots || (checkpoint.selectionSlot ? [checkpoint.selectionSlot] : []);
                const slotA = slots.includes("A");
                const slotB = slots.includes("B");
                const busy =
                  adjustmentBusyId === checkpoint.id ||
                  adjustmentBusyId.startsWith(`${checkpoint.id}:`) ||
                  adjustmentBusyId.endsWith(`:${checkpoint.id}`);
                return (
                  <div
                    key={checkpoint.id}
                    className={cn(
                      "grid min-w-0 gap-2 rounded-md border px-3 py-2 text-sm transition-colors",
                      selectedAdjustmentId === checkpoint.id ? "border-primary bg-primary/5" : "border-border",
                    )}
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <Badge tone={checkpoint.kind === "face" ? "default" : "muted"} className="h-6 shrink-0">
                        {checkpoint.kind}
                      </Badge>
                      <button
                        type="button"
                        className="min-w-0 flex-1 truncate text-left font-medium"
                        onClick={() => onPreviewAdjustment(checkpoint.id)}
                      >
                        {checkpoint.label || checkpoint.id}
                      </button>
                      {busy ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" /> : null}
                    </div>
                    <div className="truncate font-mono text-xs text-muted-foreground">{checkpoint.checkpointId || checkpoint.id}</div>
                    <div className="flex min-w-0 flex-wrap gap-1.5">
                      <Button
                        type="button"
                        variant={slotA ? "primary" : "outline"}
                        className="h-7 px-2 text-xs"
                        onClick={() => onSelectAdjustment(checkpoint.id, "A")}
                        disabled={Boolean(adjustmentBusyId)}
                      >
                        A
                      </Button>
                      <Button
                        type="button"
                        variant={slotB ? "primary" : "outline"}
                        className="h-7 px-2 text-xs"
                        onClick={() => onSelectAdjustment(checkpoint.id, "B")}
                        disabled={Boolean(adjustmentBusyId)}
                      >
                        B
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-7 px-2 text-xs"
                        onClick={() => onPreviewAdjustment(checkpoint.id)}
                        disabled={Boolean(adjustmentBusyId)}
                      >
                        <Eye className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-7 px-2 text-xs"
                        onClick={() => onApplyAdjustment(checkpoint.id)}
                        disabled={Boolean(adjustmentBusyId)}
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                        {i18n.t("checkpoint.apply")}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-7 px-2 text-xs"
                        onClick={() => onOverwriteAdjustment(checkpoint.id)}
                        disabled={Boolean(adjustmentBusyId)}
                      >
                        <Archive className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        className="h-7 px-2 text-xs"
                        onClick={() => onRenameAdjustment(checkpoint)}
                        disabled={Boolean(adjustmentBusyId)}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        className="h-7 px-2 text-xs text-destructive"
                        onClick={() => onDeleteAdjustment(checkpoint.id)}
                        disabled={Boolean(adjustmentBusyId)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        </div>

        <div className="grid min-w-0 gap-6">
          <section className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-panel">
            <div className="mb-5 flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
              <div className="truncate text-sm font-semibold">{i18n.t("checkpoint.recoveryPreview")}</div>
              {recoveryPreview ? (
                <Badge tone={recoveryPreview.ok ? "warn" : "danger"} className="ml-auto shrink-0">
                  {recoveryPreview.recovery?.status || (recoveryPreview.ok ? i18n.t("connector.ready") : "blocked")}
                </Badge>
              ) : null}
            </div>

            {!recoveryPreview ? (
              <div className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
                {i18n.t("checkpoint.selectInterruptedWrite")}
              </div>
            ) : (
              <div className="grid gap-4">
                <div className="grid gap-3">
                  <DataLine label={i18n.t("recovery.recovery")} value={recoveryPreview.recovery?.id || "-"} mono />
                  <DataLine label={i18n.t("recovery.target")} value={recoveryPreview.recovery?.targetTool || "-"} />
                  <DataLine label={i18n.t("recovery.checkpoint")} value={recoveryPreview.recovery?.checkpointId || "-"} mono />
                  <DataLine label={i18n.t("subagent.roles.projectIndexReview")} value={recoveryPreview.recovery?.projectRoot || "-"} />
                  {recoveryPreview.error ? <DataLine label={i18n.t("doctor.error")} value={recoveryPreview.error} /> : null}
                </div>
                <OutputBlock label={i18n.t("checkpoint.changedFiles")} value={recoveryChangedFiles.join("\n")} />
                <OutputBlock label={i18n.t("checkpoint.workingTree")} value={recoveryWorkingTreeStatus.join("\n")} />
                {recoveryMessage ? <div className="text-sm text-muted-foreground">{recoveryMessage}</div> : null}
                <div className="flex flex-wrap justify-end gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    disabled={!recoveryPreview.recovery?.id || Boolean(recoveryBusyId)}
                    onClick={() => recoveryPreview.recovery?.id && onExportRecoveryBundle(recoveryPreview.recovery.id)}
                  >
                    {recoveryBusyId.startsWith("bundle:") ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                    Bundle
                  </Button>
                  <Button
                    type="button"
                    variant="danger"
                    disabled={!recoveryPreview.ok || !recoveryPreview.recovery?.id || Boolean(recoveryBusyId)}
                    onClick={() => recoveryPreview.recovery?.id && onRestoreRecovery(recoveryPreview.recovery.id)}
                  >
                    {recoveryBusyId.startsWith("restore:") ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <RotateCcw className="h-4 w-4" />
                    )}
                    Restore
                  </Button>
                </div>
              </div>
            )}
          </section>

          <section className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-panel">
            <div className="mb-5 flex items-center gap-2">
              <RotateCcw className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("checkpoint.restorePreview")}</div>
              {preview ? (
                <Badge tone={preview.ok ? "ok" : "danger"} className="ml-auto shrink-0">
                  {preview.ok ? i18n.t("connector.ready") : "blocked"}
                </Badge>
              ) : null}
            </div>

            {!preview ? (
              <div className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
                {i18n.t("checkpoint.selectCheckpoint")}
              </div>
            ) : (
              <div className="grid gap-4">
                <div className="grid gap-3">
                  <DataLine label={i18n.t("recovery.checkpoint")} value={preview.checkpoint?.id || "-"} mono />
                  <DataLine label={i18n.t("recovery.target")} value={preview.checkpoint?.targetTool || "-"} />
                  <DataLine label={i18n.t("subagent.roles.projectIndexReview")} value={preview.checkpoint?.projectRoot || "-"} />
                  <DataLine label={i18n.t("recovery.gitRef")} value={shortRef(preview.checkpoint?.checkpointRef)} mono />
                  {preview.error ? <DataLine label={i18n.t("doctor.error")} value={preview.error} /> : null}
                </div>
                <OutputBlock label={i18n.t("checkpoint.changedFiles")} value={changedFiles.join("\n")} />
                <OutputBlock label={i18n.t("checkpoint.workingTree")} value={workingTreeStatus.join("\n")} />
                {message ? <div className="text-sm text-muted-foreground">{message}</div> : null}
                <div className="flex justify-end">
                  <Button
                    type="button"
                    variant="danger"
                    disabled={!preview.ok || !preview.checkpoint?.id || Boolean(restoringId)}
                    onClick={() => preview.checkpoint?.id && onRestore(preview.checkpoint.id)}
                  >
                    {restoringId ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
                    Restore
                  </Button>
                </div>
              </div>
            )}
          </section>

          <section className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-panel">
            <div className="mb-5 flex items-center gap-2">
              <Sparkles className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("checkpoint.adjustmentPreview")}</div>
              {adjustmentPreview ? (
                <Badge tone={adjustmentPreview.ok ? "ok" : "danger"} className="ml-auto shrink-0">
                  {adjustmentPreview.ok ? i18n.t("connector.ready") : "blocked"}
                </Badge>
              ) : null}
            </div>

            {!adjustmentPreview ? (
              <div className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
                {i18n.t("checkpoint.selectFaceShader")}
              </div>
            ) : (
              <div className="grid gap-4">
                <div className="grid gap-3">
                  <DataLine label="Adjustment" value={adjustmentPreview.adjustmentCheckpoint?.label || "-"} />
                  <DataLine label="Kind" value={adjustmentPreview.adjustmentCheckpoint?.kind || "-"} />
                  <DataLine label={i18n.t("recovery.checkpoint")} value={adjustmentPreview.checkpoint?.id || "-"} mono />
                  <DataLine label={i18n.t("subagent.roles.projectIndexReview")} value={adjustmentPreview.checkpoint?.projectRoot || "-"} />
                  <DataLine label={i18n.t("recovery.gitRef")} value={shortRef(adjustmentPreview.checkpoint?.checkpointRef)} mono />
                  {adjustmentPreview.error ? <DataLine label={i18n.t("doctor.error")} value={adjustmentPreview.error} /> : null}
                </div>
                <OutputBlock label={i18n.t("checkpoint.changedFiles")} value={adjustmentChangedFiles.join("\n")} />
                <OutputBlock label={i18n.t("checkpoint.workingTree")} value={adjustmentWorkingTreeStatus.join("\n")} />
                {adjustmentMessage ? <div className="text-sm text-muted-foreground">{adjustmentMessage}</div> : null}
                <div className="flex justify-end">
                  <Button
                    type="button"
                    variant="danger"
                    disabled={!adjustmentPreview.ok || !adjustmentPreview.adjustmentCheckpoint?.id || Boolean(adjustmentBusyId)}
                    onClick={() =>
                      adjustmentPreview.adjustmentCheckpoint?.id && onApplyAdjustment(adjustmentPreview.adjustmentCheckpoint.id)
                    }
                  >
                    {adjustmentBusyId.startsWith("apply:") ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <RotateCcw className="h-4 w-4" />
                    )}
                    Apply
                  </Button>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}


function shortRef(ref?: string) {
  return ref ? ref.slice(0, 12) : "-";
}

function formatCheckpointTime(value?: string) {
  if (!value) {
    return "-";
  }
  const time = new Date(value);
  if (Number.isNaN(time.getTime())) {
    return value;
  }
  return time.toLocaleString();
}
