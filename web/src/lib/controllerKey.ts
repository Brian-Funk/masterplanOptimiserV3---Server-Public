const encoder = new TextEncoder();
const NAMESPACE = "mp-opt-role-trust-v1";

function asArrayBuffer(value: Uint8Array): ArrayBuffer {
  return value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength) as ArrayBuffer;
}

export type ControllerPublicPackage = {
  format: "mp-opt-controller-public-key-v1";
  instance_id: null;
  entity_id: string;
  key_id: string;
  role: "controller";
  algorithm: "Ed25519";
  public_key: string;
  public_key_sha256: string;
  supersedes_key_id: string | null;
  created_at: string;
  signature_namespace: string;
};

export type ControllerPrivatePackage = {
  format: "mp-opt-controller-private-key-v2";
  public_package: ControllerPublicPackage;
  private_key_pkcs8: string;
};

function canonicalJson(value: unknown): string {
  if (value === null || ["boolean", "number", "string"].includes(typeof value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (!value || typeof value !== "object") throw new Error("The document contains an unsupported value.");
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`;
}

function canonicalBytes(value: unknown): Uint8Array {
  const result = encoder.encode(`${canonicalJson(value)}\n`);
  if (result.length > 64 * 1024) throw new Error("The document is too large.");
  return result;
}

function bytesToBase64(value: ArrayBuffer | Uint8Array): string {
  let binary = "";
  for (const byte of new Uint8Array(value instanceof Uint8Array ? value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength) : value)) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function base64ToBytes(value: string): Uint8Array {
  let binary: string;
  try { binary = atob(value); } catch { throw new Error("The controller key contains invalid binary data."); }
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function uint32(value: number): Uint8Array { return new Uint8Array([(value >>> 24) & 255, (value >>> 16) & 255, (value >>> 8) & 255, value & 255]); }
function concat(...values: Uint8Array[]): Uint8Array {
  const output = new Uint8Array(values.reduce((total, value) => total + value.length, 0));
  let offset = 0;
  for (const value of values) { output.set(value, offset); offset += value.length; }
  return output;
}
function sshField(value: Uint8Array): Uint8Array { return concat(uint32(value.length), value); }
function openSshPublic(raw: Uint8Array): string {
  const algorithm = encoder.encode("ssh-ed25519");
  return `ssh-ed25519 ${bytesToBase64(concat(sshField(algorithm), sshField(raw)))}`;
}
async function sha256Hex(value: Uint8Array): Promise<string> {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", asArrayBuffer(value)))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
function validatePublicPackage(value: ControllerPublicPackage): void {
  if (value?.format !== "mp-opt-controller-public-key-v1" || value.role !== "controller" || value.algorithm !== "Ed25519") throw new Error("The controller public package is invalid.");
  if (!/^ctl-[a-z0-9]{8,48}$/.test(value.entity_id) || !/^ek-[0-9a-f]{16}$/.test(value.key_id) || !/^[0-9a-f]{64}$/.test(value.public_key_sha256)) throw new Error("The controller public identity is invalid.");
  if (value.signature_namespace !== NAMESPACE) throw new Error("The controller signing scope is unsupported.");
}

export function newControllerEntityId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(10));
  return `ctl-${[...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

export async function generateControllerKey(entityId: string): Promise<{ privatePackage: ControllerPrivatePackage; publicPackage: ControllerPublicPackage }> {
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]) as CryptoKeyPair;
  const rawPublic = new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey));
  const pkcs8 = new Uint8Array(await crypto.subtle.exportKey("pkcs8", pair.privateKey));
  const publicKey = openSshPublic(rawPublic);
  const fingerprint = await sha256Hex(encoder.encode(publicKey));
  const publicPackage: ControllerPublicPackage = {
    format: "mp-opt-controller-public-key-v1", instance_id: null, entity_id: entityId,
    key_id: `ek-${fingerprint.slice(0, 16)}`, role: "controller", algorithm: "Ed25519",
    public_key: publicKey, public_key_sha256: fingerprint, supersedes_key_id: null,
    created_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"), signature_namespace: NAMESPACE,
  };
  const privateKeyPkcs8 = bytesToBase64(pkcs8);
  pkcs8.fill(0);
  const privatePackage: ControllerPrivatePackage = {
    format: "mp-opt-controller-private-key-v2", public_package: publicPackage,
    private_key_pkcs8: privateKeyPkcs8,
  };
  await loadControllerKey(privatePackage);
  return { privatePackage, publicPackage };
}

export async function loadControllerKey(keyPackage: ControllerPrivatePackage): Promise<{ privateKey: CryptoKey; publicPackage: ControllerPublicPackage }> {
  if (keyPackage?.format !== "mp-opt-controller-private-key-v2") throw new Error("Select an unencrypted controller-key package generated by Masterplan.");
  validatePublicPackage(keyPackage.public_package);
  const pkcs8 = base64ToBytes(keyPackage.private_key_pkcs8);
  if (pkcs8.length < 32 || pkcs8.length > 256) throw new Error("The controller private key has an invalid length.");
  let privateKey: CryptoKey;
  try { privateKey = await crypto.subtle.importKey("pkcs8", asArrayBuffer(pkcs8), { name: "Ed25519" }, true, ["sign"]); }
  catch { throw new Error("The controller private key is invalid or was changed."); }
  finally { pkcs8.fill(0); }
  const jwk = await crypto.subtle.exportKey("jwk", privateKey);
  if (!jwk.x) throw new Error("The controller key has no public identity.");
  const publicKey = openSshPublic(base64ToBytes(jwk.x.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(jwk.x.length / 4) * 4, "=")));
  const fingerprint = await sha256Hex(encoder.encode(publicKey));
  if (publicKey !== keyPackage.public_package.public_key || fingerprint !== keyPackage.public_package.public_key_sha256) throw new Error("The private key does not match its public package.");
  return { privateKey, publicPackage: keyPackage.public_package };
}

