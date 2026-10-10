"""Where the kit's code, its state and the decomp checkout live.

    KIT    this checkout: scripts, docs, skills. Nothing the pipeline produces is written here.
    SP     the state directory: attempts, logs, claims, staging, worker docs, knobs. $DQIX_STATE, else
           the path in KIT/state.path, else ../dqix-kit-state beside the checkout. Kept outside the
           checkout so no git command run in it can touch a single attempt.
    REPO   the decomp checkout: $DQIX_REPO, else ../dqix-decomp beside the checkout.

    python kitpaths.py kit|state|repo     print one of them, for shell scripts
    python kitpaths.py py                 the interpreter to run the kit with, for shell scripts
    python kitpaths.py behind             commits the published kit is ahead of this checkout
"""
import os
import shutil
import subprocess
import sys


def _norm(p):
    return os.path.abspath(p).replace("\\", "/")


KIT = _norm(os.path.dirname(os.path.abspath(__file__)))


def _state():
    env = os.environ.get("DQIX_STATE")
    if env:
        return _norm(env)
    try:
        with open(os.path.join(KIT, "state.path"), encoding="utf-8") as fh:
            line = fh.readline().strip()
        if line:
            return _norm(line if os.path.isabs(line) else os.path.join(KIT, line))
    except OSError:
        pass
    return _norm(os.path.join(os.path.dirname(KIT), "dqix-kit-state"))


SP = _state()
REPO = _norm(os.environ.get("DQIX_REPO", os.path.join(os.path.dirname(KIT), "dqix-decomp")))
CLAUDE_PROJECTS = _norm(os.environ.get("CLAUDE_PROJECTS", os.path.expanduser("~/.claude/projects")))
KIT_URL = os.environ.get("DQIX_KIT_URL", "https://github.com/ZevyaDev/dqix-decomp-kit.git")
KIT_BRANCH = os.environ.get("DQIX_KIT_BRANCH", "main")
DECOMP_URL = os.environ.get("DQIX_DECOMP_URL", "https://github.com/ZevyaDev/dqix-decomp.git")
DECOMP_BRANCH = os.environ.get("DQIX_DECOMP_BRANCH", "decomp-matching")
BUSY = ("pull_all.pid", "wave.lock", "claims/INTEGRATING")
FRESH_EVERY = 600


def busy():
    found = []
    for b in BUSY:
        p = os.path.join(SP, b)
        if os.path.isdir(p) or (os.path.isfile(p) and os.path.getsize(p) > 0):
            found.append(b)
    return found


def _has_kit_deps(interpreter):
    """Whether `interpreter` can import what every gate needs."""
    try:
        return subprocess.run([interpreter, "-c", "import capstone, elftools"],
                              capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def python():
    """The interpreter to run this kit with.

    $DQIX_PYTHON wins. Otherwise: one that already has the kit's dependencies -- the one running
    this, or `python` from an activated virtualenv, or one beside the checkout -- and only if none
    of those qualifies, whatever started us.

    The `.sh` scripts used to say a bare `python`. Under Git Bash on Windows that is python3, so it
    worked; on a Debian that keeps its packages in a virtualenv there is no `python` at all unless
    someone remembered to activate it, and every script died on its first line. They ask here
    instead -- and this function has to be able to answer while running under the SYSTEM python3,
    because that is what they have to ask with. So kitpaths.py itself imports nothing but the
    standard library.
    """
    env = os.environ.get("DQIX_PYTHON")
    if env:
        return env
    if _has_kit_deps(sys.executable):
        return sys.executable
    for cand in (shutil.which("python"), os.path.join(KIT, ".venv", "bin", "python"),
                 os.path.join(os.path.dirname(KIT), ".venv", "bin", "python")):
        if cand and os.path.exists(cand) and _has_kit_deps(cand):
            return os.path.abspath(cand)
    return sys.executable


def behind():
    """Commits the published kit is ahead of this checkout, fetched at most every FRESH_EVERY seconds."""
    import subprocess
    import time
    stamp = os.path.join(SP, "wlog", ".kit_fresh")
    try:
        count = int(open(stamp, encoding="utf-8").read().strip() or 0)
        age = time.time() - os.path.getmtime(stamp)
    except (OSError, ValueError):
        count, age = 0, FRESH_EVERY
    if age >= FRESH_EVERY:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        open(stamp, "w", encoding="utf-8").write(str(count))
        run = lambda *a: subprocess.run(["git", "-C", KIT, *a], capture_output=True, text=True, timeout=30)
        if run("fetch", "-q", KIT_URL, KIT_BRANCH).returncode == 0:
            count = int(run("rev-list", "--count", "HEAD..FETCH_HEAD").stdout.strip() or 0)
            open(stamp, "w", encoding="utf-8").write(str(count))
    return count


def stale_message(count):
    return (f"KIT IS {count} COMMIT(S) BEHIND the published kit: run `python {KIT}/kit_update.py` now "
            "(it keeps your own unpublished commits on top)")


def _freshness():
    count = behind()
    if count and not busy():
        print(stale_message(count), file=sys.stderr)


if os.environ.get("DQIX_NO_FRESHNESS") != "1":
    try:
        _freshness()
    except Exception:
        pass

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    if which == "behind":
        print(behind())
    elif which == "py":
        print(python())
    elif which in ("kit", "state", "repo"):
        print({"kit": KIT, "state": SP, "repo": REPO}[which])
    else:
        sys.exit(__doc__)
