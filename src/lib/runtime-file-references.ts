import type { WorkspaceDiffSummary } from "./api";
import { formatPayload } from "./conversation-utils";
import type { ConversationItem } from "./chat-types";
import { SELECTED_TEXT_ATTACHMENT_NAME } from "./chat-types";
import { asRecord } from "./runtime-parsing";
import type { RuntimeFileReference } from "./runtime-ui-types";

export function buildRuntimeFileReferences(
  items: ConversationItem[],
  diffFiles: WorkspaceDiffSummary["files"],
): RuntimeFileReference[] {
  const seen = new Map<string, RuntimeFileReference>();
  const add = (path: string, source: string) => {
    const cleaned = path.trim().replace(/[),.;:\]}]+$/g, "");
    if (!cleaned || cleaned.length > 260) {
      return;
    }
    const key = cleaned.replace(/\//g, "\\").toLowerCase();
    if (!seen.has(key)) {
      seen.set(key, { path: cleaned, source });
    }
  };
  const scan = (value: unknown, source: string) => {
    if (value === null || value === undefined) {
      return;
    }
    const text = typeof value === "string" ? value : formatPayload(value);
    const pathPattern =
      /[A-Za-z]:[\\/][^\s"'<>|]+|\b(?:Assets|Packages|ProjectSettings|src|docs|tests|packaging|artifacts)[\\/][^\s"'<>|]+|\b[\w.-]+\.(?:tsx?|py|md|json|ps1|cs|shader|asset|controller|prefab|mat|anim|unity)\b/g;
    for (const match of text.matchAll(pathPattern)) {
      add(match[0], source);
    }
  };

  for (const item of items.slice(-16)) {
    if (item.type === "user") {
      item.attachments?.forEach((attachment) => {
        if (attachment.name !== SELECTED_TEXT_ATTACHMENT_NAME) {
          add(attachment.name, "attachment");
        }
      });
    } else if (item.type === "agent") {
      const responseRecord = asRecord(item.response) || {};
      scan(responseRecord.steps, "run");
      scan(item.response.write, "write");
      scan(item.response.skill, "tool");
      scan(item.response.shell, "command");
      scan(item.response.result, "result");
    } else if (item.type === "result") {
      scan(item.result, "approval");
      scan(item.error, "approval");
    } else if (item.type === "subagent") {
      scan(item.task, "sub-agent");
    }
  }
  diffFiles.slice(0, 12).forEach((file) => add(file.path, "changes"));
  return Array.from(seen.values()).slice(-12).reverse();
}
