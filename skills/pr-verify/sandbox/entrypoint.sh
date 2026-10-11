#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Start the inner Docker daemon for the PR's own containers, then idle; prv drives everything with `docker exec`.
set -e
mkdir -p /cache
dockerd --host=unix:///var/run/docker.sock >/var/log/dockerd.log 2>&1 &
exec sleep infinity
