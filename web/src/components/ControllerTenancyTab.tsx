"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Building2, CheckCircle2, RefreshCw, Server, ShieldAlert } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { withReauth } from "@/lib/reauth";
import { responseMessage } from "@/lib/responseMessage";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";

type ControllerSummary = {
  public_id: string;
  trust_entity_id: string;
  code: string;
  display_name: string;
  status: "draft" | "active" | "suspended" | "retired";
  has_governance_profile: boolean;
  latest_governance_version: number | null;
  latest_governance_sha256: string | null;
  event_count: number;
};

type RootEvent = {
  id: number;
  name: string;
  controller_public_id: string;
  controller_name: string;
};

type TenancyStatus = {
  configured_mode: "single-controller" | "hosted-multi-controller";
  ready: boolean;
  controller_count: number;
  event_count: number;
  non_root_account_count: number;
  blockers: Array<{ code: string; [key: string]: unknown }>;
};

type OperatorForm = {
  operator_type: "organisation" | "individual";
  operator_legal_name: string;
  operator_postal_address: string;
  operator_country: string;
  privacy_contact_email: string;
  service_description: string;
  security_summary: string;
  subprocessors: string;
  hosting_regions: string;
  fixed_retention_days: number;
  dpa_url: string;
  subprocessor_schedule_url: string;
};

type ControllerForm = {
  controller_type: "organisation" | "individual";
  legal_name: string;
  postal_address: string;
  country: string;
  privacy_contact_email: string;
  dpo_contact: string;
  supervisory_authority_name: string;
  supervisory_authority_url: string;
  default_locale: string;
  processor_summary: string;
  rights_summary: string;
  terms_summary: string;
  governance: string;
};

const FEATURES = [
  ["desktop_publishing", "Desktop publishing"],
  ["offline_schedule", "Offline phone schedule"],
  ["public_schedule_links", "Public schedule links"],
  ["push_notifications", "Push notifications"],
  ["smtp_activation", "SMTP account activation"],
] as const;

const inputClass = "mt-1 min-h-11 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-900";

function parseJson(text: string, fallback: object): object {
  const parsed = JSON.parse(text || JSON.stringify(fallback));
  if (parsed === null || typeof parsed !== "object") throw new Error("Expected JSON data");
  return parsed;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block text-sm font-medium text-gray-700 dark:text-gray-200">{label}{children}</label>;
}

