export interface PairingReceipt {
  secretHash?: string;
  expiresAt: number;
  consumedAt?: number;
  materialHash?: string;
}

export type JoinDisposition = "accept" | "retry" | "invalid" | "already-used";

export function bootstrapDisposition(
  existingHash: string | undefined,
  candidateHash: string,
): "create" | "retry" | "conflict" {
  if (!existingHash) return "create";
  return existingHash === candidateHash ? "retry" : "conflict";
}

export function joinDisposition(
  pairing: PairingReceipt | undefined,
  suppliedSecretHash: string,
  suppliedMaterialHash: string,
  now: number,
): JoinDisposition {
  if (!pairing) return "invalid";
  if (pairing.consumedAt) {
    return pairing.materialHash === suppliedMaterialHash ? "retry" : "already-used";
  }
  if (!pairing.secretHash || pairing.secretHash !== suppliedSecretHash) return "invalid";
  return pairing.expiresAt >= now ? "accept" : "invalid";
}

export function pairingIsComplete(pairing: PairingReceipt | undefined): boolean {
  return !pairing || Boolean(pairing.consumedAt);
}

export function pairingIsActive(pairing: PairingReceipt | undefined, now: number): boolean {
  return Boolean(pairing && !pairing.consumedAt && pairing.expiresAt >= now);
}
