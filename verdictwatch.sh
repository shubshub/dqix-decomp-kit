#!/bin/bash
# Emit one line per worker verdict, across EVERY module. Usage: bash verdictwatch.sh [seconds]
#
# Tailing named slot logs does not work: the log is per MODULE (`pull_<mod>_s<slot>.log`) and the
# dispatcher picks a different module on almost every claim, so a tail of pull_main_s1.log is blind
# to every verdict on ov003, ov006, ov024... and a new module's log does not exist when the tail
# starts, so a glob does not fix it either. This re-globs each pass and remembers the byte offset it
# has already reported per file, so nothing is missed and nothing is repeated.
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
# The interpreter this kit is running under. A bare `python` is python3 under Git Bash on
# Windows and does not exist at all on a Debian that keeps its packages in a virtualenv, so
# every call below asks the kit which interpreter to use instead of assuming one.
PY="${DQIX_PYTHON:-$(python3 "$KIT/kitpaths.py" py)}"
# ninja and anything else installed beside it live in the same directory, and they are on
# PATH only while the venv is ACTIVATED. Put them there for this script's children.
export PATH="$(dirname "$PY"):$PATH"
SP="$("$PY" "$KIT/kitpaths.py" state)"
EVERY="${1:-60}"
STATE="$SP/wlog/.verdict_offsets"
LOG="$SP/wlog/verdictwatch.log"

if [ -f "$SP/verdictwatch.pid" ] && kill -0 "$(cat "$SP/verdictwatch.pid" 2>/dev/null)" 2>/dev/null; then
  exit 0
fi
echo $$ > "$SP/verdictwatch.pid"
trap 'rm -f "$SP/verdictwatch.pid"' EXIT
mkdir -p "$SP/wlog"
touch "$STATE"

while :; do
  for f in "$SP"/wlog/pull_*_s*.log; do
    [ -f "$f" ] || continue
    b=$(basename "$f")
    off=$(grep -m1 "^$b " "$STATE" 2>/dev/null | awk '{print $2}')
    case "$off" in ''|*[!0-9]*) off=$(wc -c < "$f") ;; esac
    now=$(wc -c < "$f")
    if [ "$now" -gt "$off" ]; then
      tail -c "+$((off + 1))" "$f" \
        | grep -E '^[0-9]{2}:[0-9]{2} s[0-9]+ (MATCH|miss|presweep|postsweep)' \
        | sed "s|^|${b%.log} |"
      grep -v "^$b " "$STATE" > "$STATE.tmp" 2>/dev/null
      echo "$b $now" >> "$STATE.tmp"
      mv -f "$STATE.tmp" "$STATE"
    elif [ "$now" -lt "$off" ]; then
      grep -v "^$b " "$STATE" > "$STATE.tmp" 2>/dev/null
      echo "$b $now" >> "$STATE.tmp"
      mv -f "$STATE.tmp" "$STATE"
    fi
  done
  sleep "$EVERY"
done
