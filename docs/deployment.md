# Deployment

## Two deployment lanes

MP-OPT keeps production and fast iteration deliberately separate.

- `production` policy accepts only immutable GitHub releases whose manifest,
  operations archive, frontend archive and image digests pass signature and
  checksum verification.
- `test` policy additionally accepts an exact 40-character commit fetched from
  `origin`. It is intended only for installations containing disposable data.

The policy is stored in root-owned `/etc/mp-opt/deployment-policy`; it is not a
Compose environment variable and cannot be changed by deploying application
code. Select it through **Configuration > Deployment policy** in the management
TUI. A server with unsigned image overrides cannot be changed back to
`production` until the recorded signed baseline has been restored.

Under test policy, **Deploy and services > Deploy an exact pushed commit** shows
a copyable change plan before doing any work. Backend-only commits rebuild and
recreate only the backend. Frontend, proxy, database, tools, witness and
operations changes use the guarded full path. Migrations create an encrypted
full snapshot first. On a two-node installation, the active node disables
automatic failover, stages the peer, stages itself, refreshes replication and
witness observations, checks HA readiness, and only then reenables failover.

The status screen clearly labels these installations `MP-OPT UNSIGNED TEST
BUILD` and records both the exact test commit and the signed baseline. The same
menu provides rollback and an exact return to that signed baseline.

### Fresh unsigned commissioning

On a test-policy VPS, fresh commissioning captures the checkout's exact pushed
HEAD once. The resumable v2 setup state records the `unsigned` lane, that
40-character commit, and the verified signed release tag and commit used only
as a rollback baseline. A branch name is never stored or followed after setup
starts.

The signed baseline application is not started. MP-OPT builds the backend,
PostgreSQL, Caddy, tools and frontend from the pinned commit, creates the blank
database with those exact images, applies ordered migrations after stopping any
older backend, and writes an unsigned deployment receipt only after public
health passes. Root commissioning is presented only after the receipt, active
images, schema and public bootstrap endpoint agree.

On resume, those facts are reconciled before any checkpoint is trusted. A
matching receipt with stopped containers is recovered in place. A partial
fresh deployment is retried idempotently. A mismatched lane or commit stops
with a specific error and never falls back to the signed application. Closing
SSH therefore pauses the current action without changing the deployment target.

For fresh HA, Node A embeds the lane and pinned commit in the protected join
payload. Node B must use the matching policy and fetch that exact object. Node A
builds the images, verifies identical image identities on Node B, activates both
nodes and requires matching deployment receipts before root commissioning.
Witness publication remains conditional on an HA witness source change and the
guarded Cloudflare-token action.

## Public distribution invariant

The Server repository, stable GitHub Release assets, and the `backend`,
`caddy`, `postgres`, and `tools` GHCR packages are public distribution
surfaces. Fresh and replacement VPSs must be able to bootstrap and install a
signed release without a GitHub login or personal access token. Signatures,
checksums, and immutable image digests establish authenticity.

Before publishing the first public release, review the complete Git history,
branches, tags, existing release assets, workflow artifacts, and public
issue/PR content for private material. Rotate any credential that was ever
committed. Then make the repository and all four production packages public.
GHCR package visibility must be treated as permanent.

The release workflow checks repository visibility before expensive builds,
logs out of GHCR before inspecting every signed image, and verifies the final
release API and assets without credentials. A private source repository,
private package, mismatched release name, reused tag, or retired tag blocks the
release.

Signed deployments can also be run non-interactively by a trusted deployment
workstation:

```bash
/opt/masterplan/deploy/signed-deployment.sh v3.9.5
```

The tag spelling is canonical and case-sensitive: `vMAJOR.MINOR.PATCH`. Release
names use the identical value. Tags and release names are never overwritten;
`v3.4.0` is permanently retired and the release prepared by this source tree is
`v3.9.5`.

Production installation, standalone-to-HA migration, node replacement, and
full-loss recovery all start in the same resumable commissioning interface.
Follow [Production setup](setup.md); do not assemble an installation from the
individual low-level scripts below.

## Production Shape

The production deployment uses the FastAPI backend, a static Next frontend, a
PostgreSQL database, and reverse-proxy configuration from `infra`. The scripts
under `deploy` and the root helper scripts exist to make installation and
updates repeatable.

