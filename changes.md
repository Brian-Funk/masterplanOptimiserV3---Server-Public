# Changes

## 3.9.12 — 16 August 2026

This patch release repairs initial account activation while preserving existing
installations and their data.

### Consent evidence

- Seal first-activation consent with the evidence ledger's existing
  pseudonymous subject, event, policy, statement, document and timestamp
  fields.
- Register the consent record type with the signed evidence validator without
  permitting controller names, email addresses or display names in ledger
  payloads.
- Exercise the real evidence writer during account activation tests and audit
  every application evidence call against the signed manifest contract.

### Registration feedback

- Show the safe message from structured passkey API errors instead of reducing
  them to a generic registration failure.

## 3.9.11 — 16 August 2026

This patch release prevents scheduled recovery snapshots from racing with
commissioning, while preserving the restricted service identities used in
production.

### Commissioning and snapshot coordination

- Use one host-local execution lease for setup and snapshots, with a consistent
  lock order across both paths.
- Defer a scheduled snapshot successfully when commissioning holds the lease,
  while returning a retryable result if setup encounters an active snapshot.
- Keep final installation validation inside the setup lease so a timer catch-up
  cannot stop the Backend during its health checks.
- Resume a completed `validated` checkpoint without redeploying the application
  or repeating the first HA copy.

### Restricted snapshot execution

- Reuse the already-validated runtime Compose command from the scheduled
  snapshot service instead of attempting permission repair or `sudo` inside a
  `NoNewPrivileges` unit.
- Retain strict path, mount, owner and mode checks before database readiness,
  encryption and retention work begins.
- Keep a non-holder Node B snapshot invocation successful and side-effect free.

### Concurrent transient cleanup

- Serialize cleanup of approved commissioning temporary files through a
  protected lock.
- Treat an expected file disappearing during inspection as already removed,
  while continuing to reject symlinks, substituted paths and unsafe file
  ownership or modes.
- Never read or print temporary secret contents during cleanup.

## 3.9.10 — 15 August 2026

This patch release keeps standby protection and scheduled recovery snapshots
working under the restricted systemd identities used in production.

### Standby-protection reliability

- Make the replication worker validate its existing runtime contract without
  attempting privileged repairs from inside a `NoNewPrivileges` sandbox.
- Capture application state from the already-running database and Backend,
  while retaining the existing permission, evidence and root-bootstrap safety
  checks.
- Reconcile the joining peer after the sender has durably written the exact
  accepted-bundle receipt, so an early receiver check cannot leave Node B in a
  stale waiting state.
- Report permission and retired-bootstrap failures with bounded causes, and
  allow an affected durable mutation to be retried after its runtime contract
  is healthy.
- Retire and verify the temporary root-bootstrap bearer value when automated
  browser commissioning is reconciled, before the first HA copy or snapshot is
  allowed to run.

### Scheduled recovery snapshots

- Run scheduled snapshots through validation-only service helpers that do not
  invoke `sudo` or mutate permissions from the restricted oneshot unit.
- Read evidence and its anchor through the Backend container's existing access
  contract and require every Compose preparation step to succeed before the
  snapshot proceeds.
- Keep interactive root recovery paths able to repair permissions while the
  scheduled service remains strictly non-privileged.

### Commissioning qualification

- Require the private commissioning laboratory to create a real protected
  event after root setup and wait for Node B's exact acceptance receipt.
- Require the installed scheduled-snapshot systemd unit to finish successfully
  before a fresh HA run can pass.

## 3.9.9 — 15 August 2026

This patch release makes commissioning and recovery resumable through the same
bounded operations used by the interactive TUI, and qualifies fresh two-node
setup against exact development artifacts before a release is created.

### Deterministic commissioning interface

- Add a root-local structured commissioning interface with versioned status,
  event, plan, advance, reconciliation, cancellation and protected handoff
  contracts.
- Keep the TUI and automation on the same commissioning engine, execution
  lease, durable checkpoints and authoritative deployment receipts.
- Accept exact digest-addressed candidate bundles whose four images and
  frontend, operations and bootstrap assets are bound to one Server commit.
- Keep candidate artifacts explicitly ineligible for release and leave
  automatic failover disabled throughout commissioning qualification.

### HA setup and recovery reliability

- Reconcile fresh-primary and joining-peer state from exact deployment,
  witness, lease and first-copy receipts without repeating completed work.
