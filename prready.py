"""Refuse a pull request built on a stale kit or a stale decomp.

    python prready.py kit       last step before a pull request on ZevyaDev/dqix-decomp-kit
    python prready.py decomp    last step before a pull request on ZevyaDev/dqix-decomp
    python prready.py --hook    Claude Code PreToolUse hook: blocks `gh pr create` until READY, and
                                `gh pr merge` on the decomp, which lands through landpr.sh

Fetches the published kit and decomp-matching and compares your commits with them. Exit 0: READY.
1: NOT READY, one line per problem saying what to do. 2: a fetch failed, or the hook blocked.
"""
import json
import os
import re
import subprocess
import sys

os.environ["DQIX_NO_FRESHNESS"] = "1"
import delinked
import kitpaths
from pragmas import CODEGEN_PRAGMA

KIT, REPO = kitpaths.KIT, kitpaths.REPO
MATCH_PATHS = ("src/", "include/", "config/")
SOURCE = (".c", ".cpp", ".s")
ADDR = re.compile(r"\b(02[0-9a-fA-F]{6})\b")
FUNCTION = re.compile(r"\bkind:function\S*\s+addr:0x([0-9a-fA-F]+)")
SYMBOLS = re.compile(r"^config/([^/]+)/(.+/symbols\.txt)$")
MARKER = re.compile(r"(?m)^(<{7}|>{7})( |\r?$)")
USA_TAG = re.compile(r"(?m)^// USA: \S+")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def published(repo, url, branch):
    if git(repo, "fetch", "-q", url, branch).returncode != 0:
        print(f"FETCH FAILED {url} {branch} in {repo}", file=sys.stderr)
        sys.exit(2)
    return git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()


def behind(repo, tip):
    return int(git(repo, "rev-list", "--count", f"HEAD..{tip}").stdout.strip() or 0)


def show(repo, rev, path):
    return git(repo, "show", f"{rev}:{path}").stdout


def delinks(repo, rev, root):
    names = git(repo, "ls-tree", "-r", "--name-only", rev, root).stdout.split()
    return [show(repo, rev, n) for n in names if n.endswith("delinks.txt")]


def landed_functions(repo, rev, root):
    found = set()
    for n in git(repo, "ls-tree", "-r", "--name-only", rev, root).stdout.split():
        if n.endswith("delinks.txt"):
            texts = [show(repo, rev, n)]
            funcs = FUNCTION.findall(show(repo, rev, n[:-len("delinks.txt")] + "symbols.txt"))
            found |= {int(a, 16) for a in funcs if delinked.covers(int(a, 16), texts)}
    return found


def diff_lines(repo, tip, path, sign):
    out = git(repo, "diff", "-U0", f"{tip}...HEAD", "--", path).stdout.splitlines()
    return [l[1:] for l in out if l.startswith(sign) and not l.startswith(sign * 3)]


def names(text):
    return {l.split()[0] for l in text.splitlines() if l.strip()}


def addrs(text):
    return {l.split()[0]: a.group(1).lower() for l in text.splitlines()
            if l.strip() and (a := re.search(r"\baddr:(0x[0-9a-fA-F]+)", l))}


def staleness():
    problems = []
    kit_tip = published(KIT, kitpaths.KIT_URL, kitpaths.KIT_BRANCH)
    n = behind(KIT, kit_tip)
    if n:
        problems.append(f"KIT BEHIND by {n} commit(s): run python kit_update.py, re-run selfcheck.py and "
                        "regress.py, re-gate what you changed")
    decomp_tip = published(REPO, kitpaths.DECOMP_URL, kitpaths.DECOMP_BRANCH)
    n = behind(REPO, decomp_tip)
    if n:
        problems.append(f"DECOMP BEHIND by {n} commit(s): in {REPO} run git pull --rebase "
                        f"{kitpaths.DECOMP_URL} {kitpaths.DECOMP_BRANCH}, then python tools/configure.py usa "
                        "&& ninja check")
    return problems, kit_tip, decomp_tip


def kit(kit_tip, decomp_tip):
    problems = []
    if git(KIT, "diff", "--name-only", f"{kit_tip}...HEAD", "--", "worker_src/deadends.md").stdout.strip():
        problems.append("NO DEAD ENDS: the kit takes no worker_src/deadends.md change; record the miss in $SP "
                        "(blocker.py, the handoff) and open the pull request from a branch without it")
    landed = landed_functions(REPO, decomp_tip, "config/usa/arm9")
    cited = dict.fromkeys(a.lower() for l in diff_lines(KIT, kit_tip, "worker_src/core.md", "+") for a in ADDR.findall(l))
    for addr in cited:
        if int(addr, 16) not in landed:
            problems.append(f"UNPROVEN CITATION {addr} in core.md: not matched on {kitpaths.DECOMP_BRANCH}; "
                            "land the match first or cite a landed address")
    return problems


