"""Name the candidates a red build log blames, so a red gate costs one re-gate instead of a bisection.

    python culprits.py <build-log>            print `<mod> <addr> <path> <reason>` per culprit
    python culprits.py <build-log> --cull     also move each culprit's staged copy to hold_<mod>

Exit 0 = culprits named, 1 = none named, 2 = a tool crashed (retry the same set, blame nobody).

Candidates are the untracked sources under the decomp's src/, keyed by their `// USA:` tag.
"""
import glob
import os
import re
import shutil
import subprocess
import sys

import kitpaths as _kp

REPO = _kp.REPO
SP = _kp.SP
TAG = re.compile(r"// USA: func_(?:ov(\d+)_)?([0-9a-fA-F]{8})")
SOURCE_ERROR = re.compile(r"^(\S+?\.(?:cpp|c|s)):\d+:", re.M)
OBJECT = re.compile(r"([\w$]+)\.o\b")
CONFIG_LINE = re.compile(r"(config[\\/]\S+?\.txt):(\d+):")
DRIFT = re.compile(r"expected to be at 0x([0-9a-f]+) but is at 0x([0-9a-f]+)")
HEX8 = re.compile(r"(?:addr:0x|start:0x|_)0*([0-9a-fA-F]{7,8})\b")
# The linker prefixes each of its own messages with its name. mwldarm keeps the .exe on every
# platform (Linux runs it under the build's runner), but matching the stem with the suffix optional
# costs nothing and stops a log whose prefix is spelled differently from being read as "no linker
# output at all" -- which reported "no culprits" for a build that had named dozens.
MWLD_PREFIX = re.compile(r"mwldarm(?:\.exe)?:\s*")


CRASH = re.compile(r"\[code=(-\d+|\d{4,})\]|out of memory|not enough memory|Access violation", re.I)


def transient(log):
    return bool(CRASH.search(log))


def tag_of(text):
    m = TAG.search(text)
    if not m:
        return None
    return ("main" if m.group(1) is None else m.group(1).zfill(3)), m.group(2).lower()


def candidates():
    out = subprocess.run(["git", "-C", REPO, "ls-files", "--others", "--exclude-standard", "-z", "--", "src"],
                         capture_output=True)
    if out.returncode != 0:
        return {}
    found = {}
    for raw in out.stdout.split(b"\0"):
        rel = raw.decode("utf-8", "surrogateescape")
        if not rel.endswith((".cpp", ".c", ".s")):
            continue
        try:
            tagged = tag_of(open(os.path.join(REPO, rel), encoding="utf-8", errors="ignore").read())
        except OSError:
            continue
        if tagged:
            found[rel] = tagged
    return found


def name(log, cands=None):
    cands = candidates() if cands is None else cands
    by_stem = {os.path.splitext(os.path.basename(p))[0].lower(): (p, t) for p, t in cands.items()}
    by_addr = {t[1]: (p, t) for p, t in cands.items()}
    blamed = {}

    def blame(hit, reason):
        if hit and hit[0] not in blamed:
            blamed[hit[0]] = (hit[1], reason)

    errors = "\n".join(l for l in log.splitlines() if "warning:" not in l)
    for path in SOURCE_ERROR.findall(errors):
        blame(by_stem.get(os.path.splitext(os.path.basename(path.replace("\\", "/")))[0].lower()), "compile")
    linker = []
    for line in errors.splitlines():
        # An ECHOED compiler command line is not linker output. Recognise it by shape -- quoted, or
        # opening with the path of a program -- rather than by one host's spelling of that path.
        stripped = line.lstrip()
        if " -o " in line and (stripped[:1] == '"' or stripped[:2] in ("./", "C:", "c:", "Z:", "z:")):
            continue
        for stem in OBJECT.findall(line):
            blame(by_stem.get(stem.lower()), "link")
        # The linker is mwldarm.exe on Windows and under the Linux runner too, but never trust a
        # hardcoded suffix to find its own output: match the stem with the extension optional.
        m = MWLD_PREFIX.search(line)
        if m:
            linker.append(line[m.end():].strip())
    for symbol in re.findall(r'"([^"]+)"', " ".join(linker)):
        for addr in re.findall(r"([0-9a-fA-F]{8})(?![0-9a-fA-F])", symbol):
            blame(by_addr.get(addr.lower()), "symbol")
    for path, num in CONFIG_LINE.findall(errors):
        try:
            text = open(os.path.join(REPO, path.replace("\\", "/")), encoding="utf-8", errors="ignore").read()
            bad = text.splitlines()[int(num) - 1]
        except (OSError, IndexError):
            continue
        for addr in HEX8.findall(bad):
            blame(by_addr.get(addr.lower().zfill(8)), "config")
        for stem in re.findall(r"([\w$]+)\.(?:cpp|c|s)\b", bad):
            blame(by_stem.get(stem.lower()), "config")
    for expected, actual in DRIFT.findall(errors):
        if int(actual, 16) < 0x02000000:
            blame(by_addr.get(expected.lower().zfill(8)), "unplaced")
    return [(t[0], t[1], p, why) for p, (t, why) in sorted(blamed.items())]


def cull(named):
    moved = 0
    for mod, addr, _path, _why in named:
        label = "main" if mod == "main" else f"ov{mod}"
        hold = f"{SP}/hold_{label}"
        for staged in glob.glob(f"{SP}/staging/{label}/*.cpp"):
            try:
                tagged = tag_of(open(staged, encoding="utf-8", errors="ignore").read())
            except OSError:
                continue
            if tagged == (mod, addr):
                os.makedirs(hold, exist_ok=True)
                shutil.move(staged, f"{hold}/{os.path.basename(staged)}")
                moved += 1
    return moved


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    log = open(sys.argv[1], encoding="utf-8", errors="ignore").read()
    if transient(log):
        print("transient: a tool crashed or ran out of memory; nothing blamed")
        sys.exit(2)
    named = name(log)
    for mod, addr, path, why in named:
        print(f"{mod} {addr} {path} {why}")
    if "--cull" in sys.argv[2:]:
        print(f"culled {cull(named)} staged source(s) to hold_<mod>", file=sys.stderr)
    sys.exit(0 if named else 1)
