# Sandbox troubleshooting

Read this only when a sandbox step fails.

- **Docker not running**: start Docker Desktop, Colima (`colima start`) or OrbStack. `prv sandbox up` needs `docker info` to succeed on the host.
- **Privileged vs sysbox**: Docker-in-Docker needs `--privileged` unless the host has the sysbox runtime, which `prv` uses automatically. On a Linux host, privileged means root-equivalent on that host. On macOS it is confined to the Docker VM. Prefer sysbox on shared Linux machines.
- **Inner Docker not starting**: `docker exec <sandbox> cat /var/log/dockerd.log`. Common causes are low VM memory or disk (raise Docker Desktop resources), or a stale `prv-docker-<repo>` volume (`docker volume rm prv-docker-<repo>`).
- **Architecture**: images built for amd64 run under emulation on Apple Silicon and can be slow or crash. Prefer multi-arch images, or set `DOCKER_DEFAULT_PLATFORM` inside the sandbox when the project requires amd64.
- **Toolchains**: run `mise install` in `/work/pr` (and `/work/base`) during setup. It reads `.tool-versions`, `.mise.toml`, `.nvmrc`, `.python-version`, `go.mod` and similar. Downloads are cached in the `prv-cache` volume.
- **Private registries and packages**: the sandbox gets no host credentials by design. Ask the user how to provide a scoped, read-only token, and never copy `~/.docker/config.json`, `~/.npmrc` or SSH keys in wholesale.
- **Network**: setup runs online. Call `prv sandbox offline` before testing, so tests that secretly need the internet show up as findings. Use `online` only to fetch something you forgot, then note it in the report.
- **Resources**: `PR_VERIFY_MEMORY=8g PR_VERIFY_CPUS=4` before `prv sandbox up` caps the sandbox.
- **Clean slate**: `prv sandbox down` removes the container and its anonymous volumes. Caches survive in `prv-cache` and `prv-docker-<repo>`.
