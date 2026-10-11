# Auth, sessions and permissions

- Build the **role × resource × ownership** decision table for every changed endpoint or action: anonymous, owner, co-author or collaborator, same-team member, other team, reviewer or moderator, admin, deactivated user. Test every cell that the change could affect.
- Hidden vs forbidden: a resource the caller may not see should look the same as one that doesn't exist (same status, same message, same timing). A different answer leaks that it exists.
- Token lifecycle: issue, use, refresh, rotate, revoke, expire. Then repeat with a token issued just before a revocation, a refresh racing a logout, a token from before a password change, and a token for a since-deleted user.
- Revocation must survive the next login, refresh and server restart. It must also fail closed when the revocation store is down.
- Password and credential changes: other sessions die, the current device keeps working only if intended, forced-change flags can't silently lapse, and errors never claim success.
- Every new route or handler declares its auth dependency. Search for routes added without one.
- Secrets never appear in logs, error messages, URLs, analytics or responses. That includes tokens echoed in `--json` CLI output.
- Brute force: rate limits on login, reset, code-exchange and invite endpoints. One-time codes must be single-use and short-lived.
- SSO and OAuth: state or nonce checked, redirect targets allow-listed (`//evil.com`, `/\evil.com`, control characters), and account linking can't take over an existing email.
