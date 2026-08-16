# Production setup

MP-OPT production commissioning is driven by one resumable terminal interface.
It supports a fresh standalone server, a fresh two-node pair, conversion from
standalone to HA, standby replacement, and full-loss recovery. Operator-created
node names, manual SSH key exchange, hand-written environment files, local
image builds, and Cloudflare load balancer construction are not part of the
normal setup.

## Before starting

Use a fresh Ubuntu 22.04 or 24.04 VPS with root SSH access. For HA, use two
VPSs. Have these values ready:

- the public application hostname;
- SMTP host, port, username, provider token, sender address and DKIM selector,
  if activation email is wanted;
- for HA, a Cloudflare zone containing the hostname;
- the Cloudflare account ID which owns that zone and the HA Worker;
- a temporary Cloudflare token scoped to the account with **Workers Scripts
  Edit**, used to deploy the witness and set its secrets;
- a long-lived token scoped to **Zone Read** and **DNS Edit** for only that
  zone.

No GitHub credential is required for installation or release downloads. The
bootstrap, signed release assets, and digest-pinned production images are
public and downloaded anonymously. The optional private Evidence archive is a
separate, disabled-by-default controller decision and uses only the protected
Fine-grained GitHub personal access token workflow documented after setup.

The long-lived DNS token is installed only as a Worker secret. It is never
stored on either VPS. The temporary deployment token is discarded when the
Worker checkpoint finishes. New HA installations use ordinary DNS-only A/AAAA
records at TTL 60 and do not require Cloudflare Load Balancing or Origin CA.

## Bootstrap a VPS

Choose an immutable stable release. With a locally installed, version-pinned
`cosign`, verify the release identity and the bootstrap digest before executing
anything as root:

```bash
TAG=vMAJOR.MINOR.PATCH
BASE="https://github.com/Brian-Funk/masterplanOptimiserV3---Server-Public/releases/download/${TAG}"
curl -fL "${BASE}/release-manifest.json" -o /tmp/mp-opt-release.json
curl -fL "${BASE}/release-manifest.bundle" -o /tmp/mp-opt-release.bundle
curl -fL "${BASE}/mp-opt-setup.sh" -o /tmp/mp-opt-setup.sh
cosign verify-blob --bundle /tmp/mp-opt-release.bundle \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github\.com/Brian-Funk/masterplanOptimiserV3---Server-Public/\.github/workflows/release\.yml@refs/(tags/v[0-9]+\.[0-9]+\.[0-9]+|heads/main)$' \
  /tmp/mp-opt-release.json
COMMIT="$(jq -er --arg tag "$TAG" \
  'select(.tag == $tag) | .commit | select(test("^[0-9a-f]{40}$"))' \
  /tmp/mp-opt-release.json)"
printf '%s  %s\n' "$(jq -r .bootstrap.sha256 /tmp/mp-opt-release.json)" \
  /tmp/mp-opt-setup.sh | sha256sum -c -
less /tmp/mp-opt-setup.sh
sudo bash /tmp/mp-opt-setup.sh \
  --repository-url https://github.com/Brian-Funk/masterplanOptimiserV3---Server-Public.git \
  --ref "$COMMIT"
```

Do not substitute `main`, `master`, or a moving branch name. The bootstrap
rejects them.

The explicit public repository URL also keeps the immutable `v3.9.14`
bootstrap asset usable after the source repository transition. Later bootstrap
assets use the same public URL by default.

It validates Ubuntu, installs Docker and the small host dependencies, creates
the `deploy` account, obtains the management checkout, installs the `mp-opt`
launcher, and opens the commissioning TUI. Production application images,
frontend files, and operational scripts then come from the newest stable
release. Their keyless signatures, source identity, signed manifest and every
digest are verified before anything is activated. The VPS does not compile the
application or install Node.js: the short-lived Wrangler invocation runs from
a separately signed commissioning-tools image.

The dedicated `deploy` account administers Docker and therefore is already
root-equivalent. The bootstrap records that explicitly in a validated
passwordless sudo rule so guarded systemd and HA file operations remain
non-interactive. Protect its SSH key as an administrator credential.

Every completed checkpoint is recorded without credentials. If SSH closes or
an external step is still propagating, reconnect and run:

```bash
mp-opt
```

Choose **Resume commissioning**. Completed destructive or network operations
are not repeated. Remote Worker bootstrap, node join, and standby-replacement
requests are exactly retryable: setup records their protected request before
the remote commit and accepts only the same request on retry.

Whenever you must copy something out of setup—bootstrap or join codes,
recovery URLs/fingerprints, DNS records, snapshot transfer commands, or legacy
routing identifiers—the full-screen interface temporarily clears and displays
ordinary selectable terminal text between explicit copy markers. Copy it, run
any workstation/provider step in a second terminal, then press Enter. The
screen and scrollback are cleared before the TUI returns. If SSH closes before
Enter, that checkpoint is not acknowledged and the same value is displayed
again after resume.

