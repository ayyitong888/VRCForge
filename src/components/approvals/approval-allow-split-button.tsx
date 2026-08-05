import { Check, ChevronDown, ShieldCheck } from "lucide-react";
import { useState } from "react";
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
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="relative inline-flex">
      <Button
        variant="primary"
        className={allowFutureEligible ? "rounded-r-none" : undefined}
        disabled={disabled}
        onClick={() => onApprove(approvalId)}
      >
        <Check className="h-4 w-4" />
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
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            <ChevronDown className="h-4 w-4" />
          </Button>
          {menuOpen ? <button type="button" className="fixed inset-0 z-10 cursor-default" aria-label={t("approval.allowFuture")} onClick={() => setMenuOpen(false)} /> : null}
          {menuOpen ? (
            <div className="absolute bottom-full right-0 z-20 mb-1 min-w-52 rounded-md border border-border bg-popover p-1 shadow-panel" role="menu">
              <Button
                type="button"
                variant="ghost"
                className="w-full justify-start"
                role="menuitem"
                disabled={disabled}
                onClick={() => {
                  setMenuOpen(false);
                  onApprove(approvalId, true);
                }}
              >
                <ShieldCheck className="h-4 w-4" />
                {t("approval.allowFuture")}
              </Button>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
