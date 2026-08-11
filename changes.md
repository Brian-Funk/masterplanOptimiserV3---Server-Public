# Changes

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
