import i18n from "../i18n";
import type { ExecutionMode, PermissionState } from "./api";

export const EXECUTION_MODES: Array<{ value: ExecutionMode; labelKey: string; descriptionKey: string }> = [
  { value: "approval", labelKey: "executionMode.approval", descriptionKey: "executionMode.approvalDesc" },
  { value: "auto", labelKey: "header.autoApproval", descriptionKey: "executionMode.autoDesc" },
  { value: "roslyn_full_auto", labelKey: "header.fullPermission", descriptionKey: "executionMode.roslynFullAutoDesc" },
];

export function executionModeLabel(mode?: string): string {
  return i18n.t(EXECUTION_MODES.find((item) => item.value === mode)?.labelKey ?? "executionMode.approval");
}

export type PermissionVisualState = {
  tier: "restricted" | "auto" | "full";
  badgeTone: "muted" | "warn" | "danger";
  textClass: string;
  hoverClass: string;
  selectedClass: string;
};

export function permissionVisualState(permission?: PermissionState | null, mode?: ExecutionMode | string): PermissionVisualState {
  const effectiveMode = mode || permission?.executionMode || "approval";
  if (permission?.roslynFullAuto || effectiveMode === "roslyn_full_auto") {
    return {
      tier: "full",
      badgeTone: "danger",
      textClass: "text-destructive",
      hoverClass: "hover:bg-destructive/10",
      selectedClass: "border-destructive bg-destructive/5",
    };
  }
  if (permission?.autoApprove || effectiveMode === "auto") {
    return {
      tier: "auto",
      badgeTone: "warn",
      textClass: "text-amber-700 dark:text-amber-300",
      hoverClass: "hover:bg-amber-500/10",
      selectedClass: "border-amber-400 bg-amber-500/5",
    };
  }
  return {
    tier: "restricted",
    badgeTone: "muted",
    textClass: "text-muted-foreground",
    hoverClass: "hover:bg-muted",
    selectedClass: "border-border bg-muted",
  };
}
