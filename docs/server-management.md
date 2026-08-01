# MP-OPT_SERVER Management

`MP-OPT_SERVER` is the production administration interface for an operator
connected to the VPS through SSH. Run it from the repository:

```bash
cd /opt/masterplan
./manage.sh
```

The `mp-opt` launcher opens the same menu after it has been installed by VPS
setup or deployment. Windows batch files do not modify production state.

The complete interface uses full-screen terminal windows, including the main
area selector, nested menus, confirmations, reports and deployment output.
`dialog` is preferred, with `whiptail` and a plain terminal interface retained
as recovery fallbacks. Run `mp-opt` directly from an interactive SSH session;
redirected or non-interactive execution is intentionally rejected.

Windows use the terminal-aware **Large** profile by default. Choose
**Maintenance and diagnostics > Change the terminal interface size** to switch
between Compact, Standard, Large and Maximum. The choice is stored only for the
current VPS operator and applies to the next window immediately.

## First production setup

When `.env` does not exist, the menu opens the production wizard. It collects
the application domain, passkey display name, internal database credentials and
VAPID contact. Application, VAPID and root-bootstrap secrets are generated into
mode `0640` Docker secret files owned by the operator and readable only by the
fixed unprivileged backend group. The database password is stored only in
`secrets/database_password`, and a separate IP-pseudonymisation key is stored
only in `secrets/ip_hmac_key`. Neither value is written to `.env`.

SMTP activation email is optional. If skipped, its non-secret settings remain
blank and `secrets/smtp_token` is created as an empty protected file. If
configured, the provider token is entered twice without echo and is never
written to `.env`.

After deployment, commissioning pauses until the root passkey is registered.
The browser recovery-key generator is then available only to a signed-in root
session with recent passkey verification. Generate and back up the key pair on
a separate trusted computer; only the public recipient beginning with `age1`
belongs on a VPS. The exact generation, two-copy backup, HA synchronization and
rotation procedure is in
[Snapshot recovery key](snapshot-recovery-key.md).

## Menu areas

- **System overview** shows health, versions, storage and recovery readiness.
- **Deploy and services** updates, rebuilds, starts, stops and restarts services.
- **Configuration** manages SMTP, runtime security, VAPID, application secrets,
  database credentials, the dedicated IP HMAC key and the advanced domain
  workflow.
- **Snapshots and recovery** creates, verifies, restores, exports, imports and
  deletes encrypted database, secrets and full recovery archives.
- **Root administrator recovery** resets only root authentication state and
  creates a replacement bootstrap code.
- **Database** provides statistics, snapshots, restore and complete wipe.
- **High availability** configures two symmetric nodes, encrypted scheduled
  point-in-time replication, the external writer lease, guarded switchover and
  isolated bundle/write-fencing self-tests.
- **Logs** shows recent, time-bounded or live backend, database and Caddy logs.
- **Maintenance** validates configuration, creates redacted diagnostics and
  manages safe Docker build-cache cleanup.

## Viewing logs

Recent and time-bounded log selections open in a scrollable window and remain
visible until **Return** is selected. Empty selections and command failures are
shown explicitly instead of returning silently to the menu.

Live logs open in a full-screen tail view. Select **Return**, press Escape or
press Ctrl+C to stop the log producer and return to the Logs menu. The viewer
uses protected temporary files, strips terminal control sequences and removes
the files when the view closes. Log contents are not copied into the management
audit log.

Caddy log and status actions automatically follow the active deployment. A
container deployment reads Compose logs, while a host deployment reads the
systemd journal. Validation reports `unavailable` rather than attempting the
wrong topology when neither configuration is present.

## Encrypted snapshots

Snapshots are stored under `~/masterplan-snapshots` in directories named with
UTC time, type and the operator's safe name. Each directory contains:

- `snapshot.tar.age`, the encrypted payload;
- `archive.sha256`, the encrypted archive hash;
- `receipt.json`, non-secret type, time, size, storage state and recovery-key
  id metadata.

The encrypted payload contains a versioned manifest with SHA-256, size and mode
for every file. Database dumps are additionally checked with `pg_restore
--list`.

Deep verification asks for the operator-held age private identity through a hidden
prompt. It is written only to a temporary memory-backed file, used to decrypt
and compare every manifest entry, then cleared and removed. An archive is not
accepted as a destructive-operation rollback point until this succeeds.

In HA mode both nodes use one long-lived cluster-level public snapshot
recipient. Configure it from the current lease holder; the CLI stages it on the
peer, atomically installs it on both nodes and compares the public SHA-256
fingerprints. This is not the same key as either node's private HA replication
identity. The key is not rotated by deployments, failover or routine
maintenance.

