#!/bin/bash
# Merge human-written decomp into ours. Run --check first in every session.
#
# SCOPE, and it is narrow on purpose: the OPEN PULL REQUESTS against
# DQIX/dqix-decomp, plus any single ref the user names explicitly. Nothing else.
#
# An earlier version of this script enumerated a hard-coded list of contributor
# branches. That is how eakeys/zone3d -- work its author had not proposed for
# merge and had not finished -- got merged uninstructed, then reverted. A branch
# that is not an open PR is not an invitation; if the user wants one, they will
# name it (that is how eakeys/file_loading came in). So this script discovers
# candidates from the PR list and from nowhere else.
#
# The clone was SHALLOW (root == upstream/main's tip), which made git report NO
# common ancestor with any contributor branch and a 12,328-file diff. `git fetch
# --unshallow` fixed that permanently; if "refusing to merge unrelated
# histories" ever returns, that is the cause.
#
# The repair passes are NOT optional polish -- a raw `git merge` of a substantial
# branch does not compile, does not link, and does not checksum:
#
#   union_merge.py      delinks.txt/symbols.txt are keyed records, not prose
#   fix_includes.py     a branch may move headers (System/ -> Util/Random.h)
#   rename_symbols.py   renames break at COMPILE time, before the linker runs
#   relink_undefined.py callers on both sides name functions the other renamed
#   merge_fixups.sh     the few sites no address lookup can bridge
#
# The relink pass must LOOP: each round exposes references masked by the previous
# round's errors.
#
# tools/configure.py carries two deltas upstream does not have, BOTH inside
# add_mwcc_builds, which is also where upstream edits land -- so every upstream
# merge conflicts there and resolving in theirs' favour silently drops one:
#   * the get_asm_files loop that emits mwasm builds for .s sources
#   * the CC_OVERRIDES / $cc_exe per-file compiler override
# Keep both sides of that hunk. selfcheck.py asserts they are still present.
#
# Usage:
#   bash merge_human.sh --check      list open PRs and whether we already have them
#   bash merge_human.sh <number>     merge that PR's head
#   bash merge_human.sh <ref>        merge a ref the user named explicitly
set -u
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
# dsd ships under the platform's own name: `dsd.exe` on Windows, plain `dsd` on Linux. Ask the decomp
# what it called it rather than guessing, so this call cannot fail quietly into an empty pipe.
DSD="dsd"; [ -x "$REPO/$DSD.exe" ] && DSD="dsd.exe"
UPSTREAM="DQIX/dqix-decomp"
cd "$REPO" || exit 2
mkdir -p "$SP/wlog"

[ $# -ge 1 ] || { echo "usage: merge_human.sh --check | <pr-number> | <ref>"; exit 2; }
ARG="$1"

if [ "$ARG" = "--check" ]; then
  git fetch -q upstream 2>/dev/null
  gh pr list --repo "$UPSTREAM" --state open \
     --json number,title,author,headRefOid --limit 50 \
    | "$PY" -c '
import json, subprocess, sys
for pr in json.load(sys.stdin):
    sha = pr["headRefOid"]
    if subprocess.run(["git", "cat-file", "-e", sha + "^{commit}"],
                      capture_output=True).returncode:
        subprocess.run(["git", "fetch", "-q", "upstream", "pull/%d/head" % pr["number"]],
                       capture_output=True)
    have = subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"],
                          capture_output=True).returncode == 0
    print("  #%-4d %-10s %-9s %s" % (pr["number"], pr["author"]["login"],
                                     "CONTAINED" if have else "OUTSTANDING", pr["title"]))
'
  exit 0
fi

case "$ARG" in
  ''|*[!0-9]*) REF="$ARG" ;;                    # a ref the user named
  *) git fetch -q upstream "pull/$ARG/head" || exit 1
     REF=$(gh pr view "$ARG" --repo "$UPSTREAM" --json headRefOid -q .headRefOid) ;;
esac
git rev-parse --verify "$REF^{commit}" >/dev/null 2>&1 || { echo "merge_human: unknown ref $REF"; exit 1; }

[ -n "$(git status --porcelain)" ] && { echo "merge_human: working tree dirty, refusing"; exit 1; }
git merge-base --is-ancestor "$REF" HEAD 2>/dev/null && { echo "merge_human: already contained"; exit 0; }
echo "merge_human: $ARG ($REF) -> $(git rev-parse --abbrev-ref HEAD)"
PRE=$(git rev-parse HEAD)

repool_pools() {
  echo "$PRE" > "$SP/wlog/last_merge_base.txt"
  "$PY" "$KIT/pad/repool.py" --apply --rev "$PRE" > "$SP/wlog/repool_merge.log" 2>&1
  echo "  pools: $(tail -1 "$SP/wlog/repool_merge.log")"
}

git merge --no-commit "$REF" >/dev/null 2>&1
CONF=$(git diff --name-only --diff-filter=U)
if [ -z "$CONF" ]; then
  git merge --abort 2>/dev/null
  git merge "$REF" -m "Merge $ARG" || exit 1
  echo "merge_human: clean merge"
  repool_pools
  exit 0
