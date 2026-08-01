# Destructive recovery drill

This runbook verifies the single-server recovery controls before any
georedundancy work begins. Run it only against an explicitly disposable test
installation. Never use the production hostname, database, mail token, or
recovery archives.

## Safety boundary

- Pin and record the tested Git commit before collecting the baseline.
- Require a clean checkout, healthy services, valid Caddy configuration, and a
  valid management audit chain.
- Keep the age private identity off the VPS. Paste it only into the protected
  `mp-opt` prompt, which stores it temporarily in memory-backed storage.
- Copy every accepted baseline archive and its receipt off-server and verify
  the archive SHA-256 again there.
- Stop at the first unexpected fingerprint, file mode, hash, health, or passkey
  result. Do not compensate with an undocumented manual change.

## Evidence checkpoints

Open `mp-opt`, select **Maintenance and diagnostics**, then select
**Create a hashed recovery-test checkpoint**. Use short labels such as
`baseline`, `after-root-reset`, or `after-full-restore`.

Each evidence directory contains:

- the commit, clean-worktree state, health response, and audit-chain result;
- container states without environment values;
- protected-file paths, modes, owners, and hashes without file contents;
- stable fingerprints for durable database data and root credentials;
- an information-schema hash and current encrypted snapshot hashes;
- `evidence.sha256`, which verifies every evidence file in the directory.

Copy accepted checkpoints off-server after verifying `evidence.sha256`.

## Required sequence

1. Create representative event, account, published schedule, edit, link,
   notification, setting, and snapshot data through supported application
   workflows. Disable bootstrap after confirming the root passkey works.
2. Create database, secrets, and full baseline snapshots. Deep-verify each and
   copy all three off-server.
3. Prove cancellation, wrong confirmation phrases, a wrong age identity, and a
   tampered archive cannot change state.
4. Stop only Caddy and run root reset. Require the failed health check to invoke
   the automatic verified rollback, restore Caddy, and preserve the original
   root passkey and baseline fingerprints.
5. Reset root successfully, register a temporary passkey on a second
   authenticator, disable bootstrap, and restore the database baseline. The
   original passkey and semantic application fingerprint must return, while
   the temporary passkey must fail. Replayed sessions, public links and old
   publishing secrets remain revoked and are intentionally excluded from the
   recovery-content fingerprint.
6. Rotate the database password, application secret, IP HMAC key and VAPID key.
   Verify the documented session, pseudonym-continuity and push-subscription
   effects. Add one durable marker, restore the secrets snapshot, and prove the
   marker remains.
7. Wipe the database and require the management output to show `PASS` for
   every named canonical schema invariant before it reports success. A failed
   invariant automatically invokes the verified pre-wipe rollback. After a
   successful wipe, register another temporary root passkey, restore the full
   baseline, and prove that the original passkey and all baseline fingerprints
   return while the temporary passkey is rejected.
8. Export the full snapshot as one `.mpopt-snapshot` file and compare its
   workstation SHA-256 with the value shown by `mp-opt`. Move the installation
   aside, remove its Docker state, clone the pinned commit into a clean
   application directory, and deploy an empty installation. Use **Snapshots
   and recovery → Import portable snapshot**, upload the file with the generated
   command, deep-verify it, restore it, and prove application, database,
   passkey, launcher, Caddy, and public views.
9. Exercise service stop, start, restart, backend recreation, frontend rebuild,
   static and live logs, interface sizing, diagnostics, audit verification,
   archive verification, and build-cache pruning. Reboot the VPS and repeat
   health, login, public-view, and evidence checks.

## Passkey allocation

Keep the baseline root passkey on the first authenticator. Create all temporary
post-reset and post-wipe credentials on the second authenticator. This prevents
the authenticator from replacing the discoverable baseline credential before
the restore tests.

## Completion gate

The drill passes only when the final full restore exactly matches the accepted
baseline, the original root and issuer passkeys work, bootstrap is disabled,
all services recover after reboot, every required archive is deeply verified
and held off-server, and the completed report contains no unexplained
deviation. Georedundancy work remains blocked until then.
- Verify that the recovered instance private key matches the retained instance
  trust fingerprint before service starts. Recovery must not create a new
  evidence identity. Controller and processor private keys are outside Server
  recovery packages.
