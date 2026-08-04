"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

type Governance = {
  configured: boolean;
  message?: string;
  version?: number;
  published_at?: string;
  controller_legal_name?: string;
  controller_postal_address?: string;
  controller_country?: string;
  privacy_contact_email?: string;
  privacy_contact_phone?: string | null;
  dpo_contact?: string | null;
  supervisory_authority_name?: string;
  supervisory_authority_url?: string;
  processor_summary?: string;
  retention_summary?: string;
  rights_summary?: string;
  terms_summary?: string;
  processing_purposes?: Array<{ purpose_code: string; description: string; required_or_optional: string }>;
  data_categories?: Array<{ category_code: string; display_name: string; visibility: string }>;
  processors?: Array<{ provider_code: string; display_name: string; service: string; hosting_countries: string[]; public_notice_summary: string }>;
  retention?: Record<string, string | number | boolean | null>;
  feature_disclosures?: Array<{ code: string; text: string }>;
  rights_request_url?: string | null;
  incident_contact_email?: string | null;
  permitted_data?: { purpose: string; allowed: string[]; unsupported: string[] };
  storage?: {
    tracking: boolean;
    session_cookie: string;
    csrf_cookie: string;
    session_metadata?: string;
    offline_schedule?: string;
    application_shell?: string;
    preferences?: string;
    tab_state?: string;
  };
  authentication?: string;
};

export type GovernanceSection = "privacy" | "legal" | "terms" | "data-policy" | "retention" | "rights" | "processors";

const headings: Record<GovernanceSection, string> = {
  privacy: "Privacy notice",
  legal: "Controller and legal notice",
  terms: "Instance terms",
  "data-policy": "Permitted-data policy",
  retention: "Retention and deletion",
  rights: "Your data-protection rights",
  processors: "Processors and service providers",
};

