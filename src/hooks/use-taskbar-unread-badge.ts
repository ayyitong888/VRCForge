import { useEffect, useMemo, useRef } from "react";
import { Image } from "@tauri-apps/api/image";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { isTauriRuntime } from "../lib/app-runtime";
import { countTaskbarAttention, type TaskbarAttention } from "../lib/taskbar-attention";

function drawBadge(label: string): ImageData {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 32;
  const context = canvas.getContext("2d")!;
  context.fillStyle = "#be185d";
  context.beginPath();
  context.arc(16, 16, 14.5, 0, Math.PI * 2);
  context.fill();
  context.lineWidth = 2;
  context.strokeStyle = "#fff";
  context.stroke();
  context.fillStyle = "#fff";
  context.font = `bold ${label.length === 1 ? 24 : label.length === 2 ? 19 : 14}px "Segoe UI", sans-serif`;
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(label, 16, 17, 26);
  return context.getImageData(0, 0, 32, 32);
}

export function useTaskbarUnreadBadge({ chats, deliveries, approvals, questions, runs }: TaskbarAttention) {
  const supported = isTauriRuntime() && navigator.userAgent.includes("Windows");
  const label = useMemo(() => {
    if (!supported) return "";
    const count = countTaskbarAttention({ chats, deliveries, approvals, questions, runs });
    return count > 99 ? "99+" : count ? String(count) : "";
  }, [chats, deliveries, approvals, questions, runs, supported]);
  const pending = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => {
    if (!supported) return;
    let stale = false;
    pending.current = pending.current.then(async () => {
      if (stale) return;
      const appWindow = getCurrentWindow();
      if (!label) {
        await appWindow.setOverlayIcon();
        return;
      }
      const { data, width, height } = drawBadge(label);
      const icon = await Image.new(new Uint8Array(data.buffer), width, height);
      try {
        if (!stale) await appWindow.setOverlayIcon(icon);
      } finally {
        await icon.close();
      }
    }).catch((error: unknown) => console.warn("Could not update the taskbar unread badge.", error));
    return () => { stale = true; };
  }, [label, supported]);
}
