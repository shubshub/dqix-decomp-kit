#!/usr/bin/env python3
"""The process table, filtered, on Windows and on Linux. The one place the fleet scripts ask
"what is running".

    python procq.py [filters] [--count | --list | --kill | --cpu]

    --match RE       command line matches RE
    --worker RE      or: the image is claude / claude.exe and its command line matches RE
    --notmatch RE    command line does not match RE (repeatable)
    --older MIN      started more than MIN minutes ago
    --orphans        its parent process is gone
    --watchers RE    fullstop's watcher tier: RE over the command line (minus --exclw), or a
                     tail/grep/bash/sh whose ancestry runs `--kids` and touches `--ours`

    --count   how many (default)    --list  pid@@@minutes@@@command, 110 chars
    --kill    kill them, print how many    --cpu  their summed CPU time

The querying process and its ancestors are never matched: their command lines carry the pattern
text, so every ad-hoc query used to count itself (psq.sh has the history).
"""
import argparse
import os
import re
import signal
import subprocess
import sys


def snapshot():
    """[{pid, ppid, name, minutes, cpu, cmd}] for every process this user can see."""
    if os.name == "nt":
        return _snapshot_windows()
    return _snapshot_linux()


def _snapshot_windows():
    ps = ("Get-CimInstance Win32_Process | ForEach-Object { "
          "$m = 0; if ($_.CreationDate) { $m = [int]((Get-Date) - $_.CreationDate).TotalMinutes }; "
          "'{0}|{1}|{2}|{3}|{4}|{5}' -f $_.ProcessId, $_.ParentProcessId, $_.Name, $m, "
          "$_.UserModeTime, $_.CommandLine }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, errors="replace").stdout
    rows = []
    for line in out.splitlines():
        f = line.rstrip("\r").split("|", 5)
        if len(f) == 6 and f[0].isdigit():
            rows.append({"pid": int(f[0]), "ppid": int(f[1] or 0), "name": f[2],
                         "minutes": int(f[3] or 0), "cpu": int(f[4] or 0), "cmd": f[5]})
    return rows


def _snapshot_linux():
    hz = os.sysconf("SC_CLK_TCK")
    with open("/proc/uptime") as fh:
        uptime = float(fh.read().split()[0])
    rows = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as fh:
                stat = fh.read()
            with open(f"/proc/{d}/cmdline", "rb") as fh:
                cmd = fh.read().rstrip(b"\0").replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue                                  # exited while we looked
        name, rest = stat[stat.index("(") + 1:stat.rindex(")")], stat[stat.rindex(")") + 2:].split()
        # rest[0] is field 3 (state): ppid is field 4, utime 14, starttime 22
        rows.append({"pid": int(d), "ppid": int(rest[1]), "name": name,
                     "minutes": int((uptime - int(rest[19]) / hz) / 60),
                     "cpu": int(rest[11]), "cmd": cmd})
    return rows


def _self_chain(byid):
    chain, c = set(), os.getpid()
    for _ in range(12):
        if c not in byid:
            break
        chain.add(c)
        c = byid[c]["ppid"]
    chain.add(os.getpid())
    return chain


def _is_watcher(x, byid, a):
    if re.search(a.watchers, x["cmd"]) and not re.search(a.exclw, x["cmd"]):
        return True
    if not re.fullmatch(r"(tail|grep|bash|sh)(\.exe)?", x["name"], re.I):
        return False
    p, saw_tail, saw_ours = x, False, False
    for _ in range(8):
        saw_tail = saw_tail or bool(re.search(a.kids, p["cmd"]))
        saw_ours = saw_ours or bool(re.search(a.ours, p["cmd"]))
        if saw_tail and saw_ours:
            return True
        if p["ppid"] not in byid:
            return saw_tail
        p = byid[p["ppid"]]
    return False


def select(rows, a):
    byid = {r["pid"]: r for r in rows}
    skip = _self_chain(byid)
    hits = []
    for r in rows:
        if r["pid"] in skip:
            continue
        if a.watchers is not None:
            if _is_watcher(r, byid, a):
                hits.append(r)
            continue
        if a.match or a.worker:
            hit = a.match and re.search(a.match, r["cmd"])
            hit = hit or (a.worker and re.fullmatch(r"claude(\.exe)?", r["name"], re.I)
                          and re.search(a.worker, r["cmd"]))
            if not hit:
                continue
        if any(re.search(n, r["cmd"]) for n in a.notmatch):
            continue
        if a.older is not None and r["minutes"] <= a.older:
            continue
        if a.orphans and r["ppid"] in byid:
            continue
        hits.append(r)
    return hits


def kill(pid):
    try:
        os.kill(pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)   # nt: TerminateProcess
        return True
    except OSError:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--match")
    ap.add_argument("--notmatch", action="append", default=[])
    ap.add_argument("--worker")
    ap.add_argument("--older", type=int)
    ap.add_argument("--orphans", action="store_true")
    ap.add_argument("--watchers")
    ap.add_argument("--exclw", default="__never__")
    ap.add_argument("--kids", default="__never__")
    ap.add_argument("--ours", default="__never__")
    out = ap.add_mutually_exclusive_group()
    for m in ("--count", "--list", "--kill", "--cpu"):
        out.add_argument(m, dest="mode", action="store_const", const=m[2:])
    a = ap.parse_args(argv)
    hits = select(snapshot(), a)
    if a.mode == "list":
        for r in hits:
            print(f"{r['pid']}@@@{r['minutes']}@@@{r['cmd'][:110]}")
    elif a.mode == "kill":
        print(sum(kill(r["pid"]) for r in hits))
    elif a.mode == "cpu":
        print(sum(r["cpu"] for r in hits))
    else:
        print(len(hits))


if __name__ == "__main__":
    sys.exit(main())
