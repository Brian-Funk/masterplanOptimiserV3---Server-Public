"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, FileText, Info, ShieldCheck } from "lucide-react";

import { GovernanceEditor } from "@/components/GovernanceEditor";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { useAuth } from "@/contexts/AuthContext";
import {
  createInitialStructured,
  createSuggestedSummaries,
  GovernanceStructured,
  RuntimeFeatures,
  RuntimeSettings,
} from "@/lib/governanceDraft";
import { apiFetch } from "@/lib/api";
import { withReauth } from "@/lib/reauth";

type FormState = {
  controller_type: "organisation" | "individual";
  controller_legal_name: string;
  controller_postal_address: string;
  controller_country: string;
  privacy_contact_email: string;
  privacy_contact_phone: string;
  dpo_contact: string;
  supervisory_authority_name: string;
  supervisory_authority_url: string;
  default_locale: string;
  processor_summary: string;
  retention_summary: string;
  rights_summary: string;
  terms_summary: string;
};

type PreflightCheck = {
  code: string;
  status: "ready" | "missing" | "contradiction" | "requires_controller_decision" | "externally_unverifiable";
  message: string;
};

const runtimeFallback: RuntimeFeatures = { smtp_enabled: false, push_enabled: false, ha_enabled: false, dns_mode: "dns_only" };

const empty: FormState = {
  controller_type: "organisation",
  controller_legal_name: "",
  controller_postal_address: "",
  controller_country: "",
  privacy_contact_email: "",
  privacy_contact_phone: "",
  dpo_contact: "",
  supervisory_authority_name: "",
  supervisory_authority_url: "",
  default_locale: "en",
  processor_summary: "",
  retention_summary: "",
  rights_summary: "",
  terms_summary: "",
};

const confirmationLabels = {
  authorised_to_configure: "I am authorised to configure this instance for the identified controller.",
  reviewed_generated_documents: "I reviewed the exact generated documents and their public consequences.",
  confirmed_permitted_data_policy: "I confirm the permitted-data boundary for this instance.",
  understands_no_legal_certification: "I understand that publication is not legal certification or legal advice.",
};

type ConfirmationKey = keyof typeof confirmationLabels;

function Guidance({ title, children, link }: { title: string; children: React.ReactNode; link?: { href: string; label: string } }) {
  return <div className="flex items-start gap-3 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900 dark:border-blue-800 dark:bg-blue-950/50 dark:text-blue-100"><Info size={18} className="mt-0.5 shrink-0" aria-hidden="true" /><div><p className="font-medium">{title}</p><div className="mt-1 text-blue-800 dark:text-blue-200">{children}</div>{link && <Link href={link.href} className="mt-2 inline-block font-medium underline">{link.label}</Link>}</div></div>;
}

function Field({ label, value, onChange, multiline = false, type = "text", placeholder, helper, required = true }: { label: string; value: string; onChange: (value: string) => void; multiline?: boolean; type?: string; placeholder?: string; helper?: string; required?: boolean }) {
  const classes = "mt-1 block min-h-11 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 placeholder:text-gray-400 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100";
  return <label className="block text-sm font-medium text-gray-700 dark:text-gray-200">{label}{multiline ? <textarea required={required} rows={4} className={classes} placeholder={placeholder} value={value} onChange={(event) => onChange(event.target.value)} /> : <input required={required} type={type} className={classes} placeholder={placeholder} value={value} onChange={(event) => onChange(event.target.value)} />}{helper && <span className="mt-1 block text-xs font-normal text-gray-500 dark:text-gray-400">{helper}</span>}</label>;
}

