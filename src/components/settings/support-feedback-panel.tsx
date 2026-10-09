import { ExternalLink, FileText, Loader2 } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { openSupportFeedbackPage, previewLatestSupportBundle, previewSupportFeedback } from "../../lib/api";
import { Button } from "../ui/button";

export function SupportFeedbackPanel({ endpoint }: { endpoint: string }) {
  const { t } = useTranslation();
  const [summary, setSummary] = useState("");
  const [steps, setSteps] = useState("");
  const [expected, setExpected] = useState("");
  const [actual, setActual] = useState("");
  const [bundleReady, setBundleReady] = useState(false);
  const [preview, setPreview] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [bundlePreview, setBundlePreview] = useState<Array<{ name: string; bytes: number; truncated?: boolean; preview: string }>>([]);
  const [bundleName, setBundleName] = useState("");
  const [bundleSha256, setBundleSha256] = useState("");
  const previewRequestSequence = useRef(0);

  async function createPreview() {
    const requestSequence = ++previewRequestSequence.current;
    const request = { summary, steps, expected, actual, supportBundleAvailable: bundleReady };
    setBusy(true);
    setMessage("");
    try {
      const result = await previewSupportFeedback(endpoint, request);
      if (requestSequence !== previewRequestSequence.current) return;
      setPreview(result.text);
      setMessage(t("settings.supportFeedbackPreviewReady"));
    } catch (cause) {
      if (requestSequence !== previewRequestSequence.current) return;
      setMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function reviewLatestBundle() {
    const requestSequence = ++previewRequestSequence.current;
    setPreview("");
    setBundleReady(false);
    setBundlePreview([]);
    setBundleName("");
    setBundleSha256("");
    setBusy(true);
    setMessage("");
    try {
      const result = await previewLatestSupportBundle(endpoint);
      if (requestSequence !== previewRequestSequence.current) return;
      setBundleName(result.bundleName || "");
      setBundleSha256(result.bundleSha256 || "");
      setBundlePreview(result.members || []);
      setMessage(result.available ? t("settings.supportFeedbackBundlePreviewReady") : t("settings.supportFeedbackBundleMissing"));
    } catch (cause) {
      if (requestSequence !== previewRequestSequence.current) return;
      setMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function copyPreview() {
    if (!preview || !navigator.clipboard?.writeText) {
      setMessage(t("settings.supportFeedbackCopyUnavailable"));
      return;
    }
    await navigator.clipboard.writeText(preview);
    setMessage(t("settings.supportFeedbackCopied"));
  }

  function openIssuePage() {
    void openSupportFeedbackPage().then(
      () => setMessage(t("settings.supportFeedbackPageOpened")),
      (cause) => setMessage(cause instanceof Error ? cause.message : String(cause)),
    );
  }

  return (
    <div className="mt-5 rounded-xl border border-border bg-card p-4" data-vrcforge-support-feedback>
      <div className="flex items-center gap-2">
        <FileText className="h-4 w-4" />
        <div className="text-sm font-medium">{t("settings.supportFeedbackTitle")}</div>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{t("settings.supportFeedbackDesc")}</p>
      <div className="mt-3 grid gap-3">
        {([
          ["summary", summary, setSummary],
          ["steps", steps, setSteps],
          ["expected", expected, setExpected],
          ["actual", actual, setActual],
        ] as const).map(([field, value, setter]) => (
          <label key={field} className="grid gap-1 text-xs">
            <span className="font-medium">{t(`settings.supportFeedback${field[0].toUpperCase()}${field.slice(1)}`)}</span>
            <textarea
              value={value}
              onChange={(event) => {
                previewRequestSequence.current += 1;
                setter(event.currentTarget.value);
                setPreview("");
              }}
              rows={field === "summary" ? 2 : 3}
              className="min-h-0 rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
            />
          </label>
        ))}
      </div>
      <label className="mt-3 flex items-start gap-2 text-xs text-muted-foreground">
        <input type="checkbox" checked={bundleReady} onChange={(event) => { previewRequestSequence.current += 1; setBundleReady(event.currentTarget.checked); setPreview(""); }} />
        <span>{t("settings.supportFeedbackBundleReady")}</span>
      </label>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button type="button" variant="outline" disabled={busy} onClick={() => void reviewLatestBundle()}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
          {t("settings.supportFeedbackReviewBundle")}
        </Button>
        <Button type="button" variant="outline" disabled={busy} onClick={() => void createPreview()}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
          {t("settings.supportFeedbackPreview")}
        </Button>
        <Button type="button" variant="outline" disabled={!preview} onClick={() => void copyPreview()}>
          {t("settings.supportFeedbackCopy")}
        </Button>
        <Button type="button" variant="outline" onClick={openIssuePage}>
          <ExternalLink className="h-4 w-4" />
          {t("settings.supportFeedbackOpenPage")}
        </Button>
      </div>
      {message ? <p className="mt-2 text-xs text-muted-foreground">{message}</p> : null}
      {preview ? (
        <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-background p-3 text-xs" data-vrcforge-support-feedback-preview>
          {preview}
        </pre>
      ) : null}
      {bundlePreview.length ? (
        <div className="mt-3 grid gap-2" data-vrcforge-support-bundle-preview>
          <div className="text-xs font-medium">{t("settings.supportFeedbackBundleContents", { name: bundleName })}</div>
          {bundleSha256 ? <div className="break-all text-[11px] text-muted-foreground">SHA-256: {bundleSha256}</div> : null}
          {bundlePreview.map((member) => (
            <details key={member.name} className="rounded-md border border-border bg-background p-2 text-xs">
              <summary className="cursor-pointer">{member.name} ({member.bytes} bytes){member.truncated ? ` — ${t("settings.supportFeedbackBundleTruncated")}` : ""}</summary>
              <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap">{member.preview}</pre>
            </details>
          ))}
        </div>
      ) : null}
      <p className="mt-2 text-xs text-muted-foreground">{t("settings.supportFeedbackBoundary")}</p>
    </div>
  );
}
