"use client";

import { ChangeEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Check, Download, KeyRound, LockKeyhole, ShieldCheck } from "lucide-react";
import { startAuthentication } from "@simplewebauthn/browser";

import { GovernanceWorkspace } from "@/app/admin/governance/page";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { apiFetch } from "@/lib/api";
import { generateAgeRecoveryIdentity } from "@/lib/ageIdentity";
import {
  ControllerPrivatePackage,
  ControllerPublicPackage,
  downloadJson,
  generateControllerKey,
  loadControllerKey,
  newControllerEntityId,
  signControllerRegistration,
} from "@/lib/controllerKey";
import { hardNavigate } from "@/lib/hardNavigation";
import { passkeyErrorMessage } from "@/lib/passkeyError";
import { responseMessage } from "@/lib/responseMessage";

type Step = { id: "recovery" | "controller" | "governance"; title: string; number: number; status: "complete" | "current" | "locked"; completed_at: string | null };
type SetupStatus = {
  current_step: "recovery" | "controller" | "governance" | "complete";
  current_step_number: number; total_steps: 3; percent_complete: number; steps: Step[];
  next_action: { code: string; message: string }; can_enter_administration: boolean;
  controller: { entity_id: string | null; key_id: string | null; public_key_sha256: string | null; trust_establishment_sha256: string | null };
  governance: { published: boolean; version: number | null; content_sha256: string | null };
  commissioning: { completed_at: string | null; receipt_sha256: string | null };
};

function Stepper({ status }: { status: SetupStatus }) {
  return <Card className="p-5">
    <div className="mb-4 flex items-center justify-between gap-4"><div><p className="text-sm font-medium text-blue-700 dark:text-blue-300">Root commissioning</p><p className="text-2xl font-semibold">{status.current_step === "complete" ? "Complete" : `Step ${status.current_step_number} of 3`}</p></div><span className="text-sm font-medium">{status.percent_complete}%</span></div>
    <div className="mb-5 h-2 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700"><div className="h-full bg-blue-600 transition-all" style={{ width: `${status.percent_complete}%` }} /></div>
    <ol className="grid gap-3 md:grid-cols-3">{status.steps.map((step) => <li key={step.id} className={`rounded-lg border p-3 ${step.status === "current" ? "border-blue-500 bg-blue-50 dark:bg-blue-950/40" : step.status === "complete" ? "border-green-300 bg-green-50 dark:border-green-800 dark:bg-green-950/30" : "border-gray-200 opacity-60 dark:border-gray-700"}`}><div className="flex items-center gap-2"><span className="flex h-7 w-7 items-center justify-center rounded-full bg-white text-sm font-semibold text-gray-800 shadow-sm dark:bg-gray-800 dark:text-gray-100">{step.status === "complete" ? <Check size={16} /> : step.number}</span><strong>{step.title}</strong></div>{step.completed_at && <p className="mt-2 text-xs text-gray-500">Completed {new Date(step.completed_at).toLocaleString()}</p>}</li>)}</ol>
    <p className="mt-4 rounded-lg bg-gray-100 px-4 py-3 text-sm dark:bg-gray-800"><strong>Next action:</strong> {status.next_action.message}</p>
  </Card>;
}

