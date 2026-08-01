"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { withReauth } from "@/lib/reauth";
import { useAuth } from "@/contexts/AuthContext";

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

type RuntimeFeatures = {
  smtp_enabled: boolean;
  push_enabled: boolean;
  ha_enabled: boolean;
  dns_mode: "dns_only";
};

type PreflightCheck = {
  code: string;
  status: "ready" | "missing" | "contradiction" | "requires_controller_decision" | "externally_unverifiable";
  message: string;
};

const empty: FormState = {
  controller_type: "organisation", controller_legal_name: "", controller_postal_address: "",
  controller_country: "CH", privacy_contact_email: "", privacy_contact_phone: "", dpo_contact: "",
  supervisory_authority_name: "Federal Data Protection and Information Commissioner",
  supervisory_authority_url: "https://www.edoeb.admin.ch/", default_locale: "en",
  processor_summary: "", retention_summary: "", rights_summary: "", terms_summary: "",
};

function initialStructured(runtime: RuntimeFeatures) {
  return {
    instance_name: "",
    dpo_name_or_role: null,
    eu_representative: null,
    swiss_representative: null,
    supported_locales: ["en"],
    jurisdiction_scope: "",
    processing_purposes: [{
      purpose_code: "event_scheduling",
      enabled: true,
      description: "Create and publish operational event schedules.",
      gdpr_legal_basis: null,
      swiss_justification_or_basis: null,
      legitimate_interest_summary: null,
      required_or_optional: "required",
      withdrawal_effect: null,
    }],
    data_categories: [{
      category_code: "operational_identity",
      display_name: "Names and operational roles",
      enabled: true,
      required_or_optional: "required",
      visibility: "participant",
      source: "Controller and participant",
      purpose_codes: ["event_scheduling"],
      retention_policy_code: "instance_default",
      sensitive_data_supported: false,
    }],
    processors: [],
    hosting_countries: [],
    retention: {
      policy_code: "instance_default",
      live_retention_days: null,
      event_grace_days: null,
      backup_retention_days: null,
      audit_retention_days: null,
      receipt_retention_days: null,
      browser_cache_expiry_hours: null,
      automatic_purge_enabled: null,
      legal_hold_supported: null,
    },
    optional_features: {
      ...runtime,
      smtp_provider_code: null,
      push_provider_codes: [],
      offline_schedule_enabled: true,
      public_schedule_enabled: true,
      external_support_enabled: false,
      backup_storage_mode: "manual_portable",
    },
    rights_request_url: null,
    incident_contact_email: null,
  };
}

const confirmationLabels = {
  authorised_to_configure: "I am authorised to configure this instance for the identified controller.",
  reviewed_generated_documents: "I reviewed the exact generated documents and their public consequences.",
  confirmed_permitted_data_policy: "I confirm the permitted-data boundary for this instance.",
  understands_no_legal_certification: "I understand that publication is not legal certification or legal advice.",
};

type ConfirmationKey = keyof typeof confirmationLabels;

