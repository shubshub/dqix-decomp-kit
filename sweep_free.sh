#!/usr/bin/env bash
# Launch the free repair sweep across N shards. Zero tokens: every shard is a local compile plus
# wgate, so a bad candidate costs CPU and nothing else.
#
#   bash sweep_free.sh [nshards]
#
# Per-shard logs go to $SP/wlog/repairsweep_s<i>.log. Old logs are truncated first: a stale log from
# a previous run reads as live progress and has faked both usage limits and strikes before.
set -u
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
# The interpreter this kit is running under. A bare `python` is python3 under Git Bash on
# Windows and does not exist at all on a Debian that keeps its packages in a virtualenv, so
# every call below asks the kit which interpreter to use instead of assuming one.
PY="${DQIX_PYTHON:-$(python3 "$KIT/kitpaths.py" py)}"
# ninja and anything else installed beside it live in the same directory, and they are on
# PATH only while the venv is ACTIVATED. Put them there for this script's children.
export PATH="$(dirname "$PY"):$PATH"
SP="$("$PY" "$KIT/kitpaths.py" state)"
REPO="$("$PY" "$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })/kitpaths.py" repo)"
N="${1:-4}"

mkdir -p "$SP/wlog"
for i in $(seq 1 "$N"); do
    : > "$SP/wlog/repairsweep_s$i.log"
done

for i in $(seq 1 "$N"); do
    (cd "$REPO" && REPAIR_SHARD="$i/$N" "$PY" "$KIT/repairsweep.py" \
        >> "$SP/wlog/repairsweep_s$i.log" 2>&1) &
    echo "shard $i/$N pid $!"
done
wait
