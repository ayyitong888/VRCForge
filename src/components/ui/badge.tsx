import * as React from "react";
import { cn } from "../../lib/utils";

type BadgeTone = "default" | "ok" | "warn" | "danger" | "muted";

const toneClasses: Record<BadgeTone, string> = {
  default: "border-primary/30 bg-primary/10 text-primary",
  ok: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  warn: "border-amber-500/30 bg-amber-500/12 text-amber-800 dark:text-amber-300",
  danger: "border-destructive/30 bg-destructive/10 text-destructive",
  muted: "border-border bg-muted text-muted-foreground",
};

export function Badge({
  className,
  tone = "default",
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone }) {
  return (
    <span
      className={cn("inline-flex h-7 items-center rounded-md border px-2.5 text-xs font-medium", toneClasses[tone], className)}
      {...props}
    />
  );
}
