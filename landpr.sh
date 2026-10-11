#!/bin/bash
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
SP="$(python "$KIT/kitpaths.py" state)"
source "$KIT/wavelock.sh"
[ $# -gt 0 ] || { echo "usage: bash landpr.sh <number>..."; exit 2; }
if ! wave_lock_acquire 1; then
  echo "REFUSED: $LOCK is held by an integration; run landpr.sh when it finishes"
  exit 1
fi
export DQIX_MAIN_REPO="${DQIX_MAIN_REPO:-$(python "$KIT/kitpaths.py" repo)}"
python "$KIT/landpr.py" "$@"
RC=$?
wave_lock_release
[ $RC -eq 0 ] || exit $RC
python "$KIT/selfcheck.py" | tail -1
