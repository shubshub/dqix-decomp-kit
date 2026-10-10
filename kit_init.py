"""Prepare a fresh kit checkout: check dependencies and the decomp checkout, create state.

    python kit_init.py            check, create state directories and OPEN_WORK.md, build worker docs,
                                  copy the agent-neutral skills into .agents/skills/
    python kit_init.py --refs     also clone the reference decomps in refs/VERIFIED.txt and index them
    python kit_init.py --slow     also run the end-to-end crack tests (regress.py --slow)
    python kit_init.py --state D  keep state in directory D (written to state.path; must be outside the kit)

The decomp checkout is $DQIX_REPO, or ../dqix-decomp next to the kit. State is $DQIX_STATE, else the
path in state.path, else ../dqix-kit-state next to the kit.
"""
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys

import kitpaths

SP = kitpaths.SP
KIT = kitpaths.KIT
REPO = kitpaths.REPO

STATE_DIRS = ["wlog", "wlog/gates", "wip", "staging", "handwork", "attempts", "claims", "scaffold",
              "gated", "clsbest", "quarantine", "doc_cache", "refs"]
REQUIRED = {"capstone": "capstone", "elftools": "pyelftools"}
OPTIONAL = {"frida": "frida (only for frida/*.py and pad/renum/)", "yaml": "pyyaml (only for frida/schedforce.py)"}
REPO_FILES = ["tools/configure.py", "config/usa/arm9/symbols.txt", "config/usa/arm9/delinks.txt",
              "build.ninja", "extract/usa/arm9/arm9.bin"]
PORTABLE_SKILLS = ["dqix-hand-match", "dqix-status", "dqix-stop", "dqix-coordinate"]


def _which(tool):
    """Where `tool` is, counting the running interpreter's own directory.

    A virtualenv's bin holds the ninja and capstone this kit was installed with, but its bin is on
    PATH only while the venv is ACTIVATED -- and every kit script resolves its interpreter through
    kitpaths, so it runs correctly whether or not anyone remembered to activate. Looking there too
    is what makes that promise true here as well.
    """
    here = os.path.dirname(os.path.abspath(sys.executable))
    return shutil.which(tool, path=os.pathsep.join([here, os.environ.get("PATH", "")]))


def check_python():
    ok = True
    if sys.version_info < (3, 10):
        print(f"FAIL  Python {sys.version.split()[0]}; 3.10 or newer is required")
        ok = False
    missing = [pkg for mod, pkg in REQUIRED.items() if importlib.util.find_spec(mod) is None]
    if missing:
        print(f"FAIL  missing Python packages: pip install {' '.join(missing)}")
        ok = False
    for mod, what in OPTIONAL.items():
        if importlib.util.find_spec(mod) is None:
            print(f"note  optional package not installed: {what}")
    for tool in ("git", "ninja", "bash"):
        if _which(tool) is None:
            print(f"FAIL  {tool} not on PATH")
            ok = False
    if shutil.which("claude") is None:
        print("note  Claude Code CLI `claude` not on PATH; needed only for the worker fleet")
    print(f"ok    interpreter {sys.executable}")
    return ok


def check_repo():
    if not os.path.isdir(REPO):
        print(f"FAIL  decomp checkout not found at {REPO}; clone it there or set DQIX_REPO")
        return False
    missing = [f for f in REPO_FILES if not os.path.exists(os.path.join(REPO, f))]
    if missing:
        print(f"FAIL  {REPO} is not configured and built; missing: {', '.join(missing)}")
        print("      follow the decomp README (base ROM in place, python tools/configure.py, ninja check)")
        return False
    try:
        import buildcfg
    except Exception as e:
        print(f"FAIL  buildcfg could not read the build configuration: {e}")
        return False
    if not os.path.exists(buildcfg.CC_TOOL):
        print(f"FAIL  compiler {buildcfg.CC_TOOL} is missing; run `ninja check` in the decomp once to fetch the toolchain")
        return False
    # mwccarm is a Win32 PE everywhere, so off Windows something has to run it. The gate measures on
    # the compiler the ROM was built with, so the runner has to be the build's own, not whatever
    # happens to be installed: buildcfg takes it from the decomp's configure.py for that reason.
    if os.name != "nt" and not buildcfg.RUNNER:
        print(f"FAIL  no runner found for the Win32 toolchain (mwccarm.exe); the decomp builds on Linux "
              f"with wibo, which ninja fetches to {REPO}/wibo. Run `ninja check` in the decomp once, "
              f"or point $DQIX_WINE at one.")
        return False
    branch = subprocess.run(["git", "-C", REPO, "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    print(f"ok    decomp checkout {REPO} (branch {branch or '?'}), compiler {buildcfg.MWCC_VERSION}")
    return True


def make_state():
    for d in STATE_DIRS:
        os.makedirs(os.path.join(SP, d), exist_ok=True)
    ow = os.path.join(SP, "OPEN_WORK.md")
    if not os.path.exists(ow):
        shutil.copyfile(os.path.join(KIT, "OPEN_WORK.template.md"), ow)
        print("ok    created OPEN_WORK.md")
    print(f"ok    state directories under {SP}")


def mirror_skills():
    for name in PORTABLE_SKILLS:
        shutil.copytree(os.path.join(KIT, ".claude", "skills", name),
                        os.path.join(KIT, ".agents", "skills", name), dirs_exist_ok=True)
    print(f"ok    {len(PORTABLE_SKILLS)} skills copied to .agents/skills/")


def run(argv):
    print("run   " + " ".join(argv), flush=True)
    return subprocess.run(argv, cwd=KIT).returncode == 0


def clone_refs():
    refs = os.path.join(SP, "refs")
    for line in open(os.path.join(KIT, "refs", "VERIFIED.txt"), encoding="utf-8"):
        parts = line.split()
        if not parts or parts[0].startswith("#") or len(parts) < 2:
            continue
        dest = os.path.join(refs, parts[0])
        if os.path.isdir(dest):
            continue
        if not run(["git", "clone", "--depth", "1", "--single-branch", parts[1], dest]):
            print(f"FAIL  clone {parts[1]}")
            return False
    return run([sys.executable, "sdkident.py", "index"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refs", action="store_true", help="clone and index the reference decomps")
    ap.add_argument("--slow", action="store_true", help="run regress.py --slow")
    ap.add_argument("--state", help="state directory to record in state.path")
    args = ap.parse_args()
    global SP
    if args.state:
        with open(os.path.join(KIT, "state.path"), "w", encoding="utf-8") as fh:
            fh.write(os.path.abspath(args.state).replace("\\", "/") + "\n")
        importlib.reload(kitpaths)
        SP = kitpaths.SP
    try:
        inside = os.path.commonpath([os.path.abspath(SP), os.path.abspath(KIT)]) == os.path.abspath(KIT)
    except ValueError:
        inside = False
    if inside:
        print(f"FAIL  state directory {SP} is inside the checkout {KIT}; git could delete it. "
              "Pick one outside with --state")
        sys.exit(1)
    print(f"ok    state directory {SP}")
    ok = check_python()
    make_state()
    mirror_skills()
    repo_ok = check_repo()
    ok = ok and repo_ok
    if repo_ok:
        ok = run([sys.executable, "build_worker_docs.py"]) and ok
    if args.refs:
        ok = clone_refs() and ok
    if args.slow and repo_ok:
        ok = run([sys.executable, "regress.py", "--slow"]) and ok
    print("ready: run `python selfcheck.py`" if ok else "not ready: fix the FAIL lines above")
    sys.exit(0 if ok else 1)


main()
