import type { ChatAttachment, ChatAttachmentPayload, ConversationItem } from "./chat-types";

export type AttachmentPayloadVault = Record<string, ChatAttachmentPayload>;

export type HistoricalAttachmentResolution = {
  attachments: ChatAttachment[];
  degraded?: "ambiguous" | "missing_or_corrupt";
};

const PAYLOAD_HASH_PATTERN = /^[a-f0-9]{64}$/;

/**
 * Use the same SHA-256 payload identity as the runtime.  The synchronous
 * implementation keeps whole-chat persistence ordered and lets a restored
 * reference reject corrupted content before it is sent back to a model.
 */
export function attachmentPayloadHash(payload: string): string {
  // AgentGateway hashes the raw `text` / `dataUrl` payload without metadata.
  const bytes = new TextEncoder().encode(normalizePythonUtf8Replacement(payload));
  const bitLength = bytes.length * 8;
  const paddedLength = ((bytes.length + 9 + 63) >> 6) << 6;
  const padded = new Uint8Array(paddedLength);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(paddedLength - 8, Math.floor(bitLength / 0x1_0000_0000));
  view.setUint32(paddedLength - 4, bitLength >>> 0);
  const state = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ];
  const constants = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];
  const words = new Uint32Array(64);
  const rotate = (value: number, bits: number) => (value >>> bits) | (value << (32 - bits));
  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let index = 0; index < 16; index += 1) {
      words[index] = view.getUint32(offset + index * 4);
    }
    for (let index = 16; index < 64; index += 1) {
      const a = words[index - 15];
      const b = words[index - 2];
      words[index] = (((rotate(a, 7) ^ rotate(a, 18) ^ (a >>> 3)) + words[index - 7] + (rotate(b, 17) ^ rotate(b, 19) ^ (b >>> 10)) + words[index - 16]) >>> 0);
    }
    let [a, b, c, d, e, f, g, h] = state;
    for (let index = 0; index < 64; index += 1) {
      const choice = (e & f) ^ (~e & g);
      const majority = (a & b) ^ (a & c) ^ (b & c);
      const first = (h + (rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25)) + choice + constants[index] + words[index]) >>> 0;
      const second = ((rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22)) + majority) >>> 0;
      [h, g, f, e, d, c, b, a] = [g, f, e, (d + first) >>> 0, c, b, a, (first + second) >>> 0];
    }
    state[0] = (state[0] + a) >>> 0;
    state[1] = (state[1] + b) >>> 0;
    state[2] = (state[2] + c) >>> 0;
    state[3] = (state[3] + d) >>> 0;
    state[4] = (state[4] + e) >>> 0;
    state[5] = (state[5] + f) >>> 0;
    state[6] = (state[6] + g) >>> 0;
    state[7] = (state[7] + h) >>> 0;
  }
  return state.map((value) => value.toString(16).padStart(8, "0")).join("");
}

// Python's encode(errors="replace") writes '?' for an unpaired UTF-16
// surrogate, while TextEncoder uses U+FFFD. Normalize that edge case so the
// browser and AgentGateway derive exactly the same content address.
function normalizePythonUtf8Replacement(value: string): string {
  let normalized = "";
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        normalized += value[index] + value[index + 1];
        index += 1;
      } else {
        normalized += "?";
      }
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      normalized += "?";
    } else {
      normalized += value[index];
    }
  }
  return normalized;
}

export function persistAttachmentReference(
  attachment: ChatAttachment,
  vault: AttachmentPayloadVault,
): ChatAttachment {
  const payload = attachmentPayload(attachment);
  if (!payload) {
    return { ...attachment, dataUrl: undefined, text: undefined };
  }
  const existingHash = attachment.payloadHash || "";
  const existingEntry = existingHash ? vault[existingHash] : undefined;
  const existingPayload = existingEntry?.payloadKind === "text" ? existingEntry.text : existingEntry?.dataUrl;
  const payloadHash = (
    PAYLOAD_HASH_PATTERN.test(existingHash)
    && existingEntry?.payloadKind === payload.payloadKind
    && existingPayload === payload.value
  )
    ? existingHash
    : attachmentPayloadHash(payload.value);
  vault[payloadHash] = {
    payloadHash,
    payloadKind: payload.payloadKind,
    ...(payload.payloadKind === "text" ? { text: payload.value } : { dataUrl: payload.value }),
  };
  return {
    ...attachment,
    payloadHash,
    payloadKind: payload.payloadKind,
    dataUrl: undefined,
    text: undefined,
  };
}

/**
 * Restore only self-consistent content-addressed payloads from an untrusted
 * transcript. Invalid entries are dropped before they can be considered for
 * a later model request.
 */
export function normalizeAttachmentPayloadVault(value: unknown): AttachmentPayloadVault | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  const normalized: AttachmentPayloadVault = {};
  for (const [key, rawEntry] of Object.entries(value)) {
    if (!PAYLOAD_HASH_PATTERN.test(key) || !rawEntry || typeof rawEntry !== "object" || Array.isArray(rawEntry)) {
      continue;
    }
    const entry = rawEntry as Partial<ChatAttachmentPayload>;
    if (entry.payloadHash !== key || (entry.payloadKind !== "text" && entry.payloadKind !== "data_url")) {
      continue;
    }
    const payload = entry.payloadKind === "text" ? entry.text : entry.dataUrl;
    if (typeof payload !== "string" || attachmentPayloadHash(payload) !== key) {
      continue;
    }
    normalized[key] = {
      payloadHash: key,
      payloadKind: entry.payloadKind,
      ...(entry.payloadKind === "text" ? { text: payload } : { dataUrl: payload }),
    };
  }
  return Object.keys(normalized).length ? normalized : undefined;
}

