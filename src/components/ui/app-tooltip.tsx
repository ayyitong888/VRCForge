import { useEffect, useId } from "react";

const tooltipAttribute = "data-app-tooltip";

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

    const hide = () => {
      if (active) {
        const descriptions = (active.getAttribute("aria-describedby") || "").split(/\s+/)
          .filter(id => id && id !== tooltipId);
        if (descriptions.length) active.setAttribute("aria-describedby", descriptions.join(" "));
        else active.removeAttribute("aria-describedby");
      }
      active = null;
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
      const anchor = active.getBoundingClientRect();
      const { clientWidth, clientHeight } = document.documentElement;
      const margin = 8;
      const gap = 6;
      const left = anchor.left + (anchor.width - tooltip.offsetWidth) / 2;
      const above = anchor.top - tooltip.offsetHeight - gap;
      const below = anchor.bottom + gap;
      const preferBelow = active.closest('[data-app-tooltip-placement="below"]');
      const pane = preferBelow?.closest("[data-chat-history-scroll]");
      const bottomLimit = Math.min(clientHeight, pane?.getBoundingClientRect().bottom ?? clientHeight) - margin;
      const top = (preferBelow && below + tooltip.offsetHeight <= bottomLimit) || above < margin ? below : above;
      tooltip.style.left = `${Math.max(margin, Math.min(left, clientWidth - tooltip.offsetWidth - margin))}px`;
      tooltip.style.top = `${Math.max(margin, Math.min(top, clientHeight - tooltip.offsetHeight - margin))}px`;
    };

    const mutations = new MutationObserver(refresh);
    const resize = new ResizeObserver(refresh);
    const show = (target: Element) => {
      if (target === active) return;
      hide();
      active = target;
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
      if (event.pointerType !== "touch" && target && target !== tooltipTarget(event.relatedTarget)) show(target);
    };
    const onPointerOut = (event: PointerEvent) => {
      if (active && tooltipTarget(event.target) === active && tooltipTarget(event.relatedTarget) !== active
        && !active.matches(":focus-visible")) hide();
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
    document.addEventListener("pointerout", onPointerOut, true);
    document.addEventListener("focusin", onFocusIn, true);
    document.addEventListener("focusout", onFocusOut, true);
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    window.addEventListener("blur", hide);
    return () => {
      document.removeEventListener("pointerover", onPointerOver, true);
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
