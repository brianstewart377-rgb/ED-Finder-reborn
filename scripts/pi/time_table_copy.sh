#!/bin/sh
# Time copying one galaxy table from the current server to the Pi: fetching
# it, loading its rows, and building its indexes. A trial for the real copy
# (docs/pi-move.md, section 2, "How long a restore takes").
#
# Runs on the Pi, as the user `pi`, with PostgreSQL started from
# deploy/pi/postgres.yml. It fetches with the Pi's key for the move
# (~/.ssh/edf_move), and writes nothing on the server. `pi` must be able to
# use docker without sudo (in the docker group): a trial runs for longer
# than sudo remembers a password, and it runs with nobody there to type one.
#
#   sh time_table_copy.sh systems     fetch the galaxy's systems table and load it
#   sh time_table_copy.sh --load-only systems
#                                     load a file an earlier trial fetched
#   sh time_table_copy.sh --remove systems
#                                     remove what a trial loaded, and its file
#
# Every step prints the time (UTC) it started, so the log shows how long each
# took. Links from the table to tables that were not copied cannot be made
# and are reported by pg_restore as "errors ignored"; that is expected.
set -u

SERVER=${EDFINDER_MOVE_SERVER:-root@nb79a3d.mevnode.com}
KEY=${EDFINDER_MOVE_KEY:-$HOME/.ssh/edf_move}
CONTAINER=edfinder-v3-phase4c-full-20260827_r5-postgres
DATABASE=edfinder_v3_phase4c_full_20260827_r5
SCHEMA=v3_gen_phase4c_full_20260827_r5
FOLDER=/srv/data/move

step() { echo "$(date -u '+%H:%M:%S') $*"; }
fail() { step "FAILED: $*"; exit 1; }
psql_pi() { docker exec "$CONTAINER" psql -X -v ON_ERROR_STOP=1 -U edfinder_v3 -d "$DATABASE" "$@"; }

mode=copy
case "${1:-}" in
    --remove) mode=remove; shift ;;
    --load-only) mode=load; shift ;;
esac
table=${1:-}
case "$table" in
    systems|bodies|rings|stations|body_composition|body_signal_current) ;;
    *) echo "Which table? One of: systems bodies rings stations body_composition body_signal_current" >&2
       exit 64 ;;
esac
file=$FOLDER/$table.dump

docker info >/dev/null 2>&1 \
    || fail "$(id -un) cannot use docker without sudo. Run: sudo usermod -aG docker $(id -un), then log out and in again"
[ -d "$FOLDER" ] || fail "$FOLDER does not exist: is the 2 TB drive mounted?"
docker exec "$CONTAINER" pg_isready -q -U edfinder_v3 -d "$DATABASE" \
    || fail "PostgreSQL on the Pi is not running (container $CONTAINER)"

if [ "$mode" = remove ]; then
    psql_pi -q -c "DROP TABLE IF EXISTS $SCHEMA.$table" || fail "could not remove the table"
    rm -f "$file"
    step "removed $SCHEMA.$table and $file"
    exit 0
fi

if [ "$(psql_pi -At -c "SELECT to_regclass('$SCHEMA.$table') IS NOT NULL")" = "t" ]; then
    fail "$SCHEMA.$table already exists on the Pi: remove the last trial first (--remove $table)"
fi

if [ "$mode" = load ]; then
    # A fetch that fails removes its file, so a file here was fetched whole.
    [ -s "$file" ] || fail "$file is not here: fetch it first (without --load-only)"
    step "loading $file, fetched earlier ($(du -h "$file" | cut -f1))"
else
    [ -e "$file" ] && fail "$file is already here: load it with --load-only, or remove it with --remove $table"
    step "fetching $SCHEMA.$table from the server"
    ssh -i "$KEY" -o BatchMode=yes "$SERVER" \
        "docker exec $CONTAINER pg_dump -Fc -Z lz4 -U edfinder_v3 -d $DATABASE -t $SCHEMA.$table" > "$file" \
        || { rm -f "$file"; fail "the fetch"; }
    step "fetched: $(du -h "$file" | cut -f1) on disk"
fi

psql_pi -q -c "CREATE SCHEMA IF NOT EXISTS $SCHEMA" || fail "could not create the schema"
docker exec "$CONTAINER" pg_restore --section=pre-data --no-owner --no-privileges \
    -U edfinder_v3 -d "$DATABASE" "/move/$table.dump" || fail "the table definition"

step "loading the rows"
docker exec "$CONTAINER" pg_restore --section=data -U edfinder_v3 -d "$DATABASE" \
    "/move/$table.dump" || fail "loading the rows"

step "building the indexes and keys, 4 at a time"
docker exec "$CONTAINER" pg_restore --section=post-data -j 4 --no-owner --no-privileges \
    -U edfinder_v3 -d "$DATABASE" "/move/$table.dump" 2>&1 \
    | grep -E "^pg_restore: (warning|error): (errors ignored|could not execute)" | sort | uniq -c

step "finished; what is now on the Pi:"
psql_pi -c "SELECT count(*) AS rows, pg_size_pretty(pg_table_size('$SCHEMA.$table')) AS table_size,
                   pg_size_pretty(pg_indexes_size('$SCHEMA.$table')) AS indexes_size
              FROM $SCHEMA.$table" \
        -c "SELECT indexname AS index FROM pg_indexes WHERE schemaname = '$SCHEMA' AND tablename = '$table' ORDER BY 1"
