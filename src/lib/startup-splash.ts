export type StartupStep = "prepare" | "listen" | "verify";

export type BackendStartupStatus = {
  ok?: boolean;
  status?: string;
  step?: StartupStep;
  error?: string;
  logDir?: string;
  logSummary?: string;
  exitCode?: number | null;
  attempt?: number;
  sequence?: number;
};

type Phase = "drawing" | "coloring" | "waiting" | "holding" | "leaving" | "error";
type Failure = "timeout" | "exited" | "error";
type Copy = (key: string, variables?: Record<string, string | number>) => string;
type Workspace = { retry: () => void; copy: Copy };

const simulation = import.meta.env.DEV ? new URLSearchParams(location.search).get("startupDebug") : null;
const readyTimes = new Map([["0.2", 200], ["1.5", 1500], ["3.5", 3500], ["8", 8000]]);
export const isStartupSimulation = simulation !== null
  && (readyTimes.has(simulation) || simulation === "exit" || simulation === "timeout");

let splash: ReturnType<typeof createStartupSplash> | undefined;

export function initializeStartupSplash(): void {
  const shell = document.querySelector<HTMLElement>("main[data-vrcforge-startup-shell]");
  if (shell && !shell.hidden) splash = createStartupSplash(shell);
}

export function dismissStartupSplash(): void {
  splash?.stop();
  splash = undefined;
  document.querySelector("main[data-vrcforge-startup-shell]")?.remove();
  document.getElementById("root")?.removeAttribute("inert");
}

export function connectStartupWorkspace(workspace: Workspace): () => void {
  return splash?.connect(workspace) ?? (() => undefined);
}

export function reportStartupStatus(payload: BackendStartupStatus): void {
  if (!isStartupSimulation) splash?.report(payload);
}

