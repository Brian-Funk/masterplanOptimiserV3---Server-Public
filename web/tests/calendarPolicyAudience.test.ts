import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("calendar permitted-data acknowledgement audience", () => {
  it("limits the prompt to management roles and accounts that can edit", () => {
    const source = readFileSync(
      path.resolve(__dirname, "..", "src", "app", "calendar", "page.tsx"),
      "utf8",
    );
    const capability = "user.can_edit || user.is_admin || user.is_root_admin || user.is_issuer";

    expect(source.match(new RegExp(capability.replaceAll("|", "\\|"), "g"))?.length).toBe(2);
    expect(source).not.toContain("{user && !data?.data_policy_acknowledged");
  });
});
