#!/bin/bash
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
SP="$(python "$KIT/kitpaths.py" state)"
REPO="$(python "$KIT/kitpaths.py" repo)"
QUEUE="${1:-$SP/wlog/clean_queue.tsv}"
LOG="$SP/wlog/clean.log"
WORK="$SP/handwork/clean"
MANIFEST="$SP/clean/manifest.tsv"

_knob() { local v; v=$(tr -dc "$2" 2>/dev/null < "$1"); echo "${v:-$3}"; }
vcount() {
  python -c "import re,sys;H=re.compile(r'0x0?(?:4[0-9a-fA-F]{6}|2[37]f[0-9a-fA-F]{4})\b|^\s*#');print(sum(len(re.findall(r'\bvolatile\b',l)) for l in open(sys.argv[1],encoding='utf-8',errors='ignore') if not H.search(l)))" "$1"
}
pcount() {
  echo $(( $(python "$KIT/plausible.py" --blocking "$1" 2>/dev/null | grep -c .) + $(grep -c "^[[:space:]]*#[[:space:]]*pragma" "$1") ))
}
export WGATE_ALLOW_COMMITTED=1
finished() { grep -E " (CLEAN|KEEP|BLOCKED|SKIP|RETRY) $1( |$)" "$LOG" 2>/dev/null | tail -1 | grep -qvE " RETRY $1( |$)"; }
CLAIMS="$SP/clean/claims"
if [ "$1" = "--retry" ]; then
  shift
  for a in "$@"; do
    rmdir "$CLAIMS/$a" 2>/dev/null
    echo "$(date '+%H:%M') RETRY $a" >> "$LOG"
  done
  exit 0
fi
SIMPLE='^(CC-FLAG-EMULATION|INT-PUN-NULL-TEST|NOOP-ADDR-CAST|TERNARY-SELF|COMPARE-MINUS-ONE|SELF-STORE|SELF-ASSIGN|SPLIT-POINTER-ADD|INLINE-LOCAL-RETURN|INLINE-IDENTITY)$'
next_row() {
  local m a rest
  while IFS=$'\t' read -r m a rest; do
    [ -n "$a" ] && ! finished "$a" && mkdir "$CLAIMS/$a" 2>/dev/null && { printf '%s\t%s\t%s\n' "$m" "$a" "$rest"; return 0; }
  done < "$QUEUE"
  return 1
}
model_for() {
  local k kinds
  kinds=$(printf '%s\n' "$1" | grep -oE '[A-Z][A-Z0-9-]+[A-Z]:' | tr -d ':' | sort -u)
  [ -n "$kinds" ] || { echo opus; return; }
  for k in $kinds; do [[ "$k" =~ $SIMPLE ]] || { echo opus; return; }; done
  echo sonnet
}
land() {
  [ -s "$MANIFEST" ] && [ ! -e "$SP/CLEAN_HOLD" ] || return 0
  LANDCLEAN_TRIES=180 bash "$KIT/landclean.sh" 2>&1 | sed "s/^/$(date '+%H:%M') landclean: /" >> "$LOG"
}

