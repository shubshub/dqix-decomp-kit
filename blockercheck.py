"""Rank measured blockers by class and hold the dispatcher while one class keeps recurring uncracked.

    python blockercheck.py [--verbose]

A class counts as ADDRESSED when worker_src/core.md cites the ADDRESS of one of its members: crack
one, write the recipe, and every later member of that class is free. Only rows recorded AFTER that
citation count against the class again. A class not worth a recipe goes in wlog/blockers_declined.txt
as `<CLASS> <reason>`. Exit 1 while any class is over threshold.
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import os
import re
import sys

SP = _kp.SP
KIT = _kp.KIT
TSV = f"{SP}/wlog/blockers.tsv"
DOC = f"{KIT}/worker_src/core.md"
DECLINED = f"{SP}/wlog/blockers_declined.txt"
NEVER_HOLD = {"MATCH", "ALREADY-COMMITTED", "NO-ARTIFACT", "UNKNOWN"}


def threshold():
    try:
        return int(open(f"{SP}/BLOCKER_THRESH", encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return int(os.environ.get("BLOCKER_THRESH", "8"))


def crack_threshold():
    try:
        return int(open(f"{SP}/CRACK_THRESH", encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return int(os.environ.get("CRACK_THRESH", "4"))


def rows():
    out = []
    try:
        for line in open(TSV, encoding="utf-8", errors="ignore"):
            f = line.rstrip("\n").split("\t")
            if len(f) >= 6 and f[0].isdigit() and re.fullmatch(r"[0-9a-fA-F]{8}", f[2].strip()):
                out.append({"epoch": int(f[0]), "mod": f[1], "addr": f[2].lower(),
                            "size": int(f[3]) if f[3].isdigit() else 0, "cls": f[4].strip(),
                            "metric": f[5].strip(), "detail": f[6] if len(f) > 6 else ""})
    except IOError:
        pass
    return out


_DELINKS = {}


def landed(r):
    import delinked
    if r["mod"] not in _DELINKS:
        _DELINKS[r["mod"]] = [open(p, encoding="utf-8", errors="ignore").read() for p in delinked.files(r["mod"])]
    return delinked.covers(int(r["addr"], 16), _DELINKS[r["mod"]])


def unfinished(r):
    # A size class only names an IDIOM while the gap is small. `UNDERGEN 8360` on a 0x23a0 function
    # is a worker that did not finish writing it, and no recipe addresses that -- counting those as
    # uncracked members holds the dispatcher for work that is merely incomplete.
    if r["cls"] == "IMPLAUSIBLE":
        return True
    if r["cls"] not in ("UNDERGEN", "OVERGEN"):
        return False
    try:
        gap = int(r["metric"])
    except ValueError:
        return False
    return gap > max(64, r["size"] // 20)


def main():
    data = [r for r in rows() if not unfinished(r)]
    if not data:
        print("no blockers.tsv yet")
        return 0
    # deadends.md counts too: recipe_select injects the row for the address being worked, so a class
    # cracked and written up there has reached the session that needs it.
    doc = ""
    for p in (DOC, f"{KIT}/worker_src/deadends.md"):
        if os.path.exists(p):
            doc += open(p, encoding="utf-8", errors="ignore").read().lower()
    declined = set()
    if os.path.exists(DECLINED):
        for line in open(DECLINED, encoding="utf-8", errors="ignore"):
            p = line.split(None, 1)
            if p:
                declined.add(p[0].strip().upper())

    classes = {}
    for r in data:
        classes.setdefault(r["cls"], []).append(r)

    thresh = threshold()
    held = []
    crack = []
    table = []
    for cls, rs in classes.items():
        cited = [r["epoch"] for r in rs if r["addr"] in doc]
        anchor = max(cited) if cited else 0
        # A WALL IS THE SAME FUNCTION COMING BACK, not eight different ones failing once. Counting
        # distinct addresses held the dispatcher every twenty minutes on UNDERGEN, whose members
        # are each short for their own reason -- that is new work, and the hold bought nothing.
        seen = {}
        for r in rs:
            if r["epoch"] > anchor and r["addr"] not in doc and not landed(r):
                seen.setdefault(r["addr"], []).append(r)
        pending = {a: v[-1] for a, v in seen.items() if len(v) > 1}
        # DISTINCT FUNCTIONS ARE THE CRACKING SIGNAL, even though repeats are the HOLD signal. Eight
        # different functions dying once each on one class is what an uncracked idiom looks like --
        # the class that most needs a recipe is precisely the one no single address repeats on --
        # and counting only repeats made SCHED, with 8 members and two hand-cracks never written up
        # as a rule, read as 0 pending for as long as the ledger has existed.
        openn = {a: v[-1] for a, v in seen.items()}
        table.append((len(pending), sum(r["size"] for r in openn.values()), cls,
                      len(cited), sorted(pending)[:4], len(openn), sorted(openn)[:4]))
        if (len(pending) >= thresh and cls not in declined and cls not in NEVER_HOLD):
            held.append((cls, len(pending)))
        if (len(openn) >= crack_threshold() and cls not in declined and cls not in NEVER_HOLD):
            crack.append((len(openn), sum(r["size"] for r in openn.values()), cls,
                          sorted(openn)[:4]))

    table.sort(key=lambda t: (-t[5], -t[1]))
    print("%-16s %7s %6s %10s %8s  %s" % ("class", "pending", "open", "bytes", "cracked", "sample"))
    for n, nbytes, cls, ncited, _sample, nopen, osample in table:
        print("%-16s %7d %6d %10d %8d  %s" % (cls, n, nopen, nbytes, ncited, " ".join(osample)))
    print("threshold %d pending per class, %d open per class to flag for cracking"
          % (thresh, crack_threshold()))

    crack.sort(key=lambda t: (-t[1], -t[0]))
    for n, nbytes, cls, sample in crack[:3]:
        print("CRACK: %s blocks %d function(s), %d bytes -- %s"
              % (cls, n, nbytes, " ".join(sample)))

    if held:
        print()
        for cls, n in held:
            print("HOLD: %s has %d uncracked function(s)." % (cls, n))
            if "--verbose" in sys.argv:
                for r in sorted(classes[cls], key=lambda r: -r["epoch"])[:6]:
                    print("    %s %s  %s  %s" % (r["mod"], r["addr"], r["metric"], r["detail"][:90]))
        print("Crack ONE member and cite its address in worker_src/core.md, or write the CLASS off in")
        print("wlog/blockers_declined.txt as `<CLASS> <why no recipe reaches it>`.")
    return 1 if held else 0


if __name__ == "__main__":
    sys.exit(main())
