import { createContext, isValidElement, useContext, useEffect, useId, useMemo, useState } from "react";
import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import type { Components } from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import remarkCjkFriendly from "remark-cjk-friendly";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { useTranslation } from "react-i18next";

import { isInternalRuntimeUrl } from "../../lib/runtime-url";
import { cn } from "../../lib/utils";
import { chatFileHref, localChatFilePath, openChatFile } from "../../lib/chat-file-link";
import { MarkdownCodeBlock } from "./markdown-code-block";
import { loadChatResourceImage, type ChatResourceImageContext as ChatResourceImageRequestContext } from "../../lib/api/chat-resource-image";

export const ChatResourceImageContext = createContext<ChatResourceImageRequestContext | null>(null);

export function ChatMarkdown({ text, variant = "agent" }: { text?: string; variant?: "agent" | "user" }) {
  const inverted = variant === "user";
  const resourceContext = useContext(ChatResourceImageContext);
  const components = useMemo(() => buildChatMarkdownComponents(inverted), [inverted]);
  const instanceId = useId().replace(/[^A-Za-z0-9_-]/g, "");
  const clobberPrefix = useMemo(() => `vrcforge-${instanceId}-`, [instanceId]);
  const sanitizeSchema = useMemo(() => ({ ...CHAT_MARKDOWN_SANITIZE_SCHEMA, clobberPrefix }), [clobberPrefix]);
  if (!text?.trim()) {
    return null;
  }
  return (
    <div
      className={cn("chat-markdown min-w-0 break-words leading-relaxed", inverted ? "text-primary-foreground" : "text-foreground")}
      data-testid={`chat-markdown-${variant}`}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkCjkFriendly, remarkMath, remarkChatFileLinks]}
        remarkRehypeOptions={{ clobberPrefix: "" }}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, sanitizeSchema], [scopeMarkdownFragments, clobberPrefix], [rehypeKatex, { trust: false }]]}
        components={components}
        urlTransform={(url, key, node) => safeMarkdownUrlTransform(url, key, node?.tagName, resourceContext)}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

const CHAT_MARKDOWN_SANITIZE_SCHEMA = {
  ...defaultSchema,
  tagNames: [
    ...new Set([
      ...(defaultSchema.tagNames || []),
      "details",
      "summary",
      "input",
      "kbd",
      "mark",
    ]),
  ],
  attributes: {
    ...defaultSchema.attributes,
    "*": [...(defaultSchema.attributes?.["*"] || []), "id"],
    a: [
      ...(defaultSchema.attributes?.a || []),
      "target",
      "rel",
    ],
    input: [
      ...(defaultSchema.attributes?.input || []),
      ["type", "checkbox"],
      "checked",
      "disabled",
      "aria-checked",
    ],
    code: [
      ["className", /^language-[A-Za-z0-9_+-]+$/, "math-inline", "math-display"],
    ],
    span: [
      ...(defaultSchema.attributes?.span || []),
      ["className", /^math-inline$/],
    ],
    div: [
      ...(defaultSchema.attributes?.div || []),
      ["className", /^math-display$/],
    ],
  },
  protocols: {
    ...defaultSchema.protocols,
    href: [...(defaultSchema.protocols?.href || []), "vrcforge-file"],
    src: [
      ...new Set([...(defaultSchema.protocols?.src || []), "data", "vrcforge"]),
    ],
  },
};

