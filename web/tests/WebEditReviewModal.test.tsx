import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WebEditReviewModal } from "@/components/WebEditReviewModal";
import { apiFetch } from "@/lib/api";
import type { WebEditSummary } from "@/lib/webEditConfidence";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

const apiFetchMock = vi.mocked(apiFetch);

const summary: WebEditSummary = {
  level: "review",
  edited_task_count: 2,
  last_edited_at: "2026-05-21T14:20:00",
  last_edited_by: "Anna",
  has_published_baseline: true,
  headline: "Review needed",
  description: "2 web edits since the last desktop publish.",
  items: [
    {
      task_id: 1,
      task_name: "Opening Briefing",
      day: "2026-05-21",
      start: "2026-05-21T09:30:00",
      end: "2026-05-21T10:30:00",
      location: "Room B",
      edited_at: "2026-05-21T14:20:00",
      edited_by: "Anna",
      edited_by_user_id: 7,
      change_summary: ["Time changed", "Location changed"],
      original_summary: "09:00 - 10:00 · Room A · Anna",
      current_summary: "09:30 - 10:30 · Room B · Anna",
    },
    {
      task_id: 2,
      task_name: "Jury Meeting",
      day: "2026-05-22",
      start: "2026-05-22T11:00:00",
      end: "2026-05-22T12:00:00",
      location: "Room C",
      edited_at: "2026-05-21T15:00:00",
      edited_by: "Ben",
      edited_by_user_id: 8,
      change_summary: ["Location changed"],
      original_summary: "11:00 - 12:00 · Room B · Ben",
      current_summary: "11:00 - 12:00 · Room C · Ben",
    },
  ],
};

function renderModal(options: { canRevert?: boolean; onRefresh?: () => void } = {}) {
  return render(
    <WebEditReviewModal
      open
      eventId={3}
      summary={summary}
      canRevert={options.canRevert ?? true}
      onClose={vi.fn()}
      onRefresh={options.onRefresh ?? vi.fn()}
    />,
  );
}

describe("WebEditReviewModal", () => {
  beforeEach(() => {
    apiFetchMock.mockReset();
  });

  it("shows grouped committed web edits and comparison details", async () => {
    const user = userEvent.setup();
    renderModal();

    expect(screen.getByText("21.05.2026")).toBeInTheDocument();
    expect(screen.getByText("22.05.2026")).toBeInTheDocument();
    expect(screen.getByText("Opening Briefing")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "Review" })[0]);

    expect(screen.getByText("Original published")).toBeInTheDocument();
    expect(screen.getByText("09:00 - 10:00 · Room A · Anna")).toBeInTheDocument();
    expect(screen.getByText("Current live")).toBeInTheDocument();
    expect(screen.getAllByText("09:30 - 10:30 · Room B · Anna")).toHaveLength(2);
  });

  it("confirms and reverts a single web edit", async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn();
    apiFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        success: true,
        message: "Opening Briefing reverted to the published version.",
      }),
    } as Response);
    renderModal({ onRefresh });

    await user.click(screen.getAllByRole("button", { name: "Revert" })[0]);
    await user.click(screen.getByRole("button", { name: "Revert to published version" }));

    await waitFor(() => expect(onRefresh).toHaveBeenCalledOnce());
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/api/v1/admin/events/3/web-edits/1/revert",
      { method: "POST", body: JSON.stringify({}) },
    );
  });

  it("sends selected task ids for bulk revert", async () => {
    const user = userEvent.setup();
    apiFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, message: "1 web edit reverted." }),
    } as Response);
    renderModal();

    await user.click(screen.getByRole("checkbox", { name: "Select Opening Briefing" }));
    await user.click(screen.getByRole("button", { name: "Revert selected" }));
    await user.click(screen.getByRole("button", { name: "Revert web edits" }));

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalled());
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/api/v1/admin/events/3/web-edits/revert",
      {
        method: "POST",
        body: JSON.stringify({ task_ids: [1], revert_all: false }),
      },
    );
  });

  it("hides revert controls when the user cannot revert web edits", () => {
    renderModal({ canRevert: false });

    expect(screen.queryByRole("button", { name: "Revert" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Revert all" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: /Select/ })).toBeNull();
  });
});
