export function dismissStartupSplash(): void {
  document.querySelector("main[data-vrcforge-startup-shell]")?.remove();
  document.getElementById("root")?.removeAttribute("inert");
}
