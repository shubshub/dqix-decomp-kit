#!/bin/bash
# Clear the whole staged backlog with ONE build if possible, falling back to per-module only on red.
# Usage: bash integrate_fast.sh        (fleet STOPPED)
#
# WHY. The build is GLOBAL -- `ninja check` builds the whole ROM and `sha1` verifies the whole image
# regardless of which module changed -- but finish_wave is per module, so clearing ten modules cost
# ten full rebuilds to verify what one rebuild verifies. That is pure wall-clock waste when the work
# is good, which it usually is.
#
# The per-module structure is not pointless: when a build goes red from link drift, culling culprits
# is only tractable if you know which module to suspect. So this is a HYBRID, not a replacement --
# best case one build instead of ten, worst case one wasted build then the old path unchanged.
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
# The interpreter this kit is running under. A bare `python` is python3 under Git Bash on
# Windows and does not exist at all on a Debian that keeps its packages in a virtualenv, so
# every call below asks the kit which interpreter to use instead of assuming one.
PY="${DQIX_PYTHON:-$(python3 "$KIT/kitpaths.py" py)}"
# ninja and anything else installed beside it live in the same directory, and they are on
# PATH only while the venv is ACTIVATED. Put them there for this script's children.
export PATH="$(dirname "$PY"):$PATH"
SP="$("$PY" "$KIT/kitpaths.py" state)"
LOG="$SP/wlog/integrate_fast.log"
# Build logs land in the state directory, not /tmp: /tmp is world-readable and shared with every other
# process on the host, and two runs writing the same fixed filename there clobber each other's
# evidence. Same names as before, so the rest of the script is unchanged.
SCRATCH="$SP/wlog"; mkdir -p "$SCRATCH"
source "$KIT/wavelock.sh"
if ! wave_lock_acquire 1; then
  echo "REFUSING: $LOCK held by another integration" | tee -a "$LOG"; exit 3
fi
export DQIX_MAIN_REPO="${DQIX_MAIN_REPO:-$("$PY" "$KIT/kitpaths.py" repo)}"
REPO="$("$PY" "$KIT/integ_tree.py" sync)" || { wave_lock_release; echo "FATAL: no integration tree"; exit 2; }
export DQIX_REPO="$REPO"
# ONE TERMINAL LINE ON EVERY EXIT PATH. There are nine `exit`s below and any of them can be the last
# thing that happens. A watcher greping for one success word goes silent on the other eight, and that
# silence is indistinguishable from "still running" -- it read as a live integration for 36 minutes on
# 2026-09-09 while nothing was running. Silence must not be a possible outcome of this script.
_final() { local rc=$?; wave_lock_release; echo "$(date '+%H:%M') INTEGRATE-END rc=$rc" | tee -a "$LOG"; }
trap _final EXIT
cd "$REPO" || exit 2

