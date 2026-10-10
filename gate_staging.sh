#!/bin/bash
# Gate every file in staging/<module> and move the failures out, so a wave only ever integrates
# candidates that have already passed the same check the integrator applies.
# Usage: gate_staging.sh <module>   (module = main or an overlay number, e.g. 016)
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
# The interpreter this kit is running under. A bare `python` is python3 under Git Bash on
# Windows and does not exist at all on a Debian that keeps its packages in a virtualenv, so
# every call below asks the kit which interpreter to use instead of assuming one.
PY="${DQIX_PYTHON:-$(python3 "$KIT/kitpaths.py" py)}"
# ninja and anything else installed beside it live in the same directory, and they are on
# PATH only while the venv is ACTIVATED. Put them there for this script's children.
export PATH="$(dirname "$PY"):$PATH"
SP="$("$PY" "$KIT/kitpaths.py" state)"
MOD="${1:-main}"
SUB="$MOD"
[ "$MOD" != main ] && [ -d "$SP/staging/ov$MOD" ] && SUB="ov$MOD"
DIR="$SP/staging/$SUB"
REJ="$SP/staging_rejected/$SUB"
mkdir -p "$REJ"
pass=0; fail=0
for f in "$DIR"/*.cpp; do
    [ -f "$f" ] || continue
    addr=$(grep -oEm1 "USA: func_(ov[0-9]+_)?[0-9a-fA-F]{8}" "$f" | grep -oE "[0-9a-fA-F]{8}$")
    if [ -z "$addr" ]; then
        echo "NOTAG   $(basename "$f")"; mv "$f" "$REJ/"; fail=$((fail+1)); continue
    fi
    out=$("$PY" "$KIT/wgate.py" "$MOD" "$addr" "$f" 2>&1 | tail -1)
    # A WRONG-SYMBOL is usually repairable, not fatal: translate.py names its output `Trans_<addr>`
    # and workers pick their own names, while the integrator renames to the config-bound symbol
    # anyway. Rejecting on the spot would strand work the pipeline can fix, so run the same repair
    # the integrator runs and re-gate. Only a file that is still wrong afterwards is rejected.
    # UNDEF-SYM belongs in the same case. It is the mirror fault -- a callee prototype declared
    # extern "C" whose ROM symbol is mangled, or one named for the wrong address -- and autorepair
    # fixes both. It also HIDES every later check: wgate reports UNDEF-SYM before RELOC-WRONG, so a
    # file rejected here can be byte-exact behind one linkage keyword. ov017:021ab280 was.
    case "$out" in
        WRONG-SYMBOL*|UNDEF-SYM*)
            "$PY" "$KIT/autorepair.py" "$MOD" "$addr" "$f" >/dev/null 2>&1
            out=$("$PY" "$KIT/wgate.py" "$MOD" "$addr" "$f" 2>&1 | tail -1)
            [ "$out" = "MATCH" ] && echo "REPAIRED $addr $(basename "$f")"
            ;;
    esac
    if [ "$out" = "MATCH" ]; then
        echo "MATCH   $addr $(basename "$f")"; pass=$((pass+1))
    else
        echo "REJECT  $addr $(basename "$f") :: $out"; mv "$f" "$REJ/"; fail=$((fail+1))
    fi
done
echo "== $MOD: $pass match, $fail rejected"