function RecoveryStep({ refresh }: { refresh: () => Promise<void> }) {
  const [identity, setIdentity] = useState(""); const [recipient, setRecipient] = useState("");
  const [downloaded, setDownloaded] = useState(false); const [verified, setVerified] = useState(false);
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState("");
  const fileContents = useMemo(() => identity && recipient ? ["# MP-OPT snapshot recovery identity", `# Public key: ${recipient}`, "# Keep this private file in protected custody.", identity, ""].join("\n") : "", [identity, recipient]);
  const generate = async () => { setMessage(""); const value = await generateAgeRecoveryIdentity(); setIdentity(value.identity); setRecipient(value.recipient); setDownloaded(false); setVerified(false); };
  const download = () => { const url = URL.createObjectURL(new Blob([fileContents], { type: "text/plain" })); const link = document.createElement("a"); link.href = url; link.download = "mp-opt-recovery.agekey"; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); setDownloaded(true); };
  const reselect = async (event: ChangeEvent<HTMLInputElement>) => { const file = event.target.files?.[0]; event.target.value = ""; if (!file) return; const exact = await file.text(); setVerified(exact === fileContents); setMessage(exact === fileContents ? "The downloaded private identity was verified locally. Nothing private was uploaded." : "That is not the exact recovery file generated in this session."); };
  const complete = async () => { setBusy(true); const response = await apiFetch("/api/v1/setup/recovery/complete", { method: "POST", body: JSON.stringify({ recipient, download_acknowledged: true, local_reimport_verified: true }) }); const body = await response.json().catch(() => ({})); setBusy(false); if (!response.ok) { setMessage(responseMessage(body, "Recovery setup failed")); return; } setIdentity(""); await refresh(); };
  return <Card className="space-y-5 p-6"><div className="flex gap-3"><LockKeyhole className="text-blue-600" /><div><h2 className="text-xl font-semibold">1. Recovery key</h2><p className="text-sm text-gray-500">The browser creates this key. Only its public recipient and your completion acknowledgement reach the Server.</p></div></div>
    {!identity ? <Button onClick={() => void generate()}>Generate recovery key locally</Button> : <><div className="rounded-lg border border-gray-200 p-4 dark:border-gray-700"><p className="text-xs text-gray-500">Public recipient</p><p className="mt-1 break-all font-mono text-sm">{recipient}</p></div><div className="flex flex-wrap gap-3"><Button onClick={download}><Download size={17} />Download private recovery file</Button><label className="inline-flex min-h-11 cursor-pointer items-center rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium dark:border-gray-600">Select downloaded file again<input type="file" accept=".agekey,text/plain" className="sr-only" onChange={(event) => void reselect(event)} /></label></div></>}
    {message && <p role="status" className={`rounded-lg p-3 text-sm ${verified ? "bg-green-50 text-green-800 dark:bg-green-950 dark:text-green-200" : "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200"}`}>{message}</p>}
    <Button disabled={!downloaded || !verified || busy} onClick={() => void complete()}>Complete recovery-key step</Button>
    <p className="text-xs text-gray-500">If you close this page before completion, the browser forgets the private key and you must generate a new one. Later changes use the guarded TUI recovery workflow.</p>
  </Card>;
}

