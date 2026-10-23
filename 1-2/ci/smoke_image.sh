#!/usr/bin/env bash
set -euo pipefail

image=${1:?Usage: smoke_image.sh IMAGE}
volume="mirea-lab2-smoke-$(openssl rand -hex 8)"
docker volume create --label ru.mirea.security.lab2=smoke "$volume" >/dev/null
trap 'docker volume rm "$volume" >/dev/null' EXIT

run() {
    docker run --rm --network none --read-only --cap-drop ALL \
        --security-opt no-new-privileges --tmpfs /tmp \
        --mount "type=volume,src=$volume,dst=/data" \
        -e FM_USER -e FM_PASSWORD "$image" "$@"
}

uid=$(docker run --rm --network none --entrypoint id "$image" -u)
test "$uid" = 10001
run --help >/dev/null
export FM_USER=smoke
export FM_PASSWORD
FM_PASSWORD=$(openssl rand -hex 24)
run register >/dev/null
run login >/dev/null
unset FM_PASSWORD FM_USER
run file create persisted.txt --text 'persistent data' >/dev/null
test "$(run file read persisted.txt)" = 'persistent data'
docker run --rm --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges \
    --mount "type=volume,src=$volume,dst=/data" \
    --entrypoint python "$image" -c '
import sqlite3
with sqlite3.connect("/data/manager.sqlite3") as database:
    assert database.execute("SELECT COUNT(*) FROM Users").fetchone()[0] == 1
    assert database.execute("SELECT size FROM Files WHERE path = ?", ("persisted.txt",)).fetchone() == (15,)
    assert database.execute("SELECT COUNT(*) FROM Operations WHERE action = ? AND status = ?", ("file_create", "success")).fetchone()[0] == 1
'
printf 'Image smoke: non-root CLI, SQLite, persistence across containers OK\n'
