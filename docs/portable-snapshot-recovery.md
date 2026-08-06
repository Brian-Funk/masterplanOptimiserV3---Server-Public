# Portable snapshot and disaster recovery

This runbook covers workstation export/import, sudden loss of one HA node and
complete loss of both VPSs. A portable snapshot is one binary-safe
`.mpopt-snapshot` file. Its application payload remains age-encrypted; the
matching private `AGE-SECRET-KEY-1...` is separate and must remain off every
VPS.

The outer `.mpopt-snapshot` file can be stored on Windows, macOS, Linux, WSL,
removable media or a network filesystem. Its host filesystem does not need to
support Unix permission bits because the payload is encrypted. Validation
still requires a regular non-symbolic-link file and verifies the fixed archive
member allowlist, internal `0600` modes, sizes, receipts and every SHA-256.

## Export one snapshot

On the VPS that stores the snapshot, open `mp-opt` and select **Snapshots and
recovery → Export one portable snapshot to a workstation**. Select a supported
v2 snapshot and then select the workstation command style:

- Windows Command Prompt;
- Windows PowerShell;
- Linux shell;
- macOS shell;
- generic SCP/SFTP client.

Enter only the SSH alias or `user@host` used by the workstation. MP-OPT keeps
the generated package filename, so you never have to type or copy its long
timestamped name. It validates the snapshot, creates one protected temporary
package, and leaves the complete transfer block visible as normal selectable
SSH-terminal text. MP-OPT clears the previous full-screen menu before showing
this block, so TUI borders and command text are not mixed together.

On the workstation, open a second terminal in the directory where the recovery
file should be stored. Copy the complete block from MP-OPT and run it there.
The command downloads the package into the current directory (`.`), calculates
its SHA-256 and compares all 64 hexadecimal characters automatically:

=== "Windows Command Prompt"

    `scp` followed by an exact PowerShell `Get-FileHash` comparison.

=== "Windows PowerShell"

    `scp` followed by an exact `Get-FileHash` comparison.

=== "Linux"

    `scp` followed by `sha256sum -c`.

=== "macOS"

    `scp` followed by an exact `shasum -a 256` comparison.

For these four command styles, success is unambiguous: the final line is exactly
`MP-OPT SNAPSHOT VERIFIED`. If the download or hash comparison fails, that
marker is not printed. Return to MP-OPT, press Enter, and confirm only when the
marker appeared. The generic SCP/SFTP option still displays the expected hash
for a manual comparison because MP-OPT cannot know the tools available in an
unknown client.

MP-OPT records the package SHA-256, snapshot archive SHA-256, recovery key id
and confirmation time in a mode-`0600` public receipt, then deletes its
temporary export. It never records the workstation path or private identity.
An unconfirmed temporary export is mode `0600` and expires after 24 hours.

When **High availability → Recovery storage** is `manual_portable`, this is the
independent disaster-recovery copy. Peer replication remains automatic, but
portable export has deliberately no recurring timer. A restore or recovery-key
rotation creates a visible action-required state until the required fresh full
snapshot is exported. For a pending rotation, export the exact baseline named
by MP-OPT; a different snapshot does not finalize it.

Keep two independent protected copies of the portable file and two protected
copies of the private recovery identity. The package without its matching
private identity cannot be restored.

## Import one snapshot

On the destination VPS open `mp-opt` and select **Snapshots and recovery →
Import one portable snapshot from a workstation**. Select the workstation
command style, enter the local package path and the SSH alias used from that
workstation. MP-OPT creates a random mode-`0700` upload ticket and displays the
exact SCP destination as normal selectable terminal text. Import retains the
local-path prompt because the file may be stored anywhere and can have arrived
from another VPS; export is the workflow that automatically uses the current
directory.

Run the generated upload command in a second workstation terminal. Return to
MP-OPT, confirm that upload completed and paste the original 64-character
package SHA-256 when it is available. Import then checks:

1. the 10 GiB package limit and guarded free-space reserve;
2. the fixed four-member allowlist and absence of links, devices, duplicates
   and traversal paths;
3. every member's declared mode, size and SHA-256;
4. the canonical encrypted-archive checksum;
5. the v2 receipt, age recipient fingerprint and recovery key id;
6. that no different snapshot already owns the same directory name.

The snapshot is installed atomically under `~/masterplan-snapshots`. Import
does not decrypt it. Select **Deep-verify a snapshot with the off-server
identity**, paste the matching private identity into the hidden prompt, and
accept the snapshot only after the archive, decrypted manifest, payload modes,
sizes, hashes and PostgreSQL catalogue all pass.

## What two-node failover does

