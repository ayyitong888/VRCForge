import { Minus, Plus, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export type ChatImageSource = { src: string; alt: string };

export function ChatImagePreview({ src, alt, onClose }: ChatImageSource & { onClose: () => void }) {
  const { t } = useTranslation();
  const dialog = useRef<HTMLDialogElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const [size, setSize] = useState<{ width: number; height: number }>();
  const [zoom, setZoom] = useState(1);
  const changeZoom = useCallback((step: number) => {
    setZoom((current) => Math.min(4, Math.max(0.5, current + step)));
  }, []);

  useEffect(() => {
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
    const target = image.current;
    const onWheel = (event: WheelEvent) => {
      if (!event.deltaY) return;
      event.preventDefault();
      changeZoom(event.deltaY < 0 ? 0.25 : -0.25);
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
      <div className="app-scrollbar absolute inset-0 overflow-auto overscroll-contain" onClick={() => dialog.current?.close()}>
        <div className="flex min-h-full w-max min-w-full items-center justify-center px-6 py-16">
          <img
            ref={image}
            src={src}
            alt={alt}
            draggable={false}
            className="block h-auto max-w-none shrink-0 select-none rounded-lg"
            style={{ width: fittedWidth ? `calc(${fittedWidth} * ${zoom})` : undefined }}
            onLoad={({ currentTarget }) => setSize({ width: currentTarget.naturalWidth, height: currentTarget.naturalHeight })}
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