/** Root-only guided governance editor, exact preview and immutable publication gate. */
export default function GovernanceAdminPage() {
  const { user, isLoading } = useAuth();
  const [form, setForm] = useState<FormState>(empty);
  const [structured, setStructured] = useState<GovernanceStructured>(() => createInitialStructured(runtimeFallback, {}));
  const [status, setStatus] = useState("Loading local governance settings...");
  const [statusKind, setStatusKind] = useState<"info" | "success" | "error">("info");
  const [publishedVersion, setPublishedVersion] = useState<number | null>(null);
  const [checks, setChecks] = useState<PreflightCheck[]>([]);
  const [changes, setChanges] = useState<Array<{ path: string }>>([]);
  const [materialChange, setMaterialChange] = useState(false);
  const [confirmations, setConfirmations] = useState<Record<ConfirmationKey, boolean>>({
    authorised_to_configure: false,
    reviewed_generated_documents: false,
    confirmed_permitted_data_policy: false,
    understands_no_legal_certification: false,
  });

  useEffect(() => {
    if (!user?.is_root_admin) return;
    Promise.all([apiFetch("/api/v1/admin/governance"), apiFetch("/api/v1/admin/settings")]).then(async ([governanceResponse, settingsResponse]) => {
      if (!governanceResponse.ok) throw new Error("Could not load governance settings");
      const data = await governanceResponse.json();
      const runtime = data.runtime_features as RuntimeFeatures;
      const runtimeSettings = settingsResponse.ok ? await settingsResponse.json() as RuntimeSettings : {};
      if (data.draft) {
        const { structured: savedStructured, ...scalar } = data.draft;
        setForm({ ...empty, ...scalar, privacy_contact_phone: scalar.privacy_contact_phone || "", dpo_contact: scalar.dpo_contact || "" });
        setStructured(savedStructured as GovernanceStructured);
      } else {
        setForm((current) => ({ ...current, ...createSuggestedSummaries(runtimeSettings) }));
        setStructured(createInitialStructured(runtime, runtimeSettings));
      }
      setPublishedVersion(data.published_version);
      setChecks(data.preflight.checks || []);
      setStatus(data.preflight.ready ? "The saved draft is ready for exact preview." : "Work through the guided sections, then save the private draft.");
      setStatusKind(data.preflight.ready ? "success" : "info");
    }).catch((error) => { setStatus(error instanceof Error ? error.message : "Could not load governance settings"); setStatusKind("error"); });
  }, [user]);

  const ready = useMemo(() => checks.length > 0 && !checks.some((item) => ["missing", "contradiction", "requires_controller_decision"].includes(item.status)), [checks]);
  const confirmed = Object.values(confirmations).every(Boolean);
  const blockingCount = checks.filter((item) => ["missing", "contradiction", "requires_controller_decision"].includes(item.status)).length;

  if (isLoading) return <main className="flex min-h-screen items-center justify-center bg-gray-50 dark:bg-gray-900"><p>Loading governance...</p></main>;
  if (!user?.is_root_admin) return <main className="flex min-h-screen items-center justify-center bg-gray-50 p-8 dark:bg-gray-900"><Card className="max-w-md p-6"><h1 className="text-xl font-semibold">Root access required</h1><p className="mt-2 text-sm text-gray-500">Only the root administrator can edit and publish controller governance.</p></Card></main>;

  const update = <Key extends keyof FormState>(key: Key, value: FormState[Key]) => setForm((current) => ({ ...current, [key]: value }));

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setStatus("Saving private draft..."); setStatusKind("info");
    const response = await withReauth(() => apiFetch("/api/v1/admin/governance", {
      method: "PUT",
      body: JSON.stringify({ ...form, privacy_contact_phone: form.privacy_contact_phone || null, dpo_contact: form.dpo_contact || null, structured }),
    }));
    const data = await response.json().catch(() => ({}));
    if (response.ok) { setChecks(data.preflight.checks || []); setStatus("Draft saved locally. It remains private until publication."); setStatusKind("success"); }
    else { setStatus(data.detail?.message || data.detail || "Draft validation failed"); setStatusKind("error"); }
  };

  const preview = async () => {
    setStatus("Loading exact preview and policy diff..."); setStatusKind("info");
    const response = await apiFetch("/api/v1/admin/governance/preview");
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { setStatus(data.detail || "Preview failed"); setStatusKind("error"); return; }
    setChecks(data.preflight.checks || []); setChanges(data.diff?.changes || []); setMaterialChange(Boolean(data.diff?.material_change));
    setStatus(data.preflight.ready ? "Exact preview is ready. Review every public page before publishing." : "Preview found blocking items. Resolve them and save again.");
    setStatusKind(data.preflight.ready ? "success" : "error");
  };

  const publish = async () => {
    if (!ready || !confirmed) { setStatus("Resolve every blocking preflight item and complete all four acknowledgements."); setStatusKind("error"); return; }
    setStatus("Publishing immutable policy version..."); setStatusKind("info");
    const response = await withReauth(() => apiFetch("/api/v1/admin/governance/publish", { method: "POST", body: JSON.stringify(confirmations) }));
    const data = await response.json().catch(() => ({}));
    if (response.ok) {
      setPublishedVersion(data.version);
      setConfirmations((current) => Object.fromEntries(Object.keys(current).map((key) => [key, false])) as Record<ConfirmationKey, boolean>);
      setStatus(`Policy version ${data.version} is published with SHA-256 ${data.content_sha256}.`); setStatusKind("success");
    } else { setStatus(data.detail?.code || data.detail || "Publication failed"); setStatusKind("error"); }
  };

  const statusClasses = statusKind === "error" ? "border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200" : statusKind === "success" ? "border-green-300 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-950 dark:text-green-200" : "border-blue-300 bg-blue-50 text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200";

  return <div className="min-h-screen bg-gray-50 text-gray-900 dark:bg-gray-900 dark:text-gray-100">
    <header className="border-b border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800"><div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3"><Logo height={32} href="https://info.mp-opt.net" /><ThemeToggle /></div></header>
    <main className="mx-auto max-w-6xl space-y-6 px-4 py-8">
      <div><Link href="/admin" className="text-sm font-medium text-blue-700 hover:underline dark:text-blue-300">Back to administration</Link><div className="mt-3 flex items-start gap-3"><ShieldCheck size={30} className="mt-1 text-blue-600" aria-hidden="true" /><div><h1 className="text-3xl font-bold">Instance governance</h1><p className="mt-1 max-w-3xl text-sm text-gray-600 dark:text-gray-300">Build this deployment&apos;s controller-reviewed legal centre. Drafts stay local, and nothing is sent to the software maintainer.</p></div></div></div>
      <div role={statusKind === "error" ? "alert" : "status"} className={`rounded-lg border px-4 py-3 text-sm ${statusClasses}`}><strong>{status}</strong><span className="mt-1 block">{publishedVersion ? `Current public version: ${publishedVersion}.` : "No policy is published."} {checks.length > 0 ? `${blockingCount} blocking preflight item(s).` : "Save a draft to run preflight."}</span></div>

      <Guidance title="How to use this page">Work from top to bottom. Blue information boxes explain what belongs in each section. Suggested text begins with <strong>TODO</strong> and cannot pass publication preflight until reviewed and replaced. Deployment settings are linked where they affect the notice.</Guidance>

      <form onSubmit={save} className="space-y-5">
        <Card className="space-y-4 p-5">
          <div><h2 className="text-lg font-semibold">1. Controller and privacy contact</h2><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Identify the organisation or individual that determines why and how this deployment processes data.</p></div>
          <Guidance title="Public controller identity">Use the controller&apos;s legal identity and an address where data-protection requests can actually be received. Do not enter the software maintainer unless the maintainer is genuinely the controller of this deployment.</Guidance>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200">Controller type<select value={form.controller_type} onChange={(event) => update("controller_type", event.target.value as FormState["controller_type"])} className="mt-1 block min-h-11 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-900"><option value="organisation">Organisation</option><option value="individual">Individual</option></select></label>
            <Field label="Legal name" placeholder="Controller's registered or legal name" value={form.controller_legal_name} onChange={(value) => update("controller_legal_name", value)} />
            <Field label="Postal address" placeholder="Complete service address" value={form.controller_postal_address} onChange={(value) => update("controller_postal_address", value)} multiline />
            <Field label="Country code" placeholder="For example CH or DE" helper="Two-letter code chosen by the controller; this does not declare hosting location." value={form.controller_country} onChange={(value) => update("controller_country", value.toUpperCase())} />
            <Field label="Privacy contact email" value={form.privacy_contact_email} onChange={(value) => update("privacy_contact_email", value)} type="email" />
            <Field label="Privacy contact phone" value={form.privacy_contact_phone} onChange={(value) => update("privacy_contact_phone", value)} required={false} />
            <Field label="DPO contact" helper="Leave empty when no DPO is appointed or required." value={form.dpo_contact} onChange={(value) => update("dpo_contact", value)} required={false} />
            <Field label="Default notice locale" helper="For example en or de-CH." value={form.default_locale} onChange={(value) => update("default_locale", value)} />
            <Field label="Supervisory authority" placeholder="Controller-confirmed competent authority" helper="Confirm the competent authority for this controller and deployment; the software does not infer it." value={form.supervisory_authority_name} onChange={(value) => update("supervisory_authority_name", value)} />
            <Field label="Authority URL" placeholder="https://authority.example" value={form.supervisory_authority_url} onChange={(value) => update("supervisory_authority_url", value)} type="url" />
          </div>
        </Card>

        <Card className="space-y-4 p-5">
          <div><h2 className="text-lg font-semibold">2. Controller-supplied public wording</h2><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Turn the structured facts into concise procedures people can understand.</p></div>
          <Guidance title="Suggested drafts are deliberately incomplete">Replace every TODO with reviewed deployment facts. Do not promise deletion from provider systems, external calendars or backups unless the controller controls and verifies that action.</Guidance>
          <Field label="Processors and service providers" helper="Include roles, services, countries, support access, transfers and relevant agreements." value={form.processor_summary} onChange={(value) => update("processor_summary", value)} multiline />
          <Field label="Retention and deletion" helper="Explain live, grace, backup, audit, evidence and external-copy periods in plain language." value={form.retention_summary} onChange={(value) => update("retention_summary", value)} multiline />
          <Field label="Rights procedure" helper="Explain contact, identity verification, handling steps and any applicable limitations." value={form.rights_summary} onChange={(value) => update("rights_summary", value)} multiline />
          <Field label="Terms for authorised use" helper="Include the permitted-data boundary and account responsibilities." value={form.terms_summary} onChange={(value) => update("terms_summary", value)} multiline />
        </Card>

        <GovernanceEditor value={structured} onChange={setStructured} />
        <div className="sticky bottom-3 z-10 flex justify-end"><Button type="submit" size="lg"><FileText size={18} />Save private draft</Button></div>
      </form>

      <Card className="space-y-4 p-5" aria-labelledby="preflight-heading">
        <div><h2 id="preflight-heading" className="text-xl font-semibold">8. Exact preview and technical preflight</h2><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Ready means the draft is internally consistent with known runtime features. External legal and provider evidence remains the controller&apos;s responsibility.</p></div>
        <Guidance title="Review the generated public result">Save first, then generate the exact preview. Open every linked page and compare it with contracts, provider facts and controller decisions before acknowledging publication.</Guidance>
        <Button variant="outline" type="button" onClick={preview}>Generate exact preview and diff</Button>
        <ul className="space-y-2">{checks.map((check) => <li key={check.code} className={`flex items-start gap-2 rounded-lg p-3 text-sm ${check.status === "ready" ? "bg-green-50 text-green-800 dark:bg-green-950/40 dark:text-green-200" : check.status === "externally_unverifiable" ? "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-200" : "bg-red-50 text-red-800 dark:bg-red-950/40 dark:text-red-200"}`}>{check.status === "ready" ? <CheckCircle2 size={17} className="mt-0.5 shrink-0" /> : <AlertTriangle size={17} className="mt-0.5 shrink-0" />}<span><strong>{check.status.replaceAll("_", " ")}</strong>: {check.message}</span></li>)}</ul>
        {changes.length > 0 && <div><h3 className="font-semibold">Draft changes {materialChange ? "(material)" : "(non-material)"}</h3><ul className="mt-2 list-disc pl-6 text-sm">{changes.slice(0, 50).map((change) => <li key={change.path}>{change.path}</li>)}</ul>{changes.length > 50 && <p className="text-sm">Plus {changes.length - 50} additional changed paths.</p>}</div>}
        <nav aria-label="Governance preview pages" className="flex flex-wrap gap-2 text-sm">{[["/privacy", "Privacy"], ["/legal", "Legal"], ["/terms", "Terms"], ["/data-policy", "Permitted data"], ["/retention", "Retention"], ["/rights", "Rights"], ["/processors", "Processors"]].map(([href, label]) => <Link key={href} className="rounded-lg border border-gray-300 bg-white px-3 py-2 font-medium text-blue-700 hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-900 dark:text-blue-300" href={href}>{label}</Link>)}</nav>
      </Card>

      <Card className="space-y-4 p-5" aria-labelledby="publish-heading">
        <div><h2 id="publish-heading" className="text-xl font-semibold">9. Root acknowledgement and publication</h2><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Publication creates an immutable version of the exact reviewed draft and makes its generated pages public.</p></div>
        <Guidance title="Publication is a controller act">A signed record proves the configured publication action and content hash. It is not legal certification and does not prove physical deletion or provider conduct.</Guidance>
        {Object.entries(confirmationLabels).map(([key, label]) => <label key={key} className="flex items-start gap-3 rounded-lg border border-gray-200 p-3 text-sm dark:border-gray-700"><input className="mt-1" type="checkbox" checked={confirmations[key as ConfirmationKey]} onChange={(event) => setConfirmations((current) => ({ ...current, [key]: event.target.checked }))} /><span>{label}</span></label>)}
        <div className="flex flex-wrap gap-3"><Button disabled={!ready || !confirmed} type="button" onClick={publish}>Publish immutable version</Button>{publishedVersion && <a className="inline-flex min-h-11 items-center rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium dark:border-gray-600 dark:bg-gray-900" href={`/api/v1/admin/governance/export/${publishedVersion}`}>Export current evidence JSON</a>}</div>
      </Card>
    </main>
  </div>;
}
