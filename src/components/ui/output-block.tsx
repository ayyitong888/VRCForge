import { cn } from "../../lib/utils";

export function OutputBlock({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="mb-1 text-xs text-muted-foreground">{label}</div>
      <pre
        className={cn(
          "max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-muted/50 p-3 font-mono text-xs",
          danger ? "text-destructive" : "text-foreground",
        )}
      >
        {value || "-"}
      </pre>
    </div>
  );
}
