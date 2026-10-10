#!/bin/bash
# ZERO-TOKEN: gate every candidate .cpp sitting in the scratchpad root and stage the ones that already
# MATCH. Workers write every attempt to SCRATCH/w<addr>.cpp; 2008 of them survive. ov_recover gathers
# from hold_*/quarantine/<mod>_stage but NEVER from here, so a candidate that reached byte-exact after
# the worker's last reported verdict just sits unbuilt. Found by accident: 2 of the first 35 examined
# already gated MATCH.
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
# The interpreter this kit is running under. A bare `python` is python3 under Git Bash on
# Windows and does not exist at all on a Debian that keeps its packages in a virtualenv, so
# every call below asks the kit which interpreter to use instead of assuming one.
PY="${DQIX_PYTHON:-$(python3 "$KIT/kitpaths.py" py)}"
# ninja and anything else installed beside it live in the same directory, and they are on
# PATH only while the venv is ACTIVATED. Put them there for this script's children.
export PATH="$(dirname "$PY"):$PATH"
SP="$("$PY" "$KIT/kitpaths.py" state)"
REPO="$("$PY" "$KIT/kitpaths.py" repo)"; cd "$REPO" || exit 2
LOG="$SP/wlog/wgate_scratch.log"; : > "$LOG"
hits=0; tried=0
for f in "$SP"/w[0-9a-f]*.cpp; do
  a=$(basename "$f" .cpp); a=${a#w}
  [[ "$a" =~ ^[0-9a-f]{8}$ ]] || continue
  # skip anything already in the build
  grep -qi "\.text start:0x0*$a " config/usa/arm9/delinks.txt config/usa/arm9/overlays/*/delinks.txt 2>/dev/null && continue
  if [ $((16#${a:0:4})) -lt $((16#0215)) ]; then m=main; else
    m=$(for sy in config/usa/arm9/overlays/ov*/symbols.txt; do grep -q "addr:0x$a\b" "$sy" && basename "$(dirname "$sy")" | sed 's/ov//' && break; done)
  fi
  [ -z "$m" ] && continue
  tried=$((tried+1))
  if "$PY" "$KIT/wgate.py" "$m" "$a" "$f" 2>&1 | head -1 | grep -q '^MATCH'; then
    d="$SP/hold_main"; [ "$m" != "main" ] && d="$SP/hold_ov$m"
    mkdir -p "$d"; cp "$f" "$d/recovered_$a.cpp"; hits=$((hits+1))
    echo "MATCH $a ov$m -> $d" >> "$LOG"
  fi
done
echo "wgate_scratch: $hits already-matching candidates staged, of $tried gated" >> "$LOG"
tail -1 "$LOG"
