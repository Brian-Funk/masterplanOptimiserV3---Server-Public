/** Tests for user-facing passkey API error messages. */
import { describe, expect, it } from "vitest";

import { passkeyErrorMessage } from "@/lib/passkeyError";

describe("passkeyErrorMessage", () => {
  it("reads FastAPI detail messages", () => {
    expect(
      passkeyErrorMessage({ detail: "Registration failed" }, "Fallback"),
    ).toBe("Registration failed");
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