function buildChatMarkdownComponents(inverted: boolean): Components {
  const textClass = inverted ? "text-primary-foreground" : "text-foreground";
  const mutedClass = inverted ? "text-primary-foreground/80" : "text-muted-foreground";
  const borderClass = inverted ? "border-primary-foreground/35" : "border-border";
  const softBgClass = inverted ? "bg-primary-foreground/15" : "bg-muted/70";
  const linkClass = inverted ? "text-primary-foreground underline underline-offset-2" : "text-blue-600 dark:text-blue-400 underline underline-offset-2";
  return {
    h1: ({ node: _node, ...props }) => <h1 className={cn("mb-3 mt-5 text-xl font-semibold leading-snug first:mt-0", textClass)} {...props} />,
    h2: ({ node: _node, ...props }) => <h2 className={cn("mb-3 mt-5 text-lg font-semibold leading-snug first:mt-0", textClass)} {...props} />,
    h3: ({ node: _node, ...props }) => <h3 className={cn("mb-2 mt-4 text-base font-semibold leading-snug first:mt-0", textClass)} {...props} />,
    h4: ({ node: _node, ...props }) => <h4 className={cn("mb-2 mt-4 text-sm font-semibold leading-snug first:mt-0", textClass)} {...props} />,
    h5: ({ node: _node, ...props }) => <h5 className={cn("mb-2 mt-4 text-sm font-semibold leading-snug first:mt-0", mutedClass)} {...props} />,
    h6: ({ node: _node, ...props }) => <h6 className={cn("mb-2 mt-4 text-xs font-semibold uppercase leading-snug first:mt-0", mutedClass)} {...props} />,
    p: ({ node: _node, ...props }) => <p className={cn("my-3 first:mt-0 last:mb-0", inverted && "whitespace-pre-wrap")} {...props} />,
    a: ({ node: _node, href, ...props }) => {
      const filePath = localChatFilePath(String(href || ""));
      if (filePath) return <ChatFileLink path={filePath} className={linkClass} {...props} />;
      const safeHref = safeMarkdownUrlTransform(String(href || ""));
      if (!safeHref) {
        return <span {...props} />;
      }
      if (safeHref.startsWith("#")) {
        return <a className={linkClass} {...props} href={safeHref} target={undefined} />;
      }
      return <a className={linkClass} {...props} href={safeHref} target="_blank" rel="noreferrer" />;
    },
    blockquote: ({ node: _node, ...props }) => <blockquote className={cn("my-3 border-l-2 pl-3", borderClass, mutedClass)} {...props} />,
    ul: ({ node: _node, className, ...props }) => <ul className={cn("my-3 list-disc space-y-1.5 pl-5 first:mt-0 last:mb-0", className)} {...props} />,
    ol: ({ node: _node, className, ...props }) => <ol className={cn("my-3 list-decimal space-y-1.5 pl-5 first:mt-0 last:mb-0", className)} {...props} />,
    li: ({ node: _node, className, ...props }) => <li className={cn("pl-1", inverted ? "marker:text-primary-foreground/80" : "marker:text-muted-foreground", className)} {...props} />,
    table: ({ node: _node, ...props }) => (
      <div className={cn("app-scrollbar my-3 overflow-auto rounded-md border first:mt-0 last:mb-0", borderClass)}>
        <table className="min-w-full border-collapse text-xs" {...props} />
      </div>
    ),
    thead: ({ node: _node, ...props }) => <thead className={inverted ? "bg-primary-foreground/15" : "bg-muted/70"} {...props} />,
    th: ({ node: _node, ...props }) => <th className={cn("border-b border-r px-3 py-2 text-left font-semibold last:border-r-0", borderClass)} {...props} />,
    td: ({ node: _node, ...props }) => <td className={cn("border-b border-r px-3 py-2 align-top last:border-r-0", borderClass)} {...props} />,
    tr: ({ node: _node, ...props }) => <tr className="[&:last-child>td]:border-b-0" {...props} />,
    hr: ({ node: _node, ...props }) => <hr className={cn("my-4", borderClass)} {...props} />,
    pre: ({ node: _node, children: _children }) => {
      const codeElement = Array.isArray(_children) ? _children[0] : _children;
      const codeProps = isValidElement(codeElement) ? codeElement.props as { className?: unknown; children?: unknown } : {};
      const className = typeof codeProps.className === "string" ? codeProps.className : "";
      const language = className.match(/(?:^|\s)language-([A-Za-z0-9_+-]+)/)?.[1];
      return <MarkdownCodeBlock code={markdownCodeText(codeProps.children)} language={language} inverted={inverted} />;
    },
    code: ({ node: _node, className, ...props }) => <code className={cn("rounded px-1 py-0.5 font-mono text-[0.92em]", softBgClass, className)} {...props} />,
    strong: ({ node: _node, ...props }) => <strong className={cn("font-semibold", textClass)} {...props} />,
    em: ({ node: _node, ...props }) => <em className="italic" {...props} />,
    del: ({ node: _node, ...props }) => <del className={mutedClass} {...props} />,
    input: ({ node: _node, className, ...props }) => <input className={cn("mr-2 align-middle", className)} disabled {...props} />,
    mark: ({ node: _node, ...props }) => <mark className={inverted ? "rounded bg-primary-foreground px-1 text-primary" : "rounded bg-yellow-200 px-1 text-yellow-950"} {...props} />,
    img: ({ node: _node, ...props }) => <ChatMarkdownImage inverted={inverted} {...props} />,
  };
}