## Fresh single-node server

Choose **Fresh single-node server**. The TUI asks only for the application
domain/name, database password preference, VAPID contact, and optional SMTP
settings. It then pauses while you point the hostname's DNS-only A record at
the VPS and verifies public resolution before deployment.

The TUI deploys the signed release and obtains public TLS automatically. It
then displays the root bootstrap URL and code, waits for successful root
passkey registration, and retires the bootstrap secret before setup can
continue. Sign in with that root passkey to open the root-only browser
recovery-key generator. Until the private key download is acknowledged, that
root session is restricted to the recovery page and logout; losing the
bootstrap code after passkey registration does not prevent completion. The TUI
stores only its public `age1...` recipient,
then validates Compose/database/Caddy/permissions, sends a real SMTP test when
SMTP is enabled, and checks visible SPF, DKIM and DMARC records. The private
`AGE-SECRET-KEY-...` must be downloaded and backed up twice outside the VPS.

## Fresh two-node HA server

Run the bootstrap on both VPSs. On the VPS that should initially hold live
traffic choose **Fresh HA pair: create Node A and a join code**. Node IDs are
fixed internally as `node-a` and `node-b`; there is nothing to name.

The TUI deploys the witness, installs its scoped DNS token, creates node-local
SSH and age identities, and displays a one-time join code valid for 15 minutes.
The code contains public pairing metadata and a short-lived secret, never
application data or a private key.

On the other VPS choose **Join an existing HA pair with a one-time code**, paste the code, and wait. Return to
Node A and resume. The TUI verifies SSH host keys in both directions, installs
the exact same signed release on both nodes, starts direct DNS-challenge TLS,
routes the public DNS record to Node A, creates and deeply verifies a complete
encrypted copy on Node B, synchronises the public snapshot recipient, verifies
SMTP from both origins, and checks all HA readiness gates.

When those gates pass, commissioning records automatic-failover readiness but
leaves automatic failover disabled. Test planned handover and both failure
directions first, then enable it explicitly through the guarded High
availability action. The supported automatic mode uses a two-minute primary
loss threshold and a five-minute verified-copy target. No provider power API,
load balancer, origin certificate, manually copied peer identity, or custom
node name is required.

The first encrypted peer copy establishes safe empty mount targets for optional
node-local credentials before starting the standby backend. Those credentials
are not copied from Node A. An unsafe or substituted path stops the receiver
with a resumable error instead of leaving an unexplained partial activation.

## Local commissioning automation adapter

The graphical TUI remains the normal operator interface. A coordinator running
on the VPS through the protected `deploy` account can inspect and advance the
same durable state with `mp-opt setup`:

```bash
mp-opt setup validate --json
mp-opt setup plan --mode ha-primary-new --lane signed --json
mp-opt setup start --mode ha-primary-new --lane signed --json
mp-opt setup status --json
mp-opt setup events --jsonl --after 0
printf '%s\n' '{"format":"mp-opt-commissioning-input-v1","checkpoint":"signed_baseline_verified","idempotency_key":"run01-baseline","values":{"tag":"v3.9.14","commit":"<exact-40-character-release-commit>"}}' \
  | mp-opt setup advance --input-stdin --json
```

For an unsigned private-lab run, stage the build-once candidate bundle through
stdin before `application_deployed`:

```bash
mp-opt setup stage-candidate --commit "$COMMIT" --sha256 "$BUNDLE_SHA256" \
  --input-stdin --json < candidate-bundle.zip
```

The accepted bundle is the private lab's non-release-eligible five-file
contract: candidate index, candidate manifest, frontend and operations
archives, and bootstrap script. All assets and four GHCR images are bound to
exact SHA-256 digests. Registry credentials are supplied only in the bounded
`application_deployed` stdin document and are removed after the pull. They do
not enter status or events.

Only one TUI or machine coordinator may hold the commissioning lease. Each
`advance` call can perform at most the exact next checkpoint named in its
schema-validated stdin document. Fresh standalone configuration, fresh HA
witness setup, Node B joining and SMTP verification have structured input
contracts. Browser commissioning and recovery ceremonies remain waiting until
their authoritative receipt is available. Every input carries an idempotency
key; a completed transition can be replayed only with the same key. `reconcile`
records only completion that existing local facts or signed receipts prove.
Standalone-to-HA conversion uses `stage-migration`, a digest-bound raw
`artifact` download and an explicit confirmation advance. Full-loss recovery
uses a bounded raw `stage-recovery` upload followed by the `imported` and
`restored` advances. Replacement pairing uses `replace-primary` on the
surviving active holder and `replace-node` on the blank VPS. The holder must
prove that automatic failover is disabled before it opens the replacement
code. Private recovery identities are consumed from protected stdin documents
and never enter status, errors, events or receipts.

