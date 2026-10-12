# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through GitHub's **Security** tab on this repository
(*Security → Report a vulnerability*), or by contacting the maintainer directly.
Use the private channel even for a low-severity finding — a public issue gives
attackers a head start and can expose client data.

Please include:

- what the issue is and where in the system it lives
- how to reproduce it, ideally with a request path or a test
- what an attacker gains
- whether any client data is reachable

You can expect an acknowledgement. Fixes for confirmed issues are prioritised by
severity, and you will be told whether a fix is planned and roughly when.

## What this project does and does not protect

DFIP is a multi-tenant data platform. Its security posture has layers, and this
section describes them honestly rather than as a blanket assurance.

**Application-level authorization is the primary isolation mechanism on the request
path.** Every resource route under the API prefix requires a bearer credential. The
resulting principal carries a role and a company scope; publisher and admin
principals may inspect the working set and publish, client and reader principals may
read only their own published data, and unknown roles are rejected rather than
defaulted.

**Company scoping is enforced in the data access layer** as well as at the route,
so tenant filtering does not depend on a single control.

**PostgreSQL row-level security is defense in depth, not the primary boundary.**
The schema enables and forces RLS on tenant tables, with policies evaluated against
transaction-local tenant context that the API sets per transaction. Important
limits, all of which are documented rather than glossed over:

- A database login that can bypass RLS, or that owns the tables, is **not**
  constrained by those policies.
- Several tables intentionally have no RLS.
- With no database configured, there is no RLS at all and isolation is in-process
  only.

The precise policy-by-policy position, including every table without RLS, is in
[`documentation/V1_RLS.md`](documentation/V1_RLS.md).

**Other implemented controls.**

- Login and initial-setup attempts are rate limited per client IP and username.
- Uploads are bounded by file count, per-file size, and total request size, and
  spreadsheet archives are validated before parsing.
- Excel workbook access uses a short-lived, client-scoped, revocable grant,
  separate from the operator session token, so it can be re-minted or invalidated
  independently.
- Publisher sessions are single-active; establishing a new one invalidates the
  previous.
- Production disables interactive API documentation and schema endpoints.
- The public liveness endpoint reports application liveness and does not claim
  database connectivity.

## Secret handling

- Secret material is supplied through the host environment and is never committed.
- The committed environment template contains no real values and is safe to read.
- The web process holds no secrets and does not proxy the API or open the database.
- The browser receives a bearer token, never a database credential.
- Logs do not emit passwords, authorization headers, cookies, or database URLs.
- Passwords are stored as hashes only.

## Sensitive material that must never reach the repository

Client workbooks, raw source exports, published data, backup dumps, source-archive
copies, `.env` files, credentials, tokens, and connection strings.

`gitignore` covers most of this by default, but review staged files before
committing. See [`documentation/DATA_HANDLING.md`](documentation/DATA_HANDLING.md).

## Further reading

- [`documentation/V1_SECURITY.md`](documentation/V1_SECURITY.md) — exposure surface and residual risk
- [`documentation/V1_RLS.md`](documentation/V1_RLS.md) — RLS policies and their limits
- [`documentation/V1_API_REFERENCE.md`](documentation/V1_API_REFERENCE.md) — route authentication requirements
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — where each control sits in the system