function ControllerStep({ refresh }: { refresh: () => Promise<void> }) {
  const [entityId, setEntityId] = useState("");
  const [privatePackage, setPrivatePackage] = useState<ControllerPrivatePackage | null>(null); const [publicPackage, setPublicPackage] = useState<ControllerPublicPackage | null>(null);
  const [privateDownloaded, setPrivateDownloaded] = useState(false); const [publicDownloaded, setPublicDownloaded] = useState(false); const [reimported, setReimported] = useState(false);
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState("");
  useEffect(() => { setEntityId(newControllerEntityId()); }, []);
  const generate = async () => { try { const value = await generateControllerKey(entityId); setPrivatePackage(value.privatePackage); setPublicPackage(value.publicPackage); setPrivateDownloaded(false); setPublicDownloaded(false); setReimported(false); setMessage("The controller key was generated locally. Download both files, store the private file in protected custody, then reselect it for verification."); } catch (caught) { setMessage(caught instanceof Error ? caught.message : "Key generation failed"); } };
  const importPrivate = async (event: ChangeEvent<HTMLInputElement>, initial: boolean) => { const file = event.target.files?.[0]; event.target.value = ""; if (!file) return; try { const value = JSON.parse(await file.text()) as ControllerPrivatePackage; const loaded = await loadControllerKey(value); setPrivatePackage(value); setPublicPackage(loaded.publicPackage); setReimported(true); if (initial) setPrivateDownloaded(true); setMessage("The private key and its public identity were verified locally. Nothing private was uploaded."); } catch (caught) { setReimported(false); setMessage(caught instanceof Error ? caught.message : "The controller-key file could not be opened."); } };
  const activate = async () => { if (!privatePackage || !publicPackage) return; setBusy(true); setMessage("Establishing controller identity…"); try {
    const begin = await apiFetch("/api/v1/setup/controller/challenges", { method: "POST", body: JSON.stringify({ public_key: publicPackage.public_key, role: "controller", entity_id: publicPackage.entity_id, supersedes_key_id: null, reason: null }) }); const begun = await begin.json().catch(() => ({})); if (!begin.ok) throw new Error(responseMessage(begun, "Controller registration could not start"));
    const challenge = begun.challenge as Record<string, unknown>; const proof = await signControllerRegistration(privatePackage, challenge);
    const proofResponse = await apiFetch("/api/v1/setup/controller/proofs", { method: "POST", body: JSON.stringify({ challenge, proof, previous_proof: null }) }); const proofBody = await proofResponse.json().catch(() => ({})); if (!proofResponse.ok) throw new Error(responseMessage(proofBody, "Controller possession proof was rejected"));
    const challengeId = String(challenge.challenge_id); const authBegin = await apiFetch(`/api/v1/setup/controller/${encodeURIComponent(challengeId)}/root-authorisation/begin`, { method: "POST", body: JSON.stringify({}) }); const authBody = await authBegin.json().catch(() => ({})); if (!authBegin.ok) throw new Error(responseMessage(authBody, "Root authorisation could not start"));
    const credential = await startAuthentication({ optionsJSON: JSON.parse(authBody.options) });
    const complete = await apiFetch(`/api/v1/setup/controller/${encodeURIComponent(challengeId)}/root-authorisation/complete`, { method: "POST", body: JSON.stringify({ ceremony_id: authBody.ceremony_id, credential }) }); const completed = await complete.json().catch(() => ({})); if (!complete.ok) throw new Error(passkeyErrorMessage(completed, "Root authorisation failed"));
    setPrivatePackage(null); await refresh();
  } catch (caught) { setMessage(caught instanceof Error ? caught.message : "Controller identity setup failed"); } finally { setBusy(false); } };
  return <Card className="space-y-5 p-6"><div className="flex gap-3"><KeyRound className="text-blue-600" /><div><h2 className="text-xl font-semibold">2. Controller identity</h2><p className="text-sm text-gray-500">The browser downloads the private key without a separate passphrase. Move it into your protected password-manager or offline custody and delete the downloaded copy after verification. The Server receives only public identity and proof.</p></div></div>
    {!privatePackage && <div className="grid gap-4 md:grid-cols-2"><section className="rounded-lg border border-blue-200 p-4 dark:border-blue-800"><h3 className="font-semibold">Generate locally <span className="text-sm font-normal text-blue-600">Recommended</span></h3><p className="my-3 text-sm text-gray-500">Opaque identity: {entityId || "Preparing…"}</p><Button className="mt-3" onClick={() => void generate()}>Generate controller key</Button></section><section className="rounded-lg border border-gray-200 p-4 dark:border-gray-700"><h3 className="font-semibold">Import existing private key</h3><p className="my-3 text-sm text-gray-500">Use an unencrypted package made by the independent Evidence-Public controller-key tool.</p><label className="inline-flex min-h-11 cursor-pointer items-center rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium dark:border-gray-600">Select private-key file<input type="file" accept="application/json,.json" className="sr-only" onChange={(event) => void importPrivate(event, true)} /></label></section></div>}
    {privatePackage && publicPackage && <><div className="rounded-lg border border-gray-200 p-4 text-sm dark:border-gray-700"><p><strong>{publicPackage.entity_id}</strong> · {publicPackage.key_id}</p><p className="mt-1 break-all font-mono text-xs">SHA-256 {publicPackage.public_key_sha256}</p></div><div className="flex flex-wrap gap-3"><Button variant="outline" onClick={() => { downloadJson(`${publicPackage.key_id}.controller-private.json`, privatePackage); setPrivateDownloaded(true); }}><Download size={17} />Private key</Button><Button variant="outline" onClick={() => { downloadJson(`${publicPackage.key_id}.controller-public.json`, publicPackage); setPublicDownloaded(true); }}><Download size={17} />Public package</Button><label className="inline-flex min-h-11 cursor-pointer items-center rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium dark:border-gray-600">Select downloaded private key again<input type="file" accept="application/json,.json" className="sr-only" onChange={(event) => void importPrivate(event, false)} /></label></div><Button disabled={!privateDownloaded || !publicDownloaded || !reimported || busy} onClick={() => void activate()}>Verify and approve controller identity</Button></>}
    {message && <p role="status" className="rounded-lg bg-gray-100 p-3 text-sm dark:bg-gray-800">{message}</p>}
  </Card>;
}

