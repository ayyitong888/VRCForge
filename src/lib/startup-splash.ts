export function dismissStartupSplash(): void {
  document.querySelector("main[data-vrcforge-startup-shell]")?.remove();
  document.getElementById("root")?.removeAttribute("inert");
}

export function dismissStartupSplashAfterAnimation(): (() => void) | undefined {
  const shell = document.querySelector<HTMLElement>("main[data-vrcforge-startup-shell]");
  if (!shell || shell.hidden) return;

  let active = true;
  const animations = shell.getAnimations({ subtree: true });
  void Promise.allSettled(animations.map((animation) => animation.finished)).then(() => {
    if (active) dismissStartupSplash();
  });
  return () => { active = false; };
}
