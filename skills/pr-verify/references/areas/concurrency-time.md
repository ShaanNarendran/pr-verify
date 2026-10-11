# Concurrency, jobs and time

- For every read-modify-write in the diff, run two at once (threads, `asyncio.gather`, two HTTP clients, `xargs -P`) and assert the final state.
- A check followed by an act (exists → create, valid → use) is a race until proven otherwise. Interleave a competing change between them, for example by pausing a fake or hooking the DB lookup.
- Retries: is the operation idempotent? Does a retry after a timeout duplicate side effects such as emails, charges or webhooks?
- Blocking calls in async code (`subprocess.run`, synchronous DNS, file I/O, CPU-heavy loops) freeze the event loop for every request. Measure the loop lag while the operation runs.
- Scheduled jobs: compute the actual schedule (runs per day) from the cron expression or library semantics. Look for overlapping runs, jobs running on every replica, and jobs that never finish within their timeout.
- Locks and leases: released on error, lost when the holder dies, safe across processes and hosts.
- Time: inject or freeze the clock. Test expiry at exactly now, ±1 unit, DST transitions, month and year ends, leap days, and local time zones that are not UTC.
- Precision: equal timestamps across seconds and milliseconds. Two events in the same unit must still be ordered correctly.
- Clock skew between servers: markers or ordering written by one host and compared on another.
- Cooldowns and rate limits: what happens on the boundary, after a restart, and when several workers share them.
