import { apiFetch } from "./api";

export type HAProtectionStage =
  | "queued"
  | "capturing"
  | "transferring"
  | "verifying"
  | "accepted"
  | "attention_required";

export interface HAProtectionStatus {
  operation_id: string;
  state: "pending" | "accepted" | "indeterminate" | "failed" | "cancelled";
  stage: HAProtectionStage;
  error_code?: string | null;
}

export interface PendingSecret {
  operationId: string;
  idempotencyKey: string;
  kind: "event-create" | "event-import" | "publisher-rotation" | "public-link-create";
  resourceId?: number;
  secret: string;
  createdAt: string;
}

const PREFIX = "mp-opt-ha-secret:";

export function randomSecret(byteLength = 48): string {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  let binary = "";
  bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

export function retainPendingSecret(value: PendingSecret): void {
  sessionStorage.setItem(`${PREFIX}${value.operationId}`, JSON.stringify(value));
}

export function removePendingSecret(operationId: string): void {
  sessionStorage.removeItem(`${PREFIX}${operationId}`);
}

export function pendingSecrets(): PendingSecret[] {
  const values: PendingSecret[] = [];
  for (let index = 0; index < sessionStorage.length; index += 1) {
    const key = sessionStorage.key(index);
    if (!key?.startsWith(PREFIX)) continue;
    try {
      const value = JSON.parse(sessionStorage.getItem(key) || "null") as PendingSecret;
      if (value?.operationId && value?.secret && value?.kind) values.push(value);
    } catch {
      sessionStorage.removeItem(key);
    }
  }
  return values;
}

export async function pollProtection(
  operationId: string,
  onStatus?: (status: HAProtectionStatus) => void,
): Promise<HAProtectionStatus> {
  for (;;) {
    const response = await apiFetch(`/api/v1/admin/ha-protection-operations/${operationId}`);
    if (!response.ok) throw new Error(`Protection status is unavailable (${response.status}).`);
    const status = (await response.json()) as HAProtectionStatus;
    onStatus?.(status);
    if (status.state === "accepted" || status.state === "failed" || status.state === "cancelled") {
      return status;
    }
    if (status.state === "indeterminate" || status.stage === "attention_required") return status;
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
}

export function protectionStageLabel(stage: HAProtectionStage | string | null | undefined): string {
  switch (stage) {
    case "queued": return "Queued for standby";
    case "capturing": return "Capturing protected state";
    case "transferring": return "Transferring to standby";
    case "verifying": return "Standby is verifying";
    case "accepted": return "Accepted by standby";
    case "attention_required": return "Attention required";
    default: return "Securing on standby";
  }
}
