import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import glob
import os
import re
import sys

SP = _kp.SP
KIT = _kp.KIT
TSV = os.environ.get("LEVERCHECK_TSV", f"{SP}/wlog/levers.tsv")


def doc_paths(kit=None):
    """worker_src files a citation is read from.

    The default used to join the two paths with os.pathsep and split them again. On Linux that
    separator is ':', so a checkout path containing one — this tree's does — became several
    nonexistent pieces and every citation looked unpromoted.
    """
    if kit is None and os.environ.get("LEVERCHECK_DOCS"):
        return [p for p in os.environ["LEVERCHECK_DOCS"].split(os.pathsep) if p]
    root = kit or KIT
    return [os.path.join(root, "worker_src", "core.md"), os.path.join(root, "worker_src", "deadends.md")]


DOCS = doc_paths()
DECLINED = os.environ.get("LEVERCHECK_DECLINED", f"{SP}/wlog/levers_declined.txt")
BOARDS = os.environ.get("LEVERCHECK_BOARDS", f"{SP}/handwork")
CFG = os.environ.get("LEVERCHECK_CFG", (_kp.REPO + "/config/usa/arm9"))
HEX8 = re.compile(r"[0-9a-fA-F]{8}")
LEVER_LINE = re.compile(r"^EVO\b.*\b(?:RULE:|MATCH\b)")


def tsv_levers():
    out = {}
    if os.path.exists(TSV):
        for line in open(TSV, encoding="utf-8", errors="ignore"):
            f = line.rstrip("\n").split("\t")
            if len(f) >= 4 and HEX8.fullmatch(f[0].strip()):
                out.setdefault(f[0].strip().lower(), []).append(f[3].strip())
    return out


def landed():
    starts = set()
    for p in glob.glob(f"{CFG}/delinks.txt") + glob.glob(f"{CFG}/overlays/*/delinks.txt"):
        text = open(p, encoding="utf-8", errors="ignore").read()
        starts.update(a.lower() for a in re.findall(
            r"(?m)^\s*\.(?:text|init) start:0x([0-9a-fA-F]{8}) end:0x[0-9a-fA-F]+\s*$", text))
    return starts


def board_levers(done):
    out = {}
    for p in sorted(glob.glob(f"{BOARDS}/*_board.md")):
        addr = os.path.basename(p)[:8].lower()
        if not HEX8.fullmatch(addr) or addr not in done:
            continue
        hit = next((ln.strip() for ln in open(p, encoding="utf-8", errors="ignore") if LEVER_LINE.match(ln)), None)
        if hit:
            out[addr] = ["evolve board: " + hit]
    return out


levers = tsv_levers()
for a, texts in board_levers(landed()).items():
    levers.setdefault(a, []).extend(texts)

doc = "".join(open(p, encoding="utf-8", errors="ignore").read().lower() for p in DOCS if os.path.exists(p))
declined = {}
if os.path.exists(DECLINED):
    for line in open(DECLINED, encoding="utf-8", errors="ignore"):
        p = line.split(None, 1)
        if p and HEX8.fullmatch(p[0]):
            declined[p[0].lower()] = p[1].strip() if len(p) > 1 else ""

missing = sorted(a for a in levers if a not in doc and a not in declined)
if "--keys" in sys.argv:
    for a in missing:
        print("%s %s" % (a, levers[a][0][:150]))
    sys.exit(1 if missing else 0)

print("%d lever address(es), %d promoted, %d declined, %d UNPROMOTED"
      % (len(levers), sum(1 for a in levers if a in doc), len(declined), len(missing)))
for a in missing:
    print("  %s  %s" % (a, levers[a][0][:100]))
    if "--verbose" in sys.argv:
        for t in levers[a][1:]:
            print("             %s" % t[:100])
if missing:
    print("\nPromote each into worker_src/core.md CITING ITS ADDRESS, or add it to")
    print("wlog/levers_declined.txt as `<addr> <why it is not worth a recipe slot>`.")
sys.exit(1 if missing else 0)
