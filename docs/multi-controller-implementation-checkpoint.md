# Multi-controller hosted Server implementation checkpoint

Status: **work in progress; not release-qualified**

Branch: `dev/v_3_9_19`

Authoritative design: `../roadmap/requirements/MULTI_CONTROLLER_HOSTED_SERVER_ARCHITECTURE.md`
in the shared EYP workspace.

## Completed foundation

- First-class operator, controller, controller-governance publication, event
  governance and exact event-membership models.
- Immutable event-to-controller ownership and immutable controller public trust
  identities at the ORM and PostgreSQL migration layers.
- Single-controller compatibility controller and an explicit, fail-closed
  hosted-mode preflight.
- Root remains globally privileged without support grants or break-glass state;
  every non-root role is derived from one active event membership.
- Event/controller scoping added to authentication, publishing, processor trust,
  activation, governance, notifications, public links, deletion, audit and
  evidence operations.
- PostgreSQL tenant context and forced RLS policy definitions, including bounded
  pre-authentication and controller-evidence-writer service scopes.
- Separate operator, controller and event legal publications. Hosted activation
  records an exact disclosure acknowledgement rather than asserting consent as
  the legal basis.
- Event-wide unavailability is visible to every authenticated account in the
  same event and hidden from other events/controllers and public routes.
- Operator-fixed retention is read from the immutable operator publication
  bound to the event governance record. Hosted SMTP activation is event-feature
  gated; single-controller compatibility remains unchanged.
- Root UI foundation for operator/controller/event context and controller-bound
  trust keys; offline cache identity includes controller, event, membership and
  policy identities.
- Operator/controller/event feature policy is resolved as an immutable
  intersection before each optional operation. Hosted publishing, SMTP, push,
  public links and offline storage all fail closed outside that intersection.
- Activation and mail disclosures now link to event-specific privacy plus
  immutable controller privacy/rights/processor documents and immutable
  operator privacy/subprocessor/security documents.
- Offline payload/cache schema v6 reflects the event-wide unavailability
  contract and invalidates the older, narrower opt-in without silently
  extending local storage.
- Foreign privileged and ordinary account identifiers are indistinguishable to
  every non-root administrator. Event secret rotation, direct deletion and
  account reassignment are now protected by the same exact-event boundary.

## Most recent local verification

- Core API and security group: **328 passed, 1 skipped**. This includes
  activation, administration, calendars/offline data, deletion/export,
  governance, history, notifications, passkeys, public links, publishing,
  web edits and the cross-controller route matrix.
- Evidence, deployment-contract and lifecycle group: **214 passed, 2 skipped**.
- Frontend: **65 files / 378 tests passed**; ESLint passed; the complete Next.js
  production build and all 29 static routes passed.
- Python bytecode compilation and `git diff --check` passed.
- The published-migration digest test now hashes the canonical Git LF bytes, so
  Windows checkout conversion cannot falsely report a released-blob change.

The remaining local shell-test failures all originate before product code runs:
this Windows host's WSL launcher returns `E_ACCESSDENIED`, and it cannot create
unprivileged test symlinks. Those exact tests remain mandatory in Linux CI and
on the staging nodes.

These are SQLite/local contract tests. They are not a substitute for the
required PostgreSQL migration/RLS and real two-node commissioning runs.

## Required continuation before PR readiness

1. Run the migration against real PostgreSQL and directly prove forced RLS with
   root, event, publisher, public and worker contexts.
2. Run the complete PostgreSQL, migration, HA, commissioning, management-shell,
   deletion,
   evidence, CSP, dependency, licensing and documentation suites.
3. Perform a security diff review and resolve every reportable finding.
4. Push qualified iterations to this development branch and open a PR without
   merging it.
5. Reconfigure and operate Commissioning-Private exclusively with enrolled
   staging aliases `mp-opt-stage-node-c` and `mp-opt-stage-node-d`. Production
   Nodes A and B are prohibited targets.
6. Re-run the existing commissioning scenario catalogue on the exact immutable
   AMD64 candidate bundle, then achieve five consecutive clean fresh-HA runs.
   Any product or acceptance-truth change resets the streak.

## Safety state

- No production node, production data, production Cloudflare resource or
  production service was accessed or changed during this implementation work.
- No staging-node commissioning run has started from this checkpoint.
- No PR has been merged, and no tag or release has been created.
