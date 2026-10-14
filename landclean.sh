#!/bin/bash
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
SP="$(python "$KIT/kitpaths.py" state)"
source "$KIT/wavelock.sh"
if ! wave_lock_acquire "${LANDCLEAN_TRIES:-1}"; then
  echo "REFUSED: $LOCK is held by an integration; run landclean.sh when it finishes"
  exit 1
fi
export DQIX_MAIN_REPO="${DQIX_MAIN_REPO:-$(python "$KIT/kitpaths.py" repo)}"
python "$KIT/landclean.py" "$@"
RC=$?
wave_lock_release
exit $RC
