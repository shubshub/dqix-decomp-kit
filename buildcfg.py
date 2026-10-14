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
import sys

REPO = _kp.REPO
_TOOLS = os.path.join(REPO, "tools")
# usa, jpn, or eur. Unset stays usa, which is every current config/ and extract/ path.
REGION = os.environ.get("DQIX_REGION", "usa")


def _load():
    cwd, argv = os.getcwd(), sys.argv[:]
    os.chdir(REPO)
    sys.argv = ["configure.py", REGION]
    if os.environ.get("DQIX_WINE"):
        sys.argv += ["-w", os.environ["DQIX_WINE"]]
    if os.environ.get("DQIX_PREINSTALLED_COMPILER"):
        sys.argv += ["--compiler", os.environ["DQIX_PREINSTALLED_COMPILER"]]
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
COMPILER_ROOT = os.path.normpath(os.path.join(REPO, str(_cfg.mwcc_root)))
CC = f"{COMPILER_ROOT}/{MWCC_VERSION}/mwccarm.exe"
AS = f"{COMPILER_ROOT}/{MWCC_VERSION}/mwasmarm.exe"
AS_FLAGS = getattr(_cfg, "AS_FLAGS", "-proc arm5TE").split()
FLAGS = (_cfg.CC_FLAGS + " " + _cfg.CC_INCLUDES + " " + _mwcc_defines(_cfg)).split()
from pragmas import CODEGEN_PRAGMA


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
    out = {**SDK_LCF_SYMBOLS, **{f"OVERLAY_{int(d[2:])}_ID": int(d[2:]) for d in overlays}}
    head = open(f"{REPO}/{config_dir('main')}/delinks.txt", encoding="utf-8").read().split("\n\n")[0]
    for name, lo, hi in re.findall(r"(?m)^\s*\.(\w+)\s+start:0x([0-9a-fA-F]+)\s+end:0x([0-9a-fA-F]+)", head):
        out[f"ARM9_{name.upper()}_START"] = int(lo, 16)
        out[f"ARM9_{name.upper()}_END"] = int(hi, 16)
    return out


def cc_path(version):
    return f"{COMPILER_ROOT}/{version}/mwccarm.exe" if version else CC


_POSTCOMPILE = []


def postcompile(obj):
    if not _POSTCOMPILE:
        path = os.path.join(_TOOLS, "mwcc.py")
        if os.path.exists(path):
            import importlib.util
            spec = importlib.util.spec_from_file_location("_decomp_mwcc", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _POSTCOMPILE.append(module.strip)
        else:
            _POSTCOMPILE.append(lambda _obj: [])
    return _POSTCOMPILE[0](obj)


def sections(elf, name):
    return [(i, s) for i, s in enumerate(elf.iter_sections()) if s.name == name and s["sh_size"]]


def relocations(elf, index):
    return [s for s in elf.iter_sections()
            if s.header["sh_type"] in ("SHT_REL", "SHT_RELA") and s.header["sh_info"] == index]


def tool_command(executable):
    runner = _cfg.WINE
    if runner and not os.path.isabs(runner) and ("/" in runner or "\\" in runner):
        runner = os.path.normpath(os.path.join(REPO, runner))
    return ([runner] if runner else []) + [executable]


if __name__ == "__main__":
    if "--cc" in sys.argv:
        print(CC)
    elif "--flags" in sys.argv:
        print(" ".join(FLAGS))
    elif "--region" in sys.argv:
        print(REGION)
    elif "--compiler-root" in sys.argv:
        print(COMPILER_ROOT.replace("\\", "/"))
    elif "--config-dir" in sys.argv:
        print(config_dir(sys.argv[sys.argv.index("--config-dir") + 1]))
    else:
        print("MWCC", MWCC_VERSION)
        print("CC  ", CC)
        print("FLAGS", " ".join(FLAGS))
