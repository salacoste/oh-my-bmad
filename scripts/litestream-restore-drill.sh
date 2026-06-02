#!/usr/bin/env bash
# Story 13.3 / FR71 — hermetic litestream replicate→restore drill.
#
# Proves the litestream restore MECHANISM end-to-end with NO cloud credentials:
# seed a WAL SQLite db → replicate to a local `file` replica → simulate host loss
# (wipe the db + meta) → `litestream restore` → assert PRAGMA integrity_check +
# exact row count. Exits non-zero on any failure, so it is CI-usable
# (.github/workflows/nightly.yml) AND locally runnable (`just litestream-restore-drill`).
#
# This exercises the same litestream restore code path the operator's
# `just restore-from-litestream` uses (config-based restore of state.sqlite3),
# minus the S3 transport — the transport is the operator's own bucket and is
# validated live (ADR-0007 first-enable item), not in this hermetic drill.
set -euo pipefail

LITESTREAM_IMAGE="litestream/litestream:${LITESTREAM_VERSION:-0.3.13}"
ROWS="${DRILL_ROWS:-500}"
# Use /tmp explicitly (NOT $TMPDIR): on macOS $TMPDIR is /var/folders/... which
# is usually NOT in Docker Desktop's shared paths, so a bind mount there is empty
# and the litestream container can't see the db. /tmp (→/private/tmp) is shared.
work="$(mktemp -d /tmp/omb-ls-drill.XXXXXX)"
cname="omb-ls-drill-$$"
# Run the litestream containers as the INVOKING uid:gid (not the image default
# root). On Linux CI a root-run container writes the restored /data/state.sqlite3
# root-owned, so the host-side python3 verify step (the unprivileged `runner`
# user) gets "sqlite3.OperationalError: unable to open database file". macOS
# Docker Desktop hid this — it maps bind-mount ownership to the host user
# regardless of container uid. Passing --user makes the round-trip files
# runner-owned on BOTH platforms; litestream only needs to read/write the
# bind-mounted /data + /replica dirs, which mktemp created owned by this uid.
ls_user="$(id -u):$(id -g)"

cleanup() {
    docker rm -f "${cname}" >/dev/null 2>&1 || true
    rm -rf "${work}"
}
trap cleanup EXIT

mkdir -p "${work}/data" "${work}/replica"

echo "→ [1/5] seed a WAL SQLite db (${ROWS} rows) at the registry-state path shape"
python3 - "${work}/data/state.sqlite3" "${ROWS}" <<'PYEOF'
import sqlite3, sys
db, rows = sys.argv[1], int(sys.argv[2])
c = sqlite3.connect(db)
c.execute("PRAGMA journal_mode=WAL;")
c.execute("CREATE TABLE drill(id INTEGER PRIMARY KEY, v TEXT);")
c.executemany("INSERT INTO drill(v) VALUES(?)", [(f"r{i}",) for i in range(rows)])
c.commit()
c.close()
print(f"   seeded {rows} rows, journal_mode=WAL")
PYEOF

cat > "${work}/litestream.yml" <<'CFGEOF'
dbs:
  - path: /data/state.sqlite3
    replicas:
      - type: file
        path: /replica/state
CFGEOF

echo "→ [2/5] replicate to a local file replica (no cloud)"
docker run --rm -d --name "${cname}" \
    --user "${ls_user}" \
    -v "${work}/data:/data" \
    -v "${work}/replica:/replica" \
    -v "${work}/litestream.yml:/etc/litestream.yml:ro" \
    "${LITESTREAM_IMAGE}" replicate -config /etc/litestream.yml >/dev/null

# Wait for the first snapshot to land (litestream snapshots on the first sync
# cycle, ≤1s; poll up to 15s to be CI-robust).
snapshot_ok=""
for _ in $(seq 1 60); do   # poll up to 30s — margin for slow CI runners (review MEDIUM)
    if ls "${work}"/replica/state/generations/*/snapshots/* >/dev/null 2>&1; then
        snapshot_ok=1
        break
    fi
    sleep 0.5
done
if [ -z "${snapshot_ok}" ]; then
    echo "✗ FAIL: no snapshot appeared in the replica within 30s" >&2
    echo "--- litestream container logs ---" >&2
    docker logs "${cname}" 2>&1 | tail -15 >&2 || true   # grab logs BEFORE --rm stop removes it
    docker stop "${cname}" >/dev/null 2>&1 || true
    exit 1
fi
docker stop "${cname}" >/dev/null 2>&1 || true
echo "   snapshot written to the file replica"

echo "→ [3/5] simulate host loss (wipe db, -wal/-shm, and the .state.sqlite3-litestream meta dir)"
rm -rf "${work}/data"/* "${work}/data"/.[!.]* 2>/dev/null || true
if [ -e "${work}/data/state.sqlite3" ]; then
    echo "✗ FAIL: db still present after wipe" >&2
    exit 1
fi

echo "→ [4/5] litestream restore from the replica (config-based, latest generation)"
docker run --rm \
    --user "${ls_user}" \
    -v "${work}/data:/data" \
    -v "${work}/replica:/replica" \
    -v "${work}/litestream.yml:/etc/litestream.yml:ro" \
    "${LITESTREAM_IMAGE}" restore -config /etc/litestream.yml /data/state.sqlite3

echo "→ [5/5] verify PRAGMA integrity_check + exact row count"
python3 - "${work}/data/state.sqlite3" "${ROWS}" <<'PYEOF'
import sqlite3, sys
db, rows = sys.argv[1], int(sys.argv[2])
c = sqlite3.connect(db)
integ = c.execute("PRAGMA integrity_check").fetchone()[0]
if integ != "ok":
    print(f"✗ FAIL: integrity_check = {integ!r}", file=sys.stderr)
    sys.exit(1)
n = c.execute("SELECT COUNT(*) FROM drill").fetchone()[0]
last = c.execute("SELECT v FROM drill ORDER BY id DESC LIMIT 1").fetchone()[0]
if n != rows or last != f"r{rows-1}":
    print(f"✗ FAIL: expected {rows} rows (last r{rows-1}), got {n} (last {last})", file=sys.stderr)
    sys.exit(1)
print(f"   integrity=ok, {n} rows recovered, last={last}")
PYEOF

echo "✓ litestream restore drill PASS — replicate→loss→restore round-trip verified"
