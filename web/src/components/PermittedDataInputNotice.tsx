"use client";

import { getApiUrl } from "@/lib/environment";

export function PermittedDataInputNotice({
  acknowledged,
  version,
  sha256,
}: {
  acknowledged: boolean;
  version?: number | null;
  sha256?: string | null;
}) {
  const policyUrl = version
    ? `${getApiUrl()}/api/v1/governance/public/versions/${version}/data-policy.html`
    : `${getApiUrl()}/api/v1/governance/public/data-policy.html`;

  if (acknowledged) {
    return (
      <p className="rounded border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300">
        Operational data only. <a className="underline" href={policyUrl}>View permitted-data rules{version ? ` v${version}` : ""}</a>
        {sha256 ? <span className="ml-2 font-mono">{sha256.slice(0, 12)}...</span> : null}
      </p>
    );
  }

  return (
    <aside className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-950 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
      <p className="font-semibold">Operational information only</p>
      <p className="mt-1">Do not enter health, dietary, safeguarding, political, religious, identity, disciplinary or unrelated private information.</p>
      <a className="mt-1 inline-block underline" href={policyUrl}>View the exact permitted-data rules{version ? ` v${version}` : ""}</a>
    </aside>
  );
}
