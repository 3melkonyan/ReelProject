# Security

Do not post credentials, student records, face references, recordings, private links or unredacted logs in public issues.

For a sensitive vulnerability, use GitHub's **Security → Report a vulnerability** option if private vulnerability reporting is enabled. If it is unavailable, open an issue requesting a private contact channel without including exploit details or affected personal data. There is no guaranteed response-time policy at present.

Include the affected commit, prerequisites, impact and a minimal reproduction using synthetic data. Never test against another person's account or a live classroom installation without permission.

## Deployment boundaries

- Keep `.env`, `secrets/`, `config.yaml`, database files, recordings, references and avatars private. Ignore rules do not remove data already tracked or present in Git history.
- Bind the dashboard to loopback by default. External deployment needs HTTPS, trusted proxy configuration and reviewed access settings.
- Unlisted YouTube publication is link-accessible, not private storage.
- Upload-review identity crops are restricted to the uploader and administrators. Keep reference copies and candidate artifacts private.
- Administrator lesson deletion tracks remote cleanup and retains a local database-state backup and source recordings; it is not secure erasure.
- Automated blurring is not guaranteed anonymization. The current policy can leave uncertain faces visible.
- Back up local state before upgrades; use a consistent SQLite backup.
- Test notification destinations and group permissions with synthetic data before production use.

See [Architecture](docs/ARCHITECTURE.md) for access boundaries and [Usage](docs/USAGE.md) for development dry-run behavior.
