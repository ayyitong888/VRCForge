import { useId, useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "./button";

export type DialogAction = {
  title: string;
  description?: string;
  confirmLabel: string;
  destructive?: boolean;
  initialValue?: string;
  onConfirm?: (value: string) => void;
};

export function ActionDialog({ action, onClose }: { action: DialogAction; onClose: () => void }) {
  const { t } = useTranslation();
  const titleId = useId();
  const descriptionId = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  useLayoutEffect(() => {
    const element = dialog.current!;
    element.showModal();
    if (input.current) {
      input.current.focus();
      input.current.select();
    }
    return () => element.close();
  }, []);
  const close = () => {
    dialog.current?.close();
    onClose();
  };

  return (
    <dialog ref={dialog} aria-labelledby={titleId} aria-describedby={action.description ? descriptionId : undefined}
      onCancel={event => { event.preventDefault(); close(); }}
      className="m-auto w-[min(26rem,90vw)] rounded-xl border border-border bg-card p-5 text-foreground shadow-panel backdrop:bg-black/40">
      <form onSubmit={event => {
        event.preventDefault();
        const value = input.current?.value ?? "";
        close();
        action.onConfirm?.(value);
      }}>
        <h2 id={titleId} className="text-base font-semibold">{action.title}</h2>
        {action.description && <p id={descriptionId} className="mt-2 break-words text-sm text-muted-foreground">{action.description}</p>}
        {action.initialValue !== undefined && <input ref={input} aria-labelledby={titleId} defaultValue={action.initialValue}
          className="mt-4 w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring" />}
        <div className="mt-5 flex justify-end gap-2">
          {action.onConfirm && <Button type="button" variant="outline" onClick={close}>{t("common.cancel")}</Button>}
          <Button type="submit" variant={action.destructive ? "danger" : "primary"}>{action.confirmLabel}</Button>
        </div>
      </form>
    </dialog>
  );
}
