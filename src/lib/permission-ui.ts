import i18n from "../i18n";
import type { ExecutionMode, PermissionState } from "./api";

export const EXECUTION_MODES: Array<{ value: ExecutionMode; label: string; description: string }> = [
  { value: "approval", label: i18n.t("executionMode.approval"), description: i18n.t("executionMode.approvalDesc") },
  { value: "auto", label: i18n.t("header.autoApproval"), description: i18n.t("executionMode.autoDesc") },
  { value: "roslyn_full_auto", label: i18n.t("header.fullPermission"), description: i18n.t("header.fullPermission") },
];

export function executionModeLabel(mode?: string): string {
  return EXECUTION_MODES.find((item) => item.value === mode)?.label || i18n.t("executionMode.approval");
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
