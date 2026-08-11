"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";

import { Logo } from "@/components/Logo";
import { PUBLIC_TEXT_LINK_CLASS } from "@/lib/publicLinks";

type Governance = {
  configured: boolean;
  message?: string;
  version?: number;
  published_at?: string;
  content_sha256?: string;
  instance_name?: string;
  jurisdiction_scope?: string;
  controller_legal_name?: string;
  controller_postal_address?: string;
  controller_country?: string;
  privacy_contact_email?: string;
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

const leads: Record<GovernanceSection, string> = {
  privacy: "How this deployment uses, protects, retains and shares operational information.",
  legal: "The controller and contact details for this self-hosted deployment.",
  terms: "The operating terms that apply to this deployment.",
  "data-policy": "The exact boundary between supported operational information and unsupported sensitive data.",
  retention: "The controller-selected periods and deletion approach for this deployment.",
  rights: "How to exercise data-protection rights with the controller of this deployment.",
  processors: "The controller-declared providers and processing locations used by this deployment.",
};

const navigation: Array<{ section: GovernanceSection; label: string }> = [
  { section: "privacy", label: "Privacy" },
  { section: "legal", label: "Legal" },
  { section: "terms", label: "Terms" },
  { section: "data-policy", label: "Permitted data" },
  { section: "retention", label: "Retention" },
  { section: "rights", label: "Rights" },
  { section: "processors", label: "Processors" },
];

const GDPR_URL = "https://eur-lex.europa.eu/eli/reg/2016/679/2016-05-04";
const FADP_URL = "https://www.fedlex.admin.ch/eli/cc/2022/491/en";

const RIGHTS = [
  { title: "Access", description: "Ask whether personal data about you is processed and request the data and relevant processing information.", gdpr: "Art. 15", fadp: "Art. 25" },
  { title: "Correction", description: "Ask for inaccurate personal data to be corrected and incomplete data to be completed where appropriate.", gdpr: "Art. 16", fadp: "Art. 32(1)" },
  { title: "Erasure", description: "Ask for deletion or destruction where the applicable legal conditions are met. This right is not unconditional.", gdpr: "Art. 17", fadp: "Art. 32(2)(c)" },
  { title: "Restriction", description: "Ask for disputed or potentially unlawful processing to be limited while the matter is assessed.", gdpr: "Art. 18", fadp: "Art. 32(2)(b)" },
  { title: "Objection", description: "Object to processing of your personal data. The controller must assess whether it may lawfully continue.", gdpr: "Art. 21", fadp: "Arts. 30(2)(b), 32" },
  { title: "Portability", description: "Ask for qualifying data in a commonly used, machine-readable format where the statutory conditions apply.", gdpr: "Art. 20", fadp: "Art. 28" },
  { title: "Automated decisions", description: "Ask for applicable safeguards or human review of a solely automated decision that significantly affects you.", gdpr: "Art. 22", fadp: "Art. 21" },
] as const;

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
    <main className="min-h-screen bg-[var(--color-surface-alt)] px-3 py-3 text-[var(--color-foreground)] sm:px-6 sm:py-8">
      <article className="mx-auto max-w-5xl overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-800">
        <header className="border-t-4 border-t-blue-600 px-5 py-6 dark:border-t-blue-400 sm:px-9 sm:py-8">
          <div className="mb-7 flex items-center gap-3">
            <Logo height={46} />
            <div className="min-w-0">
              <p className="font-semibold text-gray-900 dark:text-gray-100">Masterplan Optimiser</p>
              <p className="truncate text-sm text-gray-500 dark:text-gray-400">{notice?.instance_name || "Self-hosted instance"}</p>
            </div>
          </div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-blue-600 dark:text-blue-300">Published governance</p>
          <h1 className="max-w-3xl text-3xl font-semibold tracking-tight text-gray-900 dark:text-gray-100 sm:text-4xl">{headings[section]}</h1>
          <p className="mt-3 max-w-3xl text-base text-gray-600 dark:text-gray-400">{leads[section]}</p>
          {notice?.configured && (
            <div className="mt-5 flex flex-wrap gap-2 text-xs text-gray-600 dark:text-gray-400">
              {notice.version !== undefined && <span className="rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 dark:border-gray-700 dark:bg-gray-700">Policy version {notice.version}</span>}
              {notice.published_at && <span className="rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 dark:border-gray-700 dark:bg-gray-700">Published {new Date(notice.published_at).toLocaleDateString()}</span>}
              {notice.content_sha256 && <code className="max-w-full break-all rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 font-mono dark:border-gray-700 dark:bg-gray-700">SHA-256 {notice.content_sha256}</code>}
            </div>
          )}
        </header>

        <nav className="flex flex-wrap gap-1 border-y border-gray-200 bg-gray-50 px-4 py-2.5 dark:border-gray-700 dark:bg-gray-700/60 sm:px-7" aria-label="Legal centre">
          {navigation.map((item) => (
            <Link
              key={item.section}
              href={`/${item.section}`}
              aria-current={item.section === section ? "page" : undefined}
              className={`rounded-lg px-2.5 py-1.5 text-sm font-semibold no-underline transition-colors ${item.section === section
                ? "bg-blue-50 text-blue-700 dark:bg-blue-950/60 dark:text-blue-200"
                : "text-gray-600 hover:bg-white hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-white"}`}
            >
              {item.label}
            </Link>
          ))}
          <Link href="/licence" className="rounded-lg px-2.5 py-1.5 text-sm font-semibold text-gray-600 no-underline hover:bg-white hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-white">Licence</Link>
        </nav>

        <div className="px-5 pb-10 pt-3 sm:px-9">
          {!notice && !failed && <p className="py-8 text-gray-600 dark:text-gray-400" role="status">Loading the published instance notice...</p>}
          {(failed || notice?.configured === false) && <UnavailableNotice message={notice?.message} />}
          {notice?.configured && <NoticeContent notice={notice} section={section} />}
        </div>

        <footer className="flex flex-col gap-1 border-t border-gray-200 bg-gray-50 px-5 py-4 text-xs text-gray-500 dark:border-gray-700 dark:bg-gray-700/60 dark:text-gray-400 sm:flex-row sm:items-center sm:justify-between sm:px-9">
          <span>Masterplan Optimiser self-hosted instance</span>
          {notice?.version && <a className={PUBLIC_TEXT_LINK_CLASS} href={`/api/v1/governance/public/versions/${notice.version}/${section}.html`}>Open permanent exact page</a>}
        </footer>
      </article>
    </main>
  );
}

