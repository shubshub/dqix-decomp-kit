"""Where the kit's code, its state and the decomp checkout live.

    KIT    this checkout: scripts, docs, skills. Nothing the pipeline produces is written here.
    SP     the state directory: attempts, logs, claims, staging, worker docs, knobs. $DQIX_STATE, else
           the path in KIT/state.path, else ../dqix-kit-state beside the checkout. Kept outside the
           checkout so no git command run in it can touch a single attempt.
    REPO   the decomp checkout: $DQIX_REPO, else ../dqix-decomp beside the checkout.

    python kitpaths.py kit|state|repo     print one of them, for shell scripts
    python kitpaths.py behind             commits the published kit is ahead of this checkout
"""
import os
import shutil
import sys


def _norm(p):
    return os.path.abspath(p).replace("\\", "/")


def bash(base=None):
    env = dict(os.environ if base is None else base)
    if os.name != "nt":
        found = shutil.which("bash")
        if not found:
            raise FileNotFoundError("bash is not on PATH")
        return found, env
    roots = [os.path.join(os.environ[k], "Git") for k in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")
             if os.environ.get(k)]
    if os.environ.get("LOCALAPPDATA"):
        roots.append(os.path.join(os.environ["LOCALAPPDATA"], "Programs", "Git"))
    for exe in (shutil.which("git"), shutil.which("bash")):
        if exe:
            d = os.path.dirname(os.path.abspath(exe))
            while os.path.basename(d).lower() in ("cmd", "bin", "usr", "mingw32", "mingw64"):
                d = os.path.dirname(d)
            roots.append(d)
    for root in roots:
        sh = os.path.join(root, "usr", "bin", "bash.exe")
        if os.path.isfile(sh):
            env["PATH"] = os.pathsep.join([os.path.join(root, "usr", "bin"),
                                           os.path.join(root, "mingw64", "bin"), env.get("PATH", "")])
            return _norm(sh), env
    raise FileNotFoundError("no Git Bash found: install Git for Windows")


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


def module_label(mod):
    m = str(mod).strip()
    if m == "main":
        return "main"
    if m.isdigit():
        return "ov%03d" % int(m)
    if m[:2].lower() == "ov" and m[2:].isdigit():
        return "ov%03d" % int(m[2:])
    return m
BUSY = ("pull_all.pid", "wave.lock", "claims/INTEGRATING")
FRESH_EVERY = 600


def busy():
    found = []
    for b in BUSY:
        p = os.path.join(SP, b)
        if os.path.isdir(p) or (os.path.isfile(p) and os.path.getsize(p) > 0):
            found.append(b)
    return found


def behind():
    """Commits the published kit is ahead of this checkout, fetched at most every FRESH_EVERY seconds."""
    import subprocess
    import time
    stamp = os.path.join(SP, "wlog", ".kit_fresh")
    run = lambda *a: subprocess.run(["git", "-C", KIT, *a], capture_output=True, text=True, timeout=30)
    head = run("rev-parse", "HEAD").stdout.strip()
    try:
        count, seen = (open(stamp, encoding="utf-8").read().split() + [""])[:2]
        count, age = int(count), time.time() - os.path.getmtime(stamp)
    except (OSError, ValueError):
        count, seen, age = 0, "", FRESH_EVERY
    if age >= FRESH_EVERY or seen != head:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        open(stamp, "w", encoding="utf-8").write(f"{count if seen == head else 0} {head}")
        if run("fetch", "-q", KIT_URL, KIT_BRANCH).returncode == 0:
            count = int(run("rev-list", "--count", "HEAD..FETCH_HEAD").stdout.strip() or 0)
            open(stamp, "w", encoding="utf-8").write(f"{count} {head}")
        elif seen != head:
            count = 0
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
    elif which in ("kit", "state", "repo"):
        print({"kit": KIT, "state": SP, "repo": REPO}[which])
    else:
        sys.exit(__doc__)
