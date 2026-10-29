import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { ChatMarkdown } from "./chat-markdown";

export type ChatPositionMarker = {
  key: string;
  preview: string;
  getAssistantPreview?: () => string;
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

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), Math.max(minimum, maximum));
}

export function ChatPositionRail({ markers, scrollContainerRef, scopeKey = "" }: ChatPositionRailProps) {
  const { t } = useTranslation();
  const [currentKey, setCurrentKey] = useState<string | null>(null);
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const [previewPosition, setPreviewPosition] = useState<{ left: number; top: number; width: number } | null>(null);
  const markerListRef = useRef<HTMLDivElement>(null);
  const previewRef = useRef<HTMLDivElement>(null);
  const closePreviewTimerRef = useRef<number | null>(null);
  const previewId = useId();
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

  const clearPreviewCloseTimer = useCallback(() => {
    if (closePreviewTimerRef.current !== null) {
      window.clearTimeout(closePreviewTimerRef.current);
      closePreviewTimerRef.current = null;
    }
  }, []);

  const scheduleHoveredPreviewClose = useCallback(() => {
    clearPreviewCloseTimer();
    closePreviewTimerRef.current = window.setTimeout(() => {
      closePreviewTimerRef.current = null;
      setHoveredKey(null);
    }, 180);
  }, [clearPreviewCloseTimer]);

  const activePreviewKey = focusedKey ?? hoveredKey;
  const activePreview = markerLabels.find((marker) => marker.key === activePreviewKey) || null;
  const assistantPreview = useMemo(() => activePreview?.getAssistantPreview?.() || "", [activePreview]);

  useEffect(() => {
    clearPreviewCloseTimer();
    setHoveredKey(null);
    setFocusedKey(null);
  }, [clearPreviewCloseTimer, scopeKey, visible]);

  useEffect(() => () => clearPreviewCloseTimer(), [clearPreviewCloseTimer]);

  useLayoutEffect(() => {
    if (!activePreview) {
      setPreviewPosition(null);
      return undefined;
    }

    const list = markerListRef.current;
    const preview = previewRef.current;
    if (!list || !preview) return undefined;

    let frame = 0;
    const updatePosition = () => {
      const button = Array.from(list.querySelectorAll<HTMLButtonElement>("[data-chat-position-marker]"))
        .find((element) => element.dataset.chatPositionMarker === activePreview.key);
      if (!button || !preview.isConnected) return;

      const anchor = button.getBoundingClientRect();
      const width = Math.max(0, Math.min(480, window.innerWidth - anchor.right - 16));
      const card = preview.getBoundingClientRect();
      const left = anchor.right + 8;
      const top = clamp(anchor.top + anchor.height / 2 - card.height / 2, 8, window.innerHeight - card.height - 8);
      setPreviewPosition((previous) => previous?.left === left && previous.top === top && previous.width === width
        ? previous
        : { left, top, width });
    };
    const schedulePositionUpdate = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(updatePosition);
    };

    updatePosition();
    window.addEventListener("resize", schedulePositionUpdate);
    window.addEventListener("scroll", schedulePositionUpdate, true);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", schedulePositionUpdate);
      window.removeEventListener("scroll", schedulePositionUpdate, true);
    };
  }, [activePreview, scrollContainerRef]);

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
    <>
      <nav className="pointer-events-none absolute inset-y-0 left-0 z-10 flex w-7 items-center justify-center" aria-label={t("chat.positionNavigation")} data-chat-position-rail>
        <div ref={markerListRef} data-chat-position-marker-list className="pointer-events-auto flex max-h-full flex-col items-center overflow-y-auto overscroll-contain py-2">
          {markerLabels.map((marker) => (
            <button
              key={marker.key}
              type="button"
              className="group flex h-1.5 w-7 shrink-0 items-center justify-center focus-visible:outline focus-visible:outline-1 focus-visible:outline-foreground"
              aria-label={marker.label}
              aria-describedby={activePreviewKey === marker.key ? previewId : undefined}
              aria-current={marker.key === currentKey ? "location" : undefined}
              data-chat-position-marker={marker.key}
              onPointerEnter={() => {
                clearPreviewCloseTimer();
                setFocusedKey(null);
                setHoveredKey(marker.key);
              }}
              onPointerLeave={scheduleHoveredPreviewClose}
              onFocus={() => {
                clearPreviewCloseTimer();
                setFocusedKey(marker.key);
              }}
              onBlur={() => setFocusedKey(null)}
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
      {activePreview && typeof document !== "undefined" && createPortal(
        <div
          ref={previewRef}
          id={previewId}
          role="tooltip"
          data-chat-position-preview
          className="fixed z-50 max-h-[min(15rem,calc(100vh-16px))] w-[30rem] max-w-[calc(100vw-16px)] overflow-y-auto overscroll-contain rounded-md border border-border bg-background p-3 text-sm text-foreground shadow-lg"
          style={{
            left: previewPosition?.left ?? 0,
            top: previewPosition?.top ?? 0,
            width: previewPosition?.width ?? (typeof window === "undefined" ? 480 : Math.min(480, window.innerWidth - 16)),
            visibility: previewPosition ? "visible" : "hidden",
          }}
          onPointerEnter={clearPreviewCloseTimer}
          onPointerLeave={scheduleHoveredPreviewClose}
        >
          <p className="whitespace-pre-wrap break-words">{activePreview.preview}</p>
          {assistantPreview ? (
            <div className="mt-3 border-t border-border pt-3 text-muted-foreground" data-chat-position-assistant-preview>
              <ChatMarkdown text={assistantPreview} allowImages={false} />
            </div>
          ) : null}
        </div>,
        document.body,
      )}
    </>
  );
}