n=$(ls "$SP"/staging/*/*.cpp 2>/dev/null | wc -l)
[ "$n" -eq 0 ] && { echo "nothing staged"; exit 0; }
echo "=== integrate_fast $(date '+%m-%d %H:%M'): $n staged ===" >> "$LOG"

# REFUSE ON A DIRTY TREE. The rollback below is `git checkout -- config/ src/`, which would also
# discard someone else's uncommitted work. Only run this from a clean state.
if [ -n "$(git status --porcelain config/ src/ | grep -v '^??')" ]; then
  echo "REFUSING: tracked files already modified; commit or revert first" | tee -a "$LOG"; exit 3
fi

# HOLD BACK THE SECURE AREA. 0x02000000-0x02000800 stubs drift the layout CUMULATIVELY, which is why
# the project lands them one per wave. Every previous combined build included eleven of them at once
# (GetCRC16, BitUnPack, CpuFastSet, VBlankIntrWait, WaitByLoop, Div, Sqrt, SoftReset ...) and every
# one of those runs went RED with a symbol shifted by a small multiple of 4 -- 08-20 reported
# `data_020eeb18 ... is at 0x020eeb2c`, off by 0x14. The combined build was never unsound; it just
# always carried the one class of function that cannot be batched. They are moved, not dropped:
# land_harvest.sh feeds them back one per wave.
# INTEGRATE EXACTLY WHAT WE STAGED, NOTHING ELSE. integrate.py takes its candidates from every
# UNTRACKED file under src/ (integrate.py:165), so src/ doubles as the working tree AND the handoff
# channel: anything left lying there -- a killed wave's placement, a NOSKIP run, a crashed job --
# joins the combined build uninvited. That is how 0208f588, skiplisted for reding ARM9 main's
# checksum, got wired into a build nobody chose to put it in. Sweep src/ clean first; the files are
# moved, not deleted, and waves re-gather them from hold_<mod>/.
_stray=0
for f in $(git ls-files --others --exclude-standard -- src | grep -E '\.(cpp|c)$'); do
  [ -e "$f" ] || continue
  a=$(grep -oE '// USA: func_(ov[0-9]+_)?[0-9a-fA-F]{8}' "$f" | head -1 | grep -oE '[0-9a-fA-F]{8}$')
  d="$SP/hold_main"; case "$f" in *Overlay_*) d="$SP/quarantine";; esac
  mkdir -p "$d"; mv -f "$f" "$d/$(basename "$f")" && _stray=$((_stray + 1))
done
[ "$_stray" -gt 0 ] && echo "swept $_stray stray untracked source(s) out of src/ before wiring" | tee -a "$LOG"

SECURE="$SP/secure_queue"; mkdir -p "$SECURE"
_held=0
for f in "$SP"/staging/*/*.cpp; do
  [ -e "$f" ] || continue
  a=$(basename "$f" | grep -o '0[0-9a-f]\{7\}' | head -1)
  [ -z "$a" ] && continue
  if [ $((0x$a)) -lt $((0x02000800)) ]; then
    mv "$f" "$SECURE/" && _held=$((_held + 1))
  fi
done
# The same rule has to cover src/ itself. integrate.py takes its candidates from every UNTRACKED
# file under src/ (integrate.py:165), not from staging, so a stub another tool already dropped there
# -- land_harvest placing one per wave, ov_recover, a previous red pass -- walks straight into the
# combined build past a staging-only holdback.
for f in $(git ls-files --others --exclude-standard -- src | grep -E '\.(cpp|c)$'); do
  a=$(basename "$f" | grep -o '0[0-9a-f]\{7\}' | head -1)
  [ -z "$a" ] && continue
  if [ $((0x$a)) -lt $((0x02000800)) ]; then
    mv -f "$f" "$SECURE/" && _held=$((_held + 1))
  fi
done
[ "$_held" -gt 0 ] && echo "held back $_held secure-area stub(s) for one-per-wave landing" | tee -a "$LOG"

ls "$SP"/staging/*/*.cpp >/dev/null 2>&1 || { echo "nothing left to batch" | tee -a "$LOG"; exit 0; }

# PRE-CLASSIFY, BECAUSE ONE BAD CANDIDATE REDS THE WHOLE COMBINED BUILD. That is the inherent cost
# of building everything at once, and it is avoidable: classify compiles each candidate and verifies
# every relocation target resolves to the address the pristine binary points at -- which is exactly
# what wgate cannot see, since it masks reloc bytes. A single RELOCWRONG file (ov008/021894b8, whose
# callee resolved to 0x205d1e0) cost a full combined build. Move anything not TRUSTED/RISKY aside
# first; it is preserved, not deleted, and the per-module path can still try it later.
"$PY" - "$SP" "$KIT" <<'PRE' >> "$LOG" 2>&1
import collections, glob, os, re, shutil, sys
SP = sys.argv[1]
KIT = sys.argv[2]
sys.path.insert(0, KIT)
import classify as C
bad = f"{SP}/staging_unclassified"
os.makedirs(bad, exist_ok=True)
by = collections.defaultdict(dict)
paths = {}
for p in glob.glob(f"{SP}/staging/*/*.cpp"):
    tag = os.path.basename(os.path.dirname(p))
    mod = tag[2:] if tag.startswith("ov") else "main"
    m = re.search(r"// USA: func_(?:ov\d+_)?([0-9a-fA-F]{8})", open(p, encoding="utf-8", errors="ignore").read())
    if m:
        by[mod][m.group(1).lower()] = open(p, encoding="utf-8", errors="ignore").read()
        paths[(mod, m.group(1).lower())] = p
moved = 0
for mod, cands in by.items():
    try:
        verdicts = C.classify(mod, cands)
    except Exception as e:
        print(f"pre-classify {mod}: {e}")
        continue
    for a, v in verdicts.items():
        if v not in ("TRUSTED", "RISKY"):
            p = paths.get((mod, a))
            if p and os.path.exists(p):
                shutil.move(p, f"{bad}/{os.path.basename(p)}")
                moved += 1
                print(f"pre-classify held back {a}: {v}")
print(f"pre-classify: {moved} candidate(s) held back")
PRE

placed=0
# Where this run's integrator output starts, so the summary can count what actually LANDED instead
# of what was staged. On 2026-08-25 a run that integrated 0 (both candidates ASM-SKIPped) still
# reported "GREEN: 2 functions, pushed" and then deleted staging.
_mark=$(wc -l < "$LOG")
# DROP WHAT IS ALREADY LANDED BEFORE COPYING ANYTHING. finish_wave has always had this check; this
# path did not, so a staged copy of an address already committed was re-wired on every pass and the
# linker aborted with "Previously defined" -- two combined builds went RED that way on 2026-09-09,
# each costing a full rollback to per-module. One shared implementation so the two cannot drift.
"$PY" "$KIT/stagepurge.py" --apply >> "$LOG" 2>&1
ls "$SP"/staging/*/*.cpp >/dev/null 2>&1 || { echo "$(date '+%H:%M') everything staged was already landed" | tee -a "$LOG"; exit 0; }
for d in "$SP"/staging/*/; do
  [ -d "$d" ] || continue
  tag=$(basename "$d"); mod=${tag#ov}
  ls "$d"*.cpp >/dev/null 2>&1 || continue
  if [ "$mod" = "main" ]; then dst="$REPO/src/Combat/Main"; else dst="$REPO/src/Combat/Overlay_$((10#$mod))"; fi
  mkdir -p "$dst"; cp "$d"*.cpp "$dst"/ 2>/dev/null
  # Wire config only -- no build. That is what makes one global build possible.
  "$PY" "$KIT/integrate.py" "$mod" >> "$LOG" 2>&1 && placed=$((placed+1))
done
# REGENERATE THE BUILD GRAPH. Wiring a source into delinks.txt tells the LINKER to expect
# `<file>.o`, but ninja only knows how to produce objects listed in build.ninja -- so without this
# the link fails with "Specified file build/usa/src/.../0200006a.o not found" and the combined build
# looks like the functions are bad when nothing was ever compiled. finish_wave has always done this;
# my fast path omitted it, which is what made a set of TRUSTED candidates appear unbuildable.
"$PY" tools/configure.py usa --no-extract >> "$LOG" 2>&1
echo "$(date '+%H:%M') wired $placed modules, configured, building once" >> "$LOG"

green=0
if ninja check >"$SCRATCH"/if_check.log 2>&1 && ninja sha1 2>&1 | grep -q "OK"; then green=1; fi

landed=$(tail -n +$((_mark + 1)) "$LOG" | sed -n 's/^integrated \([0-9]*\);.*/\1/p' \
         | awk '{s += $1} END {print s + 0}')

if [ "$green" = "1" ]; then
  # A green build proves nothing landed BADLY; it does not prove anything landed. Report the
  # integrator's own count, and never clear staging for work that is still staged.
  if [ "${landed:-0}" -eq 0 ]; then
    echo "$(date '+%H:%M') nothing integrated: $n staged, 0 landed -- staging kept" | tee -a "$LOG"
    git checkout -- config/ src/ >> "$LOG" 2>&1
    git clean -fdq src/ >> "$LOG" 2>&1
    exit 0
  fi
  "$PY" "$KIT/countfix.py" >> "$LOG" 2>&1
  if [ $? -eq 3 ] && ! { ninja check >"$SCRATCH"/if_countfix.log 2>&1 && ninja sha1 2>&1 | grep -q "OK"; }; then
    "$PY" "$KIT/countfix.py" --restore >> "$LOG" 2>&1
    ninja check >/dev/null 2>&1
  fi
  git add -A config/ src/ >> "$LOG" 2>&1
  if git diff --cached --quiet; then
    echo "$(date '+%H:%M') $landed integrated but NOTHING TO COMMIT -- staging kept" | tee -a "$LOG"
    exit 4
  fi
  # NO --author OVERRIDE. A hardcoded identity here beats ~/.gitconfig, so waves kept committing
  # under an old username after it had been changed globally. Commit as whatever git is configured
  # to be.
  git commit -q \
      -m "Match $landed functions across $placed modules" >> "$LOG" 2>&1 || {
    echo "$(date '+%H:%M') COMMIT FAILED -- staging kept" | tee -a "$LOG"; exit 5; }
  "$PY" "$KIT/regionsync.py" 2>&1 | tee -a "$LOG"
  _before=$(git rev-parse origin/decomp-matching 2>/dev/null)
  "$PY" "$KIT/integ_tree.py" publish >> "$LOG" 2>&1
  _after=$(git rev-parse origin/decomp-matching 2>/dev/null)
  if [ "$_before" = "$_after" ]; then
    echo "$(date '+%H:%M') GREEN: $landed functions, $placed modules, COMMITTED BUT NOT PUSHED" \
      | tee -a "$LOG"
  else
    # Clear only what actually LANDED. A blanket `rm` here deleted a verified MATCH that the
    # integrator had dropped -- 02097280 was skipped because a tracked src/ file already defined it,
    # and then its staged copy went with the five that did land, leaving no trace of either.
    _kept=0
    for f in "$SP"/staging/*/*.cpp; do
      [ -e "$f" ] || continue
      a=$(grep -oE '// USA: func_(ov[0-9]+_)?[0-9a-fA-F]{8}' "$f" | head -1 \
          | grep -oE '[0-9a-fA-F]{8}$' | tr 'A-F' 'a-f')
      _m=$(basename "$(dirname "$f")"); _m=${_m#ov}
      if [ -n "$a" ] && ! "$PY" "$KIT/delinked.py" "$a" "$_m"; then
        _kept=$((_kept + 1)); continue
      fi
      rm -f "$f"
    done
    echo "$(date '+%H:%M') GREEN in ONE build: $landed functions, $placed modules, pushed" \
      | tee -a "$LOG"
    [ "$_kept" -gt 0 ] && echo "$(date '+%H:%M') $_kept staged file(s) NOT delinked -- kept" \
      | tee -a "$LOG"
  fi
  ninja report >/dev/null 2>&1 || echo "$(date '+%H:%M') WARN ninja report failed -- cov is stale" | tee -a "$LOG"
  "$PY" "$KIT/integ_tree.py" report
  "$PY" "$KIT/cov.py" | tee -a "$LOG"
  exit 0
fi

# RED: undo everything this script did and hand over to the per-module path, which can cull.
echo "$(date '+%H:%M') RED on the combined build -- rolling back, falling back to per-module" | tee -a "$LOG"
# Keep the whole build log: `tail -3` twice reduced the cause to "Errors caused tool to abort",
# and the next pass overwrote the check log before anyone could read the line above it.
cp "$SCRATCH"/if_check.log "$SP/wlog/if_check_$(date '+%m%d_%H%M').log" 2>/dev/null
grep -aE "expected to be at|error:|ERROR|undefined|not found|FAILED" "$SCRATCH"/if_check.log | tail -12 >> "$LOG"
tail -5 "$SCRATCH"/if_check.log >> "$LOG"
_round=${INTEGRATE_FAST_ROUND:-0}
_cul=1
[ "$_round" -lt 3 ] && { "$PY" "$KIT/culprits.py" "$SCRATCH"/if_check.log --cull >> "$LOG" 2>&1; _cul=$?; }
if [ "$_cul" -eq 0 ] || [ "$_cul" -eq 2 ]; then
  git checkout -- config/ src/ >> "$LOG" 2>&1
  git clean -fdq src/ >> "$LOG" 2>&1
  echo "$(date '+%H:%M') red build: culprits culled or a tool crashed; rebuilding" | tee -a "$LOG"
  wave_lock_release
  INTEGRATE_FAST_ROUND=$((_round + 1)) exec bash "$KIT/integrate_fast.sh"
fi
git checkout -- config/ src/ >> "$LOG" 2>&1
git clean -fdq src/ >> "$LOG" 2>&1      # remove the copies this script made; staging still holds them
wave_lock_release
exec bash "$KIT/integrate_all.sh"
