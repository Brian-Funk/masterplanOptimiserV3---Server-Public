import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const {
  mockPush,
  mockStartRegistration,
  mockCaptureRouteSecret,
  mockClearRouteSecret,
} = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockStartRegistration: vi.fn(),
  mockCaptureRouteSecret: vi.fn(),
  mockClearRouteSecret: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/environment", () => ({ getApiUrl: () => "https://api.test" }));
vi.mock("@/lib/routeSecret", () => ({
  captureRouteSecret: mockCaptureRouteSecret,
  clearRouteSecret: mockClearRouteSecret,
  isDefinitiveSecretRejection: () => false,
}));
vi.mock("@simplewebauthn/browser", () => ({
  startRegistration: mockStartRegistration,
}));
vi.mock("@/components/Logo", () => ({ Logo: () => <div>Logo</div> }));
vi.mock("@/components/ThemeToggle", () => ({ ThemeToggle: () => null }));

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

import ActivatePage from "@/app/activate/page";

const consent = {
  format: "mp-opt-account-processing-consent-v1",
  statement_sha256: "a".repeat(64),
  policy_version: 4,
  policy_sha256: "b".repeat(64),
  controller_identity: "Synthetic Controller",
  privacy_contact: "privacy@controller.test",
  processing_purposes: ["Coordinate operational schedules."],
  data_categories: ["Names and operational roles"],
  authenticated_audience: "Authenticated Masterplan users for the assigned event",
  privacy_url: "https://api.test/privacy",
  rights_url: "https://api.test/rights",
  event_privacy_url: "https://api.test/event-privacy",
  statement: "I have read the privacy information and consent to the described processing.",
};

function response(data: unknown): Response {
  return { ok: true, status: 200, json: async () => data } as Response;
}

describe("initial account activation", () => {
  beforeEach(() => {
    mockFetch.mockReset();
    mockPush.mockReset();
    mockStartRegistration.mockReset();
    mockCaptureRouteSecret.mockReset();
    mockClearRouteSecret.mockReset();
    mockCaptureRouteSecret.mockReturnValue("activation-token");
  });

  it("shows published facts and requires the unchecked exact consent", async () => {
    mockFetch
      .mockResolvedValueOnce(response({
        valid: true,
        username: "participant",
        display_name: "Participant",
        purpose: "initial_setup",
        processing_consent: consent,
      }))
      .mockResolvedValueOnce(response({ options: "{}", ceremony_id: "ceremony-identifier-long-enough" }))
      .mockResolvedValueOnce(response({ status: "ok" }));
    mockStartRegistration.mockResolvedValueOnce({ id: "credential" });

    render(<ActivatePage />);
    const user = userEvent.setup();
    const register = await screen.findByRole("button", { name: "Register passkey" });

    expect(register).toBeDisabled();
    expect(screen.getByText("Synthetic Controller")).toBeInTheDocument();
    expect(screen.getByText("Names and operational roles")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Privacy notice" })).toHaveAttribute(
      "href",
      consent.privacy_url,
    );

    await user.click(screen.getByRole("checkbox"));
    expect(register).toBeEnabled();
    await user.click(register);

    await screen.findByText(/account is now active/i);
    const begin = mockFetch.mock.calls[1];
    expect(begin[0]).toBe("https://api.test/api/v1/passkey/register/begin");
    expect(JSON.parse(begin[1].body)).toEqual({
      confirmed: true,
      statement_version: consent.format,
      statement_sha256: consent.statement_sha256,
      policy_version: consent.policy_version,
      policy_sha256: consent.policy_sha256,
    });
    await waitFor(() => expect(mockStartRegistration).toHaveBeenCalledOnce());
  });

  it("does not request consent again for an additional passkey", async () => {
    mockFetch.mockResolvedValueOnce(response({
      valid: true,
      username: "participant",
      display_name: "Participant",
      purpose: "additional_passkey",
      processing_consent: null,
    }));

    render(<ActivatePage />);

    expect(await screen.findByRole("button", { name: "Add passkey" })).toBeEnabled();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});