mkdir -p "$WORK" "$SP/clean" "$CLAIMS"
git -C "$REPO" fetch -q origin decomp-matching
while :; do
  { [ -e "$SP/STOP_CLEAN" ] || [ -e "$SP/FLEET_STOPPED" ]; } && break
  ROW=$(next_row) || break
  IFS=$'\t' read -r MOD ADDR REL PRIVATE HINT DEST < <(printf '%s\n' "$ROW")
  F="$WORK/$MOD/$(basename "$REL")"
  mkdir -p "$(dirname "$F")"
  if [ -n "$DEST" ]; then
    if [ ! -s "$REL" ]; then
      echo "$(date '+%H:%M') SKIP $ADDR source gone: $REL" >> "$LOG"
      continue
    fi
    BASE=""
    cp "$REL" "$F"
    cp "$REL" "$WORK/.orig_$ADDR"
  else
    BASE=$(git -C "$REPO" rev-parse "origin/decomp-matching:$REL" 2>/dev/null)
    if [ -z "$BASE" ]; then
      echo "$(date '+%H:%M') SKIP $ADDR not on decomp-matching: $REL" >> "$LOG"
      continue
    fi
    git -C "$REPO" show "origin/decomp-matching:$REL" > "$F"
  fi
  before=$(vcount "$F")
  pbefore=$(pcount "$F")
  if [ "$before" -eq 0 ] 2>/dev/null && [ "$pbefore" -eq 0 ] 2>/dev/null; then
    echo "$(date '+%H:%M') SKIP $ADDR nothing invented left" >> "$LOG"
    continue
  fi
  SITES=$(python -c "import sys;t=open(sys.argv[1],encoding='utf-8',errors='ignore').read().split('\n');print('\n'.join('  line %s: %s' % (n, t[int(n)-1].strip()) for n in sys.argv[2].split(',') if n.strip() and int(n) <= len(t)))" "$F" "$PRIVATE")
  DOC=$(python "$KIT/recipe_select.py" "$MOD" "$ADDR" --lean 2>/dev/null | tr -d '\r' | tr '\\' '/' | head -1)
  MODEL=$(_knob "$SP/CLEAN_MODEL" 'a-z0-9.-' "$(model_for "$HINT")")
  CAP=$(_knob "$SP/CLEAN_CAP" '0-9.' 4)
  OUT="$SP/wlog/clean_${MOD}_${ADDR}.json"
  echo "$(date '+%H:%M') claim $ADDR $MOD $(basename "$REL") volatile=$before findings=$pbefore [$MODEL] cap \$$CAP" >> "$LOG"
  cd "$WORK" || exit 1
  WGATE_SESSION="clean_$(date +%s)" timeout -k 30 "${HANG_GUARD:-7200}" claude -p "DQIX cleanup worker, module $MOD.
