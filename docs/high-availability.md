# Two-node high availability

The production topology consists of two equivalent application VPSs and one
small Cloudflare Worker/Durable Object used as the external writer witness.
Only the current lease holder may write. Both nodes serve direct HTTPS with
Caddy; the witness changes a DNS-only A/AAAA record to the holder after local
promotion checks pass.

```text
browser -> DNS-only application record -> current holder VPS
                         ^
                         | changed only after promotion is ready
             Cloudflare Worker/Durable Object
                         |
                    node-a <-> node-b
                    encrypted complete copies
```

Cloudflare Load Balancing, Origin CA certificates, provider power APIs and a
Cloudflare token on either VPS are not required. Caddy presents and cleans its
DNS-01 challenge through a narrowly authenticated witness endpoint. The
witness accepts only the exact `_acme-challenge.<application-hostname>` name.

## Data and recovery objectives

The holder sends a complete PostgreSQL dump, shared non-local configuration,
and shared Docker secrets to its peer at least every five minutes and after
important changes. Each copy is encrypted to the peer's node-local age key,
hashed, manifest-validated, restored into a staging database, checked for
cluster/generation identity, and atomically swapped. The former peer database
is retained as an immediate rollback point.

This is a hybrid protection model. Ordinary writes retain a five-minute
recovery-point target and a catastrophic primary loss can lose ordinary
changes since the last accepted copy. The following narrow operations do not
report success until the standby verifies their exact database marker inside a
complete encrypted copy:

- event creation, publisher-secret setup/import, and publisher-secret rotation;
- public schedule-link creation, update, invalidation, and deletion; and
- deletion-case peer confirmation.

Each such mutation and its idempotent protection operation commit in one local
database transaction. While protection is pending, the resource is durable but
locked and the UI shows the capture, transfer, and verification stage. A timeout
never deletes or reverses it. A minimal UUID-addressed result is readable by the
backend from a non-listable runtime directory; detailed diagnostics remain in
the private host-management directory. If an acknowledgement is lost, the
sender reconciles the exact bundle hash and operation-marker list with the
receiver state. The root HA panel reports the latest accepted copy and current
potential data-loss age. Independent disaster recovery still requires an
encrypted portable snapshot whose private identity is held outside both VPSs.

## Automatic failover

Nodes heartbeat every 15 seconds. After two minutes without the holder, the
witness may promote the peer only when all safety gates pass:

- the candidate is healthy and has the current generation's complete copy;
- both nodes report the same signed release identity;
- no critical replication/configuration operation is pending;
- no unresolved critical-operation witness guard is active;
- no write permit or transfer guard remains active;
- SMTP configuration is either disabled on both nodes or verified and
  fingerprint-identical on both;
- direct routing is configured.

The instance evidence signing key is deployment identity, not node identity. It
travels only through the encrypted HA secret-replication boundary. Both nodes
verify the same instance ID and public fingerprint before service or failover.
They must never independently generate competing instance keys.

The candidate restores the new generation locally and passes health before the
witness updates DNS. A stale former primary cannot regain writes merely by
receiving traffic. Mutations need a short witness permit at both middleware and
database-commit boundaries. When the witness is unavailable, writes fail
closed while already-saved schedules remain readable in the browser.

The holder opens a bounded witness guard before committing a critical
operation. Planned and automatic promotion remain blocked until the standby
accepts the exact operation or the holder proves the database transaction did
not commit. An expired unresolved guard is retained as a bounded witness
incident; it never turns an unverified mutation into a successful result.

Normal users see a small generic availability message and can open their saved
read-only schedule. Root administrators additionally see the active transition
and timestamped checkpoints. Related events are grouped into one incident with
service downtime; aggregate total and average downtime distinguish planned
transitions from automatic failovers. Loss of redundancy alone is not kept as
an incident-history entry.

## Routine operation

Run `mp-opt` on the current holder and open **High availability** to:

- review lease, peer, replication, SMTP and incident state;
- send an immediate verified copy;
- perform a planned switchover;
- disable or re-enable gated automatic failover;
- replace a lost standby with a one-time join code;
- migrate/retire legacy load-balancer routing;
- intentionally decommission the witness and managed DNS records;
- run isolated bundle and write-fencing self-tests.

Signed release updates install the same digest-pinned images, static frontend
and operational scripts on both nodes. Automatic failover must be disabled
during a rolling deployment; re-enable it only after the releases match and a
fresh copy is accepted.

## Planned switchover

The holder obtains a write-safe checkpoint, sends and verifies a fresh complete
copy, asks the witness to transfer ownership, and stops serving live writes.
The peer promotes the authorised generation, passes health/TLS, and only then
receives the DNS record. Existing passkeys and durable credentials remain;
short-lived sessions/ceremonies may be invalidated at the generation boundary.

## Standby replacement

Disable automatic failover, power off the lost standby, then choose **Replace a
lost standby**. The witness revokes that node's old credential immediately and
issues a one-time code. Bootstrap the replacement VPS, choose **Join an HA
pair**, paste the code, and resume on Node A. The workflow regenerates all
node-local SSH/age material and refuses to enable protection until a current
complete copy is accepted.

## Legacy load-balancer migration

Choose **Migrate a legacy Cloudflare load balancer to DNS-only routing** on the
holder. Supply the existing Worker name, a temporary Worker deployment token,
the new long-lived zone-scoped DNS token, and both public IP addresses. The TUI
upgrades both nodes to the same signed release and verifies a fresh copy before
changing witness routing. Once DNS routing is configured, it removes the
obsolete broad Cloudflare API secret from the Worker.

At the displayed external checkpoint, disable—but do not delete—the old load
balancer. The witness then creates the DNS-only record at TTL 60, and the TUI
probes direct TLS on both origins. Keep the disabled load balancer and pools for
seven days. After the recorded date, use **Delete legacy routing after its
seven-day rollback window** and confirm the dashboard deletion. Never delete
the application A record or HA Worker during cleanup.

## Intentional Cloudflare decommissioning

Use **Decommission Cloudflare HA resources** only when retiring the HA
installation or deliberately replacing its witness. The TUI first authenticates
to the existing witness and asks it to remove the exact managed A, AAAA and
ACME challenge state. A temporary narrowly scoped deployment token may then
delete the Worker. The token is not saved on either VPS.

This action does not delete VPS data, snapshots or the documentation service.
It also does not revoke application public schedule links or publisher tokens.
Those are database-backed application records and remain subject to their own
explicit lifecycle controls.

## Recovery from complete loss

Bootstrap a replacement Ubuntu VPS and choose **Recover after complete server
loss**. Import the newest portable snapshot, compare its recorded SHA-256, and
supply the off-VPS private age identity only to the guarded restore prompt.
After restoring a standalone service, commission a new Node B using the normal
conversion flow. Do not attempt to resurrect a witness lease using two
independently restored databases.
