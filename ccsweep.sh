#!/bin/bash
# Try every installed mwccarm build (optionally with extra flags) against one function.
# Library code in the ROM was built by whatever toolchain shipped that library, so a function
# whose SHAPE is right but whose size is stubbornly wrong is usually a different compiler build,
# not a different source. Usage: ccsweep.sh <OV|main> <addr> <file.cpp> ["extra flags"]
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
OV="$1"; ADDR="$2"; SRC="$3"; EXTRA="$4"
cd "$REPO" || exit 2

for d in tools/mwccarm/*/*/mwccarm.exe; do
  ver=$(echo "$d" | sed 's|tools/mwccarm/||; s|/mwccarm.exe||')
  out=$(MWCC="$ver" WDIFF_FLAGS="$EXTRA" "$PY" "$KIT/wdiff.py" "$OV" "$ADDR" "$SRC" </dev/null 2>&1 | head -1)
  printf '%-14s %s\n' "$ver" "$out"
done
echo "CCSWEEP DONE"
