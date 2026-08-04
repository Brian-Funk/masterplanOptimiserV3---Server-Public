"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { startAuthentication } from "@simplewebauthn/browser";
import { ArrowDown, CheckCircle2, Info, KeyRound, Laptop, RefreshCw, Server, ShieldCheck, UserRoundCheck } from "lucide-react";

import { apiFetch } from "@/lib/api";
import { withReauth } from "@/lib/reauth";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";

type TrustKey = {
  instance_id: string;
  entity_id: string | null;
  key_id: string;
  role: "instance" | "controller" | "processor";
  public_key_sha256: string;
  validity_status: "pending" | "active" | "revoked";
  created_at: string | null;
  activated_at: string | null;
  revoked_at: string | null;
  supersedes_key_id: string | null;
  event_ref: string | null;
  event_name: string | null;
  display_label: string | null;
  trust_establishment_sha256: string | null;
};

type PendingEnrolment = {
  challenge_id: string;
  event_ref: string;
  event_name: string;
  entity_id: string;
  display_label: string | null;
  key_id: string;
  public_key_sha256: string;
  purpose: "register" | "rotate";
  expires_at: string;
};

function messageFrom(value: unknown): string {
  if (!value || typeof value !== "object") return "The operation was rejected.";
  const detail = (value as Record<string, unknown>).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && typeof (detail as Record<string, unknown>).message === "string") {
    return String((detail as Record<string, unknown>).message);
  }
  return "The operation was rejected.";
}

async function checked(response: Response): Promise<unknown> {
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(messageFrom(body));
  return body;
}