function NoticeContent({ notice, section }: { notice: Governance; section: GovernanceSection }) {
  return <>
    {(section === "privacy" || section === "legal") && <ControllerSection notice={notice} />}
    {(section === "privacy" || section === "data-policy") && notice.permitted_data && <PermittedDataSection notice={notice} />}
    {section === "privacy" && notice.storage && <StorageSection notice={notice} />}
    {(section === "privacy" || section === "retention") && <RetentionSection notice={notice} />}
    {(section === "privacy" || section === "rights") && <RightsSection notice={notice} full={section === "rights"} />}
    {(section === "privacy" || section === "processors") && <ProcessorsSection notice={notice} />}
    {(section === "legal" || section === "terms") && <TermsSection notice={notice} />}
  </>;
}

function ControllerSection({ notice }: { notice: Governance }) {
  return <Section title="Controller">
    <p className="font-semibold text-gray-900 dark:text-gray-100">{notice.controller_legal_name}</p>
    {notice.controller_postal_address && <p className="whitespace-pre-line">{notice.controller_postal_address}</p>}
    {notice.controller_country && <p>Country: {notice.controller_country}</p>}
    <p><a className={PUBLIC_TEXT_LINK_CLASS} href={`mailto:${notice.privacy_contact_email}`}>{notice.privacy_contact_email}</a></p>
    {notice.dpo_contact && <p>Data-protection contact: <ContactValue value={notice.dpo_contact} /></p>}
  </Section>;
}

function PermittedDataSection({ notice }: { notice: Governance }) {
  const policy = notice.permitted_data!;
  return <Section title="Purpose and permitted information">
    <p>{policy.purpose}. Optional data must be necessary for that purpose.</p>
    <div className="mt-4 grid gap-3 sm:grid-cols-2">
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950/40"><strong className="text-gray-900 dark:text-gray-100">Normally permitted</strong><p className="mt-1">{policy.allowed.join(", ")}.</p></div>
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/40"><strong className="text-gray-900 dark:text-gray-100">Not supported</strong><p className="mt-1">{policy.unsupported.join(", ")}.</p></div>
    </div>
  </Section>;
}

function StorageSection({ notice }: { notice: Governance }) {
  const storage = notice.storage!;
  const storageItems = [storage.session_cookie, storage.csrf_cookie, storage.session_metadata, storage.offline_schedule, storage.application_shell, storage.preferences, storage.tab_state].filter(Boolean) as string[];
  return <>
    <Section title="Passkey authentication"><p>{notice.authentication}</p></Section>
    <Section title="Cookies and browser storage">
      <p>No analytics, advertising or cross-site tracking is enabled by the supported release.</p>
      <ul className="mt-3 list-disc space-y-2 pl-5 marker:text-blue-600">{storageItems.map((item) => <li key={item}>{item}</li>)}</ul>
    </Section>
    {notice.feature_disclosures && notice.feature_disclosures.length > 0 && <Section title="Deployment features"><ul className="list-disc space-y-2 pl-5 marker:text-blue-600">{notice.feature_disclosures.map((item) => <li key={item.code}>{item.text}</li>)}</ul></Section>}
  </>;
}

