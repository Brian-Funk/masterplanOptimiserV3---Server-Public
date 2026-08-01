# Archived production HA migration

The former environment-specific, command-by-command migration runbook has been
retired. It depended on manually exchanged SSH keys, copied age recipients,
Cloudflare load-balancer pools, Origin CA files, hand-written node IDs, and
fixed infrastructure identifiers. Keeping those instructions would make a new
installation both harder and less safe.

Use the resumable management interface instead:

```bash
mp-opt
```

- For an existing single server, choose **Commission server → Convert this
  existing standalone server to Node A**. A newly verified off-VPS recovery
  copy is mandatory before the conversion begins.
- For an existing two-node installation that still uses Cloudflare Load
  Balancing, choose **High availability → Migrate a legacy Cloudflare load
  balancer to DNS-only routing**. The old routing objects remain disabled for a
  seven-day rollback window and are deleted only through a separate confirmed
  checkpoint.

Current end-to-end instructions are in [Production setup](setup.md). The
writer-lease, replication, routing, and recovery guarantees are described in
[Two-node high availability](high-availability.md).
