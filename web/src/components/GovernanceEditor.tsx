"use client";

import { cloneElement, isValidElement, useEffect, useState } from "react";
import Link from "next/link";
import { Info, Plus, Settings, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import {
  codesFromText,
  codesToText,
  GovernanceStructured,
  ProcessorEntry,
  PURPOSE_LABELS,
  PurposeCode,
} from "@/lib/governanceDraft";

const fieldClass = "mt-1 block min-h-11 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100";
type Requirement = "required" | "conditional" | "optional";
type SectionState = "unreviewed" | "ready" | "error";
const sectionBorder: Record<SectionState, string> = {
  unreviewed: "border-gray-200 dark:border-gray-700",
  ready: "border-green-400 ring-1 ring-green-200/70 dark:border-green-400 dark:ring-green-400/35",
  error: "border-red-400 dark:border-red-700",
};

function listFromText(value: string): string[] {
  return [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))];
}

function optional(value: string): string | null {
  return value.trim() || null;
}

function Guidance({ title, children, link }: { title: string; children: React.ReactNode; link?: { href: string; label: string } }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900 dark:border-blue-800 dark:bg-blue-950/50 dark:text-blue-100">
      <Info size={18} className="mt-0.5 shrink-0" aria-hidden="true" />
      <div><p className="font-medium">{title}</p><div className="mt-1 text-blue-800 dark:text-blue-200">{children}</div>{link && <Link className="mt-2 inline-flex items-center gap-1 font-medium underline" href={link.href}><Settings size={14} />{link.label}</Link>}</div>
    </div>
  );
}

function RequirementBadge({ requirement }: { requirement: Requirement }) {
  const label = requirement === "required" ? "Required to publish" : requirement === "conditional" ? "Conditionally required" : "Optional";
  return <span className="ml-2 rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-normal text-gray-600 dark:bg-gray-700 dark:text-gray-300">{label}</span>;
}

function Label({ text, help, children, requirement = "required", managed = false }: { text: string; help?: string; children: React.ReactNode; requirement?: Requirement; managed?: boolean }) {
  const control = isValidElement<Record<string, unknown>>(children) ? cloneElement(children, { "aria-label": text }) : children;
  return <label className="block text-sm font-medium text-gray-700 dark:text-gray-200"><span>{text}<RequirementBadge requirement={requirement} />{managed && <span className="ml-2 rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-normal text-blue-700 dark:bg-blue-950 dark:text-blue-200">Server-managed</span>}</span>{control}{help && <span className="mt-1 block text-xs font-normal text-gray-500 dark:text-gray-400">{help}</span>}</label>;
}

function NumberField({ label, value, onChange, unit, managed = false, requirement = "required" }: { label: string; value: number | null; onChange: (value: number | null) => void; unit: string; managed?: boolean; requirement?: Requirement }) {
  const help = managed ? `Authoritative ${unit} value from Administration → Security settings.` : `Controller-reviewed ${unit}; needed when no equivalent retention criterion is stated.`;
  return <Label text={label} help={help} managed={managed} requirement={requirement}><input className={`${fieldClass} disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-600 dark:disabled:bg-gray-800`} type="number" min={1} value={value ?? ""} disabled={managed} onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)} /></Label>;
}

