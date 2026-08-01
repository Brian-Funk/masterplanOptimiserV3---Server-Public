"use client";

import { useState } from "react";
import { AlertCircle, CheckCircle, CircleHelp, Link2, UserPlus } from "lucide-react";
import {
  formatActivationTimestamp,
  type ActivationCampaignActionTarget,
  type ActivationCampaignSummary,
} from "@/lib/activationCampaign";

const LEVEL_STYLES = {
  healthy: {
    icon: CheckCircle,
    ring: "ring-green-500/20",
    iconColour: "text-green-600 dark:text-green-300",
    bar: "bg-green-500",
  },
  review: {
    icon: CircleHelp,
    ring: "ring-amber-500/20",
    iconColour: "text-amber-600 dark:text-amber-300",
    bar: "bg-amber-500",
  },
  blocked: {
    icon: AlertCircle,
    ring: "ring-red-500/20",
    iconColour: "text-red-600 dark:text-red-300",
    bar: "bg-red-500",
  },
  unknown: {
    icon: CircleHelp,
    ring: "ring-gray-400/20",
    iconColour: "text-gray-500 dark:text-gray-300",
    bar: "bg-gray-400",
  },
};

export function ActivationCampaignCard({
  summary,
  onPrimaryAction,
}: {
  summary: ActivationCampaignSummary;
  onPrimaryAction?: (target: ActivationCampaignActionTarget) => void;
}) {
  const [showAllAttention, setShowAllAttention] = useState(false);
  const style = LEVEL_STYLES[summary.level];
  const Icon = style.icon;
  const visibleAttention = showAllAttention
    ? summary.needsAttentionUsers
    : summary.needsAttentionUsers.slice(0, 3);
  const lastActivation = formatActivationTimestamp(summary.lastActivationAt);
  const lastLink = formatActivationTimestamp(summary.lastLinkGeneratedAt);

  return (
    <section
      aria-label="Activation campaign status"
      className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-900"
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className={`inline-flex h-6 w-6 items-center justify-center rounded-full bg-white ring-1 dark:bg-gray-900 ${style.ring}`}
            >
              <Icon className={`h-3.5 w-3.5 ${style.iconColour}`} />
            </span>
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              {summary.headline}
            </h3>
          </div>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {summary.description}
          </p>
        </div>

        {summary.primaryAction && (
          <button
            type="button"
            onClick={() => onPrimaryAction?.(summary.primaryAction!.target)}
            className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800"
          >
            {summary.primaryAction.target === "add_users" ? (
              <UserPlus className="h-3.5 w-3.5" />
            ) : (
              <Link2 className="h-3.5 w-3.5" />
            )}
            {summary.primaryAction.label}
          </button>
        )}
      </div>

      <div className="mt-4">
        <div className="mb-1 flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
          <span>{summary.activatedUsers} / {summary.totalUsers} activated</span>
          <span>{summary.activationPercent}%</span>
        </div>
        <div
          role="progressbar"
          aria-label="Activation progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={summary.activationPercent}
          className="h-1.5 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800"
        >
          <div
            className={`h-full rounded-full ${style.bar}`}
            style={{ width: `${summary.activationPercent}%` }}
          />
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
        <span>Activated <strong className="font-semibold text-gray-700 dark:text-gray-200">{summary.activatedUsers}</strong></span>
        <span>Pending <strong className="font-semibold text-gray-700 dark:text-gray-200">{summary.pendingUsers}</strong></span>
        <span>Needs link <strong className="font-semibold text-gray-700 dark:text-gray-200">{summary.usersWithoutLinks}</strong></span>
        <span>Ready to email <strong className="font-semibold text-gray-700 dark:text-gray-200">{summary.usersReadyToEmail}</strong></span>
        {summary.usersWithoutEmail > 0 && <span>Missing email <strong className="font-semibold text-gray-700 dark:text-gray-200">{summary.usersWithoutEmail}</strong></span>}
        {summary.emailFailures > 0 && <span>Email problems <strong className="font-semibold text-red-600 dark:text-red-300">{summary.emailFailures}</strong></span>}
        {lastActivation && <span>Last activation {lastActivation}</span>}
        {!lastActivation && lastLink && <span>Last link generated {lastLink}</span>}
      </div>

      {summary.needsAttentionUsers.length > 0 && (
        <div className="mt-3 border-t border-gray-100 pt-3 dark:border-gray-800">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
              Needs attention
            </p>
            {summary.needsAttentionUsers.length > 3 && (
              <button
                type="button"
                onClick={() => setShowAllAttention((value) => !value)}
                className="text-xs font-medium text-gray-500 underline-offset-2 hover:text-gray-700 hover:underline dark:text-gray-400 dark:hover:text-gray-200"
              >
                {showAllAttention ? "Show less" : "Show all needing attention"}
              </button>
            )}
          </div>
          <div className="space-y-1">
            {visibleAttention.map((user) => (
              <div
                key={user.id}
                className="flex items-center justify-between gap-3 rounded-lg bg-gray-50 px-2.5 py-2 text-xs dark:bg-gray-800/70"
              >
                <div className="min-w-0">
                  <p className="truncate font-medium text-gray-800 dark:text-gray-100">
                    {user.name}
                    <span className="ml-1 font-normal text-gray-400">@{user.username}</span>
                  </p>
                  <p className="text-gray-500 dark:text-gray-400">{user.detail}</p>
                </div>
                <span className="shrink-0 rounded-full bg-white px-2 py-0.5 text-gray-600 ring-1 ring-gray-200 dark:bg-gray-900 dark:text-gray-300 dark:ring-gray-700">
                  {user.label}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