function RetentionSection({ notice }: { notice: Governance }) {
  const rows = Object.entries(notice.retention || {}).filter(([, value]) => value !== null);
  return <Section title="Retention and deletion">
    {notice.retention_summary && <p className="whitespace-pre-line">{notice.retention_summary}</p>}
    {rows.length > 0 && <div className="mt-4 overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700"><table className="w-full border-collapse text-sm"><caption className="bg-gray-50 px-4 py-3 text-left font-semibold text-gray-900 dark:bg-gray-700/60 dark:text-gray-100">Controller-selected retention periods</caption><tbody>{rows.map(([key, value]) => <tr key={key} className="border-t border-gray-200 dark:border-gray-700"><th scope="row" className="w-1/2 bg-gray-50 px-4 py-2.5 text-left font-medium text-gray-900 dark:bg-gray-700/40 dark:text-gray-100">{sentenceLabel(key)}</th><td className="px-4 py-2.5">{String(value)}</td></tr>)}</tbody></table></div>}
  </Section>;
}

function RightsSection({ notice, full }: { notice: Governance; full: boolean }) {
  const contact = notice.privacy_contact_email || "the controller's published privacy contact";
  return <Section title="Your rights">
    <p>Whether you are a participant, organiser, administrator or another account holder, data-protection rights concern your own personal data. They do not provide access to another person&apos;s information or to all operational records.</p>
    {!full && <><p className="mt-2">Depending on the law that applies, you may ask to access, correct, erase, restrict or object to processing, and to receive eligible data in a portable form. The controller will assess the applicable right, scope and proportionate identity verification.</p><p className="mt-3"><Link className={PUBLIC_TEXT_LINK_CLASS} href="/rights">Read how to exercise your rights</Link></p></>}
    {full && <>
      <div className="mt-4 rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-700/50"><strong className="text-gray-900 dark:text-gray-100">You can ask in ordinary language.</strong><p className="mt-1">You do not need to identify the applicable law or quote an article. Describe what you want to know or change, and the controller will assess the request.</p></div>
      {notice.jurisdiction_scope && <><Subheading>Applicable scope</Subheading><p>The controller recorded the following jurisdiction scope for this deployment: {notice.jurisdiction_scope}</p><p className="mt-2">The Swiss FADP, the EU GDPR, or both may apply. The controller must assess the law for the particular request; the software does not infer jurisdiction from hosting, network location or account role.</p></>}
      <Subheading>Rights you may exercise</Subheading>
      <ul className="grid list-none gap-3 p-0 sm:grid-cols-2">
        {RIGHTS.map((right) => <li key={right.title} className="flex min-h-40 flex-col rounded-xl border border-gray-200 p-4 dark:border-gray-700"><strong className="text-base text-gray-900 dark:text-gray-100">{right.title}</strong><p className="mt-1">{right.description}</p><div className="mt-auto flex flex-wrap gap-1.5 pt-4 text-xs font-semibold"><a className="rounded-md bg-blue-50 px-2 py-1 text-blue-700 underline decoration-blue-300 underline-offset-2 dark:bg-blue-950/60 dark:text-blue-300 dark:decoration-blue-700" href={GDPR_URL} rel="noopener noreferrer" target="_blank">GDPR {right.gdpr}</a><a className="rounded-md bg-blue-50 px-2 py-1 text-blue-700 underline decoration-blue-300 underline-offset-2 dark:bg-blue-950/60 dark:text-blue-300 dark:decoration-blue-700" href={FADP_URL} rel="noopener noreferrer" target="_blank">FADP {right.fadp}</a></div></li>)}
      </ul>
      <p className="mt-3 text-sm">The precise scope, exceptions and response depend on the applicable law, processing purpose, other people&apos;s rights, and any legally required retention.</p>
      {notice.rights_summary && <p className="mt-3 whitespace-pre-line">{notice.rights_summary}</p>}
      <Subheading>How to make a request</Subheading>
      <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-700/50"><p>You may write in ordinary language. Describe what you want to know or change and do not email unnecessary identity documents or sensitive information.</p><div className="mt-4 flex flex-wrap gap-2"><a className="inline-flex min-h-10 items-center justify-center rounded-lg bg-blue-600 px-4 py-2 font-semibold text-white no-underline hover:bg-blue-700" href={`mailto:${contact}`}>Email the privacy contact</a>{notice.rights_request_url && <a className="inline-flex min-h-10 items-center justify-center rounded-lg border border-gray-300 bg-white px-4 py-2 font-semibold text-gray-900 no-underline hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100" href={notice.rights_request_url} rel="noopener noreferrer">Open the rights-request form</a>}</div></div>
      <Subheading>What happens next</Subheading>
      <ol className="list-decimal space-y-2 pl-5 marker:font-semibold marker:text-blue-600"><li>The controller confirms the scope of the request.</li><li>Only proportionate identity verification is requested where needed.</li><li>The controller assesses the applicable law, any limits, and the response or action required.</li></ol>
      <p className="mt-3">Under <a className={PUBLIC_TEXT_LINK_CLASS} href={GDPR_URL} rel="noopener noreferrer" target="_blank">GDPR Article 12</a>, a controller normally responds without undue delay and within one month. Under the <a className={PUBLIC_TEXT_LINK_CLASS} href="https://www.fedlex.admin.ch/eli/cc/2022/568/en" rel="noopener noreferrer" target="_blank">Swiss Data Protection Ordinance</a>, access information is normally provided within 30 days.</p>
      <Subheading>Complaint and legal remedy</Subheading>
      <p>If the controller does not handle a request properly, the applicable route may include a complaint to a supervisory authority or a legal remedy. See <a className={PUBLIC_TEXT_LINK_CLASS} href={GDPR_URL} rel="noopener noreferrer" target="_blank">GDPR Articles 77 and 79</a> and <a className={PUBLIC_TEXT_LINK_CLASS} href={FADP_URL} rel="noopener noreferrer" target="_blank">FADP Articles 32 and 49 onward</a>.</p>
      <Subheading>Supervisory authority</Subheading>
      {notice.supervisory_authority_name && notice.supervisory_authority_url ? <p><a className={PUBLIC_TEXT_LINK_CLASS} href={notice.supervisory_authority_url} rel="noopener noreferrer" target="_blank">{notice.supervisory_authority_name}</a></p> : notice.supervisory_authority_name ? <p>{notice.supervisory_authority_name}</p> : <p>Where applicable, you may lodge a complaint with the competent data-protection supervisory authority.</p>}
      {notice.incident_contact_email && <p className="mt-2">Incident contact: <a className={PUBLIC_TEXT_LINK_CLASS} href={`mailto:${notice.incident_contact_email}`}>{notice.incident_contact_email}</a></p>}
    </>}
  </Section>;
}

