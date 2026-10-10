#!/bin/bash
# How many DQIX jobs are actually alive. Prints a count, or `--list` for pid + command line.
#
#   bash psq.sh              # count of integration/sweep/worker processes
#   bash psq.sh --list       # one line each
#   bash psq.sh --kind work  # workers only (a claude.exe with ` -p `)
#
# EVERY AD-HOC `Get-CimInstance ... -match 'finish_wave'` MATCHES ITS OWN QUERY. The command line of
# the powershell process running the query contains the pattern text, and so do the two bash shells
# wrapping it, so a check for "is anything running" answers "yes, 4" against an idle machine. That
# read as a live integration for 36 minutes on 2026-09-09 while nothing at all was running. The fix
# is not a cleverer pattern -- it is excluding the querying process and its own ancestors by PID.
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
# The interpreter this kit is running under. A bare `python` is python3 under Git Bash on
# Windows and does not exist at all on a Debian that keeps its packages in a virtualenv, so
# every call below asks the kit which interpreter to use instead of assuming one.
PY="${DQIX_PYTHON:-$(python3 "$KIT/kitpaths.py" py)}"
# ninja and anything else installed beside it live in the same directory, and they are on
# PATH only while the venv is ACTIVATED. Put them there for this script's children.
export PATH="$(dirname "$PY"):$PATH"
SP="$("$PY" "$KIT/kitpaths.py" state)"
MODE="${1:---count}"
KIND="${3:-all}"
[ "$1" = "--kind" ] && { KIND="$2"; MODE="--count"; }

case "$KIND" in
  work) PAT='pull_worker\.sh|pull_all\.sh|supervise\.sh|run_all\.sh' ;;
  job)  PAT='integrate_fast\.sh|integrate_all\.sh|finish_wave\.sh|ov_recover\.py|repairsweep\.py|colorsweep\.py|proppurge\.py|transmassive\.py' ;;
  *)    PAT='pull_worker\.sh|pull_all\.sh|supervise\.sh|run_all\.sh|integrate_fast\.sh|integrate_all\.sh|finish_wave\.sh|ov_recover\.py|repairsweep\.py|colorsweep\.py|proppurge\.py|transmassive\.py' ;;
esac

if [ "$MODE" = "--list" ]; then
  "$PY" "$KIT/procq.py" --list --match "$PAT" --notmatch 'psq\.sh' 2>/dev/null \
    | awk -F'@@@' '{print $1 "  " substr($3, 1, 100)}'
else
  "$PY" "$KIT/procq.py" --count --match "$PAT" --notmatch 'psq\.sh' 2>/dev/null
fi