export default function SetupPage() {
  const [status, setStatus] = useState<SetupStatus | null>(null); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => { const response = await apiFetch("/api/v1/setup/status"); if (response.status === 401) { hardNavigate("/login?next=/setup"); return; } const body = await response.json().catch(() => ({})); if (!response.ok) { setError(responseMessage(body, "Setup status is unavailable")); return; } setStatus(body as SetupStatus); setError(""); }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  const finalise = async () => { setBusy(true); const response = await apiFetch("/api/v1/setup/finalise", { method: "POST", body: JSON.stringify({}) }); const body = await response.json().catch(() => ({})); setBusy(false); if (!response.ok) { setError(responseMessage(body, "Final checks failed")); return; } setStatus(body as SetupStatus); };
  if (!status) return <main className="flex min-h-screen items-center justify-center bg-gray-50 dark:bg-gray-900"><p>{error || "Loading commissioning progress…"}</p></main>;
  return <div className="min-h-screen bg-gray-50 text-gray-900 dark:bg-gray-900 dark:text-gray-100"><header className="border-b border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800"><div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3"><Logo height={34} href="https://info.mp-opt.net" /><ThemeToggle /></div></header><main className="mx-auto max-w-6xl space-y-6 px-4 py-8"><Stepper status={status} />{error && <p role="alert" className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
    {status.current_step === "recovery" && <RecoveryStep refresh={refresh} />}
    {status.current_step === "controller" && <ControllerStep refresh={refresh} />}
    {status.current_step === "governance" && !status.governance.published && <GovernanceWorkspace setupMode onPublished={() => void refresh()} />}
    {status.current_step === "governance" && status.governance.published && <Card className="space-y-4 p-6"><div className="flex gap-3"><ShieldCheck className="text-green-600" /><div><h2 className="text-xl font-semibold">Governance version 1 is published</h2><p className="text-sm text-gray-500">Run the automatic final checks and seal the commissioning receipt.</p></div></div><p className="break-all font-mono text-xs">SHA-256 {status.governance.content_sha256}</p><Button disabled={busy} onClick={() => void finalise()}>Run final checks and complete setup</Button></Card>}
    {status.current_step === "complete" && <Card className="space-y-5 p-6"><div className="flex gap-3"><ShieldCheck className="text-green-600" /><div><h2 className="text-2xl font-semibold">Commissioning complete</h2><p className="text-sm text-gray-500">The Server verified the setup facts and sealed the final receipt.</p></div></div><dl className="grid gap-3 text-sm md:grid-cols-2"><div><dt className="text-gray-500">Controller key</dt><dd>{status.controller.key_id}</dd></div><div><dt className="text-gray-500">Governance version</dt><dd>{status.governance.version}</dd></div><div><dt className="text-gray-500">Governance SHA-256</dt><dd className="break-all font-mono text-xs">{status.governance.content_sha256}</dd></div><div><dt className="text-gray-500">Commissioning receipt</dt><dd className="break-all font-mono text-xs">{status.commissioning.receipt_sha256}</dd></div></dl><div className="flex flex-wrap gap-3"><Button onClick={() => hardNavigate("/admin")}>Enter administration</Button><a className="inline-flex min-h-11 items-center rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium dark:border-gray-600" href="/api/v1/setup/report.zip"><Download size={17} className="mr-2" />Download commissioning report</a><a className="inline-flex min-h-11 items-center rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium dark:border-gray-600" href="/account/security">Add a second root passkey (optional)</a></div></Card>}
  </main></div>;
}