New installations use the Caddy container and immutable digest-pinned images
from a signed stable release. Verify the bootstrap through the signed release
manifest before running it as root (use a locally version-pinned `cosign`):

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

Reconnect and run `mp-opt` to resume any incomplete external checkpoint.
`configure-production.sh` and `deploy/deploy.sh` remain implementation and
development entry points; operators do not need to sequence them manually.

## Configuration

Start from `.env.example` and configure:

- Public server URL and allowed origins
- Session and security settings
- Non-secret SMTP and VAPID contact settings

The supported production wizard generates the database password into
`secrets/database_password`. PostgreSQL reads that file through
`POSTGRES_PASSWORD_FILE`; the backend reads the same mounted secret and builds
its connection URL inside the process. Neither `DATABASE_URL` nor
`POSTGRES_PASSWORD` belongs in production `.env`. The guarded legacy migration
refuses mismatched sources before removing either setting.

The wizard also creates the independent `secrets/ip_hmac_key`. It keys only the
daily IP pseudonym HMAC and must never equal the general application secret.
Private values must stay out of Git, diagnostics and ordinary Compose output.
Keep `.env` and non-runtime secret material mode `0600`. Runtime-mounted
backend secrets are owner-writable and backend-group-readable (`0640`) inside
the mode-`0700` canonical secret directory; the fixed group ID `10001` has no
host directory traversal and exists solely for the unprivileged container.

The optional Evidence Git token is deliberately node-local and is never
replicated or included in a snapshot. Before Compose activation, management
code creates a missing token mount as an empty regular file, preserves an
existing configured token byte-for-byte, and rejects symlinks or unsafe file
types. This invariant also applies to a fresh HA peer receiving its first copy.

The first-run wizard selects the final topology explicitly. HA node names,
peer identities, direct TLS, DNS routing and replication settings are generated
and validated automatically; node-local state remains under `/etc/mp-opt-ha`.

### Root Bootstrap

The deployment script creates `secrets/root_bootstrap_token` when it is absent,
using a valid `ROOT_BOOTSTRAP_TOKEN` from `.env` or a securely generated value.
Read it on the server and enter it only on the root bootstrap page:

```bash
cat secrets/root_bootstrap_token
```

After the root passkey has been registered, clear the file and redeploy. Keep
the empty file in place because Docker Compose still requires the bind source:

```bash
: > secrets/root_bootstrap_token
./deploy/deploy.sh
```

The deploy script preserves an existing empty file, keeping bootstrap disabled.
Restoring bootstrap access must remain an operator-controlled recovery action.

## Build Checks

Before deploying a change:

```bash
bash -n manage.sh configure-production.sh deploy/deploy.sh deploy/setup-server.sh deploy/management/*.sh deploy/ha/*.sh
shellcheck --severity=error manage.sh configure-production.sh deploy/deploy.sh deploy/setup-server.sh deploy/management/*.sh deploy/ha/*.sh
python -m compileall backend/app deploy/ha deploy/management
python -m unittest discover -s deploy/ha/tests -p 'test_*.py' -v
python -m pip_audit -r backend/requirements.txt
npm --prefix web ci
npm --prefix web audit --audit-level=high
npm --prefix web run lint
npm --prefix web run build
npm --prefix web run docs:typedoc
mkdocs build --strict
```

Also validate both Compose models, both Caddy configurations, the Cloudflare
witness type-check, and the PostgreSQL schema contract. CI performs these checks
and scans the backend, Caddy, PostgreSQL, and commissioning-tools images for
every HIGH or CRITICAL OS and application vulnerability.

The development deployment path performs the production web dependency audit.
Signed production releases are built and audited in CI before publication. For
a blank database, the backend first
creates the canonical base tables, after which every committed SQL migration is
applied in filename order. Existing databases skip base initialisation.

If base initialisation or a migration fails, the deployment stops before
starting the new public application stack and prints the relevant service logs.

The GitHub Actions CI also runs server-focused suites from the external Testing
repo on merge requests and pushes to `main`.

## Documentation Deployment

The docs workflow builds MkDocs Material with generated Python and TypeScript
API documentation. On pushes to `main`, the static site is uploaded and deployed
through GitHub Pages.
