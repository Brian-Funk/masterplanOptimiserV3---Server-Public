export type ActivationConfidence = "healthy" | "review" | "blocked" | "unknown";

export type ActivationCampaignActionTarget =
  | "add_users"
  | "generate_missing_links"
  | "filter_pending"
  | "view_pending";

export type ActivationCampaignUser = {
  id: number;
  username: string;
  display_name: string;
  email?: string | null;
  is_active?: boolean;
  is_activated: boolean;
  has_activation_link?: boolean;
  linked_person_id?: number | null;
  can_edit?: boolean;
  last_activation_at?: string | null;
  last_activation_link_created_at?: string | null;
  activation_email_status?: string | null;
  has_valid_email?: boolean;
};

export type ActivationCampaignAttentionUser = {
  id: number;
  name: string;
  username: string;
  reason: "needs_link" | "pending";
  label: string;
  detail: string;
};

export type ActivationCampaignSummary = {
  level: ActivationConfidence;
  totalUsers: number;
  activatedUsers: number;
  pendingUsers: number;
  usersWithLinks: number;
  usersWithoutLinks: number;
  usersReadyToEmail: number;
  usersWithoutEmail: number;
  emailFailures: number;
  linkedUsers: number;
  unlinkedUsers: number;
  editorUsers: number;
  lastActivationAt: string | null;
  lastLinkGeneratedAt: string | null;
  activationPercent: number;
  headline: string;
  description: string;
  primaryAction?: {
    label: string;
    target: ActivationCampaignActionTarget;
  };
  needsAttentionUsers: ActivationCampaignAttentionUser[];
};

export type ActivationUserFilter =
  | ""
  | "__activated"
  | "__pending"
  | "__needs_link"
  | "__has_link"
  | "__not_linked"
  | "__editor"
  | "__attention"
  | "__email_failed"
  | "__missing_email";

function activeUsers(users: ActivationCampaignUser[]): ActivationCampaignUser[] {
  return users.filter((user) => user.is_active !== false);
}

function latestIso(values: Array<string | null | undefined>): string | null {
  const dates = values
    .filter(Boolean)
    .map((value) => new Date(value as string))
    .filter((date) => !Number.isNaN(date.getTime()))
    .sort((a, b) => b.getTime() - a.getTime());
  return dates[0]?.toISOString() ?? null;
}

export function formatActivationTimestamp(
  value?: string | null,
  now: Date = new Date(),
): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;

  const time = new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diffDays = Math.round((today.getTime() - target.getTime()) / 86400000);
  if (diffDays === 0) return `today at ${time}`;
  if (diffDays === 1) return `yesterday at ${time}`;
  const day = String(date.getDate()).padStart(2, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0");
  return `${day}.${month}.${date.getFullYear()} at ${time}`;
}

export function activationUserNeedsAttention(
  user: ActivationCampaignUser,
): boolean {
  if (user.is_active === false || user.is_activated) return false;
  return true;
}

function attentionForUser(
  user: ActivationCampaignUser,
): ActivationCampaignAttentionUser | null {
  if (!activationUserNeedsAttention(user)) return null;
  if (!user.has_activation_link) {
    return {
      id: user.id,
      name: user.display_name,
      username: user.username,
      reason: "needs_link",
      label: "needs link",
      detail: "Generate an activation link before asking this user to activate.",
    };
  }
  return {
    id: user.id,
    name: user.display_name,
    username: user.username,
    reason: "pending",
    label: "pending",
    detail: "Activation link is available, but the user has not activated yet.",
  };
}

