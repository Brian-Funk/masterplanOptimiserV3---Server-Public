# Snapshot recovery key

MP-OPT uses one dedicated age key pair per HA cluster to encrypt management
snapshots. Both VPSs receive the same **public** `age1...` recipient. Neither
VPS ever receives the matching **private** `AGE-SECRET-KEY-1...` identity.

This key is separate from the two HA replication identities under
`/etc/mp-opt-ha`. Replication identities are node-local and let one VPS send a
copy to the other. The snapshot recovery identity is an operator-held disaster
recovery key. Automatic and planned failover do not prompt for it and do not
exchange it.

Use a different recovery identity for the disposable test cluster and
production. One production identity may cover both production VPSs because
they are two members of the same recovery domain.

## 1. Generate the identity on the Windows workstation

Install the official `age` tools so `age-keygen.exe` is on `PATH`. Open
**Command Prompt** in the repository and choose a destination outside the
repository. The destination directory must already exist. No Python or Conda
environment is required.

```bat
where age-keygen
deploy\ha\create-recovery-key.cmd --output "%USERPROFILE%\Documents\MP-OPT-Recovery\cluster-recovery.agekey"
```

The command refuses to overwrite a file and refuses any destination inside the
repository. It creates:

- `cluster-recovery.agekey`: the private identity;
- `cluster-recovery.agekey.recipient.json`: public recipient, creation time
  and recipient SHA-256.

The private identity is not printed. Record the displayed public recipient and
SHA-256. They are not secret.

## 2. Make and verify two off-server copies

1. Import the complete private identity file into a protected password-manager
   entry as a file attachment or secure note.
2. Copy it to a second encrypted, offline medium kept separately.
3. Retrieve each copy into a temporary directory outside the repository and
   verify it derives the recorded public recipient:

   ```bat
   python deploy\ha\recovery_key_setup.py verify --identity "D:\Temporary\restored-test-cluster.agekey" --recipient "age1..."
   ```

4. Accept only `Result: MATCH` and identical `Recipient SHA-256` values.
5. Securely remove the temporary retrieval copies. Retain the two protected
   backups and the original only if its location is itself protected.

Do not put the private identity in Git, `.env`, `secrets/`, `/etc/mp-opt-ha`,
email, chat, a ticket or an unencrypted cloud drive.

## 3. Configure the cluster once

Connect to the **current lease holder**, run `mp-opt`, then select:

**Configuration → Configure the public snapshot encryption recipient**

Paste only the public `age1...` value. In HA mode the CLI:

1. obtains a fresh writer permit from the witness;
2. reaches the peer over the configured passwordless SSH link;
3. stages the public recipient on the peer;
4. installs it atomically on the holder;
5. activates it on the peer;
6. compares both public-recipient SHA-256 values.

The action fails if the peer is unreachable or either final fingerprint does
not match. It never asks for or transfers the private identity.

Open `mp-opt` on either VPS and select **High availability → Show lease, peer
and replication state**. Under **Snapshot recovery public recipient**, accept
only:

```text
Status: MATCH
Local SHA-256: <the 64-character SHA-256 printed by the workstation tool>
Peer SHA-256: <the same 64-character SHA-256>
```

The HA state screen is the operator-facing source of truth for this invariant.
The isolated management safety tests also reject missing and mismatched
recipients without requiring a workstation coordinator.

## 4. Prove that recovery actually works

On the current holder, use `mp-opt` to create a new named **complete recovery
snapshot**. Then select **Deep-verify a snapshot with the off-server identity**,
choose that snapshot and paste the `AGE-SECRET-KEY-1...` value into the hidden
prompt.

The identity is held only in a protected temporary memory-backed file for the
verification and is then removed. Accept the test only when the CLI reports a
valid archive hash, matching decrypted manifest hashes/sizes/modes and a valid
PostgreSQL dump catalogue.

After a planned switchover, repeat the same create-and-deep-verify procedure on
the other VPS. This proves both nodes encrypt to the same operator-held key.

## Normal failover behavior

No recovery key is involved in automatic failover. The current primary sends
point-in-time replication bundles encrypted to the peer's separate public HA
replication recipient. The peer decrypts those with its node-local replication
identity. The external witness controls writer ownership. The operator-held
snapshot key is used only for manual deep verification or restore.

## Snapshot key generations

