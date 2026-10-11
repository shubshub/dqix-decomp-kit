#!/bin/bash
# ONE worker slot: claim a function from the shared pool, work it, repeat until told to stop.
# Usage: pull_worker.sh <main|NNN> <slot> [ignored]
#
# THE SLOT IS A BUDGET, NOT A CONVERSATION. It restarts the session for every function instead of
# carrying one long conversation, because cost per message climbs with session length -- measured
# over 264 sessions: $0.0623/msg at 10-39 messages against $0.1777/msg past 220. Re-entering costs
# one read of a ~5.4k-token doc (it was 24k until the recipes were moved on demand), which is far
# cheaper than paying the long-session rate for every function after the first.
#
# EXACTLY ONE LIMIT: the per-session cost cap, sized to the function (cap_for). Everything else that
# used to stop a worker has been removed as either useless or actively harmful -- a slot budget that
# only clamped the next session's cap, a 30-minute wall clock that guillotined large functions with
# budget to spare, and a try count that a dollar cap already subsumes. Spend is MEASURED per session
# from `--output-format json`, so the logs say what work actually cost rather than estimating it.
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
SP="$(python "$KIT/kitpaths.py" state)"
REPO="$(python "$KIT/kitpaths.py" repo)"
MOD="${1:-main}"
SLOT="${2:-1}"
BUDGET="${3:-10}"
PER_FUNC_CAP="${PER_FUNC_CAP:-3}"     # no single function may swallow the slot

if [ "$MOD" = "main" ]; then
  SRCDIR=$(python "$KIT/srcdir.py" main); DOCS="worker_main.md"; DOCL="worker_mainL.md"; TAG="func_"; MARG="main"
else
  SRCDIR=$(python "$KIT/srcdir.py" "$MOD"); DOCS="worker_ov.md"; DOCL="worker_ovL.md"
  TAG="func_ov${MOD}_"; MARG="$MOD"
fi

# THE DOC MUST MATCH THE FUNCTION SIZE. This handed every worker the small-function doc regardless,
# so 260B+ functions were being attacked with the small playbook and none of the large-function
# method -- skeleton first, iterate on wdiff, never whole-function-then-diff. The large band was
# 0 of 7 for $16.63. The batch driver had always split these into separate worker types; the pull
# loop lost that distinction when it was written.
size_of() {   # $1 = addr -> function size in bytes (0 if unknown)
  python "$KIT/claim.py" "$MARG" --size "$1" 2>/dev/null || echo 0
}

# THE CAP MUST SCALE WITH THE WORK. A flat $3 is generous for a 4-byte stub and too little for a
# 264-byte function: a third of medium/large failures spent >=80% of it, and one reported "budget
# near cap, held best attempt (200-byte diff, closest of all tries)" -- that is the cap truncating
# real progress, not a function refusing to match. The batch fleet gave large functions 90 minutes
# for the same reason. Raised only moderately, because a bigger cap on a hopeless function just
# spends more: the progress rule (stop when the diff has not moved for 3 gates) is what keeps the
# extra budget from being wasted, and the monitor's `band` alert says so if large stays at 0%.
# Live knob per band: `echo 8 > $SP/CAP_LARGE`. Raised from $5 on evidence, not feel -- 9 of 14
# large-band failures spent >=90% of a $5 cap (4.57, 4.57, 4.60, 4.62, 4.63, 4.85, 4.90, 4.99), so
# the budget was ending them, not the work. A function that fails now must be re-attempted later at
# full price anyway, which makes finishing it on the first pass the cheaper option even when the
# extra budget is sometimes spent on a genuine dead end.
# `tr -dc ... < "$f" 2>/dev/null` does NOT stay quiet when $f is absent: bash performs the input
# redirection BEFORE it applies 2>/dev/null, so the shell itself reports the failure and the driver
# log fills with "No such file or directory" for every knob an operator has not created. Real errors
# then hide in that noise. `cat "$f" 2>/dev/null` suppresses it, because the redirection is cat's.
_knob() {     # $1 = knob file, $2 = charset, $3 = default
  local _f
  _f=$(cat "$1" 2>/dev/null | tr -dc "$2")
  echo "${_f:-$3}"
}

