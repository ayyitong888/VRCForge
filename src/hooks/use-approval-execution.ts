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
import type { ApprovalActionState, ConversationItem } from "../lib/chat-types";
import { approvalIdFromResponse, asRecord, isAgentShellResult } from "../lib/runtime-parsing";

type UseApprovalExecutionParams = {
  endpoint: string;
  activeRuntimeProjectPath: string;
  activeChatId: string;
  activeView: ActiveView;
  pendingApprovalItems: AgentApproval[];
  setRuntimeNotice: (message: string) => void;
  setError: (message: string) => void;
  appendToChat: (chatId: string, item: ConversationItem) => void;
  chatIdForSessionId: (sessionId: string) => string;
  refresh: (target?: string) => Promise<void>;
  refreshRuntimeRuns: (includeEvents?: boolean, target?: string) => Promise<void>;
  loadCheckpoints: () => Promise<void>;
  reloadChatStorageState: () => Promise<boolean>;
};

export function useApprovalExecution({
  endpoint,
  activeRuntimeProjectPath,
  activeChatId,
  activeView,
  pendingApprovalItems,
  setRuntimeNotice,
  setError,
  appendToChat,
  chatIdForSessionId,
  refresh,
  refreshRuntimeRuns,
  loadCheckpoints,
  reloadChatStorageState,
}: UseApprovalExecutionParams) {
  const { t } = useTranslation();
  const [approvalActions, setApprovalActions] = useState<Record<string, ApprovalActionState>>({});

  function appendContinuation(response: AgentRuntimeResponse | undefined): boolean {
    if (!response) {
      return false;
    }
    const ownerSessionId = response.sessionId || response.session_id || "";
    const ownerChatId = chatIdForSessionId(ownerSessionId);
    if (!ownerChatId) {
      return false;
    }
    appendToChat(ownerChatId, {
      id: `approval-continuation-${response.turnId || response.turn_id || Date.now()}`,
      type: "agent",
      response,
      elapsedSeconds: 0,
      createdAt: new Date().toISOString(),
    });
    return true;
  }

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

  async function modifyApprovalInComposer(approval: AgentApproval, revisionReasonText = "", denyReasonCode = "") {
    // Goal-linked approvals have durable terminal semantics. They cannot be
    // converted into an interactive revision turn from the chat UI.
    if (approval.goalDeliveryId?.trim()) {
      return;
    }
    const target = approval.targetTool || t("approval.thisApproval");
    const reason = revisionReasonText.trim() || t("approval.revisionReason");
    const note = t("approval.revisionNote", { id: approval.id, target });
    setApprovalActions((current) => ({ ...current, [approval.id]: "modify" }));
    setError("");
    const approvalScope = scopeForApproval(approval.id);
    try {
      const payload = await requestApprovalRevision(endpoint, approval.id, {
        denyReasonCode,
        reason,
        note,
        ...approvalScope,
      });
      if (!payload.ok) {
        throw new Error(payload.message || t("approval.notificationFailed"));
      }
      const revisedApproval = payload.approval || approval;
      const safeTarget = revisedApproval.targetTool || approval.targetTool || "";
      const requestedAt = revisedApproval.revisionRequestedAt || new Date().toISOString();
      const ownerChatId = chatIdForSessionId(revisedApproval.taskContext?.sessionId || "");
      const revisionChatId = ownerChatId || activeChatId;
      if (revisionChatId) {
        appendToChat(revisionChatId, {
          id: `approval-revision-${approval.id}-${Date.now()}`,
          type: "approval_revision",
          approvalId: approval.id,
          targetTool: safeTarget,
          requestedAt,
          denyReasonCode,
          reason,
          note,
          status: "retrying",
          createdAt: requestedAt,
        });
      }
      if (payload.continuationError) {
        setRuntimeNotice(payload.continuationError);
      } else {
        setRuntimeNotice(t("approval.modifyNotice"));
      }
      appendContinuation(payload.continuation);
      await refresh();
      await refreshRuntimeRuns(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      clearApprovalAction(approval.id);
    }
  }

  async function approveShell(approvalId: string, allowFutureCategory = false) {
    setApprovalActions((current) => ({ ...current, [approvalId]: "approve" }));
    setError("");
    const approvalScope = scopeForApproval(approvalId, allowFutureCategory);
    const pendingApproval = pendingApprovalItems.find((approval) => approval.id === approvalId);
    const pendingTargetTool = pendingApproval?.targetTool || "";
    const taskSessionId = pendingApproval?.taskContext?.sessionId || "";
    const ownerChatId = chatIdForSessionId(taskSessionId);
    const resultChatId = taskSessionId ? ownerChatId : activeChatId;
    try {
      const payload = await approveAgentApproval(endpoint, approvalId, approvalScope);
      if (payload.continuationError) {
        setRuntimeNotice(payload.continuationError);
      }
      const executionResult = payload.execution?.result;
      const shellResult = isAgentShellResult(executionResult) ? executionResult : undefined;
      const executionRecord = asRecord(payload.execution);
      const completionNotice =
        payload.execution?.status === "needs_user_action"
        && typeof payload.execution.outcome?.summary === "string"
          ? payload.execution.outcome.summary
          : "";
      if (resultChatId && (shellResult || payload.execution?.error || completionNotice)) {
        appendToChat(resultChatId, {
          id: `result-${approvalId}-${Date.now()}`,
          type: "result",
          approvalId,
          result: shellResult,
          error: payload.execution?.error || completionNotice,
        });
      }
      appendContinuation(payload.continuation);
      await refresh();
      await refreshRuntimeRuns(false);
      const executionApproval = asRecord(executionRecord?.approval);
      const executionTargetTool =
        (typeof executionRecord?.targetTool === "string" ? executionRecord.targetTool : "")
        || (typeof executionApproval?.targetTool === "string" ? executionApproval.targetTool : "")
        || payload.approval?.targetTool
        || pendingTargetTool;
      const executionResultRecord = asRecord(executionResult);
      if (executionTargetTool === "vrcforge_repair_project_chat_store" && executionRecord?.status === "applied") {
        await reloadChatStorageState();
      }
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
    const approvalScope = scopeForApproval(approvalId);
    try {
      const payload = await rejectAgentApproval(endpoint, approvalId, approvalScope);
      if (!payload.ok) {
        throw new Error(payload.message || `Approval ${approvalId} could not be rejected.`);
      }
      if (payload.continuationError) {
        setRuntimeNotice(payload.continuationError);
      }
      const approval = pendingApprovalItems.find((item) => item.id === approvalId);
      const taskSessionId = approval?.taskContext?.sessionId || "";
      const ownerChatId = chatIdForSessionId(taskSessionId);
      const resultChatId = taskSessionId ? ownerChatId : activeChatId;
      if (resultChatId) {
        appendToChat(resultChatId, {
          id: `result-${approvalId}-${Date.now()}`,
          type: "result",
          approvalId,
          error: "rejected",
        });
      }
      appendContinuation(payload.continuation);
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

  function scopeForApproval(approvalId: string, allowFutureCategory = false) {
    const approval = pendingApprovalItems.find((item) => item.id === approvalId);
    const projectRoot = approval
      ? approval.projectRoot?.trim() || ""
      : activeRuntimeProjectPath.trim();
    return {
      expectedProjectRoot: projectRoot || undefined,
      globalOnly: !projectRoot,
      ...(allowFutureCategory ? { allowFutureCategory: true } : {}),
    };
  }

  return {
    approvalActions,
    pendingApprovalForResponse,
    modifyApprovalInComposer,
    approveShell,
    rejectShell,
  };
}
