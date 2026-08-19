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

## Most recent local verification

- Multi-controller, activation-email and imported-retention focused suite:
  **50 passed**.
- Broader route matrix covering calendar, history, public links, notifications,
  deletion/export, web edits and event scoping: **89 passed** at the preceding
  checkpoint.
- Authentication/passkey/admin activation focused suite: **116 passed** at the
  preceding checkpoint.
- Controller evidence/evidence export focused suite: **27 passed** at the
  preceding checkpoint.

These are SQLite/local contract tests. They are not a substitute for the
required PostgreSQL migration/RLS and real two-node commissioning runs.

## Required continuation before PR readiness

1. Complete the remaining endpoint/service audit, especially background jobs,
   controller-specific evidence export/archive behavior, deletion decisions,
   operator publication evidence and all optional-feature gates.
2. Run the migration against real PostgreSQL and directly prove forced RLS with
   root, event, publisher, public and worker contexts.
3. Complete root and non-root UI tests, public legal-centre tests and accessibility
   coverage; run the full frontend production build.
4. Run the complete backend, PostgreSQL, migration, HA, commissioning, deletion,
   evidence, CSP, dependency, licensing and documentation suites.
5. Perform a security diff review and resolve every reportable finding.
6. Push qualified iterations to this development branch and open a PR without
   merging it.
7. Reconfigure and operate Commissioning-Private exclusively with enrolled
   staging aliases `mp-opt-stage-node-c` and `mp-opt-stage-node-d`. Production
   Nodes A and B are prohibited targets.
8. Re-run the existing commissioning scenario catalogue on the exact immutable
   AMD64 candidate bundle, then achieve five consecutive clean fresh-HA runs.
   Any product or acceptance-truth change resets the streak.

## Safety state

- No production node, production data, production Cloudflare resource or
  production service was accessed or changed during this implementation work.
- No staging-node commissioning run has started from this checkpoint.
- No PR has been merged, and no tag or release has been created.