# ONE CAP PER SIZE BAND, matching claim.py's SIZE_BOUNDS. A single CAP_LARGE covered 257..10204
# bytes -- a 40x range on one budget, so a 4KB function got the same money as a 260B one and ran out
# with the work half done, while the 260B one could burn $8 on a dead end. Live knobs, all optional.
cap_for() {   # $1 = size in bytes
  if   [ "$1" -gt 4096 ] 2>/dev/null; then _knob "$SP/CAP_MASSIVE" '0-9.' "${CAP_MASSIVE:-25}"
  elif [ "$1" -gt 2048 ] 2>/dev/null; then _knob "$SP/CAP_XL"      '0-9.' "${CAP_XL:-18}"
  elif [ "$1" -gt 1024 ] 2>/dev/null; then _knob "$SP/CAP_LPLUS"   '0-9.' "${CAP_LPLUS:-12}"
  elif [ "$1" -gt 256 ]  2>/dev/null; then _knob "$SP/CAP_LARGE"   '0-9.' "${CAP_LARGE:-8}"
  elif [ "$1" -gt 64 ]   2>/dev/null; then _knob "$SP/CAP_MED"     '0-9.' "${CAP_MED:-3}"
  else _knob "$SP/CAP_SMALL" '0-9.' "${CAP_SMALL:-1.5}"
  fi
}

cd "$REPO" || exit 2
LOG="$SP/wlog/pull_${MOD}_s${SLOT}.log"
spent=0; matched=0; tried=0

# WORKERS WRITE TO STAGING, NOT TO src/. finish_wave's preflight quarantines untracked .cpp that
# belong to a module OTHER than the one it is integrating -- correct on its own, since foreign files
# pollute configure -- but slots now work other modules DURING an integration, so a main wave swept
# four freshly-matched ov017 functions out of src/ mid-flight. They were preserved rather than lost,
# but they had to be recovered by hand. staging/<module>/ is the path finish_wave already uses for
# exactly this: it copies a module's staged files in only when THAT module integrates.
STAGE="$SP/staging/$([ "$MOD" = "main" ] && echo main || echo "ov$MOD")"
mkdir -p "$STAGE"
# WORKERS DO NOT WRITE INTO THE BUILD TREE. configure.py globs all of src/, so one half-written
# worker file -- a `goto` whose label is not typed yet -- fails `ninja check` for EVERY module, and
# a wave integrating one module deletes, quarantines or commits another module's in-flight file.
# Four separate match-eaters on 2026-09-06 all required a live file in src/. wgate compiles any
# path, so nothing needs the file to be there until it MATCHES.
WIP="$SP/wip/$([ "$MOD" = "main" ] && echo main || echo "ov$MOD")"
mkdir -p "$WIP"

