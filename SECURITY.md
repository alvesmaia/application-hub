# Security policy

## How to report a vulnerability

**Do not open a public issue.** Use GitHub's private reporting: the **Security › Report a vulnerability**
tab (GitHub private vulnerability reporting) of this repository. Include steps to reproduce, the affected version/commit
and the expected impact. We respond as soon as possible; the fix and the disclosure are coordinated
with the reporter.

## Supported versions

| Branch | Support |
|---|---|
| `main` (Python hub in `platform-py/`, apps and SDK in `platform/`) | yes — receives fixes |
| old tags/releases | no |

## Scope

- In scope: the hub (`platform-py/hub`), the SDK (`platform/sdk-python`), the `hub-cli` (`platform-py/cli`),
  the gateway, authentication/RBAC, MCP/OAuth, the vault and the deployment scripts.
- Out of scope: the **demo** credentials and apps (`docker-compose.yml`, `HUB_DEMO_USERS=1`,
  `*-demo-*` passwords — these are public on purpose), third-party dependencies (report to the
  original project) and the limitations already listed in `docs/PENDENCIAS.md` ("Known limitations").

There is no bug bounty program.

---

## Security policy (English)

Please **do not open public issues** for vulnerabilities. Report privately through GitHub's
**Security › Report a vulnerability** (private vulnerability reporting) on this repository, with
reproduction steps, affected commit and impact. Supported branch: `main`
(Python hub in `platform-py/`, apps and SDK in `platform/`). Demo credentials and demo apps are public by design and out of scope,
as are third-party dependencies and the known limitations in `docs/PENDENCIAS.md`. There is no bug bounty.
