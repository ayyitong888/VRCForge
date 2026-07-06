import type { Dispatch, SetStateAction } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ActiveView } from "../lib/app-view";
import type { AgentApproval, AgentRuntimeResponse } from "../lib/api";
import {
  approveAgentApproval,
  rejectAgentApproval,
  requestApprovalRevision,
} from "../lib/api";
import type { ApprovalActionState, ChatAttachment, ConversationItem } from "../lib/chat-types";
import { textContextAttachment } from "../lib/conversation-utils";
import { approvalIdFromResponse, asRecord, isAgentShellResult } from "../lib/runtime-parsing";

type UseApprovalExecutionParams = {
  endpoint: string;
  activeRuntimeProjectPath: string;
  activeChatId: string;
  activeView: ActiveView;
  pendingApprovalItems: AgentApproval[];
  maxAttachmentsPerTurn: number;
  setInput: Dispatch<SetStateAction<string>>;
  setAttachments: Dispatch<SetStateAction<ChatAttachment[]>>;
  setRuntimeNotice: (message: string) => void;
  setError: (message: string) => void;
  formatPayload: (value: unknown) => string;
  appendToChat: (chatId: string, item: ConversationItem) => void;
  refresh: (target?: string) => Promise<void>;
  refreshRuntimeRuns: (includeEvents?: boolean, target?: string) => Promise<void>;
  loadCheckpoints: () => Promise<void>;
};

export function useApprovalExecution({
  endpoint,
  activeRuntimeProjectPath,
  activeChatId,
  activeView,
  pendingApprovalItems,
  maxAttachmentsPerTurn,
  setInput,
  setAttachments,
  setRuntimeNotice,
  setError,
  formatPayload,
  appendToChat,
  refresh,
  refreshRuntimeRuns,
  loadCheckpoints,
}: UseApprovalExecutionParams) {
  const { t } = useTranslation();
  const [approvalActions, setApprovalActions] = useState<Record<string, ApprovalActionState>>({});

  function pendingApprovalForResponse(response: AgentRuntimeResponse): AgentApproval | null {
    const approvalId = approvalIdFromResponse(response);
    if (approvalId) {
      const pending = pendingApprovalItems.find((approval) => approval.id === approvalId);
      if (pending) {
        return pending;
      }
    }
    const shellApproval = response.shell?.approval;
    if (shellApproval?.status === "pending") {
      return shellApproval;
    }
    return null;
  }

  async function modifyApprovalInComposer(approval: AgentApproval) {
    const target = approval.targetTool || approval.preview?.command || t("approval.thisApproval");
    const detail = approval.paramsSummary || approval.arguments || approval.preview || {};
    const approvalContext = [
      `${t("approval.contextPending")}: ${approval.id}`,
      `${t("approval.contextTarget")}: ${target}`,
      approval.reason ? `${t("approval.contextReason")}: ${approval.reason}` : "",
      `${t("approval.contextDetails")}:\n${formatPayload(detail)}`,
    ]
      .filter(Boolean)
      .join("\n\n");
    setInput((current) => {
      const prefix = current.trim() ? `${current.trimEnd()}\n\n` : "";
      return `${prefix}${t("approval.modifyPrompt", { id: approval.id, target })}\n`;
    });
    setAttachments((current) => [
      ...current,
      textContextAttachment(t("approval.pendingContextTitle"), approvalContext),
    ].slice(0, maxAttachmentsPerTurn));
    setRuntimeNotice(t("approval.modifyNotice"));
    setApprovalActions((current) => ({ ...current, [approval.id]: "modify" }));
    setError("");
    const approvalScope = { expectedProjectRoot: activeRuntimeProjectPath || undefined, globalOnly: !activeRuntimeProjectPath };
    try {
      await requestApprovalRevision(endpoint, approval.id, {
        reason: t("approval.revisionReason"),
        note: t("approval.revisionNote", { id: approval.id, target }),
        ...approvalScope,
      });
      await refresh();
      await refreshRuntimeRuns(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      clearApprovalAction(approval.id);
    }
  }

  async function approveShell(approvalId: string) {
    setApprovalActions((current) => ({ ...current, [approvalId]: "approve" }));
    setError("");
    const approvalScope = { expectedProjectRoot: activeRuntimeProjectPath || undefined, globalOnly: !activeRuntimeProjectPath };
    try {
      const payload = await approveAgentApproval(endpoint, approvalId, approvalScope);
      const executionResult = payload.execution?.result;
      const shellResult = isAgentShellResult(executionResult) ? executionResult : undefined;
      if (activeChatId && (shellResult || payload.execution?.error)) {
        appendToChat(activeChatId, {
          id: `result-${approvalId}-${Date.now()}`,
          type: "result",
          approvalId,
          result: shellResult,
          error: payload.execution?.error,
        });
      }
      await refresh();
      await refreshRuntimeRuns(false);
      const executionRecord = asRecord(payload.execution);
      const executionTargetTool = typeof executionRecord?.targetTool === "string" ? executionRecord.targetTool : "";
      const executionResultRecord = asRecord(executionResult);
      const resolvedRecoveries = executionResultRecord?.resolvedApplyRecoveries;
      const shouldRefreshCheckpoints =
        activeView === "checkpoints" ||
        executionTargetTool === "vrcforge_restore_checkpoint" ||
        executionTargetTool === "vrcforge_resolve_interrupted_apply_recovery" ||
        Array.isArray(resolvedRecoveries);
      if (shouldRefreshCheckpoints) {
        await loadCheckpoints();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      clearApprovalAction(approvalId);
    }
  }

  async function rejectShell(approvalId: string) {
    setApprovalActions((current) => ({ ...current, [approvalId]: "reject" }));
    setError("");
    const approvalScope = { expectedProjectRoot: activeRuntimeProjectPath || undefined, globalOnly: !activeRuntimeProjectPath };
    try {
      await rejectAgentApproval(endpoint, approvalId, approvalScope);
      if (activeChatId) {
        appendToChat(activeChatId, {
          id: `result-${approvalId}-${Date.now()}`,
          type: "result",
          approvalId,
          error: "rejected",
        });
      }
      await refresh();
      await refreshRuntimeRuns(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      clearApprovalAction(approvalId);
    }
  }

  function clearApprovalAction(approvalId: string) {
    setApprovalActions((current) => {
      const next = { ...current };
      delete next[approvalId];
      return next;
    });
  }

  return {
    approvalActions,
    pendingApprovalForResponse,
    modifyApprovalInComposer,
    approveShell,
    rejectShell,
  };
}
