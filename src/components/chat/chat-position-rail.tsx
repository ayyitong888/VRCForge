import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";

export type ChatPositionMarker = {
  key: string;
  preview: string;
};

type ChatPositionRailProps = {
  markers: ChatPositionMarker[];
  scrollContainerRef: RefObject<HTMLDivElement | null>;
  scopeKey?: string;
};

function findAnchor(container: HTMLDivElement, key: string): HTMLElement | null {
  return Array.from(container.querySelectorAll<HTMLElement>("[data-chat-nav-anchor]"))
    .find((element) => element.dataset.chatNavAnchor === key) || null;
}

export function ChatPositionRail({ markers, scrollContainerRef, scopeKey = "" }: ChatPositionRailProps) {
  const { t } = useTranslation();
  const [currentKey, setCurrentKey] = useState<string | null>(null);
  const markerListRef = useRef<HTMLDivElement>(null);
  const visible = markers.length >= 2;

  const updateCurrent = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container || !markers.length) return;
    const rect = container.getBoundingClientRect();
    const readingLine = rect.top + rect.height * 0.5;
    const anchors = new Map(
      Array.from(container.querySelectorAll<HTMLElement>("[data-chat-nav-anchor]"))
        .map((anchor) => [anchor.dataset.chatNavAnchor || "", anchor] as const),
    );
    let active: string | null = null;
    let activeTop = -Infinity;
    let first: string | null = null;
    let firstTop = Infinity;
    for (const marker of markers) {
      const anchor = anchors.get(marker.key);
      if (!anchor) continue;
      const top = anchor.getBoundingClientRect().top;
      if (top < firstTop) {
        firstTop = top;
        first = marker.key;
      }
      if (top <= readingLine && top > activeTop) {
        activeTop = top;
        active = marker.key;
      }
    }
    setCurrentKey(active || first);
  }, [markers, scrollContainerRef]);

  useEffect(() => {
    if (!visible) {
      setCurrentKey(null);
      return undefined;
    }
    const container = scrollContainerRef.current;
    if (!container) return undefined;
    let frame = 0;
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(updateCurrent);
    };
    container.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    schedule();
    return () => {
      cancelAnimationFrame(frame);
      container.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
    };
  }, [scopeKey, scrollContainerRef, updateCurrent, visible]);

  const markerLabels = useMemo(() => markers.map((marker, index) => ({
    ...marker,
    index: index + 1,
    label: t("chat.jumpToTurn", { index: index + 1, preview: marker.preview }),
  })), [markers, t]);

  useEffect(() => {
    const list = markerListRef.current;
    const marker = list?.querySelector<HTMLElement>('[aria-current="location"]');
    if (!list || !marker) return;
    const bounds = list.getBoundingClientRect();
    const position = marker.getBoundingClientRect();
    if (position.top < bounds.top) list.scrollTop -= bounds.top - position.top;
    else if (position.bottom > bounds.bottom) list.scrollTop += position.bottom - bounds.bottom;
  }, [currentKey]);

  if (!visible) return null;

  return (
    <nav className="pointer-events-none absolute inset-y-0 left-0 z-10 flex w-7 items-center justify-center" aria-label={t("chat.positionNavigation")} data-chat-position-rail>
      <div ref={markerListRef} className="pointer-events-auto flex max-h-full flex-col items-center overflow-y-auto py-2">
        {markerLabels.map((marker) => (
          <button
            key={marker.key}
            type="button"
            className="group flex h-1.5 w-7 shrink-0 items-center justify-center focus-visible:outline focus-visible:outline-1 focus-visible:outline-foreground"
            aria-label={marker.label}
            title={marker.label}
            aria-current={marker.key === currentKey ? "location" : undefined}
            data-chat-position-marker={marker.key}
            onClick={() => {
              const container = scrollContainerRef.current;
              const anchor = container ? findAnchor(container, marker.key) : null;
              if (!anchor || !container) return;
              const targetTop = anchor.getBoundingClientRect().top
                - container.getBoundingClientRect().top
                + container.scrollTop;
              const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
              container.scrollTo({ top: Math.max(0, targetTop), behavior: reduceMotion ? "auto" : "smooth" });
              setCurrentKey(marker.key);
            }}
          >
            <span
              aria-hidden="true"
              className={`block h-0.5 rounded-full transition-all ${marker.key === currentKey ? "w-4 bg-foreground" : "w-2 bg-muted-foreground/50 group-hover:w-3 group-hover:bg-foreground/70"}`}
            />
          </button>
        ))}
      </div>
    </nav>
  );
}
