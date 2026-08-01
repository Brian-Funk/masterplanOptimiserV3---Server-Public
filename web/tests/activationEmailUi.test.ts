/** Static and derivation checks for activation email administration UI. */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { deriveActivationCampaignSummary } from "@/lib/activationCampaign";

const serverRoot = path.resolve(__dirname, "..", "..");

function adminPageSource(): string {
  return readFileSync(
    path.join(serverRoot, "web", "src", "app", "admin", "page.tsx"),
    "utf8",
  );
}

function activationPageSource(relativePath: string): string {
  return readFileSync(
    path.join(serverRoot, "web", "src", "app", "activate", relativePath),
    "utf8",
  );
}

describe("activation email administration", () => {
  it("derives calm campaign counts for email readiness and failures", () => {
    const summary = deriveActivationCampaignSummary([
      {
        id: 1,
        username: "ready",
        display_name: "Ready Person",
        is_activated: false,
        has_valid_email: true,
        activation_email_status: null,
      },
      {
        id: 2,
        username: "missing",
        display_name: "Missing Person",
        is_activated: false,
        has_valid_email: false,
        activation_email_status: "failed",
      },
    ]);

    expect(summary.usersReadyToEmail).toBe(1);
    expect(summary.usersWithoutEmail).toBe(1);
    expect(summary.emailFailures).toBe(1);
  });

  it("provides both per-user and explicitly selected batch delivery", () => {
    const source = adminPageSource();

    expect(source).toContain("/activation-email`");
    expect(source).toContain('"/api/v1/admin/batch-activation-emails"');
    expect(source).toContain("Email link and QR");
    expect(source).toContain("Retry unsuccessful");
    expect(source).toContain("Batch actions are always explicit");
    expect(source).toContain("Accepted by mail server");
    expect(source).toContain("batchActionError");
    expect(source).toContain("emailActionErrors[user.id]");
  });

  it("reports excluded manual-link recipients without hiding successful links", () => {
    const source = adminPageSource();

    expect(source).toContain("batchLinkSkipped");
    expect(source).toContain("This account is deactivated");
    expect(source).toContain("No links were created for the selected users");
  });

  it("downloads canonical QR artwork from the protected backend renderer", () => {
    const source = adminPageSource();

    expect(source).toContain('"/api/v1/admin/activation-qr-codes"');
    expect(source).toContain("token: activationTokenFromUrl(link.activation_url)");
    expect(source).toContain("await response.blob()");
    expect(source).toContain('a.download = "activation-qr-codes.zip"');
    expect(source).not.toContain("QRCodeCanvas");
    expect(source).not.toContain("new JSZip");
  });

  it("shows safe mail readiness and link invalidation controls", () => {
    const source = adminPageSource();

    expect(source).toContain("SMTP credentials are deployment-only");
    expect(source).toContain("/api/v1/admin/settings/email/test");
    expect(source).toContain("/api/v1/admin/activation-links/invalidate-all");
    expect(source).toContain("Changes apply only to links generated afterwards");
  });

  it("consolidates active account access into one responsive passkey action", () => {
    const source = adminPageSource();

    expect(source).toContain("<Key size={14} /> Passkeys");
    expect(source).toContain("Add another passkey");
    expect(source).toContain("Reset passkeys");
    expect(source).toContain('setManagedPasskeyPurpose("additional_passkey")');
    expect(source).toContain('setManagedPasskeyPurpose("credential_reset")');
    expect(source).toContain("Existing passkeys and signed-in sessions remain valid");
    expect(source).toContain("Generate link / QR");
    expect(source).toContain("Email link and QR");
  });

  it("keeps public link and QR guidance purpose-aware", () => {
    const page = activationPageSource("page.tsx");
    const qr = activationPageSource(path.join("qr", "page.tsx"));

    expect(page).toContain("Your existing passkeys and signed-in sessions will remain valid");
    expect(page).toContain("Previous passkeys and sessions have been revoked");
    expect(qr).toContain('purpose === "additional_passkey"');
    expect(qr).toContain('purpose === "credential_reset"');
    expect(qr).toContain("Existing access remains valid");
  });
});
