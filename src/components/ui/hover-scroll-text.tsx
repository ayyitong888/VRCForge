import { useEffect, useRef, type PointerEvent } from "react";
import { cn } from "../../lib/utils";

const HOVER_DELAY_MS = 500;
const END_PAUSE_MS = 650;
const PIXELS_PER_MS = 0.036;

export function HoverScrollText({ text, className }: { text: string; className?: string }) {
  const stopRef = useRef<(() => void) | null>(null);

  useEffect(() => () => stopRef.current?.(), [text]);

  function start(event: PointerEvent<HTMLSpanElement>) {
    const element = event.currentTarget;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (event.pointerType !== "mouse" || reducedMotion.matches || element.scrollWidth <= element.clientWidth) return;

    let frame = 0;
    const timer = window.setTimeout(() => {
      const visibleWidth = element.clientWidth;
      const distance = element.scrollWidth - visibleWidth;
      if (!visibleWidth || distance <= 0 || reducedMotion.matches) {
        reset();
        return;
      }
      const direction = getComputedStyle(element).direction === "rtl" ? -1 : 1;
      const travelTime = distance / PIXELS_PER_MS;
      const totalTime = travelTime * 2 + END_PAUSE_MS;
      const started = performance.now();
      element.style.textOverflow = "clip";

      function move(time: number) {
        const elapsed = Math.max(0, time - started);
        if (elapsed >= totalTime || element.clientWidth !== visibleWidth || reducedMotion.matches) {
          reset();
          return;
        }
        const outward = Math.min(distance, elapsed * PIXELS_PER_MS);
        const backward = Math.max(0, elapsed - travelTime - END_PAUSE_MS) * PIXELS_PER_MS;
        element.scrollLeft = direction * (outward - backward);
        frame = window.requestAnimationFrame(move);
      }

      frame = window.requestAnimationFrame(move);
    }, HOVER_DELAY_MS);

    function reset() {
      window.clearTimeout(timer);
      window.cancelAnimationFrame(frame);
      element.scrollLeft = 0;
      element.style.removeProperty("text-overflow");
      stopRef.current = null;
    }

    stopRef.current = reset;
  }

  return (
    <span
      className={cn("block min-w-0 truncate", className)}
      onPointerEnter={start}
      onPointerLeave={() => stopRef.current?.()}
    >
      {text}
    </span>
  );
}
