import type { AgentQuestion } from "./api/types";
import type { ConversationItem } from "./chat-types";

type AgentItem = Extract<ConversationItem, { type: "agent" }>;
type UserItem = Extract<ConversationItem, { type: "user" }>;
type Task = { id: string; closedBy?: AgentItem; pendingQuestions: Set<string> };

function createdQuestions(item: AgentItem): Array<Pick<AgentQuestion, "questionId" | "status" | "blocking">> {
  return (item.response.steps || []).flatMap((step) => {
    if (!["ask_user", "call_user"].includes((step.tool || "").replace(/^vrcforge_/, ""))) return [];
    const result = step.result as { ok?: boolean; question?: { questionId?: string; id?: string; status?: string; blocking?: boolean } } | undefined;
    const questionId = result?.question?.questionId || result?.question?.id;
    return result?.ok === true && questionId ? [{ ...result.question, questionId }] : [];
  });
}

/** Logical user tasks retain separate runtime identities, timelines and clocks. */
export function projectConversationTasks(
  items: ConversationItem[],
  initialUsersByTurnId: ReadonlyMap<string, UserItem>,
  questions: AgentQuestion[] = [],
): {
  taskIdByItem: Map<ConversationItem, string>;
  startsAfter: Map<ConversationItem, AgentItem>;
} {
  const taskIdByItem = new Map<ConversationItem, string>();
  const startsAfter = new Map<ConversationItem, AgentItem>();
  const tasksByRun = new Map<string, Task>();
  const tasksByQuestion = new Map<string, Task>();
  const seenInputs = new Set<string>();
  let current: Task | undefined;
  const bindQuestion = (question: Pick<AgentQuestion, "questionId" | "status" | "blocking" | "needsUserAction">, task: Task) => {
    tasksByQuestion.set(question.questionId, task);
    const latest = questions.find((candidate) => candidate.questionId === question.questionId) || question;
    if (latest.status === "pending" && (latest.blocking !== false || latest.needsUserAction === true)) {
      task.pendingQuestions.add(question.questionId);
    } else task.pendingQuestions.delete(question.questionId);
  };

  for (const item of items) {
    const response = item.type === "agent" ? item.response : undefined;
    const cid = response?.clientTurnId || (item.type === "streaming" ? item.clientTurnId : "");
    const backendTurn = response?.turnId || response?.turn_id;
    const input = item.type === "user" ? item : cid ? initialUsersByTurnId.get(cid) : undefined;
    const freshInput = input && !seenInputs.has(input.id);
    if (input) seenInputs.add(input.id);
    const relatedQuestion = item.type === "question_answer"
      ? questions.find((question) => question.questionId === item.questionId)
      : questions.find((question) => Boolean(question.runtimeContinuation)
        && ((cid && question.runtimeContinuation?.clientTurnId === cid)
          || (backendTurn && question.runtimeContinuation?.turnId === backendTurn)));
    const questionId = item.type === "question_answer" ? item.questionId : relatedQuestion?.questionId;
    const origin = relatedQuestion?.originClientTurnId || relatedQuestion?.originTurnId;
    const relatedTask = (questionId ? tasksByQuestion.get(questionId) : undefined)
      || (origin ? tasksByRun.get(origin) : undefined);
    const knownRun = (cid ? tasksByRun.get(cid) : undefined) || (backendTurn ? tasksByRun.get(backendTurn) : undefined);
    const continuation = Boolean(relatedQuestion || item.type === "question_answer"
      || response?.continuationSource === "question_answered");
    let task = relatedTask || knownRun;
    if (!task) {
      if (!current || (freshInput && current.closedBy && !continuation)) {
        if (current?.closedBy && input) startsAfter.set(item, current.closedBy);
        current = { id: input?.id || `task:${cid || item.id}`, pendingQuestions: new Set() };
      }
      task = current;
    }
    taskIdByItem.set(item, task.id);
    if (cid) tasksByRun.set(cid, task);
    if (backendTurn) tasksByRun.set(backendTurn, task);
    if (questionId) tasksByQuestion.set(questionId, task);
    for (const question of questions) {
      if ((cid && question.originClientTurnId === cid) || (backendTurn && question.originTurnId === backendTurn)) bindQuestion(question, task);
    }
    if (continuation) task.closedBy = undefined;
    if (item.type === "question_answer") task.pendingQuestions.delete(item.questionId);
    if (item.type === "agent") {
      for (const created of createdQuestions(item)) bindQuestion(created, task);
      const nextStep = item.response.plan.nextStep;
      const localInterruption = item.response.continuationSource === "foreground_interrupted"
        || (item.response.status === "cancelled" && backendTurn === cid);
      if (!localInterruption && ((nextStep === "done" && !task.pendingQuestions.size) || nextStep === "cancelled")) task.closedBy = item;
    }
  }
  return { taskIdByItem, startsAfter };
}
