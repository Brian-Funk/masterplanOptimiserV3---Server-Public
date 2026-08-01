# Legacy workstation archive template

This template is retained only for compatibility with earlier manual exports.
New deployments use the self-contained portable bundle and the bundle-only
`git-template` archive described in `docs/evidence/controller-evidence-git.md`.
The optional integrated Server uploader uses one narrowly scoped fine-grained
GitHub personal access token; this legacy template is not its target.

This repository is a controller-owned archive for verified, non-identifying
Masterplan accountability bundles. Keep it private even when the application
source is public.

Before adding evidence, require the `Verify accountability evidence` workflow
on the protected default branch. The workflow runs on every push and pull
request and rejects missing, modified, truncated, forked or undeclared data.

Never add private signing keys, recovery packages, credentials, names, email
addresses, tasks, schedules or raw participant identifiers. The repository
records integrity evidence. It does not prove physical deletion by a storage or
Git provider.

Use `scripts/evidence_repo.py` on the controller workstation to verify and
import bundles, create a locally signed commit, push the current branch, and
prepare an exact Git anchor for signing by the desktop custody helper. The
helper refuses unrelated dirty files and never stores Git credentials.
