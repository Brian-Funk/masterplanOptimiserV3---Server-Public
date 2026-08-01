# Contributing

## Pull Requests

All changes should go through a pull request into `main`. Configure branch
protection so `server-ci-result` is required before merging.

## Required Checks

Server CI runs on every pull request and push to `main`.

- Server-native validation checks shell scripts, Compose and Caddy models,
  compiles Python, runs the isolated HA/recovery suite, audits dependencies,
  scans every service image, and builds the Next frontend.
- Repository tests run the deterministic application, PostgreSQL concurrency,
  frontend and deployment-contract suites directly from this checkout. Only
  destructive VPS drills and credential-bearing harnesses remain private.

High or critical dependency and container findings are release-blocking. Do
not weaken the audit severity, ignore unfixed findings, or replace a failing
check with an allow-list without documenting and reviewing the shipped attack
surface.

Fork pull requests run the same repository-local tests without access to
secrets. The workflow does not use `pull_request_target`.

## Documentation Rules

Use British English in user-facing documentation and generated API comments.
Code identifiers may keep library-compatible spelling such as `color`,
`finalize`, or `optimization` when changing them would break contracts.

Add docstrings or JSDoc for public functions, route handlers, reusable
components, and exported types whenever behaviour changes.

## Release Hygiene

Before proposing a release, confirm that the checkout contains no private
environment files, recovery identities, portable snapshots, generated sites,
frontend build output, local HA configuration, diagnostic bundles, or operator
evidence. Production management and recovery self-tests remain source code;
one-off commissioning coordinators and completed drill reports do not.

The production repository, GitHub Release assets, and GHCR images are public.
Before the first public release and after any suspected exposure, review the
complete Git history as well as branches, tags, releases, workflow artifacts,
issues, and pull requests. A clean current checkout is not sufficient. Rotate
any credential found in historical or generated material before changing
visibility or publishing a release.

Version changes are a separate release decision. Do not silently change the
web package, API, or witness version as part of unrelated cleanup.

## Manual Docs

Update manual pages when setup, architecture, workflows, deployment, or security
behaviour changes. Generated API reference pages are rebuilt in CI rather than
committed.
