import assert from "node:assert/strict";
import test from "node:test";
import {
  bootstrapDisposition, joinDisposition, pairingIsActive, pairingIsComplete,
} from "./commissioning.ts";

test("bootstrap accepts only an exact retry", () => {
  assert.equal(bootstrapDisposition(undefined, "request-a"), "create");
  assert.equal(bootstrapDisposition("request-a", "request-a"), "retry");
  assert.equal(bootstrapDisposition("request-a", "request-b"), "conflict");
});

test("join can retry the exact accepted material but rejects reuse", () => {
  const open = { secretHash: "secret", expiresAt: 2_000 };
  assert.equal(joinDisposition(open, "secret", "material", 1_000), "accept");
  assert.equal(joinDisposition(open, "wrong", "material", 1_000), "invalid");
  assert.equal(joinDisposition(open, "secret", "material", 2_001), "invalid");

  const consumed = {
    expiresAt: 2_000, consumedAt: 1_500,
    materialHash: "material",
  };
  assert.equal(joinDisposition(consumed, "secret", "material", 9_000), "retry");
  assert.equal(joinDisposition(consumed, "secret", "different", 1_600), "already-used");
  assert.equal(pairingIsComplete(consumed), true);
  assert.equal(pairingIsActive(consumed, 1_600), false);
});

test("only an unconsumed unexpired pairing is active", () => {
  assert.equal(pairingIsComplete(undefined), true);
  assert.equal(pairingIsActive(undefined, 1_000), false);
  assert.equal(pairingIsComplete({ secretHash: "s", expiresAt: 2_000 }), false);
  assert.equal(pairingIsActive({ secretHash: "s", expiresAt: 2_000 }, 1_000), true);
  assert.equal(pairingIsActive({ secretHash: "s", expiresAt: 2_000 }, 2_001), false);
});
