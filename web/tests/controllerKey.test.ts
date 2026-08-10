import { describe, expect, it } from "vitest";

import {
  generateControllerKey,
  loadControllerKey,
  newControllerEntityId,
} from "@/lib/controllerKey";

describe("controller key custody", () => {
  it("exports an unencrypted private package and verifies its public identity", async () => {
    const generated = await generateControllerKey(newControllerEntityId());

    expect(generated.privatePackage.format).toBe("mp-opt-controller-private-key-v2");
    expect(generated.privatePackage.private_key_pkcs8).toMatch(/^[A-Za-z0-9+/]+=*$/);
    expect(JSON.stringify(generated.privatePackage)).not.toMatch(/passphrase|PBKDF2|AES-GCM|ciphertext/i);
    const loaded = await loadControllerKey(generated.privatePackage);
    expect(loaded.publicPackage.key_id).toBe(generated.publicPackage.key_id);
  });

  it("rejects changed private material and a substituted public package", async () => {
    const generated = await generateControllerKey(newControllerEntityId());
    await expect(loadControllerKey({ ...generated.privatePackage, private_key_pkcs8: "AAAA" }))
      .rejects.toThrow(/invalid length/i);
    await expect(loadControllerKey({
      ...generated.privatePackage,
      public_package: { ...generated.publicPackage, public_key_sha256: "0".repeat(64) },
    })).rejects.toThrow(/does not match/i);
  });
});
