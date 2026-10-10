#!/bin/bash
# Integrate every module that has staged work, one at a time, then report. Run with the fleet STOPPED.
#
# finish_wave serialises on wave.lock anyway, but running these sequentially and only with zero
# workers alive is the condition the whole integration path assumes: its preflight reverts tracked
# files and moves untracked ones around, so a worker writing during it loses work (four matched
# ov017 functions were swept into quarantine that way).
#
# Each module costs a full rebuild, so this is slow by nature -- the point is to clear the backlog
# completely before a measurement window, not to be quick.
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
LOG="$SP/wlog/integrate_all.log"

echo "=== integrate_all $(date '+%m-%d %H:%M') ===" >> "$LOG"

# Biggest backlog first: each wave costs the same rebuild whether it lands 1 function or 37.
for d in $(ls -d "$SP"/staging/*/ 2>/dev/null \
           | while read -r x; do echo "$(ls "$x"*.cpp 2>/dev/null | wc -l) $x"; done \
           | sort -rn | awk '$1>0{print $2}'); do
  tag=$(basename "$d")                 # main | ovNNN
  mod=${tag#ov}                        # finish_wave takes main | NNN
  n=$(ls "$d"*.cpp 2>/dev/null | wc -l)
  echo "$(date '+%H:%M') --- $tag: $n staged ---" >> "$LOG"
  bash "$KIT/finish_wave.sh" "$mod" >> "$SP/wlog/int_${tag}.log" 2>&1
  tail -1 "$SP/wlog/int_${tag}.log" >> "$LOG"
done

echo "$(date '+%H:%M') === done ===" >> "$LOG"
"$PY" "$KIT/cov.py" >> "$LOG" 2>&1
tail -3 "$LOG"
