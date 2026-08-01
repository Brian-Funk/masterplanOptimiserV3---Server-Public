# Exact-stack qualification

Phase F qualifies one exact set of App, Server, Testing and public Docs revisions in a disposable local synthetic environment. It is software test evidence, not a production deployment or an external commissioning result.

The qualification covers:

- bootstrap and controller-selected governance configuration;
- current-format Desktop publication and participant and organiser reads;
- optional public-schedule and offline-cache lifecycles;
- encrypted synthetic snapshot creation, verification and restore with a verified rollback snapshot;
- account and event deletion workflows;
- local signed evidence export, offline bundle verification, temporary private archive import and deterministic human-readable summaries.

The exact run receipt records every repository commit, whether a worktree was clean, each command and its result. A failure, unexplained data discrepancy or visibility discrepancy fails the qualification.

## One-time Desktop conversion boundary

The older Desktop database converter is a separate, temporary operator tool. It may be used for one one-time conversion into the current data format. It is not imported, invoked or exposed by the Desktop application.

Qualification also exercises the completed four-domain trust architecture:
exactly-once instance commissioning, root WebAuthn action binding, external
controller custody, Desktop-only processor custody, proof before activation,
role and entity rejection, HA fingerprint continuity, rotation, revocation and
historic verification. Phase G validates these completed controls and does not
introduce the architecture.

Phase F uses synthetic data to test converter dry run, conversion, semantic comparison and hash-verified rollback. It also checks that the Desktop runtime has no automatic startup migration, converter UI or user-facing import flow, dual-read or dual-write path, legacy-data synchronisation or ongoing legacy-format support.

Use of the configured expendable protected-data copy requires a separate controller approval. The live original and its untouched backup remain inaccessible and unchanged.

## Exclusions

The phase does not use production SSH, external DNS, TLS or SMTP, real provider failover, a real evidence repository, real credentials, protected data or live backups. It does not deploy or release the application.
