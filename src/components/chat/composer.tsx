import { Archive, Camera, Check, ChevronDown, Globe, MessageSquare, MousePointer2, Paperclip, Pencil, Plus, Send, Shield, Square, X } from "lucide-react";
import { type ClipboardEvent, type DragEvent, type FormEvent, type ReactNode, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "../../i18n";
import { executionModeLabel, EXECUTION_MODES, permissionVisualState } from "../../lib/permission-ui";
import { cn, formatCount } from "../../lib/utils";
import { formatAttachmentSize } from "../../lib/chat-format";
import type { ChatAttachment, ComposerAction, ComposerActionId, ComposerSlashCommand, ContextUsage } from "../../lib/chat-types";
import { SELECTED_TEXT_ATTACHMENT_NAME } from "../../lib/chat-types";
import type { PermissionState, ExecutionMode } from "../../lib/api";
import { Button } from "../ui/button";

function composerActionIcon(action: ComposerActionId): ReactNode {
  switch (action) {
    case "attach":
      return <Paperclip className="h-4 w-4" />;
    case "screenshot":
      return <Camera className="h-4 w-4" />;
    case "annotation":
      return <Pencil className="h-4 w-4" />;
    case "browser":
      return <Globe className="h-4 w-4" />;
    case "desktop":
      return <MousePointer2 className="h-4 w-4" />;
    default:
      return <Plus className="h-4 w-4" />;
  }
}



type ComposerFileInput = FileList | File[] | null;

export function Composer({
  input,
  setInput,
  sending,
  permission,
  onSubmit,
  onStop,
  onSwitchMode,
  commands = [],
  actions = [],
  onAction,
  compact = false,
  disabledReason = "",
  attachments = [],
  onAttachFiles,
  onRemoveAttachment,
  contextUsage,
  providerLabel,
  model,
  projects: _projects = [],
  onBindProject: _onBindProject,
}: {
  input: string;
  setInput: (value: string) => void;
  sending: boolean;
  permission?: PermissionState;
  onSubmit: (event?: FormEvent) => void;
  onStop?: () => void;
  onSwitchMode: (mode: PermissionState["executionMode"]) => void;
  commands?: Array<{ name: string; title: string }>;
  actions?: ComposerAction[];
  onAction?: (action: ComposerActionId) => void | Promise<void>;
  compact?: boolean;
  disabledReason?: string;
  attachments?: ChatAttachment[];
  onAttachFiles?: (files: ComposerFileInput) => void;
  onRemoveAttachment?: (id: string) => void;
  contextUsage?: ContextUsage;
  providerLabel?: string;
  model?: string;
  projects?: Array<{ key: string; name: string }>;
  onBindProject?: (path: string) => void;
}) {
  const { t } = useTranslation();
  const [modeMenuOpen, setModeMenuOpen] = useState(false);
  const [actionMenuOpen, setActionMenuOpen] = useState(false);
  const [dragDepth, setDragDepth] = useState(0);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const currentMode = (permission?.executionMode || "approval") as ExecutionMode;
  const currentModeVisual = permissionVisualState(permission, currentMode);
  const canSubmit = !disabledReason && (input.trim().length > 0 || attachments.length > 0);
  const commandActions: ComposerSlashCommand[] = actions.map((action) => ({
    name: action.id === "desktop" ? "desktop-rescue" : action.id,
    title: action.disabled ? action.disabledReason || action.description : action.description,
    action,
  }));
  const slashQuery = input.startsWith("/") && !input.includes(" ") && !input.includes("\n") ? input.slice(1).toLowerCase() : null;
  const slashMatches: ComposerSlashCommand[] =
    slashQuery !== null
      ? [...commands.map((command) => ({ ...command })), ...commandActions].filter((command) => command.name.toLowerCase().includes(slashQuery)).slice(0, 8)
      : [];
  const visibleActions: ComposerAction[] = actions.length
    ? actions
    : [{ id: "attach", label: t("composerAction.attach"), description: t("composerAction.attachDesc") }];
  const dragActive = dragDepth > 0;
  const hasDraggedFiles = (event: DragEvent) => Array.from(event.dataTransfer.types || []).includes("Files");
  const handleDragEnter = (event: DragEvent<HTMLFormElement>) => {
    if (!hasDraggedFiles(event)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    setDragDepth((depth) => depth + 1);
  };
  const handleDragOver = (event: DragEvent<HTMLFormElement>) => {
    if (!hasDraggedFiles(event)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
  };
  const handleDragLeave = (event: DragEvent<HTMLFormElement>) => {
    if (!hasDraggedFiles(event)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    setDragDepth((depth) => Math.max(0, depth - 1));
  };
  const handleDrop = (event: DragEvent<HTMLFormElement>) => {
    if (!hasDraggedFiles(event)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    setDragDepth(0);
    onAttachFiles?.(event.dataTransfer.files);
  };
  const handlePaste = (event: ClipboardEvent<HTMLFormElement>) => {
    const pastedFiles = [
      ...Array.from(event.clipboardData.files || []),
      ...Array.from(event.clipboardData.items || [])
        .filter((item) => item.kind === "file")
        .map((item) => item.getAsFile())
        .filter((file): file is File => Boolean(file)),
    ];
    const seen = new Set<string>();
    const files = pastedFiles.filter((file) => {
      const key = `${file.name}:${file.size}:${file.type}:${file.lastModified}`;
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
    if (!files.length) {
      return;
    }
    event.preventDefault();
    onAttachFiles?.(files);
  };
  return (
    <form
      onSubmit={onSubmit}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onPaste={handlePaste}
      className={cn("relative rounded-3xl bg-muted/70 shadow-composer", dragActive && "ring-2 ring-primary/35")}
    >
      {slashMatches.length > 0 ? (
        <div className="absolute bottom-full left-0 right-0 z-20 mb-2 overflow-hidden rounded-xl border border-border bg-card shadow-panel">
          {slashMatches.map((command) => (
            <button
              key={command.name}
              type="button"
              className={cn(
                "flex w-full min-w-0 items-center gap-3 px-3 py-2 text-left hover:bg-muted",
                command.action?.disabled ? "opacity-60" : "",
              )}
              onClick={() => {
                if (command.action) {
                  setInput("");
                  if (command.action.id === "attach" && !command.action.disabled) {
                    fileInputRef.current?.click();
                  } else {
                    onAction?.(command.action.id);
                  }
                  return;
                }
                setInput(`/${command.name} `);
              }}
            >
              <span className="shrink-0 font-mono text-xs text-primary">/{command.name}</span>
              <span className="truncate text-xs text-muted-foreground">{command.title}</span>
            </button>
          ))}
        </div>
      ) : null}
      <div className={cn("rounded-3xl border bg-card transition-colors", dragActive ? "border-primary/50 bg-primary/5" : "border-border", compact ? "p-3" : "p-4")}>
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          className="min-h-[76px] w-full resize-none bg-transparent px-1 text-base outline-none placeholder:text-muted-foreground"
          placeholder={disabledReason || t("chat.inputPlaceholder")}
          disabled={Boolean(disabledReason)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              onSubmit();
            }
          }}
        />
        {attachments.length ? (
          <div className="mt-3">
            <AttachmentStrip attachments={attachments} onRemove={onRemoveAttachment} />
          </div>
        ) : null}
        <div className="mt-3 flex min-w-0 items-center justify-between gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(event) => {
                onAttachFiles?.(event.currentTarget.files);
                event.currentTarget.value = "";
              }}
            />
            <button
              type="button"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
              onClick={() => setActionMenuOpen((open) => !open)}
              title={t("composerAction.addContext")}
            >
              <Plus className="h-4 w-4" />
            </button>
            {actionMenuOpen ? <div className="fixed inset-0 z-20" onClick={() => setActionMenuOpen(false)} /> : null}
            {actionMenuOpen ? (
              <div className="absolute bottom-[96px] left-4 z-30 w-80 rounded-lg border border-border bg-card p-1.5 shadow-panel">
                {visibleActions.map((action) => (
                  <button
                    key={action.id}
                    type="button"
                    className={cn(
                      "flex w-full min-w-0 items-start gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                      action.disabled ? "cursor-not-allowed opacity-55" : "hover:bg-muted",
                    )}
                    onClick={() => {
                      setActionMenuOpen(false);
                      if (action.disabled) {
                        onAction?.(action.id);
                        return;
                      }
                      if (action.id === "attach") {
                        fileInputRef.current?.click();
                        return;
                      }
                      onAction?.(action.id);
                    }}
                    title={action.disabled ? action.disabledReason : action.description}
                  >
                    <span className="mt-0.5 shrink-0 text-muted-foreground">{composerActionIcon(action.id)}</span>
                    <span className="min-w-0">
                      <span className="block truncate font-medium">{action.label}</span>
                      <span className="block text-xs text-muted-foreground">
                        {action.disabled ? action.disabledReason || t("common.unavailable") : action.description}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            ) : null}
            <div className="relative">
              <button
                type="button"
                className={cn("flex h-8 min-w-0 max-w-full items-center gap-2 rounded-md px-2 text-sm transition-colors", currentModeVisual.textClass, currentModeVisual.hoverClass)}
                onClick={() => setModeMenuOpen((open) => !open)}
              >
                <Shield className="h-4 w-4 shrink-0" />
                <span className="truncate">{executionModeLabel(currentMode)}</span>
                <ChevronDown className="h-3.5 w-3.5 shrink-0" />
              </button>
              {modeMenuOpen ? <div className="fixed inset-0 z-20" onClick={() => setModeMenuOpen(false)} /> : null}
              {modeMenuOpen ? (
                <div className="absolute bottom-10 left-0 z-30 w-72 rounded-lg border border-border bg-card p-1.5 shadow-panel">
                  {EXECUTION_MODES.map((mode) => {
                    const modeVisual = permissionVisualState(undefined, mode.value);
                    return (
                      <button
                        key={mode.value}
                        type="button"
                        className={cn(
                          "flex w-full items-start gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                          modeVisual.hoverClass,
                          currentMode === mode.value ? "bg-muted" : "",
                        )}
                        onClick={() => {
                          setModeMenuOpen(false);
                          if (mode.value !== currentMode) {
                            onSwitchMode(mode.value);
                          }
                        }}
                      >
                        <Check className={cn("mt-0.5 h-4 w-4 shrink-0", currentMode === mode.value ? modeVisual.textClass : "opacity-0")} />
                        <span className="min-w-0">
                          <span className={cn("block font-medium", modeVisual.textClass)}>
                            {mode.label}
                          </span>
                          <span className="block text-xs text-muted-foreground">{mode.description}</span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
            {providerLabel || model ? (
              <span className="max-w-[260px] truncate px-1 text-sm text-muted-foreground">
                {providerLabel || t("provider.apiProvider")}{model ? ` · ${model}` : ""}
              </span>
            ) : null}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {contextUsage ? (
              <ContextUsageMeter usage={contextUsage} className="w-36" />
            ) : null}
            {sending ? (
              <Button type="button" variant="outline" className="h-10 w-10 rounded-full px-0" onClick={onStop} title={t("chat.stop")}>
                <Square className="h-4 w-4" />
              </Button>
            ) : null}
            <Button
              className="h-10 min-w-10 rounded-full px-3"
              disabled={!canSubmit}
              type="submit"
              title={sending ? t("chat.queue") : t("chat.send")}
              aria-label={sending ? t("chat.queue") : t("chat.send")}
            >
              {sending ? <span className="text-xs">{t("chat.queue")}</span> : <Send className="h-4 w-4" />}
            </Button>
          </div>
        </div>
      </div>
    </form>
  );
}



export function ContextUsageMeter({ usage, className = "" }: { usage: ContextUsage; className?: string }) {
  const knownRatio = usage.limitKnown && usage.exact;
  const percent = knownRatio ? Math.round(Math.min(1, Math.max(0, usage.ratio)) * 100) : 0;
  const fillColorClass = percent >= 90 ? "bg-destructive" : percent >= 60 ? "bg-amber-500" : "bg-primary";
  const tooltipTitle = usage.cached
    ? i18n.t("chat.contextUsageCached", { value: knownRatio ? `${percent}%` : usage.label })
    : knownRatio
      ? i18n.t("chat.contextMeterPercentUsed", { percent })
      : i18n.t("chat.contextUsageUnavailable");
  const tooltipDetail = knownRatio
    ? i18n.t("chat.contextMeterTokenDetail", { used: formatCount(usage.used), limit: formatCount(usage.limit) })
    : "";
  const nativeTitle = tooltipDetail ? `${tooltipTitle}\n${tooltipDetail}` : tooltipTitle;
  return (
    <div
      className={cn("group relative flex h-8 w-32 shrink-0 items-center rounded-md px-1", className)}
      tabIndex={0}
      aria-label={nativeTitle}
      title={nativeTitle}
      data-context-meter="true"
      data-context-percent={knownRatio ? String(percent) : "unknown"}
    >
      <div className="h-2.5 w-full overflow-hidden rounded-full border border-border bg-muted">
        {knownRatio ? (
          <div
            className={cn("h-full rounded-full transition-[width,background-color] duration-500", fillColorClass)}
            style={{ width: `${percent}%` }}
            data-context-segment={percent >= 90 ? "high" : percent >= 60 ? "medium" : "low"}
          />
        ) : (
          <div className="h-full w-full bg-muted-foreground/35" data-context-segment="unknown" />
        )}
      </div>
      <div className="pointer-events-none absolute bottom-full left-1/2 z-40 mb-2 hidden w-52 -translate-x-1/2 rounded-lg border border-border bg-card px-3 py-2 text-center text-xs text-foreground shadow-panel group-hover:block group-focus:block">
        <div className="font-medium">{i18n.t("chat.contextMeterTitle")}</div>
        <div className="mt-1 text-muted-foreground">{tooltipTitle}</div>
        {tooltipDetail ? <div className="mt-1 text-muted-foreground">{tooltipDetail}</div> : null}
      </div>
    </div>
  );
}



export function AttachmentStrip({
  attachments,
  onRemove,
  compact = false,
}: {
  attachments: ChatAttachment[];
  onRemove?: (id: string) => void;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  if (!attachments.length) {
    return null;
  }
  if (!compact) {
    return (
      <div className="flex min-w-0 flex-wrap gap-2">
        {attachments.map((attachment) => {
          const isSelectedText = attachment.name === SELECTED_TEXT_ATTACHMENT_NAME;
          const isImage = Boolean(attachment.dataUrl && attachment.type.startsWith("image/"));
          const extension = attachmentExtension(attachment, t("attachments.fileTypeFallback"));
          const selectedPreview = isSelectedText ? (attachment.text || "").replace(/\s+/g, " ").trim().slice(0, 260) : "";
          return (
            <div
              key={attachment.id}
              className={cn(
                "group relative overflow-hidden rounded-xl border border-border bg-background text-foreground shadow-sm",
                isImage ? "h-28 w-28" : "h-[72px] w-[220px]",
                isSelectedText && "h-auto min-h-10 w-auto max-w-full rounded-full px-3 py-2",
              )}
              title={isSelectedText ? undefined : `${attachment.name} · ${formatAttachmentSize(attachment.size)}`}
            >
              {isImage && attachment.dataUrl ? (
                <img src={attachment.dataUrl} alt={attachment.name} className="h-full w-full object-cover" />
              ) : isSelectedText ? (
                <div className="flex min-w-0 items-center gap-2 text-xs">
                  <MessageSquare className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 max-w-[220px] truncate">{t("attachments.selectedText", { count: 1 })}</span>
                </div>
              ) : (
                <div className="flex h-full min-w-0 items-center gap-3 px-3">
                  <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-muted">
                    <Archive className="h-5 w-5 text-muted-foreground" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{attachment.name}</div>
                    <div className="mt-0.5 flex items-center gap-2 text-xs uppercase text-muted-foreground">
                      <span>{extension}</span>
                      {attachment.truncated ? <span className="normal-case text-amber-700">{t("attachments.metadataOnly")}</span> : null}
                    </div>
                  </div>
                </div>
              )}
              {isSelectedText && selectedPreview ? (
                <div className="pointer-events-none absolute bottom-[calc(100%+0.5rem)] left-0 z-50 hidden w-max max-w-[min(32rem,calc(100vw-3rem))] rounded-lg border border-border bg-popover px-3 py-2 text-sm leading-relaxed text-popover-foreground shadow-panel group-hover:block">
                  {selectedPreview}
                  {attachment.text && attachment.text.length > selectedPreview.length ? "..." : ""}
                </div>
              ) : null}
              {onRemove ? (
                <button
                  type="button"
                  className={cn(
                    "absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-full transition-colors",
                    isImage ? "bg-foreground text-background hover:bg-foreground/85" : "bg-foreground text-background hover:bg-foreground/85",
                    isSelectedText && "static ml-2 inline-flex align-middle",
                  )}
                  onClick={() => onRemove(attachment.id)}
                  title={t("attachments.remove")}
                  aria-label={t("attachments.remove")}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              ) : null}
            </div>
          );
        })}
      </div>
    );
  }
  return (
    <div className={cn("flex min-w-0 flex-wrap gap-2", compact ? "mt-2" : "")}>
      {attachments.map((attachment) => {
        const isSelectedText = attachment.name === SELECTED_TEXT_ATTACHMENT_NAME;
        const selectedPreview = isSelectedText ? (attachment.text || "").replace(/\s+/g, " ").trim().slice(0, 260) : "";
        return (
          <div
            key={attachment.id}
            className={cn("group relative flex max-w-full min-w-0 items-center gap-2 rounded-md border border-border/70 bg-background/75 px-2 py-1 text-xs text-foreground shadow-sm", isSelectedText && "rounded-full")}
            title={isSelectedText ? undefined : `${attachment.name} · ${formatAttachmentSize(attachment.size)}`}
          >
            {attachment.dataUrl && attachment.type.startsWith("image/") ? (
              <img src={attachment.dataUrl} alt="" className="h-8 w-8 shrink-0 rounded object-cover" />
            ) : isSelectedText ? (
              <MessageSquare className="h-4 w-4 shrink-0 text-muted-foreground" />
            ) : (
              <Archive className="h-4 w-4 shrink-0 text-muted-foreground" />
            )}
            <span className="min-w-0 max-w-[220px] truncate">{isSelectedText ? t("attachments.selectedText", { count: 1 }) : attachment.name}</span>
            {!isSelectedText ? <span className="shrink-0 text-muted-foreground">{formatAttachmentSize(attachment.size)}</span> : null}
            {attachment.truncated ? <span className="shrink-0 text-amber-700">{t("attachments.metadataOnly")}</span> : null}
            {isSelectedText && selectedPreview ? (
              <div className="pointer-events-none absolute bottom-[calc(100%+0.5rem)] left-0 z-50 hidden w-max max-w-[min(32rem,calc(100vw-3rem))] rounded-lg border border-border bg-popover px-3 py-2 text-sm leading-relaxed text-popover-foreground shadow-panel group-hover:block">
                {selectedPreview}
                {attachment.text && attachment.text.length > selectedPreview.length ? "..." : ""}
              </div>
            ) : null}
            {onRemove ? (
              <button
                type="button"
                className="shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                onClick={() => onRemove(attachment.id)}
                title={t("attachments.remove")}
                aria-label={t("attachments.remove")}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function attachmentExtension(attachment: ChatAttachment, fallback: string): string {
  const namePart = attachment.name.includes(".") ? attachment.name.split(".").pop() || "" : "";
  if (namePart) {
    return namePart.slice(0, 8);
  }
  const typePart = attachment.type.includes("/") ? attachment.type.split("/").pop() || "" : attachment.type;
  return (typePart || fallback).slice(0, 10);
}
