# Cryptographic inventory

The versioned machine-readable catalogue at
`deploy/security/cryptographic_inventory.json` is the canonical list of keys,
tokens, credentials and public verification material used by the supported
deployment. It records, for every item, its purpose, format, owner, generation,
storage, recipients, access roles, backup, rotation, revocation, compromise
response, destruction condition and key-ID source.

The catalogue deliberately contains no private values. Validate it with:

```bash
python3 deploy/security/cryptographic_inventory.py validate
```

The four trust domains are inventoried separately: instance signing, root
WebAuthn authorisation, controller signing and Desktop processor signing. See
[Key custody and trust](key-custody-and-trust.md) for generation, ownership,
recipients, backup, rotation, revocation, recovery and destruction conditions.

On an installed server, open **Configuration > View non-secret key and
credential inventory status**. The report checks protected-file presence and
mode and shows only safe public fingerprints or the existing IP-HMAC key ID.
It reports ephemeral database state, Caddy-managed certificates and external
provider credentials as such. It never prints a password, token, private key,
or a digest of a provider/database credential that could assist guessing.

The controller must maintain the deployment-specific parts marked
`operator_record_required`, including:

- named human SSH and Git/evidence signers;
- provider token identifiers and recovery-code custody;
- Fine-grained GitHub personal access token expiry, rotation and revocation for optional private Evidence archival;
- current certificate and provider public-key fingerprints;
- desktop database-key custody and the tracked release-manifest public-key fingerprint;
- normal rotation dates and any shortened incident rotation;
- revocation and destruction evidence;
- access-review date and current custodians.

Use the guarded management actions for application, IP-HMAC, database, VAPID,
recovery and HA changes. A provider-side credential is not revoked merely by
deleting a local file: revoke it at the provider, verify the provider record,
then remove or replace the local copy. Preserve public verification history
when rotating evidence or Git signing keys.

Never copy the generated report into a public repository if an operator has
added deployment-specific account names, host paths or provider identifiers.