/** Render one stable public legal-centre section from the published local policy. */
export function GovernanceNotice({ section }: { section: GovernanceSection }) {
  const [notice, setNotice] = useState<Governance | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/v1/governance/public", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("notice-unavailable");
        setNotice(await response.json());
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setFailed(true);
      });
    return () => controller.abort();
  }, []);

  return (
    <main className="min-h-screen bg-gray-50 px-6 py-12 text-gray-700 dark:bg-gray-900 dark:text-gray-300">
      <article className="mx-auto max-w-3xl space-y-6">
        <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100">{headings[section]}</h1>
        {!notice && !failed && <p role="status">Loading the published instance notice...</p>}
        {(failed || notice?.configured === false) && (
          <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-amber-950 dark:bg-amber-950 dark:text-amber-100">
            {notice?.message || "The published controller notice is temporarily unavailable."}
            <p className="mt-2">Do not rely on generic project information as the privacy notice for this self-hosted instance. Contact the instance operator.</p>
          </div>
        )}
        {notice?.configured && (
          <>
            {(section === "privacy" || section === "legal") && (
              <section className="space-y-2">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Controller</h2>
                <p>{notice.controller_legal_name}</p>
                {notice.controller_postal_address && <p className="whitespace-pre-line">{notice.controller_postal_address}</p>}
                {notice.controller_country && <p>Country: {notice.controller_country}</p>}
                <p><a className="text-blue-600 underline dark:text-blue-400" href={`mailto:${notice.privacy_contact_email}`}>{notice.privacy_contact_email}</a></p>
                {notice.privacy_contact_phone && <p>{notice.privacy_contact_phone}</p>}
                {notice.dpo_contact && <p>Data-protection contact: {notice.dpo_contact}</p>}
              </section>
            )}
            {(section === "privacy" || section === "data-policy") && notice.permitted_data && (
              <section className="space-y-3">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Purpose and permitted information</h2>
                <p>{notice.permitted_data.purpose}. Optional data must be necessary for that purpose.</p>
                <p><strong>Normally permitted:</strong> {notice.permitted_data.allowed.join(", ")}.</p>
                <p><strong>Not supported:</strong> {notice.permitted_data.unsupported.join(", ")}.</p>
              </section>
            )}
            {section === "privacy" && notice.storage && (
              <section className="space-y-2">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Passkey authentication</h2>
                <p>{notice.authentication}</p>
                <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Cookies and browser storage</h2>
                <p>No analytics, advertising or cross-site tracking is enabled by the supported release.</p>
                <ul className="list-disc space-y-1 pl-6">
                  <li>{notice.storage.session_cookie}</li>
                  <li>{notice.storage.csrf_cookie}</li>
                  {notice.storage.session_metadata && <li>{notice.storage.session_metadata}</li>}
                  {notice.storage.offline_schedule && <li>{notice.storage.offline_schedule}</li>}
                  {notice.storage.application_shell && <li>{notice.storage.application_shell}</li>}
                  {notice.storage.preferences && <li>{notice.storage.preferences}</li>}
                  {notice.storage.tab_state && <li>{notice.storage.tab_state}</li>}
                </ul>
                {notice.feature_disclosures && notice.feature_disclosures.length > 0 && <>
                  <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Deployment features</h2>
                  <ul className="list-disc space-y-1 pl-6">{notice.feature_disclosures.map((item) => <li key={item.code}>{item.text}</li>)}</ul>
                </>}
              </section>
            )}
            {(section === "privacy" || section === "retention") && (
              <><OptionalTextSection heading="Retention and deletion" value={notice.retention_summary} />
              {notice.retention && <table className="w-full border-collapse text-sm"><caption className="pb-2 text-left font-semibold">Controller-selected retention periods</caption><tbody>{Object.entries(notice.retention).filter(([, value]) => value !== null).map(([key, value]) => <tr key={key} className="border-t"><th scope="row" className="py-2 pr-3 text-left font-medium">{sentenceLabel(key)}</th><td className="py-2">{String(value)}</td></tr>)}</tbody></table>}</>
            )}
            {(section === "privacy" || section === "rights") && (
              <section className="space-y-2"><h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Your rights</h2><p>Depending on the law that applies, you may have rights to access, correct, erase, restrict or object to processing, and to receive portable data. Contact the controller at <a className="text-blue-600 underline dark:text-blue-400" href={`mailto:${notice.privacy_contact_email}`}>{notice.privacy_contact_email}</a>; the controller will assess the applicable right, scope and proportionate identity verification.</p>{notice.rights_summary && <p className="whitespace-pre-line">{notice.rights_summary}</p>}</section>
            )}
            {(section === "privacy" || section === "processors") && (
              <><OptionalTextSection heading="Processors and service providers" value={notice.processor_summary} />
              {notice.processors && <ul className="space-y-3">{notice.processors.map((processor) => <li key={processor.provider_code} className="rounded border p-3"><strong>{processor.display_name}</strong><p>{processor.service}</p><p>{processor.public_notice_summary}</p><p>Hosting countries: {processor.hosting_countries.join(", ")}</p></li>)}</ul>}</>
            )}
            {(section === "legal" || section === "terms") && <section className="space-y-2"><h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Terms for this instance</h2><p>Use is limited to authorised operational event scheduling and access management. Users must follow the permitted-data boundary and protect their own account access.</p>{notice.terms_summary && <p className="whitespace-pre-line">{notice.terms_summary}</p>}</section>}
            {section === "rights" && (
              <section>
                <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Supervisory authority</h2>
                {notice.supervisory_authority_name && notice.supervisory_authority_url
                  ? <a className="text-blue-600 underline dark:text-blue-400" href={notice.supervisory_authority_url} rel="noopener noreferrer">{notice.supervisory_authority_name}</a>
                  : notice.supervisory_authority_name
                    ? <p>{notice.supervisory_authority_name}</p>
                    : <p>Where applicable, you may lodge a complaint with the competent data-protection supervisory authority.</p>}
                {notice.rights_request_url && <p><a className="text-blue-600 underline dark:text-blue-400" href={notice.rights_request_url} rel="noopener noreferrer">Submit a rights request</a></p>}
                {notice.incident_contact_email && <p>Incident contact: <a className="text-blue-600 underline dark:text-blue-400" href={`mailto:${notice.incident_contact_email}`}>{notice.incident_contact_email}</a></p>}
              </section>
            )}
            <p className="border-t border-gray-200 pt-4 text-sm dark:border-gray-700">
              Published policy version {notice.version} on {notice.published_at ? new Date(notice.published_at).toLocaleDateString() : "an unknown date"}.
            </p>
          </>
        )}
        <nav className="flex flex-wrap gap-3 border-t border-gray-200 pt-5 text-sm dark:border-gray-700" aria-label="Legal centre">
          <Link href="/privacy">Privacy</Link><Link href="/legal">Legal</Link><Link href="/terms">Terms</Link><Link href="/data-policy">Permitted data</Link>
          <Link href="/retention">Retention</Link><Link href="/rights">Rights</Link><Link href="/processors">Processors</Link><Link href="/licence">Licence</Link>
        </nav>
      </article>
    </main>
  );
}

function sentenceLabel(value: string) {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function OptionalTextSection({ heading, value }: { heading: string; value?: string }) {
  return <section className="space-y-2"><h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">{heading}</h2>{value && <p className="whitespace-pre-line">{value}</p>}</section>;
}
