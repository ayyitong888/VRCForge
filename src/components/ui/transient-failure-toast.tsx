import { AlertTriangle, X } from "lucide-react";
import { Button } from "./button";

export function TransientFailureToast({
  title,
  message,
  dismissLabel,
  onDismiss,
}: {
  title: string;
  message: string;
  dismissLabel: string;
  onDismiss: () => void;
}) {
  return (
    <div
      role="alert"
      aria-live="assertive"
      className="transient-failure-toast fixed bottom-8 left-1/2 z-[100] flex w-[min(520px,calc(100vw-2rem))] -translate-x-1/2 items-start gap-3 rounded-lg border border-destructive/25 bg-background px-4 py-3 text-sm shadow-xl"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1">
        <div className="font-medium text-foreground">{title}</div>
        <div className="mt-1 break-words text-xs text-muted-foreground">{message}</div>
      </div>
      <Button
        type="button"
        variant="ghost"
        className="h-7 w-7 shrink-0 px-0"
        aria-label={dismissLabel}
        title={dismissLabel}
        onClick={onDismiss}
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}
