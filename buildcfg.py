#!/usr/bin/env python3
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
# The compiler, flags and include paths the BUILD uses, read from tools/configure.py itself.
# wgate, classify and the repair sweeps each carried their own copy of the flag list, so the
# rebase onto sp2p2 left every one of them measuring on a compiler the ROM is no longer built
# with. Importing the build's own definitions is the only arrangement they cannot drift from.
import ast
import os
import re
import shutil
import sys

REPO = _kp.REPO
_TOOLS = os.path.join(REPO, "tools")
# usa, jpn, or eur. Unset stays usa, which is every current config/ and extract/ path.
REGION = os.environ.get("DQIX_REGION", "usa")


def _load():
    cwd, argv = os.getcwd(), sys.argv[:]
    os.chdir(REPO)
    sys.argv = ["configure.py", REGION]
    sys.path.insert(0, _TOOLS)
    try:
        import configure
        return configure
    finally:
        os.chdir(cwd)
        sys.argv = argv
        if _TOOLS in sys.path:
            sys.path.remove(_TOOLS)


def _mwcc_defines(configure):
    '''The -d flags on configure.py's mwcc rule, for the region it was loaded with.

    $game_version is the ninja variable that rule uses for usa and jpn. The gate
    does not run ninja, so that variable is expanded to the loaded region.
    '''
    path = configure.__file__
    source = open(path, encoding="utf-8").read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "region_defines":
                value = eval(compile(ast.Expression(node.value), path, "eval"), configure.__dict__)
                return value.replace("$game_version", REGION)
    return f"-d {REGION}"


_cfg = _load()

MWCC_VERSION = _cfg.MWCC_VERSION
DECOMP_ME_COMPILER = _cfg.DECOMP_ME_COMPILER


def _from_repo(path):
    """configure.py builds its tool paths against the decomp root ('.' relative paths)."""
    return os.path.abspath(path if os.path.isabs(path) else os.path.join(REPO, path))


# mwccarm.exe is a Win32 PE on every platform -- the decomp builds it on Linux too, under wibo. So the
# FILE keeps its .exe name off Windows; what changes is who runs it.
def runner():
    """The program that executes a Win32 binary here, or "" when the host runs it itself.

    The build gets its loader from its own configure.py (`wibo`, the decomp's default on Linux, or
    whatever `-w` was given), and the kit takes the same one in the same order, so the gate measures
    on the compiler the ROM was built with -- measuring on a different one is how a gate ends up
    explaining a difference that is not in the source. A wine on PATH is the fallback for a
    hand-built tree; $DQIX_WINE overrides both, for diagnosis.
    """
    if os.name == "nt":
        return ""
    for cand in (os.environ.get("DQIX_WINE"), getattr(_cfg, "WINE", ""),
                 shutil.which("wine"), shutil.which("wine64")):
        if cand and os.path.exists(_from_repo(cand)):
            return _from_repo(cand)
    return ""


RUNNER = runner()


def native(exe):
    """A path that runs the Windows tool `exe` as argv[0]: the .exe itself on Windows; elsewhere a
    wrapper script under $SP/wibo/ that hands it to the decomp's wibo (configure.py's own -w loader).

    Every caller builds `[CC] + FLAGS`, so a wrapper keeps them all unchanged and none of them has to
    know the host is not Windows. A missing .exe is returned as-is, so `os.path.exists(CC)` still
    reports an unfetched toolchain, and so is a tree with no loader at all -- the exec then fails
    with the real error instead of a wrapper pointing at nothing.
    """
    if os.name == "nt" or not RUNNER or not os.path.isfile(exe):
        return exe
    wrapper = os.path.join(_kp.SP, "wibo", os.path.relpath(exe, REPO))
    body = f'#!/bin/sh\nexec "{RUNNER}" "{os.path.abspath(exe)}" "$@"\n'
    try:
        current = open(wrapper, encoding="utf-8").read()
    except OSError:
        current = None
    if current != body:
        os.makedirs(os.path.dirname(wrapper), exist_ok=True)
        tmp = f"{wrapper}.{os.getpid()}"      # published atomically: a gate may be reading it now
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(tmp, 0o755)
        os.replace(tmp, wrapper)
    return wrapper


CC = native(_from_repo(_cfg.CC))             # configure.py's own paths, so `--compiler` is honoured
AS = native(_from_repo(_cfg.AS))
AS_FLAGS = _cfg.AS_FLAGS.split()
FLAGS = (_cfg.CC_FLAGS + " " + _cfg.CC_INCLUDES + " " + _mwcc_defines(_cfg)).split()
CODEGEN_PRAGMA = re.compile(r"(?m)^[ \t]*#[ \t]*pragma[ \t]+(?!(?:define_section|section|once)\b)(\w+)")


def config_dir(mod):
    root = f"config/{REGION}/arm9"
    return root if mod == "main" else f"{root}/overlays/ov{mod}"


def pristine(mod):
    if mod == "main":
        return f"extract/{REGION}/arm9/arm9.bin"
    return f"extract/{REGION}/arm9_overlays/ov{mod}.bin"


SDK_LCF_SYMBOLS = {"SDK_IRQ_STACKSIZE": 0x400, "SDK_SYS_STACKSIZE": 0}


def lcf_symbols():
    overlays = [d for d in os.listdir(f"{REPO}/{config_dir('main')}/overlays") if re.fullmatch(r"ov\d+", d)]
    return {**SDK_LCF_SYMBOLS, **{f"OVERLAY_{int(d[2:])}_ID": int(d[2:]) for d in overlays}}


def cc_path(version):
    """The compiler for a named build (MWCC=<ver>/<sub>), or the build's own for the default."""
    if not version:
        return CC
    root = getattr(_cfg, "mwcc_root", f"{REPO}/tools/mwccarm")
    return native(_from_repo(os.path.join(str(root), version, "mwccarm.exe")))


if __name__ == "__main__":
    if "--cc" in sys.argv:
        print(CC)
    elif "--flags" in sys.argv:
        print(" ".join(FLAGS))
    else:
        print("MWCC", MWCC_VERSION)
        print("CC  ", CC)
        print("FLAGS", " ".join(FLAGS))
