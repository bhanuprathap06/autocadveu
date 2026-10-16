# Security and privacy

## Threat model (summary)

Assets: proprietary CAD models and drawings, simulation results, requirements, account credentials, invitation links.
Adversaries: unauthenticated internet users, authenticated users of other organisations or rooms, malicious files,
compromised browser extensions, and misconfiguration.

## Controls

| Area | Control |
|---|---|
| Passwords | Argon2id hashing; length and common-password checks; rate-limited login with audit of failures. |
| Sessions | Server-side sessions; opaque token in an HttpOnly, SameSite=Lax cookie (`Secure` enforced in production); session list and remote revoke; password change signs out other sessions. |
| CSRF | Session-bound double-submit token (`x-csrf-token`) on every state-changing request; WebSocket checks the `Origin` header. |
| MFA | TOTP (RFC 6238) with QR enrolment; login requires the second factor once enabled. |
| Authorisation | Capability checks (view, comment, edit, run_simulation, export, manage) on every request; objects in projects the caller cannot access return 404 (no existence oracle). Tested for cross-project and cross-room access. |
| Invitations | Single-use, expiring, bound to the invited email address, stored hashed; revocable; acceptance audited. |
| Uploads | Streaming size limit, extension allow-list, content sniffing, rejection of HTML/SVG/scripts/executables; CAD parsed only inside memory-limited kernel workers; downloads are `attachment` with `nosniff`. Malware scanning is an external integration (files are marked "not scanned"). |
| Kernel/solver isolation | Separate processes with address-space limits and wall-clock timeouts; crashes contained; no user-supplied code or shell commands are ever executed. |
| Data at rest | Postgres and object storage on infrastructure you control; S3 server-side encryption requested on AWS; checksums on every object and verification on read. |
| Transport | TLS via Caddy (automatic certificates), HSTS, strict CSP (no third-party origins), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`. |
| Secrets | Only from environment variables; never in source, browser bundles, logs or reports. `/metrics` is not exposed through the proxy. |
| External AI | Off by default; per-request user opt-in; only problem statements and requirements are sent, never CAD files; outputs schema-validated. |
| Audit | Append-only audit events for auth, sharing, invitations, saves, restores, exports, downloads, simulations and admin actions, with request IDs. |
| Availability | Rate limits on authentication, saves, recompute, solves, exports, simulations and AI; durable job queue with stale-job recovery. |

## Known limitations

- `script-src 'unsafe-inline'` is required by Next.js bootstrap scripts until nonce-based CSP is enabled (backlog).
- No SSO/SAML/OIDC yet (backlog); organisations rely on email + password + TOTP.
- Malware scanning requires an external scanner (ClamAV) integration.

## Reporting

Report vulnerabilities privately to the maintainers; do not open public issues for security problems.
