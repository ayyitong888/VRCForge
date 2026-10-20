import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  activateChatGptOAuthModel,
  fetchChatGptOAuthModels,
  fetchChatGptOAuthStatus,
  type ChatGptOAuthModel,
} from "./api";

export function ChatGptOAuthComposerReasoningControl({
  endpoint,
  model,
  thinkingLevel,
  disabled,
  onActivated,
}: {
  endpoint: string;
  model: string;
  thinkingLevel: string;
  disabled: boolean;
  onActivated: () => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const [levels, setLevels] = useState<NonNullable<ChatGptOAuthModel["supportedReasoningLevels"]>>([]);
  const [accountId, setAccountId] = useState("");
  const [loadState, setLoadState] = useState<"loading" | "ready" | "unavailable">("loading");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [menuPosition, setMenuPosition] = useState<{ left: number; bottom: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!menuPosition) return;
    const closeOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!triggerRef.current?.contains(target) && !menuRef.current?.contains(target)) setMenuPosition(null);
    };
    const close = () => setMenuPosition(null);
    document.addEventListener("pointerdown", closeOutside);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    const selected = menuRef.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]');
    (selected || menuRef.current?.querySelector<HTMLButtonElement>("button"))?.focus();
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [menuPosition]);

  useEffect(() => {
    if (disabled || saving) setMenuPosition(null);
  }, [disabled, saving]);

  useEffect(() => {
    const controller = new AbortController();
    setLevels([]);
    setAccountId("");
    setLoadState("loading");
    setError("");
    setMenuPosition(null);
    void (async () => {
      try {
        const status = await fetchChatGptOAuthStatus(endpoint, controller.signal);
        const activeAccountId = status.activeAccountId || "";
        if (!activeAccountId || status.activeModel !== model) {
          setLoadState("unavailable");
          return;
        }
        const account = status.accounts.find((item) => item.id === activeAccountId && item.connected);
        if (!account) {
          setLoadState("unavailable");
          return;
        }
        const result = await fetchChatGptOAuthModels(endpoint, activeAccountId, controller.signal);
        if (controller.signal.aborted) return;
        const selectedModel = result.models.find((item) => item.id === model);
        const supported = selectedModel?.supportedReasoningLevels ?? [];
        setAccountId(activeAccountId);
        setLevels(supported);
        setLoadState(supported.length ? "ready" : "unavailable");
      } catch {
        if (!controller.signal.aborted) setLoadState("unavailable");
      }
    })();
    return () => controller.abort();
  }, [endpoint, model]);

  if (loadState === "unavailable") return null;

  const selectedSupported = levels.some((item) => item.effort === thinkingLevel);
  const value = selectedSupported ? thinkingLevel : "";
  const locked = disabled || saving || loadState === "loading";
  const openMenu = () => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect || locked) return;
    setMenuPosition({ left: Math.max(8, Math.min(rect.left, window.innerWidth - 152)), bottom: window.innerHeight - rect.top + 6 });
  };
  const closeMenu = () => {
    setMenuPosition(null);
    triggerRef.current?.focus();
  };
  const navigateMenu = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("button") || []);
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === "Escape" || event.key === "Tab") {
      if (event.key === "Escape") event.preventDefault();
      closeMenu();
    } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
        : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[next]?.focus();
    }
  };
  const changeLevel = async (nextLevel: string) => {
    if (!accountId || !nextLevel || nextLevel === value || !levels.some((item) => item.effort === nextLevel) || saving || disabled) return;
    setSaving(true);
    setError("");
    try {
      await activateChatGptOAuthModel(endpoint, accountId, model, nextLevel);
      await onActivated();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="inline-flex h-7 min-w-0 items-center">
      <button
        ref={triggerRef}
        type="button"
        aria-label={t("provider.reasoningEffort")}
        aria-haspopup="menu"
        aria-expanded={Boolean(menuPosition)}
        aria-controls={menuPosition ? menuId : undefined}
        data-chatgpt-composer-reasoning-trigger
        data-reasoning-value={value}
        className={`inline-flex h-7 items-center gap-1 rounded-md border-0 bg-transparent px-1 text-xs shadow-none outline-none transition-colors hover:bg-muted/60 focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50 ${value === "ultra" ? "text-violet-600 dark:text-violet-400" : "text-muted-foreground"}`}
        disabled={locked}
        onClick={() => menuPosition ? closeMenu() : openMenu()}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); openMenu(); }
        }}
        title={error || undefined}
      >
        <span>{value ? t(`provider.reasoning_${value}`, value) : t(loadState === "loading" ? "chatgptOAuth.reasoningLoading" : "chatgptOAuth.reasoningSelect")}</span>
        <ChevronDown aria-hidden="true" className="h-3 w-3 text-muted-foreground/75" />
      </button>
      {menuPosition ? createPortal(
        <div ref={menuRef} id={menuId} role="menu" aria-label={t("provider.reasoningEffort")}
          style={{ left: menuPosition.left, bottom: menuPosition.bottom }}
          onKeyDown={navigateMenu}
          className="fixed z-[80] min-w-36 rounded-xl border border-border/60 bg-card p-1 text-card-foreground shadow-lg shadow-black/10">
          {levels.map((item) => (
            <button key={item.effort} type="button" role="menuitemradio" aria-checked={value === item.effort}
              tabIndex={value === item.effort || (!value && item === levels[0]) ? 0 : -1}
              onClick={() => { closeMenu(); void changeLevel(item.effort); }}
              className={`flex h-8 w-full items-center justify-between gap-6 rounded-lg px-2.5 text-left text-xs outline-none hover:bg-muted focus:bg-muted ${item.effort === "ultra" ? "text-violet-600 dark:text-violet-400" : "text-muted-foreground"}`}>
              <span>{t(`provider.reasoning_${item.effort}`, item.effort)}</span>
              {value === item.effort ? <Check aria-hidden="true" className="h-3.5 w-3.5" /> : <span className="w-3.5" />}
            </button>
          ))}
        </div>, document.body,
      ) : null}
      {error ? <span role="alert" className="sr-only">{error}</span> : null}
    </div>
  );
}
