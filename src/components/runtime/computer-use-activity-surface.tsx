import { MonitorUp, Square } from "lucide-react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import type { ThemeMode } from "../../lib/app-preferences";
import type { AgentDesktopAction } from "../../lib/api";
import {
  resolveComputerUseVisualTokens,
  type ComputerUseVisualPhase,
} from "../../lib/computer-use-visuals";
import { cn } from "../../lib/utils";

type ComputerUseActivitySurfaceProps = {
  actions: AgentDesktopAction[];
  cancellingActionIds: string[];
  theme: ThemeMode;
  onCancel: (actionId: string) => void | Promise<void>;
};

const EMBEDDED_NATIVE_PROVIDER = "embedded-ctypes-win32";

export function usesNativeComputerUseOverlay(action: AgentDesktopAction): boolean {
  if (action.provider === EMBEDDED_NATIVE_PROVIDER) {
    return true;
  }
  const candidates = action.bridgeCandidates ?? [];
  return (
    action.status === "requested" &&
    candidates.length > 0 &&
    candidates.every((candidate) => candidate.provider === EMBEDDED_NATIVE_PROVIDER)
  );
}

function activeDesktopAction(actions: AgentDesktopAction[]): AgentDesktopAction | null {
  const eligible = actions.filter((action) =>
    ["computer_use", "desktop_rescue"].includes(action.action || "") &&
    ["requested", "claimed", "cancel_requested"].includes(action.status || "") &&
    !usesNativeComputerUseOverlay(action),
  );
  return (
    eligible.find((action) => action.status === "cancel_requested") ??
    eligible.find((action) => action.status === "claimed") ??
    eligible.find((action) => action.status === "requested") ??
    null
  );
}

function activityPhase(action: AgentDesktopAction): ComputerUseVisualPhase {
  if (action.status === "cancel_requested") {
    return "stopping";
  }
  return action.status === "claimed" ? "running" : "starting";
}

export function ComputerUseActivitySurface({
  actions,
  cancellingActionIds,
  theme,
  onCancel,
}: ComputerUseActivitySurfaceProps) {
  const { t } = useTranslation();
  const action = activeDesktopAction(actions);
  if (!action || typeof document === "undefined") {
    return null;
  }

  const actionId = action.actionId || action.id || "";
  const phase = activityPhase(action);
  const cancelling = phase === "stopping" || cancellingActionIds.includes(actionId);
  const title = t(`computerUse.${phase}`);
  const detail = action.promptSummary || t("computerUse.defaultDetail");
  const visuals = resolveComputerUseVisualTokens(theme, phase);

  return createPortal(
    <div
      className="computer-use-overlay pointer-events-none fixed inset-0 z-[70]"
      style={visuals.style}
      data-vrcforge-computer-use
      data-state={phase}
      data-action-id={actionId}
      data-visual-palette={visuals.palette}
    >
      <div
        aria-hidden="true"
        className="computer-use-glow absolute inset-0"
        data-vrcforge-computer-use-glow
      />

      <div
        className="computer-use-banner absolute left-1/2 top-3 flex w-[min(600px,calc(100vw-32px))] -translate-x-1/2 items-center gap-3 rounded-2xl py-2 pl-2 pr-2 backdrop-blur-xl backdrop-saturate-150"
        data-vrcforge-computer-use-banner
      >
        <div className="computer-use-icon relative grid h-9 w-9 shrink-0 place-items-center rounded-[10px]">
          <MonitorUp className="h-[18px] w-[18px]" strokeWidth={2} />
          <span
            aria-hidden="true"
            className={cn(
              "computer-use-status-dot absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2",
              phase === "stopping" && "computer-use-status-dot-stopping",
            )}
          />
        </div>
        <div className="min-w-0 flex-1 pr-2" role="status" aria-live="polite" aria-atomic="true">
          <div className="text-sm font-semibold leading-4 text-foreground">{title}</div>
          <div className="truncate text-xs leading-4 text-muted-foreground" title={detail}>
            {detail}
          </div>
        </div>
        <button
          type="button"
          className="computer-use-stop pointer-events-auto inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full px-3.5 text-xs font-semibold transition-[background-color,border-color,box-shadow,transform] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-55"
          onClick={() => void onCancel(actionId)}
          disabled={!actionId || cancelling}
          title={t("computerUse.cancel")}
          data-vrcforge-computer-use-cancel
        >
          <Square className="h-3.5 w-3.5 fill-current" />
          <span>{cancelling ? t("computerUse.cancelling") : t("computerUse.cancel")}</span>
        </button>
      </div>
    </div>,
    document.body,
  );
}