export function ControllerTenancyTab({ events }: { events: RootEvent[] }) {
  const [status, setStatus] = useState<TenancyStatus | null>(null);
  const [controllers, setControllers] = useState<ControllerSummary[]>([]);
  const [operatorPolicy, setOperatorPolicy] = useState<{ version: number; sha256: string } | null>(null);
  const [operator, setOperator] = useState<OperatorForm>({
    operator_type: "organisation", operator_legal_name: "", operator_postal_address: "",
    operator_country: "CH", privacy_contact_email: "", service_description: "",
    security_summary: "", subprocessors: "[]", hosting_regions: "[\"CH\"]",
    fixed_retention_days: 90, dpa_url: "", subprocessor_schedule_url: "",
  });
  const [newController, setNewController] = useState({ code: "", display_name: "" });
  const [selectedController, setSelectedController] = useState("");
  const [controller, setController] = useState<ControllerForm>({
    controller_type: "organisation", legal_name: "", postal_address: "", country: "CH",
    privacy_contact_email: "", dpo_contact: "", supervisory_authority_name: "",
    supervisory_authority_url: "", default_locale: "en", processor_summary: "",
    rights_summary: "", terms_summary: "", governance: "{}",
  });
  const [selectedEvent, setSelectedEvent] = useState<number | "">("");
  const [eventNotice, setEventNotice] = useState("");
  const [eventFeatures, setEventFeatures] = useState<string[]>([]);
  const [contactRouting, setContactRouting] = useState("{}");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const selectedControllerSummary = useMemo(
    () => controllers.find((item) => item.public_id === selectedController) || null,
    [controllers, selectedController],
  );
  const selectedEventRow = useMemo(
    () => events.find((item) => item.id === selectedEvent) || null,
    [events, selectedEvent],
  );

  const load = useCallback(async () => {
    setError("");
    const [statusResponse, controllerResponse, operatorResponse, publicOperatorResponse] = await Promise.all([
      apiFetch("/api/v1/admin/tenancy"), apiFetch("/api/v1/admin/controllers"),
      apiFetch("/api/v1/admin/operator"), apiFetch("/api/v1/legal/operator"),
    ]);
    if (statusResponse.ok) setStatus(await statusResponse.json());
    if (controllerResponse.ok) {
      const rows = await controllerResponse.json() as ControllerSummary[];
      setControllers(rows);
      setSelectedController((current) => current || rows[0]?.public_id || "");
    }
    if (operatorResponse.ok) {
      const value = await operatorResponse.json();
      setOperator({
        operator_type: value.operator_type,
        operator_legal_name: value.operator_legal_name,
        operator_postal_address: value.operator_postal_address,
        operator_country: value.operator_country,
        privacy_contact_email: value.privacy_contact_email,
        service_description: value.service_description,
        security_summary: value.security_summary,
        subprocessors: JSON.stringify(value.subprocessors || [], null, 2),
        hosting_regions: JSON.stringify(value.hosting_regions || [], null, 2),
        fixed_retention_days: value.fixed_retention_days,
        dpa_url: value.dpa_url || "",
        subprocessor_schedule_url: value.subprocessor_schedule_url || "",
      });
    }
    if (publicOperatorResponse.ok) {
      const value = await publicOperatorResponse.json();
      setOperatorPolicy({ version: value.version, sha256: value.sha256 });
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (!selectedController) return;
    apiFetch(`/api/v1/admin/controllers/${selectedController}/governance`).then(async (response) => {
      if (!response.ok) return;
      const value = await response.json();
      setController({
        controller_type: value.controller_type, legal_name: value.legal_name,
        postal_address: value.postal_address, country: value.country,
        privacy_contact_email: value.privacy_contact_email, dpo_contact: value.dpo_contact || "",
        supervisory_authority_name: value.supervisory_authority_name,
        supervisory_authority_url: value.supervisory_authority_url,
        default_locale: value.default_locale, processor_summary: value.processor_summary,
        rights_summary: value.rights_summary, terms_summary: value.terms_summary,
        governance: JSON.stringify(value.governance || {}, null, 2),
      });
    }).catch(() => undefined);
  }, [selectedController]);

  useEffect(() => {
    if (!selectedEvent) return;
    apiFetch(`/api/v1/admin/events/${selectedEvent}/governance-configuration`).then(async (response) => {
      if (!response.ok) {
        setEventNotice(""); setEventFeatures([]); setContactRouting("{}");
        return;
      }
      const value = await response.json();
      setEventNotice(value.event_notice || "");
      setEventFeatures(value.enabled_optional_features || []);
      setContactRouting(JSON.stringify(value.contact_routing || {}, null, 2));
    }).catch(() => undefined);
  }, [selectedEvent]);

  const run = async (key: string, action: () => Promise<Response>, success: string) => {
    setBusy(key); setError(""); setNotice("");
    try {
      const response = await action();
      const value = await response.json().catch(() => null);
      if (!response.ok) throw new Error(responseMessage(value, `${success} failed (${response.status}).`));
      setNotice(success); await load(); return value;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The operation failed safely.");
      return null;
    } finally { setBusy(""); }
  };

  const saveOperator = () => run("operator", () => withReauth(() => apiFetch("/api/v1/admin/operator", {
    method: "PUT", body: JSON.stringify({ ...operator,
      subprocessors: parseJson(operator.subprocessors, []),
      hosting_regions: parseJson(operator.hosting_regions, []),
      dpa_url: operator.dpa_url || null,
      subprocessor_schedule_url: operator.subprocessor_schedule_url || null,
    }),
  })), "Hosting-operator profile saved");

  const saveController = () => {
    if (!selectedController || !operatorPolicy) {
      setError("Publish the hosting-operator policy before saving controller governance."); return;
    }
    void run("controller", () => withReauth(() => apiFetch(`/api/v1/admin/controllers/${selectedController}/governance`, {
      method: "PUT", body: JSON.stringify({ ...controller,
        dpo_contact: controller.dpo_contact || null,
        governance: parseJson(controller.governance, {}),
        accepted_operator_policy_version: operatorPolicy.version,
        accepted_operator_policy_sha256: operatorPolicy.sha256,
      }),
    })), "Controller governance saved");
  };

  const saveEvent = () => {
    if (!selectedEventRow || !operatorPolicy) { setError("Select a configured event."); return; }
    const controllerSummary = controllers.find((item) => item.public_id === selectedEventRow.controller_public_id);
    if (!controllerSummary?.latest_governance_version) { setError("Publish this controller's governance first."); return; }
    void run("event", () => withReauth(() => apiFetch(`/api/v1/admin/events/${selectedEventRow.id}/governance-configuration`, {
      method: "PUT", body: JSON.stringify({
        event_notice: eventNotice || null, enabled_optional_features: eventFeatures,
        contact_routing: parseJson(contactRouting, {}),
        operator_policy_version: operatorPolicy.version,
        controller_policy_version: controllerSummary.latest_governance_version,
      }),
    })), "Event governance and features saved");
  };

  return <div className="space-y-5">
    <div><h2 className="text-lg font-semibold">Controllers & hosting</h2><p className="mt-1 text-sm text-gray-600 dark:text-gray-300">Root remains the trusted technical administrator. Legal controller identity comes from each event&apos;s controller, while the hosting operator is disclosed separately as the technically privileged processor or service provider.</p></div>
    {error && <div role="alert" className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-100">{error}</div>}
    {notice && <div role="status" className="rounded-lg border border-green-300 bg-green-50 p-3 text-sm text-green-900 dark:border-green-800 dark:bg-green-950/40 dark:text-green-100">{notice}</div>}

    <Card className="p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div className="flex gap-3"><Server className="mt-0.5 text-blue-600"/><div><h3 className="font-semibold">Tenancy mode</h3><p className="text-sm text-gray-600 dark:text-gray-300">{status?.configured_mode || "Loading…"} · {status?.controller_count || 0} controllers · {status?.event_count || 0} events</p></div></div><Button variant="outline" onClick={() => void load()}><RefreshCw size={15}/>Refresh</Button></div>
      {status && <div className={`mt-4 rounded-lg border p-3 text-sm ${status.ready ? "border-green-300 bg-green-50 dark:border-green-800 dark:bg-green-950/30" : "border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30"}`}>{status.ready ? <p className="flex gap-2"><CheckCircle2 size={18}/>Hosted-mode preflight is ready.</p> : <><p className="flex gap-2 font-medium"><ShieldAlert size={18}/>Resolve every blocker before hosted mode can be enabled.</p><ul className="mt-2 list-disc pl-5">{status.blockers.map((item, index) => <li key={`${item.code}-${index}`}>{item.code}</li>)}</ul></>}</div>}
      {status?.configured_mode !== "hosted-multi-controller" && <Button className="mt-4" disabled={!status?.ready || !!busy} onClick={() => void run("mode", () => withReauth(() => apiFetch("/api/v1/admin/tenancy/mode", { method: "PUT", body: JSON.stringify({ mode: "hosted-multi-controller" }) })), "Hosted multi-controller mode enabled")}>Enable hosted mode</Button>}
    </Card>

    <Card className="space-y-4 p-5"><div><h3 className="font-semibold">Hosting operator</h3><p className="text-sm text-gray-600 dark:text-gray-300">Infrastructure identity, technical access, subprocessors, security and fixed retention.</p></div><div className="grid gap-3 md:grid-cols-2">
      <Field label="Legal name"><input className={inputClass} value={operator.operator_legal_name} onChange={(e) => setOperator({...operator, operator_legal_name:e.target.value})}/></Field>
      <Field label="Type"><select className={inputClass} value={operator.operator_type} onChange={(e) => setOperator({...operator, operator_type:e.target.value as OperatorForm["operator_type"]})}><option value="organisation">Organisation</option><option value="individual">Individual</option></select></Field>
      <Field label="Postal address"><textarea className={inputClass} value={operator.operator_postal_address} onChange={(e) => setOperator({...operator, operator_postal_address:e.target.value})}/></Field>
      <Field label="Country"><input className={inputClass} maxLength={2} value={operator.operator_country} onChange={(e) => setOperator({...operator, operator_country:e.target.value.toUpperCase()})}/></Field>
      <Field label="Privacy contact"><input className={inputClass} type="email" value={operator.privacy_contact_email} onChange={(e) => setOperator({...operator, privacy_contact_email:e.target.value})}/></Field>
      <Field label="Fixed retention days"><input className={inputClass} type="number" min={1} max={3650} value={operator.fixed_retention_days} onChange={(e) => setOperator({...operator, fixed_retention_days:Number(e.target.value)})}/></Field>
    </div><Field label="Service description"><textarea rows={3} className={inputClass} value={operator.service_description} onChange={(e) => setOperator({...operator, service_description:e.target.value})}/></Field><Field label="Security and privileged-access summary"><textarea rows={3} className={inputClass} value={operator.security_summary} onChange={(e) => setOperator({...operator, security_summary:e.target.value})}/></Field><div className="grid gap-3 md:grid-cols-2"><Field label="Subprocessors (JSON array)"><textarea rows={5} className={`${inputClass} font-mono`} value={operator.subprocessors} onChange={(e) => setOperator({...operator, subprocessors:e.target.value})}/></Field><Field label="Hosting regions (JSON array)"><textarea rows={5} className={`${inputClass} font-mono`} value={operator.hosting_regions} onChange={(e) => setOperator({...operator, hosting_regions:e.target.value})}/></Field></div><div className="flex flex-wrap gap-2"><Button disabled={!!busy} onClick={() => void saveOperator()}>Save operator</Button><Button variant="outline" disabled={!!busy} onClick={() => void run("operator-publish", () => withReauth(() => apiFetch("/api/v1/admin/operator/publications", {method:"POST", body:"{}"})), "Operator policy published")}>Publish immutable operator policy</Button>{operatorPolicy && <span className="self-center text-xs font-mono text-gray-500">v{operatorPolicy.version} · {operatorPolicy.sha256.slice(0,12)}…</span>}</div></Card>

    <Card className="space-y-4 p-5"><div className="flex gap-3"><Building2 className="text-blue-600"/><div><h3 className="font-semibold">Legal controllers</h3><p className="text-sm text-gray-600 dark:text-gray-300">One controller may own several events. Non-root roles remain event-scoped.</p></div></div><div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]"><input className={inputClass} placeholder="controller-code" value={newController.code} onChange={(e)=>setNewController({...newController,code:e.target.value})}/><input className={inputClass} placeholder="Controller display name" value={newController.display_name} onChange={(e)=>setNewController({...newController,display_name:e.target.value})}/><Button className="self-end" disabled={!!busy || !newController.code || !newController.display_name} onClick={() => void run("controller-create", () => withReauth(() => apiFetch("/api/v1/admin/controllers", {method:"POST",body:JSON.stringify(newController)})), "Controller created").then((value) => { if(value?.public_id) setSelectedController(value.public_id); setNewController({code:"",display_name:""}); })}>Create</Button></div>
      <Field label="Controller"><select className={inputClass} value={selectedController} onChange={(e)=>setSelectedController(e.target.value)}>{controllers.map((item)=><option key={item.public_id} value={item.public_id}>{item.display_name} · {item.status} · {item.event_count} events</option>)}</select></Field>
      {selectedControllerSummary && <><div className="grid gap-3 md:grid-cols-2"><Field label="Legal name"><input className={inputClass} value={controller.legal_name} onChange={(e)=>setController({...controller,legal_name:e.target.value})}/></Field><Field label="Controller type"><select className={inputClass} value={controller.controller_type} onChange={(e)=>setController({...controller,controller_type:e.target.value as ControllerForm["controller_type"]})}><option value="organisation">Organisation</option><option value="individual">Individual</option></select></Field><Field label="Postal address"><textarea className={inputClass} value={controller.postal_address} onChange={(e)=>setController({...controller,postal_address:e.target.value})}/></Field><Field label="Country"><input className={inputClass} maxLength={2} value={controller.country} onChange={(e)=>setController({...controller,country:e.target.value.toUpperCase()})}/></Field><Field label="Privacy contact"><input className={inputClass} type="email" value={controller.privacy_contact_email} onChange={(e)=>setController({...controller,privacy_contact_email:e.target.value})}/></Field><Field label="DPO contact (optional)"><input className={inputClass} value={controller.dpo_contact} onChange={(e)=>setController({...controller,dpo_contact:e.target.value})}/></Field><Field label="Supervisory authority"><input className={inputClass} value={controller.supervisory_authority_name} onChange={(e)=>setController({...controller,supervisory_authority_name:e.target.value})}/></Field><Field label="Authority URL"><input className={inputClass} value={controller.supervisory_authority_url} onChange={(e)=>setController({...controller,supervisory_authority_url:e.target.value})}/></Field></div><Field label="Processor / operator relationship"><textarea rows={3} className={inputClass} value={controller.processor_summary} onChange={(e)=>setController({...controller,processor_summary:e.target.value})}/></Field><Field label="Rights procedure"><textarea rows={3} className={inputClass} value={controller.rights_summary} onChange={(e)=>setController({...controller,rights_summary:e.target.value})}/></Field><Field label="Terms summary"><textarea rows={3} className={inputClass} value={controller.terms_summary} onChange={(e)=>setController({...controller,terms_summary:e.target.value})}/></Field><Field label="Structured controller governance (JSON)"><textarea rows={7} className={`${inputClass} font-mono`} value={controller.governance} onChange={(e)=>setController({...controller,governance:e.target.value})}/></Field><div className="flex flex-wrap gap-2"><Button disabled={!!busy} onClick={saveController}>Save controller governance</Button><Button variant="outline" disabled={!!busy || !selectedControllerSummary.has_governance_profile} onClick={() => void run("controller-publish", () => withReauth(() => apiFetch(`/api/v1/admin/controllers/${selectedController}/governance/publications`, {method:"POST", body:JSON.stringify({external_authorisation_ref:null})})), "Controller governance published")}>Publish immutable controller policy</Button><span className="self-center text-xs font-mono text-gray-500">Trust ID {selectedControllerSummary.trust_entity_id}</span></div></>}
    </Card>

    <Card className="space-y-4 p-5"><div><h3 className="font-semibold">Event governance and optional features</h3><p className="text-sm text-gray-600 dark:text-gray-300">The event owner is immutable. This panel records only event-specific notice and feature choices.</p></div><Field label="Event"><select className={inputClass} value={selectedEvent} onChange={(e)=>setSelectedEvent(e.target.value ? Number(e.target.value) : "")}><option value="">Select event</option>{events.map((item)=><option key={item.id} value={item.id}>{item.controller_name} · {item.name}</option>)}</select></Field>{selectedEventRow && <><Field label="Event-specific notice (optional)"><textarea rows={3} className={inputClass} value={eventNotice} onChange={(e)=>setEventNotice(e.target.value)}/></Field><fieldset><legend className="text-sm font-medium">Enabled optional features</legend><div className="mt-2 grid gap-2 sm:grid-cols-2">{FEATURES.map(([key,label])=><label key={key} className="flex items-center gap-2 rounded-lg border border-gray-200 p-3 text-sm dark:border-gray-700"><input type="checkbox" checked={eventFeatures.includes(key)} onChange={(e)=>setEventFeatures((current)=>e.target.checked?[...current,key].sort():current.filter((item)=>item!==key))}/>{label}</label>)}</div></fieldset><Field label="Contact routing (JSON object)"><textarea rows={4} className={`${inputClass} font-mono`} value={contactRouting} onChange={(e)=>setContactRouting(e.target.value)}/></Field><Button disabled={!!busy} onClick={saveEvent}>Save event configuration</Button></>}
    </Card>
  </div>;
}
