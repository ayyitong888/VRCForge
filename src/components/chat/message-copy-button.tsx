import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "../../lib/utils";
import { Button, type ButtonProps } from "../ui/button";

export function MessageCopyButton({ onCopy, className, ...buttonProps }: Pick<ButtonProps, "className"> & {
  onCopy: () => Promise<boolean>;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const latestAttempt = useRef(0);

  useEffect(() => () => {
    latestAttempt.current += 1;
    window.clearTimeout(timer.current);
  }, []);

  const copyMessage = async () => {
    const attempt = ++latestAttempt.current;
    const success = (await onCopy()) === true;
    if (attempt !== latestAttempt.current) return;
    window.clearTimeout(timer.current);
    setCopied(success);
    if (success) timer.current = window.setTimeout(() => setCopied(false), 2000);
  };

  const Icon = copied ? Check : Copy;
  const label = t(copied ? "chat.copied" : "chat.copyMessage");
  return (
    <Button {...buttonProps} type="button" variant="ghost" className={cn("h-7 w-7 shrink-0 p-0", className)}
      title={label} aria-label={label} onClick={() => void copyMessage()}>
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
    </Button>
  );
}
