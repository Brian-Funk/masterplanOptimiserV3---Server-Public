# Deletion accountability

MP-OPT coordinates personal-data and whole-event erasure across the desktop
source, server, HA peer and recovery copies. It records bounded signed evidence
of the controlled steps. A signature proves who or which instance approved
exact bytes and that those bytes have not changed. It cannot prove physical
erasure from every storage device or provider.

Signed evidence is mandatory. The application refuses an evidence-backed
transition if its protected Ed25519 instance key, evidence store, management
audit head or linked ledger is unavailable or inconsistent. There is no
advisory, disabled, imported-attestation or exception-completion mode.

## Current data boundary

Publish payloads accept only the current typed contract. Unknown properties
are rejected. Structured fields that indicate health, medical, disability,
dietary, allergy, political, religious, safeguarding, disciplinary, ethnicity,
sexual, trade-union, criminal or unrelated private profiling are rejected.
These categories are unsupported by this release, not silently retained for
organiser review.

The live account or event data is deleted. The durable case and evidence chain
use random instance, event, subject, case and action identifiers. They do not
contain names, email addresses, event titles, free-text reasons, host paths or
private keys. The controller must still define and review an appropriate
accountability-record retention period.

## Strict erasure flow

1. A user submits a personal-data request, or root starts a whole-event case.
2. The accountable workflow records the request. For a personal request the
   account is disabled and all sessions and activation links are revoked as the
   controlled erasure begins.
3. When the account is linked to a desktop person, the server issues an
   event-bound work order. The desktop claims it with a short-lived bearer
   capability, deletes the matching local person or event and commits a
   privacy-safe report to its encrypted outbox in the same SQLite transaction.
   A genuinely server-only account is marked as such and does not invent a
   desktop prerequisite.
4. The desktop sends the report. Failed delivery remains retryable even if a
   whole-event deletion removed the last local event.
5. Only after the required report is accepted, or immediately for a verified
   server-only scope, can the server delete its live account, participant,
   task-reference and event-scoped data.
6. In HA, the current privacy action must be included in a bundle accepted by
   the peer at the expected generation.
7. The management workflow creates, deeply verifies and exports a clean
   encrypted replacement snapshot. The browser accepts only its locally
   verified receipt, never a package ID or digest typed by an operator. The
   host then removes only local snapshots proven to predate the covered purge;
   later clean recovery points do not re-block the case.
8. Every known pre-deletion external package in the inventory must be resolved. Exact external
   actions reported by the desktop, such as a calendar-provider copy or an
   untracked export, must also be confirmed exactly.
9. The server freezes an immutable checklist containing the pseudonymous scope,
   Server receipt, every required event-processor receipt, peer confirmation and
   recovery resolution. Root reviews and authorises that exact checklist with a
   passkey; root cannot substitute for a missing processor.
10. The instance evidence key seals the final record only while every
    prerequisite remains true.

There is no `complete_with_exceptions`. An unresolved copy produces restricted
retention with a review date and keeps the case open.

## Signed checklist and legal meaning

The passkey-bound checklist is a technical equivalent of a signed operational
checklist: it binds the approver, role and exact receipt set without asking the
server to trust an uploaded statement. A separately signed controller-processor
document can also support organisational accountability, but it should not
replace the machine checks for local deletion, peer acceptance and clean backup
replacement. Neither form proves facts outside the signer's knowledge.

## Recovery safety

The database and evidence filesystem travel in the same encrypted HA bundle.
Database-bearing snapshots contain the exact signed chain head and restoration
fails before replacing live data when that head is missing or incompatible.
Privacy-action receipts prevent an older recovery copy from being treated as a
safe current baseline. After erasure, create the clean replacement snapshot and
remove every superseded external copy according to the controller's documented
procedure.

The public-only evidence export contains signed records and public keys. It
never includes the instance signing key, recovery identity, participant data or
application backup. A controller-owned archive is useful independent evidence,
but it is not proof of provider-side deletion.
