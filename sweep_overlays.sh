#!/bin/bash
# Identify every unmatched overlay function against the reference decompilations, one overlay at a
# time. Library code is not confined to the arm9 bands -- NNS graphics and sound, DWC wifi and the
# filesystem live in overlays -- so the bands order the search, they do not bound it.
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
# The interpreter this kit is running under. A bare `python` is python3 under Git Bash on
# Windows and does not exist at all on a Debian that keeps its packages in a virtualenv, so
# every call below asks the kit which interpreter to use instead of assuming one.
PY="${DQIX_PYTHON:-$(python3 "$KIT/kitpaths.py" py)}"
# ninja and anything else installed beside it live in the same directory, and they are on
# PATH only while the venv is ACTIVATED. Put them there for this script's children.
export PATH="$(dirname "$PY"):$PATH"
SP="$("$PY" "$KIT/kitpaths.py" state)"
cd "$SP" || exit 1
for o in $(cat "$SP/wlog/ovlist.txt"); do
    "$PY" -u "$KIT/sdkident.py" sweep "$o" > "$SP/wlog/ident_ov$o.txt" 2>&1
    echo "$(date +%H:%M) ov$o $(grep -c '^EXACT\|^CLOSE' "$SP/wlog/ident_ov$o.txt")" \
        >> "$SP/wlog/ident_ov_progress.txt"
done
echo done >> "$SP/wlog/ident_ov_progress.txt"
