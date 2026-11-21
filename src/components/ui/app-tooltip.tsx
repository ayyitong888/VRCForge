import { useEffect, useId } from "react";

const tooltipAttribute = "data-app-tooltip";
const cursorPlacement = '[data-app-tooltip-placement="cursor"]';

function tooltipTarget(target: EventTarget | null) {
  return target instanceof Element ? target.closest(`[${tooltipAttribute}]`) : null;
}

export function AppTooltipLayer() {
  const tooltipId = useId();

  useEffect(() => {
    const tooltip = document.createElement("div");
    tooltip.id = tooltipId;
    tooltip.className = "app-tooltip";
    tooltip.setAttribute("role", "tooltip");
    tooltip.setAttribute("data-app-tooltip-layer", "");
    tooltip.hidden = true;
    document.body.append(tooltip);
    let active: Element | null = null;
    let cursor: Pick<PointerEvent, "clientX" | "clientY"> | undefined;

    const hide = () => {
      if (active) {
        const descriptions = (active.getAttribute("aria-describedby") || "").split(/\s+/)
          .filter(id => id && id !== tooltipId);
        if (descriptions.length) active.setAttribute("aria-describedby", descriptions.join(" "));
        else active.removeAttribute("aria-describedby");
      }
      active = null;
      cursor = undefined;
      tooltip.hidden = true;
      mutations.disconnect();
      resize.disconnect();
    };

    const refresh = () => {
      const label = active?.getAttribute(tooltipAttribute)?.trim();
      if (!active?.isConnected || !label || !active.getClientRects().length) {
        hide();
        return;
      }
      if (tooltip.textContent !== label) tooltip.textContent = label;
      tooltip.hidden = false;
      const { clientWidth, clientHeight } = document.documentElement;
      const margin = 8;
      const { offsetWidth: width, offsetHeight: height } = tooltip;
      let left: number;
      let top: number;
      if (cursor) {
        const gap = 12;
        const right = cursor.clientX + gap;
        left = right + width <= clientWidth - margin ? right : cursor.clientX - gap - width;
        top = cursor.clientY - height / 2;
      } else {
        const anchor = active.getBoundingClientRect();
        const gap = 6;
        left = anchor.left + (anchor.width - width) / 2;
        const above = anchor.top - height - gap;
        const below = anchor.bottom + gap;
        const preferBelow = active.closest('[data-app-tooltip-placement="below"]');
        const pane = preferBelow?.closest("[data-chat-history-scroll]");
        const bottomLimit = Math.min(clientHeight, pane?.getBoundingClientRect().bottom ?? clientHeight) - margin;
        top = (preferBelow && below + height <= bottomLimit) || above < margin ? below : above;
      }
      tooltip.style.left = `${Math.max(margin, Math.min(left, clientWidth - width - margin))}px`;
      tooltip.style.top = `${Math.max(margin, Math.min(top, clientHeight - height - margin))}px`;
    };

    const mutations = new MutationObserver(refresh);
    const resize = new ResizeObserver(refresh);
    const show = (target: Element, pointer?: PointerEvent) => {
      const position = pointer && target.matches(cursorPlacement)
        ? { clientX: pointer.clientX, clientY: pointer.clientY }
        : undefined;
      if (target === active) {
        cursor = position;
        refresh();
        return;
      }
      hide();
      active = target;
      cursor = position;
      // A native modal lives above the app's stacking contexts.
      (target.closest("dialog:modal") || document.body).append(tooltip);
      refresh();
      if (active !== target) return;
      const description = target.getAttribute("aria-describedby");
      target.setAttribute("aria-describedby", description ? `${description} ${tooltipId}` : tooltipId);
      mutations.observe(target, { attributes: true, attributeFilter: [tooltipAttribute] });
      mutations.observe(document.body, { subtree: true, childList: true });
      resize.observe(target);
    };
    const onPointerOver = (event: PointerEvent) => {
      const target = tooltipTarget(event.target);
      if (event.pointerType !== "touch" && target && target !== tooltipTarget(event.relatedTarget)) show(target, event);
    };
    const onPointerMove = (event: PointerEvent) => {
      if (cursor && active && event.pointerType !== "touch" && tooltipTarget(event.target) === active) {
        cursor = { clientX: event.clientX, clientY: event.clientY };
        refresh();
      }
    };
    const onPointerOut = (event: PointerEvent) => {
      if (active && tooltipTarget(event.target) === active && tooltipTarget(event.relatedTarget) !== active) {
        if (active.matches(":focus-visible")) show(active);
        else hide();
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      const target = tooltipTarget(event.target);
      if (target && event.target instanceof Element && event.target.matches(":focus-visible")) show(target);
    };
    const onFocusOut = (event: FocusEvent) => {
      if (active && tooltipTarget(event.target) === active && tooltipTarget(event.relatedTarget) !== active) hide();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") hide();
    };

    document.addEventListener("pointerover", onPointerOver, true);
    document.addEventListener("pointermove", onPointerMove, true);
    document.addEventListener("pointerout", onPointerOut, true);
    document.addEventListener("focusin", onFocusIn, true);
    document.addEventListener("focusout", onFocusOut, true);
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    window.addEventListener("blur", hide);
    return () => {
      document.removeEventListener("pointerover", onPointerOver, true);
      document.removeEventListener("pointermove", onPointerMove, true);
      document.removeEventListener("pointerout", onPointerOut, true);
      document.removeEventListener("focusin", onFocusIn, true);
      document.removeEventListener("focusout", onFocusOut, true);
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
      window.removeEventListener("blur", hide);
      hide();
      tooltip.remove();
    };
  }, [tooltipId]);

  return null;
}