/** Guided editor for controller facts; advanced JSON remains an explicit escape hatch. */
export function GovernanceEditor({ value, onChange, sectionStates = {} }: { value: GovernanceStructured; onChange: (value: GovernanceStructured) => void; sectionStates?: Record<number, SectionState> }) {
  const [advancedText, setAdvancedText] = useState(JSON.stringify(value, null, 2));
  const [advancedError, setAdvancedError] = useState("");

  useEffect(() => { setAdvancedText(JSON.stringify(value, null, 2)); }, [value]);

  const update = <K extends keyof GovernanceStructured>(key: K, next: GovernanceStructured[K]) => onChange({ ...value, [key]: next });
  const updateRetention = <K extends keyof GovernanceStructured["retention"]>(key: K, next: GovernanceStructured["retention"][K]) => update("retention", { ...value.retention, [key]: next });
  const updateFeature = <K extends keyof GovernanceStructured["optional_features"]>(key: K, next: GovernanceStructured["optional_features"][K]) => update("optional_features", { ...value.optional_features, [key]: next });

  const updatePurpose = (code: PurposeCode, patch: Partial<GovernanceStructured["processing_purposes"][number]>) => update(
    "processing_purposes",
    value.processing_purposes.map((item) => item.purpose_code === code ? { ...item, ...patch } : item),
  );

  const updateCategory = (index: number, patch: Partial<GovernanceStructured["data_categories"][number]>) => update(
    "data_categories",
    value.data_categories.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item),
  );

  const updateProcessor = (index: number, patch: Partial<ProcessorEntry>) => update(
    "processors",
    value.processors.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item),
  );

  const addCategory = () => update("data_categories", [...value.data_categories, {
    category_code: `category_${value.data_categories.length + 1}`,
    display_name: "TODO: Name this ordinary operational data category",
    enabled: true,
    required_or_optional: "optional",
    visibility: "organiser",
    source: "TODO: Identify the source",
    purpose_codes: ["event_scheduling"],
    retention_policy_code: "instance_default",
    sensitive_data_supported: false,
  }]);

  const addProcessor = () => update("processors", [...value.processors, {
    provider_code: `provider_${value.processors.length + 1}`,
    display_name: "TODO: Provider legal or trading name",
    service: "TODO: Service supplied to this deployment",
    role: "processor",
    purpose_codes: ["event_scheduling"],
    data_categories: value.data_categories.slice(0, 1).map((item) => item.category_code),
    hosting_countries: [],
    support_access_countries: [],
    dpa_status: "unknown",
    dpa_version: null,
    subprocessor_url: null,
    transfer_mechanism: null,
    public_notice_summary: "TODO: Explain the provider's role, access and relevant transfer safeguards.",
    internal_notes_reference: null,
    review_due_at: null,
    enabled: true,
  }]);

  const applyAdvanced = () => {
    try {
      const parsed = JSON.parse(advancedText) as GovernanceStructured;
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("The JSON root must be an object.");
      onChange(parsed);
      setAdvancedError("");
    } catch (cause) {
      setAdvancedError(cause instanceof Error ? cause.message : "The JSON is invalid.");
    }
  };

  return <div className="space-y-5">
    <Card className={`space-y-4 border-2 p-5 ${sectionBorder[sectionStates[3] || "unreviewed"]}`} data-validation-state={sectionStates[3] || "unreviewed"}>
      <div><h2 className="text-lg font-semibold">3. Deployment and jurisdiction</h2><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Record the public identity and scope of this individual deployment.</p></div>
      <Guidance title="Record only assessed facts">Public naming, hosting countries and jurisdiction notes are optional or conditional. Do not copy another deployment or infer jurisdiction and international transfers from an IP address.</Guidance>
      <div className="grid gap-4 md:grid-cols-2">
        <Label text="Public instance name" requirement="optional"><input className={fieldClass} value={value.instance_name} onChange={(event) => update("instance_name", event.target.value)} /></Label>
        <Label text="Supported notice locales" help="Comma-separated locale codes, for example en, de-CH."><input className={fieldClass} value={value.supported_locales.join(", ")} onChange={(event) => update("supported_locales", listFromText(event.target.value))} /></Label>
        <Label text="Hosting countries" requirement="conditional" help="Add confirmed two-letter codes when hosting or transfer disclosures apply; do not infer them from an IP address."><input className={fieldClass} placeholder="CH, DE" value={codesToText(value.hosting_countries)} onChange={(event) => update("hosting_countries", codesFromText(event.target.value))} /></Label>
        <Label text="Incident contact email" requirement="optional"><input className={fieldClass} type="email" value={value.incident_contact_email ?? ""} onChange={(event) => update("incident_contact_email", optional(event.target.value))} /></Label>
        <Label text="Rights-request URL" requirement="optional"><input className={fieldClass} type="url" value={value.rights_request_url ?? ""} onChange={(event) => update("rights_request_url", optional(event.target.value))} /></Label>
        <Label text="DPO name or role (if appointed)" requirement="conditional"><input className={fieldClass} value={value.dpo_name_or_role ?? ""} onChange={(event) => update("dpo_name_or_role", optional(event.target.value))} /></Label>
      </div>
      <Label text="Jurisdiction scope" requirement="optional" help="Optional explanation of the controller's own assessment; Masterplan does not infer applicable law."><textarea rows={4} className={fieldClass} value={value.jurisdiction_scope} onChange={(event) => update("jurisdiction_scope", event.target.value)} /></Label>
      <div className="grid gap-4 md:grid-cols-2"><Label text="EU representative" requirement="conditional"><textarea rows={3} className={fieldClass} value={value.eu_representative ?? ""} onChange={(event) => update("eu_representative", optional(event.target.value))} /></Label><Label text="Swiss representative" requirement="conditional"><textarea rows={3} className={fieldClass} value={value.swiss_representative ?? ""} onChange={(event) => update("swiss_representative", optional(event.target.value))} /></Label></div>
    </Card>

    <Card className={`space-y-4 border-2 p-5 ${sectionBorder[sectionStates[4] || "unreviewed"]}`} data-validation-state={sectionStates[4] || "unreviewed"}>
      <div><h2 className="text-lg font-semibold">4. Processing purposes and legal decisions</h2><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Enable only purposes used by this deployment and record the controller&apos;s own GDPR basis or Swiss justification.</p></div>
      <Guidance title="The software cannot choose a legal basis">Descriptions below are product-oriented suggestions. The controller must decide whether each purpose is used and enter its own basis or justification. Legitimate-interest wording must describe the controller&apos;s actual balancing assessment.</Guidance>
      <div className="space-y-3">{value.processing_purposes.map((purpose) => <section key={purpose.purpose_code} className="rounded-lg border border-gray-200 p-4 dark:border-gray-700">
        <label className="flex items-start gap-3"><input className="mt-1" type="checkbox" checked={purpose.enabled} onChange={(event) => updatePurpose(purpose.purpose_code, { enabled: event.target.checked })} /><span><strong>{PURPOSE_LABELS[purpose.purpose_code]}</strong><span className="mt-1 block text-xs text-gray-500">{purpose.purpose_code}</span></span></label>
        {purpose.enabled && <div className="mt-4 grid gap-3 md:grid-cols-2">
          <Label text="Public description"><textarea rows={3} className={fieldClass} value={purpose.description} onChange={(event) => updatePurpose(purpose.purpose_code, { description: event.target.value })} /></Label>
          <Label text="Required or optional"><select className={fieldClass} value={purpose.required_or_optional} onChange={(event) => updatePurpose(purpose.purpose_code, { required_or_optional: event.target.value as "required" | "optional" })}><option value="required">Required</option><option value="optional">Optional</option></select></Label>
          <Label text="GDPR legal basis (controller decision)" requirement="conditional"><input className={fieldClass} placeholder="For example: Article and controller rationale" value={purpose.gdpr_legal_basis ?? ""} onChange={(event) => updatePurpose(purpose.purpose_code, { gdpr_legal_basis: optional(event.target.value) })} /></Label>
          <Label text="Swiss justification or basis (controller decision)" requirement="conditional"><input className={fieldClass} value={purpose.swiss_justification_or_basis ?? ""} onChange={(event) => updatePurpose(purpose.purpose_code, { swiss_justification_or_basis: optional(event.target.value) })} /></Label>
          <Label text="Legitimate-interest summary" requirement="conditional"><textarea rows={3} className={fieldClass} value={purpose.legitimate_interest_summary ?? ""} onChange={(event) => updatePurpose(purpose.purpose_code, { legitimate_interest_summary: optional(event.target.value) })} /></Label>
          <Label text="Effect of declining or withdrawing" requirement="conditional"><textarea rows={3} className={fieldClass} value={purpose.withdrawal_effect ?? ""} onChange={(event) => updatePurpose(purpose.purpose_code, { withdrawal_effect: optional(event.target.value) })} /></Label>
        </div>}
      </section>)}</div>
    </Card>

    <Card className={`space-y-4 border-2 p-5 ${sectionBorder[sectionStates[5] || "unreviewed"]}`} data-validation-state={sectionStates[5] || "unreviewed"}>
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">5. Data categories</h2><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Describe ordinary operational data actually enabled for this deployment.</p></div><Button type="button" size="sm" variant="outline" onClick={addCategory}><Plus size={15} />Add category</Button></div>
      <Guidance title="Special-category data remains unsupported">Do not add health, dietary, safeguarding, political, religious, disciplinary or unrelated private information. These entries document the permitted operational boundary; they do not enable new product fields.</Guidance>
      {value.data_categories.map((category, index) => <section key={`${category.category_code}-${index}`} className="space-y-3 rounded-lg border border-gray-200 p-4 dark:border-gray-700">
        <div className="flex items-start justify-between gap-3"><label className="flex items-center gap-2 text-sm font-medium"><input type="checkbox" checked={category.enabled} onChange={(event) => updateCategory(index, { enabled: event.target.checked })} />Enabled</label><Button type="button" size="sm" variant="ghost" aria-label={`Remove ${category.display_name}`} onClick={() => update("data_categories", value.data_categories.filter((_, itemIndex) => itemIndex !== index))}><Trash2 size={15} />Remove</Button></div>
        <div className="grid gap-3 md:grid-cols-2">
          <Label text="Stable code"><input className={fieldClass} value={category.category_code} onChange={(event) => updateCategory(index, { category_code: event.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })} /></Label>
          <Label text="Public name"><input className={fieldClass} value={category.display_name} onChange={(event) => updateCategory(index, { display_name: event.target.value })} /></Label>
          <Label text="Source"><input className={fieldClass} value={category.source} onChange={(event) => updateCategory(index, { source: event.target.value })} /></Label>
          <Label text="Visibility"><select className={fieldClass} value={category.visibility} onChange={(event) => updateCategory(index, { visibility: event.target.value as typeof category.visibility })}><option value="root">Root only</option><option value="organiser">Organiser</option><option value="participant">Participant</option><option value="public">Public</option></select></Label>
          <Label text="Required or optional"><select className={fieldClass} value={category.required_or_optional} onChange={(event) => updateCategory(index, { required_or_optional: event.target.value as typeof category.required_or_optional })}><option value="required">Required</option><option value="optional">Optional</option></select></Label>
          <Label text="Purpose codes" help="Comma-separated codes shown in the purpose section."><input className={fieldClass} value={category.purpose_codes.join(", ")} onChange={(event) => updateCategory(index, { purpose_codes: listFromText(event.target.value) as PurposeCode[] })} /></Label>
        </div>
      </section>)}
    </Card>

    <Card className={`space-y-4 border-2 p-5 ${sectionBorder[sectionStates[6] || "unreviewed"]}`} data-validation-state={sectionStates[6] || "unreviewed"}>
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">6. Providers, countries and transfers</h2><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Add every provider used by an enabled feature, including hosting and SMTP.</p></div><Button type="button" size="sm" variant="outline" onClick={addProcessor}><Plus size={15} />Add provider</Button></div>
      <Guidance title="Record deployment facts, not software guesses">Confirm provider role, countries, support access, agreement status and any transfer mechanism from the provider&apos;s current documents. Hosting/CDN/network logging remains an operator/provider fact.</Guidance>
      {value.processors.length === 0 && <p className="rounded-lg border border-dashed p-5 text-center text-sm text-gray-500">No providers recorded. Add the VPS host and each enabled email, push, backup or support provider.</p>}
      {value.processors.map((processor, index) => <section key={`${processor.provider_code}-${index}`} className="space-y-3 rounded-lg border border-gray-200 p-4 dark:border-gray-700">
        <div className="flex items-start justify-between gap-3"><label className="flex items-center gap-2 text-sm font-medium"><input type="checkbox" checked={processor.enabled} onChange={(event) => updateProcessor(index, { enabled: event.target.checked })} />Enabled provider</label><Button type="button" size="sm" variant="ghost" aria-label={`Remove ${processor.display_name}`} onClick={() => update("processors", value.processors.filter((_, itemIndex) => itemIndex !== index))}><Trash2 size={15} />Remove</Button></div>
        <div className="grid gap-3 md:grid-cols-2">
          <Label text="Stable provider code"><input className={fieldClass} value={processor.provider_code} onChange={(event) => updateProcessor(index, { provider_code: event.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })} /></Label>
          <Label text="Provider name"><input className={fieldClass} value={processor.display_name} onChange={(event) => updateProcessor(index, { display_name: event.target.value })} /></Label>
          <Label text="Service"><input className={fieldClass} value={processor.service} onChange={(event) => updateProcessor(index, { service: event.target.value })} /></Label>
          <Label text="Role"><select className={fieldClass} value={processor.role} onChange={(event) => updateProcessor(index, { role: event.target.value as ProcessorEntry["role"] })}><option value="processor">Processor</option><option value="infrastructure_provider">Infrastructure provider</option><option value="independent_controller">Independent controller</option></select></Label>
          <Label text="Hosting countries" help="Two-letter codes confirmed by the provider/controller."><input className={fieldClass} value={codesToText(processor.hosting_countries)} onChange={(event) => updateProcessor(index, { hosting_countries: codesFromText(event.target.value) })} /></Label>
          <Label text="Support-access countries" requirement="conditional"><input className={fieldClass} value={codesToText(processor.support_access_countries)} onChange={(event) => updateProcessor(index, { support_access_countries: codesFromText(event.target.value) })} /></Label>
          <Label text="Agreement status"><select className={fieldClass} value={processor.dpa_status} onChange={(event) => updateProcessor(index, { dpa_status: event.target.value as ProcessorEntry["dpa_status"] })}><option value="unknown">Unknown</option><option value="pending">Pending</option><option value="accepted">Accepted</option><option value="not_required">Not required</option></select></Label>
          <Label text="Agreement version/reference" requirement="conditional"><input className={fieldClass} value={processor.dpa_version ?? ""} onChange={(event) => updateProcessor(index, { dpa_version: optional(event.target.value) })} /></Label>
          <Label text="Purpose codes"><input className={fieldClass} value={processor.purpose_codes.join(", ")} onChange={(event) => updateProcessor(index, { purpose_codes: listFromText(event.target.value) as PurposeCode[] })} /></Label>
          <Label text="Data-category codes"><input className={fieldClass} value={processor.data_categories.join(", ")} onChange={(event) => updateProcessor(index, { data_categories: listFromText(event.target.value) })} /></Label>
          <Label text="Transfer mechanism" requirement="conditional"><textarea rows={3} className={fieldClass} value={processor.transfer_mechanism ?? ""} onChange={(event) => updateProcessor(index, { transfer_mechanism: optional(event.target.value) })} /></Label>
          <Label text="Public notice summary"><textarea rows={3} className={fieldClass} value={processor.public_notice_summary} onChange={(event) => updateProcessor(index, { public_notice_summary: event.target.value })} /></Label>
        </div>
      </section>)}
    </Card>

    <Card className={`space-y-4 border-2 p-5 ${sectionBorder[sectionStates[7] || "unreviewed"]}`} data-validation-state={sectionStates[7] || "unreviewed"}>
      <div><h2 className="text-lg font-semibold">7. Retention and enabled features</h2><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Reconcile the public notice with controller policy and the effective Server settings.</p></div>
      <Guidance title="Some values are technical settings" link={{ href: "/admin?tab=security", label: "Open Administration → Security settings" }}>Event purge grace, audit retention and offline access are prefilled from the effective Server configuration. Live records, backups, evidence receipts and legal holds remain controller decisions and are never inferred.</Guidance>
      <div className="grid gap-4 md:grid-cols-3">
        <NumberField label="Live record retention" unit="days" requirement="conditional" value={value.retention.live_retention_days} onChange={(next) => updateRetention("live_retention_days", next)} />
        <NumberField label="Event purge grace" unit="days" managed value={value.retention.event_grace_days} onChange={(next) => updateRetention("event_grace_days", next)} />
        <NumberField label="Backup retention" unit="days" requirement="conditional" value={value.retention.backup_retention_days} onChange={(next) => updateRetention("backup_retention_days", next)} />
        <NumberField label="Audit retention" unit="days" managed value={value.retention.audit_retention_days} onChange={(next) => updateRetention("audit_retention_days", next)} />
        <NumberField label="Evidence-receipt retention" unit="days" requirement="conditional" value={value.retention.receipt_retention_days} onChange={(next) => updateRetention("receipt_retention_days", next)} />
        <NumberField label="Browser cache expiry" unit="hours" managed value={value.retention.browser_cache_expiry_hours} onChange={(next) => updateRetention("browser_cache_expiry_hours", next)} />
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <Label text="Automatic purge" requirement="conditional"><select className={fieldClass} value={value.retention.automatic_purge_enabled === null ? "" : String(value.retention.automatic_purge_enabled)} onChange={(event) => updateRetention("automatic_purge_enabled", event.target.value === "" ? null : event.target.value === "true")}><option value="">Not declared</option><option value="true">Enabled</option><option value="false">Disabled</option></select></Label>
        <Label text="Legal-hold support" requirement="optional"><select className={fieldClass} value={value.retention.legal_hold_supported === null ? "" : String(value.retention.legal_hold_supported)} onChange={(event) => updateRetention("legal_hold_supported", event.target.value === "" ? null : event.target.value === "true")}><option value="">Not declared</option><option value="true">Supported and governed</option><option value="false">Not supported</option></select></Label>
      </div>
      <div className="grid gap-3 rounded-lg bg-gray-50 p-4 text-sm dark:bg-gray-900 md:grid-cols-2">
        <p>SMTP: <strong>{value.optional_features.smtp_enabled ? "enabled" : "disabled"}</strong></p><p>Push: <strong>{value.optional_features.push_enabled ? "enabled" : "disabled"}</strong></p><p>High availability: <strong>{value.optional_features.ha_enabled ? "enabled" : "disabled"}</strong></p><p>Routing: <strong>DNS-only direct TLS</strong></p>
        {value.optional_features.smtp_enabled && <Label text="SMTP provider code" requirement="conditional" help="Required while SMTP is enabled; must match an enabled provider above."><input className={fieldClass} value={value.optional_features.smtp_provider_code ?? ""} onChange={(event) => updateFeature("smtp_provider_code", optional(event.target.value))} /></Label>}
        {value.optional_features.push_enabled && <Label text="Push provider codes" requirement="conditional"><input className={fieldClass} value={value.optional_features.push_provider_codes.join(", ")} onChange={(event) => updateFeature("push_provider_codes", listFromText(event.target.value))} /></Label>}
      </div>
    </Card>

    <details className="rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-800">
      <summary className="cursor-pointer font-semibold">Advanced: validated governance JSON</summary>
      <Guidance title="Advanced editing" >Use this only for fields not exposed above. Applying JSON does not publish anything; the same backend schema and preflight checks still apply.</Guidance>
      <textarea aria-label="Structured governance configuration" spellCheck={false} rows={28} className={`${fieldClass} mt-4 font-mono text-xs`} value={advancedText} onChange={(event) => setAdvancedText(event.target.value)} />
      {advancedError && <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-300">{advancedError}</p>}
      <Button type="button" className="mt-3" variant="outline" onClick={applyAdvanced}>Apply advanced JSON to this draft</Button>
    </details>
  </div>;
}
