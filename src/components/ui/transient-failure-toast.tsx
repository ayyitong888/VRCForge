import { AlertTriangle, Check, X } from "lucide-react";
import { Button } from "./button";

export type TransientFailureToastTone = "success" | "error";

export function TransientFailureToast({
  title,
  tone,
  message,
  dismissLabel,
  onDismiss,
}: {
  title?: string;
  tone?: TransientFailureToastTone;
  message: string;
  dismissLabel: string;
  onDismiss: () => void;
}) {
  const success = tone === "success";
  const Icon = success ? Check : AlertTriangle;
  const accent = success ? "text-emerald-700 dark:text-emerald-400" : "text-destructive";
  const border = success ? "border-emerald-200 dark:border-emerald-800" : "border-destructive/25";
  return (
    <div
      className={`transient-failure-toast mx-auto mb-1 mt-2 flex w-[min(520px,calc(100%-2rem))] shrink-0 items-start gap-2 rounded-lg border bg-card px-3 py-2 text-sm ${border}`}
    >
      <Icon className={`mt-1.5 h-4 w-4 shrink-0 ${accent}`} aria-hidden="true" />
      <div role={success ? "status" : "alert"} className="max-h-24 min-w-0 flex-1 overflow-y-auto break-words py-1">
        {title ? <div className="font-medium text-foreground">{title}</div> : null}
        <div className={title ? "text-xs text-muted-foreground" : "text-foreground"}>{message}</div>
      </div>
      <Button
        type="button"
        variant="ghost"
        className="h-7 w-7 shrink-0 px-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={dismissLabel}
        title={dismissLabel}
        onClick={onDismiss}
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}