Provider identity is also fail-closed. HA commissioning records the exact
Cloudflare account, Worker name, Worker URL and zone in a protected local
receipt. Cleanup must match that receipt and the coordinator's independently
observed account, Worker and zone before Wrangler is allowed to delete the
script. A missing or mismatched receipt requires operator recovery; cleanup
does not derive or guess a Worker name.

Full-loss recovery is accepted by the machine interface only on a verified
blank host. A protected authorization binds the setup start, imported snapshot
receipt and recovery recipient before configuration is installed. It permits
deterministic resume of that one operation, but cannot authorise overwriting an
unrelated live installation. SMTP delivery similarly records one provider-
accepted test send for the exact idempotency key, recipient digest,
configuration digest and correlation ID before waiting for DNS. DNS retries do
not send the test message again.

Exact deployment lifecycle operations use another stdin-only contract:

```bash
printf '%s\n' '{"format":"mp-opt-deployment-lifecycle-input-v1","action":"signed-upgrade","tag":"v3.9.14","commit":"<exact-40-character-release-commit>","idempotency_key":"run01-signed-upgrade","values":{}}' \
  | mp-opt setup deployment --input-stdin --json
```

Signed upgrade and rollback require the exact signed tag and commit. Candidate
advance and rollback require a previously staged or retained exact candidate,
set `tag` to `null`, and carry registry credentials plus the matching recovery
identity inside `values`. Before an established candidate advance, the Server
creates and deeply verifies an encrypted rollback snapshot and retains the
previous exact candidate bundle. Candidate rollback accepts only that recorded
prior commit. Automatic failover remains disabled throughout these lifecycle
operations. In an explicit test-policy candidate deployment, seven real
commissioning transitions expose one-shot adjacent fault hooks at four crash
boundaries. Production policy rejects the hook before creating state. The
machine status publishes the exact transition list; the private laboratory
must match it before arming a fault. Packaged PTY acceptance remains reported
as unavailable and is not inferred from machine-interface coverage.

Two narrowly scoped handoffs are available at their exact pending checkpoint:

```bash
mp-opt setup handoff --kind root-bootstrap
mp-opt setup handoff --kind ha-join
```

These commands write the raw secret value and a trailing newline to stdout.
Treat stdout as secret: pass it directly to the intended browser or joining
node, do not log it, and discard it after use. The root bootstrap value is
unavailable after root registration. An HA join code is short-lived and is
consumed or invalidated by the witness. Handoff values never appear in status,
errors, event journals, or diagnostics.

## First participant activation

An initial activation email opens a page containing the effective published
controller, processing purpose, operational data categories, authenticated
audience and immutable privacy/rights links. The user must actively confirm the
exact statement before the browser is asked to register a passkey. Successful
registration consumes the one-time link, activates the account and records the
statement and policy digests as one transaction; a failed passkey or evidence
append records none of them.

This confirmation is requested only for first activation. Additional-passkey
and credential-reset links retain the existing account record and do not ask
again. No age declaration is presented. The in-product **Delete my data**
action remains available after activation, with accountable erasure handled by
the deletion-evidence workflow.

## Other lifecycle choices

- **Convert this existing standalone server to Node A** first requires a
  freshly verified off-VPS portable snapshot, then uses the same one-time Node
  B join flow. Existing application data is preserved.
- **Replace a lost standby** revokes whichever non-primary node is missing and
  emits a new 15-minute join code. The old VPS must be powered off.
- **Recover after complete server loss** is offered only on a blank VPS. It
  imports one portable encrypted full snapshot, records that exact import,
  verifies its receipt and every payload hash with the browser-generated
  private identity, and restores shared configuration before the database.
  Old node-local HA, Compose override, and host-proxy topology are deliberately
  ignored; recovery first returns as a standalone server. Sessions and one-time
  activation ceremonies are revoked, while registered passkeys, public
  schedule links, and publisher credentials remain valid.
- **Migrate a legacy Cloudflare load balancer** upgrades both nodes and the
  Worker, changes routing to DNS-only, verifies direct TLS at both origins, and
  records a seven-day rollback window. Deletion is a separate TUI checkpoint.

## Deliberate manual checkpoints

The TUI stops only when a human or an external provider is genuinely required:

1. publishing/confirming the DNS record;
2. creating the two least-privilege Cloudflare tokens;
3. saving and independently backing up the browser-generated recovery identity;
4. publishing SMTP-provider SPF, DKIM and DMARC records;
5. registering the root passkey;
6. powering off a lost standby before replacement;
7. selecting a portable snapshot and supplying its recovery identity after
   total server loss.

Secrets are entered in hidden fields, never put on a command line, and are not
written to the resumable checkpoint.

## Local development

Developers can still run the backend and frontend directly and select **Build
and deploy the current checkout**. That source-build path is explicitly a
development/diagnostic operation; normal production setup and updates use
signed releases.
