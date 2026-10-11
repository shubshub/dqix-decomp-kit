"""Record what actually blocked a function, measured by the gate rather than described by the worker.

    python blocker.py <main|NNN> <addr> <file.cpp|-> <size> [session.json]
    python blocker.py <main|NNN> <addr> <file.cpp> --print

Appends one row to wlog/blockers.tsv: epoch, module, addr, size, class, metric, detail, evidence.
A residue on a file plausible.py flags is recorded as IMPLAUSIBLE and exits 1: not parked.
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import os
import subprocess
import sys
import time

import plausible
import residue

SP = _kp.SP
KIT = _kp.KIT
TSV = f"{SP}/wlog/blockers.tsv"
NOT_RESIDUE = {"MATCH", "UNKNOWN", "ALREADY-COMMITTED"}


def gate_residue(mod, addr, src):
    env = dict(os.environ)
    env["WGATE_CALLER"] = "blocker"
    env.pop("WGATE_SESSION", None)
    proc = subprocess.run([sys.executable, f"{KIT}/wgate.py", mod, addr, src],
                          capture_output=True, text=True, env=env)
    return residue.parse_verdict((proc.stdout or "") + (proc.stderr or ""))


def find_artifact(mod, addr):
    """The worker's file may be gone by the time this runs, and NO-ARTIFACT costs the whole miss.

    A wave deletes untracked .cpp from the module's src dir -- preserving each into hold_<mod> first
    -- so a session running while its own module integrates loses the file the caller names here. The
    attempt still exists; it is just somewhere else. Best copy first: clsbest is the sweep's ranked
    pick, then this session's own attempts, then whatever the wave preserved.
    """
    import glob as _glob
    suf = "main" if mod == "main" else "ov" + mod
    addr = addr.lower()
    for pat in (f"{SP}/clsbest/{addr}.cpp",
                f"{SP}/attempts/*{addr}*.cpp",
                f"{SP}/hold_{suf}/{addr}.cpp",
                f"{SP}/hold_{suf}/{addr}_*.cpp",
                f"{SP}/gated/{suf}/{addr}.cpp",
                f"{SP}/quarantine/*{addr}*.cpp"):
        hits = sorted(_glob.glob(pat))
        if hits:
            return hits[0]
    return None


def measure(mod, addr, src):
    if src == "-" or not os.path.isfile(src):
        src = find_artifact(mod, addr) or src
    if src == "-" or not os.path.isfile(src):
        return "NO-ARTIFACT", -1, "no candidate source on disk", ""
    src = os.path.abspath(src)
    cls, metric, detail = gate_residue(mod, addr, src)
    with open(src, encoding="utf-8", errors="ignore") as fh:
        invented = plausible.findings(fh.read())
    if invented and cls not in NOT_RESIDUE:
        detail = "%s %s; %s" % (cls, detail, "; ".join("%s %s" % (k, w) for _l, k, w in invented))
        cls = "IMPLAUSIBLE"
    return cls, metric, detail, src.replace(chr(92), "/")


def record(mod, addr, size, cls, metric, detail, evidence):
    os.makedirs(f"{SP}/wlog", exist_ok=True)
    row = "\t".join(str(x) for x in
                    (int(time.time()), mod, addr.lower(), size, cls, metric,
                     detail.replace("\t", " ")[:200], evidence))
    with open(TSV, "a", encoding="utf-8") as fh:
        fh.write(row + "\n")
    return row


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    mod, addr, src = sys.argv[1], sys.argv[2], sys.argv[3]
    printing = "--print" in sys.argv
    size = 0
    if len(sys.argv) > 4 and sys.argv[4].isdigit():
        size = int(sys.argv[4])

    cls, metric, detail, evidence = measure(mod, addr, src)
    if printing:
        print("%s %s %s" % (cls, metric, detail))
        return 0
    print(record(mod, addr, size, cls, metric, detail, evidence))
    if cls == "IMPLAUSIBLE":
        print("NOT PARKED: strip what the developers could not have written, go back to the last natural "
              "form and try another lever from there (worker_src/core.md: A RESIDUE THAT WILL NOT MOVE)",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
