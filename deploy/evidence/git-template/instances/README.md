# Instance evidence

Create one directory per canonical instance UUID. The only automatic ingestion
path is `bundles/<bundle_id>/`, containing `evidence.bundle` and
`bundle.sha256`. The portable bundle includes the complete trust declarations,
signed chain, deterministic Markdown and HTML summaries, and offline verifier.

Never include live database content, backup archives, names, personal email addresses, task content, or schedules.