def decomp(decomp_tip):
    problems = []
    changed = git(REPO, "diff", "--name-only", f"{decomp_tip}...HEAD").stdout.split()
    for path in changed:
        if not path.startswith(MATCH_PATHS):
            problems.append(f"OUTSIDE A MATCH {path}: differs from {kitpaths.DECOMP_BRANCH}; restore it with "
                            f"git checkout {decomp_tip[:10]} -- {path}, or send it as its own pull request")
        elif MARKER.search(show(REPO, "HEAD", path)):
            problems.append(f"CONFLICT MARKERS in {path}")
    wired = "\n".join(delinks(REPO, "HEAD", "config"))
    added = git(REPO, "diff", "--name-only", "--diff-filter=A", f"{decomp_tip}...HEAD", "--", "src").stdout.split()
    for path in added:
        if path.endswith(SOURCE) and f"{path}:" not in wired:
            problems.append(f"DEAD FILE {path}: no delinks.txt entry names it; delete it")
        elif path.endswith((".c", ".cpp")) and not USA_TAG.search(show(REPO, "HEAD", path)):
            problems.append(f"NO USA TAG {path}: put `// USA: <USA symbol>` on the line above the definition")
    for path in changed:
        if path.startswith("src/"):
            gained = (len(CODEGEN_PRAGMA.findall("\n".join(diff_lines(REPO, decomp_tip, path, "+"))))
                      - len(CODEGEN_PRAGMA.findall("\n".join(diff_lines(REPO, decomp_tip, path, "-")))))
            if gained > 0:
                problems.append(f"CODEGEN PRAGMA {path}: adds {gained}; a match never lands behind one "
                                f"(AGENTS.md rule 8): remove it and find what the source lacks")
    regions = [p.rstrip("/").split("/")[-1] for p in git(REPO, "ls-tree", "-d", "--name-only", "HEAD", "config/").stdout.split()]
    for path in changed:
        m = SYMBOLS.match(path)
        if not m:
            continue
        removed = "\n".join(diff_lines(REPO, decomp_tip, path, "-"))
        gone = names(removed) - names(show(REPO, "HEAD", path))
        was_at = addrs(removed)
        now_named = {a: n for n, a in addrs(show(REPO, "HEAD", path)).items()}
        for region in regions:
            other = f"config/{region}/{m.group(2)}"
            if other != path:
                theirs = names(show(REPO, "HEAD", other))
                for name in sorted(gone & theirs):
                    if now_named.get(was_at.get(name)) in theirs:
                        continue
                    problems.append(f"HALF RENAME {name}: gone from {path}, still in {other}; rename it there too")
    return problems


def hooked_target():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return None
    command = str(event.get("tool_input", {}).get("command", ""))
    action = re.search(r"\bgh\s+pr\s+(create|merge)\b", command)
    if not action:
        return None
    if "dqix-decomp-kit" in command:
        target = "kit"
    elif "dqix-decomp" in command:
        target = "decomp"
    else:
        cwd = os.path.abspath(event.get("cwd") or os.getcwd()).replace("\\", "/").lower()
        target = "decomp" if cwd.startswith(REPO.lower()) else "kit"
    if action.group(1) == "merge":
        return "land" if target == "decomp" else None
    return target


HOOK = sys.argv[1:] == ["--hook"]
if HOOK:
    target = hooked_target()
    if target is None:
        sys.exit(0)
    if target == "land":
        print("gh pr merge skips the strip, the region ports and the three-region build; "
              "land decomp pull requests with: bash landpr.sh <number>...", file=sys.stderr)
        sys.exit(2)
elif sys.argv[1:] in (["kit"], ["decomp"]):
    target = sys.argv[1]
else:
    sys.exit(__doc__)
problems, kit_tip, decomp_tip = staleness()
problems += kit(kit_tip, decomp_tip) if target == "kit" else decomp(decomp_tip)
report = "\n".join(problems + [f"NOT READY: {len(problems)} problem(s)" if problems else "READY"])
if HOOK:
    if problems:
        print(f"prready.py {target}: fix these before opening the pull request.\n{report}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)
print(report)
sys.exit(1 if problems else 0)
