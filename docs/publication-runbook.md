# Public repository cutover

The current private repository remains the development source until the publication gate passes. Do not change its visibility in place if historical branches, tags, releases, artefacts or discussions may contain deployment-specific material.

## Gate

1. Complete governance, Cloudflare privacy, code-polish and security review work.
2. Run repository tests, dependency audits, image scans, shell validation and the publication audit.
3. Review every branch, tag, release asset, workflow artefact, issue and pull request in the private repository.
4. Rotate every credential that ever appeared in Git, an artefact, a terminal capture or a release.
5. Export the reviewed tree into a new repository with a new parentless root
   commit and no branches, tags, releases, pull requests, issues, Actions
   artefacts or Git objects copied from the private repository. Never change
   the private development repository's visibility in place.
6. Confirm `LICENSE`, `SECURITY.md`, support policy, third-party notices and controller-neutral documentation are present.
7. Make the reviewed repository public, enable private vulnerability reporting and branch protection, then create the first canonical signed `v3.8.0` release. Subsequent releases use the same protected process.

Before the first public push, run
`python deploy/security/publication_audit.py --history` inside the new
repository. A finding for `CODEX_*`, an engineering report, `notes/`, a secret
artefact or another forbidden historical path proves that the export is not
clean and must block publication.

The frontend build derives its corresponding-source identity from the Git
remote and exact `HEAD`. A source archive without Git metadata, or a modified
build whose public source is elsewhere, must set all three non-secret values:

- `MP_PUBLIC_SOURCE_REPOSITORY_URL`, a credential-free HTTPS repository URL;
- `MP_PUBLIC_SOURCE_REVISION`, the exact 40-character commit SHA; and
- `MP_PUBLIC_SOURCE_URL`, a credential-free HTTPS URL containing that SHA.

The build fails closed for a floating revision, embedded credentials or a
mismatched source link.

The old `v3.4.0` release is retired. Do not reuse or replace its tag. The current prepared stable release is `v3.9.5`. Development deployments continue to use commit-addressed unsigned test artefacts and must show that they are not a production release.
