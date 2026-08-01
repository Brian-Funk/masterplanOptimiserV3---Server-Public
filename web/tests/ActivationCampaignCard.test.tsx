import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { ActivationCampaignCard } from "@/components/ActivationCampaignCard";
import {
  deriveActivationCampaignSummary,
  type ActivationCampaignUser,
} from "@/lib/activationCampaign";

function user(overrides: Partial<ActivationCampaignUser>): ActivationCampaignUser {
  return {
    id: overrides.id ?? 1,
    username: overrides.username ?? "user.one",
    display_name: overrides.display_name ?? "User One",
    is_active: overrides.is_active ?? true,
    is_activated: overrides.is_activated ?? false,
    has_activation_link: overrides.has_activation_link ?? false,
    linked_person_id: overrides.linked_person_id ?? null,
    can_edit: overrides.can_edit ?? false,
    ...overrides,
  };
}

describe("ActivationCampaignCard", () => {
  it("renders the no-user state with one primary action", async () => {
    const onPrimaryAction = vi.fn();
    const userEventClient = userEvent.setup();
    render(
      <ActivationCampaignCard
        summary={deriveActivationCampaignSummary([])}
        onPrimaryAction={onPrimaryAction}
      />,
    );

    expect(screen.getByText("No users yet")).toBeInTheDocument();
    await userEventClient.click(screen.getByRole("button", { name: "Add users" }));
    expect(onPrimaryAction).toHaveBeenCalledWith("add_users");
  });

  it("shows progress and key activation counts", () => {
    render(
      <ActivationCampaignCard
        summary={deriveActivationCampaignSummary([
          user({ id: 1, is_activated: true }),
          user({ id: 2, has_activation_link: true }),
        ])}
      />,
    );

    expect(screen.getByRole("progressbar", { name: "Activation progress" })).toHaveAttribute(
      "aria-valuenow",
      "50",
    );
    expect(screen.getByText("1 / 2 activated")).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.textContent === "Pending 1")).toBeInTheDocument();
  });

  it("lists a compact set of users needing attention", () => {
    render(
      <ActivationCampaignCard
        summary={deriveActivationCampaignSummary([
          user({ id: 1, username: "anna", display_name: "Anna", has_activation_link: false }),
          user({ id: 2, username: "ben", display_name: "Ben", has_activation_link: true }),
        ])}
      />,
    );

    expect(screen.getByText("Needs attention")).toBeInTheDocument();
    expect(screen.getByText("Anna")).toBeInTheDocument();
    expect(screen.getByText("needs link")).toBeInTheDocument();
    expect(screen.getByText("Ben")).toBeInTheDocument();
    expect(screen.getByText("Activation link is available, but the user has not activated yet.")).toBeInTheDocument();
  });

  it("calls the generate-links action for blocked campaigns", async () => {
    const onPrimaryAction = vi.fn();
    const userEventClient = userEvent.setup();
    render(
      <ActivationCampaignCard
        summary={deriveActivationCampaignSummary([
          user({ id: 1, has_activation_link: false }),
        ])}
        onPrimaryAction={onPrimaryAction}
      />,
    );

    await userEventClient.click(
      screen.getByRole("button", { name: "Generate missing links" }),
    );
    expect(onPrimaryAction).toHaveBeenCalledWith("generate_missing_links");
  });
});
