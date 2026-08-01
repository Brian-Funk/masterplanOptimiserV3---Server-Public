# Controller and operator checklist

Use this checklist before a production launch and repeat it after material
configuration, provider or policy changes.

## Governance

- [ ] The controller identity and privacy contact are accurate.
- [ ] The privacy, retention, rights, processor and permitted-data texts are
      published from the local governance centre.
- [ ] Legal bases, retention periods, processor agreements and transfer
      assessments are documented outside the application where required.
- [ ] Every authorised editor understands the permitted-data boundary.

## Infrastructure and security

- [ ] The latest supported signed release and digest-pinned images are used.
- [ ] Root bootstrap is disabled after the first root passkey is registered.
- [ ] Administrator, VPS, registrar, DNS, SMTP and backup accounts use strong,
      separate credentials and multi-factor authentication where available.
- [ ] DNSSEC, firewall rules, operating-system updates and time synchronisation
      have been considered and documented.
- [ ] Recovery identities and controller-held evidence keys are stored away
      from both VPSs.
- [ ] An encrypted restore has been tested.
- [ ] For HA, replication, SMTP on both nodes, planned switchover and automatic
      failover have been tested.

## Data lifecycle

- [ ] Desktop input and server output contain only supported operational data.
- [ ] Public links and offline schedules were checked for excess fields.
- [ ] Retention includes live data, provider snapshots, portable backups,
      workstations, email-provider copies and evidence records.
- [ ] Access revocation, individual deletion and complete-event deletion have
      an assigned operator and an escalation path.
- [ ] Deletion records use non-identifying evidence IDs and never claim that a
      signature proves physical deletion.

## Release and incident readiness

- [ ] Security reporting and supported-version policies are available.
- [ ] Dependency, image, secret, licence and publication checks passed.
- [ ] The incident plan identifies who assesses risk and handles statutory
      notification deadlines.
- [ ] No production database, backup, private key or participant data is sent
      to the project maintainer for support.