function Status({ value }: { value: TrustKey["validity_status"] | "ready" | "required" }) {
  const active = value === "active" || value === "ready";
  return <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${active ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300" : value === "revoked" ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300" : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300"}`}>{value.replaceAll("_", " ")}</span>;
}

function KeyCard({ title, subtitle, scope, icon, status, fingerprint, children }: { title: string; subtitle: string; scope: string; icon: React.ReactNode; status: TrustKey["validity_status"] | "ready" | "required"; fingerprint?: string; children?: React.ReactNode }) {
  return <Card className="space-y-3 border-l-4 border-l-blue-500 p-4">
    <div className="flex items-start justify-between gap-3"><div className="flex min-w-0 gap-3"><span className="mt-0.5 text-blue-600 dark:text-blue-300">{icon}</span><div className="min-w-0"><h3 className="font-semibold text-gray-900 dark:text-gray-100">{title}</h3><p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">{subtitle}</p></div></div><Status value={status} /></div>
    <p className="text-sm text-gray-700 dark:text-gray-300">{scope}</p>
    {fingerprint && <p className="truncate font-mono text-xs text-gray-500" title={fingerprint}>SHA-256 {fingerprint}</p>}
    {children}
  </Card>;
}

export function TrustKeysPanel() {
  const [keys, setKeys] = useState<TrustKey[]>([]);
  const [pending, setPending] = useState<PendingEnrolment[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [controllerPublicKey, setControllerPublicKey] = useState("");
  const [controllerEntity, setControllerEntity] = useState("");
  const [challenge, setChallenge] = useState("");
  const [signedRegistration, setSignedRegistration] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [keysResponse, pendingResponse] = await Promise.all([
        apiFetch("/api/v1/admin/evidence/trust-keys"),
        apiFetch("/api/v1/admin/evidence/trust-keys/pending-enrolments"),
      ]);
      setKeys(await checked(keysResponse) as TrustKey[]);
      setPending(await checked(pendingResponse) as PendingEnrolment[]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Trust status could not be loaded.");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const controllers = keys.filter((key) => key.role === "controller");
  const processorsByEvent = useMemo(() => {
    const result = new Map<string, TrustKey[]>();
    keys.filter((key) => key.role === "processor").forEach((key) => {
      const event = key.event_ref || "Unassigned";
      result.set(event, [...(result.get(event) || []), key]);
    });
    return [...result.entries()];
  }, [keys]);

  async function authorise(enrolment: PendingEnrolment) {
    setBusy(enrolment.challenge_id); setError(""); setNotice("");
    try {
      const begin = await checked(await apiFetch(`/api/v1/admin/evidence/trust-keys/${enrolment.challenge_id}/root-authorisation/begin`, { method: "POST", body: "{}" })) as { options: string; ceremony_id: string };
      const credential = await startAuthentication({ optionsJSON: JSON.parse(begin.options) });
      await checked(await apiFetch(`/api/v1/admin/evidence/trust-keys/${enrolment.challenge_id}/root-authorisation/complete`, { method: "POST", body: JSON.stringify({ ceremony_id: begin.ceremony_id, credential }) }));
      setNotice(`${enrolment.display_label || enrolment.entity_id} is active for ${enrolment.event_name}.`);
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Processor activation failed."); }
    finally { setBusy(""); }
  }

  async function revoke(key: TrustKey) {
    const reason = window.prompt("Why is this key being revoked? Use retired, lost, compromised, or role_changed.", "retired");
    if (!reason || !["retired", "lost", "compromised", "role_changed"].includes(reason)) return;
    setBusy(key.key_id); setError("");
    try {
      await checked(await withReauth(() => apiFetch(`/api/v1/admin/evidence/trust-keys/${key.key_id}/revoke`, { method: "POST", body: JSON.stringify({ reason_code: reason, confirmation: "ROOT PASSKEY AUTHORISED" }) })));
      setNotice(`${key.key_id} was revoked. Historical evidence remains verifiable.`);
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Key revocation failed."); }
    finally { setBusy(""); }
  }

  async function beginControllerRegistration() {
    setBusy("controller-begin"); setError("");
    try {
      const result = await checked(await withReauth(() => apiFetch("/api/v1/admin/evidence/trust-keys/challenges", { method: "POST", body: JSON.stringify({ public_key: controllerPublicKey.trim(), role: "controller", entity_id: controllerEntity.trim() }) }))) as { challenge: object };
      setChallenge(JSON.stringify(result.challenge, null, 2));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Controller challenge creation failed."); }
    finally { setBusy(""); }
  }

  async function completeControllerRegistration() {
    setBusy("controller-complete"); setError("");
    try {
      const packageValue = JSON.parse(signedRegistration) as { document: object; proof: object };
      const proof = await checked(await withReauth(() => apiFetch("/api/v1/admin/evidence/trust-keys/proofs", { method: "POST", body: JSON.stringify({ challenge: packageValue.document, proof: packageValue.proof, previous_proof: null }) }))) as { challenge_id: string };
      const enrolment: PendingEnrolment = { challenge_id: proof.challenge_id, event_ref: "", event_name: "controller trust", entity_id: controllerEntity, display_label: null, key_id: "", public_key_sha256: "", purpose: "register", expires_at: "" };
      await authorise(enrolment);
      setControllerPublicKey(""); setControllerEntity(""); setChallenge(""); setSignedRegistration("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Controller key activation failed."); }
    finally { setBusy(""); }
  }

  return <div className="space-y-6">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h1 className="flex items-center gap-2 text-3xl font-bold"><KeyRound className="text-blue-600" />Trust &amp; keys</h1><p className="mt-1 max-w-3xl text-sm text-gray-600 dark:text-gray-300">Set up trust once, then return here only for enrolment, rotation, recovery, or revocation.</p></div><Button size="sm" variant="outline" onClick={() => void load()} disabled={!!busy}><RefreshCw size={15} />Refresh</Button></div>
    {error && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">{error}</p>}
    {notice && <p role="status" className="rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-800 dark:border-green-800 dark:bg-green-950/40 dark:text-green-200">{notice}</p>}
    <div className="flex items-start gap-3 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-100"><Info size={18} className="mt-0.5 shrink-0" /><p>Arrows show trust responsibilities, not key derivation. Private processor keys stay on their Desktop workstation; this Server stores public keys only.</p></div>

    <section aria-labelledby="trust-flow" className="space-y-3"><h2 id="trust-flow" className="text-lg font-semibold">Trust flow</h2>
      <div className="mx-auto grid max-w-3xl gap-2">
        <KeyCard title="Controller trust" subtitle={controllers.filter((key) => key.validity_status === "active").length ? "Controller identity established" : "Controller key required before governance publication"} scope="Signs controller trust and governance statements. It does not approve individual deletion cases." icon={<UserRoundCheck size={20} />} status={controllers.some((key) => key.validity_status === "active") ? "active" : "required"} />
        <ArrowDown className="mx-auto text-gray-400" aria-hidden="true" />
        <KeyCard title="Root passkey" subtitle="Human authorisation" scope="Approves processor enrolment, privileged Server deletion steps, and final case closure." icon={<ShieldCheck size={20} />} status="ready" />
        <ArrowDown className="mx-auto text-gray-400" aria-hidden="true" />
        <KeyCard title="Instance evidence key" subtitle="Held by this deployment" scope="Seals the append-only evidence chain and final receipts after required human and processor actions are complete." icon={<Server size={20} />} status="ready" />
      </div>
    </section>

    {pending.length > 0 && <section aria-labelledby="pending-enrolments" className="space-y-3"><h2 id="pending-enrolments" className="text-lg font-semibold">Pending Desktop enrolments</h2>{pending.map((item) => <Card key={item.challenge_id} className="flex flex-wrap items-center justify-between gap-4 border-amber-200 p-4 dark:border-amber-800"><div><p className="font-semibold">{item.display_label || item.entity_id}</p><p className="mt-1 text-sm text-gray-600 dark:text-gray-300">{item.event_name} · {item.purpose}</p><p className="mt-1 font-mono text-xs text-gray-500">{item.public_key_sha256}</p></div><Button onClick={() => void authorise(item)} disabled={!!busy}>{busy === item.challenge_id ? "Waiting for passkey…" : "Approve this event assignment"}</Button></Card>)}</section>}

    <section aria-labelledby="processor-keys" className="space-y-3"><h2 id="processor-keys" className="text-lg font-semibold">Event processor keys</h2>{processorsByEvent.length === 0 ? <Card className="p-5 text-sm text-gray-500">No Desktop processor is enrolled. Linking an event from Desktop starts enrolment.</Card> : processorsByEvent.map(([eventRef, eventKeys]) => <div key={eventRef} className="space-y-2"><h3 className="text-sm font-semibold text-gray-700 dark:text-gray-200">{eventKeys[0]?.event_name || eventRef}</h3><div className="grid gap-3 lg:grid-cols-2">{eventKeys.map((key) => <KeyCard key={key.key_id} title={key.display_label || key.entity_id || key.key_id} subtitle={`${key.key_id} · ${key.event_ref || "event assignment missing"}`} scope="Signs this event's Desktop policy acknowledgement, controlled deletion receipt, and local-copy resolution only." icon={<Laptop size={20} />} status={key.validity_status} fingerprint={key.public_key_sha256}>{key.validity_status === "active" && <Button size="sm" variant="outline" onClick={() => void revoke(key)} disabled={!!busy}>Revoke</Button>}</KeyCard>)}</div></div>)}</section>

    <section aria-labelledby="controller-keys" className="space-y-3"><h2 id="controller-keys" className="text-lg font-semibold">Controller keys</h2><div className="grid gap-3 lg:grid-cols-2">{controllers.map((key) => <KeyCard key={key.key_id} title={key.entity_id || key.key_id} subtitle={`${key.key_id} · ${key.trust_establishment_sha256 ? "trust established" : "pending activation"}`} scope="Controller trust and governance statements only. Each publication still requires the root passkey." icon={<UserRoundCheck size={20} />} status={key.validity_status} fingerprint={key.public_key_sha256}>{key.validity_status === "active" && <Button size="sm" variant="outline" onClick={() => void revoke(key)} disabled={!!busy}>Revoke</Button>}</KeyCard>)}</div>
      {controllers.some((key) => key.validity_status === "active" && key.trust_establishment_sha256) && <p className="flex items-center gap-2 text-sm text-green-700 dark:text-green-300"><CheckCircle2 size={16} />Controller identity and possession were established during registration.</p>}
      <details className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800"><summary className="cursor-pointer text-sm font-semibold">Advanced controller key ceremony</summary><div className="mt-4 space-y-3"><p className="text-sm text-gray-600 dark:text-gray-300">The controller custody tool signs the exact challenge. Never paste a private key into this page.</p><label className="block text-sm font-medium">Controller entity ID<input value={controllerEntity} onChange={(event) => setControllerEntity(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-900" /></label><label className="block text-sm font-medium">OpenSSH public key<textarea value={controllerPublicKey} onChange={(event) => setControllerPublicKey(event.target.value)} rows={3} className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 font-mono text-xs dark:border-gray-600 dark:bg-gray-900" /></label><Button variant="outline" onClick={() => void beginControllerRegistration()} disabled={!!busy || !controllerEntity || !controllerPublicKey}>Create exact challenge</Button>{challenge && <label className="block text-sm font-medium">Challenge to sign<textarea readOnly value={challenge} rows={8} className="mt-1 w-full rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 font-mono text-xs dark:border-gray-600 dark:bg-gray-950" /></label>}<label className="block text-sm font-medium">Signed registration package<textarea value={signedRegistration} onChange={(event) => setSignedRegistration(event.target.value)} rows={8} className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 font-mono text-xs dark:border-gray-600 dark:bg-gray-900" /></label><Button onClick={() => void completeControllerRegistration()} disabled={!!busy || !signedRegistration}>Verify and activate with root passkey</Button></div></details>
    </section>
    <p className="flex items-center gap-2 text-xs text-gray-500"><CheckCircle2 size={14} />Rotation and revocation preserve earlier public keys and signatures for historical verification.</p>
  </div>;
}
