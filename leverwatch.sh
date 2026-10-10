#!/usr/bin/env bash
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
# The interpreter this kit is running under. A bare `python` is python3 under Git Bash on
# Windows and does not exist at all on a Debian that keeps its packages in a virtualenv, so
# every call below asks the kit which interpreter to use instead of assuming one.
PY="${DQIX_PYTHON:-$(python3 "$KIT/kitpaths.py" py)}"
SP="$("$PY" "$KIT/kitpaths.py" state)"
mkdir -p "$SP/wlog"
ONCE=0
[ "$1" = "--once" ] && { ONCE=1; shift; }
INTERVAL="${1:-300}"
SEEN="${LEVERWATCH_SEEN:-$SP/wlog/leverwatch_seen.txt}"
touch "$SEEN"

while :; do
  fired=0
  while read -r addr text; do
    [ -n "$addr" ] || continue
    grep -qxF "$addr" "$SEEN" && continue
    echo "$addr" >> "$SEEN"
    echo "LEVER NEEDS PROMOTING $addr: $text"
    fired=1
  done < <("$PY" "$KIT/levercheck.py" --keys 2>/dev/null)
  [ "$ONCE" = 1 ] && [ "$fired" = 1 ] && exit 0
  sleep "$INTERVAL"
done
