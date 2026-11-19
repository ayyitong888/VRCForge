import type { AgentQuestion } from "./api";
import type { ChatAttachment, ConversationItem } from "./chat-types";

/** Keep an answer tied to its ask_user record without creating a new user turn. */
export function questionAnswerItem(
  question: Pick<AgentQuestion, "questionId" | "header" | "question" | "questionFeedbackRevision" | "updatedAt"
    | "answeredAt" | "createdAt" | "originClientTurnId" | "originTurnId" | "attachments" | "selectedOptionId">,
  text: string,
  attachments?: ChatAttachment[],
): Extract<ConversationItem, { type: "question_answer" }> {
  const persistedAttachments = question.attachments?.map((attachment) => ({
    ...attachment,
    payloadKind: (attachment.payloadKind === "text" || attachment.payloadKind === "data_url"
      || attachment.payloadKind === "metadata" || attachment.payloadKind === "vault_file")
      ? attachment.payloadKind as ChatAttachment["payloadKind"]
      : undefined,
  }));
  const visibleAttachments = persistedAttachments?.length ? persistedAttachments : attachments;
  const revision = Number.isInteger(question.questionFeedbackRevision) ? question.questionFeedbackRevision : 0;
  const answerTime = question.answeredAt || question.updatedAt;
  return {
    id: `question-answer-${question.questionId}-${revision}`,
    type: "question_answer",
    questionId: question.questionId,
    ...(question.originClientTurnId ? { originClientTurnId: question.originClientTurnId } : {}),
    ...(question.originTurnId ? { originTurnId: question.originTurnId } : {}),
    ...(question.createdAt ? { askedAt: question.createdAt } : {}),
    ...(question.header?.trim() ? { header: question.header.trim() } : {}),
    ...(question.question?.trim() ? { question: question.question.trim() } : {}),
    text,
    revision,
    ...(question.selectedOptionId ? { selectedOptionId: question.selectedOptionId } : {}),
    ...(answerTime ? { updatedAt: answerTime } : {}),
    ...(visibleAttachments?.length ? { attachments: visibleAttachments } : {}),
  };
}