An operator-requested key change is a guarded consolidation, not a simple
configuration edit. With the old identity, every managed local and peer copy
is re-encrypted and deep-verified before originals are removed; SSH archive
copies are included when configured. Without it, old ciphertext remains
explicitly unavailable. A new deeply verified baseline plus either a verified
SSH archive or an operator-confirmed portable workstation SHA-256 is mandatory.
See [Snapshot recovery
key](snapshot-recovery-key.md) for the exact workflow.

One snapshot can be exported as a single `.mpopt-snapshot` file for protected
workstation storage. The menu generates commands for Windows Command Prompt,
PowerShell, Linux, macOS or a generic SFTP client. Import validates a fixed
member allowlist, file modes, sizes and hashes before installing the snapshot;
it still requires a separate deep verification with the matching private age
identity before restore. See [Portable snapshot and disaster
recovery](portable-snapshot-recovery.md).

## Destructive safeguards

Database wipe, snapshot restore, root reset, database-password rotation,
application-secret rotation, VAPID rotation and application-domain changes all
create a fresh full snapshot first. The operator must deep-verify that snapshot
and type an exact confirmation phrase before mutation begins.

If service or public-health verification fails, the CLI applies the verified
rollback snapshot. Operations are serialised with `flock`, and sanitised
management receipts are hash-chained without recording passwords, tokens or
private identities.

Restoring a database snapshot revokes replayed sessions, temporary passkey and
activation state, public schedule links and event publishing secrets.
Registered passkeys remain valid. Regenerate each event's desktop publishing
secret after recovery.

The active recovery verifier therefore records a separate semantic recovery
fingerprint after the scheduled marker and baseline snapshots exist. It covers
application rows, user properties and WebAuthn credential registrations, while
excluding login timestamps, credential usage counters, event publishing-secret
fields and public-link invalidation timestamps that authentication or restore
must change. Database, full and portable-rebuild gates require this semantic
fingerprint to match exactly.

In symmetric HA mode, long recovery operations also require automatic failover
to be disabled. This prevents the external lease from moving to the peer while
the current database is being replaced. Short shared-configuration changes
require a fresh online writer permit and queue a complete peer copy.

### Root recovery

Root reset removes only root passkeys, sessions, ceremonies, exchange codes and
root activation links. It preserves the root account and all other data. The
CLI creates a new bootstrap code and displays it once. After registering the
replacement root passkey, use **Disable bootstrap** to clear the code.

### Database wipe

A wipe drops and recreates only the application database. It preserves `.env`,
Docker secrets, proxy configuration and encrypted snapshots. The current
backend creates the base schema and root account before ordered migrations are
applied, after which the CLI displays a new bootstrap code.

### Application domain change

Changing the WebAuthn relying-party identity makes existing passkeys unusable.
The guarded workflow therefore invalidates pending activation links, clears
passkeys and sessions, prepares root bootstrap and marks non-root accounts for
fresh activation. With host Caddy it updates only the main application host and
preserves every other host block. With container Caddy it updates the protected
environment and recreates the proxy without modifying `/etc/caddy`.

The workflow refuses to start until the new hostname resolves to the current
application endpoint. Caddy and Compose are validated before installation, and
the new public HTTPS health endpoint must pass before the operation is accepted.

Full snapshots record the active Caddy topology. Host snapshots include the
host Caddyfile, while container snapshots do not copy unrelated host state. A
configuration restore is refused when its recorded topology differs from the
current installation, avoiding accidental proxy conversion or port conflicts.

## Operational security

- Never copy the age private identity into `.env`, `secrets/` or the repository.
- Do not send diagnostics, snapshots or bootstrap codes through untrusted chat.
- Keep `.env` mode `0600`; keep runtime-mounted backend secrets mode `0640`
  with operator ownership and backend group `10001`, inside a mode `0700`
  directory. Snapshot payload copies remain mode `0600`.
- Rotate the database password, application secret and IP HMAC key only through
  their guarded Configuration actions. IP HMAC rotation intentionally ends
  pseudonym continuity but does not revoke existing sessions.
- Use the redacted configuration view rather than printing `.env`.
- Keep at least one deeply verified encrypted snapshot off the VPS as well as
  the local copy.
- Keep two independently verified, protected copies of the current snapshot
  recovery identity. A successful explicit consolidation is the only point at
  which an older generation stops being required for managed archives.
- Never copy `/etc/mp-opt-ha` between nodes or include its node token, age
  identity or Origin CA private key in an application snapshot.

## Trust identity management

Initial commissioning explicitly confirms creation of the deployment's one
instance Ed25519 signing identity. The TUI displays its public fingerprint and
verifies the protected key, public trust record and HA fingerprint consistency.
It never regenerates a missing key. Missing or mismatched material requires the
guarded recovery or rotation workflow. Controller and processor registration
accepts public material and proof packages only. Their private keys must never
enter Server. See [Key custody and trust](key-custody-and-trust.md).
