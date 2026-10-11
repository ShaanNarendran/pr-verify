# SPDX-License-Identifier: Apache-2.0
# pr-verify sandbox: a disposable box with its own Docker daemon, so a PR's compose stack runs isolated
# from the host. Toolchains install on demand (mise reads .tool-versions, .nvmrc, .python-version, go.mod ...)
# into the /cache volume, so the image stays small and repeat runs reuse them.
# The box is disposable, so CI's plain `pip install` may write to the system Python.
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg git jq make build-essential pkg-config unzip xz-utils zip \
      python3 python3-venv python3-pip python-is-python3 sqlite3 iproute2 iptables procps less openssh-client \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
 && rm -rf /var/lib/apt/lists/*

# mise (toolchains) and uv (Python) are small; everything they download goes to /cache.
RUN curl -fsSL https://mise.run | sh && curl -LsSf https://astral.sh/uv/install.sh | sh

ENV PATH=/root/.local/bin:/cache/mise/shims:$PATH \
    MISE_DATA_DIR=/cache/mise \
    MISE_CACHE_DIR=/cache/mise-cache \
    MISE_YES=1 \
    MISE_IDIOMATIC_VERSION_FILE_ENABLE_TOOLS=node,python,ruby,go,java \
    UV_CACHE_DIR=/cache/uv \
    PIP_CACHE_DIR=/cache/pip \
    npm_config_cache=/cache/npm \
    npm_config_store_dir=/cache/pnpm-store \
    COREPACK_HOME=/cache/corepack \
    GOMODCACHE=/cache/go/mod \
    GOCACHE=/cache/go/build \
    CARGO_HOME=/cache/cargo \
    PLAYWRIGHT_BROWSERS_PATH=/cache/ms-playwright \
    XDG_CACHE_HOME=/cache/xdg \
    CI=true \
    PIP_BREAK_SYSTEM_PACKAGES=1

COPY entrypoint.sh /usr/local/bin/pr-verify-entrypoint
RUN chmod +x /usr/local/bin/pr-verify-entrypoint
WORKDIR /work
ENTRYPOINT ["pr-verify-entrypoint"]
