"""Gate every parked artifact under alternative COMPILER FLAG SETS, not just the project default.

`tools/cc_overrides.txt` can swap the mwccarm BUILD per file, but the flag set has always been fixed,
so every "no C form reaches this" verdict in this project was measured on exactly one flag set. It is
not one: `main:020227dc` gates REGPERM 14 on the default and UNDERGEN 4 under `-O3` -- a different
residue class, four bytes from matching.

    python flagsweep.py [--only <addr> ...] [--sets "-O3|-O4|..."]

Reports, per address, the best verdict across sets and which set produced it. Writes nothing.
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import glob
import os
import re
import subprocess
import sys

import residue

SP = _kp.SP
KIT = _kp.KIT
REPO = _kp.REPO

SETS = ["", "-O3", "-O4", "-opt speed", "-inline on", "-inline all", "-O3 -inline on"]

owner = {}
for p in [f"{REPO}/config/usa/arm9/symbols.txt"] + \
        sorted(glob.glob(f"{REPO}/config/usa/arm9/overlays/*/symbols.txt")):
    mod = "main" if "overlays" not in p else re.search(r"ov(\d+)", p).group(1)
    for m in re.finditer(r"kind:function\(\w+,size=0x[0-9a-fA-F]+\) addr:0x([0-9a-fA-F]{8})",
                         open(p, encoding="utf-8", errors="ignore").read()):
        owner.setdefault(m.group(1).lower(), mod)


def rank(v):
    """Lower is better; mirrors how colorsweep scores -- right length beats wrong length."""
    cls, n, _ = residue.parse_verdict(v)
    if cls == "MATCH":
        return (0, 0)
    if cls == "UNKNOWN":
        return (9, 1 << 30)
    tier = {"REGPERM": 1, "SCHED": 1, "OPERAND": 1, "SHAPE": 2, "LOOP-SHAPE": 2,
            "UNDERGEN": 3, "OVERGEN": 3}.get(cls, 8)
    return (tier, abs(n))


def verdict(mod, addr, path, flags):
    env = dict(os.environ)
    env["WGATE_FLAGS"] = flags
    env.pop("WGATE_SESSION", None)
    r = subprocess.run([sys.executable, f"{KIT}/wgate.py", mod, addr, path],
                       capture_output=True, text=True, cwd=REPO, env=env)
    return residue.headline((r.stdout or "") + (r.stderr or ""))


def main():
    only = set()
    if "--only" in sys.argv:
        only = {a.lower() for a in sys.argv[sys.argv.index("--only") + 1:] if re.fullmatch(r"[0-9a-fA-F]{8}", a)}
    sets = SETS
    if "--sets" in sys.argv:
        sets = [s.strip() for s in sys.argv[sys.argv.index("--sets") + 1].split("|")]
    files = sorted(glob.glob(f"{SP}/clsbest/*.cpp"))
    wins = 0
    for f in files:
        addr = os.path.basename(f)[:8].lower()
        if only and addr not in only:
            continue
        mod = owner.get(addr)
        if not mod:
            continue
        base = verdict(mod, addr, f, "")
        best, bflags = base, "default"
        for flags in sets[1:]:
            v = verdict(mod, addr, f, flags)
            if rank(v) < rank(best):
                best, bflags = v, flags
        if bflags != "default":
            wins += 1
            print("%-9s %-5s %-26s -> %-26s with %s" % (addr, mod, base, best, bflags), flush=True)
    print("%d address(es) improve under a non-default flag set" % wins)


if __name__ == "__main__":
    main()
