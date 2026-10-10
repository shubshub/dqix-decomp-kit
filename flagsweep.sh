#!/bin/bash
# Which compiler settings produce THIS function? Library objects linked into the ROM were not
# necessarily built with the project's own flags: a routine that branches to a shared `bx lr`
# where our -O2 predicates the return is a different optimisation level, not a different source.
# Usage: flagsweep.sh <OV|main> <addr> <file.cpp>
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
# The interpreter this kit is running under. A bare `python` is python3 under Git Bash on
# Windows and does not exist at all on a Debian that keeps its packages in a virtualenv, so
# every call below asks the kit which interpreter to use instead of assuming one.
PY="${DQIX_PYTHON:-$(python3 "$KIT/kitpaths.py" py)}"
# ninja and anything else installed beside it live in the same directory, and they are on
# PATH only while the venv is ACTIVATED. Put them there for this script's children.
export PATH="$(dirname "$PY"):$PATH"
SP="$("$PY" "$KIT/kitpaths.py" state)"
REPO="$("$PY" "$KIT/kitpaths.py" repo)"
OV="$1"; ADDR="$2"; SRC="$3"
cd "$REPO" || exit 2

sets=(
  ""
  "-O0"
  "-O1"
  "-O2"
  "-O3"
  "-O4"
  "-Os"
  "-O2 -ipa off"
  "-O4,p"
  "-O4,s"
  "-O2 -sdatathreshold 0"
  "-O2 -opt noschedule"
  "-O2 -opt nolifetimes"
  "-O2 -opt nopeephole"
)

for f in "${sets[@]}"; do
  out=$(WDIFF_FLAGS="$f" "$PY" "$KIT/wdiff.py" "$OV" "$ADDR" "$SRC" </dev/null 2>&1 | head -1)
  printf '%-24s %s\n' "[${f:-default}]" "$out"
done
echo "FLAGSWEEP DONE"