export async function signControllerRegistration(keyPackage: ControllerPrivatePackage, document: Record<string, unknown>) {
  const { privateKey, publicPackage } = await loadControllerKey(keyPackage);
  if (document.format !== "mp-opt-controller-trust-registration-v2" || document.trust_scope !== "controller_governance_authority" || document.governance_authorisation !== "root_passkey_per_publication") throw new Error("The Server requested an unsupported controller action.");
  if (document.entity_id !== publicPackage.entity_id || document.key_id !== publicPackage.key_id || document.public_key_sha256 !== publicPackage.public_key_sha256) throw new Error("The Server action targets a different controller key.");
  const exactAction = {
    format: "mp-opt-trust-action-v1", action: document.action, instance_id: document.instance_id,
    entity_id: document.entity_id, key_id: document.key_id, role: document.role,
    algorithm: document.algorithm, public_key_sha256: document.public_key_sha256,
    trust_scope: document.trust_scope, governance_authorisation: document.governance_authorisation,
    supersedes_key_id: document.supersedes_key_id, reason: document.reason,
  };
  if (document.action_sha256 !== await sha256Hex(canonicalBytes(exactAction))) throw new Error("The Server action digest is invalid.");
  const payload = concat(encoder.encode(NAMESPACE), new Uint8Array([0]), canonicalBytes(document));
  const signature = await crypto.subtle.sign({ name: "Ed25519" }, privateKey, asArrayBuffer(payload));
  return { format: "mp-opt-ed25519-signature-v1", key_id: publicPackage.key_id, namespace: NAMESPACE, signature: bytesToBase64(signature) };
}

export function downloadJson(name: string, value: unknown): void {
  const url = URL.createObjectURL(new Blob([`${JSON.stringify(value, null, 2)}\n`], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = name; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
