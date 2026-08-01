# Security

## Authentication

The server uses passkey-based authentication and short-lived exchange codes.
Session cookies are HTTP-only, CSRF tokens are required for mutating requests,
and sensitive admin operations require recent re-authentication.

## Authorisation

Backend dependencies and middleware enforce role and event boundaries. Root
admins can manage global configuration. Event admins and issuers are scoped to
their events. Regular users can only access permitted calendar data.

## Publish Secrets

Each event has a publish secret used by the desktop app. The server stores a
hash of the secret rather than the raw value. Publish requests must include a
valid secret before replacing event data.

## Request Hardening

The backend limits request body size, rejects unexpected content types for JSON
mutations, and logs requests with basic timing information. Runtime settings
control re-authentication age and related security thresholds.

The production API image runs as a fixed unprivileged user with all Linux
capabilities dropped, a read-only root filesystem and `no-new-privileges`.
Only its bounded temporary filesystem and the HA replication request directory
are writable.

## Supply Chain

Python production dependencies use a reviewed complete constraint set. npm
dependencies use committed lock files. CI audits both dependency families and
scans every affected runtime image for high and critical vulnerabilities.

Each signed production release includes CycloneDX SBOMs for the source tree and
all four runtime images. Their SHA-256 digests are covered by the signed release
manifest. Dependabot proposes scheduled dependency, image and workflow updates;
an update is not shipped until the normal validation and release gates pass.

The complete purpose, custody, rotation, revocation and compromise catalogue is
machine-validated and described in the [cryptographic inventory](cryptographic-inventory.md).

Provider and controller-workstation facts are recorded through the protected
[storage security checklist](storage-security-checklist.md). Repository source
does not claim to verify external disk, snapshot, account or deletion controls.

## Audit And History

Administrative actions are written to the audit log. Published schedules are
snapshotted so event data can be inspected and restored when needed.

Raw client IP addresses are not stored in application sessions or audit rows.
Valid addresses are canonicalised and pseudonymised as a daily HMAC-SHA-256
using a dedicated Docker secret. The retained value carries only a non-secret
key ID and a truncated digest. It remains pseudonymous personal data and is
deleted with the associated session or audit retention lifecycle. Rotating the
key through the management TUI deliberately ends continuity with older values.

Uvicorn's raw access log is disabled. The application emits a bounded
JSON-shaped request record without query strings or raw addresses, and Docker
rotates each container's local log at five 10 MiB files. The complete
controller-facing source, retention and external-provider checklist is in the
[log inventory](log-inventory.md).

## Privacy

The root publishes an instance-specific, versioned governance notice. Drafts
remain private and the public legal centre never substitutes the software
author for the actual controller. Offline schedule storage requires explicit
browser opt-in.

GDPR endpoints allow users to request deletion after recent re-authentication.
The controller accepts one deletion case that coordinates server data, the
matching desktop record, HA peers, replacement snapshots, superseded backup
packages and named processor actions. A case cannot complete while a required
action remains unresolved. Only a non-identifying accountability receipt is
retained after the identity and event-linked copies are deleted.

These controls support GDPR and Swiss FADP readiness. They are not a compliance
certification or legal advice.
