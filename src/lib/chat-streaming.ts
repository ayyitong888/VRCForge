export type AgentRuntimeDeltaEvent = {
  type: "agentRuntimeDelta";
  sessionId?: string;
  turnId?: string;
  clientTurnId?: string;
  textDelta?: string;
  done?: boolean;
};

export function extractReplyTextFromPartialJson(value: string): string {
  const marker = '"reply"';
  const markerIndex = value.indexOf(marker);
  if (markerIndex < 0) {
    return "";
  }
  const colonIndex = value.indexOf(":", markerIndex + marker.length);
  if (colonIndex < 0) {
    return "";
  }
  const quoteIndex = value.indexOf('"', colonIndex + 1);
  if (quoteIndex < 0) {
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
