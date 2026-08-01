# Self-hosting and data protection

Masterplan Optimiser is self-hosted software. The organisation or person that
decides why and how a deployment processes personal data is normally the data
controller. Publishing or maintaining this software does not make the project
maintainer a processor for an independently operated instance.

The software provides technical controls that can support GDPR and Swiss FADP
readiness. It does not certify a deployment, select a legal basis, set an
appropriate retention period, conclude processor agreements, or guarantee
compliance.

## Controller responsibilities

Before processing real participant data, the controller must:

- identify itself and provide a working privacy contact;
- publish an accurate instance privacy notice and rights procedure;
- document the purpose and legal basis for each supported category of data;
- configure justified retention periods and deletion procedures;
- identify hosting, DNS, email, push, backup and other external providers;
- conclude any processor agreements and assess international transfers;
- protect VPS, registrar, DNS, SMTP, backup and administrator accounts;
- test restores, failover, access revocation and deletion regularly; and
- keep an incident and data-subject request procedure appropriate to its use.

Configure the local legal centre at `/admin/governance`. Public information is
available at `/privacy`, `/legal`, `/data-policy`, `/retention`, `/rights`,
`/processors` and `/licence` after the root administrator publishes it.

## Data boundary

Masterplan is intended for operational scheduling, access management and
closely related communications. It is not a personnel, health, safeguarding,
disciplinary or profiling system. See
[PERMITTED-DATA-AND-ACCEPTABLE-USE.md](PERMITTED-DATA-AND-ACCEPTABLE-USE.md).

## Providers and support

The official project has no central telemetry and receives no deployment,
participant, acknowledgement or evidence data by default. A controller must
describe the providers it actually enables. Support must follow
[SUPPORT-DATA-POLICY.md](SUPPORT-DATA-POLICY.md).

## Licences

The source is licensed under `AGPL-3.0-only`. Operators who modify the software
and make that modified version available over a network must comply with the
licence, including its corresponding-source obligations. Third-party software
retains its own licence. See [LICENSE](LICENSE) and
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

