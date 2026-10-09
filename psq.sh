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
SP="$(python "$KIT/kitpaths.py" state)"
MODE="${1:---count}"
KIND="${3:-all}"
[ "$1" = "--kind" ] && { KIND="$2"; MODE="--count"; }

case "$KIND" in
  work) PAT='pull_worker\.sh|pull_all\.sh|supervise\.sh|run_all\.sh' ;;
  job)  PAT='integrate_fast\.sh|integrate_all\.sh|finish_wave\.sh|ov_recover\.py|repairsweep\.py|colorsweep\.py|proppurge\.py|transmassive\.py' ;;
  *)    PAT='pull_worker\.sh|pull_all\.sh|supervise\.sh|run_all\.sh|integrate_fast\.sh|integrate_all\.sh|finish_wave\.sh|ov_recover\.py|repairsweep\.py|colorsweep\.py|proppurge\.py|transmassive\.py' ;;
esac

if [ "$MODE" = "--list" ]; then
  python "$KIT/procq.py" --list --match "$PAT" --notmatch 'psq\.sh' 2>/dev/null \
    | awk -F'@@@' '{print $1 "  " substr($3, 1, 100)}'
else
  python "$KIT/procq.py" --count --match "$PAT" --notmatch 'psq\.sh' 2>/dev/null
fi