$F is a byte-exact source of $ADDR that already landed on decomp-matching. Make it read like the
source the original developers wrote, and keep it byte-exact.
These lines carry constructs the original developers did not write -- each exists only to steer the
compiler (a volatile on data nothing else writes, a cast, a pun, an extra local, an identity):
$SITES
Each stands in for real source nobody found yet: an inline accessor, the real struct field and its
type, one shared prototype, a different declaration or statement order, a struct copy. Find it.
${HINT:+Hint: $HINT}
python $KIT/plausible.py --blocking $F names most such constructs: your result must report fewer than
now. Never change a call's arguments to fit a mangled name's parameter list: those names were guessed. Every volatile on a hardware register, ARM7-shared memory, or data an interrupt
handler or another thread writes is real: leave those lines exactly as they are.
Never introduce another construct the developers could not have written: no parameter the callers
never pass, no self-assignment, no zero-negation padding, no dead address-taking local, no codegen
#pragma. python $KIT/plausible.py $F must not report anything it did not report before.
Codegen levers: grep -n '^##' $KIT/worker_src/core.md, then read only the sections you need with
grep -n -A60 '<heading>' $KIT/worker_src/core.md.
${DOC:+Module recipes: $DOC.}
Target listing: python $KIT/wlist.py $MOD $ADDR
Gate after every edit: python $KIT/wgate.py $MOD $ADDR $F
Shell commands are allowlisted one at a time: run each command exactly as written above, alone,
with these absolute paths -- no cd, no &&, no ;, no pipes, no cat. Read files with the Read tool.
Edit only $F. When the gate prints STOP, stop. Do not rename functions, fields, parameters or locals
the fix does not need, and add no comments: naming is a separate, later pass.
If real source replaced an invented construct, append ONE tab-separated line to $SP/wlog/levers.tsv:
${ADDR}<TAB><size><TAB><bytes off without the invented construct><TAB><the source that replaced it>
Reply with exactly one line:
  CLEAN $ADDR
  KEEP $ADDR <why that construct is what the developers wrote>
  BLOCKED $ADDR <CLASS> <metric>" \
    --model "$MODEL" --permission-mode dontAsk --safe-mode --autocompact auto \
    --disallowedTools "WebFetch" "WebSearch" "NotebookEdit" "Task" "Agent" "TodoWrite" "Artifact" "Monitor" "Skill" \
    --max-budget-usd "$CAP" --output-format json \
    --allowedTools "Bash(python $KIT/wgate.py:*)" "Bash(python $KIT/wlist.py:*)" "Bash(python $KIT/wdiff.py:*)" \
      "Bash(python $KIT/plausible.py:*)" "Bash(python $KIT/colorsweep.py:*)" "Bash(grep:*)" \
      "Write" "Read" "Edit" "Grep" "Glob" \
    --add-dir "$SP" \
    > "$OUT" 2>/dev/null < /dev/null
  cd "$KIT" || exit 1
  cost=$(python -c "import json,sys;print('%.4f' % json.load(open(sys.argv[1]))['total_cost_usd'])" "$OUT" 2>/dev/null || echo "$CAP")
  said=$(python -c "import json,sys;print((json.load(open(sys.argv[1])).get('result') or '').strip().splitlines()[-1])" "$OUT" 2>/dev/null)
  after=$(vcount "$F")
  ORIG="$WORK/.orig_$ADDR"
  [ -n "$BASE" ] && git -C "$REPO" cat-file blob "$BASE" > "$ORIG"
  if [ -n "$DEST" ]; then
    if python "$KIT/wgate.py" "$MOD" "$ADDR" "$F" 2>&1 | grep -q "^MATCH" \
       && [ -z "$(python "$KIT/plausible.py" --blocking "$F" 2>/dev/null)" ] \
       && { grep -qi "^$ADDR " "$KIT/asm_allow.txt" || ! grep -qE '^[[:space:]]*asm\b|\basm[[:space:]]+(void|int|unsigned|char|long|short)\b' "$F"; } \
       && [ "$(grep -c "^[[:space:]]*#[[:space:]]*pragma" "$F")" -le "$(grep -c "^[[:space:]]*#[[:space:]]*pragma" "$REL")" ]; then
      if [ "$DEST" = "staging" ]; then
        _sub=$([ "$MOD" = "main" ] && echo main || echo "ov$MOD")
        mkdir -p "$SP/staging/$_sub"
        cp "$F" "$SP/staging/$_sub/$(basename "$REL")" && rm -f "$REL"
      else
        cp "$F" "$REL"
      fi
      v=CLEAN
    else
      v=BLOCKED
    fi
  elif python "$KIT/wgate.py" "$MOD" "$ADDR" "$F" 2>&1 | grep -q "^MATCH" \
     && python "$KIT/cleancheck.py" "$ORIG" "$F" "$PRIVATE" > /dev/null \
     && [ "$(pcount "$F")" -le "$pbefore" ]; then
    mkdir -p "$SP/clean/$(dirname "$REL")"
    cp "$F" "$SP/clean/$REL"
    printf '%s\t%s\t%s\n' "$REL" "$BASE" "volatile $before -> $after" >> "$MANIFEST"
    v=CLEAN
  elif [ "${said%% *}" = "KEEP" ]; then
    v=KEEP
  else
    v=BLOCKED
  fi
  echo "$(date '+%H:%M') $v $ADDR \$$cost volatile $before->$after | ${said:0:200}" >> "$LOG"
  rmdir "$CLAIMS/$ADDR" 2>/dev/null
  [ "$(wc -l < "$MANIFEST" 2>/dev/null || echo 0)" -ge "$(_knob "$SP/CLEAN_BATCH" '0-9' 8)" ] && land
done
land
echo "$(date '+%H:%M') cleanwork done" >> "$LOG"
