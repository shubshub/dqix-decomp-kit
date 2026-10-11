#!/usr/bin/env python3
"""Propose a batch of free functions to reserve (SWARM-IDENTITY.md §3 step 1).

    python pick_wave.py main -n 32            propose 32 main functions
    python pick_wave.py main -n 32 --md       as a markdown table, ready to paste into an issue
    python pick_wave.py --all -n 5            spread the picks across every module with a pool

Ranking is SIBLING PROXIMITY: how much already-matched, already-SOURCED code lies within 0x400 bytes
of the function. It is the one selection lever with evidence behind it across six waves -- a function
whose neighbours are already written can be read against them, and a batch in one neighbourhood
shares the header work. Size only breaks ties, smallest first: the smallest functions in `main` are
the secure-area stubs, whose link drift is cumulative, so they must not lead a wave.

What comes out is a PROPOSAL, never a reservation. Run it through audit_batch.py before opening the
issue -- it re-reads the live issue and pull-request lists, which is the only check that can see
another swarm.

Reuses claim.py's own notion of what is free, blocked, skiplisted and worth retrying. It does not
reimplement any of it: a second definition of "free" would drift from the one the dispatcher serves.
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import argparse
import bisect
import glob
import os
import re
import sys

import claim

NEIGHBOURHOOD = 0x400
REPO = _kp.REPO


def sourced_addresses():
    """{address: path} for every source in the decomp that carries a `// USA:` tag.

    "Already sourced" is the half of sibling proximity that the ROM cannot tell us: a delinked range
    says a function is matched, but it is the file beside it that a worker can read.
    """
    out = {}
    for path in glob.glob(f"{REPO}/src/**/*.cpp", recursive=True):
        try:
            head = open(path, encoding="utf-8", errors="ignore").read(4000)
        except OSError:
            continue
        m = re.search(r"//\s*USA:\s*func_(?:ov(\d+)_)?([0-9a-fA-F]{8})", head)
        if m:
            out[f"{int(m.group(2), 16):08x}"] = path
    return out


def score(sizes, sourced, matched_starts):
    """Proximity score per address: matched and already-SOURCED code within the neighbourhood.

    Sourced code counts double: a delinked range says a function is matched, but it is the source
    file beside it that a worker can actually read against.
    """
    marks = sorted({int(a, 16) for a in sourced} | {int(a, 16) for a in matched_starts})
    srcs = {int(a, 16) for a in sourced}
    out = {}
    for addr, size in sizes.items():
        lo, hi = int(addr, 16), int(addr, 16) + max(size, NEIGHBOURHOOD)
        window = marks[bisect.bisect_left(marks, lo):bisect.bisect_left(marks, hi)]
        out[addr] = (2 * sum(1 for a in window if a in srcs) + len(window), size)
    return out


def candidates(mod, limit):
    """[(addr, name, size, score)] free, ranked. Everything claim.py already excludes stays excluded."""
    free = claim.unmatched(mod)
    # function_sizes() yields (size, addr), the other way round from what a size table wants.
    sizes = {f"{int(a, 16):08x}": s for s, a in claim.function_sizes(mod)}
    sourced = sourced_addresses()
    dl = open(f"{claim.cfg_for(mod)}/delinks.txt", encoding="utf-8", errors="ignore").read()
    matched_starts = {f"{int(a, 16):08x}" for a in
                      re.findall(r"(?m)^\s*\.(?:text|init) start:0x([0-9a-fA-F]+)", dl)}
    sc = score(sizes, sourced, matched_starts)
    tries = claim.attempt_counts()
    names = dict((f"{int(a, 16):08x}", n) for a, n in _names(mod))
    rows = []
    for a in free:
        addr = f"{int(a, 16):08x}"          # unmatched() yields hex strings; the table is keyed 8-wide
        size = sizes.get(addr, 0)
        if not size:
            continue                         # a zero-byte function is a phantom, not work
        if names.get(addr, "").endswith("_dup"):
            continue                         # the delink left a duplicate alias; matching it is a no-op
        sc_val = sc.get(addr, (0, 0))[0]
        rows.append((sc_val, addr, size, tries.get(addr, 0)))
    # Highest proximity first; then smallest, so a wave is not led by the cumulative-drift stubs.
    rows.sort(key=lambda r: (-r[0], r[2]))
    return [(addr, size, s, n) for s, addr, size, n in rows[:limit]]


def render(mod, rows, md):
    names = dict((f"{int(a, 16):08x}", n) for a, n in _names(mod))
    head = ("| # | Address | Bytes | Proximity | Bound name |\n|---|---|---|---|---|\n" if md else
            f"{'address':10} {'bytes':>6} {'near':>5}  name\n")
    out = [head]
    for i, (addr, size, sc, tries) in enumerate(rows, 1):
        nm = names.get(addr, "")
        out.append(f"| {i} | `{addr}` | {size} | {sc} | `{nm}` |\n" if md
                   else f"{addr:10} {size:6} {sc:5}  {nm}"
                        + (f"   ({tries} prior attempts)" if tries else "") + "\n")
    total = sum(s for _, s, _, _ in rows)
    out.append(f"\n{len(rows)} functions, {total} bytes\n" if md
               else f"\n{len(rows)} functions, {total} bytes\n")
    return "".join(out)


def _names(mod):
    sym = open(f"{claim.cfg_for(mod)}/symbols.txt", encoding="utf-8", errors="ignore").read()
    return [(a.lower(), n) for n, a in re.findall(
        r"(?m)^(\S+)\s+kind:function\([a-z]+,size=0x[0-9a-fA-F]+\)\s+addr:0x0*([0-9a-fA-F]+)", sym)]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mod", nargs="?", default="main", help="main, or a 3-digit overlay")
    ap.add_argument("--all", action="store_true", help="spread -n across every module with a pool")
    ap.add_argument("-n", type=int, default=32, help="how many functions to propose")
    ap.add_argument("--md", action="store_true", help="a markdown table for an issue body")
    args = ap.parse_args()

    mods = [m for m, live in claim.pools() if live] if args.all else [args.mod]
    if not mods:
        sys.exit("no module has a free pool")
    per = max(1, args.n // len(mods))
    print(f"# proposed for {', '.join(mods)} -- NOT reserved. Run audit_batch.py before reserving.\n")
    for m in mods:
        rows = candidates(m, per)
        print(f"## {m}\n" + render(m, rows, args.md))


if __name__ == "__main__":
    main()