- Preserve required evidence, secret and runtime custody while activating a
  blank standby, replacing a failed peer, converting standalone service to HA
  or staging a full-loss recovery.
- Make witness creation and secret rotation safely retryable, including the
  interval where provider state exists but the local checkpoint is missing.
- Distinguish bounded provider, service, replication and recovery failures so
  an interrupted operation resumes at the step that actually needs attention.

### Safer validation and cleanup

- Validate machine-supplied commissioning values before they reach remote
  management or provider operations, and bind provider cleanup to exact
  run-attributed resources and durable receipts.
- Enforce protected ownership and modes for bootstrap, snapshot, evidence,
  recovery and replication state before every relevant action.
- Add test-policy-only interruption hooks at real commissioning boundaries
  without enabling them in signed production operation.
- Refresh the `nanoid` security override and the generated third-party notices.

## 3.9.8 — 12 August 2026

This patch release makes two-node commissioning report the step that actually
needs attention and keeps first account activation compact without hiding the
published processing information.

### Deterministic commissioning and HA validation

- Accept systemd's valid optional `-` prefix while enforcing the complete
  service identity, sandbox and path contract for every HA service.
- Store the exact sender and receiver bundle receipts before marking the first
  protected copy complete, then activate and validate HA services in a separate
  checkpoint.
- Record an action code and checkpoint around every commissioning step so a
  later installation, SMTP or recovery failure cannot inherit an earlier label.
- Avoid recreating an already-correct primary backend during initial lease
  registration and require consecutive healthy local observations before a
  deployment is considered stable.
- Distinguish an unavailable Caddy service, a failed Docker execution and a
  configuration rejection, while keeping public routing separate from local
  installation validity.
- Clean interrupted setup, pairing, witness and validation files only after
  strict path, owner, mode and file-type checks.

### Compact first-account consent

- Show the effective controller, a short purpose and the authenticated audience
  directly on account setup.
- Move the complete purposes, data categories, privacy contact, notice links,
  policy digest and exact statement into an accessible responsive details
  dialog.
- Keep the confirmation unchecked and bound to the same immutable consent
  document. Additional-passkey and credential-reset ceremonies are unchanged.
- Continue showing only the effective controller; processor identities are not
  exposed or remodelled by this release.

## 3.9.7 — 12 August 2026

This patch release makes commissioning distinguish a healthy application from
DNS propagation and public-routing readiness.

### Deterministic deployment and resume

- Verify the Backend and the certificate-bound local TLS origin without using
  the VPS provider's DNS resolver.
- Store the signed deployment checkpoint before waiting for public DNS, so an
  interrupted or delayed routing check resumes without recreating services.
- Reconcile a missing checkpoint from signed release metadata, image digests,
  database invariants, running containers and local health.
- Keep DNS propagation inside the TUI with resolver agreement, the expected
  address, the observed answers and a bounded status for the current wait.

### Resolver-independent public health

- Require agreement from at least two of Cloudflare, Google and Quad9 before
  using an address for public HTTPS verification.
- Treat the host resolver as diagnostic information only and distinguish
  pending propagation, unavailable quorum, conflicting answers, incorrect
  addresses and unhealthy public TLS routing.
- Classify deployment, restore, restart, rotation, SMTP/DKIM, HA and validation
  checks as either local-origin or public-network checks.

### Interrupted witness-secret cleanup

- Remove temporary Wrangler secret files on success, error, interruption and
  SSH termination without reading or printing their contents.
- On resume, remove only strictly named, owner-controlled, mode-0600 regular
  files in the expected state directory and reject substituted paths.

## 3.9.6 — 12 August 2026

This patch release makes the host, container and service permission boundaries
used by HA, recovery and evidence explicit and self-validating.

### Runtime permission contracts

- Replace the frontend-only runtime preparation step with one idempotent
  contract for HA requests and results, compliance requests and receipts,
  private scheduler state and the generated Caddy policy.
- Reapply the contract around commissioning, deployment, container recreation,
  replication, restore, failover, secret rotation and frontend rebuilds.
- Validate the real Backend UID, Compose mount direction, Caddy and PostgreSQL
  access, host-private state and HA systemd sandboxes without reading secrets.
- Require every TUI-dispatched action to declare and pass an appropriate
  permission profile before and after a change.

### Safe HA protection failure and retry

- Prove that the Backend can perform an atomic queue write before opening a
  witness guard or committing a standby-protected mutation.