fi

# Sources and headers: theirs. Where both sides have the file, ours is a stale
# hand-copy of an older revision of the same work, not independent decomp.
for f in $(echo "$CONF" | grep -E '^(src|include)/'); do
  git checkout --theirs -- "$f" && git add "$f"
done

# Config: semantic 3-way union, then delete any of our sources whose address
# range a human file now covers (left on disk they are orphans nothing builds).
CFG=$(git diff --name-only --diff-filter=U)
if [ -n "$CFG" ]; then
  "$PY" "$KIT/union_merge.py" $CFG > "$SP/wlog/merge_human.log" 2>&1
  grep -E "^UNION" "$SP/wlog/merge_human.log"
  grep "^DROP-FILE " "$SP/wlog/merge_human.log" | sed 's/^DROP-FILE //' | while read -r d; do
    [ -f "$d" ] && git rm -q --ignore-unmatch "$d" && echo "  removed superseded $d"
  done
  # Only records get a union rule. Anything else (tools/, README) is still full of
  # conflict markers; staging it commits a file that does not even parse.
  STILL=""
  for f in $CFG; do
    if grep -q '^<<<<<<< ' "$f" 2>/dev/null; then STILL="$STILL $f"; else git add "$f"; fi
  done
  if [ -n "$STILL" ]; then
    echo "merge_human: resolve by hand, then re-run the repair passes:$STILL"
    exit 1
  fi
fi

"$PY" "$KIT/fix_includes.py"
"$PY" "$KIT/rename_symbols.py" HEAD
"$PY" tools/configure.py usa --no-extract > "$SP/wlog/merge_configure.log" 2>&1 || { echo "merge_human: configure FAILED"; exit 1; }
# configure exits 0 on this, but delinks naming a file we do not have means a DROP-FILE
# deleted a source we still build, or a record survived a rename. Both silently unmatch it.
if grep -q "not on disk" "$SP/wlog/merge_configure.log"; then
  echo "merge_human: delinks.txt names sources that are gone -- not committing"
  grep -A2 "not on disk" "$SP/wlog/merge_configure.log"
  exit 1
fi

for i in 1 2 3 4 5 6 7 8; do
  if ninja check > "$SP/wlog/merge_check.log" 2>&1; then
    echo "merge_human: ninja check PASSES after $((i - 1)) repair rounds"
    break
  fi
  "$PY" "$KIT/relink_undefined.py" "$SP/wlog/merge_check.log" > "$SP/wlog/relink_$i.log" 2>&1
  bash "$KIT/merge_fixups.sh" >/dev/null
  echo "  round $i: $(grep -c '^  [A-Za-z_]' "$SP/wlog/relink_$i.log") call sites repointed"

  # Symbols we still declare that nothing defines any more, and symbols the
  # branch made static -- ONLY when the link itself succeeded. "not found in
  # linked binary" also fires for every symbol in a translation unit that failed
  # to COMPILE, which is the normal state mid-repair; running this on a broken
  # build deleted 24 real symbols the branch was adding.
  if ! grep -q "^FAILED.*arm9\.o" "$SP/wlog/merge_check.log" \
     && ! grep -qE "cpp:[0-9]+:" "$SP/wlog/merge_check.log"; then
    ./"$DSD" check symbols --config-path config/usa/arm9/config.yaml \
        --elf-path build/usa/arm9.o --fail 2>&1 \
      | grep -oE "Symbol '[^']+'" | cut -d"'" -f2 | sort -u > "$SP/wlog/stale_symbols.txt"
    # A link that SUCCEEDS still reports the whole table when the elf is stale, and dropping it
    # took symbols.txt from 7987 lines to 2058 -- the delink then fails on every relocation.
    N=$(wc -l < "$SP/wlog/stale_symbols.txt")
    if [ "$N" -gt 50 ]; then
      echo "  refusing to drop $N symbols; see wlog/stale_symbols.txt"
    else
      while read -r s; do
        sed -i "/^$s /d" config/usa/arm9/symbols.txt && echo "  dropped stale symbol $s"
      done < "$SP/wlog/stale_symbols.txt"
    fi
  fi
done

if ninja check > "$SP/wlog/merge_check.log" 2>&1; then
  git add -A
  git commit -q -m "Merge $ARG" && echo "merge_human: OK, committed $(git rev-parse --short HEAD)"
  repool_pools
  # Regenerate before reading it: report.json is a build artefact, so without this the line either
  # prints a stale number or tracebacks on a missing file and makes a successful merge look failed.
  rm -f build/usa/report.json; ninja report >/dev/null 2>&1
  "$PY" -c "import json;m=json.load(open('build/usa/report.json'))['measures'];print('  coverage %.2f%% (%d matched)'%(m['matched_functions_percent'],m['matched_functions']))" 2>/dev/null \
    || echo "  (coverage unavailable)"
else
  echo "merge_human: ninja check STILL FAILS -- not committed. First error:"
  grep -m1 -E "cpp:[0-9]+:|^Error:" "$SP/wlog/merge_check.log" || tail -3 "$SP/wlog/merge_check.log"
  exit 1
fi
