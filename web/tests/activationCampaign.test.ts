import { describe, expect, it } from "vitest";

import {
  deriveActivationCampaignSummary,
  formatActivationTimestamp,
  matchesActivationFilter,
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

describe("activation campaign confidence helpers", () => {
  it("summarises the no-user state", () => {
    const summary = deriveActivationCampaignSummary([]);

    expect(summary.level).toBe("unknown");
    expect(summary.headline).toBe("No users yet");
    expect(summary.primaryAction?.target).toBe("add_users");
  });

  it("marks missing activation links as action needed", () => {
    const summary = deriveActivationCampaignSummary([
      user({ id: 1, username: "anna", has_activation_link: false }),
      user({ id: 2, username: "ben", has_activation_link: true }),
    ]);

    expect(summary.level).toBe("blocked");
    expect(summary.usersWithoutLinks).toBe(1);
    expect(summary.primaryAction?.target).toBe("generate_missing_links");
    expect(summary.needsAttentionUsers[0].label).toBe("needs link");
  });

  it("reports activation in progress when links exist and users are pending", () => {
    const summary = deriveActivationCampaignSummary([
      user({ id: 1, is_activated: true }),
      user({ id: 2, has_activation_link: true }),
    ]);

    expect(summary.level).toBe("review");
    expect(summary.headline).toBe("Activation in progress - 1 of 2 users activated.");
    expect(summary.activationPercent).toBe(50);
  });

  it("marks activation healthy at conservative completion thresholds", () => {
    const users = Array.from({ length: 10 }, (_, index) =>
      user({
        id: index + 1,
        username: `user.${index + 1}`,
        is_activated: index < 9,
        has_activation_link: index >= 9,
      }),
    );

    const summary = deriveActivationCampaignSummary(users);

    expect(summary.level).toBe("healthy");
    expect(summary.activatedUsers).toBe(9);
    expect(summary.pendingUsers).toBe(1);
  });

  it("filters users by activation campaign states", () => {
    const pendingNeedsLink = user({ id: 1, username: "needs", has_activation_link: false });
    const pendingHasLink = user({ id: 2, username: "linked", has_activation_link: true });
    const activated = user({ id: 3, username: "active", is_activated: true });

    expect(matchesActivationFilter(pendingNeedsLink, "__needs_link")).toBe(true);
    expect(matchesActivationFilter(pendingHasLink, "__has_link")).toBe(true);
    expect(matchesActivationFilter(activated, "__activated")).toBe(true);
  });

  it("formats campaign timestamps in local readable language", () => {
    expect(
      formatActivationTimestamp(
        "2026-05-21T14:35:00",
        new Date("2026-05-21T16:00:00"),
      ),
    ).toBe("today at 14:35");
  });
});
