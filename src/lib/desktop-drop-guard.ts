export function installDesktopDropGuard() {
  if (!("__TAURI_INTERNALS__" in window)) return;
  const keepDropInApp = (event: DragEvent) => {
    const types = event.dataTransfer?.types || [];
    if (types.includes("Files") || types.includes("text/uri-list")) event.preventDefault();
  };
  // The composer still receives its own file drops. Only the document's
  // default URL/file navigation is cancelled outside of that handler.
  document.addEventListener("dragover", keepDropInApp);
  document.addEventListener("drop", keepDropInApp);
}
