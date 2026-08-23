import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

type TextField = HTMLInputElement | HTMLTextAreaElement;

type MenuState = {
  x: number;
  y: number;
  target: TextField;
};

const TEXT_INPUT_TYPES = new Set([
  "email",
  "password",
  "search",
  "tel",
  "text",
  "url",
]);

function editableTextField(target: EventTarget | null): TextField | null {
  if (target instanceof HTMLTextAreaElement) {
    return target;
  }
  if (target instanceof HTMLInputElement && TEXT_INPUT_TYPES.has(target.type.toLowerCase())) {
    return target;
  }
  return null;
}

function selectionRange(target: TextField): { start: number; end: number } {
  const length = target.value.length;
  const start = Math.max(0, Math.min(length, target.selectionStart ?? 0));
  const end = Math.max(start, Math.min(length, target.selectionEnd ?? start));
  return { start, end };
}

function replaceSelection(target: TextField, value: string, inputType: string) {
  const { start, end } = selectionRange(target);
  target.focus();
  target.setRangeText(value, start, end, "end");
  target.dispatchEvent(new InputEvent("input", { bubbles: true, inputType, data: value }));
}

async function writeClipboard(target: TextField, value: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    // Fall through to the WebView's editing command.
  }
  target.focus();
  return document.execCommand("copy");
}

export function TextEditContextMenu() {
  const { t } = useTranslation();
  const [menu, setMenu] = useState<MenuState | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const openMenu = (event: MouseEvent) => {
      // Always suppress browser/WebView branding. Editable fields receive the
      // app-native menu below; other surfaces keep their own scoped menus.
      event.preventDefault();
      const target = editableTextField(event.target);
      setMenu(target ? { x: event.clientX, y: event.clientY, target } : null);
    };
    const closeMenu = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) {
        setMenu(null);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenu(null);
      }
    };
    const close = () => setMenu(null);

    window.addEventListener("contextmenu", openMenu);
    window.addEventListener("pointerdown", closeMenu, true);
    window.addEventListener("keydown", closeOnEscape);
    window.addEventListener("blur", close);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("contextmenu", openMenu);
      window.removeEventListener("pointerdown", closeMenu, true);
      window.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("blur", close);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, []);

  useLayoutEffect(() => {
    const element = menuRef.current;
    if (!menu || !element) {
      return;
    }
    const margin = 8;
    const rect = element.getBoundingClientRect();
    const left = Math.min(Math.max(margin, menu.x), Math.max(margin, window.innerWidth - rect.width - margin));
    const top = Math.min(Math.max(margin, menu.y), Math.max(margin, window.innerHeight - rect.height - margin));
    element.style.left = `${left}px`;
    element.style.top = `${top}px`;
  }, [menu]);

  if (!menu || !menu.target.isConnected) {
    return null;
  }

  const target = menu.target;
  const { start, end } = selectionRange(target);
  const hasSelection = end > start;
  const mutable = !target.disabled && !target.readOnly;
  const close = () => setMenu(null);

  const copy = async () => {
    target.setSelectionRange(start, end);
    await writeClipboard(target, target.value.slice(start, end));
    close();
  };
  const cut = async () => {
    target.setSelectionRange(start, end);
    if (await writeClipboard(target, target.value.slice(start, end))) {
      replaceSelection(target, "", "deleteByCut");
    }
    close();
  };
  const paste = async () => {
    try {
      if (navigator.clipboard?.readText) {
        replaceSelection(target, await navigator.clipboard.readText(), "insertFromPaste");
      } else {
        target.focus();
        document.execCommand("paste");
      }
    } catch {
      target.focus();
      document.execCommand("paste");
    } finally {
      close();
    }
  };
  const selectAll = () => {
    target.focus();
    target.setSelectionRange(0, target.value.length);
    close();
  };

  const itemClass = "flex w-full items-center justify-between gap-8 rounded-md px-3 py-2 text-left text-sm transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40";

  return createPortal(
    <div
      ref={menuRef}
      className="fixed z-[100] min-w-44 rounded-lg border border-border bg-card p-1 shadow-panel"
      style={{ left: 0, top: 0 }}
      role="menu"
      onContextMenu={(event) => {
        event.preventDefault();
        event.stopPropagation();
      }}
      onMouseDown={(event) => event.preventDefault()}
    >
      <button type="button" className={itemClass} disabled={!mutable || !hasSelection} onClick={() => void cut()}>
        <span>{t("contextMenu.cut")}</span><span className="text-xs text-muted-foreground">Ctrl+X</span>
      </button>
      <button type="button" className={itemClass} disabled={!hasSelection} onClick={() => void copy()}>
        <span>{t("contextMenu.copy")}</span><span className="text-xs text-muted-foreground">Ctrl+C</span>
      </button>
      <button type="button" className={itemClass} disabled={!mutable} onClick={() => void paste()}>
        <span>{t("contextMenu.paste")}</span><span className="text-xs text-muted-foreground">Ctrl+V</span>
      </button>
      <div className="my-1 border-t border-border" />
      <button type="button" className={itemClass} disabled={target.value.length === 0} onClick={selectAll}>
        <span>{t("contextMenu.selectAll")}</span><span className="text-xs text-muted-foreground">Ctrl+A</span>
      </button>
    </div>,
    document.body,
  );
}
