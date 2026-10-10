#!/bin/bash
# Autonomous post-worker pipeline. Run AFTER all workers for one overlay quiesce.
# Does EVERYTHING safely: preserve -> integrate -> gate -> sha1 -> push -> verify. One-line verdict.
# Usage: bash finish_wave.sh <OV|main>   (e.g. bash finish_wave.sh 024 / bash finish_wave.sh main)
#
# MODULE PARAMETER, NOT A FORK — same rule as ov_recover.py. run_main.sh used to carry its OWN
# integrate+gate block that was all-or-nothing: a single reloc-false-match reds the wave and the WHOLE
# wave rolls back to main_stage/. That is how 181 matched files ended up parked there. Routing main
# through this script gives it what overlays already had: bisection, drift-culling, and automatic
# reconsideration of everything held from earlier waves.
set -o pipefail
OV="$1"
# The module is a bare number or `main`, never `ov031`. Passed `ov031` this script printed
# `10#ov031: value too great for base`, crashed ov_recover, integrated nothing, and still signed off
# with `OK : +0 delinked, green, no-new-commits` -- a green verdict for a run that did no work is
# worse than a red one, because a harvest that never happened looks like a harvest with no hits.
case "$OV" in
  main|[0-9][0-9][0-9]) ;;
  *) echo "FATAL: module must be 'main' or a 3-digit overlay number (got '${OV:-}')"; exit 2 ;;
esac
# The scratchpad is the directory this script lives in. It used to be an absolute path under %TEMP%,
# which Windows cleanup deleted whole on 2026-08-24, taking the pipeline with it.
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
# The interpreter this kit is running under. A bare `python` is python3 under Git Bash on
# Windows and does not exist at all on a Debian that keeps its packages in a virtualenv, so
# every call below asks the kit which interpreter to use instead of assuming one.
PY="${DQIX_PYTHON:-$(python3 "$KIT/kitpaths.py" py)}"
# ninja and anything else installed beside it live in the same directory, and they are on
# PATH only while the venv is ACTIVATED. Put them there for this script's children.
export PATH="$(dirname "$PY"):$PATH"
SP="$("$PY" "$KIT/kitpaths.py" state)"
# Build logs land in the state directory, not /tmp: /tmp is world-readable and shared with every other
# process on the host, and two waves writing the same fixed filename there clobber each other's
# evidence. Same names as before, so the rest of the script is unchanged.
SCRATCH="$SP/wlog"; mkdir -p "$SCRATCH"
source "$KIT/wavelock.sh"
if ! wave_lock_acquire 900; then
  echo "REFUSING: $LOCK held by another wave after 5h of waiting; not running unlocked"
  exit 3
fi
trap wave_lock_release EXIT
export DQIX_MAIN_REPO="${DQIX_MAIN_REPO:-$("$PY" "$KIT/kitpaths.py" repo)}"
REPO="$("$PY" "$KIT/integ_tree.py" sync)" || { echo "FATAL: no integration tree"; exit 2; }
export DQIX_REPO="$REPO"
cd "$REPO" || { echo "FATAL: no repo"; exit 2; }
if [ "$OV" = "main" ]; then
  DL="config/usa/arm9/delinks.txt"; SRCDIR=$("$PY" "$KIT/srcdir.py" main); TAGPRE="func_"; LBL="main"
