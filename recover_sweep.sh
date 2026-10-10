#!/bin/bash
# ZERO-TOKEN RECOVERY SWEEP. Re-integrates matched worker source that is sitting on disk but is NOT
# in the build, for every module that has any.
#
# WHY THIS EXISTS. hold_<mod>/ , <mod>_stage/ and quarantine/ accumulate every .cpp a worker ever
# produced. A wave defers work for reasons that are NOT "the match is wrong" — gate-cap, link-layout
# drift from a neighbour, a transient git index.lock (that one alone deferred 42 green functions in
# ov001 wave 2; re-gating them later committed 41/42 on the first try). Those files were only ever
# reconsidered when run_all happened to route back to that same module — but a module that defers its
# wave scores low yield and goes ON COOLDOWN, so the module holding the most recoverable work is the
# one least likely to be revisited. Measured at 68.75%: 419+ functions already matched, byte-exact,
# on disk, invisible to the build. This sweep lands them for zero worker tokens.
#
# Serial by construction — ov_recover mutates config/ and gates a full `ninja check`; two at once
# corrupt each other. Usage: bash recover_sweep.sh
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
cd "$REPO" || exit 2
LOG="$SP/wlog/sweep.log"
echo "=== recover_sweep $(date '+%m-%d %H:%M:%S') ===" >> "$LOG"

# HARVEST THE SCRATCHPAD FIRST. Workers iterate in SCRATCH/w<addr>.cpp and only copy the file into
# src/ once it matches — so a worker killed BETWEEN achieving the match and copying leaves a real,
# gate-verified match where nothing looks for it. Measured: 78 addrs a worker declared PASS on are
# not in the build, 67 of them with a candidate still sitting in scratch; a full gate pass found 38
# that match TODAY. This stages them into hold_* so the sweep below commits them.
bash "$KIT/wgate_scratch.sh" >> "$LOG" 2>&1

# ZERO-TOKEN PRODUCERS. These generate and GATE candidate source with no model involved; anything that
# passes is staged into hold_* and committed by the sweep below. Each is throttled by a stamp file
# because they scan the whole remaining pool — worth doing regularly, wasteful every single module.
#   synth      trivial accessor shapes derived straight from the disassembly
#   translate  literal ARM->C transliteration, verified by the gate (21% at <=24 insns). The cap was
#              24 insns because the translator emitted an unresolved marker for every `bl` and threw
#              the whole draft away, so nothing with a call could ever pass; it resolves callees now.
#   scaffold   regenerates the per-function fact sheets (callee names go stale as functions get renamed)
_stamp="$SP/wlog/.zerotoken_stamp"
_age=999999
[ -f "$_stamp" ] && _age=$(( $(date +%s) - $(stat -c %Y "$_stamp") ))
if [ "$_age" -gt 10800 ]; then          # at most once every 3h
  echo "--- zero-token producers (last run ${_age}s ago)" >> "$LOG"
  timeout 900  "$PY" "$KIT/synth.py" --sweep 64    >> "$LOG" 2>&1
  timeout 3600 "$PY" "$KIT/translate.py" --sweep 256 >> "$LOG" 2>&1
  timeout 1800 "$PY" "$KIT/scaffold.py" --all       >> "$LOG" 2>&1
  touch "$_stamp"
  echo "--- zero-token producers done" >> "$LOG"
fi
# rank modules by how many DISTINCT matched addrs they hold that are not yet delinked
MODS=$("$PY" "$KIT/recoverable.py" 2>/dev/null | awk '$2>0{print $1}')
[ -z "$MODS" ] && { echo "nothing recoverable" >> "$LOG"; exit 0; }

for M in $MODS; do
  N=$("$PY" "$KIT/recoverable.py" "$M" 2>/dev/null | awk '{print $2}')
  [ -z "$N" ] && N=0
  [ "$N" -lt 1 ] && continue
  echo "--- $M: $N recoverable" >> "$LOG"
  # MAXGATES 24 (vs the 16 a worker wave uses): these candidates cost no tokens to retry, so it is
  # worth more link-gates to isolate a drift culprit than it would be mid-wave.
  # NOSKIP=1: reconsider skiplisted addrs too. The 2-strike skiplist records "a worker printed SKIP",
  # which is not the same as "no match exists" — a different worker may already have matched it, and
  # the strike bookkeeping has been wrong before (30 byte-exact functions were permanently written off
  # that way). Free to re-test: classify runs locally at ~0.3s/func with no build, so a genuinely bad
  # func is dropped before it can cost a single gate.
  NOSKIP=1 MAXGATES=24 bash "$KIT/finish_wave.sh" "$M" > "$SP/wlog/sweep_$M.log" 2>&1
  echo "  $M: $(tail -1 "$SP/wlog/sweep_$M.log")" >> "$LOG"
done

# fail-soft: a missing/short report must never make the sweep look like it errored — run_all reads the
# exit code of the last command, and a bare `python -c` that raises would report a failed sweep after
# a run that actually committed everything it found.
"$PY" -c "import json;m=json.load(open('build/usa/report.json'))['measures'];print('sweep end: %.2f%% (%d/%d)'%(m['matched_functions_percent'],m['matched_functions'],m['total_functions']))" >> "$LOG" 2>/dev/null \
  || echo "sweep end: (report unavailable)" >> "$LOG"
tail -1 "$LOG"
exit 0
