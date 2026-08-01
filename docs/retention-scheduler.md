# Retention scheduler and event deletion grace

The Server runs one retention cycle at startup and then every five minutes by
default. In HA mode only the current routed writer may run a cycle. Every write
also remains subject to the online witness permit, and PostgreSQL uses a
transaction-scoped advisory lock so parallel workers cannot run the same cycle.

## Event lifecycle

An event with an end date receives a materialised UTC deletion-review deadline.
The default is 90 full days after the inclusive end date. A root administrator
can change `event_purge_grace_days` under Security settings. Existing deadlines
do not move silently when the global value changes; editing an event end date
materialises a new deadline using the then-current value.

At the deadline the scheduler creates one signed `event_erasure` case with the
reason `retention_schedule`. It does not accept the case, delete data, or create
a Desktop work order. A recently re-authenticated root must still review and
accept the case. The ordinary strict workflow then requires:

1. Desktop whole-event deletion and a validated report;
2. Server live-data purge;
3. peer confirmation in HA mode;
4. a clean replacement recovery package;
5. resolution of every named superseded package;
6. the immutable checklist and required human approvals; and
7. the minimal signed completion receipt.

Schedule publishing is blocked after the purge case starts, while the Desktop
publish credential remains usable for claiming and reporting the deletion work
order.

## Automated classes

The same cycle handles expired and revoked sessions, passkey challenge rows,
passkey ceremony rows after their short reconciliation margin, exchange codes,
used or invalid activation links, activation-delivery rows, audit logs, and
the configured maximum number of unfrozen publish snapshots.

Recovery packages are not silently deleted. Their exact identifiers remain in
the controller-attested deletion workflow. Privacy tombstones remain restore
guards for their configured horizon. The append-only evidence ledger follows
the controller's documented private-repository retention policy rather than a
database timer.

## Monitoring

Root administrators can read `GET /api/v1/admin/retention/status`. The response
contains only the last bounded result, class counts, scheduler interval and the
complete retention inventory. It contains no event names, user identifiers or
record contents. Event cards show their grace value, UTC deadline and whether a
deletion workflow has started.

An incorrect system clock or a stopped application can delay a cycle. Because
deadlines and case identity are stored in PostgreSQL, the first successful
writer cycle after restart catches up without creating a duplicate case.

This is technical implementation evidence. It does not choose a lawful period,
prove deletion by an external provider, or constitute legal approval.
