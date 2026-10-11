# API and contracts

- Status codes and error bodies for every failure path (400 / 401 / 403 / 404 / 409 / 422 / 429 / 5xx). A 500 for bad input is a bug.
- Backward compatibility: removed or renamed fields, changed types, new required fields, enum values old clients don't know, changed defaults. Test with a client built against the old contract.
- Validation at the boundary: types, ranges, lengths, formats, and unknown fields (ignored or rejected, consistently).
- Pagination and filtering: empty page, last page, page size limits, stable ordering, filters combined, totals that match.
- Idempotency of PUT, DELETE and retried POSTs. Concurrent updates: lost update, ETag or version checks.
- Large payloads and request size limits, slow clients, timeouts and partial reads.
- Auth on every new route (see auth.md). Rate limits on expensive or sensitive endpoints.
- Generated clients, OpenAPI or GraphQL schema and docs updated to match the actual behaviour.
- CLIs: exit codes, `--json` output shape stays stable, no secrets in output, `--help` text matches the behaviour, non-interactive mode never prompts.
