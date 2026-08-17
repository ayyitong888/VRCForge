import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../ui/button";

export type ApprovalRevisionReasonCode =
  | "wrong_arguments"
  | "wrong_target"
  | "insufficient_context"
  | "policy_objection"
  | "other";

export function ApprovalRevisionEditor({
  disabled = false,
  onCancel,
  onSubmit,
}: {
  disabled?: boolean;
  onCancel: () => void;
  onSubmit: (reason: string, reasonCode: ApprovalRevisionReasonCode) => void;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  const [reasonCode, setReasonCode] = useState<ApprovalRevisionReasonCode>("other");

  return (
    <div className="w-full space-y-2 rounded-lg border border-border bg-background/80 p-3 text-xs" data-approval-revision-editor>
      <label className="flex flex-col gap-1">
        <span className="font-medium text-foreground">{t("approval.revisionCategoryLabel")}</span>
        <select
          className="h-8 w-full rounded-md border border-border bg-background px-2 text-xs"
          value={reasonCode}
          disabled={disabled}
          onChange={(event) => setReasonCode(event.target.value as ApprovalRevisionReasonCode)}
        >
          <option value="wrong_arguments">{t("approval.denyCode.wrong_arguments")}</option>
          <option value="wrong_target">{t("approval.denyCode.wrong_target")}</option>
          <option value="insufficient_context">{t("approval.denyCode.insufficient_context")}</option>
          <option value="policy_objection">{t("approval.denyCode.policy_objection")}</option>
          <option value="other">{t("approval.denyCode.other")}</option>
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="font-medium text-foreground">{t("approval.revisionReasonPrompt")}</span>
        <textarea
          className="min-h-20 w-full resize-y rounded-md border border-border bg-background px-2 py-1.5 text-xs"
          value={reason}
          disabled={disabled}
          onChange={(event) => setReason(event.target.value)}
          placeholder={t("approval.revisionReasonPlaceholder")}
          data-approval-revision-reason-input
        />
      </label>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" disabled={disabled} onClick={onCancel}>
          {t("common.cancel")}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={disabled || !reason.trim()}
          onClick={() => onSubmit(reason.trim(), reasonCode)}
        >
          {t("approval.modifyConfirm")}
        </Button>
      </div>
    </div>
  );
}
