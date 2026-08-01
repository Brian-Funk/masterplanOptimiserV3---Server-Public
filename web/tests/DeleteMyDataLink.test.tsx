/**
 * Tests for DeleteMyDataLink component - GDPR data deletion request.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

// Mock environment
vi.mock("@/lib/environment", () => ({
  getApiUrl: () => "https://api.test",
}));

// Mock AuthContext
let mockIsAuthenticated = true;
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ isAuthenticated: mockIsAuthenticated }),
}));

// Mock fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

// CSRF cookie
Object.defineProperty(document, "cookie", {
  writable: true,
  value: "csrf_token=test-csrf",
});

// Mock lucide-react
vi.mock("lucide-react", () => ({
  X: (props: Record<string, unknown>) =>
    React.createElement("svg", { "data-testid": "x-icon", ...props }),
  AlertTriangle: (props: Record<string, unknown>) =>
    React.createElement("svg", { "data-testid": "alert-icon", ...props }),
}));

import { DeleteMyDataLink } from "@/components/DeleteMyDataLink";

beforeEach(() => {
  mockFetch.mockReset();
  mockFetch.mockImplementation((input: RequestInfo | URL) => {
    if (String(input).includes("/deletion-requests/current")) {
      return Promise.resolve({ ok: false, status: 404 });
    }
    return Promise.reject(new Error("Unexpected request"));
  });
  mockIsAuthenticated = true;
});

const receipt = {
  request_id: "403c7971-3b18-4ec4-b589-b1595701240d",
  state: "submitted",
  submitted_at: "2026-07-27T10:00:00Z",
  normal_response_due_at: "2026-08-26T10:00:00Z",
};

describe("DeleteMyDataLink", () => {
  it("renders delete button when authenticated", () => {
    render(<DeleteMyDataLink />);
    expect(screen.getByText("Delete my data")).toBeInTheDocument();
  });

  it("renders nothing when not authenticated", () => {
    mockIsAuthenticated = false;
    const { container } = render(<DeleteMyDataLink />);
    expect(container.textContent).toBe("");
  });

  it("opens modal on click", async () => {
    const user = userEvent.setup();
    render(<DeleteMyDataLink />);

    await user.click(screen.getByText("Delete my data"));

    expect(screen.getByText("Request Data Deletion")).toBeInTheDocument();
    expect(screen.getByText(/GDPR Article 17/)).toBeInTheDocument();
  });

  it("modal has cancel and submit buttons", async () => {
    const user = userEvent.setup();
    render(<DeleteMyDataLink />);

    await user.click(screen.getByText("Delete my data"));

    expect(screen.getByText("Cancel")).toBeInTheDocument();
    expect(screen.getByText("Submit Deletion Request")).toBeInTheDocument();
  });

  it("closes modal on cancel", async () => {
    const user = userEvent.setup();
    render(<DeleteMyDataLink />);

    await user.click(screen.getByText("Delete my data"));
    expect(screen.getByText("Request Data Deletion")).toBeInTheDocument();

    await user.click(screen.getByText("Cancel"));
    expect(screen.queryByText("Request Data Deletion")).toBeNull();
  });

  it("closes modal on close button", async () => {
    const user = userEvent.setup();
    render(<DeleteMyDataLink />);

    await user.click(screen.getByText("Delete my data"));
    expect(screen.getByText("Request Data Deletion")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /close/i }));
    expect(screen.queryByText("Request Data Deletion")).toBeNull();
  });

  it("submits deletion request successfully", async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes("/deletion-requests/current")) {
        return Promise.resolve({ ok: false, status: 404 });
      }
      return Promise.resolve({ ok: true, json: async () => receipt });
    });

    const user = userEvent.setup();
    render(<DeleteMyDataLink />);

    await user.click(screen.getByText("Delete my data"));
    await user.click(screen.getByText("Submit Deletion Request"));

    await waitFor(() => {
      expect(screen.getByTestId("deletion-request-id")).toHaveTextContent(
        receipt.request_id,
      );
      expect(screen.getByText("Current phase")).toBeInTheDocument();
    });
  });

  it("restores the current deletion receipt after a refresh", async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes("/deletion-requests/current")) {
        return Promise.resolve({ ok: true, json: async () => receipt });
      }
      return Promise.reject(new Error("Unexpected request"));
    });

    const user = userEvent.setup();
    render(<DeleteMyDataLink />);

    await user.click(await screen.findByText("Deletion request"));
    expect(screen.getByTestId("deletion-request-id")).toHaveTextContent(
      receipt.request_id,
    );
  });

  it("shows error on failed submission", async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes("/deletion-requests/current")) {
        return Promise.resolve({ ok: false, status: 404 });
      }
      return Promise.resolve({
        ok: false,
        json: async () => ({ detail: "Already requested" }),
      });
    });

    const user = userEvent.setup();
    render(<DeleteMyDataLink />);

    await user.click(screen.getByText("Delete my data"));
    await user.click(screen.getByText("Submit Deletion Request"));

    await waitFor(() => {
      expect(screen.getByText("Already requested")).toBeInTheDocument();
    });
  });

  it("shows generic error when response has no detail", async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes("/deletion-requests/current")) {
        return Promise.resolve({ ok: false, status: 404 });
      }
      return Promise.resolve({
        ok: false,
        json: async () => {
          throw new Error("no json");
        },
      });
    });

    const user = userEvent.setup();
    render(<DeleteMyDataLink />);

    await user.click(screen.getByText("Delete my data"));
    await user.click(screen.getByText("Submit Deletion Request"));

    await waitFor(() => {
      expect(
        screen.getByText("Something went wrong. Please try again."),
      ).toBeInTheDocument();
    });
  });

  it("shows network error message", async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes("/deletion-requests/current")) {
        return Promise.resolve({ ok: false, status: 404 });
      }
      return Promise.reject(new Error("Network error"));
    });

    const user = userEvent.setup();
    render(<DeleteMyDataLink />);

    await user.click(screen.getByText("Delete my data"));
    await user.click(screen.getByText("Submit Deletion Request"));

    await waitFor(() => {
      expect(
        screen.getByText("Network error. Please try again."),
      ).toBeInTheDocument();
    });
  });

  it("shows loading state during submission", async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes("/deletion-requests/current")) {
        return Promise.resolve({ ok: false, status: 404 });
      }
      // Never resolve the submission request, keeping the loading state.
      return new Promise(() => {});
    });

    const user = userEvent.setup();
    render(<DeleteMyDataLink />);

    await user.click(screen.getByText("Delete my data"));
    await user.click(screen.getByText("Submit Deletion Request"));

    await waitFor(() => {
      expect(screen.getByText("Submitting...")).toBeInTheDocument();
    });
  });

  it("displays explanation steps in the modal", async () => {
    const user = userEvent.setup();
    render(<DeleteMyDataLink />);

    await user.click(screen.getByText("Delete my data"));

    expect(
      screen.getByText(
        /account data and matching desktop person record are deleted/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/administrator is notified/)).toBeInTheDocument();
    expect(
      screen.getByText(/non-identifying accountability receipt is retained/),
    ).toBeInTheDocument();
  });
});
