import type { ButtonHTMLAttributes } from "react";
import { Check } from "lucide-react";
import { cn } from "../../lib/utils";

export function Checkbox({ checked = false, onCheckedChange, className, ...props }: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange"> & {
  checked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
}) {
  return <button type="button" role="checkbox" aria-checked={checked}
    onClick={() => onCheckedChange?.(!checked)}
    className={cn("inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border border-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50", checked ? "bg-primary text-primary-foreground" : "bg-transparent", className)}
    {...props}>{checked ? <Check size={12} strokeWidth={3} /> : null}</button>;
}
