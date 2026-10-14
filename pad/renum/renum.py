import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.dirname(_kpos.path.dirname(_kpos.path.abspath(__file__)))))
import kitpaths as _kp
import json
import os
import sys

sys.path.insert(0, (_kp.KIT + "/frida"))

import colorforce  # noqa: E402
import forcereal  # noqa: E402

colorforce.JS = colorforce.JS.replace(
    "  const trace = [];",
    "  if (CFG.stack && call === CFG.call) {\n"
    "    const pos = {}; CFG.stack.forEach((v, k) => pos[v] = k);\n"
    "    nodes.sort((a, b) => pos[a.add(0x28).readS16()] - pos[b.add(0x28).readS16()]);\n"
    "    for (let q = 0; q < nodes.length; q++) nodes[q].writePointer(q + 1 < nodes.length ? nodes[q + 1] : ptr(0));\n"
    "  }\n  const trace = [];", 1)


def simulate(trace, ranks):
    nodes = trace["nodes"]
    K = bin(trace["usable"]).count("1")
    nb = {x[0]: set(x[6]) for x in nodes}
    for a in list(nb):
        for b in nb[a]:
            nb.setdefault(b, set()).add(a)
    present = {x[0] for x in nodes}
    info = {x[0]: x for x in nodes}
    rank = lambda v: ranks.get(v, v)
    alive = set(present)
    deg = {v: len([u for u in nb[v] if u in alive or u not in present]) for v in alive}
    pushed = []

    def rm(v):
        pushed.append(v)
        alive.discard(v)
        for u in nb[v]:
            if u in alive:
                deg[u] -= 1

    while alive:
        changed = False
        for v in sorted(alive, key=rank):
            if v in alive and deg[v] < K:
                rm(v)
                changed = True
        if not changed:
            cand = [v for v in alive if not info[v][5] & 0x80] or list(alive)
            rm(min(cand, key=lambda v: (info[v][4] / deg[v], -rank(v))))
    return list(reversed(pushed))


if __name__ == "__main__":
    src, mod, addr, size = sys.argv[1], _kp.module_label(sys.argv[2]), int(sys.argv[3], 16), int(sys.argv[4], 0)
    ranks = {int(k): float(v) for k, v in (a.split("=") for a in sys.argv[5:])}
    out = os.path.splitext(src)[0] + ".cf"
    os.makedirs(out, exist_ok=True)
    R = forcereal.rom(mod, addr, size)
    ev = colorforce.run(src, {"kind": "none", "call": -1}, out + "/rn_base.o")
    calls = [e for e in ev if e.get("ev") == "color"]
    last = calls[-1]
    base_bad, _ = colorforce.score(out + "/rn_base.o", R, size)
    stack = simulate(last, {})
    assert stack == [x[0] for x in last["nodes"]], "simulator mismatch"
    stack = simulate(last, ranks)
    ev = colorforce.run(src, {"kind": "none", "call": last["call"], "stack": stack}, out + "/rn.o")
    bad, err = colorforce.score(out + "/rn.o", R, size)
    print("base diff words %d; renumbered diff %s %s" % (len(base_bad), err or len(bad), ["0x%x" % b for b in (bad or [])]))
