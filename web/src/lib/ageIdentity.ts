const CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l";

function polymod(values: number[]): number {
  const generators = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
  let checksum = 1;
  for (const value of values) {
    const top = checksum >>> 25;
    checksum = ((checksum & 0x1ffffff) << 5) ^ value;
    for (let index = 0; index < 5; index += 1) {
      if ((top >>> index) & 1) checksum ^= generators[index];
    }
  }
  return checksum;
}

function hrpExpand(hrp: string): number[] {
  return [
    ...[...hrp].map((character) => character.charCodeAt(0) >>> 5),
    0,
    ...[...hrp].map((character) => character.charCodeAt(0) & 31),
  ];
}

function convertBits(input: Uint8Array, from: number, to: number): number[] {
  let accumulator = 0;
  let bits = 0;
  const output: number[] = [];
  const mask = (1 << to) - 1;
  for (const value of input) {
    accumulator = (accumulator << from) | value;
    bits += from;
    while (bits >= to) {
      bits -= to;
      output.push((accumulator >>> bits) & mask);
    }
  }
  if (bits > 0) output.push((accumulator << (to - bits)) & mask);
  return output;
}

function bech32(hrp: string, bytes: Uint8Array): string {
  const data = convertBits(bytes, 8, 5);
  const values = [...hrpExpand(hrp), ...data, 0, 0, 0, 0, 0, 0];
  const checksum = polymod(values) ^ 1;
  const suffix = Array.from({ length: 6 }, (_, index) =>
    (checksum >>> (5 * (5 - index))) & 31
  );
  return `${hrp}1${[...data, ...suffix].map((value) => CHARSET[value]).join("")}`;
}

export interface AgeRecoveryIdentity {
  recipient: string;
  identity: string;
}

export async function generateAgeRecoveryIdentity(): Promise<AgeRecoveryIdentity> {
  const pair = await crypto.subtle.generateKey(
    { name: "X25519" } as Algorithm,
    true,
    ["deriveBits"],
  ) as CryptoKeyPair;
  const publicBytes = new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey));
  const privatePkcs8 = new Uint8Array(await crypto.subtle.exportKey("pkcs8", pair.privateKey));
  if (publicBytes.length !== 32 || privatePkcs8.length < 32) {
    throw new Error("The browser returned an unsupported X25519 key format.");
  }
  const privateBytes = privatePkcs8.slice(-32);
  return {
    recipient: bech32("age", publicBytes),
    identity: bech32("AGE-SECRET-KEY-".toLowerCase(), privateBytes).toUpperCase(),
  };
}
