import { invoke } from "@tauri-apps/api/core";
import type { TFunction } from "i18next";
import { appSessionAuthHeaders, hasTauriInternals, requestJson } from "./api/http";
import type { ChatAttachment } from "./chat-types";
import { MAX_ATTACHMENT_PAYLOAD_BYTES, readChatAttachment } from "./conversation-utils";

/**
 * 1.3.2 vault ingestion (frontend half).
 *
 * Binary formats stop traveling as base64 in the message: eligible files are
 * posted raw to the backend attachment vault and the durable attachment keeps
 * metadata + payloadHash only ("vault_file"). A rejected allowlisted binary
 * becomes honest metadata-only state; its bytes are never reinterpreted as
 * text or an inline image after a magic-byte rejection.
 *
 * Images also keep a verified vault hash. Small images retain their bounded
 * data URL for vision while the hash enables a later supervised Unity import.
 */

const VAULT_ARCHIVE_EXTENSIONS = [".zip", ".unitypackage"];
const VAULT_IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp"];

type VaultUploadResponse = {
  ok?: boolean;
  reason?: string;
  error?: string;
  attachment?: {
    payloadHash?: string;
    name?: string;
    size?: number;
    type?: string;
    kind?: string;
    category?: string;
    extension?: string;
  };
};

export function isVaultEligibleFile(file: File): boolean {
  const name = file.name.toLowerCase();
  if (VAULT_ARCHIVE_EXTENSIONS.some((extension) => name.endsWith(extension))) {
    return true;
  }
  return VAULT_IMAGE_EXTENSIONS.some((extension) => name.endsWith(extension));
}

export async function ingestChatAttachment(
  file: File,
  options: { endpoint: string; chatId: string },
  t: TFunction,
): Promise<ChatAttachment> {
  if (!isVaultEligibleFile(file) || !options.chatId) {
    return readChatAttachment(file, t);
  }
  let response: VaultUploadResponse;
  try {
    response = await uploadToVault(file, options);
  } catch (error) {
    return {
      id: `att-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      name: file.name,
      size: file.size,
      type: file.type || "application/octet-stream",
      payloadKind: "metadata",
      error: t("attachments.vaultUploadFailed", {
        reason: error instanceof Error ? error.message : String(error),
      }),
    };
  }
  const stored = response.attachment;
  if (response.ok && stored && typeof stored.payloadHash === "string" && /^[a-f0-9]{64}$/.test(stored.payloadHash)) {
    if (VAULT_IMAGE_EXTENSIONS.some((extension) => file.name.toLowerCase().endsWith(extension)) && file.size <= MAX_ATTACHMENT_PAYLOAD_BYTES) {
      const inline = await readChatAttachment(file, t);
      return {
        ...inline,
        vaultPayloadHash: stored.payloadHash,
        vaultKind: typeof stored.kind === "string" ? stored.kind : undefined,
      };
    }
    return {
      id: `att-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      name: file.name,
      size: file.size,
      type: file.type || stored.type || "application/octet-stream",
      payloadKind: "vault_file",
      payloadHash: stored.payloadHash,
      vaultKind: typeof stored.kind === "string" ? stored.kind : undefined,
    };
  }
  return {
    id: `att-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: file.name,
    size: file.size,
    type: file.type || "application/octet-stream",
    payloadKind: "metadata",
    error: t("attachments.vaultUploadFailed", { reason: response.reason || response.error || "unknown" }),
  };
}

async function uploadToVault(
  file: File,
  options: { endpoint: string; chatId: string },
): Promise<VaultUploadResponse> {
  if (hasTauriInternals()) {
    // Packaged mode blocks WebView fetch to the loopback backend. Keep peak
    // memory bounded by sending file slices through raw IPC instead of
    // materializing the whole (up to 512 MB) archive in JS and Rust.
    let uploadId = "";
    try {
      const begun = await invoke<{ ok: boolean; uploadId: string; chunkSize: number }>("begin_chat_attachment_upload", {
        request: {
          body: {
            name: file.name,
            chatId: options.chatId,
            declaredType: file.type || "application/octet-stream",
            size: file.size,
          },
          timeoutMs: 30000,
        },
      });
      uploadId = begun.uploadId;
      const chunkSize = Math.max(64 * 1024, Math.min(Number(begun.chunkSize) || 8 * 1024 * 1024, 8 * 1024 * 1024));
      for (let offset = 0; offset < file.size; offset += chunkSize) {
        const chunk = new Uint8Array(await file.slice(offset, Math.min(file.size, offset + chunkSize)).arrayBuffer());
        await invoke("append_chat_attachment_upload", chunk, {
          headers: {
            "x-vrcforge-upload-id": uploadId,
            "x-vrcforge-upload-offset": String(offset),
          },
        });
      }
      return await invoke<VaultUploadResponse>("finish_chat_attachment_upload", {
        request: { body: { uploadId }, timeoutMs: 300000 },
      });
    } catch (error) {
      if (uploadId) {
        try {
          await invoke("abort_chat_attachment_upload", {
            request: { body: { uploadId }, timeoutMs: 30000 },
          });
        } catch {
          // Stale staged uploads are also bounded and reaped by the backend.
        }
      }
      throw error;
    }
  }
  let uploadId = "";
  try {
    const begun = await requestJson<{ ok: boolean; uploadId: string; chunkSize: number }>(
      `${options.endpoint}/api/app/chat-attachments/uploads`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: file.name,
          chatId: options.chatId,
          declaredType: file.type || "application/octet-stream",
          size: file.size,
        }),
      },
    );
    uploadId = begun.uploadId;
    const chunkSize = Math.max(64 * 1024, Math.min(Number(begun.chunkSize) || 8 * 1024 * 1024, 8 * 1024 * 1024));
    for (let offset = 0; offset < file.size; offset += chunkSize) {
      const response = await fetch(
        `${options.endpoint}/api/app/chat-attachments/uploads/${encodeURIComponent(uploadId)}/chunks?offset=${offset}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream", ...appSessionAuthHeaders() },
          body: file.slice(offset, Math.min(file.size, offset + chunkSize)),
        },
      );
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
    }
    return await requestJson<VaultUploadResponse>(`${options.endpoint}/api/app/chat-attachments/uploads/finish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ uploadId }),
      timeoutMs: 300000,
    });
  } catch (error) {
    if (uploadId) {
      try {
        await requestJson(`${options.endpoint}/api/app/chat-attachments/uploads/abort`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ uploadId }),
        });
      } catch {
        // Stale staged uploads are also bounded and reaped by the backend.
      }
    }
    throw error;
  }
}
