"""Regenerate STATE.md — the single machine-written answer to "where are we?".

WHY THIS EXISTS. Every session was hand-editing the `dqix-plan` and `dqix-continue` skills to keep a
"State as of <date>" block current: coverage, knobs, what landed, what is broken. That defeats the
point of a skill (a stable procedure) and it goes stale the moment a wave commits. The skills now
carry PROCEDURE ONLY and point here; this file carries the facts and nothing hand-written.

Costs no model tokens. `pull_all.sh` refreshes it every loop, and it is safe to run by hand:

    python progress.py          # rewrite STATE.md
    python progress.py --print  # and echo it

Hand-written knowledge -- cracked idioms, open residues, what has been ruled out -- stays in
OPEN_WORK.md, which this file links to rather than duplicates.
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import datetime
import glob
import json
import os
import re
import subprocess
import sys

SP = _kp.SP
KIT = _kp.KIT
REPO = _kp.REPO
sys.path.insert(0, KIT)
import claim                                                    # noqa: E402

BANDS = [("small  <=64", 0, 64), ("medium 65-256", 65, 256), ("l- 257-512", 257, 512),
         ("l  513-1024", 513, 1024), ("l+ 1025-2048", 1025, 2048), ("xl 2049-4096", 2049, 4096),
         ("massive 4097+", 4097, 1 << 30)]


def sh(*args, **kw):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=120, **kw).stdout.strip()
    except Exception:
        return ""


def knob(name, default=""):
    try:
        return open(os.path.join(SP, name), encoding="utf-8").read().strip() or default
    except OSError:
        return default


def coverage():
    """Functions AND bytes. They diverge hard -- 80% of functions is 38% of the code -- and steering
    by the function count quietly favours small work."""
    tot_b = tot_n = done_b = done_n = 0
    left = {b[0]: [0, 0] for b in BANDS}
    skip = claim.skiplist()
    for mod in claim.all_modules():
        try:
            cfg = claim.cfg_for(mod)
            sym = open(cfg + "/symbols.txt", encoding="utf-8", errors="ignore").read()
            dl = open(cfg + "/delinks.txt", encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        ranges = [(int(a, 16), int(b, 16)) for a, b in re.findall(
            r"(?m)^\s*\.(?:text|init) start:0x([0-9a-fA-F]+) end:0x([0-9a-fA-F]+)\s*$", dl)]
        for m in re.finditer(r"(?m)^(\S+)\s+kind:function\((?:arm|thumb),size=0x([0-9a-fA-F]+)\)"
                             r"\s+addr:0x([0-9a-fA-F]+)", sym):
            size, addr = int(m.group(2), 16), int(m.group(3), 16)
            if not size or m.group(3).lower() in skip:
                continue
            tot_b += size
            tot_n += 1
            if any(s <= addr < e for s, e in ranges):
                done_b += size
                done_n += 1
                continue
            for name, lo, hi in BANDS:
                if lo <= size <= hi:
                    left[name][0] += 1
                    left[name][1] += size
                    break
    return tot_b, tot_n, done_b, done_n, left


def verdicts(limit=12):
    """The last N finished functions, newest first, from the per-address result JSON."""
    rows = []
    for p in glob.glob(SP + "/wlog/pull_*_s*_*.json"):
        try:
            st = os.path.getmtime(p)
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        addr = os.path.basename(p).rsplit("_", 1)[-1][:-5]
        res = str(d.get("result", "")).strip().replace(chr(10), " ")
        rows.append((st, addr, float(d.get("total_cost_usd") or 0), res[:150]))
    rows.sort(reverse=True)
    return rows[:limit]


def _proc_table():
    """`pid|ppid|cmdline` for every live process, from /proc."""
    rows = []
    for entry in glob.glob("/proc/[0-9]*"):
        try:
            pid = os.path.basename(entry)
            with open(f"{entry}/stat", encoding="utf-8", errors="replace") as fh:
                ppid = fh.read().rsplit(") ", 1)[1].split()[1]
            with open(f"{entry}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except (OSError, IndexError):
            continue                          # the process exited between the glob and the read
        rows.append((pid, ppid, cmd))
    return "\n".join("|".join(r) for r in rows)


def fleet():
    """Count live drivers and workers BY COMMAND LINE.

    `ps -W` under Git Bash lists Windows processes without their arguments, so matching it against
    `pull_all.sh` finds nothing and this reported `drivers 0 workers 0 -- the run died` while a
    worker was mid-function. A false death notice is worse than no notice: it invites a relaunch on
    a live fleet, which is the one state this project must never reach. So ask for the command
    lines themselves: Get-CimInstance on Windows, /proc on Linux, which is the same three fields.
    """
    out = sh("powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | ForEach-Object "
             "{ \"$($_.ProcessId)|$($_.ParentProcessId)|$($_.CommandLine)\" }") \
        if os.name == "nt" else _proc_table()
    if not out:
        return -1, -1                            # unknown, not zero -- never claim a false death

    # A PROCESS THAT MENTIONS THE SCRIPT IS NOT A PROCESS RUNNING IT, TWICE OVER:
    #   * every harness wrapper carries it inside `bash -c "source ... && eval '...'"`, and
    #   * a subshell FORKED by the driver (the detached repair sweep) inherits the driver's whole
    #     command line under MSYS, so it looks like a second driver.
    # Counting naively said FOUR drivers, then TWO, and acting on the two got a running repair sweep
    # killed as an imposter. Count only processes whose PARENT is not itself a match.
    rows = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3 and parts[0].strip().isdigit():
            rows.append((parts[0].strip(), parts[1].strip(), parts[2]))

    def roots(name):
        hits = [r for r in rows if name in r[2] and " -c " not in r[2] and "ForEach-Object" not in r[2]]
        ids = {r[0] for r in hits}
        return len([r for r in hits if r[1] not in ids])

    return roots("pull_all.sh"), roots("pull_worker.sh")


def main():
    tot_b, tot_n, done_b, done_n, left = coverage()
    rem_b = sum(v[1] for v in left.values()) or 1
    rem_n = sum(v[0] for v in left.values()) or 1
    drivers, workers = fleet()
    flags = [f for f in ("FLEET_STOPPED", "STOP_PULL", "USAGE_LIMIT_STOP", "wave.lock")
             if os.path.exists(os.path.join(SP, f))]
    staged = sorted(glob.glob(SP + "/staging/*/*.cpp"))
    self_out = sh(sys.executable, KIT + "/selfcheck.py", cwd=SP).splitlines()
    regress_marker = sh(sys.executable, KIT + "/regress.py", cwd=SP).splitlines()

    L = []
    a = L.append
    a("# DQIX state — GENERATED, do not hand-edit")
    a("")
    a("`python $KIT/progress.py` rewrites this file; `pull_all.sh` refreshes it every loop. The skills")
    a("(`dqix-plan`, `dqix-continue`) point here instead of carrying a state block that goes stale.")
    a("Hand-written knowledge — cracked idioms, open residues, what is ruled out — is in OPEN_WORK.md.")
    a("")
    a("Generated %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    a("")
    a("## Fleet")
    a("")
    a("    drivers %s   workers %s   flags: %s" % (
        drivers, workers, ", ".join(flags) if flags else "none"))
    a("    PULL_SLOTS=%s  PULL_BAND=%s  MODEL_LARGE=%s  CAP_LARGE=%s" % (
        knob("PULL_SLOTS", "?"), knob("PULL_BAND", "(round-robin)") or "(round-robin)",
        knob("MODEL_LARGE", "(sonnet)") or "(sonnet)", knob("CAP_LARGE", "8")))
    # One flag per size band. Which bands are OPEN decides what every claim serves, so it belongs in
    # the state block rather than in seven files nobody thinks to cat.
    a("    bands open: " + "  ".join(
        "%s=%s" % (n.replace("PULL_", "").lower(), knob(n, d))
        for n, d in (("PULL_SMALL", "1"), ("PULL_MED", "1"), ("PULL_LMINUS", "1"),
                     ("PULL_L", "1"), ("PULL_LPLUS", "1"), ("PULL_XL", "0"),
                     ("PULL_MASSIVE", "0"))))
    if drivers == -1:
        a("")
        a("    (could not read the process list -- counts unknown, NOT zero)")
    if drivers == 0 and workers == 0 and not flags:
        a("")
        a("    *** NOTHING IS RUNNING AND NO STOP FLAG IS SET -- the run died. Read the tail of")
        a("        wlog/pull_all.log before relaunching. ***")
    a("")
    a("## Coverage — functions flatter, bytes are the truth")
    a("")
    a("    BYTES  %8d / %8d = %5.2f%%" % (done_b, tot_b, 100.0 * done_b / (tot_b or 1)))
    a("    FUNCS  %8d / %8d = %5.2f%%" % (done_n, tot_n, 100.0 * done_n / (tot_n or 1)))
    a("")
    a("    %-15s %7s %7s %9s %8s" % ("remaining", "funcs", "func%", "bytes", "byte%"))
    for name, _lo, _hi in BANDS:
        n, b = left[name]
        a("    %-15s %7d %6.1f%% %9d %7.1f%%" % (name, n, 100.0 * n / rem_n, b, 100.0 * b / rem_b))
    a("    %-15s %7d %7s %9d" % ("TOTAL LEFT", rem_n, "", rem_b))
    a("")
    a("## Repo")
    a("")
    for ln in sh("git", "-C", REPO, "log", "--oneline", "-4").splitlines():
        a("    " + ln)
    dirty = sh("git", "-C", REPO, "status", "--porcelain")
    a("")
    a("    tree: %s" % ("clean" if not dirty else "%d modified/untracked" % len(dirty.splitlines())))
    a("    staged and NOT yet committed: %s" % (
        ", ".join(os.path.basename(f) for f in staged) if staged else "none"))
    a("")
    a("## Health")
    a("")
    a("    selfcheck: %s" % (self_out[-1] if self_out else "did not run"))
    a("    regress:   %s" % (regress_marker[-1] if regress_marker else "did not run"))
    a("")
    a("## Last finished functions")
    a("")
    a("    %-10s %8s  %s" % ("addr", "cost", "verdict"))
    for _st, addr, cost, res in verdicts():
        a("    %-10s %8.2f  %s" % (addr, cost, res[:120]))
    a("")

    out = chr(10).join(L) + chr(10)
    with open(SP + "/STATE.md", "w", encoding="utf-8", newline=chr(10)) as fh:
        fh.write(out)
    if "--print" in sys.argv:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