export function deriveActivationCampaignSummary(
  users: ActivationCampaignUser[],
): ActivationCampaignSummary {
  const relevantUsers = activeUsers(users);
  const totalUsers = relevantUsers.length;
  const activatedUsers = relevantUsers.filter((user) => user.is_activated).length;
  const pendingUsers = Math.max(totalUsers - activatedUsers, 0);
  const usersWithLinks = relevantUsers.filter((user) => user.has_activation_link).length;
  const usersWithoutLinks = relevantUsers.filter(
    (user) => !user.is_activated && !user.has_activation_link,
  ).length;
  const usersReadyToEmail = relevantUsers.filter(
    (user) => !user.is_activated && user.has_valid_email,
  ).length;
  const usersWithoutEmail = relevantUsers.filter(
    (user) => !user.is_activated && !user.has_valid_email,
  ).length;
  const emailFailures = relevantUsers.filter((user) =>
    ["failed", "unknown", "not_attempted"].includes(
      user.activation_email_status || "",
    ),
  ).length;
  const linkedUsers = relevantUsers.filter((user) => user.linked_person_id != null).length;
  const unlinkedUsers = Math.max(totalUsers - linkedUsers, 0);
  const editorUsers = relevantUsers.filter((user) => user.can_edit).length;
  const needsAttentionUsers = relevantUsers
    .map(attentionForUser)
    .filter((user): user is ActivationCampaignAttentionUser => Boolean(user));
  const activationPercent =
    totalUsers > 0 ? Math.round((activatedUsers / totalUsers) * 100) : 0;
  const lastActivationAt = latestIso(
    relevantUsers.map((user) => user.last_activation_at),
  );
  const lastLinkGeneratedAt = latestIso(
    relevantUsers.map((user) => user.last_activation_link_created_at),
  );

  if (totalUsers === 0) {
    return {
      level: "unknown",
      totalUsers,
      activatedUsers,
      pendingUsers,
      usersWithLinks,
      usersWithoutLinks,
      usersReadyToEmail,
      usersWithoutEmail,
      emailFailures,
      linkedUsers,
      unlinkedUsers,
      editorUsers,
      lastActivationAt,
      lastLinkGeneratedAt,
      activationPercent,
      headline: "No users yet",
      description: "Add users before starting activation.",
      primaryAction: { label: "Add users", target: "add_users" },
      needsAttentionUsers,
    };
  }

  if (usersWithoutLinks > 0) {
    return {
      level: "blocked",
      totalUsers,
      activatedUsers,
      pendingUsers,
      usersWithLinks,
      usersWithoutLinks,
      usersReadyToEmail,
      usersWithoutEmail,
      emailFailures,
      linkedUsers,
      unlinkedUsers,
      editorUsers,
      lastActivationAt,
      lastLinkGeneratedAt,
      activationPercent,
      headline: `Action needed - ${usersWithoutLinks} user${usersWithoutLinks === 1 ? "" : "s"} still need${usersWithoutLinks === 1 ? "s" : ""} activation links.`,
      description: "Generate missing links before asking users to activate.",
      primaryAction: {
        label: "Generate missing links",
        target: "generate_missing_links",
      },
      needsAttentionUsers,
    };
  }

  if (activatedUsers === 0) {
    return {
      level: "review",
      totalUsers,
      activatedUsers,
      pendingUsers,
      usersWithLinks,
      usersWithoutLinks,
      usersReadyToEmail,
      usersWithoutEmail,
      emailFailures,
      linkedUsers,
      unlinkedUsers,
      editorUsers,
      lastActivationAt,
      lastLinkGeneratedAt,
      activationPercent,
      headline: "Activation not started",
      description: `${totalUsers} user${totalUsers === 1 ? "" : "s"} exist, but nobody has activated yet.`,
      primaryAction: { label: "Filter pending users", target: "filter_pending" },
      needsAttentionUsers,
    };
  }

  if (activationPercent >= 90) {
    return {
      level: "healthy",
      totalUsers,
      activatedUsers,
      pendingUsers,
      usersWithLinks,
      usersWithoutLinks,
      usersReadyToEmail,
      usersWithoutEmail,
      emailFailures,
      linkedUsers,
      unlinkedUsers,
      editorUsers,
      lastActivationAt,
      lastLinkGeneratedAt,
      activationPercent,
      headline: `Activation healthy - ${activatedUsers} of ${totalUsers} users activated.`,
      description:
        pendingUsers > 0
          ? "Most participants can access the event."
          : "All participants can access the event.",
      primaryAction:
        pendingUsers > 0
          ? { label: "View pending users", target: "view_pending" }
          : undefined,
      needsAttentionUsers,
    };
  }

  return {
    level: "review",
    totalUsers,
    activatedUsers,
    pendingUsers,
    usersWithLinks,
    usersWithoutLinks,
    usersReadyToEmail,
    usersWithoutEmail,
    emailFailures,
    linkedUsers,
    unlinkedUsers,
    editorUsers,
    lastActivationAt,
    lastLinkGeneratedAt,
    activationPercent,
    headline: `Activation in progress - ${activatedUsers} of ${totalUsers} users activated.`,
    description: `${pendingUsers} user${pendingUsers === 1 ? "" : "s"} still need${pendingUsers === 1 ? "s" : ""} to activate.`,
    primaryAction: { label: "Filter pending users", target: "filter_pending" },
    needsAttentionUsers,
  };
}

export function matchesActivationFilter(
  user: ActivationCampaignUser,
  filter: ActivationUserFilter | string,
): boolean {
  if (filter === "__activated") return user.is_activated;
  if (filter === "__pending") return !user.is_activated;
  if (filter === "__needs_link") return !user.is_activated && !user.has_activation_link;
  if (filter === "__has_link") return Boolean(user.has_activation_link);
  if (filter === "__not_linked") return user.linked_person_id == null;
  if (filter === "__editor") return Boolean(user.can_edit);
  if (filter === "__attention") return activationUserNeedsAttention(user);
  if (filter === "__email_failed") {
    return ["failed", "unknown", "not_attempted"].includes(
      user.activation_email_status || "",
    );
  }
  if (filter === "__missing_email") {
    return !user.is_activated && !user.has_valid_email;
  }
  return true;
}
