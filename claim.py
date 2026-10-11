#!/usr/bin/env python3
"""Atomically hand out ONE unmatched address to a worker. The pull half of pull-based dispatch.

    python claim.py <main|NNN>            claim the next address, print it (empty = pool drained;
                                          exit 3 = the kit is behind, run kit_update.py first)
    python claim.py <mod> --release <ad>  put one back (worker died without a verdict)
    python claim.py <mod> --status        how many are claimed right now

WHY PULL INSTEAD OF PRE-PARTITION. genwave hands each worker a fixed batch of P addresses. The
batches are never equally hard, so a worker that draws three easy functions finishes and idles while
another grinds three hard ones -- and the pre-partition is decided before anyone knows which is
which. Pulling one at a time balances by construction and deletes the SMALL_PER tuning question.

WHY ONE AT A TIME, NOT "PULL UNTIL THE BUDGET RUNS OUT". Cost per message rises with session length:
measured over 264 worker sessions, $0.0623/msg at 10-39 messages against $0.1777/msg past 220. A
worker that keeps pulling builds one long session and pays the expensive rate for its later
functions. One function per session keeps every session in the cheap band; `--max-budget-usd` is
then a backstop against a single runaway, not the stopping rule.

The claim is a directory created with mkdir, which is atomic on every filesystem we run on -- an
O_EXCL file would do as well, but a directory also survives being inspected by hand. A stale claim
(worker killed) is reclaimed after CLAIM_TTL_MIN so addresses cannot leak out of the pool forever.
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import glob
import os
import re
import shutil
import subprocess
import sys
import time

import buildcfg
import reserved

SP = _kp.SP
KIT = _kp.KIT
REPO = _kp.REPO
CLAIM_TTL_MIN = 90
RESERVED = ".reserved"
RESERVE_SEC = 60


def claims_dir(mod):
    d = f"{SP}/claims/{mod}"
    os.makedirs(d, exist_ok=True)
    return d


def cfg_for(mod):
    return f"{REPO}/{buildcfg.config_dir(mod)}"


def skiplist():
    out = set()
    for name in ("skiplist_main.txt", "skiplist_ov.txt"):
        try:
            for line in open(f"{KIT}/{name}", encoding="utf-8"):
                if line.strip():
                    out.add(line.split()[0].lower())
        except OSError:
            pass
    try:
        for line in open(f"{SP}/wlog/evolve_queue.txt", encoding="utf-8"):
            if ":" in line:
                out.add(line.strip().split(":")[1].lower())
    except OSError:
        pass
    return out | reserved.load()


_ATTEMPTS = None


def attempt_counts():
    """How many times each address has already been served, from the worker logs.

    Derived rather than stored: the logs are the record of what actually happened, so this cannot
    drift out of sync with reality the way a separate tally would. Archived windows count too --
    a function that failed four times before a harness fix is still the least promising thing to
    hand a worker while thousands of untried ones wait.
    """
    global _ATTEMPTS
    if _ATTEMPTS is None:
        import collections
        c = collections.Counter()
        for pat in (f"{SP}/wlog/pull_*_s*.log", f"{SP}/wlog/*/pull_*_s*.log"):
            for p in glob.glob(pat):
                try:
                    for line in open(p, encoding="utf-8", errors="replace"):
                        m = re.search(r" (?:MATCH|miss|ERROR) ([0-9a-f]{8})", line)
                        if m:
                            c[m.group(1)] += 1
                except OSError:
                    pass
        _ATTEMPTS = c
    return _ATTEMPTS


def priority_path(mod):
    return f"{SP}/wlog/priority_{'main' if mod == 'main' else 'ov' + mod}.txt"


def priority(mod):
    """Addresses to serve BEFORE the band rotation, best first.

    The sweep's kept artifacts (clsbest/) rank every parked address by how close it is, and a
    function sitting at BYTEDIFF 4 converts on a different budget than a cold one from the band
    queue -- but nothing could point a worker at a specific address, so that ranking could only ever
    be worked by hand. One line per address, `#` comments and blank lines ignored; an entry that is
    already matched, skiplisted or blocked is dropped by the same filters as any other claim.
    """
    p = priority_path(mod)
    if not os.path.exists(p):
        return []
    want = []
    for line in open(p, encoding="utf-8", errors="ignore"):
        m = re.match(r"\s*([0-9a-fA-F]{8})\b", line)
        if m:
            want.append(m.group(1).lower())
    if not want:
        return []
    live = set(unmatched(mod))
    return [a for a in want if a in live]


def priority_strike(mod, addr):
    """Drop a served address so a restart does not hand out the same one again."""
    p = priority_path(mod)
    if not os.path.exists(p):
        return
    keep = [l for l in open(p, encoding="utf-8", errors="ignore")
            if not re.match(r"\s*%s\b" % addr, l, re.I)]
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(keep)
    os.replace(tmp, p)


_BLOCKED = None


def blocked_addrs():
    """Addresses blocked since the last edit to core.md or colorsweep.py -- nothing has changed."""
    global _BLOCKED
    if _BLOCKED is None:
        if os.environ.get("CLAIM_IGNORE_BLOCKED"):
            _BLOCKED = set()
            return _BLOCKED
        cut = 0
        for p in (f"{KIT}/worker_src/core.md", f"{KIT}/colorsweep.py"):
            try:
                cut = max(cut, int(os.path.getmtime(p)))
            except OSError:
                pass
        last = {}
        try:
            for line in open(f"{SP}/wlog/blockers.tsv", encoding="utf-8", errors="ignore"):
                f = line.rstrip("\n").split("\t")
                if len(f) >= 5 and f[0].isdigit():
                    a = f[2].strip().lower()
                    last[a] = max(last.get(a, 0), int(f[0]))
        except OSError:
            pass
        _BLOCKED = {a for a, t in last.items() if t >= cut}
    return _BLOCKED


def band_cursor(mod, advance=False):
    """Which size band this module's rotation starts at, PERSISTED across claims.

    `stratify` interleaves the bands, but the queue is rebuilt from scratch on every claim and only
    index 0 is ever taken, so without a stored offset index 0 is always the biggest OPEN band's
    least-attempted, largest function and the rotation never advances -- four consecutive massive
    claims (4684, 4668, 4488, 4316) and a sample of zero for the xl band underneath them. The cursor
    moves one band per CLAIM, so `--peek` and `poolsize` can read the queue without disturbing it.
    """
    p = f"{SP}/wlog/bandcur_{mod}.txt"
    try:
        cur = int(open(p, encoding="utf-8").read().strip() or 0)
    except (OSError, ValueError):
        cur = 0
    if advance:
        os.makedirs(f"{SP}/wlog", exist_ok=True)
        try:
            open(p, "w", encoding="utf-8").write(str((cur + 1) % 997))
        except OSError:
            pass
    return cur


def max_size():
    # A CEILING ON WHAT A SLOT MAY CLAIM, in bytes, live: `echo 768 > $SP/CLAIM_MAX_SIZE`, empty or
    # absent for no ceiling. Conversion is measured per band and the bands do not pay the same --
    # a session that misses a 6056-byte function still spends its whole cap.
    try:
        v = open(f"{SP}/CLAIM_MAX_SIZE", encoding="utf-8").read().strip()
        return int(v) if v.isdigit() else 0
    except (OSError, ValueError):
        return 0


def focus_band():
    try:
        v = open(f"{SP}/CLAIM_FOCUS", encoding="utf-8").read().strip()
    except OSError:
        return None
    m = re.fullmatch(r"(\d+)-(\d+)", v)
    return (int(m.group(1)), int(m.group(2))) if m else None


def variety_every():
    try:
        v = open(f"{SP}/PULL_VARIETY", encoding="utf-8").read().strip()
    except OSError:
        return 0
    return int(v) if v.isdigit() and int(v) >= 2 else 0


def unmatched(mod, cursor=None):
    """Addresses with no delink range yet, SIZE-STRATIFIED rather than smallest-first.

    Smallest-first looked like "cheap wins before expensive ones" and was wrong twice over. It made
    the first 30 functions a run of 4-24 byte stubs, so the measured $0.56/function said nothing
    about the pool the fleet actually has to clear -- the cost curve stayed unknown exactly when it
    was most useful. Worse, the smallest functions in main are the secure-area stubs, whose link
    drift is CUMULATIVE (see the secure-area note), so draining them first piles the one class that
    integrates badly into a single pass.

    Interleaving by size band samples the real distribution from the first hour: every band makes
    progress, the cost curve is visible immediately, and no single awkward class dominates a wave.
    """
    cfg = cfg_for(mod)
    sym = open(f"{cfg}/symbols.txt", encoding="utf-8", errors="ignore").read()
    dl = open(f"{cfg}/delinks.txt", encoding="utf-8", errors="ignore").read()
    done = [(int(a, 16), int(b, 16)) for a, b in re.findall(
        r"(?m)^\s*\.(?:text|init) start:0x([0-9a-fA-F]+) end:0x([0-9a-fA-F]+)\s*$", dl)]
    skip = skiplist()
    _max_size = max_size()
    out = []
    for m in re.finditer(r"(?m)^(\S+)\s+kind:function\((?:arm|thumb),size=0x([0-9a-fA-F]+)\)"
                         r"\s+addr:0x([0-9a-fA-F]+)", sym):
        size, addr = int(m.group(2), 16), m.group(3).lower()
        if not size or addr in skip:
            continue
        if _max_size and size > _max_size:
            continue
        if any(s <= int(addr, 16) < e for s, e in done):
            continue
        out.append((size, addr))

    stale = blocked_addrs()
    if stale:
        keep = [(s, a) for s, a in out if a not in stale]
        if keep:
            out = keep

    # LEAST-ATTEMPTED FIRST. Claims are released on every restart and the order is deterministic, so
    # the same head-of-queue functions were re-served after every stop: 0204bc74 was attempted SEVEN
    # times, 020dd7ac and 020b33f0 six each. That is what made the large band look like it converted
    # nothing -- the sample was four known-hard functions on a loop, not 2,132 different ones. Prior
    # attempts are counted from the worker logs (including archived windows), so nothing new has to
    # be written and no state can go stale.
    tried = attempt_counts()
    rows = sorted((tried.get(a, 0), s, a) for s, a in out)
    bands = {"s": [], "m": [], "l": []}
    for n, size, addr in rows:
        bands["s" if size <= 64 else "m" if size <= 256 else "l"].append((n, size, addr))

    # SAMPLE EVERY SIZE BOUND, not four index-quartiles. Quartiles collapse 257..10204 bytes into
    # buckets whose contents depend on the population, so the tail never gets a turn: main's head was
    # 852 bytes while its largest unmatched function is 10204. Fixed bounds give each band its own
    # slot in the rotation, which is what makes the yield table comparable across bands -- the whole
    # point of the exercise is to learn where the money converts, and that needs samples everywhere.
    # COVER THE WHOLE SIZE RANGE, starting at 1. These bounds used to start at 257 because only the
    # large band was stratified; when the medium band was stratified too, every 65-256 function fell
    # outside every bound and the band came back EMPTY (see stratify below). Bounds that span the
    # full range mean any band can be stratified safely and the list says what it claims to say.
    #   s  1-64     m  65-256     l-  257-512     l  513-1024     l+  1025-2048
    #   xl 2049-4096            massive 4097+
    SIZE_BOUNDS = [(1, 64), (65, 256), (257, 512), (513, 1024), (1025, 2048), (2049, 4096),
                   (4097, 1 << 30)]
    _cursor = band_cursor(mod) if cursor is None else cursor

    def stratify(items):
        """Round-robin across fixed size bands, largest first within each band.

        ANYTHING OUTSIDE SIZE_BOUNDS MUST STILL COME OUT. SIZE_BOUNDS used to start at 257, so when
        this was applied to the MEDIUM band (65-256) every item fell outside every bound, `subs`
        came back empty and the function returned []. That silently deleted the medium band from the
        scheduler: `PULL_BAND=med` reported `pool drained` on a module with 331 medium functions
        waiting, and mixed round-robin -- which pops s/m/l in turn -- never served a medium function
        at all. 765 of them, 749 never once attempted, were unreachable.

        SIZE_BOUNDS now spans 1..inf, so `rest` should always be empty; it stays as the guard that
        makes a future gap in the bounds harmless instead of silently fatal.
        """
        subs = []
        for lo, hi in SIZE_BOUNDS:
            sub = sorted([r for r in items if lo <= r[1] <= hi], key=lambda r: (r[0], -r[1]))
            if sub:
                subs.append(sub)
        rest = [r for r in items if not any(lo <= r[1] <= hi for lo, hi in SIZE_BOUNDS)]
        if rest:
            subs.append(sorted(rest, key=lambda r: (r[0], -r[1])))
        # Biggest band first each cycle, but START at the persisted cursor so consecutive claims
        # walk DOWN the bands instead of re-serving the biggest one every time.
        order = list(reversed(subs))
        if order and _cursor:
            k = _cursor % len(order)
            order = order[k:] + order[:k]
        outq = []
        while any(order):
            for sub in order:
                if sub:
                    outq.append(sub.pop(0))
        return outq

    # ONE FLAG PER SIZE BAND, read as a FILE on every claim like PULL_BAND.
    #
    # `PULL_XL` used to be a single all-or-nothing gate over everything above 1024, so "sample the
    # xl band" and "spend the whole run on 4KB+ functions" were the same switch -- and because
    # `stratify`'s queue is rebuilt per claim, index 0 is always the BIGGEST open band's
    # least-attempted, largest function. With the tail open that meant four consecutive massive
    # claims (4684, 4668, 4488, 4316) and zero xl attempts, which is how the xl band came to have a
    # sample of zero while being blamed for the tail's cost. Until the rotation itself keeps a
    # cursor, closing a band IS how you reach the one below it.
    _bandflags = [((1, 64), "PULL_SMALL", True), ((65, 256), "PULL_MED", True),
                  ((257, 512), "PULL_LMINUS", True), ((513, 1024), "PULL_L", True),
                  ((1025, 2048), "PULL_LPLUS", True), ((2049, 4096), "PULL_XL", False),
                  ((4097, 1 << 30), "PULL_MASSIVE", False)]

    def _bandflag(name, default):
        try:
            v = open(f"{SP}/{name}", encoding="utf-8").read().strip()
        except OSError:
            return default
        return v == "1" if v else default

    _shut = [b for b, name, dflt in _bandflags if not _bandflag(name, dflt)]
    if _shut:
        for k in ("s", "m", "l"):
            bands[k] = [r for r in bands[k]
                        if not any(lo <= r[1] <= hi for lo, hi in _shut)]
    _focus = focus_band()
    if _focus:
        _n = variety_every()
        _is_variety = _n and _cursor % _n == _n - 1
        lo, hi = _focus
        picked = {}
        for k in ("s", "m", "l"):
            if _is_variety:
                picked[k] = [r for r in bands[k] if not lo <= r[1] <= hi]
            else:
                picked[k] = [r for r in bands[k] if lo <= r[1] <= hi]
        if any(picked.values()):
            bands = picked

    bands["l"] = stratify(bands["l"])
    bands["m"] = stratify(bands["m"])
    for k in ("s", "m", "l"):
        bands[k] = [a for _n, _s, a in bands[k]] if bands[k] and isinstance(bands[k][0], tuple)                    else bands[k]

    # FOCUS ONE BAND AT A TIME while tuning. Round-robin splits every sample across three very
    # different populations, so a window of 16 attempts is ~5 per band -- far too few to tell a
    # real effect from noise, which is exactly how three consecutive tuning windows came out
    # unreadable. Write `small`, `med` or `large` into $SP/PULL_BAND to serve only that band,
    # concentrate the sample, tune until its rate is good, then clear the file to go back to
    # round-robin for the actual run.
    want = ""
    try:
        want = open(f"{SP}/PULL_BAND", encoding="utf-8").read().strip().lower()
    except OSError:
        pass
    key = {"small": "s", "s": "s", "med": "m", "medium": "m", "m": "m",
           "large": "l", "l": "l"}.get(want)
    if key:
        return bands[key]

    mixed = []
    while any(bands.values()):
        for k in ("s", "m", "l"):
            if bands[k]:
                mixed.append(bands[k].pop(0))
    return mixed


def reap(d):
    """Release claims whose worker is long gone, so an address cannot leak out of the pool."""
    cut = time.time() - CLAIM_TTL_MIN * 60
    for name in os.listdir(d):
        p = os.path.join(d, name)
        try:
            if os.path.getmtime(p) < cut:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def all_modules():
    ov = f"{REPO}/{buildcfg.config_dir('main')}/overlays"
    return ["main"] + [d[2:] for d in sorted(os.listdir(ov))] if os.path.isdir(ov) else ["main"]


def pools(exclude=()):
    """(module, unclaimed count) for every module, largest first. Drives slot placement.

    `exclude` skips modules currently being integrated. A worker must not write into a module dir
    while finish_wave is moving files around in it, but that is a per-MODULE constraint -- blocking
    every slot during an integration left the whole fleet idle for 20 minutes.
    """
    out = []
    for mod in all_modules():
        if mod in exclude:
            continue
        try:
            free = [a for a in unmatched(mod)
                    if not os.path.isdir(os.path.join(claims_dir(mod), a))]
        except OSError:
            continue
        if free:
            try:
                live = len([n for n in os.listdir(claims_dir(mod))
                            if n != RESERVED or time.time() - os.path.getmtime(
                                os.path.join(claims_dir(mod), n)) < RESERVE_SEC])
            except OSError:
                live = 0
            out.append((mod, len(free), live))
    # SPREAD SLOTS ACROSS MODULES FIRST, depth second. Integration runs detached precisely because
    # "slots keep working other modules" -- but ranking on depth alone put every slot on main, the
    # deepest pool, so a main wave's `git checkout HEAD -- src/` lands on the files three live main
    # workers are writing. An ov015 wave committed one such in-flight file at BYTEDIFF 55.
    #
    # A PRIORITY QUEUE HAS TO STEER THE MODULE TOO. priority() only orders addresses WITHIN whatever
    # module the dispatcher already picked, and the pick was pool size -- so a near-miss queue built
    # in main and ov024 sent the first opus slot to ov003, the deepest pool, and would never have
    # served a queued address at all. A module holding claimable priority work outranks a deeper one.
    def _has_priority(mod):
        p = priority_path(mod)
        if not os.path.exists(p):
            return 1
        return 0 if priority(mod) else 1

    def _largest(mod):
        sizes = dict((a, s) for s, a in function_sizes(mod))
        return max((sizes.get(a, 0) for a in priority(mod) + unmatched(mod)), default=0)

    out.sort(key=lambda t: (t[2], _has_priority(t[0]), -_largest(t[0]), -t[1]))
    return [(m, n) for m, n, _live in out]


def function_sizes(mod):
    sym = open(f"{cfg_for(mod)}/symbols.txt", encoding="utf-8", errors="ignore").read()
    return [(int(s, 16), a.lower()) for s, a in re.findall(
        r"(?m)^\S+\s+kind:function\((?:arm|thumb),size=0x([0-9a-fA-F]+)\)\s+addr:0x([0-9a-fA-F]+)", sym)]


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: claim.py <main|NNN> [--release <addr> | --status] | --best | --pools")
    busy = set()
    if sys.argv[1] == "--best":
        p = pools(busy)
        if p:
            open(os.path.join(claims_dir(p[0][0]), RESERVED), "w").close()
        print(p[0][0] if p else "")
        return 0
    if sys.argv[1] == "--pools":
        for mod, n in pools(busy):
            print(f"{mod} {n}")
        return 0
    mod = sys.argv[1]
    if "--size" in sys.argv:
        addr = sys.argv[sys.argv.index("--size") + 1].lower().removeprefix("0x").zfill(8)
        print(dict((a, s) for s, a in function_sizes(mod)).get(addr, 0))
        return 0
    d = claims_dir(mod)

    if "--release" in sys.argv:
        shutil.rmtree(os.path.join(d, sys.argv[sys.argv.index("--release") + 1].lower()),
                      ignore_errors=True)
        return 0
    if "--peek" in sys.argv:
        # Non-mutating: what the NEXT claims would be, so a band-flag change can be verified
        # without spending a session to find out.
        # Simulate the cursor advancing, one claim per line -- a single queue's first n entries is
        # NOT what n claims would serve, because each claim moves the band cursor on.
        n = int(sys.argv[sys.argv.index("--peek") + 1])
        pri = priority(mod)[:n]
        for addr in pri:
            print("%s (priority)" % addr)
        n -= len(pri)
        base = band_cursor(mod)
        for i in range(n):
            q = unmatched(mod, cursor=base + i)
            if not q:
                break
            print(q[0])
        return 0

    if "--status" in sys.argv:
        reap(d)
        held = sorted(os.listdir(d))
        print(f"{len(held)} claimed: {' '.join(held[:12])}")
        return 0

    # THE GATES HAVE TO BITE HERE, not only in the dispatcher. pull_all's hold stops it REFILLING a
    # slot, but a live slot claims its next function through this path on its own, so a hold that
    # was meant to stop spending watched five more functions get claimed for ~$18 while the free
    # half of the pipeline sat idle. Serving nothing ends the slot's session, which is the point.
    # Only the per-module claim is gated: `--best` must keep answering or pull_all reads an empty
    # reply as "every pool drained" and stops itself for good.
    for gate in ("levercheck.py", "blockercheck.py"):
        if subprocess.run([sys.executable, os.path.join(KIT, gate)],
                          capture_output=True).returncode != 0:
            return 0
    if os.path.exists(os.path.join(SP, "claims", "UPDATE_WAITING")) and "pull_all.pid" in _kp.busy():
        return 0
    stale = _kp.behind()
    if stale and not _kp.busy():
        print(_kp.stale_message(stale) + "; no new work is served until then", file=sys.stderr)
        return 3

    reap(d)
    for addr in priority(mod) + unmatched(mod, cursor=band_cursor(mod, advance=True)):
        try:
            os.mkdir(os.path.join(d, addr))   # atomic: exactly one worker wins
        except FileExistsError:
            continue
        priority_strike(mod, addr)
        print(addr)
        return 0
    return 0          # drained: print nothing, caller stops


if __name__ == "__main__":
    sys.exit(main())
