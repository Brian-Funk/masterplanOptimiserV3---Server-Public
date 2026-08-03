import { describe, expect, it } from "vitest";

import { responseMessage } from "@/app/admin/page";

describe("admin validation messages", () => {
  it("renders a field-specific Pydantic validation error", () => {
    expect(responseMessage({
      detail: [{
        type: "value_error",
        loc: ["body", "email"],
        msg: "value is not a valid email address",
      }],
    }, "fallback")).toBe("Email: value is not a valid email address");
  });

  it("retains the safe fallback for an unknown response shape", () => {
    expect(responseMessage({ detail: [] }, "The user could not be created.")).toBe(
      "The user could not be created.",
    );
  });
});