/** Root-only governance editor, preview, evidence export and publication gate. */
export default function GovernanceAdminPage() {
  const { user, isLoading } = useAuth();
  const [form, setForm] = useState<FormState>(empty);
  const [structuredText, setStructuredText] = useState("{}");
  const [status, setStatus] = useState("Loading local governance settings...");
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
    apiFetch("/api/v1/admin/governance").then(async (response) => {
      if (!response.ok) throw new Error("Could not load governance settings");
      const data = await response.json();
      const runtime = data.runtime_features as RuntimeFeatures;
      if (data.draft) {
        const { structured, ...scalar } = data.draft;
        setForm({
          ...empty, ...scalar,
          privacy_contact_phone: scalar.privacy_contact_phone || "",
          dpo_contact: scalar.dpo_contact || "",
        });
        setStructuredText(JSON.stringify(structured, null, 2));
      } else {
        setStructuredText(JSON.stringify(initialStructured(runtime), null, 2));
      }
      setPublishedVersion(data.published_version);
      setChecks(data.preflight.checks || []);
      setStatus(data.preflight.ready ? "The saved draft is ready for preview." : "Complete the missing controller decisions and resolve contradictions.");
    }).catch((error) => setStatus(error instanceof Error ? error.message : "Could not load governance settings"));
  }, [user]);

  const ready = useMemo(
    () => checks.length > 0 && !checks.some((item) => ["missing", "contradiction", "requires_controller_decision"].includes(item.status)),
    [checks],
  );
  const confirmed = Object.values(confirmations).every(Boolean);

  if (isLoading) return <main className="p-8">Loading...</main>;
  if (!user?.is_root_admin) return <main className="p-8"><h1 className="text-2xl font-bold">Root access required</h1></main>;

  const update = <Key extends keyof FormState,>(key: Key, value: FormState[Key]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const parseStructured = () => {
    const parsed = JSON.parse(structuredText);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("Structured deployment facts must be a JSON object.");
    return parsed;
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    let structured: object;
    try { structured = parseStructured(); }
    catch (error) { setStatus(error instanceof Error ? error.message : "Structured deployment facts are invalid."); return; }
    setStatus("Saving private draft...");
    const response = await withReauth(() => apiFetch("/api/v1/admin/governance", {
      method: "PUT",
      body: JSON.stringify({
        ...form,
        privacy_contact_phone: form.privacy_contact_phone || null,
        dpo_contact: form.dpo_contact || null,
        structured,
      }),
    }));
    const data = await response.json().catch(() => ({}));
    if (response.ok) {
      setChecks(data.preflight.checks || []);
      setStatus("Draft saved locally. It remains private until publication.");
    } else {
      setStatus(data.detail?.message || data.detail || "Draft validation failed");
    }
  };

  const preview = async () => {
    setStatus("Loading exact preview and policy diff...");
    const response = await apiFetch("/api/v1/admin/governance/preview");
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { setStatus(data.detail || "Preview failed"); return; }
    setChecks(data.preflight.checks || []);
    setChanges(data.diff?.changes || []);
    setMaterialChange(Boolean(data.diff?.material_change));
    setStatus(data.preflight.ready ? "Exact preview is ready. Review every public page before publishing." : "Preview found blocking items. Resolve them and save again.");
  };

  const publish = async () => {
    if (!ready || !confirmed) { setStatus("Resolve every blocking preflight item and complete all four acknowledgements."); return; }
    setStatus("Publishing immutable policy version...");
    const response = await withReauth(() => apiFetch("/api/v1/admin/governance/publish", {
      method: "POST", body: JSON.stringify(confirmations),
    }));
    const data = await response.json().catch(() => ({}));
    if (response.ok) {
      setPublishedVersion(data.version);
      setConfirmations((current) => Object.fromEntries(Object.keys(current).map((key) => [key, false])) as Record<ConfirmationKey, boolean>);
      setStatus(`Policy version ${data.version} is published with SHA-256 ${data.content_sha256}.`);
    } else setStatus(data.detail?.code || data.detail || "Publication failed");
  };

  return <main className="min-h-screen bg-gray-50 px-6 py-10 dark:bg-gray-900"><div className="mx-auto max-w-5xl space-y-6 text-gray-800 dark:text-gray-200">
    <header><Link href="/admin" className="text-blue-600 dark:text-blue-400">Back to administration</Link><h1 className="mt-3 text-3xl font-bold">Instance governance</h1><p>Configure deployment facts locally. Nothing is sent to the software maintainer, and the application does not decide whether a legal basis is correct.</p></header>
    <div role="status" className="rounded border border-blue-300 bg-blue-50 p-3 dark:bg-blue-950">{status} {publishedVersion ? `Current public version: ${publishedVersion}.` : "No policy is published."}</div>

    <form onSubmit={save} className="space-y-5">
      <fieldset className="grid gap-4 rounded border p-4 md:grid-cols-2"><legend className="px-2 font-semibold">1. Controller and privacy contact</legend>
        <label>Controller type<select value={form.controller_type} onChange={(e) => update("controller_type", e.target.value as FormState["controller_type"])} className="field"><option value="organisation">Organisation</option><option value="individual">Individual</option></select></label>
        <Field label="Legal name" value={form.controller_legal_name} onChange={(v) => update("controller_legal_name", v)} />
        <Field label="Postal address" value={form.controller_postal_address} onChange={(v) => update("controller_postal_address", v)} multiline />
        <Field label="Country code" value={form.controller_country} onChange={(v) => update("controller_country", v)} />
        <Field label="Privacy contact email" value={form.privacy_contact_email} onChange={(v) => update("privacy_contact_email", v)} type="email" />
        <Field label="Privacy contact phone (optional)" value={form.privacy_contact_phone} onChange={(v) => update("privacy_contact_phone", v)} />
        <Field label="DPO contact (optional)" value={form.dpo_contact} onChange={(v) => update("dpo_contact", v)} />
        <Field label="Authority name" value={form.supervisory_authority_name} onChange={(v) => update("supervisory_authority_name", v)} />
        <Field label="Authority URL" value={form.supervisory_authority_url} onChange={(v) => update("supervisory_authority_url", v)} type="url" />
      </fieldset>

      <fieldset className="space-y-4 rounded border p-4"><legend className="px-2 font-semibold">2. Controller-supplied public wording</legend>
        <p className="text-sm">These fields are marked as controller-supplied wording in the published evidence. Use reviewed text, not placeholders.</p>
        <Field label="Processors and service providers" value={form.processor_summary} onChange={(v) => update("processor_summary", v)} multiline />
        <Field label="Retention and deletion" value={form.retention_summary} onChange={(v) => update("retention_summary", v)} multiline />
        <Field label="Rights procedure" value={form.rights_summary} onChange={(v) => update("rights_summary", v)} multiline />
        <Field label="Terms for authorised use" value={form.terms_summary} onChange={(v) => update("terms_summary", v)} multiline />
      </fieldset>

      <fieldset className="space-y-3 rounded border p-4"><legend className="px-2 font-semibold">3. Structured deployment facts</legend>
        <p className="text-sm">Record purposes and controller-selected bases, data categories, providers and countries, retention periods, enabled features, rights and incident contacts. Internal provider references remain root-only and are removed from public output.</p>
        <textarea aria-label="Structured governance configuration" spellCheck={false} rows={30} className="w-full rounded border bg-white p-3 font-mono text-xs text-gray-900 dark:border-gray-600 dark:bg-gray-950 dark:text-gray-100" value={structuredText} onChange={(event) => setStructuredText(event.target.value)} />
      </fieldset>

      <button className="rounded bg-blue-600 px-4 py-2 text-white" type="submit">Save private draft</button>
    </form>

    <section className="space-y-4 rounded border p-4" aria-labelledby="preflight-heading">
      <div><h2 id="preflight-heading" className="text-xl font-semibold">4. Preview and production preflight</h2><p className="text-sm">Ready means a technical consistency check passed. External legal and provider evidence remains the controller&apos;s responsibility.</p></div>
      <button className="rounded border px-4 py-2" type="button" onClick={preview}>Generate exact preview and diff</button>
      <ul className="space-y-2">{checks.map((check) => <li key={check.code} className="rounded bg-gray-100 p-2 text-sm dark:bg-gray-800"><strong>{check.status.replaceAll("_", " ")}</strong>: {check.message}</li>)}</ul>
      {changes.length > 0 && <div><h3 className="font-semibold">Draft changes {materialChange ? "(material)" : "(non-material)"}</h3><ul className="list-disc pl-6 text-sm">{changes.slice(0, 50).map((change) => <li key={change.path}>{change.path}</li>)}</ul>{changes.length > 50 && <p className="text-sm">Plus {changes.length - 50} additional changed paths.</p>}</div>}
      <nav className="flex flex-wrap gap-3 text-sm"><Link href="/privacy">Privacy preview</Link><Link href="/legal">Legal preview</Link><Link href="/terms">Terms preview</Link><Link href="/data-policy">Permitted data</Link><Link href="/retention">Retention</Link><Link href="/rights">Rights</Link><Link href="/processors">Processors</Link></nav>
    </section>

    <section className="space-y-4 rounded border p-4" aria-labelledby="publish-heading">
      <h2 id="publish-heading" className="text-xl font-semibold">5. Root acknowledgement and publication</h2>
      {Object.entries(confirmationLabels).map(([key, label]) => <label key={key} className="flex items-start gap-2"><input type="checkbox" checked={confirmations[key as ConfirmationKey]} onChange={(event) => setConfirmations((current) => ({ ...current, [key]: event.target.checked }))} /><span>{label}</span></label>)}
      <div className="flex flex-wrap gap-3"><button disabled={!ready || !confirmed} className="rounded bg-emerald-700 px-4 py-2 text-white disabled:cursor-not-allowed disabled:opacity-50" type="button" onClick={publish}>Publish immutable version</button>{publishedVersion && <a className="rounded border px-4 py-2" href={`/api/v1/admin/governance/export/${publishedVersion}`}>Export current evidence JSON</a>}</div>
    </section>
  </div></main>;
}

function Field({ label, value, onChange, multiline = false, type = "text" }: { label: string; value: string; onChange: (value: string) => void; multiline?: boolean; type?: string }) {
  const classes = "mt-1 block w-full rounded border border-gray-300 bg-white p-2 text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100";
  return <label className="block">{label}{multiline ? <textarea required={!label.includes("optional")} rows={4} className={classes} value={value} onChange={(e) => onChange(e.target.value)} /> : <input required={!label.includes("optional")} type={type} className={classes} value={value} onChange={(e) => onChange(e.target.value)} />}</label>;
}