function remarkChatFileLinks() {
  return (tree: { children?: Array<Record<string, unknown>> }) => {
    walkMarkdownTree(tree, node => {
      if (node.type === "link" && typeof node.url === "string") {
        const href = chatFileHref(node.url);
        if (href) node.url = href;
      }
    });
  };
}

function ChatFileLink({ path, children, className }: ComponentPropsWithoutRef<"a"> & { path: string }) {
  const context = useContext(ChatResourceImageContext);
  const [error, setError] = useState("");
  const [opening, setOpening] = useState(false);
  if (!context) return <span>{children}</span>;
  return <><a className={className} href={chatFileHref(path) || undefined} aria-busy={opening} onClick={event => {
    event.preventDefault();
    if (opening) return;
    setOpening(true); setError("");
    void openChatFile(context, path).catch(cause => setError(String(cause instanceof Error ? cause.message : cause)))
      .finally(() => setOpening(false));
  }}>{children}</a>{error && <span role="status" className="ml-2 text-sm text-destructive">{error}</span>}</>;
}

function ChatMarkdownImage({ inverted, alt, src, ...props }: ComponentPropsWithoutRef<"img"> & { inverted: boolean }) {
  const resourceContext = useContext(ChatResourceImageContext);
  const source = String(src || "");
  const className = cn("my-3 max-h-96 max-w-full rounded-md border object-contain", inverted ? "border-primary-foreground/35" : "border-border");
  if (isChatResourceImageUri(source) && resourceContext) {
    return <AuthenticatedChatResourceImage uri={source} alt={alt || ""} context={resourceContext} className={className} />;
  }
  const safeSrc = safeMarkdownUrlTransform(source);
  if (!safeSrc) return alt ? <span>{alt}</span> : null;
  return <img className={className} src={safeSrc} alt={alt || ""} {...props} />;
}

function markdownCodeText(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(markdownCodeText).join("");
  if (isValidElement(value)) return markdownCodeText((value.props as { children?: unknown }).children);
  return value == null ? "" : String(value);
}

function scopeMarkdownFragments(prefix: string) {
  return (tree: { children?: Array<Record<string, unknown>> }) => {
    const ids = new Set<string>();
    walkMarkdownTree(tree, (node) => {
      const properties = node.properties as Record<string, unknown> | undefined;
      if (typeof properties?.id === "string") {
        // Footnote identifiers may contain URI escapes; fragments navigate to decoded IDs.
        try { properties.id = decodeURIComponent(properties.id); } catch { /* Preserve literal malformed escapes. */ }
        ids.add(properties.id as string);
      }
    });
    walkMarkdownTree(tree, (node) => {
      const properties = node.properties as Record<string, unknown> | undefined;
      if (!properties) return;
      if (typeof properties.href === "string" && properties.href.startsWith("#")) {
        properties.href = `#${resolveMarkdownFragment(properties.href.slice(1), ids, prefix)}`;
      }
      if (Array.isArray(properties.ariaDescribedBy)) {
        properties.ariaDescribedBy = properties.ariaDescribedBy.map((id) => resolveMarkdownFragment(String(id), ids, prefix));
      } else if (typeof properties.ariaDescribedBy === "string") {
        properties.ariaDescribedBy = properties.ariaDescribedBy.split(/\s+/).map((id) => resolveMarkdownFragment(id, ids, prefix)).join(" ");
      }
    });
  };
}