/** Keep only payloads still referenced by durable user messages. */
export function referencedAttachmentPayloadVault(
  items: readonly ConversationItem[],
  vault: AttachmentPayloadVault,
): AttachmentPayloadVault | undefined {
  const referenced = new Set<string>();
  for (const item of items) {
    if (item.type !== "user") {
      continue;
    }
    for (const attachment of item.attachments || []) {
      if (attachment.payloadHash) {
        referenced.add(attachment.payloadHash);
      }
    }
  }
  const selected: AttachmentPayloadVault = {};
  for (const payloadHash of referenced) {
    const entry = vault[payloadHash];
    if (entry) {
      selected[payloadHash] = entry;
    }
  }
  return Object.keys(selected).length ? selected : undefined;
}

export function resolveHistoricalAttachmentPayloads(
  items: readonly ConversationItem[],
  vault: AttachmentPayloadVault | undefined,
  prompt: string,
): HistoricalAttachmentResolution {
  const candidates = historicalPayloadAttachments(items);
  if (!candidates.length) {
    return { attachments: [] };
  }
  const normalizedPrompt = prompt.toLocaleLowerCase();
  const named = candidates.filter(({ attachment }) => {
    const name = attachment.name.trim().toLocaleLowerCase();
    return Boolean(name) && normalizedPrompt.includes(name);
  });
  if (!named.length && !looksLikeAttachmentFollowup(prompt)) {
    return { attachments: [] };
  }
  const selected = named.length ? named : candidates.length === 1 ? candidates : [];
  if (!selected.length || named.length > 1) {
    return {
      attachments: [{
        id: "history-attachments-ambiguous",
        name: "historical attachments",
        size: 0,
        type: "application/vnd.vrcforge.attachment-reference",
        payloadKind: "metadata",
        error: "Multiple historical attachments match this follow-up. Ask the user to name the file before answering from its contents.",
      }],
      degraded: "ambiguous",
    };
  }
  const attachment = selected[0].attachment;
  const restored = restoreAttachmentPayload(attachment, vault);
  if (!restored) {
    return {
      attachments: [{
        ...attachment,
        dataUrl: undefined,
        text: undefined,
        payloadKind: "metadata",
        error: "Historical attachment body is unavailable because its saved reference is missing or corrupt.",
      }],
      degraded: "missing_or_corrupt",
    };
  }
  return { attachments: [{ ...restored, id: `history-${restored.payloadHash || attachment.id}` }] };
}

/**
 * Hydrate attachment references that are being explicitly resent (retry/edit)
 * without looking at any unrelated message in the transcript.
 */
export function resolveAttachmentPayloadReferences(
  attachments: readonly ChatAttachment[],
  vault: AttachmentPayloadVault | undefined,
): ChatAttachment[] {
  return attachments.map((attachment) => {
    if (attachmentPayload(attachment) && !attachment.payloadHash) {
      return attachment;
    }
    const restored = restoreAttachmentPayload(attachment, vault);
    if (restored) {
      return restored;
    }
    if (!attachment.payloadHash) {
      return attachment;
    }
    return {
      ...attachment,
      payloadKind: "metadata",
      error: "Attachment body is unavailable because its saved reference is missing or corrupt.",
    };
  });
}

function attachmentPayload(attachment: ChatAttachment): { payloadKind: "text" | "data_url"; value: string } | null {
  if ((attachment.payloadKind === "text" || attachment.payloadKind === undefined) && typeof attachment.text === "string") {
    return { payloadKind: "text", value: attachment.text };
  }
  if ((attachment.payloadKind === "data_url" || attachment.payloadKind === undefined) && typeof attachment.dataUrl === "string") {
    return { payloadKind: "data_url", value: attachment.dataUrl };
  }
  return null;
}

function restoreAttachmentPayload(attachment: ChatAttachment, vault: AttachmentPayloadVault | undefined): ChatAttachment | null {
  const inline = attachmentPayload(attachment);
  if (inline) {
    const payloadHash = attachmentPayloadHash(inline.value);
    if (attachment.payloadHash && attachment.payloadHash !== payloadHash) {
      return null;
    }
    return { ...attachment, payloadHash };
  }
  const payloadHash = attachment.payloadHash || "";
  const stored = payloadHash ? vault?.[payloadHash] : undefined;
  if (!stored || stored.payloadHash !== payloadHash) {
    return null;
  }
  const value = stored.payloadKind === "text" ? stored.text : stored.dataUrl;
  if (typeof value !== "string" || attachmentPayloadHash(value) !== payloadHash) {
    return null;
  }
  return {
    ...attachment,
    payloadHash,
    payloadKind: stored.payloadKind,
    ...(stored.payloadKind === "text" ? { text: value } : { dataUrl: value }),
  };
}

function historicalPayloadAttachments(items: readonly ConversationItem[]): Array<{ attachment: ChatAttachment }> {
  const candidates: Array<{ attachment: ChatAttachment }> = [];
  const seen = new Set<string>();
  for (let itemIndex = items.length - 1; itemIndex >= 0; itemIndex -= 1) {
    const item = items[itemIndex];
    if (item.type !== "user") {
      continue;
    }
    for (const attachment of item.attachments || []) {
      const hasPayload = attachment.payloadKind === "text"
        || attachment.payloadKind === "data_url"
        || typeof attachment.text === "string"
        || typeof attachment.dataUrl === "string";
      const identity = attachment.payloadHash || attachment.id;
      if (hasPayload && !seen.has(identity)) {
        seen.add(identity);
        candidates.push({ attachment });
      }
    }
  }
  return candidates;
}

function looksLikeAttachmentFollowup(prompt: string): boolean {
  return /\b(?:file|attachment|document|contents?|image|picture|screenshot)\b|附件|文件|文档|图片|图像|截图/iu.test(prompt);
}