- Return bounded `HA_PROTECTION_UNAVAILABLE` reasons when the queue is missing,
  unsafe, not writable or fails an atomic write.
- Keep an indeterminate non-privacy mutation durable and locked, with a
  root-authorised **Retry standby protection** action that reuses the original
  operation and cannot duplicate its event, link or credential.

## 3.9.5 — 11 August 2026

This patch release accepts the stable evidence identities produced by the
separate one-time Desktop conversion tool without rewriting them.

### UUIDv5 evidence identities

- Accept canonical lower-case UUIDv4 and UUIDv5 evidence identities during
  setup import, supplied event creation and Desktop publishing.
- Preserve imported event and person identities exactly through publishing,
  deletion cases and signed evidence records.
- Continue generating UUIDv4 identities for new Server records and reject
  malformed, nil, noncanonical, invalid-variant and unsupported UUID values.
- Keep the database schema unchanged; the correction is confined to the API
  validation boundary.

### Email-only operational contacts

- Preserve optional person email addresses from Desktop setup exports and make
  them immediately available to activation and passkey-email workflows,
  without requiring an administrator to re-enter them.
- Continue accepting people without an email address while rejecting malformed
  addresses before any setup records are written.
- Retire the governance telephone field from the editable profile, imports,
  administration UI and newly generated notices. Privacy contact remains
  email-only.

## 3.9.4 — 11 August 2026

This patch release completes the first protected copy to a fresh HA peer and
keeps the joining node's progress truthful until that copy is locally healthy.

### Fresh peer service activation

- Start Caddy when a receiving peer has no running reverse proxy, even when
  its preconfigured domain already matches the incoming shared configuration.
- Keep an established, healthy Caddy instance running across ordinary copies
  when its effective domain is unchanged.
- Verify PostgreSQL, Backend and Caddy before writing the accepted receiver
  receipt, with bounded errors for each activation and health boundary.
- Preserve the existing atomic rollback to the previous database,
  configuration, evidence, secrets and service state after a failed copy.

### Accurate join progress

- Keep signed Node B in a resumable **Waiting for first verified copy** state
  after the one-time join code is consumed.
- Complete its setup checkpoint only after an accepted receiver receipt and
  healthy local database, Backend and Caddy services agree.
- Add executable Linux coverage for the fresh and established Caddy state
  matrix, first-copy reconciliation, missing services and malformed receipts.

## 3.9.3 — 11 August 2026

This patch release makes the first root commissioning write available on a
fresh two-node HA deployment without weakening normal writer fencing.

### Fresh HA ownership

- Persisted the generation-1 Node A ownership record in the same guarded
  transaction that creates the fresh root and evidence genesis, before public
  backend services start.
- Bound the narrow bootstrap to the active setup-v2 HA-primary checkpoint and
  validated the exact cluster, node and generation supplied by protected host
  configuration.
- Made retries idempotent only for an exact ownership match. Standalone setups,
  conflicting generations and unexpected cluster identities continue to fail
  closed.
- Added regression coverage for initialization order, valid retries, invalid
  identities and the standalone/HA boundary.

## 3.9.2 — 11 August 2026

This patch release makes clean two-node commissioning self-contained and binds
first account activation to the exact processing information shown to the
invitee.

### Fresh HA peer activation

- Added one idempotent, fail-closed host helper for the optional node-local
  Evidence Git credential mount. A fresh peer now creates the required empty
  mount target before Compose activation while preserving any configured token
  byte-for-byte.
- Kept the credential outside replication, snapshots, diagnostics and public
  evidence. Unsafe paths, symlinks and non-regular files stop activation with a
  bounded error.
- Applied the invariant during peer preparation and immediately before an
  accepted copy recreates the backend, so interrupted first copies can resume
  without leaving a database-only standby.

### Consent-bound first activation

- Added a concise published-governance disclosure to initial activation and an
  unchecked confirmation before WebAuthn registration begins.
- Bound the exact policy, controller, purpose, categories, audience and
  statement digests to the ceremony. The passkey, activation-link consumption,
  account activation, immutable consent row and pseudonymous evidence record
  now commit or roll back together.
- Kept additional-passkey and credential-reset ceremonies unchanged. Existing
  accounts are not backfilled and no age declaration is requested.
- Updated initial activation email to say that the activation page explains
  the processing and requests confirmation before a passkey is registered.

The stored proof identifies the exact published statement confirmed by the
account holder. It does not by itself determine whether consent is the correct
or legally valid basis for every controller relationship.

