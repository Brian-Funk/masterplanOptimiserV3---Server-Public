import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PasskeyManager } from "@/components/PasskeyManager";

const apiFetch = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ apiFetch }));
vi.mock("@/lib/reauth", () => ({ withReauth: vi.fn() }));
vi.mock("@simplewebauthn/browser", () => ({ startRegistration: vi.fn() }));

function response(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

const credential = {
  id: 1,
  friendly_name: "Phone",
  created_at: "2026-08-01T10:00:00Z",
  last_used_at: null,
};

describe("PasskeyManager enrollment modes", () => {
  beforeEach(() => apiFetch.mockReset());

  it("does not show an add action when participant self-service is disabled", async () => {
    apiFetch
      .mockResolvedValueOnce(response([credential]))
      .mockResolvedValueOnce(response({
        mode: "email",
        self_service_enabled: false,
        email_available: true,
        mail_configured: true,
        can_request: false,
        message: "Additional passkey enrollment is not enabled for participant accounts.",
      }));

    render(<PasskeyManager open onClose={vi.fn()} />);

    expect(await screen.findByText("Phone")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add passkey/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /email me/i })).not.toBeInTheDocument();
  });

  it("explains that an administrator must add the participant email", async () => {
    apiFetch
      .mockResolvedValueOnce(response([credential]))
      .mockResolvedValueOnce(response({
        mode: "email",
        self_service_enabled: true,
        email_available: false,
        mail_configured: true,
        can_request: false,
        message: "Your email address has not been added by an administrator. Contact an administrator to make additional-passkey enrollment available.",
      }));

    render(<PasskeyManager open onClose={vi.fn()} />);

    expect(await screen.findByText(/email address has not been added by an administrator/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /email me/i })).not.toBeInTheDocument();
  });

  it("sends a purpose-bound enrollment email without starting WebAuthn", async () => {
    const user = userEvent.setup();
    apiFetch
      .mockResolvedValueOnce(response([credential]))
      .mockResolvedValueOnce(response({
        mode: "email",
        self_service_enabled: true,
        email_available: true,
        mail_configured: true,
        can_request: true,
        message: "A one-time enrollment link can be sent to the email address recorded by your administrator.",
      }))
      .mockResolvedValueOnce(response({ status: "accepted", purpose: "additional_passkey" }));

    render(<PasskeyManager open onClose={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: /email me an additional-passkey link/i }));

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/account/additional-passkey/email",
      { method: "POST", body: JSON.stringify({}) },
    ));
    expect(await screen.findByText(/mail server accepted your one-time enrollment email/i)).toBeInTheDocument();
  });
});
