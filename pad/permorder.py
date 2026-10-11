"""Bind a block of field reads to locals and search DEFINITION ORDER exhaustively.

When a run of struct-field reads feeds arithmetic, which value lands in which register is decided by
definition order -- and that is a bounded space, so it can be searched instead of reasoned about.
Written for main:020b7ba0, whose whole 14-byte residue is two fields holding each other's registers
and which matches under -O4 (a flag we do not believe the original used, so the SOURCE is what is
wrong).

    python pad/permorder.py <mod> <addr> <src.cpp> <marker-file>

The marker file holds two blocks separated by a line of `---`: the exact text to replace, and a
template whose `{decls}` is filled with one permutation of the `int vNAME = EXPR;` lines that follow
a `@decls` line. Early-exits on MATCH.
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.dirname(_kpos.path.abspath(__file__))))
import kitpaths as _kp
import itertools
import os
import subprocess
import sys

import residue

SP = _kp.SP
KIT = _kp.KIT
REPO = _kp.REPO


def gate(mod, addr, path):
    env = dict(os.environ)
    env.pop("WGATE_SESSION", None)
    r = subprocess.run([sys.executable, f"{KIT}/wgate.py", mod, addr, path],
                       capture_output=True, text=True, cwd=REPO, env=env)
    return residue.headline((r.stdout or "") + (r.stderr or ""))


UNSCORED = 1 << 20


def score(v):
    cls, n, _ = residue.parse_verdict(v)
    if cls == "MATCH":
        return -1
    if cls in ("NO-COMPILE", "UNKNOWN"):
        return UNSCORED
    return abs(n)


def main():
    mod, addr, src, marker = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    spec = open(marker, encoding="utf-8").read()
    old, rest = spec.split("\n---\n", 1)
    tmpl, decls = rest.split("@decls\n", 1)
    decls = [d for d in decls.split("\n") if d.strip()]
    base = open(src, encoding="utf-8").read().replace("// SCRATCH-USA", "// USA")
    if old not in base:
        sys.exit("marker block not found verbatim in %s" % src)
    work = f"{SP}/handwork/permorder_{addr}"
    os.makedirs(work, exist_ok=True)
    best, bestv, n = UNSCORED, None, 0
    for perm in itertools.permutations(range(len(decls))):
        body = tmpl.replace("{decls}", "\n".join(decls[i] for i in perm))
        p = f"{work}/p.cpp"
        open(p, "w", encoding="utf-8", newline="\n").write(base.replace(old, body))
        v = gate(mod, addr, p)
        n += 1
        s = score(v)
        if s < best:
            best, bestv = s, (v, perm)
            print("  %-26s %s" % (v, " ".join(decls[i].split()[1] for i in perm)), flush=True)
        if v == "MATCH":
            keep = f"{work}/MATCH.cpp"
            open(keep, "w", encoding="utf-8", newline="\n").write(base.replace(old, body))
            print("\nMATCH after %d orderings -> %s" % (n, keep))
            return 0
    print("\n%d orderings, best %s" % (n, bestv[0] if bestv else "none"))
    return 2


if __name__ == "__main__":
    sys.exit(main())