function resolveMarkdownFragment(fragment: string, ids: Set<string>, prefix: string): string {
  let decoded = fragment;
  try { decoded = decodeURIComponent(fragment); } catch { /* Keep malformed escapes as literal fragments. */ }
  if (ids.has(decoded)) return decoded;
  const prefixed = `${prefix}${decoded}`;
  if (ids.has(prefixed)) return prefixed;
  return fragment;
}

function walkMarkdownTree(node: { children?: Array<Record<string, unknown>> } | null | undefined, visit: (node: Record<string, unknown>) => void) {
  if (!node) return;
  visit(node as Record<string, unknown>);
  for (const child of node.children || []) walkMarkdownTree(child, visit);
}

function safeMarkdownUrlTransform(url: string, key?: string, tagName?: string, resourceContext?: ChatResourceImageRequestContext | null): string {
  const trimmed = url.trim();
  if (!trimmed) {
    return "";
  }
  if (key === "src" && tagName === "img" && resourceContext && isChatResourceImageUri(trimmed)) {
    return trimmed;
  }
  if (key === "href" && tagName === "a" && resourceContext && trimmed.startsWith("vrcforge-file:")) {
    return chatFileHref(trimmed) || "";
  }
  if (isInternalRuntimeUrl(trimmed)) {
    return "";
  }
  if (/^(?:https?:|mailto:|tel:|#|\/(?!\/))/i.test(trimmed)) {
    return defaultUrlTransform(trimmed);
  }
  if (/^data:image\/(?:png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=]+$/i.test(trimmed)) {
    return trimmed;
  }
  return "";
}

function isChatResourceImageUri(value: string): boolean {
  return /^vrcforge:\/\/scene-capture-preview\/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\?context=runtime%3A[A-Za-z0-9._~-]+&revision=[1-9][0-9]*&store=[0-9a-f]{32}$/i.test(value);
}

function AuthenticatedChatResourceImage({ uri, alt, context, className }: { uri: string; alt: string; context: ChatResourceImageRequestContext; className: string }) {
  const [src, setSrc] = useState("");
  const [unavailable, setUnavailable] = useState(false);
  const { t } = useTranslation();
  useEffect(() => {
    const controller = new AbortController();
    setSrc("");
    setUnavailable(false);
    void loadChatResourceImage(context.endpoint, { ...context, uri }, controller.signal)
      .then((result) => {
        if (result.uri !== uri || result.mimeType !== "image/png" || !/^[0-9a-f]{64}$/i.test(result.sha256) || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(result.pngBase64) || result.pngBase64.length > 1_400_000) {
          throw new Error("Invalid authenticated chat image response.");
        }
        setSrc(`data:image/png;base64,${result.pngBase64}`);
      })
      .catch(() => {
        if (!controller.signal.aborted) setUnavailable(true);
      });
    return () => controller.abort();
  }, [context.endpoint, context.chatId, context.projectPath, context.sessionId, context.clientTurnId, uri]);
  if (!src) {
    if (unavailable) return <span role="status">{alt ? `${alt} · ${t("chat.imageUnavailable")}` : t("chat.imageUnavailable")}</span>;
    return alt ? <span>{alt}</span> : null;
  }
  return <img className={className} src={src} alt={alt} />;
}