## 3.9.1 — 10 August 2026

This maintenance release corrects fresh signed commissioning discovered during
the first clean production two-node setup after v3.9.0.

### Fresh commissioning

- Replaced the circular blank-database bootstrap with bounded one-shot commands
  inside the exact signed backend image. The base schema is created without
  starting FastAPI, and root/evidence genesis is accepted only from an active
  fresh setup-v2 checkpoint and an independently verified empty database.
- Preserved normal HA write fencing for every application mutation; the narrow
  bootstrap exception cannot run against populated application, credential,
  governance, deletion or non-genesis evidence state.
- Made interrupted root/evidence genesis idempotently resumable while retaining
  strict evidence-key and chain verification.

### HA sequencing and TUI resume

- Kept replication, request-path and snapshot triggers dormant until the
  guarded first peer copy is accepted. Only the lease observer runs during the
  initial deployment-health and routing transition.
- Moved HA service activation after backend health so witness promotion cannot
  invalidate the deployment health window or hold the evidence advisory lock
  during initialisation.
- Kept the Node A join window open after displaying the Node B code. It now
  polls the witness, reports progress and proceeds automatically after pairing;
  Ctrl+C and lost SSH sessions retain the same safe checkpoint.
- Added regression coverage for empty-state refusal, exact genesis retries,
  deployment ordering, dormant commissioning services and persistent peer
  polling.

## 3.9.0 — 10 August 2026

This release promotes the reviewed `3.8.2` security-qualification baseline to
the next supported public Server release. The Server intentionally has no
interim `3.8.1` package release.

### Commissioning, governance and trust

- Replaced the recovery-only bootstrap with a resumable three-step root
  commissioning flow for recovery-key custody, controller identity and the
  first immutable governance publication.
- Added authoritative setup fencing, safe resume status and an instance-sealed
  commissioning receipt before normal administration becomes available.
- Added deployment-derived governance import/export, actionable preflight,
  exact previews and runtime-change notices for SMTP, retention and HA.
- Added event-scoped Desktop processor enrolment with root-passkey activation,
  rotation history and public-key-only Server custody.

### Deletion, retention and evidence

- Added processor-scoped Desktop work orders and signed policy, deletion and
  local-copy receipts, while keeping root authority over Server actions and
  final case closure.
- Replaced the global one-snapshot deletion gate with deletion-scoped,
  resumable removal of only pre-purge local recovery snapshots.
- Added explicit external-copy resolution, automatic deterministic case
  advancement, compact completed cases and per-case technical disclosures.
- Added a guarded retention scheduler with exact deadlines, signed automatic
  erasure queueing and HA writer fencing.
- Added complete-chain verification and portable evidence ZIP downloads with
  independently verifiable public material.

### High availability and recovery

- Added deterministic signed and unsigned deployment lanes pinned to exact
  commits, with interruption-safe commissioning and peer convergence.
- Added hybrid HA replication barriers for critical credentials, schedule
  links and deletion confirmation while retaining the documented periodic RPO
  for ordinary writes.
- Added durable protection operations, bounded witness guards, receiver marker
  verification, idempotent status polling and planned/automatic failover
  recovery.
- Hardened full-snapshot creation, verification, export, restore and rollback,
  including clear operator recovery instructions.

### Authentication, email and administration

- Added participant self-service additional-passkey links with root-configured
  availability and rate limits, while preserving unrestricted root/admin
  recovery controls.
- Made activation and passkey emails action-first and fully derived from the
  deployment's published controller, processor, country and contact facts.
- Reorganised root and organiser navigation, simplified account management and
  made loading/error states specific to the action being performed.
- Restyled public governance, security, licence, disclaimer and notice pages
  with self-hosted Source Sans 3 and consistent light/dark presentation.

### Security and release hardening

- Preserved least-privilege containers, strict CSP, protected secrets, signed
  manifests, SBOMs, immutable images and fail-closed public-material checks.
- Corrected public repository references, neutralised self-hosting examples and
  removed the hardcoded production metadata base.
- Rechecked the temporary `cryptography` exception. Version 50.0.0 is published,
  but WebAuthn's current `pyOpenSSL 26.3.0` dependency requires
  `cryptography<50`; the bounded non-applicable PKCS#7 exception therefore
  remains until a compatible dependency release permits the fixed lock.
- Expanded exact-SHA CI coverage for commissioning, HA, deletion evidence,
  governance, email privacy and public documentation.
