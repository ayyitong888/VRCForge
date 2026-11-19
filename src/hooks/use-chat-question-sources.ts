import { useEffect, useState } from "react";
import { fetchAgentQuestions, type AgentQuestion } from "../lib/api";
import type { ConversationItem } from "../lib/chat-types";
import { missingTimelineAnswerSources } from "../lib/chat-steering-projection";

export function useChatQuestionSources(items: ConversationItem[], known: AgentQuestion[], endpoint?: string,
  sessionId?: string, projectRoot?: string) {
  const missing = JSON.stringify(missingTimelineAnswerSources(items, known));
  const scope = JSON.stringify([endpoint, sessionId, projectRoot, missing]);
  const [history, setHistory] = useState<{ scope: string; questions: AgentQuestion[] }>();
  useEffect(() => {
    if (!endpoint || !sessionId || missing === "[]") return;
    let active = true;
    const ids: string[] = JSON.parse(missing);
    void (async () => {
      const questions: AgentQuestion[] = [];
      for (const questionId of ids) {
        if (!active) return;
        try {
          const result = await fetchAgentQuestions(endpoint, { questionId, sessionId, projectRoot });
          const receipt = result.questions.find(question => question.questionId === questionId);
          if (receipt) questions.push(receipt);
        } catch {
          // A missing historical receipt leaves the answer in its stored position.
        }
      }
      if (active) setHistory({ scope, questions });
    })();
    return () => { active = false; };
  }, [endpoint, sessionId, projectRoot, missing, scope]);
  return history?.scope === scope ? [...history.questions, ...known] : known;
}
