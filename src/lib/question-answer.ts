import type { AgentQuestion } from "./api";
import type { ChatAttachment, ConversationItem } from "./chat-types";

/** Keep an answer tied to its ask_user record without creating a new user turn. */
export function questionAnswerItem(
  question: Pick<AgentQuestion, "questionId" | "header" | "question" | "questionFeedbackRevision" | "updatedAt" | "attachments">,
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
  return {
    id: `question-answer-${question.questionId}${question.updatedAt ? `-${encodeURIComponent(question.updatedAt)}` : ""}`,
    type: "question_answer",
    questionId: question.questionId,
    ...(question.header?.trim() ? { header: question.header.trim() } : {}),
    ...(question.question?.trim() ? { question: question.question.trim() } : {}),
    text,
    ...(Number.isInteger(question.questionFeedbackRevision) ? { revision: question.questionFeedbackRevision } : {}),
    ...(question.updatedAt ? { updatedAt: question.updatedAt } : {}),
    ...(visibleAttachments?.length ? { attachments: visibleAttachments } : {}),
  };
}
