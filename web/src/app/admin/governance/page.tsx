"use client";

import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, Download, FileText, Info, ShieldCheck, Upload } from "lucide-react";

import { GovernanceEditor } from "@/components/GovernanceEditor";
import { AdminNavigation } from "@/components/AdminNavigation";
import { AuthenticatedHeaderActions } from "@/components/AuthenticatedHeaderActions";
import { Logo } from "@/components/Logo";
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
import {
  GOVERNANCE_CONFIGURATION_MAX_BYTES,
  type GovernanceFormState,
  governanceConfigurationFilename,
  parseGovernanceConfiguration,
  serializeGovernanceConfiguration,
} from "@/lib/governanceConfig";
import { withReauth } from "@/lib/reauth";
import { responseMessage } from "@/lib/responseMessage";

type PreflightCheck = {
  code: string;
  status: "ready" | "optional" | "missing" | "contradiction" | "requires_controller_decision" | "externally_unverifiable";
  message: string;
};

export type GovernanceSectionState = "unreviewed" | "ready" | "error";

const runtimeFallback: RuntimeFeatures = { smtp_enabled: false, push_enabled: false, ha_enabled: false, dns_mode: "dns_only" };

const empty: GovernanceFormState = {
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

function RequirementBadge({ requirement }: { requirement: "required" | "conditional" | "optional" }) {
  const label = requirement === "required" ? "Required to publish" : requirement === "conditional" ? "Conditionally required" : "Optional";
  return <span className="ml-2 rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-normal text-gray-600 dark:bg-gray-700 dark:text-gray-300">{label}</span>;
}

function Field({ label, value, onChange, multiline = false, type = "text", placeholder, helper, required = true, requirement }: { label: string; value: string; onChange: (value: string) => void; multiline?: boolean; type?: string; placeholder?: string; helper?: string; required?: boolean; requirement?: "required" | "conditional" | "optional" }) {
  const classes = "mt-1 block min-h-11 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 placeholder:text-gray-400 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100";
  const effectiveRequirement = requirement ?? (required ? "required" : "optional");
  return <label className="block text-sm font-medium text-gray-700 dark:text-gray-200"><span>{label}<RequirementBadge requirement={effectiveRequirement} /></span>{multiline ? <textarea aria-label={label} required={required} rows={4} className={classes} placeholder={placeholder} value={value} onChange={(event) => onChange(event.target.value)} /> : <input aria-label={label} required={required} type={type} className={classes} placeholder={placeholder} value={value} onChange={(event) => onChange(event.target.value)} />}{helper && <span className="mt-1 block text-xs font-normal text-gray-500 dark:text-gray-400">{helper}</span>}</label>;
}

const sectionBorder: Record<GovernanceSectionState, string> = {
  unreviewed: "border-gray-200 dark:border-gray-700",
  ready: "border-green-400 ring-1 ring-green-200/70 dark:border-green-400 dark:ring-green-400/35",
  error: "border-red-400 dark:border-red-700",
};

/** Root-only guided governance editor, exact preview and immutable publication gate. */
export function GovernanceWorkspace({ setupMode = false, onPublished }: { setupMode?: boolean; onPublished?: () => void }) {
  const { user, isLoading } = useAuth();
  const importInput = useRef<HTMLInputElement>(null);
  const [form, setForm] = useState<GovernanceFormState>(empty);
  const [structured, setStructured] = useState<GovernanceStructured>(() => createInitialStructured(runtimeFallback, {}));
  const [status, setStatus] = useState("Loading local governance settings...");
  const [statusKind, setStatusKind] = useState<"info" | "success" | "error">("info");
  const [publishedVersion, setPublishedVersion] = useState<number | null>(null);
  const [checks, setChecks] = useState<PreflightCheck[]>([]);
  const [changes, setChanges] = useState<Array<{ path: string }>>([]);
  const [materialChange, setMaterialChange] = useState(false);
  const [runtimeFeatureChanges, setRuntimeFeatureChanges] = useState<string[]>([]);
  const [previewReady, setPreviewReady] = useState(false);
  const [confirmations, setConfirmations] = useState<Record<ConfirmationKey, boolean>>({
    authorised_to_configure: false,
    reviewed_generated_documents: false,
    confirmed_permitted_data_policy: false,
    understands_no_legal_certification: false,
  });
  const governancePath = setupMode ? "/api/v1/setup/governance" : "/api/v1/admin/governance";

  useEffect(() => {
    if (!user?.is_root_admin) return;
    (async () => {
      const governanceResponse = await apiFetch(governancePath);
      if (!governanceResponse.ok) throw new Error("Could not load governance settings");
      const data = await governanceResponse.json();
      const runtime = data.runtime_features as RuntimeFeatures;
      const runtimeSettings = (data.runtime_settings || {}) as RuntimeSettings;
      if (data.draft) {
        const { structured: savedStructured, ...scalar } = data.draft;
        setForm({ ...empty, ...scalar, privacy_contact_phone: scalar.privacy_contact_phone || "", dpo_contact: scalar.dpo_contact || "" });
        setStructured(savedStructured as GovernanceStructured);
        const declared = (savedStructured as GovernanceStructured).optional_features;
        const changed = [
          declared.smtp_enabled !== runtime.smtp_enabled ? "SMTP" : null,
          declared.ha_enabled !== runtime.ha_enabled ? "high availability" : null,
          declared.push_enabled !== runtime.push_enabled ? "push delivery" : null,
        ].filter((item): item is string => Boolean(item));
        setRuntimeFeatureChanges(data.published_version ? changed : []);
      } else {
        setForm((current) => ({ ...current, ...createSuggestedSummaries(runtimeSettings) }));
        setStructured(createInitialStructured(runtime, runtimeSettings));
      }
      setPublishedVersion(data.published_version);
      setChecks(data.preflight.checks || []);
      setStatus(data.preflight.ready ? "The saved draft is ready for exact preview." : "Work through the guided sections, then save the private draft.");
      setStatusKind(data.preflight.ready ? "success" : "info");
    })().catch((error) => { setStatus(error instanceof Error ? error.message : "Could not load governance settings"); setStatusKind("error"); });
  }, [user, governancePath, setupMode]);

  const ready = useMemo(() => checks.length > 0 && !checks.some((item) => ["missing", "contradiction", "requires_controller_decision"].includes(item.status)), [checks]);
  const confirmed = Object.values(confirmations).every(Boolean);
  const blockingCount = checks.filter((item) => ["missing", "contradiction", "requires_controller_decision"].includes(item.status)).length;
  const checkState = (...codes: string[]): GovernanceSectionState => {
    const relevant = checks.filter((item) => codes.includes(item.code));
    if (relevant.length === 0) return "unreviewed";
    if (relevant.some((item) => ["missing", "contradiction", "requires_controller_decision"].includes(item.status))) return "error";
    return relevant.every((item) => item.status === "optional") || relevant.some((item) => item.status === "externally_unverifiable") ? "unreviewed" : "ready";
  };
  const sectionStates: Record<number, GovernanceSectionState> = {
    1: checkState("controller_identity", "controller_address", "privacy_contact", "supervisory_authority"),
    2: checkState("processor_summary", "retention_summary", "rights_summary", "instance_terms"),
    3: checkState("instance_name", "jurisdiction_scope", "incident_contact", "hosting_countries"),
    4: checkState("processing_purposes", "controller_basis_decisions"),
    5: checkState("data_categories"),
    6: checkState("processor_register", "enabled_feature_processors"),
    7: checkState("retention_configuration", "smtp_enabled", "push_enabled", "ha_enabled", "dns_mode"),
  };
  const nonReadyChecks = checks.filter((item) => ["missing", "contradiction", "requires_controller_decision", "externally_unverifiable"].includes(item.status));
  const readyCount = checks.filter((item) => item.status === "ready").length;

  if (isLoading) return <main className="flex min-h-screen items-center justify-center bg-gray-50 dark:bg-gray-900"><p>Loading governance...</p></main>;
  if (!user?.is_root_admin) return <main className="flex min-h-screen items-center justify-center bg-gray-50 p-8 dark:bg-gray-900"><Card className="max-w-md p-6"><h1 className="text-xl font-semibold">Root access required</h1><p className="mt-2 text-sm text-gray-500">Only the root administrator can edit and publish controller governance.</p></Card></main>;

  const update = <Key extends keyof GovernanceFormState>(key: Key, value: GovernanceFormState[Key]) => setForm((current) => ({ ...current, [key]: value }));

  const exportConfiguration = () => {
    const contents = serializeGovernanceConfiguration(form, structured);
    const url = URL.createObjectURL(new Blob([contents], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = governanceConfigurationFilename(structured.instance_name);
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    setStatus("Downloaded the current editor entries as a governance JSON draft. No key, signature, publication approval or passkey material is included.");
    setStatusKind("success");
  };

  const importConfiguration = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > GOVERNANCE_CONFIGURATION_MAX_BYTES) {
      setStatus("The governance configuration is larger than 1 MiB and was not opened.");
      setStatusKind("error");
      return;
    }
    try {
      const imported = parseGovernanceConfiguration(await file.text(), structured);
      setForm(imported.form);
      setStructured(imported.structured);
      setChecks([]);
      setChanges([]);
      setMaterialChange(false);
      setPreviewReady(false);
      setConfirmations((current) => Object.fromEntries(Object.keys(current).map((key) => [key, false])) as Record<ConfirmationKey, boolean>);
      setStatus("Configuration imported into an unsaved draft. Review every section, then select Save private draft. SMTP, push, HA and DNS runtime facts were kept from this deployment.");
      setStatusKind("success");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "The governance configuration could not be imported.");
      setStatusKind("error");
    }
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setStatus("Saving private draft..."); setStatusKind("info");
    const saveRequest = () => apiFetch(governancePath, {
      method: "PUT",
      body: JSON.stringify({ ...form, privacy_contact_phone: form.privacy_contact_phone || null, dpo_contact: form.dpo_contact || null, structured }),
    });
    const response = setupMode ? await saveRequest() : await withReauth(saveRequest);
    const data = await response.json().catch(() => ({}));
    if (response.ok) {
      setChecks(data.preflight.checks || []);
      if (data.draft?.structured) setStructured(data.draft.structured as GovernanceStructured);
      setRuntimeFeatureChanges([]);
      setPreviewReady(false);
      const enforced = data.runtime_enforced_changes?.length ? ` ${data.runtime_enforced_changes.length} runtime-managed value(s) were restored from Server settings.` : "";
      setStatus(`Draft saved locally. It remains private until publication.${enforced}`); setStatusKind("success");
    }
    else { setStatus(responseMessage(data, "Draft validation failed")); setStatusKind("error"); }
  };

  const preview = async () => {
    setStatus("Loading exact preview and policy diff..."); setStatusKind("info");
    const response = await apiFetch(`${governancePath}/preview`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { setStatus(responseMessage(data, "Preview failed")); setStatusKind("error"); return; }
    setChecks(data.preflight.checks || []); setChanges(data.diff?.changes || []); setMaterialChange(Boolean(data.diff?.material_change)); setPreviewReady(Boolean(data.preflight.ready));
    setStatus(data.preflight.ready ? "Exact preview is ready. Review every public page before publishing." : "Preview found blocking items. Resolve them and save again.");
    setStatusKind(data.preflight.ready ? "success" : "error");
  };

  const publish = async () => {
    if (!ready || !confirmed) { setStatus("Resolve every blocking preflight item and complete all four acknowledgements."); setStatusKind("error"); return; }
    setStatus("Publishing immutable policy version..."); setStatusKind("info");
    const response = await withReauth(() => apiFetch(`${governancePath}/publish`, { method: "POST", body: JSON.stringify(confirmations) }));
    const data = await response.json().catch(() => ({}));
    if (response.ok) {
      setPublishedVersion(data.version);
      onPublished?.();
      setConfirmations((current) => Object.fromEntries(Object.keys(current).map((key) => [key, false])) as Record<ConfirmationKey, boolean>);
      setStatus(`Policy version ${data.version} is published with SHA-256 ${data.content_sha256}.`); setStatusKind("success");
    } else { setStatus(responseMessage(data, "Publication failed")); setStatusKind("error"); }
  };

  const statusClasses = statusKind === "error" ? "border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200" : statusKind === "success" ? "border-green-300 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-950 dark:text-green-200" : "border-blue-300 bg-blue-50 text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200";

  return <div className={setupMode ? "space-y-6 text-gray-900 dark:text-gray-100" : "min-h-screen bg-gray-50 text-gray-900 dark:bg-gray-900 dark:text-gray-100"}>
    {!setupMode && <header className="sticky top-0 z-20 border-b border-gray-200 bg-white/95 backdrop-blur dark:border-gray-700 dark:bg-gray-800/95"><div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6"><div className="flex items-center gap-3"><Logo height={32} href="https://info.mp-opt.net" /><span className="hidden text-sm font-semibold text-gray-500 dark:text-gray-400 sm:inline">Root administration</span></div><AuthenticatedHeaderActions /></div></header>}
    <div className={setupMode ? "space-y-6" : "mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:py-8"}>
      <div className={setupMode ? "space-y-6" : "grid items-start gap-6 lg:grid-cols-[15rem_minmax(0,1fr)] xl:gap-8"}>
      {!setupMode && <AdminNavigation active="policies" isRootAdmin isIssuerOnly={false} canManagePublicLinks />}
      <div className="min-w-0 space-y-6">
      <div><div className="flex items-start gap-3"><ShieldCheck size={26} className="mt-0.5 text-blue-600" aria-hidden="true" /><div><h1 className="text-2xl font-semibold tracking-tight">Policies &amp; notices</h1><p className="mt-1 max-w-3xl text-sm text-gray-600 dark:text-gray-300">Build this deployment&apos;s controller-reviewed legal centre. Drafts stay local, and nothing is sent to the software maintainer.</p></div></div></div>
      <div role={statusKind === "error" ? "alert" : "status"} className={`rounded-lg border px-4 py-3 text-sm ${statusClasses}`}><strong>{status}</strong><span className="mt-1 block">{publishedVersion ? `Current public version: ${publishedVersion}.` : "No policy is published."} {checks.length > 0 ? `${blockingCount} blocking preflight item(s).` : "Save a draft to run preflight."}</span></div>

      <Guidance title="How to use this page">Work from top to bottom. <strong>Required to publish</strong> means Masterplan needs the entry to produce a coherent notice; it is not a universal legal conclusion. Conditional duties depend on the controller and deployment. Optional fields may be left empty. Deployment settings are linked where they affect the notice.</Guidance>

      {publishedVersion && <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-100"><strong>Published notices remain immutable.</strong> Runtime-backed changes update this private draft only. Review the exact diff and publish a new version before relying on changed public wording.</div>}
      {runtimeFeatureChanges.length > 0 && <div role="alert" className="rounded-lg border border-amber-400 bg-amber-50 px-4 py-3 text-sm text-amber-950 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-100"><strong>Governance update required.</strong><p className="mt-1">The running deployment changed: {runtimeFeatureChanges.join(", ")}. Save this private draft to apply the authoritative runtime facts, review the exact diff, then publish a new immutable policy version. The currently published notice has not been changed silently.</p></div>}

      <Card className="space-y-4 p-5" aria-labelledby="configuration-file-heading">
        <div><h2 id="configuration-file-heading" className="text-lg font-semibold">Governance configuration file</h2><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Reuse controller-reviewed entries between test runs or similar deployments without publishing them automatically.</p></div>
        <Guidance title="Import is reviewable and never publishes">JSON import replaces only the entries currently shown in this editor. It remains unsaved until you select <strong>Save private draft</strong>. Deployment-derived SMTP, push, HA and DNS facts remain local. Files can contain controller contact information, so store them appropriately.</Guidance>
        <div className="flex flex-wrap gap-3">
          <Button type="button" variant="outline" onClick={exportConfiguration}><Download size={18} />Export current entries</Button>
          <Button type="button" variant="outline" onClick={() => importInput.current?.click()}><Upload size={18} />Import configuration</Button>
          <input ref={importInput} className="sr-only" type="file" accept="application/json,.json" aria-label="Choose governance configuration JSON" onChange={importConfiguration} />
        </div>
        <p className="text-xs text-gray-500 dark:text-gray-400">Format: versioned JSON, maximum 1 MiB. Excludes private keys, signatures, publication history, passkeys and publication acknowledgements.</p>
      </Card>

      <form onSubmit={save} className="space-y-5">
        <Card className={`space-y-4 border-2 p-5 ${sectionBorder[sectionStates[1]]}`} data-validation-state={sectionStates[1]}>
          <div><h2 className="text-lg font-semibold">1. Controller and privacy contact</h2><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Identify the organisation or individual that determines why and how this deployment processes data.</p></div>
          <Guidance title="Public controller identity">Use the controller&apos;s legal identity and a privacy email where requests can actually be received. A service or correspondence address is optional here; do not enter a private home address merely to satisfy this form. Do not enter the software maintainer unless the maintainer is genuinely the controller.</Guidance>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200"><span>Controller type<RequirementBadge requirement="required" /></span><select value={form.controller_type} onChange={(event) => update("controller_type", event.target.value as GovernanceFormState["controller_type"])} className="mt-1 block min-h-11 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-900"><option value="organisation">Organisation</option><option value="individual">Individual</option></select></label>
            <Field label="Legal name" placeholder="Controller's registered or legal name" value={form.controller_legal_name} onChange={(value) => update("controller_legal_name", value)} />
            <Field label="Service or correspondence address" placeholder="Optional public service address" helper="Optional in Masterplan. Add it only when the controller chooses or is required to publish one; avoid a private home address when another reachable address exists." value={form.controller_postal_address} onChange={(value) => update("controller_postal_address", value)} multiline required={false} />
            <Field label="Controller country code" placeholder="For example CH or DE" helper="Optional controller fact; it does not declare hosting location or determine applicable law." value={form.controller_country} onChange={(value) => update("controller_country", value.toUpperCase())} required={false} />
            <Field label="Privacy contact email" value={form.privacy_contact_email} onChange={(value) => update("privacy_contact_email", value)} type="email" />
            <Field label="Privacy contact phone" value={form.privacy_contact_phone} onChange={(value) => update("privacy_contact_phone", value)} required={false} />
            <Field label="DPO contact" helper="Add only when a DPO is appointed or applicable law requires one." value={form.dpo_contact} onChange={(value) => update("dpo_contact", value)} required={false} requirement="conditional" />
            <Field label="Default notice locale" helper="For example en or de-CH." value={form.default_locale} onChange={(value) => update("default_locale", value)} />
            <Field label="Named supervisory authority" placeholder="Optional controller-confirmed authority" helper="Optional. If omitted, the notice still states the general complaint right without guessing the competent authority." value={form.supervisory_authority_name} onChange={(value) => update("supervisory_authority_name", value)} required={false} />
            <Field label="Authority URL" placeholder="https://authority.example" helper="Optional; use only with a controller-confirmed authority." value={form.supervisory_authority_url} onChange={(value) => update("supervisory_authority_url", value)} type="url" required={false} />
          </div>
        </Card>

        <Card className={`space-y-4 border-2 p-5 ${sectionBorder[sectionStates[2]]}`} data-validation-state={sectionStates[2]}>
          <div><h2 className="text-lg font-semibold">2. Optional controller-supplied wording</h2><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Add deployment-specific explanations only where the structured facts and generated baseline do not say enough.</p></div>
          <Guidance title="Supplement, do not duplicate">These fields are optional. Structured provider and retention entries are rendered automatically, and the generated notice includes baseline rights and authorised-use wording. Never promise deletion from provider systems, external calendars or backups unless the controller controls and verifies it.</Guidance>
          <Field label="Processors and service providers" helper="Optional supplementary wording; provider entries below remain the authoritative structured facts." value={form.processor_summary} onChange={(value) => update("processor_summary", value)} multiline required={false} />
          <Field label="Retention and deletion" helper="Optional when live, backup and receipt periods are entered below; otherwise use this field to state clear retention criteria." value={form.retention_summary} onChange={(value) => update("retention_summary", value)} multiline required={false} />
          <Field label="Rights procedure" helper="Optional deployment-specific handling details; the generated notice already states the general rights and contact route." value={form.rights_summary} onChange={(value) => update("rights_summary", value)} multiline required={false} />
          <Field label="Terms for authorised use" helper="Optional additions; the generated notice already states authorised use and the permitted-data boundary." value={form.terms_summary} onChange={(value) => update("terms_summary", value)} multiline required={false} />
        </Card>

        <GovernanceEditor value={structured} onChange={setStructured} sectionStates={sectionStates} />
        <div className="sticky bottom-3 z-10 flex justify-end"><Button type="submit" size="lg"><FileText size={18} />Save private draft</Button></div>
      </form>

      <Card className="space-y-4 p-5" aria-labelledby="preflight-heading">
        <div><h2 id="preflight-heading" className="text-xl font-semibold">8. Exact preview and technical preflight</h2><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Ready means the draft is internally consistent with known runtime features. External legal and provider evidence remains the controller&apos;s responsibility.</p></div>
        <Guidance title="Review the generated public result">Save first, then generate the exact preview. Open every linked page and compare it with contracts, provider facts and controller decisions before acknowledging publication.</Guidance>
        <Button variant="outline" type="button" onClick={preview}>Generate exact preview and diff</Button>
        {checks.length > 0 && <div className="flex flex-wrap gap-2 text-sm"><span className="inline-flex items-center gap-1 rounded-full bg-green-50 px-3 py-1 text-green-800 dark:bg-green-950/40 dark:text-green-200"><CheckCircle2 size={15} />{readyCount} checks passed</span><span className={`inline-flex items-center gap-1 rounded-full px-3 py-1 ${blockingCount ? "bg-red-50 text-red-800 dark:bg-red-950/40 dark:text-red-200" : "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-200"}`}><AlertTriangle size={15} />{blockingCount} blocking</span></div>}
        {nonReadyChecks.length > 0 && <ul className="space-y-2">{nonReadyChecks.map((check) => <li key={check.code} className={`flex items-start gap-2 rounded-lg p-3 text-sm ${check.status === "externally_unverifiable" ? "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-200" : "bg-red-50 text-red-800 dark:bg-red-950/40 dark:text-red-200"}`}><AlertTriangle size={17} className="mt-0.5 shrink-0" /><span><strong>{check.status.replaceAll("_", " ")}</strong>: {check.message}</span></li>)}</ul>}
        {changes.length > 0 && <div><h3 className="font-semibold">Draft changes {materialChange ? "(material)" : "(non-material)"}</h3><ul className="mt-2 list-disc pl-6 text-sm">{changes.slice(0, 50).map((change) => <li key={change.path}>{change.path}</li>)}</ul>{changes.length > 50 && <p className="text-sm">Plus {changes.length - 50} additional changed paths.</p>}</div>}
        {previewReady && <nav aria-label="Governance draft preview pages" className="flex flex-wrap gap-2 text-sm">{[["privacy", "Privacy"], ["legal", "Legal"], ["terms", "Terms"], ["data-policy", "Permitted data"], ["retention", "Retention"], ["rights", "Rights"], ["processors", "Processors"]].map(([section, label]) => <a key={section} className="rounded-lg border border-gray-300 bg-white px-3 py-2 font-medium text-blue-700 hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-900 dark:text-blue-300" href={`${governancePath}/preview/${section}.html`} target="_blank" rel="noreferrer">{label} draft preview</a>)}</nav>}
      </Card>

      <Card className="space-y-4 p-5" aria-labelledby="publish-heading">
        <div><h2 id="publish-heading" className="text-xl font-semibold">9. Root acknowledgement and publication</h2><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Publication creates an immutable version of the exact reviewed draft and makes its generated pages public.</p></div>
        <Guidance title="Publication is a controller act" link={setupMode ? undefined : { href: "/admin/governance/trust", label: "Open Trust & keys" }}>A signed record proves the configured publication action and content hash. It is not legal certification and does not prove physical deletion or provider conduct. Controller identity must be complete before this step.</Guidance>
        {Object.entries(confirmationLabels).map(([key, label]) => <label key={key} className="flex items-start gap-3 rounded-lg border border-gray-200 p-3 text-sm dark:border-gray-700"><input className="mt-1" type="checkbox" checked={confirmations[key as ConfirmationKey]} onChange={(event) => setConfirmations((current) => ({ ...current, [key]: event.target.checked }))} /><span>{label}</span></label>)}
        <div className="flex flex-wrap gap-3"><Button disabled={!ready || !confirmed} type="button" onClick={publish}>Publish immutable version</Button>{publishedVersion && <a className="inline-flex min-h-11 items-center rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium dark:border-gray-600 dark:bg-gray-900" href={`/api/v1/admin/governance/export/${publishedVersion}`}>Export current evidence JSON</a>}</div>
      </Card>
      </div>
      </div>
    </div>
  </div>;
}

export default function GovernanceAdminPage() {
  return <GovernanceWorkspace />;
}