else
  DEC=$((10#$OV))
  DL="config/usa/arm9/overlays/ov${OV}/delinks.txt"; SRCDIR=$("$PY" "$KIT/srcdir.py" "$OV")
  TAGPRE="func_ov${OV}_"; LBL="ov${OV}"
fi
Q="$SP/quarantine"; mkdir -p "$Q"

# ===== SELF-HEALING PREFLIGHT — never abort/lose on stale/dupe/leftover files; quarantine them =====
# 0. DROP SKIPLISTED ADDRESSES. This used to be a rule the operator remembered before an integration.
#    It stopped being safe the moment repairsweep began staging its own hits: 0208f588 -- skiplisted
#    because it is byte-exact per function yet shifts the ARM9 link and reds the checksum -- was
#    staged automatically on 2026-08-20 and would have entered the next main build unasked.
"$PY" "$KIT/purge_skiplisted.py" 2>/dev/null | grep -E '^purge ' || true
# 1. purge pure scratch/junk (repo-root w*.cpp, any stray .o under src) — never part of build.
find . -maxdepth 1 -name 'w*.cpp' -delete 2>/dev/null
find src -name '*.o' -delete 2>/dev/null
# 2. REVERT any worker edits to COMMITTED files (headers/siblings/config) to HEAD. Untracked worker
#    output is NOT touched by checkout — only tracked files are restored. Kills persist-edit poison.
git checkout HEAD -- include/ config/ src/ 2>/dev/null
# 3. QUARANTINE (preserve, don't delete) untracked .cpp in OTHER module dirs — they pollute configure.
#    ALL of src/, not src/Combat/ alone: the update-compiler base put ARM9 main's sources in
#    src/World, src/Util, src/System and six more, and a half-written file there reds every module.
git status --porcelain | awk '/\?\?.*\.cpp/{print $2}' | grep '^src/' | grep -v "^$SRCDIR/" | while read -r f; do
  mv "$f" "$Q/$(basename "$f")" 2>/dev/null; done
# A candidate's address is the one on its `// USA:` tag, never just the first func_ symbol in the
# file: every source declares its CALLEES with the same prefix, and those are usually already
# committed. Reading the first match made finish_wave delete five byte-exact ov031 sources on
# 2026-08-20 as "already committed" because a callee was (0223cf70). Filename is the fallback.
addr_of() {
  local a
  a=$(grep -oE "// USA: ${TAGPRE}[0-9a-fA-F]{8}" "$1" 2>/dev/null | head -1 \
      | grep -oE '[0-9a-fA-F]{8}$' | tr 'A-F' 'a-f')
  [ -z "$a" ] && a=$(basename "$1" | grep -oE '[0-9a-fA-F]{8}\.cpp$' | cut -c1-8 | tr 'A-F' 'a-f')
  echo "$a"
}

# 4. QUARANTINE untracked .cpp in THIS module whose addr is ALREADY committed (dupes cause collisions).
git grep -hoE "// USA: ${TAGPRE}[0-9a-fA-F]{8}" HEAD -- src/ 2>/dev/null | grep -oE '[0-9a-fA-F]{8}$' | tr 'A-F' 'a-f' | sort -u > "$Q/.done_$OV"
# BULK-TRACKED-BEGIN: one checked index snapshot, never per-file Git.
mkdir -p "$SP/handwork" || { echo "FATAL: cannot create preflight scratch"; exit 6; }
_tracked_tmp="$SP/handwork/fw_tracked_$$.nul"
if ! git ls-files --cached -z -- "$SRCDIR/" > "$_tracked_tmp"; then
  rm -f "$_tracked_tmp"
  echo "FATAL: cannot read tracked paths; refusing duplicate quarantine"
  exit 6
fi
# Check the complete read and record terminator before publishing any membership.
# mapfile alone accepts an unterminated last record and may treat a read error as EOF.
if ! "$PY" -c 'import pathlib,sys; d=pathlib.Path(sys.argv[1]).read_bytes(); sys.exit(0 if not d or d.endswith(bytes([0])) else 1)' "$_tracked_tmp"; then
  rm -f "$_tracked_tmp"
  echo "FATAL: unreadable or truncated tracked paths; refusing duplicate quarantine"
  exit 6
fi
_fw_tracked_paths=()
if ! mapfile -d '' -t _fw_tracked_paths < "$_tracked_tmp"; then
  rm -f "$_tracked_tmp"
  echo "FATAL: cannot load tracked paths; refusing duplicate quarantine"
  exit 6
fi
declare -A _fw_tracked=()
for _tracked_path in "${_fw_tracked_paths[@]}"; do
  _fw_tracked["$_tracked_path"]=1
done
rm -f "$_tracked_tmp"
# BULK-TRACKED-END
for f in "$SRCDIR"/*.cpp; do
  [ -e "$f" ] || continue
  [ -n "${_fw_tracked["$f"]+present}" ] && continue   # tracked: leave it
  a=$(addr_of "$f")
  [ -n "$a" ] && grep -qx "$a" "$Q/.done_$OV" && mv "$f" "$Q/$(basename "$f")" 2>/dev/null
done
# 5. STAGING — hand-cracked functions land here. finish_wave is the ONLY path into the repo that
#    bisects + gates, so anything matched by hand outside a worker wave must enter through it.
#    Dropping a .cpp straight into src/ does NOT work: preflight step 3 quarantines untracked .cpp
#    belonging to a module other than the one being integrated, so a hand-matched ov000 file would be
#    swept away by the next ov014 integration and stranded. Staging is keyed by module and copied in
#    only when THAT module integrates. Already-committed addrs are dropped from staging so a matched
#    function is never re-offered forever.
STAGE="$SP/staging/$LBL"
if [ -d "$STAGE" ]; then
  for f in "$STAGE"/*.cpp; do
    [ -e "$f" ] || continue
    a=$(addr_of "$f")
    if [ -n "$a" ] && grep -qx "$a" "$Q/.done_$OV" 2>/dev/null; then
      rm -f "$f"; echo "staging: $LBL $a already committed, dropped"
    else
      cp "$f" "$SRCDIR/" && echo "staging: $LBL took $(basename "$f")"
    fi
  done
fi
# ===== end preflight: tree now has ONLY this overlay's fresh untracked .cpp + pristine committed files =====

BEFORE=$(grep -cE '^\s+\.(text|init) start:' "$DL")   # .init functions count too
H0=$(git rev-parse --short HEAD)

# integrate (ov_recover snapshots to hold_$LBL BEFORE any git touch, then bisect-commits)
"$PY" -u "$KIT/ov_recover.py" "$OV" src 2>&1 | tee -a "$SP/wlog/rec_${OV}.log" | tail -3   # -u: progress visible live (classify+gate can run 10+ min)
# A CRASHED INTEGRATOR IS NOT A QUIET WAVE. `set -o pipefail` cannot see this one: the exit status
# of the pipeline is `tail`'s, which is 0 however badly the python died. On 2026-08-25 ov_recover
# raised AttributeError three lines in, integrated nothing, and the wave signed off
# "OK main: +0 delinked, build skipped" -- the exact class of green-for-a-run-that-never-happened
# the module check was added to stop.
_rc=${PIPESTATUS[0]}
if [ "$_rc" -ne 0 ]; then
  echo "FATAL: ov_recover.py exited $_rc -- nothing integrated. tail:"
  tail -5 "$SP/wlog/rec_${OV}.log"
  exit 6
fi

AFTER=$(grep -cE '^\s+\.(text|init) start:' "$DL")
GAINED=$((AFTER-BEFORE))

# A wave that delinked nothing and moved no commit has nothing to prove: the tree is byte-identical
# to the one the last green gate signed off. Building anyway cost a full ninja check (~6 min, and it
# holds the wave lock the whole time) on every quiet wave.
if [ "$GAINED" -eq 0 ] && [ "$(git rev-parse --short HEAD)" = "$H0" ]; then
  echo "OK ${LBL}: +0 delinked, HEAD unmoved, build skipped (nothing changed), held ${SP}/hold_${LBL}"
  exit 0
fi

# gate
_compiler_args=()
[ -n "${DQIX_PREINSTALLED_COMPILER:-}" ] && _compiler_args=(--compiler "$DQIX_PREINSTALLED_COMPILER")
"$PY" tools/configure.py usa --no-extract "${_compiler_args[@]}" >/dev/null 2>&1
if ! ninja check >"$SCRATCH"/fw_check.log 2>&1; then
  echo "RED: ninja check FAILED — NOT pushing. tail:"; tail -3 "$SCRATCH"/fw_check.log
  echo "held: $SP/hold_${LBL} (nothing lost)"; exit 4
fi
ninja rom >/dev/null 2>&1
if ! ninja sha1 2>&1 | grep -q "OK"; then echo "RED: sha1 mismatch — NOT pushing"; exit 5; fi
if [ "$(git rev-parse --short HEAD)" != "$H0" ]; then
  "$PY" "$KIT/countfix.py" --since="$H0"
  if [ $? -eq 3 ]; then
    if ninja check >"$SCRATCH"/fw_countfix.log 2>&1 && ninja sha1 2>&1 | grep -q "OK"; then
      git add config/ && git commit -q -m "Align config with landed functions"
    else
      "$PY" "$KIT/countfix.py" --restore
      ninja check >/dev/null 2>&1
    fi
  fi
  "$PY" "$KIT/regionsync.py"
fi

# push only if we actually gained and HEAD moved
H1=$(git rev-parse --short HEAD)
if [ "$H1" != "$H0" ]; then
  PUSH="PUSH-FAILED"
  "$PY" "$KIT/integ_tree.py" publish > "$SCRATCH"/fw_push.log 2>&1
  case $? in
    0) PUSH="pushed" ;;
    2) PUSH="pushed-but-main-checkout-behind" ;;
    *) cat "$SCRATCH"/fw_push.log ;;
  esac
  # git push exits 0 on "Everything up-to-date", so compare refs
  if [ "$PUSH" != "PUSH-FAILED" ] && \
     [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/decomp-matching 2>/dev/null)" ]; then
    PUSH="PUSH-DID-NOT-LAND"
  fi
else
  PUSH="no-new-commits"
fi

rm -f build/usa/report.json; ninja report >/dev/null 2>&1
COV=$("$PY" -c "import json;m=json.load(open('build/usa/report.json'))['measures'];print('%d/%d = %.2f%%'%(m['matched_functions'],m['total_functions'],m['matched_functions_percent']))")
"$PY" "$KIT/integ_tree.py" report
echo "OK ${LBL}: +${GAINED} delinked (${BEFORE}->${AFTER}), green, sha1 OK, ${PUSH}, cov ${COV}, held ${SP}/hold_${LBL}"
