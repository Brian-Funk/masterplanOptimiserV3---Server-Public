# Support data policy

The project does not need production personal data to investigate normal
defects. Do not attach or paste databases, encrypted backups, `.env` files,
private keys, tokens, activation links, passkey material, email addresses,
participant names, schedules or raw production logs into an issue or support
request.

Provide the least sensitive reproduction possible:

- the affected signed release or test commit;
- deployment topology and relevant non-secret configuration names;
- a minimal reproduction using invented data;
- redacted error text and timestamps; and
- the result of built-in health, configuration and evidence verification.

Security vulnerabilities must follow [SECURITY.md](SECURITY.md). Controllers
remain responsible for deciding whether support material may be disclosed and
for removing it when the support purpose ends. The maintainer must not request
a production database or recovery snapshot.

