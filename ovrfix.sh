#!/bin/bash
# Work the functions that only match behind a per-file compiler override.
#
# WHY THIS IS A SEPARATE LANE. These addresses are COMMITTED, so claim.py will never serve them --
# the pool is unmatched work. But a committed function that only matches on a different compiler is
# not finished: under a one-toolchain build its SOURCE is wrong in some findable way, and the
# override is hiding that. The same mistake is probably in functions we have not matched yet, which
# is what makes these worth paying for.
#
# Usage: ovrfix.sh <mod> <addr> [model]
# The session must produce a source that matches with NO override. It may not touch the override
# table, and it may not change what the function does.
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
MOD="$1"; ADDR="$2"; MODEL="${3:-sonnet}"
[ -z "$ADDR" ] && { echo "usage: ovrfix.sh <mod> <addr> [model]"; exit 2; }

LOG="$SP/wlog/ovrfix_${MOD}_${ADDR}.log"
OUT="$SP/wip/ovrfix"; mkdir -p "$OUT"
SRC=$(grep -rl "USA: func_\(ov[0-9]*_\)\?$ADDR" "$REPO/src" 2>/dev/null | head -1)
[ -z "$SRC" ] && { echo "no committed source for $ADDR"; exit 2; }
OVR=$(grep "$(basename "$SRC")" "$REPO/tools/cc_overrides.txt" 2>/dev/null | awk '{print $2}')
cp "$SRC" "$OUT/$ADDR.cpp"

echo "$(date '+%H:%M') ovrfix $MOD:$ADDR [$MODEL] override=$OVR src=$SRC" >> "$LOG"

cd "$REPO" || exit 2
WGATE_SESSION="ovrfix_$ADDR" timeout -k 30 5400 claude -p "DQIX decomp: remove a compiler-override dependency.

$MOD:$ADDR is COMMITTED and byte-exact, but ONLY when compiled with mwccarm $OVR. The project's
build uses the default compiler, and the original game was built with ONE toolchain -- so the
override is not a property of this function, it is hiding a mistake in OUR source. Your job is to
find that mistake.

  working copy : $OUT/$ADDR.cpp   (a copy of the committed $SRC)
  gate         : python $KIT/wgate.py $MOD $ADDR $OUT/$ADDR.cpp
                 (set WGATE_ALLOW_COMMITTED=1; it MUST print MATCH with no MWCC= and no override)
  diff         : python $KIT/wdiff.py $MOD $ADDR $OUT/$ADDR.cpp
  listing      : python $KIT/wlist.py $MOD $ADDR
  doc          : $KIT/worker_src/core.md

Compare the two compilers to LOCATE the difference -- \`MWCC=$OVR python $KIT/wgate.py ...\` matches,
the default does not, so whatever the newer compiler folds away is what our C is doing wrong. Known
shapes: a redundant local the original re-read, a value cached that the ROM reloads, a definition
order that hands two values each other's registers (try python $KIT/pad/permorder.py), an expression
associated differently.

RULES: do not edit tools/cc_overrides.txt. Do not change behaviour. No assembly. No subagents.
Finish only when the gate prints MATCH under the DEFAULT compiler; if you cannot, write one line
saying exactly which construct the two compilers disagree on." \
  --output-format json --model "$MODEL" --permission-mode bypassPermissions \
  >> "$LOG" 2>&1

if WGATE_ALLOW_COMMITTED=1 "$PY" "$KIT/wgate.py" "$MOD" "$ADDR" "$OUT/$ADDR.cpp" 2>&1 | tail -1 | grep -q "^MATCH"; then
  echo "$(date '+%H:%M') ovrfix $ADDR MATCHES at the default -- copy over $SRC and drop the override" >> "$LOG"
  echo "MATCH $ADDR"
else
  echo "$(date '+%H:%M') ovrfix $ADDR still needs $OVR" >> "$LOG"
  echo "no-match $ADDR"
fi
