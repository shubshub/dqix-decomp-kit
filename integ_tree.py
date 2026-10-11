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


def link_dir(src, dst):
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
    tracked = set(git("ls-files", "extract").splitlines())
    for name in os.listdir(f"{REPO}/extract"):
        if os.path.isdir(f"{REPO}/extract/{name}") and not any(t.startswith(f"extract/{name}/") for t in tracked):
            link_dir(f"{REPO}/extract/{name}", f"{INTEG}/extract/{name}")
    for name in ("arm7_bios.bin",):
        if os.path.isfile(f"{REPO}/{name}") and not os.path.exists(f"{INTEG}/{name}"):
            shutil.copy2(f"{REPO}/{name}", f"{INTEG}/{name}")


def bootstrap():
    env = dict(os.environ, DQIX_REPO=INTEG, DQIX_REGION=REGION)
    cc = subprocess.run([sys.executable, f"{_kp.KIT}/buildcfg.py", "--cc"], cwd=INTEG, env=env,
                        capture_output=True, text=True, check=True).stdout.strip()
    if os.path.isfile(f"{INTEG}/build.ninja") and os.path.isfile(cc):
        return
    cmd = [sys.executable, "tools/configure.py", REGION, "--no-extract"]
    if os.environ.get("DQIX_PREINSTALLED_COMPILER"):
        cmd += ["--compiler", os.environ["DQIX_PREINSTALLED_COMPILER"]]
    if os.environ.get("DQIX_WINE"):
        cmd += ["-w", os.environ["DQIX_WINE"]]
    subprocess.run(cmd, cwd=INTEG, env=env, stdout=sys.stderr, check=True)
    if not os.path.isfile(cc):
        subprocess.run(["ninja", os.path.relpath(cc, INTEG).replace("\\", "/")], cwd=INTEG, env=env,
                       stdout=sys.stderr, check=True)
    if not (os.path.isfile(f"{INTEG}/build.ninja") and os.path.isfile(cc)):
        sys.exit(f"integration tree {INTEG} has no build.ninja or no compiler at {cc}")


def sync():
    if OFF:
        bootstrap()
        return INTEG
    if not os.path.exists(f"{INTEG}/.git"):
        create()
    tip, head = git("rev-parse", BRANCH), git("rev-parse", "HEAD", cwd=INTEG)
    if not LOCAL and subprocess.run(["git", "-C", REPO, "fetch", "-q", "origin", BRANCH],
                                    capture_output=True).returncode == 0:
        remote = git("rev-parse", f"origin/{BRANCH}")
        if remote != tip and ancestor(tip, remote):
            tip = remote
    git("checkout", "-q", "-f", "--detach", head if ancestor(tip, head) else tip, cwd=INTEG)
    bootstrap()
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
        try:
            print(sync())
        except subprocess.CalledProcessError as e:
            sys.exit(f"integration tree setup failed (exit {e.returncode}): {e.cmd}")
    elif cmd == "publish":
        sys.exit(publish())
    elif cmd == "report":
        report()
    else:
        sys.exit(__doc__)
