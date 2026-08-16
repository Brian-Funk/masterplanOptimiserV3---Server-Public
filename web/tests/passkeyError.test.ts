/** Tests for user-facing passkey API error messages. */
import { describe, expect, it } from "vitest";

import { passkeyErrorMessage } from "@/lib/passkeyError";

describe("passkeyErrorMessage", () => {
  it("reads FastAPI detail messages", () => {
    expect(
      passkeyErrorMessage({ detail: "Registration failed" }, "Fallback"),
    ).toBe("Registration failed");
  });

  it("reads safe messages from structured FastAPI details", () => {
    expect(
      passkeyErrorMessage(
        {
          detail: {
            code: "processing_consent_evidence_unavailable",
            message: "The consent record could not be sealed. No passkey was registered; try again.",
          },
        },
        "Fallback",
      ),
    ).toBe(
      "The consent record could not be sealed. No passkey was registered; try again.",
    );
  });

  it("turns SlowAPI rate-limit errors into actionable copy", () => {
    expect(
      passkeyErrorMessage(
        { error: "Rate limit exceeded: 5 per 1 minute" },
        "Fallback",
      ),
    ).toBe("Too many passkey attempts. Please wait a minute and try again.");
  });

  it("falls back for unknown response bodies", () => {
    expect(passkeyErrorMessage({ message: "Internal" }, "Fallback")).toBe(
      "Fallback",
    );
  });
});