function ProcessorsSection({ notice }: { notice: Governance }) {
  return <Section title="Processors and service providers">
    {notice.processor_summary && <p className="whitespace-pre-line">{notice.processor_summary}</p>}
    {notice.processors && notice.processors.length > 0 && <ul className="mt-4 grid list-none gap-3 p-0 sm:grid-cols-2">{notice.processors.map((processor) => <li key={processor.provider_code} className="rounded-xl border border-gray-200 p-4 dark:border-gray-700"><strong className="text-gray-900 dark:text-gray-100">{processor.display_name}</strong><p className="mt-1">{processor.service}</p><p className="mt-2">{processor.public_notice_summary}</p><p className="mt-2 text-sm">Countries: {processor.hosting_countries.join(", ")}</p></li>)}</ul>}
  </Section>;
}

function TermsSection({ notice }: { notice: Governance }) {
  return <Section title="Terms for this instance"><p>Use is limited to authorised operational event scheduling and access management. Users must follow the <Link className={PUBLIC_TEXT_LINK_CLASS} href="/data-policy">permitted-data boundary</Link> and protect their own account access.</p>{notice.terms_summary && <p className="mt-3 whitespace-pre-line">{notice.terms_summary}</p>}</Section>;
}

function ContactValue({ value }: { value: string }) {
  const contact = value.trim();
  if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contact)) {
    return <a className={PUBLIC_TEXT_LINK_CLASS} href={`mailto:${contact}`}>{contact}</a>;
  }
  if (/^https?:\/\//.test(contact)) {
    return <a className={PUBLIC_TEXT_LINK_CLASS} href={contact} rel="noopener noreferrer" target="_blank">{contact}</a>;
  }
  return contact;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <section className="mt-7 border-t border-gray-200 pt-7 first:mt-0 first:border-t-0 dark:border-gray-700"><h2 className="mb-2 text-xl font-semibold tracking-tight text-gray-900 dark:text-gray-100">{title}</h2><div className="max-w-[78ch] text-gray-600 dark:text-gray-300">{children}</div></section>;
}

function Subheading({ children }: { children: ReactNode }) {
  return <h3 className="mb-2 mt-7 text-lg font-semibold text-gray-900 dark:text-gray-100">{children}</h3>;
}

function UnavailableNotice({ message }: { message?: string }) {
  return <div role="alert" className="my-6 rounded-xl border border-amber-300 bg-amber-50 p-4 text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100">{message || "The published controller notice is temporarily unavailable."}<p className="mt-2">Do not rely on generic project information as the privacy notice for this self-hosted instance. Contact the instance operator.</p></div>;
}

function sentenceLabel(value: string) {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
