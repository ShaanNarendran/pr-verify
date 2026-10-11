# Configuration, build and dependencies

- Every new setting: its default, behaviour when it's unset, invalid or the wrong type, and both values of a boolean flag. Is the default safe?
- Environment-specific behaviour: dev vs prod paths, and settings that only exist in one deployment method (compose vs Helm vs bare metal). Are all of them updated?
- Dependencies: new packages are maintained and pinned or locked, the licence is compatible, there are no known advisories (run the ecosystem's audit), and nothing large was pulled in for a few lines.
- Lockfile changes match the manifest change, with no unrelated upgrades smuggled in.
- Docker and CI: the image still builds from clean, layers are cached sensibly, no secrets are baked in, it runs as non-root where expected, and health checks still pass.
- Migrations and startup order in compose and Helm: the app waits for its dependencies, and init jobs run before the app.
- Feature flags: behaviour with the flag both off and on, and the path for removing the flag later.