while :; do
  [ -e "$SP/STOP_PULL" ] && { echo "$(date '+%H:%M') s$SLOT stop flag" >> "$LOG"; break; }

  # NO SLOT BUDGET. There used to be one, and it bounded nothing: when a slot exhausted it, the
  # dispatcher simply respawned the slot with a fresh budget, so the only thing it ever did was
  # clamp the next session's cap to whatever was left over -- which launched a 268-byte function
  # with $1.18 and guaranteed the truncation. Every SESSION is already capped by size, which is the
  # limit that actually binds; total burn is set by how many slots run and for how long, both of
  # which are live knobs. One layer, not two.
  ADDR=$(python "$KIT/claim.py" "$MOD" 2>/dev/null | tr -d '\r\n ')
  [ -z "$ADDR" ] && { echo "$(date '+%H:%M') s$SLOT pool drained" >> "$LOG"; break; }
  # THIRD ARGUMENT IS REQUIRED. Without it scaffold.py prints to stdout and writes nothing, so the
  # prompt would send the worker to a scaffold path that does not exist.
  mkdir -p "$SP/scaffold"
  python "$KIT/scaffold.py" "$MARG" "$ADDR" "$SP/scaffold/$ADDR.cpp" >/dev/null 2>&1

  # THE FREE REWRITES RUN BEFORE THE PAID SESSION, NOT AFTER IT. colorsweep only ever ran inside
  # repairsweep, on an hourly timer over a 475-candidate pool walked in order, so it had usually
  # NOT touched the address about to be claimed -- and the prompt then asked the WORKER to run it,
  # spending tokens on a mechanical, gated, meaning-preserving rewrite that costs only CPU. Every
  # session was paying to redo what a script does for nothing.
  #
  # An address with no artifact yet is skipped: there is nothing to rewrite until a first source
  # exists, which is what a worker is for.
  _pre=$(timeout "${CLAIM_PRESWEEP_TIMEOUT:-240}" python "$KIT/presweep.py" "$MARG" "$ADDR" 2>/dev/null | tr -d '\r')
  case "$_pre" in
    MATCH*)
      _pf=${_pre#MATCH }
      mkdir -p "$STAGE"
      sed 's|^// SCRATCH-USA: func_|// USA: func_|' "$_pf" > "$STAGE/$ADDR.cpp" 2>/dev/null
      echo "$(date '+%H:%M') s$SLOT presweep MATCHED $ADDR for free -- no session spawned" >> "$LOG"
      python "$KIT/claim.py" "$MOD" --release "$ADDR" >/dev/null 2>&1
      continue
      ;;
    IMPROVED*)
      echo "$(date '+%H:%M') s$SLOT presweep $ADDR ${_pre#IMPROVED }" >> "$LOG"
      ;;
  esac

  FSIZE=$(size_of "$ADDR")
  if [ "$FSIZE" -gt 256 ] 2>/dev/null; then DOC="$DOCL"; else DOC="$DOCS"; fi

  # PER-FUNCTION DOC. The static doc is 120 KB and a worker's Read of it is TRUNCATED at ~58 KB
  # (`truncatedByTokenCap: true`, 832 of 1876 lines, measured from a worker transcript) -- so half of
  # it, including every lever promoted from the opus run, reached zero sessions. recipe_select.py
  # keeps the procedure plus the recipes whose instruction shapes appear in THIS function's listing,
  # and indexes the rest up top with a fetch command. Costs no model tokens. Falls back to the static
  # doc if selection fails, because a truncated doc still beats no doc.
  _sel=$(python "$KIT/recipe_select.py" "$MARG" "$ADDR" 2>/dev/null | tr -d '\r' | tr '\\' '/')
  if [ -n "$_sel" ] && [ -f "$_sel" ]; then
    DOC="doc_cache/${_sel##*/}"
  else
    echo "$(date '+%H:%M') s$SLOT WARN recipe_select fell back to $DOC (truncated) for $ADDR" >> "$LOG"
  fi
  fcap=$(cap_for "$FSIZE")

  # THE SLOT-BUDGET GUARD THAT USED TO LIVE HERE IS GONE, AND SO IS THE VARIABLE IT READ.
  # It tested `$remain`, which no assignment has set since the per-slot budget was removed (see the
  # "NO SLOT BUDGET" note above), so every claim ran `python -c "print(1 if  < 0.9 * 8 else 0)"`,
  # got a SyntaxError, and left $short empty -- the guard could never fire and only wrote a
  # traceback into the dispatcher's stdout log on every single claim. Per-session cap_for() is the
  # only spend limit, which is the design; $BUDGET is a display figure in the log line and bounds
  # nothing.
  cap="$fcap"
  # A NEAR-MISS IS CAPPED BY ITS RESIDUE, NOT BY THE FUNCTION'S SIZE. cap_for sizes a session to the
  # work of decompiling the function; a queued near-miss has already been decompiled and needs one
  # insight, so the size cap buys hours of re-derivation instead. Measured: opus took 021eb5d0 --
  # 4 bytes short of an 8896-byte function -- to a miss for $21.49 under the $25 massive cap. If the
  # insight does not come early it does not come, so bound these by CAP_NEARMISS and let the queue
  # buy many attempts rather than one long one.
  # Ask for the MEASURED residue, never the queue: claim.py strikes an address out of
  # priority_<mod>.txt the moment it serves it, so a lookup there can never match the address whose
  # cap is being computed -- the first cut of this checked the queue and silently never fired.
  _res=$(python "$KIT/nearmiss.py" --residue "$ADDR" 2>/dev/null | tr -dc '0-9')
  if [ -n "$_res" ] && [ "$_res" -le "${NEARMISS_MAXB:-16}" ] 2>/dev/null; then
    _nm=$(_knob "$SP/CAP_NEARMISS" '0-9.' "${CAP_NEARMISS:-6}")
    _lo=$(python -c "print('$_nm' if float('$_nm') < float('$cap') else '$cap')" 2>/dev/null)
    [ -n "$_lo" ] && cap="$_lo"
  fi
  # MODEL PER SIZE, as a live knob. Measured on the first clean window: sonnet took three fresh
  # 280-byte functions to ~60 bytes of regalloc/scheduling residue each -- colorsweep applied, no
  # improvement -- at ~$4.50 a time, 0 for 3. That is a capability wall on this band, not a harness
  # fault, and large is 71% of everything left. A stronger model is only worth its cost here if each
  # match yields a LEVER the doc can carry back to sonnet, which is why levers.tsv capture is on.
  # A COSTLY TIER MUST EXPIRE BY ITSELF. This was an unbounded live knob: set to opus for a short
  # recipe-locking probe, it then billed opus on 54 consecutive claims ($191.72, 100% of the window)
  # because nothing ever revoked it and no alert watched tier spend. The tier now spends a QUOTA --
  # each large claim decrements MODEL_LARGE_LEFT, and at zero the knob clears itself back to sonnet.
  # Forgetting to revert costs the quota, not the run.
  #   echo opus > $SP/MODEL_LARGE ; echo 6 > $SP/MODEL_LARGE_LEFT
  MODEL="${PULL_MODEL:-sonnet}"
  _pm=$(_knob "$SP/PULL_MODEL" 'a-z0-9.-' "")
  _pml=$(_knob "$SP/PULL_MODEL_LEFT" '0-9' 0)
  if [ -n "$_pm" ] && [ "$_pml" -gt 0 ]; then
    MODEL="$_pm"
    _pml=$((_pml - 1))
    echo "$_pml" > "$SP/PULL_MODEL_LEFT"
    if [ "$_pml" -le 0 ]; then
      : > "$SP/PULL_MODEL"
      echo "$(date '+%H:%M') s$SLOT PULL_MODEL quota spent -- reverted to ${PULL_MODEL:-sonnet}" >> "$LOG"
    fi
  fi
  if [ "$FSIZE" -gt 256 ] 2>/dev/null; then
    _ml=$(_knob "$SP/MODEL_LARGE" 'a-z0-9.-' "")
    _left=$(_knob "$SP/MODEL_LARGE_LEFT" '0-9' 0)
    if [ -n "$_ml" ] && [ "$_left" -gt 0 ]; then
      MODEL="$_ml"
      _left=$((_left - 1))
      echo "$_left" > "$SP/MODEL_LARGE_LEFT"
      if [ "$_left" -le 0 ]; then
        : > "$SP/MODEL_LARGE"
        echo "$(date '+%H:%M') s$SLOT MODEL_LARGE quota spent -- reverted to sonnet" >> "$LOG"
      fi
    fi
  fi
  echo "$(date '+%H:%M') s$SLOT claim $ADDR ${FSIZE}B -> $DOC [$MODEL] cap \$$cap" >> "$LOG"

  OUT="$SP/wlog/pull_${MOD}_s${SLOT}_${ADDR}.json"
  SESS="s${SLOT}_$(date +%s)"
  STAMP="$SP/wlog/.stamp_s${SLOT}"
  : > "$STAMP"
  # HANG GUARD ONLY -- NOT a work limit. This was 1800s, which is not a safety net, it is a second
  # stopping rule competing with the cost cap: a $5 budget at sonnet rates is 50-100 messages, and
  # on a 260-byte function that runs well past 30 minutes, so every large session was guillotined
  # with budget to spare and recorded as a `miss`. Cost is the only limit that should decide when
  # work stops. A generous wall clock stays purely to unstick a session that has HUNG -- a stalled
  # process spends nothing, so the cost cap can never fire on it.
  WGATE_SESSION="$SESS" timeout -k 30 "${HANG_GUARD:-7200}" claude -p "DQIX decomp worker, module $MOD (write to $WIP/, read siblings in $SRCDIR/).