function createStartupSplash(shell: HTMLElement) {
  const timeline = shell.getAnimations().find((animation) =>
    "animationName" in animation && animation.animationName === "startup-camera",
  );
  timeline?.pause();
  const progress = shell.querySelector<HTMLElement>(".startup-progress")!;
  const panel = shell.querySelector<HTMLElement>(".startup-failure")!;
  const title = panel.querySelector<HTMLElement>("h1")!;
  const detail = panel.querySelector<HTMLElement>("p")!;
  const summary = panel.querySelector<HTMLElement>("pre")!;
  const retry = panel.querySelector<HTMLButtonElement>("button")!;
  const requested = (window as Window & {
    __vrcforgeStartupMetrics?: { startupShellRequestedMs?: number };
  }).__vrcforgeStartupMetrics?.startupShellRequestedMs;
  let startedAt = requested ?? performance.now();
  let phase: Phase = "drawing";
  let step: StartupStep = "prepare";
  let ready = false;
  let accelerationAt: number | undefined;
  let drawingEnd = 2000;
  let coloringDuration = 1000;
  let leaveAt: number | undefined;
  let frame = 0;
  let workspace: Workspace | undefined;
  let failure: Failure | undefined;
  let failureDetail = "";
  let logDir = "";
  let logSummary = "";
  let simulationDelivered = false;

  const elapsed = () => Math.max(0, performance.now() - startedAt);

  function setPhase(next: Phase) {
    if (shell.dataset.startupPhase !== next) shell.dataset.startupPhase = next;
    phase = next;
  }

  function updateText() {
    if (!workspace) return;
    const { copy } = workspace;
    progress.textContent = copy(step);
    retry.textContent = copy("retry");
    if (!failure) return;
    title.textContent = copy(failure);
    detail.textContent = failureDetail || copy(step);
    summary.textContent = [logSummary, logDir && copy("logs", { directory: logDir })].filter(Boolean).join("\n");
    summary.hidden = !summary.textContent;
  }

  function fail(reason: Failure, message = "") {
    if (failure === "exited" || (failure && reason !== "exited")) return;
    failure = reason;
    failureDetail = message;
    setPhase("error");
    shell.dataset.startupReason = reason;
    progress.hidden = true;
    panel.hidden = false;
    if (timeline) timeline.currentTime = 0;
    updateText();
    panel.focus();
    cancelAnimationFrame(frame);
  }

  function report(payload: BackendStartupStatus) {
    if (payload.logDir) logDir = payload.logDir;
    if (payload.logSummary) logSummary = payload.logSummary;
    if (payload.status === "exited") {
      fail("exited", payload.error);
      return;
    }
    if (failure) {
      updateText();
      return;
    }
    if (payload.status === "progress" && payload.step) {
      step = payload.step;
      updateText();
    } else if (payload.status === "error" || payload.error) {
      fail("error", payload.error);
    } else if (payload.ok && !ready) {
      ready = true;
      const time = elapsed();
      if (time < drawingEnd) {
        accelerationAt = time;
        drawingEnd = time + (2000 - time) / 3;
        coloringDuration = 500;
      }
      progress.hidden = true;
    }
  }

  function simulate(time: number) {
    if (!isStartupSimulation || simulationDelivered) return;
    const readyAt = readyTimes.get(simulation!);
    if (readyAt !== undefined && time >= readyAt) {
      simulationDelivered = true;
      report({ ok: true, status: "ready" });
    } else if (simulation === "exit" && time >= 2400) {
      simulationDelivered = true;
      report({
        status: "exited",
        exitCode: 1,
        error: workspace?.copy("simulatedExit"),
        logSummary: workspace?.copy("simulatedLog"),
      });
    } else {
      const current = time >= 6800 ? "verify" : time >= 350 ? "listen" : "prepare";
      if (step !== current) report({ status: "progress", step: current });
    }
  }

  function tick() {
    if (!shell.isConnected) return;
    const time = elapsed();
    simulate(time);
    if (failure) return;
    if (!ready && time >= 20000) {
      fail("timeout");
      return;
    }

    const completedAt = drawingEnd + coloringDuration;
    let drawingTime = 3000;
    let idleTime = 0;
    if (timeline && time < drawingEnd) {
      setPhase("drawing");
      drawingTime = accelerationAt === undefined ? time : accelerationAt + (time - accelerationAt) * 3;
    } else if (timeline && time < completedAt) {
      setPhase("coloring");
      drawingTime = 2000 + (time - drawingEnd) / coloringDuration * 1000;
    } else if (!ready) {
      setPhase("waiting");
      idleTime = Math.max(0, time - completedAt);
    } else {
      const earliestLeave = timeline ? completedAt + 200 : 0;
      if (!workspace || time < Math.max(1200, earliestLeave)) {
        setPhase("holding");
      } else {
        leaveAt ??= time;
        setPhase("leaving");
        drawingTime = 3200 + (time - leaveAt);
      }
    }

    if (timeline) timeline.currentTime = Math.min(3550, drawingTime);
    shell.dispatchEvent(new CustomEvent("startup-frame", { detail: { time: drawingTime, idle: timeline ? idleTime : 0 } }));
    progress.hidden = ready || time < 6000;
    if (phase === "leaving" && (!timeline || drawingTime >= 3550)) {
      dismissStartupSplash();
      return;
    }
    frame = requestAnimationFrame(tick);
  }

  retry.addEventListener("click", () => {
    if (!workspace || !failure) return;
    startedAt = performance.now();
    step = "prepare";
    ready = false;
    accelerationAt = undefined;
    drawingEnd = 2000;
    coloringDuration = 1000;
    leaveAt = undefined;
    failure = undefined;
    failureDetail = "";
    logSummary = "";
    simulationDelivered = false;
    delete shell.dataset.startupReason;
    panel.hidden = true;
    updateText();
    if (!isStartupSimulation) workspace.retry();
    tick();
  });

  tick();
  return {
    stop: () => cancelAnimationFrame(frame),
    connect(next: Workspace) {
      workspace = next;
      updateText();
      return () => {
        if (workspace === next) workspace = undefined;
      };
    },
    report,
  };
}
