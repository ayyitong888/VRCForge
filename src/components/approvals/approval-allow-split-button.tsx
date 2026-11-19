import { Check, ChevronDown, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../ui/button";

type ApprovalAllowSplitButtonProps = {
  approvalId: string;
  allowFutureEligible: boolean;
  disabled: boolean;
  onApprove: (approvalId: string, allowFutureCategory?: boolean) => void;
};

export function ApprovalAllowSplitButton({
  approvalId,
  allowFutureEligible,
  disabled,
  onApprove,
}: ApprovalAllowSplitButtonProps) {
  const { t } = useTranslation();
  const [menuAnchor, setMenuAnchor] = useState<{ button: HTMLButtonElement; rect: DOMRect } | null>(null);
  const closeMenu = () => setMenuAnchor(null);

  useEffect(() => {
    if (!menuAnchor) return;
    const closeOnScroll = () => {
      const rect = menuAnchor.button.getBoundingClientRect();
      if (rect.top !== menuAnchor.rect.top || rect.right !== menuAnchor.rect.right) closeMenu();
    };
    window.addEventListener("scroll", closeOnScroll, true);
    window.addEventListener("resize", closeMenu);
    return () => {
      window.removeEventListener("scroll", closeOnScroll, true);
      window.removeEventListener("resize", closeMenu);
    };
  }, [menuAnchor]);

  const rect = menuAnchor?.rect;
  const above = rect ? rect.top >= 96 : false;
  const menuPosition = rect ? {
    right: `clamp(0.5rem, ${window.innerWidth - rect.right}px, max(0.5rem, 100vw - 13.5rem))`,
    ...(above ? { bottom: window.innerHeight - rect.top + 4 } : { top: rect.bottom + 4 }),
    maxHeight: Math.max(0, (above ? rect.top : window.innerHeight - rect.bottom) - 12),
  } : undefined;

  return (
    <div className="inline-flex shrink-0">
      <Button
        variant="primary"
        className={allowFutureEligible ? "whitespace-nowrap rounded-r-none" : "whitespace-nowrap"}
        disabled={disabled}
        onClick={() => onApprove(approvalId)}
      >
        <Check className="h-4 w-4 shrink-0" />
        {t("approval.approveOnce")}
      </Button>
      {allowFutureEligible ? (
        <>
          <Button
            variant="primary"
            className="rounded-l-none border-l border-primary-foreground/30 px-2"
            disabled={disabled}
            aria-label={t("approval.allowFuture")}
            aria-haspopup="menu"
            aria-expanded={Boolean(menuPosition)}
            onClick={(event) => {
              const button = event.currentTarget;
              setMenuAnchor(menuAnchor ? null : { button, rect: button.getBoundingClientRect() });
            }}
          >
            <ChevronDown className="h-4 w-4 shrink-0" />
          </Button>
          {menuPosition ? <button type="button" className="fixed inset-0 z-10 cursor-default" aria-label={t("approval.allowFuture")} onClick={closeMenu} /> : null}
          {menuPosition ? (
            <div
              className="fixed z-20 w-52 max-w-[calc(100vw-1rem)] overflow-y-auto rounded-md border border-border bg-popover p-1 shadow-panel"
              style={menuPosition}
              role="menu"
            >
              <Button
                type="button"
                variant="ghost"
                className="h-auto min-h-10 w-full justify-start py-2 text-left"
                role="menuitem"
                disabled={disabled}
                onClick={() => {
                  closeMenu();
                  onApprove(approvalId, true);
                }}
              >
                <ShieldCheck className="h-4 w-4 shrink-0" />
                <span className="min-w-0 break-words">{t("approval.allowFuture")}</span>
              </Button>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