New snapshots use the `mp-opt-snapshot-v2` encrypted manifest and
`mp-opt-snapshot-receipt-v2`. Both record three public values:

- the complete `age1...` recipient;
- its SHA-256 fingerprint;
- a short key id such as `rk-0123456789abcdef`.

The private identity is never recorded. The snapshot list in `mp-opt` shows the
key id required by each archive. Snapshot manifest and receipt v1 are not
supported: test-era v1 directories are displayed as unsupported and can only
be deleted. Create a new v2 baseline instead of relying on them.

During restore, `mp-opt` asks for the identity named by the selected snapshot.
If that generation differs from the currently configured generation, it asks
separately for the current identity. The selected identity decrypts the
requested recovery point; the current identity creates and verifies the
mandatory pre-restore rollback snapshot. This prevents a mixed-generation
restore from producing an unusable rollback point.

## Lost private identity

If every private copy is lost, existing snapshot archives are permanently
undecryptable. The public recipient on the VPS cannot reconstruct the private
identity.

Generate a new key pair and verify two private copies. Configure the new public
recipient from the active node and leave the old-identity prompt blank. The CLI
requires the exact phrase `ROTATE WITHOUT OLD KEY`, retains every old encrypted
archive unchanged, records their old key id as unavailable in the protected
rotation journal, and creates a new full baseline. In `ssh_archive` mode the
rotation completes after that baseline reaches the verified SSH archive. In
`manual_portable` mode it pauses at `awaiting-portable-export`: export that
exact baseline to the workstation, compare all 64 SHA-256 characters and
confirm the match. Only that confirmation completes the rotation.

Finding the old key later makes those retained archives restorable again. The
system never claims they were deleted or migrated when it could not decrypt
them.

## Safe rotation and consolidation

1. Generate and independently back up the new private identity.
2. Verify both backups derive the new public recipient.
3. Open **High availability → Choose manual workstation or automatic SSH
   recovery storage**. Select either `manual_portable` (no additional server)
   or a passwordless `ssh_archive` destination. Both HA nodes must use the same
   mode.
4. On the current lease holder run `mp-opt`, open **Configuration → Configure
   or safely rotate snapshot recovery encryption**, and paste the new public
   `age1...` recipient.
5. Paste the new private identity. The CLI derives its recipient and refuses a
   mismatch before reading any snapshot.
6. Paste the old private identity. The CLI must derive the old configured
   recipient. Type `ROTATE RECOVERY KEY` only after reading the inventory
   warning.

The guarded transaction then:

1. requires the active writer lease and an HA maintenance window with automatic
   failover disabled;
2. inventories local snapshots and the peer's local snapshots, plus the SSH
   archive only when that mode is configured;
3. verifies conservative free staging space;
4. fetches remote ciphertext without sending either private key to a VPS;
5. decrypts every copy with the old identity, validates all hashes, modes and
   PostgreSQL catalogues, writes a separate v2 replacement, and deep-verifies
   that replacement with the new identity;
6. keeps every original under a hidden rollback name while verified
   replacements are installed;
7. installs the new public recipient atomically on both HA nodes;
8. creates and deep-verifies a new full baseline;
9. either proves the SSH archive and receipt hashes or pauses until the exact
   baseline has an operator-confirmed portable package SHA-256;
10. removes protected old working copies only after every preceding step
   succeeds.

Any staging, installation, recipient-sync or baseline failure restores the old
public recipient and every already-replaced original. Public, mode-0600 event,
phase and copy-registry records are retained under
`~/.local/state/mp-opt-server/recovery-rotations/`. They contain public
recipients, key ids, managed paths and outcomes, never private identities. If a
process or host interruption leaves a non-terminal phase, the next rotation
attempt either rolls back everything before baseline verification or preserves
the new recipient and `awaiting-portable-export` phase. Use **Configuration →
Resume a pending manual recovery-key rotation** after exporting the required
baseline. A process interruption can never turn a lost key back into the
active recovery recipient.

After a successful consolidation all managed snapshots require the new key,
so the old private identity can be retired according to the operator's secure
media policy. Keep it until the final success screen and journal are reviewed.

Portable workstation export, cross-platform transfer commands and complete
two-VPS loss recovery are documented in [Portable snapshot and disaster
recovery](portable-snapshot-recovery.md).
