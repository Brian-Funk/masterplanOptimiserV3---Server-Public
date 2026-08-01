# Compliance evidence

Masterplan includes a strict canonical-manifest and OpenSSH Ed25519 signing
foundation for non-identifying accountability evidence. It uses the namespace
`mp-opt-evidence-v1`, a dedicated per-instance key, bounded JSON documents,
atomic mode `0600` writes and a SHA-256 linked ledger.

The signing key is separate from SSH, recovery, SMTP, DNS, passkey and
application keys. In HA it is a protected shared secret and is included in the
same encrypted replication and recovery boundary as other instance secrets.

The verifier proves that an allowed key signed exact canonical bytes and that
the stored chain has not subsequently changed. It does not prove physical
deletion and must never be described as proof that no other copy exists.

The deletion workflow uses this primitive for request, access revocation,
desktop report, live server, HA peer, replacement backup, backup resolution,
checklist approval and final phases. Every phase is mandatory when applicable.
An unresolved external copy keeps the case open under restricted retention;
there is no exception-completion path. This is a proportionate record of work
under the controller's control, not a claim that no physical copy exists. See
[Deletion accountability](deletion-accountability.md).

For local development and verification run:

```bash
python3 deploy/evidence/evidence_manifest.py --help
python3 -m pytest server_backend/test_evidence_manifest.py -q
```

Evidence exported to a controller-owned repository contains no participant
data, private keys, application backups or Git credentials. The management TUI
exports a deterministic, self-contained portable bundle and shows copyable,
idempotent workstation staging commands. Optional automatic archival is
disabled by default and is documented under
[Optional private Evidence Git archive](evidence/controller-evidence-git.md).

## Separate instance, root, controller and processor trust

The instance signer, root passkey, external controller key and Desktop processor
key are separate trust domains. They are not generic operator keys. Controller
and processor private keys never enter Server. Root authorises exact actions
with WebAuthn and receives no exportable Server signing key. See
[Key custody and trust domains](key-custody-and-trust.md).

Controller keys are created through the external controller-custody workflow.
Processor keys are created only by Desktop and remain in its operating-system
credential store. Registration verifies external proof first, then requires a
separate root WebAuthn ceremony bound to the exact activation action. The
instance key signs the resulting ledger record. Routine rotation requires old
and new proof. Revocation blocks future use while preserving historical public
verification material.

## Manual private evidence repository workflow

Create a new, empty controller-owned repository with:

```bash
python3 deploy/evidence/evidence_repo.py initialise --archive /protected/path/evidence
```

Keep it private. Protect its default branch, block force pushes and branch
deletion, and require the generated `Verify accountability evidence` workflow.
That workflow runs on every push and pull request and validates every declared
bundle and the complete chain. It rejects missing, modified, truncated, forked,
rolled-back or undeclared files.

The workstation helper refuses unrelated dirty files, stages only declared
paths, uses the controller's configured local Git signer, never receives an
evidence private key and remains available without a Server token. The
resulting unsigned anchor JSON is signed with the controller-custody workflow,
not Desktop. Root passkey authorisation and the instance ledger record remain
separate from the controller signature.

Branch protection prevents unverified history from entering the protected
branch. A workflow triggered after an unprotected push can detect a bad push,
but cannot by itself prevent that push.
