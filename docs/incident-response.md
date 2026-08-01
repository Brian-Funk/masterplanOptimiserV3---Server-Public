# Incident response

1. Contain access. Disable automatic failover if ownership is uncertain, revoke affected sessions and provider credentials, and preserve minimal timestamps and identifiers.
2. Assess scope. Identify the affected instance, event, data categories, recipients, processors, backups and time window without copying unnecessary personal data into tickets.
3. Recover safely. Use a verified signed release and an encrypted, deeply verified recovery snapshot. Do not bypass HA fencing or restore root bootstrap automatically.
4. Notify the controller. The controller determines applicable GDPR, FADP, contractual and supervisory-notification duties and deadlines.
5. Rotate dedicated credentials. Treat Cloudflare, SMTP, Git, SSH, recovery, application and evidence keys as separate trust domains.
6. Record decisions locally. Keep factual, bounded evidence. Do not claim that a signature proves physical deletion.
7. Review. Fix the cause, validate single-node and HA behavior, and publish a new signed patch release when shipped code changed.

Never attach production databases, `.env`, private keys, activation links, participant schedules or recovery packages to a public issue.