HA replication and recovery snapshots use different keys. Normal replication
is encrypted to the standby's node-local HA recipient and decrypted with that
standby's identity under `/etc/mp-opt-ha`. The workstation-held snapshot
identity is never requested for replication, planned switchover or automatic
failover.

With automatic failover enabled, the witness waits for the fixed two-minute
failover delay after the current holder disappears. It promotes
the peer only when the peer is healthy, has an accepted bundle for the current
generation, reports the same release and has no active transfer or write
permit. Promotion increments the generation, replaces local state, revokes
bearer access and changes Cloudflare routing only after the new holder reports
ready. Ordinary writes after the last accepted periodic replication may be
lost. The documented critical publisher, public-link, and deletion-confirmation
operations are not reported as successful until their exact marker is present
in an accepted peer bundle.

With automatic failover disabled, scheduled/manual replication and planned
switchovers still work. Sudden holder loss does not promote the peer. The peer
continues to return not-ready and Cloudflare must not route application traffic
to it. There is no separate emergency promotion code: the current witness
accepts the automatic toggle and planned handoff only from the live holder.

Snapshot restore is allowed only on the current holder while automatic
failover is disabled. This prevents the witness from promoting the peer while
the holder is replacing its database and configuration. After restore, send a
fresh complete peer copy, compare its bundle generation/hash, test a planned
switchover and only then enable automatic failover again.
In manual storage mode, also create, deep-verify and export a fresh full
workstation snapshot after the restore. The pre-restore workstation package is
still valuable history, but it is not labelled as the current recovery point.

## Complete loss of both VPSs

### Recover one standalone node

1. Provision fresh VPS A, create the `deploy` account and install its SSH key.
2. Clone and deploy the tested server release in standalone mode.
3. Configure the original `DOMAIN`, `WEBAUTHN_RP_ID` and `WEBAUTHN_ORIGIN`.
   Registered passkeys work only with their original relying-party hostname.
4. Derive the public `age1...` recipient from the protected recovery identity
   on the workstation. Configure only that public recipient in MP-OPT.
5. Configure the same host/container Caddy topology recorded by the snapshot.
6. Route the application hostname to VPS A in Cloudflare and install fresh TLS
   material. Do not copy old `/etc/mp-opt-ha` files.
7. Import the portable file using the exact workflow above.
8. Deep-verify it with the matching private identity.
9. Select **Restore a verified snapshot with rollback protection**, read the
   warning and enter `RESTORE SNAPSHOT`.
10. MP-OPT creates and deep-verifies a current full rollback snapshot before
    replacing data. If restore verification fails it applies that rollback.
11. Require public `/health` and `/ready` status 200 and verify root login,
    events, users, schedules, settings and SMTP test delivery.
12. Confirm old sessions, activation links and public schedule links fail.
    Registered passkeys remain valid. Regenerate every event's desktop publish
    secret before publishing again.
13. Create, deep-verify and export a fresh full v2 snapshot from VPS A.

The application is now recovered but intentionally standalone.

### Rebuild the HA pair

1. Provision VPS B and deploy exactly the same Git commit as VPS A.
2. Generate fresh node-local HA identities, node tokens and Cloudflare Origin
   CA material. Never restore `/etc/mp-opt-ha` from application snapshots.
3. Create a new cluster ID. The old Cloudflare Durable Object belongs to the
   lost cluster and cannot be bootstrapped again.
4. Update or recreate the Cloudflare pools with the new VPS addresses.
5. Configure the same public snapshot recovery recipient on both nodes.
6. Configure peer SSH and exchange only the node-local public HA recipients.
7. Bootstrap the new witness cluster with recovered VPS A as initial holder.
8. Start the lease and replication services and select **Replicate now** on A.
9. In **High availability → Show lease, peer and replication state**, require
   matching cluster/generation/release values, matching public recovery-key
   SHA-256 values and a peer-accepted bundle ID and SHA-256.
10. Require A origin `/ready` 200 and B origin `/ready` 503.
11. Perform planned switchovers A → B and B → A.
12. Attach the accepted
    [destructive recovery drill completion gate](recovery-drill.md#completion-gate)
    for the same signed release and record both planned switchovers above as HA
    evidence. Never run the destructive drill against non-disposable production
    data. Enable automatic failover only after every gate passes.

## Unsupported test-era snapshots

Snapshot manifest/receipt v1 is deliberately unsupported. In **List snapshot
receipts**, these directories are marked `UNSUPPORTED` and are excluded from
verification, export and restore. Delete them through the snapshot menu only
after creating, deep-verifying and exporting a new v2 baseline.
