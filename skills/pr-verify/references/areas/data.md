# Data, persistence and migrations

- Run the migration **up, down and up again** against a database holding realistic existing rows: nulls in old columns, legacy enum values, duplicates the new constraint forbids, the largest table size you can reasonably create.
- Rolling deploy: the old code must work against the new schema, and the new code against data written by the old code. Check old and new servers side by side.
- Transactions: what happens if the process dies or the database errors between two writes? Look for partial state, orphans and double charges. Check that every multi-step write is atomic or idempotent.
- Uniqueness and concurrency: two inserts with the same natural key at once, check-then-insert races, unique index vs application check.
- Deletes: soft vs hard, cascades, foreign keys, references from other services and caches, and whether undelete brings back stale state.
- Caches: invalidated on every write path, keys include tenant and user identity, TTL vs eviction policy (keys with a TTL can be evicted under `volatile-*`).
- Queries: N+1 across lists, missing index for new filters, pagination with ties, ordering stability, filters applied before LIMIT.
- Data types: money as decimal, time zones stored as UTC, string length limits vs column sizes, encoding round-trips (emoji, RTL, null bytes).
- Backfills and data migrations: idempotent, resumable, batched, and they log counts.
