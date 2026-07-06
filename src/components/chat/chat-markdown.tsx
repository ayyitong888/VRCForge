import { useMemo } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import type { Components } from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { isInternalRuntimeUrl } from "../../lib/runtime-url";
import { cn } from "../../lib/utils";

export function ChatMarkdown({ text, variant = "agent" }: { text?: string; variant?: "agent" | "user" }) {
  const inverted = variant === "user";
  const components = useMemo(() => buildChatMarkdownComponents(inverted), [inverted]);
  if (!text?.trim()) {
    return null;
  }
  return (
    <div
      className={cn("chat-markdown break-words leading-relaxed", inverted ? "text-primary-foreground" : "text-foreground")}
      data-testid={`chat-markdown-${variant}`}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, CHAT_MARKDOWN_SANITIZE_SCHEMA]]}
        components={components}
        urlTransform={safeMarkdownUrlTransform}
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
      ...(defaultSchema.attributes?.code || []),
      ["className", /^language-[A-Za-z0-9_-]+$/],
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
    src: [
      ...new Set([...(defaultSchema.protocols?.src || []), "data"]),
    ],
  },
};

function buildChatMarkdownComponents(inverted: boolean): Components {
  const textClass = inverted ? "text-primary-foreground" : "text-foreground";
  const mutedClass = inverted ? "text-primary-foreground/80" : "text-muted-foreground";
  const borderClass = inverted ? "border-primary-foreground/35" : "border-border";
  const softBgClass = inverted ? "bg-primary-foreground/15" : "bg-muted/70";
  const linkClass = inverted ? "text-primary-foreground underline underline-offset-2" : "text-primary underline underline-offset-2";
  return {
    h1: ({ node: _node, ...props }) => <h1 className={cn("mb-3 mt-5 text-xl font-semibold leading-snug first:mt-0", textClass)} {...props} />,
    h2: ({ node: _node, ...props }) => <h2 className={cn("mb-3 mt-5 text-lg font-semibold leading-snug first:mt-0", textClass)} {...props} />,
    h3: ({ node: _node, ...props }) => <h3 className={cn("mb-2 mt-4 text-base font-semibold leading-snug first:mt-0", textClass)} {...props} />,
    h4: ({ node: _node, ...props }) => <h4 className={cn("mb-2 mt-4 text-sm font-semibold leading-snug first:mt-0", textClass)} {...props} />,
    h5: ({ node: _node, ...props }) => <h5 className={cn("mb-2 mt-4 text-sm font-semibold leading-snug first:mt-0", mutedClass)} {...props} />,
    h6: ({ node: _node, ...props }) => <h6 className={cn("mb-2 mt-4 text-xs font-semibold uppercase leading-snug first:mt-0", mutedClass)} {...props} />,
    p: ({ node: _node, ...props }) => <p className="my-3 whitespace-pre-wrap first:mt-0 last:mb-0" {...props} />,
    a: ({ node: _node, href, ...props }) => {
      const safeHref = safeMarkdownUrlTransform(String(href || ""));
      if (!safeHref) {
        return <span {...props} />;
      }
      return <a className={linkClass} href={safeHref} target="_blank" rel="noreferrer" {...props} />;
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
    tr: ({ node: _node, ...props }) => <tr className="last:[&>td]:border-b-0" {...props} />,
    hr: ({ node: _node, ...props }) => <hr className={cn("my-4", borderClass)} {...props} />,
    pre: ({ node: _node, ...props }) => <pre className={cn("app-scrollbar my-3 max-h-80 overflow-auto rounded-md border p-3 text-xs first:mt-0 last:mb-0", borderClass, softBgClass)} {...props} />,
    code: ({ node: _node, className, ...props }) => <code className={cn("rounded px-1 py-0.5 font-mono text-[0.92em]", softBgClass, className)} {...props} />,
    strong: ({ node: _node, ...props }) => <strong className={cn("font-semibold", textClass)} {...props} />,
    em: ({ node: _node, ...props }) => <em className="italic" {...props} />,
    del: ({ node: _node, ...props }) => <del className={mutedClass} {...props} />,
    input: ({ node: _node, className, ...props }) => <input className={cn("mr-2 align-middle", className)} disabled {...props} />,
    mark: ({ node: _node, ...props }) => <mark className={inverted ? "rounded bg-primary-foreground px-1 text-primary" : "rounded bg-yellow-200 px-1 text-yellow-950"} {...props} />,
    img: ({ node: _node, alt, src, ...props }) => {
      const safeSrc = safeMarkdownUrlTransform(String(src || ""));
      if (!safeSrc) {
        return alt ? <span>{alt}</span> : null;
      }
      return <img className={cn("my-3 max-h-96 max-w-full rounded-md border object-contain", borderClass)} src={safeSrc} alt={alt || ""} {...props} />;
    },
  };
}

function safeMarkdownUrlTransform(url: string): string {
  const trimmed = url.trim();
  if (!trimmed) {
    return "";
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
