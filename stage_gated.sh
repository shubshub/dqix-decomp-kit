#!/usr/bin/env bash
# Stage every wgate-PROVEN source that is still uncommitted, so a wave can try to land it.
#
#   bash stage_gated.sh [--apply]        without --apply it only counts
#
# wgate.py copies a source into gated/<mod>/<addr>.cpp ONLY on a MATCH -- "the one place in the
# pipeline where a match is PROVEN". 801 accumulated there and no dispatcher ever read the
# directory, so 112 addresses whose exact bytes had already matched sat uncommitted.
#
# This does NOT re-gate. finish_wave gates and classifies anyway, and it is the authority: a wgate
# MATCH masks relocations, so it can pass a wrong-callee function that still fails the overlay
# checksum. Staging is the cheap half; the wave decides.
#
# Secure-area addresses (< 0x02000800) are staged too -- land_harvest.sh parks them back out and
# feeds them one per wave, because their layout drift is cumulative.
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
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1
# resumable.py owns "is this already committed": it answers from the delink ranges, not from a
# symbol name, so a renamed matched function still reads as committed. Scan ONCE -- calling it per
# file would re-walk ~35k pool files 800 times.
Q=$(mktemp); trap 'rm -f "$Q"' EXIT
"$PY" "$KIT/resumable.py" --all 2>/dev/null | awk '{print $2}' | sort -u > "$Q"

n=0; skip=0
for f in "$SP"/gated/*/*.cpp; do
  [ -e "$f" ] || continue
  a=$(basename "$f" .cpp)
  case "$a" in 0[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;; *) continue ;; esac
  if ! grep -qx "$a" "$Q"; then
    skip=$((skip + 1)); continue
  fi
  d=$(basename "$(dirname "$f")")          # main | ov017
  n=$((n + 1))
  [ "$APPLY" = 1 ] && { mkdir -p "$SP/staging/$d"; cp "$f" "$SP/staging/$d/$a.cpp"; }
done
echo "stage_gated: $n uncommitted proven source(s), $skip already committed or not queued (apply=$APPLY)"
