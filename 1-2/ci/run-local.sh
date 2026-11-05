#!/usr/bin/env bash
set -euo pipefail

repository=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
state=${LAB2_CI_STATE:-${XDG_CACHE_HOME:-$HOME/.cache}/mirea-security-lab2}
mkdir -p "$state/artifacts" "$state/cache"
if ! command -v act >/dev/null || ! docker info >/dev/null 2>&1; then
    printf 'Install act and start Docker before running local CI.\n' >&2
    exit 1
fi
export DOCKER_HOST=${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}
cd "$repository"
exec act workflow_dispatch \
    --workflows .github/workflows/main.yml \
    --eventpath 1-2/ci/act-event.json \
    --platform ubuntu-24.04=catthehacker/ubuntu:act-24.04@sha256:c58e2b364da03b0c804c7d660f2ecbedf2f221a382b9baa0b344b0144780ff43 \
    --artifact-server-path "$state/artifacts" \
    --cache-server-path "$state/cache" \
    --container-daemon-socket /var/run/docker.sock \
    "$@"
