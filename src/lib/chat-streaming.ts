export type AgentRuntimeDeltaEvent = {
  type: "agentRuntimeDelta";
  sessionId?: string;
  turnId?: string;
  clientTurnId?: string;
  textDelta?: string;
  done?: boolean;
};

const STREAMING_DIALOGUE_FIELDS = ["reply", "summary"] as const;

export function extractReplyTextFromPartialJson(value: string): string {
  return extractDialogueTextFromPartialJson(value).text;
}

export function extractDialogueTextFromPartialJson(value: string): { field: string; text: string } {
  for (const field of STREAMING_DIALOGUE_FIELDS) {
    const text = extractJsonStringField(value, field);
    if (text) {
      return { field, text };
    }
  }
  return { field: "", text: "" };
}

function extractJsonStringField(value: string, field: string): string {
  const marker = `"${field}"`;
  let searchFrom = 0;
  let colonIndex = -1;
  while (true) {
    const markerIndex = value.indexOf(marker, searchFrom);
    if (markerIndex < 0) {
      return "";
    }
    let cursor = markerIndex + marker.length;
    while (cursor < value.length && /\s/.test(value[cursor])) {
      cursor += 1;
    }
    if (value[cursor] === ":") {
      colonIndex = cursor;
      break;
    }
    searchFrom = markerIndex + marker.length;
  }
  let quoteIndex = colonIndex + 1;
  while (quoteIndex < value.length && /\s/.test(value[quoteIndex])) {
    quoteIndex += 1;
  }
  if (value[quoteIndex] !== '"') {
    return "";
  }

  let output = "";
  let escaped = false;
  for (let index = quoteIndex + 1; index < value.length; index += 1) {
    const char = value[index];
    if (escaped) {
      output += decodeJsonStringEscape(char);
      escaped = false;
      continue;
    }
    if (char === "\\") {
      escaped = true;
      continue;
    }
    if (char === '"') {
      break;
    }
    output += char;
  }
  return output;
}

function decodeJsonStringEscape(char: string): string {
  switch (char) {
    case "n":
      return "\n";
    case "r":
      return "\r";
    case "t":
      return "\t";
    case '"':
      return '"';
    case "\\":
      return "\\";
    case "/":
      return "/";
    default:
      return char;
  }
}
