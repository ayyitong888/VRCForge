import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../ui/button";

export function DeleteProjectDialog({
  path,
  onCancel,
  onConfirm,
}: {
  path: string;
  onCancel: () => void;
  onConfirm: (confirmation: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const submittingRef = useRef(false);
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const confirmationMatches = confirmation === "DeLeTe";

  useEffect(() => {
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);

  useEffect(() => {
    if (!submitting) inputRef.current?.focus();
  }, [submitting]);

  async function submit() {
    if (submittingRef.current || !confirmationMatches) return;
    submittingRef.current = true;
    setSubmitting(true);
    setError("");
    try {
      await onConfirm(confirmation);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setConfirmation("");
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="delete-project-title"
      aria-describedby="delete-project-description delete-project-path"
      onCancel={(event) => {
        event.preventDefault();
        if (!submittingRef.current) onCancel();
      }}
      className="m-auto max-h-[calc(100vh-2rem)] w-[min(28rem,90vw)] overflow-y-auto rounded-xl border border-border bg-card p-5 text-foreground shadow-panel backdrop:bg-black/40"
    >
      <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <h2 id="delete-project-title" className="text-base font-semibold text-destructive">{t("project.deleteProject")}</h2>
        <p id="delete-project-description" className="mt-3 text-sm text-muted-foreground">{t("project.deleteProjectDescription")}</p>
        <p id="delete-project-path" className="mt-3 select-text break-all rounded-md bg-muted px-3 py-2 font-mono text-xs">{path}</p>
        <label htmlFor="delete-project-confirmation" className="mt-4 block text-sm">{t("project.deleteProjectPrompt")}</label>
        <input
          ref={inputRef}
          id="delete-project-confirmation"
          autoComplete="off"
          spellCheck={false}
          disabled={submitting}
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
          className="mt-2 w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50"
        />
        {error ? <p role="alert" className="mt-3 break-words text-sm text-destructive">{error}</p> : null}
        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" variant="outline" disabled={submitting} onClick={onCancel}>{t("deleteModal.cancel")}</Button>
          <Button type="submit" variant="danger" disabled={submitting || !confirmationMatches}>
            {t(submitting ? "project.deletingProject" : "project.deleteProject")}
          </Button>
        </div>
      </form>
    </dialog>
  );
}
