<p align="center">
  <img src="web/public/logo_normal.png" alt="Masterplan Optimiser" height="80" />
</p>

This project is licensed under AGPL-3.0-only. It provides technical controls
for self-hosted GDPR and Swiss FADP readiness, but does not certify a deployment
or replace the controller's legal and organisational assessment.

Read the exact [software licence](LICENSE), generated
[third-party notices](THIRD-PARTY-NOTICES.md), separate
[branding policy](BRANDING.md) and
[contribution-provenance record](COPYRIGHT-AND-CONTRIBUTION-PROVENANCE.md).
Every built web interface displays the repository and exact commit containing
the corresponding source. Modified deployments must supply their own exact
source identity at build time.

<h1 align="center">Masterplan Optimiser - Server</h1>

<p align="center">
  Collaborative web calendar for viewing and managing published masterplans.<br>
  Receives schedule data from the desktop app and serves a responsive calendar<br>
  for participants, event issuers, and administrators.
</p>

<p align="center">
  <a href="https://info.mp-opt.net"><strong>Documentation &amp; Info &rarr;</strong></a>
</p>

---

> For setup instructions, deployment guides, and full documentation visit **[info.mp-opt.net](https://info.mp-opt.net)**.

## Installation

New installations are commissioned through one resumable terminal wizard. On
a clean Ubuntu 22.04 or 24.04 VPS, sign in as `root` and run the documented
bootstrap command. It installs Docker and the management command, obtains the
latest stable signed release, and starts `mp-opt`.

The Server repository, release files, and production container images are
publicly readable. A VPS downloads them anonymously and never needs or stores a
GitHub token. Authenticity comes from the verified release manifest, keyless
signatures, checksums, and digest-pinned images rather than repository privacy.

Before operating a real instance, read the
[self-hosting data-protection policy](SELF-HOSTING-DATA-PROTECTION.md),
[permitted-data rules](PERMITTED-DATA-AND-ACCEPTABLE-USE.md), and
[controller checklist](CONTROLLER-AND-OPERATOR-CHECKLIST.md). Support material
must follow [SUPPORT-DATA-POLICY.md](SUPPORT-DATA-POLICY.md).
Release-facing changes are summarised in [changes.md](changes.md).

The wizard supports:

- a complete single-node installation;
- a new two-node HA installation using fixed `node-a` and `node-b` names;
- conversion from single-node to HA, with a required verified off-VPS backup;
- replacement of either lost HA node using a 15-minute one-time join code; and
- guided restoration after complete server loss.

For HA, start on the intended primary. The wizard pauses only for actions that
must happen outside the server: creating the two narrowly scoped Cloudflare
tokens, adding or checking DNS/email records, opening the generated recovery
key page, and pasting the join code on the second VPS. It then configures peer
trust, direct TLS, DNS-only failover, encrypted replication, SMTP on both
nodes, readiness checks, and automatic failover. No Cloudflare credential is
stored on either VPS.

Setup progress contains only non-secret checkpoint metadata and can be resumed
from **Commission server** in `mp-opt`. See [docs/setup.md](docs/setup.md) for
the exact end-to-end walkthrough and [docs/high-availability.md](docs/high-availability.md)
for the HA safety model.

## Activation email setup

Activation emails are optional and use standard SMTP. The provider token is
read only from the Docker secret `secrets/smtp_token`; do not place it in
`.env` or source control. Configure the non-secret `SMTP_*` values shown in
`.env.example`, then restart the backend and use **Security Settings >
Activation email > Send test** before sending a real activation.

Production delivery requires either STARTTLS or implicit TLS. Configure the
sending domain with the SPF record supplied by the provider, enable the
provider's DKIM records, and publish a DMARC policy. Begin with DMARC reporting
while verifying legitimate delivery, then move to quarantine or reject.

The recommended rollout is:

1. Configure and verify the custom sending domain with the SMTP provider.
2. Pull this release before running an older deployment script. The management
   tooling creates safe empty mount targets for optional node-local credentials;
   enter the SMTP provider token only through the guarded setup flow.
3. Configure the SMTP host, port, username, sender and TLS mode in `.env`.
4. Deploy and confirm that Activation email shows **Ready** in Security
   Settings.
5. Send a test email, then test one real participant before a selected batch.

Activation URLs are generated from `WEBAUTHN_ORIGIN`. Keep this as the exact
public HTTPS origin. Email failures never expose the token in logs or API
responses. Failed and uncertain deliveries invalidate their link immediately;
retrying creates a new one.

For an account's first activation, the email explains that the activation page
will show the applicable processing information. The page derives its
controller, purpose, operational categories, authenticated audience and notice
links from the current published governance, then requires an unchecked
confirmation before WebAuthn starts. The exact statement and policy digest are
recorded atomically with successful activation. This record proves what was
confirmed; it does not replace the controller's assessment of the appropriate
legal basis. Additional-passkey and reset links do not ask again.

For an active account, administrators and event issuers manage both recovery
operations through the user's **Passkeys** action. **Add another passkey** keeps
all existing passkeys and signed-in sessions valid. **Reset passkeys** replaces
every existing passkey and revokes all sessions after the replacement is
registered successfully. Both operations can be shared by email or as a manual
link and QR code, require recent administrator re-authentication, and are
available only per user.

The `secrets/` directory and environment backups are excluded from both Git
and the Docker build context. If a secret was ever staged or committed before
this protection was installed, rotate it before deployment.

## SSH server management

Production administration is available through the graphical SSH menu:

```bash
cd /opt/masterplan
./manage.sh
```

After VPS setup or a deployment with non-interactive sudo access, `mp-opt` opens
the same interface from any directory. The menu handles deployment, optional
SMTP configuration, health checks, logs, encrypted snapshots, database
recovery, root-passkey recovery and guarded secret or domain rotation.
The main selector, nested menus, reports and scrollable or live logs all remain
inside the same full-screen terminal interface.

Infrastructure snapshots use an `age` public recipient. Keep the corresponding
private identity off the VPS. Database wipe, restore, root reset and domain
changes remain unavailable until the CLI creates and deeply verifies a fresh
encrypted rollback snapshot.