Paths in the docs: \$KIT is $KIT (every script), \$SP is $SP (state: wip/, handwork/, scaffold/, wlog/).
Read $SP/$DOC first — it is the only RECIPE doc. If it names an EARLIER ATTEMPTS file, read that
one next, before you write anything: it holds every form already gated on this address and the
residue each produced, so reproducing one costs a compile to learn what is already written down.
ONE address this session: $ADDR
A scaffold with every callee/data name already resolved is at $SP/scaffold/$ADDR.cpp — START FROM IT.
Target listing: python $KIT/wlist.py $MARG $ADDR
Match it to byte-exact READABLE C++, tag it \`// USA: ${TAG}${ADDR}\`, and write it to
$WIP/ -- NOT into src/. The build compiles everything under src/, so an in-progress file
there breaks every other worker's gate. wgate takes any path; the pipeline moves your file
into the repo itself once it MATCHES.
Done only when \`python $KIT/wgate.py $MARG $ADDR <file>\` prints MATCH.
No asm. No subagents.
THE GATE DECIDES WHEN YOU ARE DONE, NOT YOU. Every wgate run prints a RESIDUE line naming the
measured class of the remaining diff, and a GATE line with your best result so far and how many
gates since it improved. When it prints STOP, stop: write your verdict line and end the session.
Do not start another variant after a STOP, and do not stop before one while the gate still improves.
REGALLOC/SCHEDULING IS NOT A REASON TO STOP. colorsweep has ALREADY been run to exhaustion on the
artifact you were handed, so do not open with it and do not report a residue it left as a wall --
what remains is what its rules cannot express. Run it again only after you have CHANGED the source
(\`python $KIT/colorsweep.py $MARG $ADDR <your.cpp> --apply\`): it hill-climbs meaning-preserving
rewrites for compile cost only, so it is worth a pass on each new form you write, never on the one
you started from.
Reply with exactly one line, and never go quiet instead:
  PASS $ADDR
  BLOCKED $ADDR <CLASS> <metric> <path to your best file>
CLASS and metric are the ones wgate printed on its last RESIDUE line -- copy them, do not invent a
reason and do not describe it in prose. The file path matters: it is the only thing a later rule can
be tested against.
CONTEXT, NOT THE DOLLAR CAP, IS WHAT ENDS A BIG SESSION. Measured: two 4.6KB functions ended
at \$3.16 and \$3.44 of a \$25 cap with the window full, at 45 and 59 turns. So on anything
over ~2KB your FINDINGS are a deliverable in their own right. Before you sign off, append what
you derived -- struct layout, callee signatures, control-flow map, which ROM offsets you have
already translated, what you have RULED OUT -- to $SP/$DOC under the exact heading
'## HANDOFF FROM THE PREVIOUS SESSION ON THIS ADDRESS', and end the block with
'<!-- END HANDOFF -->'. That block is the ONLY thing carried into the next session on this
address; every other part of the doc is regenerated from scratch. If a handoff block is
already there, READ IT FIRST and continue from it instead of re-deriving it.
ON A PASS, record what closed it -- append ONE tab-separated line to $SP/wlog/levers.tsv:
${ADDR}<TAB>${FSIZE}<TAB><BYTEDIFF just before the fix><TAB><the specific transformation that closed it>
Be concrete -- 'split the combine block so the load hoists out of the loop' -- never generic like
'fixed regalloc'. A lever reported three times becomes a rule in the doc that every future worker
gets for free, so this function's cost is not paid again on its siblings." \
    --model "$MODEL" --permission-mode dontAsk \
    --safe-mode \
    --autocompact auto \
    --disallowedTools "WebFetch" "WebSearch" "NotebookEdit" "Task" "Agent" "TodoWrite" "Artifact" "Monitor" "Skill" \
    --max-budget-usd "$cap" --output-format json \
    --allowedTools "Bash" "Write" "Read" "Edit" "Grep" "Glob" \
    --add-dir "$SP" --add-dir "$KIT" \
    > "$OUT" 2>/dev/null < /dev/null &
  CPID=$!
  "$KIT/gatewatch.sh" "$MARG" "$ADDR" "$SESS" "$CPID" >/dev/null 2>&1 &
  WPID=$!
  wait "$CPID"
  kill "$WPID" 2>/dev/null
    # `< /dev/null` IS LOad-BEARING. Inside this loop claude inherits the loop's stdin, waits on it,
    # and then emits nothing at all -- every session JSON came back 0 bytes, so `total_cost_usd` was
    # unreadable and the cost fell back to the cap. Match verdicts were unaffected (they come from
    # wgate) but every cost figure in that window was fiction.

  cost=$(python -c "
import json,sys
try: print('%.4f' % json.load(open(r'$OUT'))['total_cost_usd'])
except Exception: print('$cap')          # unparseable: charge the cap, never nothing
")
  spent=$(python -c "print(round($spent + $cost, 4))")
  tried=$((tried+1))

  # The GATE decides, never the worker's own claim.
  #
  # LOOK IN $STAGE TOO, OR A REAL MATCH IS RECORDED AS A MISS. This searched $SRCDIR alone, so a
  # worker that wrote straight into staging -- or whose src/ file was swept by a wave running
  # concurrently for the same module -- left $f empty and was logged `miss` no matter what it had
  # achieved. main:0204bab4 signed off PASS, its staged file gates MATCH, and the log still says
  # miss. The function still lands (the wave reads staging), but every band-conversion number the
  # spend decisions are made from is understated.
  f=$(grep -rl "USA: ${TAG}${ADDR}" "$WIP" 2>/dev/null | head -1)
  [ -z "$f" ] && f=$(grep -rl "USA: ${TAG}${ADDR}" "$SRCDIR" 2>/dev/null | head -1)
  [ -z "$f" ] && f=$(grep -rl "USA: ${TAG}${ADDR}" "$STAGE" 2>/dev/null | head -1)
  if [ -n "$f" ] && python "$KIT/wgate.py" "$MARG" "$ADDR" "$f" 2>&1 | tail -1 | grep -q "^MATCH"; then
    matched=$((matched+1)); v=MATCH
    # A MATCH THE INTEGRATOR CANNOT SEE IS NOT A MATCH. Workers write into $SRCDIR (inside the
    # repo), but pull_all picks the module to integrate by counting files in staging/<module>/ --
    # so a match left only in src/ never triggers integration, and the next wave for a DIFFERENT
    # module quarantines it as a foreign untracked file. ov017:021d4e38 (4796B, $7.72) sat in
    # src/Combat/Overlay_17 with staging empty until it was copied in by hand.
    mkdir -p "$STAGE"
    # VERIFY THE COPY, AND FALL BACK TO THE PROOF. A wave for ANOTHER module quarantines this
    # module's untracked .cpp while it runs, so $f routinely vanishes between the gate above and
    # this copy -- and `cp ... 2>/dev/null` said nothing, leaving the match invisible to the
    # integration trigger. ov000:0215858c ($19.23) and ov024:021ea85c both had to be staged by hand.
    # wgate archives every MATCH under gated/<mod>/<addr>.cpp, so that copy is proof and is safe to
    # stage from.
    if ! cp "$f" "$STAGE/$(basename "$f")" 2>/dev/null || [ ! -s "$STAGE/$(basename "$f")" ]; then
      _gsuf=$([ "$MOD" = "main" ] && echo main || echo "ov$MOD")
      if [ -s "$SP/gated/$_gsuf/$ADDR.cpp" ]; then
        cp "$SP/gated/$_gsuf/$ADDR.cpp" "$STAGE/$ADDR.cpp"
        echo "$(date '+%H:%M') s$SLOT staged $ADDR from gated/ (src copy had vanished)" >> "$LOG"
      else
        echo "$(date '+%H:%M') s$SLOT WARN $ADDR MATCHED but could not be staged" >> "$LOG"
      fi
    fi
  else
    v=miss
    # CARRY WHAT THIS SESSION LEARNED. A big function needs more context than one session
    # has; without this the next pass starts from the scaffold again and can never converge.
    python "$KIT/handoff.py" "$SP/$DOC" "$ADDR" "$OUT" "$FSIZE" >> "$LOG" 2>&1
    # STAGING HOLDS MATCHES ONLY. A worker that fails still leaves its best attempt behind, and
    # every wave then copies that non-matching file into src/, watches classify reject it, and burns
    # a full rebuild reporting `committed 0`. That is what had main rebuilding every twelve minutes
    # to land nothing. Keep the attempt -- it is useful to nearmiss and diffmine -- but move it out
    # of the path the integrator reads.
    #
    # THIS BRANCH WAS A BARE `:` AND THE ATTEMPTS WERE BEING DESTROYED. Preservation depended
    # entirely on the file surviving untracked in src/ until some later wave happened to sweep it
    # into hold_<mod>, and on 2026-08-26 five of one night's near-misses did not survive that:
    # 02093b90 at BYTEDIFF 14 (pure register residue -- one colorsweep away), 02069fec at 63,
    # 020830cc at 165, 020def98 and 02005ac4, none of them anywhere on disk afterwards. A copy
    # costs nothing and `attempts/` is gathered by repairsweep, so every future rule gets a free
    # second pass at work that has already been paid for.
    # NEVER OVERWRITE A BETTER ARTIFACT. This copy exists to preserve near-misses, and doing it
    # unconditionally destroyed them: ov024:021f9874 sat at OVERGEN 4 until a later session ended at
    # SHAPE 87 and copied over the same basename, so the ledger still advertised "4 bytes" while the
    # only file behind it was 87 bytes off -- and the near-miss queue sent opus at it on that basis.
    # SWEEP THE SESSION'S OWN OUTPUT, NOW. This artifact is the closest anyone has been to this
    # address and colorsweep has never seen it -- the rules only ever reached it through the hourly
    # repairsweep, which walks a 475-candidate pool in order, so a session ending ONE rule short of
    # a match sat unswept for hours. It costs CPU, the address is still claimed, and a MATCH here is
    # a function closed for the price of a compile.
    if [ -f "$f" ] && [ "$v" != "MATCH" ]; then
      _post=$(timeout "${POSTSWEEP_TIMEOUT:-900}" python "$KIT/presweep.py" "$MARG" "$ADDR" "$f" --force 2>/dev/null | tr -d '\r')
      case "$_post" in
        MATCH*)
          _pf=${_post#MATCH }
          mkdir -p "$STAGE"
          sed 's|^// SCRATCH-USA: func_|// USA: func_|' "$_pf" > "$STAGE/$ADDR.cpp" 2>/dev/null
          echo "$(date '+%H:%M') s$SLOT postsweep MATCHED $ADDR from this session's own artifact" >> "$LOG"
          v="MATCH"
          ;;
        IMPROVED*)
          echo "$(date '+%H:%M') s$SLOT postsweep $ADDR ${_post#IMPROVED }" >> "$LOG"
          ;;
      esac
    fi

    # Nothing is compared or discarded: a differing artifact lands under a unique name, so the row
    # blocker.py writes always points at the file that actually produced it.
    _keep="-"
    if [ -f "$f" ]; then
      mkdir -p "$SP/attempts"
      _dst="$SP/attempts/$(basename "$f")"
      if [ -f "$_dst" ] && ! cmp -s "$f" "$_dst"; then
        _dst="${_dst%.cpp}.s$(date +%s).cpp"
      fi
      cp "$f" "$_dst" 2>/dev/null
      _keep="$_dst"
    fi
    python "$KIT/blocker.py" "$MARG" "$ADDR" "$_keep" "$FSIZE" >> "$LOG" 2>&1
  fi

  # RE-CAP THE DOC ON EVERY PATH. DOC_MAX is applied when recipe_select GENERATES the file, and the
  # session then appends its own handoff to that same file, so the artifact left on disk is bounded
  # by nobody. The regeneration that fixes it lived in the miss branch alone, which is why a MATCH
  # left 027_021db524.md at 59,502 bytes -- past the ~58KB Read limit the cap exists to respect.
  python "$KIT/recipe_select.py" "$MARG" "$ADDR" >/dev/null 2>&1

  # ONE FILE PER SESSION, AT MOST. Workers iterate by writing variant after variant, and every one
  # of them landed in staging: a single FAILED function left 8 files behind (022157f8), another 4.
  # The integrator then copies all of them into src/, classify rejects each, and the wave burns a
  # rebuild to report `committed 0`. Keep only the file that actually gated MATCH; sweep every other
  # file this session created into attempts/ (kept -- nearmiss and diffmine read them).
  # SWEEP ONLY THIS ADDRESS. `-newer $STAMP` alone matches anything staged while the session ran,
  # including a hand-matched function for a DIFFERENT address -- on 2026-08-20 a byte-exact ov017
  # source staged from the main thread was moved to attempts/ by a worker that never touched it.
  # COMPARE THE STAGED COPY, NOT THE FILE IT CAME FROM. `$f` is the proven file where the worker
  # wrote it ($WIP/, or src/), and the copy this session must keep is `$STAGE/<basename>` -- a
  # different path, so a `$_v = $f` guard never fired and the sweep moved every MATCH straight back
  # out of staging. Integration is triggered by counting staging/*/*.cpp, so no worker match could
  # land at all: 6 of them sat in attempts/ while the log said MATCH.
  mkdir -p "$SP/attempts"
  _fb=""; [ -n "$f" ] && _fb=$(basename "$f")
  while IFS= read -r _v; do
    [ -z "$_v" ] && continue
    if [ "$v" = "MATCH" ]; then
      _vb=$(basename "$_v")
      [ -n "$_fb" ] && [ "$_vb" = "$_fb" ] && continue
      [ "$_vb" = "$ADDR.cpp" ] && continue
    fi
    # SAME NO-OVERWRITE RULE AS THE KEEP-COPY ABOVE. Guarding only that one left this path
    # destroying artifacts anyway: ov024:02079cf8's 3-byte source was replaced here by a 4-byte
    # session minutes after the first guard went in. Two writers, one rule.
    _sd="$SP/attempts/$(basename "$_v")"
    if [ -f "$_sd" ] && ! cmp -s "$_v" "$_sd"; then
      _sd="${_sd%.cpp}.s$(date +%s).cpp"
    fi
    mv "$_v" "$_sd" 2>/dev/null && _swept=$((${_swept:-0} + 1))
  done <<EOF
$(find "$STAGE" -name '*.cpp' -newer "$STAMP" -exec grep -l "USA: ${TAG}${ADDR}" {} + 2>/dev/null)
EOF
  [ "${_swept:-0}" -gt 0 ] && echo "$(date '+%H:%M') s$SLOT swept ${_swept} non-final file(s) out of staging" >> "$LOG"
  # THE MATCH MUST BE IN STAGING WHEN THIS SESSION ENDS. Anything that removes it -- this sweep, a
  # concurrent wave, a vanished source -- is recoverable from wgate's archive, and a MATCH that is
  # not staged is invisible to every later pass.
  if [ "$v" = "MATCH" ] && [ -z "$(find "$STAGE" -name '*.cpp' -exec grep -l "USA: ${TAG}${ADDR}" {} + 2>/dev/null)" ]; then
    _gsuf=$([ "$MOD" = "main" ] && echo main || echo "ov$MOD")
    if [ -s "$SP/gated/$_gsuf/$ADDR.cpp" ]; then
      cp "$SP/gated/$_gsuf/$ADDR.cpp" "$STAGE/$ADDR.cpp"
      echo "$(date '+%H:%M') s$SLOT restaged $ADDR from gated/ after the sweep" >> "$LOG"
    else
      echo "$(date '+%H:%M') s$SLOT WARN $ADDR MATCHED but is not staged and has no gated proof" >> "$LOG"
    fi
  fi
  _swept=0
  echo "$(date '+%H:%M') s$SLOT $v $ADDR \$$cost (slot \$$spent/\$$BUDGET)" >> "$LOG"
  (cd "$REPO" && python "$KIT/pad/repool.py" --apply --rev a70058a0 >/dev/null 2>&1)
done

echo "$(date '+%H:%M') s$SLOT done: $matched/$tried matched, \$$spent spent" >> "$LOG"
