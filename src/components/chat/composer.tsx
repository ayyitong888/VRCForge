import { Archive, Camera, Check, ChevronDown, Globe, MessageSquare, MousePointer2, Paperclip, Pencil, Plus, Send, Shield, Square, Target, X } from "lucide-react";
import { getCurrentWebview, type DragDropEvent } from "@tauri-apps/api/webview";
import { convertFileSrc } from "@tauri-apps/api/core";
import { type ClipboardEvent, type DragEvent, type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "../../i18n";
import { executionModeLabel, EXECUTION_MODES, permissionVisualState } from "../../lib/permission-ui";
import { cn, formatCount } from "../../lib/utils";
import { formatAttachmentSize } from "../../lib/chat-format";
import type { ChatAttachment, ComposerAction, ComposerActionId, ComposerSlashCommand, ContextUsage } from "../../lib/chat-types";
import { SELECTED_TEXT_ATTACHMENT_NAME } from "../../lib/chat-types";
import type { AgentGoal, PermissionState, ExecutionMode } from "../../lib/api";
import { Button } from "../ui/button";
import { hasTauriInternals } from "../../lib/api/http";

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
  queueAllowed = false,
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
  goalEndpoint,
  activeGoal,
  editing = false,
  onCancelEdit,
  projects: _projects = [],
  onBindProject: _onBindProject,
}: {
  input: string;
  setInput: (value: string) => void;
  sending: boolean;
  queueAllowed?: boolean;
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
  goalEndpoint?: string;
  activeGoal?: AgentGoal | null;
  editing?: boolean;
  onCancelEdit?: () => void;
  projects?: Array<{ key: string; name: string }>;
  onBindProject?: (path: string) => void;
}) {
  const { t } = useTranslation();
  const [modeMenuOpen, setModeMenuOpen] = useState(false);
  const [actionMenuOpen, setActionMenuOpen] = useState(false);
  const [paletteIndex, setPaletteIndex] = useState(0);
  const [paletteDismissed, setPaletteDismissed] = useState(false);
  const [dragDepth, setDragDepth] = useState(0);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const composerRef = useRef<HTMLFormElement | null>(null);
  const currentMode = (permission?.executionMode || "approval") as ExecutionMode;
  const currentModeVisual = permissionVisualState(permission, currentMode);
  const canSubmit = !disabledReason && (input.trim().length > 0 || attachments.length > 0);
  const availableActions: ComposerAction[] = actions.length ? actions : [{ id: "attach", label: t("composerAction.attach"), description: t("composerAction.attachDesc") }];
  const commandActions: ComposerSlashCommand[] = availableActions.map((action) => ({
    name: action.id,
    title: action.disabled ? action.disabledReason || action.description : action.description,
    action,
  }));
  const slashQuery = !editing && input.startsWith("/") && !input.includes(" ") && !input.includes("\n") ? input.slice(1).toLowerCase() : null;
  const slashMatches: ComposerSlashCommand[] = [];
  if (slashQuery !== null) {
    const seen = new Set<string>();
    for (const command of [...commands.map((item) => ({ ...item })), ...commandActions]) {
      const name = command.name.toLowerCase();
      if (!name.includes(slashQuery) || seen.has(name)) {
        continue;
      }
      slashMatches.push(command);
      seen.add(name);
      if (slashMatches.length >= 8) {
        break;
      }
    }
  }
  const paletteOpen = actionMenuOpen || (!paletteDismissed && slashMatches.length > 0);
  const paletteCommands: ComposerSlashCommand[] = actionMenuOpen ? commandActions : slashMatches;
  const providerModelLabel = [providerLabel?.trim(), model?.trim()].filter(Boolean).join(" · ");
  const hasProviderIdentity = Boolean(providerLabel?.trim() || model?.trim());
  const effectiveContextUsage = contextUsage
    ?? (hasProviderIdentity
      ? {
          used: 0,
          limit: 0,
          limitKnown: false,
          source: "unavailable" as const,
          exact: false,
          ratio: 0,
          label: i18n.t("chat.contextUsageUnavailable"),
          title: "",
          warning: false,
        }
      : undefined);
  useEffect(() => {
    setPaletteIndex((current) => Math.min(current, Math.max(0, paletteCommands.length - 1)));
  }, [actionMenuOpen, paletteCommands.length, slashQuery]);
  const dragActive = dragDepth > 0;
  const hasDraggedFiles = (event: DragEvent) =>
    Array.from(event.dataTransfer.types || []).includes("Files") ||
    event.dataTransfer.files.length > 0 ||
    Array.from(event.dataTransfer.items || []).some((item) => item.kind === "file");
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
  useEffect(() => {
    if (!hasTauriInternals()) return;
    let unlisten: (() => void) | undefined;
    void getCurrentWebview().onDragDropEvent(async (event: { payload: DragDropEvent }) => {
      const payload = event.payload;
      if (payload.type !== "drop" || !composerRef.current) return;
      const ratio = window.devicePixelRatio || 1;
      const bounds = composerRef.current.getBoundingClientRect();
      const x = payload.position.x / ratio;
      const y = payload.position.y / ratio;
      if (x < bounds.left || x > bounds.right || y < bounds.top || y > bounds.bottom) return;
      const files: File[] = [];
      for (const path of payload.paths) {
        try {
          const response = await fetch(convertFileSrc(path));
          if (!response.ok) throw new Error(`Dropped file could not be read (${response.status}).`);
          const blob = await response.blob();
          const name = path.split(/[\\/]/).pop() || "dropped-file";
          files.push(new File([blob], name, { type: blob.type }));
        } catch {
          // The existing attachment pipeline reports metadata/read failures after this point.
        }
      }
      if (files.length) onAttachFiles?.(files);
    }).then((cleanup) => { unlisten = cleanup; });
    return () => { unlisten?.(); };
  }, [onAttachFiles]);
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
      ref={composerRef}
      onSubmit={onSubmit}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onPaste={handlePaste}
      data-vrcforge-composer-dropzone
      className={cn("relative rounded-3xl bg-muted/70 shadow-composer", dragActive && "ring-2 ring-primary/35")}
    >
      {paletteOpen ? (
        <div className="absolute bottom-full left-0 right-0 z-30 mb-2 max-h-72 overflow-y-auto rounded-xl border border-border bg-card p-1 shadow-panel" data-composer-command-palette>
          {paletteCommands.map((command, index) => (
            <button
              key={command.name}
              type="button"
              data-composer-slash-command={command.name}
              data-composer-action={command.action?.id}
              data-composer-palette-item
              disabled={Boolean(command.action?.disabled)}
              className={cn("flex w-full min-w-0 items-center gap-3 rounded-lg px-3 py-2 text-left transition-colors hover:bg-muted", command.action?.disabled ? "opacity-60" : "", index === paletteIndex ? "bg-muted" : "")}
              onMouseEnter={() => setPaletteIndex(index)}
              onClick={() => {
                setActionMenuOpen(false);
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
              <span className="shrink-0 text-muted-foreground">{command.action ? composerActionIcon(command.action.id) : <MessageSquare className="h-4 w-4" />}</span>
              <span className="grid min-w-0 flex-1 grid-cols-[minmax(0,auto)_minmax(0,1fr)] items-center gap-3"><span className="truncate text-sm font-medium">{slashMatches.length && !actionMenuOpen ? `/${command.name}` : (command.action?.label || command.name)}</span><span className="truncate text-right text-xs text-muted-foreground">{command.title}</span></span>
            </button>
          ))}
        </div>
      ) : null}
      <div className={cn("rounded-3xl border bg-card transition-colors", dragActive ? "border-primary/50 bg-primary/5" : "border-border", compact ? "p-3" : "p-4")}>
        <textarea
          value={input}
          onChange={(event) => { setPaletteDismissed(false); setInput(event.target.value); }}
          className="min-h-[76px] w-full resize-none bg-transparent px-1 text-base outline-none placeholder:text-muted-foreground"
          placeholder={disabledReason || t("chat.inputPlaceholder")}
          disabled={Boolean(disabledReason)}
          onKeyDown={(event) => {
            if (paletteOpen && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
              event.preventDefault();
              setPaletteIndex((current) => event.key === "ArrowDown"
                ? (current + 1) % Math.max(1, paletteCommands.length)
                : (current - 1 + paletteCommands.length) % Math.max(1, paletteCommands.length));
              return;
            }
            if (paletteOpen && event.key === "Escape") {
              event.preventDefault();
              setActionMenuOpen(false);
              setPaletteDismissed(true);
              return;
            }
            if (paletteOpen && event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              const command = paletteCommands[paletteIndex];
              if (command && !command.action?.disabled) {
                event.preventDefault();
                if (command.action) {
                  setActionMenuOpen(false);
                  setInput("");
                  if (command.action.id === "attach") fileInputRef.current?.click();
                  else onAction?.(command.action.id);
                } else setInput(`/${command.name} `);
                return;
              }
            }
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
        <div className="mt-3 grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 xl:grid-cols-[auto_minmax(0,1fr)_auto]">
          <div className="col-start-1 row-start-1 flex min-w-0 items-center gap-2">
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
              data-composer-action-menu
            >
              <Plus className="h-4 w-4" />
            </button>
            {actionMenuOpen ? <div className="fixed inset-0 z-20" onClick={() => setActionMenuOpen(false)} /> : null}
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
            {!editing && goalEndpoint && activeGoal ? (
              <span
                className="flex h-8 items-center gap-1.5 rounded-md px-2 text-sm text-muted-foreground"
                data-composer-goal
              >
                <Target className="h-4 w-4 shrink-0" />
                <span>{t("goal.managementTitle", "Goals")}</span>
              </span>
            ) : null}
          </div>
          <div className="col-start-2 row-start-1 flex shrink-0 items-center gap-2 xl:col-start-3">
            {effectiveContextUsage ? (
              <ContextUsageMeter usage={effectiveContextUsage} />
            ) : null}
            {editing && !sending ? (
              <>
                <Button type="button" variant="outline" className="h-9 rounded-md px-3" onClick={onCancelEdit} title={t("chat.cancelEdit")}>
                  {t("chat.cancelEdit")}
                </Button>
                <Button className="h-9 rounded-md px-3" disabled={!canSubmit} type="submit" title={t("chat.saveEdit")} aria-label={t("chat.saveEdit")}>
                  {t("chat.saveEdit")}
                </Button>
              </>
            ) : sending ? (
              <>
                <Button type="button" variant="outline" className="h-10 w-10 rounded-full px-0" onClick={onStop} title={t("chat.stop")} data-composer-stop>
                  <Square className="h-4 w-4" />
                </Button>
                {queueAllowed ? (
                  <Button
                    className="h-10 min-w-10 rounded-full px-3"
                    disabled={!canSubmit}
                    type="submit"
                    title={t("chat.queue")}
                    aria-label={t("chat.queue")}
                    data-composer-send
                  >
                    <Send className="h-4 w-4" />
                  </Button>
                ) : null}
              </>
            ) : (
              <Button
                className="h-10 min-w-10 rounded-full px-3"
                disabled={!canSubmit}
                type="submit"
                title={t("chat.send")}
                aria-label={t("chat.send")}
                data-composer-send
              >
                <Send className="h-4 w-4" />
              </Button>
            )}
          </div>
          {providerModelLabel ? (
            <span
              className="col-span-2 col-start-1 row-start-2 min-w-0 break-words px-1 text-sm text-muted-foreground xl:col-span-1 xl:col-start-2 xl:row-start-1 xl:whitespace-nowrap"
              title={providerModelLabel}
              aria-label={providerModelLabel}
            >
              {providerModelLabel}
            </span>
          ) : null}
        </div>
      </div>
    </form>
  );
}



export function ContextUsageMeter({ usage, className = "" }: { usage: ContextUsage; className?: string }) {
  const knownRatio = usage.limitKnown && usage.exact;
  const percent = knownRatio ? Math.round(Math.min(1, Math.max(0, usage.ratio)) * 100) : 0;
  const strokeColorClass = percent >= 90 ? "stroke-destructive" : percent >= 60 ? "stroke-amber-500" : "stroke-primary";
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
      className={cn("group relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full", className)}
      tabIndex={0}
      aria-label={nativeTitle}
      title={nativeTitle}
      data-context-meter="true"
      data-context-percent={knownRatio ? String(percent) : "unknown"}
    >
      <svg className="h-6 w-6 -rotate-90" viewBox="0 0 24 24" aria-hidden="true" data-context-ring>
        <circle cx="12" cy="12" r="9" fill="none" strokeWidth="2.5" className="stroke-border" />
        {knownRatio ? (
          <circle
            cx="12"
            cy="12"
            r="9"
            pathLength="100"
            fill="none"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeDasharray={`${percent} 100`}
            className={cn("transition-[stroke-dasharray,stroke] duration-500", strokeColorClass)}
            data-context-segment={percent >= 90 ? "high" : percent >= 60 ? "medium" : "low"}
          />
        ) : (
          <circle
            cx="12"
            cy="12"
            r="9"
            pathLength="100"
            fill="none"
            strokeWidth="2.5"
            className="stroke-muted-foreground/45"
            data-context-segment="unknown"
          />
        )}
      </svg>
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
  onImport,
  compact = false,
}: {
  attachments: ChatAttachment[];
  onRemove?: (id: string) => void;
  /** 1.3.2: import affordance for vault-stored attachments (archives / oversized images). */
  onImport?: (attachment: ChatAttachment) => void;
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
          const isVaultFile = attachment.payloadKind === "vault_file" || Boolean(attachment.vaultPayloadHash);
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
                      {isVaultFile ? <span className="normal-case text-sky-700">{t("attachments.vaultStored")}</span> : null}
                      {isVaultFile && onImport ? (
                        <button
                          type="button"
                          className="shrink-0 rounded border border-border px-1.5 py-0.5 normal-case text-foreground transition-colors hover:bg-muted"
                          onClick={() => onImport(attachment)}
                          title={t("attachments.importToProject")}
                        >
                          {t("attachments.importToProject")}
                        </button>
                      ) : null}
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
              {isImage && isVaultFile && onImport ? (
                <button
                  type="button"
                  className="absolute bottom-2 left-2 rounded border border-border bg-background/90 px-1.5 py-0.5 text-[10px] text-foreground shadow-sm"
                  onClick={() => onImport(attachment)}
                  title={t("attachments.importToProject")}
                >
                  {t("attachments.importToProject")}
                </button>
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
        const isVaultFile = attachment.payloadKind === "vault_file" || Boolean(attachment.vaultPayloadHash);
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
            {isVaultFile ? <span className="shrink-0 text-sky-700">{t("attachments.vaultStored")}</span> : null}
            {isVaultFile && onImport ? (
              <button
                type="button"
                className="shrink-0 rounded border border-border px-1.5 py-0.5 text-foreground transition-colors hover:bg-muted"
                onClick={() => onImport(attachment)}
                title={t("attachments.importToProject")}
              >
                {t("attachments.importToProject")}
              </button>
            ) : null}
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
