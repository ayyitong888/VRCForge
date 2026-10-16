import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import DOMPurify from "dompurify";

export interface MarkdownCodeBlockProps {
  code: string;
  language?: string;
  inverted?: boolean;
}

type CopyState = "idle" | "copied" | "failed";

const MERMAID_LANGUAGES = new Set(["mermaid", "mmd"]);

export function MarkdownCodeBlock({ code, language, inverted = false }: MarkdownCodeBlockProps) {
  const { t } = useTranslation();
  const normalizedLanguage = (language || "").trim().toLowerCase().replace(/^language-/, "");
  const isMermaid = MERMAID_LANGUAGES.has(normalizedLanguage);
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const [mermaidState, setMermaidState] = useState<"idle" | "loading" | "ready" | "failed">(isMermaid ? "loading" : "idle");
  const codeRef = useRef<HTMLElement>(null);
  const mermaidRef = useRef<HTMLDivElement>(null);
  const copyTimerRef = useRef<number>();

  useEffect(() => {
    if (isMermaid || !codeRef.current) return;
    codeRef.current.textContent = code;
    if (!normalizedLanguage) return;
    let cancelled = false;
    void import("highlight.js").then(({ default: hljs }) => {
      if (cancelled || !codeRef.current) return;
      if (!hljs.getLanguage(normalizedLanguage)) return;
      try {
        codeRef.current.innerHTML = sanitizeHighlightHtml(hljs.highlight(code, { language: normalizedLanguage, ignoreIllegals: true }).value);
      } catch { if (codeRef.current) codeRef.current.textContent = code; }
    }).catch(() => { if (!cancelled && codeRef.current) codeRef.current.textContent = code; });
    return () => { cancelled = true; };
  }, [code, isMermaid, normalizedLanguage]);

  useEffect(() => {
    if (!isMermaid || !mermaidRef.current || !code.trim() || code.includes("\u0000")) {
      if (isMermaid) setMermaidState("failed");
      return;
    }
    let cancelled = false;
    setMermaidState("loading");
    void import("mermaid").then(async ({ default: mermaid }) => {
      if (cancelled || !mermaidRef.current) return;
      mermaid.initialize({ startOnLoad: false, securityLevel: "strict", suppressErrorRendering: true, htmlLabels: false, theme: "base" });
      try {
        const id = `markdown-mermaid-${Math.random().toString(36).slice(2)}`;
        const rendered = await mermaid.render(id, code);
        if (cancelled || !mermaidRef.current) return;
        const safe = sanitizeMermaidSvg(rendered.svg);
        mermaidRef.current.replaceChildren(safe);
        setMermaidState("ready");
      } catch {
        if (!cancelled) setMermaidState("failed");
      }
    }).catch(() => {
      if (!cancelled) setMermaidState("failed");
    });
    return () => { cancelled = true; };
  }, [code, isMermaid]);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
    if (copyTimerRef.current) window.clearTimeout(copyTimerRef.current);
    copyTimerRef.current = window.setTimeout(() => setCopyState("idle"), 1800);
  }, [code]);

  useEffect(() => () => { if (copyTimerRef.current) window.clearTimeout(copyTimerRef.current); }, []);

  const surface = inverted ? "border-white/30 bg-white/10 text-white" : "border-border bg-muted/70 text-foreground";
  const button = inverted ? "border-white/30 text-white hover:bg-white/10" : "border-border text-muted-foreground hover:bg-background";
  return (
    <div className={`markdown-code-block my-3 overflow-hidden rounded-md border ${surface}`} data-language={normalizedLanguage || undefined}>
      <div className="flex items-center justify-between border-b border-inherit px-3 py-1.5 text-[11px] opacity-80">
        <span>{normalizedLanguage || "code"}</span>
        <button type="button" className={`rounded border px-2 py-0.5 ${button}`} onClick={copy} aria-label={t("chat.markdown.copy", { defaultValue: "复制代码" })}>
          {copyState === "copied" ? t("chat.markdown.copied", { defaultValue: "已复制" }) : copyState === "failed" ? t("chat.markdown.copyFailed", { defaultValue: "复制失败" }) : t("chat.markdown.copy", { defaultValue: "复制代码" })}
        </button>
      </div>
      {isMermaid ? <div ref={mermaidRef} className={mermaidState === "ready" ? "overflow-auto bg-white p-3 text-slate-900" : "hidden"} aria-label="Mermaid diagram" /> : null}
      {isMermaid && mermaidState === "loading" ? <div className="px-3 py-2 text-xs opacity-70">{t("chat.markdown.mermaidLoading", { defaultValue: "正在渲染 Mermaid…" })}</div> : null}
      {isMermaid && mermaidState === "failed" ? <div className="px-3 py-2 text-xs opacity-70">{t("chat.markdown.mermaidUnavailable", { defaultValue: "Mermaid 预览不可用，以下保留源码。" })}</div> : null}
      <pre className="m-0 max-h-80 overflow-auto p-3 text-xs"><code ref={codeRef} className={normalizedLanguage ? `language-${normalizedLanguage}` : undefined}>{code}</code></pre>
    </div>
  );
}

function sanitizeHighlightHtml(source: string): string {
  return DOMPurify.sanitize(source, { ALLOWED_TAGS: ["span"], ALLOWED_ATTR: ["class"], RETURN_TRUSTED_TYPE: false });
}

function sanitizeMermaidSvg(source: string): DocumentFragment {
  const fragment = DOMPurify.sanitize(source, {
    USE_PROFILES: { svg: true, svgFilters: true },
    FORBID_TAGS: ["script", "a", "foreignObject"],
    FORBID_ATTR: ["href", "xlink:href", "src", "action", "onclick", "onload"],
    ALLOW_UNKNOWN_PROTOCOLS: false,
    RETURN_DOM_FRAGMENT: true,
  }) as DocumentFragment;
  for (const element of [fragment, ...[...fragment.querySelectorAll("*")]]) {
    if (!(element instanceof Element)) continue;
    for (const attribute of [...element.attributes]) {
      if (attribute.name !== "style" && /url\s*\(/i.test(attribute.value) && !/^url\(\s*#[^\s()]+\s*\)$/i.test(attribute.value)) {
        element.removeAttribute(attribute.name);
      }
    }
    const style = element.getAttribute("style");
    if (style && /url\s*\(\s*(?!#)/i.test(style)) element.setAttribute("style", style.replace(/url\s*\([^)]*\)/gi, ""));
    if (element.tagName.toLowerCase() === "style" && element.textContent) {
      element.textContent = element.textContent.replace(/@import[^;]+;/gi, "").replace(/url\s*\(\s*(?!#)[^)]*\)/gi, "");
    }
  }
  return fragment;
}
