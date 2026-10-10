"""The decomp worktree integrations run in, so the main checkout never holds a half-integrated tree.

    python integ_tree.py path       print the integration tree
    python integ_tree.py sync       create it if missing, move it to the branch tip, print its path
    python integ_tree.py publish    push its HEAD to the branch, fast-forward the main checkout
    python integ_tree.py report     copy its build/<region>/report.json into the main checkout

$DQIX_INTEG names the tree (default: <decomp checkout>-integ); DQIX_INTEG=off integrates in the
main checkout itself. $DQIX_BRANCH is the branch (default decomp-matching). DQIX_PUBLISH=local
moves the branch without pushing.
"""
import os
import shutil
import subprocess
import sys

import kitpaths as _kp

REPO = os.environ.get("DQIX_MAIN_REPO") or _kp.REPO
BRANCH = os.environ.get("DQIX_BRANCH", "decomp-matching")
REGION = os.environ.get("DQIX_REGION", "usa")
_env = os.environ.get("DQIX_INTEG", "")
OFF = _env.lower() == "off"
LOCAL = os.environ.get("DQIX_PUBLISH") == "local"
INTEG = REPO if OFF else os.path.abspath(_env or f"{REPO}-integ").replace("\\", "/")


def git(*args, cwd=None, check=True):
    r = subprocess.run(["git", "-C", cwd or REPO, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed in {cwd or REPO}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def exclude(dst):
    """Keep a link out of `git add -A`. A .gitignore entry like `tools/mwccarm/` matches a directory
    but not a symlink, so ov_recover committed both links, and the fast-forward then replaced the
    main checkout's real extract/usa and tools/mwccarm with self-referencing links."""
    rel = "/" + os.path.relpath(dst, INTEG).replace("\\", "/")
    path = os.path.join(REPO, git("rev-parse", "--git-common-dir"), "info", "exclude")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        lines = []
    if rel not in lines:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(rel + "\n")


def link_dir(src, dst):
    exclude(dst)
    if os.path.lexists(dst) or not os.path.isdir(src):
        return
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", dst.replace("/", "\\"), src.replace("/", "\\")],
                       capture_output=True, check=True)
    else:
        os.symlink(src, dst)


def create():
    git("worktree", "prune")
    git("worktree", "add", "--detach", INTEG, BRANCH)
    for name in ("arm7_bios.bin",):
        if os.path.isfile(f"{REPO}/{name}") and not os.path.exists(f"{INTEG}/{name}"):
            shutil.copy2(f"{REPO}/{name}", f"{INTEG}/{name}")


def link_tools():
    """The untracked toolchain the decomp's build downloaded. ov_recover compiles before this tree
    has ever been built, so a fresh tree without them fails on a missing mwccarm."""
    tracked = set(git("ls-files", "extract").splitlines())
    for name in os.listdir(f"{REPO}/extract"):
        if os.path.isdir(f"{REPO}/extract/{name}") and not os.path.islink(f"{REPO}/extract/{name}") \
                and not any(t.startswith(f"extract/{name}/") for t in tracked):
            link_dir(f"{REPO}/extract/{name}", f"{INTEG}/extract/{name}")
    link_dir(f"{REPO}/tools/mwccarm", f"{INTEG}/tools/mwccarm")
    for name in ("wibo", "dsd", "dsd.exe", "objdiff-cli", "objdiff-cli.exe"):
        src, dst = f"{REPO}/{name}", f"{INTEG}/{name}"
        exclude(dst)
        if os.path.isfile(src) and not os.path.lexists(dst):
            shutil.copy2(src, dst) if os.name == "nt" else os.symlink(src, dst)


def sync():
    if OFF:
        return INTEG
    if not os.path.exists(f"{INTEG}/.git"):
        create()
    link_tools()
    tip, head = git("rev-parse", BRANCH), git("rev-parse", "HEAD", cwd=INTEG)
    if not LOCAL and subprocess.run(["git", "-C", REPO, "fetch", "-q", "origin", BRANCH],
                                    capture_output=True).returncode == 0:
        remote = git("rev-parse", f"origin/{BRANCH}")
        if remote != tip and ancestor(tip, remote):
            tip = remote
    git("checkout", "-q", "-f", "--detach", head if ancestor(tip, head) else tip, cwd=INTEG)
    return INTEG


def ancestor(older, newer):
    return subprocess.run(["git", "-C", REPO, "merge-base", "--is-ancestor", older, newer]).returncode == 0


def publish():
    if OFF:
        on = git("symbolic-ref", "-q", "--short", "HEAD", check=False)
        if on != BRANCH:
            print(f"REFUSED: HEAD is on '{on or 'a detached commit'}', not {BRANCH}; committed but not pushed")
            return 1
        if LOCAL:
            return 0
        r = subprocess.run(["git", "-C", REPO, "push", "-q", "origin", BRANCH], capture_output=True, text=True)
        return 0 if r.returncode == 0 else 1
    head, tip = git("rev-parse", "HEAD", cwd=INTEG), git("rev-parse", BRANCH)
    if head == tip:
        return 0
    if not ancestor(tip, head):
        print(f"REFUSED: {BRANCH} moved to {tip[:8]} during the integration; {head[:8]} not published")
        return 1
    for _ in range(0 if LOCAL else 3):
        if subprocess.run(["git", "-C", INTEG, "push", "-q", "origin", f"HEAD:{BRANCH}"]).returncode == 0:
            break
    else:
        if not LOCAL:
            return 1
    if git("symbolic-ref", "-q", "--short", "HEAD", check=False) == BRANCH:
        r = subprocess.run(["git", "-C", REPO, "merge", "-q", "--ff-only", head], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"pushed {head[:8]}, but the main checkout did not fast-forward: {r.stderr.strip()[:200]}")
            return 2
    else:
        git("update-ref", f"refs/heads/{BRANCH}", head, tip)
    return 0


def report():
    src = f"{INTEG}/build/{REGION}/report.json"
    if not OFF and os.path.isfile(src):
        os.makedirs(f"{REPO}/build/{REGION}", exist_ok=True)
        shutil.copy2(src, f"{REPO}/build/{REGION}/report.json")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "path":
        print(INTEG)
    elif cmd == "sync":
        print(sync())
    elif cmd == "publish":
        sys.exit(publish())
    elif cmd == "report":
        report()
    else:
        sys.exit(__doc__)
