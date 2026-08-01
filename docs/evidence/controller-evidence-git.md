# Optional private Evidence Git archive

Every Masterplan deployment keeps its local, signed, hash-chained evidence ledger as the authoritative record. A deterministic, self-contained portable bundle is the primary export. Git is an optional private remote archive of that bundle and its digest. It is not the primary ledger and it is not evidence that an external provider physically deleted data.

Automatic archival is disabled by default. When a controller enables it, the ordinary Server process runs one background uploader backed by a durable database queue and lease. There is no second service, endpoint or port. Local ledger writes and manual bundle exports remain available when GitHub is unavailable or archival is disabled.

## Portable bundle

The Server verifies the complete local ledger and controller trust declarations before creating a bundle. The bundle includes:

- the controller, processor and deployment trust declarations;
- the complete signed chain through one exact head digest;
- deterministic Markdown and accessible HTML summaries;
- public verification keys and integrity metadata;
- offline verifier source for controller inspection.

The controller prepares and signs the public trust declarations outside the Server, then installs the complete `trust/` and `instances/<instance_id>/trust/` structure under the protected `/evidence/controller-trust` area. Controller private keys never enter the Server. Before accepting a token or enabling automation, the TUI performs a temporary local bundle preflight and requires its verified controller ID to match the entered controller ID.

The integrated uploader never executes code from a bundle. It uses only its protected installed verifier. Identical verified input produces identical bundle bytes and SHA-256 digest. A bundle contains no token, private key, database, backup, name, personal email address, task content or schedule.

## Fine-grained GitHub personal access token

This is the only supported authentication mode. Classic personal access tokens are not supported.

Scope the token to the one private controller Evidence repository and grant only:

- Metadata: read;
- Contents: read and write;
- Pull requests: read and write;
- Actions or Commit statuses: read, only for exact-SHA check monitoring.

Do not grant Administration write, Workflows write, Secrets access, Environments write, Releases write, Issues access, organisation administration or protected-branch bypass. Do not scope the token to Evidence-Public. Use a defined expiry, rotate it regularly, prefer a dedicated GitHub service account where practical, and revoke it immediately after suspected VPS compromise.

The controller enters the token only through masked input in the existing management TUI. The TUI validates the repository owner, name, stable numeric ID, privacy, fork status, default branch and reported branch protection before atomic owner-only storage. It can test readiness, replace or rotate the token, delete it, disable automatic archival and retry safe failed submissions.

The token is read from the protected secret file for each provider operation. It never appears in command-line arguments, URLs, output, logs, tracebacks, the ordinary database, the evidence ledger, portable bundles, summaries, application backups or diagnostics. The database holds only non-secret queue state and metadata such as the repository ID, token fingerprint, exact PR head SHA and merge SHA.

## Protected repository

The controller configures the private repository rules separately. The token must not configure or weaken them. Protected `main` must require:

- pull requests;
- the exact check named `Evidence verification`;
- the exact check named `Ingestion path validation`;
- branches to be up to date before merge;
- blocked force pushes and branch deletion;
- no token-owner bypass;
- no automatic workflow changes.

The template workflow has read-only repository permission. `Evidence verification` uses the protected default-branch verifier to check every bundle, digest, controller identity and chain relationship. `Ingestion path validation` requires a PR to add exactly these two files under one new identity directory:

```text
instances/<instance_id>/bundles/<bundle_id>/evidence.bundle
instances/<instance_id>/bundles/<bundle_id>/bundle.sha256
```

The uploader refuses a public repository, a fork, Evidence-Public, a repository identity mismatch, an unprotected default branch, a changed base, a changed PR head, failed checks or an identity mismatch between the credential, controller, instance and bundle.

## Upload lifecycle

For each queued chain head, the integrated uploader:

1. Generates and locally verifies the portable bundle.
2. Reads the Fine-grained GitHub personal access token from protected storage.
3. Creates an ingestion branch from the observed protected default-branch SHA.
4. Uploads only the bundle and digest.
5. Opens a pull request and records its exact head SHA.
6. Monitors checks only for that exact SHA.
7. Requests a normal protected merge after the checks pass.
8. Deletes the merged ingestion branch.
9. Appends a local instance-signed receipt containing the non-secret repository ID, PR number, PR head SHA and merge SHA.
10. Clears transient credential references as far as the runtime permits.

Retries use bounded exponential backoff with deterministic jitter and honour provider rate limits. Once a pull request exists, a retry resumes that pull request and exact head instead of creating a duplicate. HA workers use durable leases and only the current writer may submit.

## Manual fallback

The controller can export and verify a portable bundle without a token. On a trusted workstation, stage it into a clone with:

```text
python tools/portable_bundle.py stage-archive \
  --bundle /path/to/accountability.evidence \
  --archive /path/to/private-evidence-clone
```

Inspect the two staged files, commit and push through the repository's normal protected pull-request workflow. The staging command is idempotent and rejects reuse of a bundle identity with different content.

## Verification limits

A valid signature proves that the identified key signed the exact statement. It does not prove physical deletion, the absence of copies outside controlled systems, physical-world truth or legal compliance. Providers, countries, transfers, enabled features and retention are declarations made per deployment and are not inferred by the software.
