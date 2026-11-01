import type { HTMLAttributes } from "react";
import { cn } from "../../lib/utils";

export function Progress({ value = 0, className, ...props }: HTMLAttributes<HTMLDivElement> & { value?: number }) {
  return <div role="progressbar" aria-valuenow={value} aria-valuemin={0} aria-valuemax={100}
    className={cn("h-1.5 overflow-hidden rounded-full bg-secondary", className)} {...props}>
    <div className="h-full rounded-full bg-primary transition-all duration-300 ease-out" style={{ width: `${value}%` }} />
  </div>;
}
