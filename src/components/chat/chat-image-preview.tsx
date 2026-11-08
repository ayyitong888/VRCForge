import { Minus, Plus, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type PointerEvent } from "react";
import { useTranslation } from "react-i18next";

export type ChatImageSource = { src: string; alt: string };

export function ChatImagePreview({ src, alt, onClose }: ChatImageSource & { onClose: () => void }) {
  const { t } = useTranslation();
  const dialog = useRef<HTMLDialogElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const zoomAnchor = useRef<{ x: number; y: number; imageX: number; imageY: number }>();
  const drag = useRef<{ pointerId: number; x: number; y: number; left: number; top: number }>();
  const [size, setSize] = useState<{ width: number; height: number }>();
  const [zoom, setZoom] = useState(1);
  const [dragging, setDragging] = useState(false);
  const changeZoom = useCallback((step: number, point?: { x: number; y: number }) => {
    const container = viewport.current;
    const bounds = image.current?.getBoundingClientRect();
    if (container && bounds?.width && bounds.height) {
      const viewportBounds = container.getBoundingClientRect();
      const x = point?.x ?? viewportBounds.left + container.clientWidth / 2;
      const y = point?.y ?? viewportBounds.top + container.clientHeight / 2;
      zoomAnchor.current = {
        x, y, imageX: (x - bounds.left) / bounds.width, imageY: (y - bounds.top) / bounds.height,
      };
    }
    setZoom((current) => Math.min(4, Math.max(0.5, current + step)));
  }, []);

  useLayoutEffect(() => {
    const anchor = zoomAnchor.current;
    zoomAnchor.current = undefined;
    const bounds = image.current?.getBoundingClientRect();
    const container = viewport.current;
    if (!anchor || !bounds || !container) return;
    container.scrollLeft += bounds.left + bounds.width * anchor.imageX - anchor.x;
    container.scrollTop += bounds.top + bounds.height * anchor.imageY - anchor.y;
  }, [zoom]);

  function startDrag(event: PointerEvent<HTMLImageElement>) {
    const container = viewport.current;
    if (!container || event.button !== 0 || !event.isPrimary
      || (container.scrollWidth <= container.clientWidth && container.scrollHeight <= container.clientHeight)) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      pointerId: event.pointerId, x: event.clientX, y: event.clientY,
      left: container.scrollLeft, top: container.scrollTop,
    };
    setDragging(true);
  }

  function moveImage(event: PointerEvent<HTMLImageElement>) {
    const start = drag.current;
    const container = viewport.current;
    if (!start || start.pointerId !== event.pointerId || !container) return;
    container.scrollLeft = start.left + start.x - event.clientX;
    container.scrollTop = start.top + start.y - event.clientY;
  }

  function stopDrag(event: PointerEvent<HTMLImageElement>) {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = undefined;
    setDragging(false);
  }

  useEffect(() => {
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
    const target = image.current;
    const onWheel = (event: WheelEvent) => {
      if (!event.deltaY) return;
      event.preventDefault();
      changeZoom(event.deltaY < 0 ? 0.25 : -0.25, { x: event.clientX, y: event.clientY });
    };
    target?.addEventListener("wheel", onWheel, { passive: false });
    return () => target?.removeEventListener("wheel", onWheel);
  }, [changeZoom]);

  const fittedWidth = size
    ? `min(${size.width}px, 100vw - 3rem, (100dvh - 8rem) * ${size.width / size.height})`
    : undefined;
  const buttonClass = "flex h-9 w-9 items-center justify-center rounded-full bg-black/70 text-white hover:bg-black focus-visible:outline focus-visible:outline-2 focus-visible:outline-white disabled:opacity-50";
  return (
    <dialog
      ref={dialog}
      className="fixed inset-0 m-0 h-full max-h-none w-full max-w-none overflow-hidden bg-transparent p-0 text-white backdrop:bg-black/75"
      aria-label={t("chat.imagePreview")}
      onClose={onClose}
    >
      <div ref={viewport} className="app-scrollbar absolute inset-0 overflow-auto overscroll-contain" onClick={() => dialog.current?.close()}>
        <div className="flex min-h-full w-max min-w-full items-center justify-center px-6 py-16">
          <img
            ref={image}
            src={src}
            alt={alt}
            draggable={false}
            className={"block h-auto max-w-none shrink-0 touch-none select-none rounded-lg" + (dragging ? " cursor-grabbing" : zoom > 1 ? " cursor-grab" : "")}
            style={{ width: fittedWidth ? `calc(${fittedWidth} * ${zoom})` : undefined }}
            onLoad={({ currentTarget }) => setSize({ width: currentTarget.naturalWidth, height: currentTarget.naturalHeight })}
            onPointerDown={startDrag}
            onPointerMove={moveImage}
            onLostPointerCapture={stopDrag}
            onClick={(event) => event.stopPropagation()}
          />
        </div>
      </div>
      <div className="absolute right-5 top-5 flex items-center gap-2">
        <button type="button" className={`${buttonClass} order-last`} aria-label={t("chat.closeImagePreview")} title={t("chat.closeImagePreview")} onClick={() => dialog.current?.close()}>
          <X className="h-5 w-5" aria-hidden="true" />
        </button>
        <button type="button" className={buttonClass} aria-label={t("chat.zoomOut")} title={t("chat.zoomOut")} onClick={() => changeZoom(-0.25)} disabled={zoom <= 0.5}>
          <Minus className="h-4 w-4" aria-hidden="true" />
        </button>
        <span className="min-w-12 rounded-full bg-black/70 px-2 py-1 text-center text-xs tabular-nums">{Math.round(zoom * 100)}%</span>
        <button type="button" className={buttonClass} aria-label={t("chat.zoomIn")} title={t("chat.zoomIn")} onClick={() => changeZoom(0.25)} disabled={zoom >= 4}>
          <Plus className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    </dialog>
  );
}
