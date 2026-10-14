import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.dirname(_kpos.path.abspath(__file__))))
import kitpaths as _kp
import json
import os
import struct
import sys
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor

import frida
from elftools.elf.elffile import ELFFile

sys.path.insert(0, (_kp.KIT + "/frida"))
import forcereal  # noqa: E402

SP = _kp.SP
KIT = _kp.KIT
REPO = _kp.REPO
sys.path.insert(0, KIT)
import buildcfg  # noqa: E402

JS = r"""
const CFG = %s;
const CLS = ptr('0x64669d');
const NODES = ptr('0x63ba40');
const NREAL = ptr('0x63a2bc');
const resetColors = new NativeFunction(ptr('0x513680'), 'void', ['int'], 'mscdecl');
const usableMask = new NativeFunction(ptr('0x4fe580'), 'uint32', ['int'], 'mscdecl');
const newRegister = new NativeFunction(ptr('0x4fe630'), 'int', ['int'], 'mscdecl');
const CHOICES = new Set(CFG.choices || (CFG.kind === 'choice' ? [CFG.idx] : []));
const ORDERS = CFG.orders || (CFG.kind === 'order' ? [CFG.pos] : []);
let call = 0;

function neighbours(n) {
  const idx = n.add(0x28).readS16();
  const base = NODES.readPointer();
  const out = [];
  let e = n.add(0x32).readPointer();
  while (!e.isNull()) {
    const hi = e.add(0xe).readS16();
    const other = hi <= idx ? e.add(0xc).readS16() : hi;
    out.push(base.add(other * 0x36));
    e = idx < hi ? e.add(4).readPointer() : e.add(8).readPointer();
  }
  return out;
}

function colorgraph(head) {
  call++;
  const cls = CLS.readS8();
  const nreal = NREAL.add(4 * cls).readS32();
  let ok = 1;
  resetColors(cls);
  let usable = usableMask(cls) >>> 0;
  const nodes = [];
  for (let n = head; !n.isNull(); n = n.readPointer()) nodes.push(n);
  const flipHere = CFG.call === -1 || CFG.call === call;
  if (flipHere) {
    let swapped = false;
    for (const mv of (CFG.moves || [])) {
      let at = -1;
      for (let k = 0; k < nodes.length; k++) if (nodes[k].add(0x28).readS16() === mv[0]) at = k;
      if (at >= 0) { nodes.splice(mv[1], 0, nodes.splice(at, 1)[0]); swapped = true; }
    }
    for (const p of ORDERS) {
      if (p + 1 >= nodes.length) continue;
      const t = nodes[p]; nodes[p] = nodes[p + 1]; nodes[p + 1] = t;
      swapped = true;
    }
    if (swapped)
      for (let i = 0; i < nodes.length; i++) nodes[i].writePointer(i + 1 < nodes.length ? nodes[i + 1] : ptr(0));
  }
  const trace = [];
  let i = 0;
  while (i < nodes.length) {
    const n = nodes[i];
    const idx = n.add(0x28).readS16();
    const pair = (n.add(0x2c).readU16() & 0x200) !== 0;
    let avail = usable;
    const nb = neighbours(n);
    for (const o of nb) {
      const r = o.add(0x2a).readS16();
      if (r === -1) continue;
      avail = ((o.add(0x2c).readU16() & 0x200) === 0 ? (avail & ~(1 << r)) : (avail & ~(3 << r))) >>> 0;
    }
    const cands = [];
    if (avail !== 0) {
      if (!pair) {
        for (let r = 0; r < nreal; r++) if ((avail >>> r) & 1) cands.push(r);
      } else {
        for (let r = 0; r < nreal; r += 2) if (((avail >>> r) & 3) === 3) cands.push(r);
      }
    }
    let pick = 0;
    if (flipHere && CHOICES.has(idx) && cands.length > 1) pick = 1;
    if (cands.length) n.add(0x2a).writeS16(cands[pick]);
    if (n.add(0x2a).readS16() === -1) {
      const nr = (newRegister(cls) << 16) >> 16;
      if (nr !== -1) { usable = (usable | (1 << nr)) >>> 0; continue; }
      n.add(0x2c).writeU16(n.add(0x2c).readU16() | 1);
      ok = 0;
    }
    trace.push([idx, n.add(0x2a).readS16(), cands.length, n.add(0x2e).readS16(), n.add(0xc).readS32(),
                n.add(0x2c).readU16(), nb.map(o => o.add(0x28).readS16())]);
    i++;
  }
  send({ev: 'color', call: call, ok: ok, nreal: nreal, usable: usable, nodes: trace});
  return ok;
}
Interceptor.replace(ptr('0x513a50'), new NativeCallback(colorgraph, 'int', ['pointer'], 'mscdecl'));
"""


def run(src, cfg, obj):
    cc = os.path.normpath(buildcfg.cc_path(None))
    argv = buildcfg.tool_command(cc) + list(buildcfg.FLAGS) + ["-c", src, "-o", obj]
    events = []
    done = threading.Event()
    device = frida.get_local_device()
    pid = device.spawn(argv, cwd=REPO)
    session = device.attach(pid)
    script = session.create_script(JS % json.dumps(cfg))
    script.on("message", lambda m, d: events.append(m["payload"]) if m["type"] == "send" else print(m))
    script.load()
    session.on("detached", lambda *a: done.set())
    device.resume(pid)
    done.wait(300)
    return events


def text_of(obj):
    with open(obj, "rb") as fh:
        e = ELFFile(fh)
        secs = [s for s in e.iter_sections() if s.name.startswith(".text") and s.data_size]
        return max(secs, key=lambda s: s.data_size).data()


def score(obj, R, size):
    data = text_of(obj)
    if len(data) != size:
        return None, "SIZE 0x%x" % len(data)
    ours = struct.unpack("<%dI" % (size // 4), data)
    bad = [i * 4 for i, (a, b) in enumerate(zip(ours, R)) if a != b
           and not (forcereal.relocish(a) and forcereal.relocish(b))]
    return bad, None


def main():
    src, mod, addr, size = sys.argv[1], _kp.module_label(sys.argv[2]), int(sys.argv[3], 16), int(sys.argv[4], 0)
    pool_from = int(sys.argv[5], 16) if len(sys.argv) > 5 else size
    out = os.path.splitext(src)[0] + ".cf"
    os.makedirs(out, exist_ok=True)
    R = forcereal.rom(mod, addr, size)

    native = out + "/native.o"
    import subprocess
    subprocess.run(buildcfg.tool_command(buildcfg.cc_path(None)) + list(buildcfg.FLAGS) + ["-c", src, "-o", native], cwd=REPO, check=True)
    ev = run(src, {"kind": "none", "call": -1}, out + "/replay.o")
    if text_of(native) != text_of(out + "/replay.o"):
        print("REPLAY MISMATCH: the JS colouring does not reproduce mwcc")
        return
    calls = [e for e in ev if e.get("ev") == "color"]
    last = calls[-1]
    if BASE:
        print("replay exact; carrying forward %s" % "+".join(BASE))
        out += "/base_%08x" % (zlib.crc32("+".join(BASE).encode()))
        os.makedirs(out, exist_ok=True)
        native = out + "/native.o"
        ev = run(src, flipset(tagcfgs(BASE, last["call"])), native)
        calls = [e for e in ev if e.get("ev") == "color"]
        last = calls[-1]
    base_bad, _ = score(native, R, size)
    base_real = [b for b in base_bad if b < pool_from]
    print("replay exact; colour calls %d; last call nodes %d; baseline real diff words %d %s"
          % (len(calls), len(last["nodes"]), len(base_real), ["0x%x" % b for b in base_real]))
    with open(out + "/trace.json", "w") as fh:
        json.dump(calls, fh)

    jobs = []
    for pos, node in enumerate(last["nodes"]):
        if node[2] > 1:
            jobs.append({"kind": "choice", "call": last["call"], "idx": node[0]})
        if pos + 1 < len(last["nodes"]):
            jobs.append({"kind": "order", "call": last["call"], "pos": pos})

    def one(cfg):
        tag = "%s_%s" % (cfg["kind"], cfg.get("idx", cfg.get("pos")))
        obj = "%s/%s.o" % (out, tag)
        run(src, flipset(tagcfgs(BASE, cfg["call"]) + [cfg]) if BASE else cfg, obj)
        bad, err = score(obj, R, size)
        if bad is None:
            return tag, None, err
        return tag, [b for b in bad if b < pool_from], None

    results = []
    with ThreadPoolExecutor(int(os.environ.get("CF_JOBS", "8"))) as pool:
        for tag, real, err in pool.map(one, jobs):
            results.append((tag, real, err))
            if real is not None and len(real) < len(base_real):
                print("%-14s real=%d %s" % (tag, len(real), ["0x%x" % b for b in real]))
                sys.stdout.flush()
    exact = [t for t, r, e in results if r == []]
    print("flips %d; exact: %s" % (len(results), exact or "none"))
    if os.environ.get("CF_PAIRS"):
        pairs(src, out, R, size, pool_from, native, last, jobs, results, base_real)


BASE = [t for t in os.environ.get("CF_BASE", "").split("+") if t]


def tagcfgs(tags, call):
    return [{"kind": t.split("_")[0], "call": call, ("idx" if t.startswith("choice") else "pos"): int(t.split("_")[1])}
            for t in tags]


def flipset(cfgs):
    return {"kind": "multi", "call": cfgs[0]["call"],
            "choices": [c["idx"] for c in cfgs if c["kind"] == "choice"],
            "orders": [c["pos"] for c in cfgs if c["kind"] == "order"]}


def pairs(src, out, R, size, pool_from, native, last, jobs, results, base_real):
    nat = text_of(native)
    diff = set(base_real)
    cfg_of = {"%s_%s" % (c["kind"], c.get("idx", c.get("pos"))): c for c in jobs}
    nodes = last["nodes"]

    def desc(tag):
        c = cfg_of[tag]
        if c["kind"] == "choice":
            n = next(x for x in nodes if x[0] == c["idx"])
            return "v%d r%d->2nd of %d" % (n[0], n[1], n[2])
        return "swap v%d<->v%d" % (nodes[c["pos"]][0], nodes[c["pos"] + 1][0])

    same = []
    for tag, real, err in results:
        if real is None:
            continue
        data = text_of("%s/%s.o" % (out, tag))
        changed = {i for i in range(0, min(len(data), len(nat)), 4) if data[i:i + 4] != nat[i:i + 4]}
        same.append((tag, real, changed))
    top = int(os.environ.get("CF_PAIR_TOP", "40"))
    hot = sorted((s for s in same if s[2] & diff), key=lambda s: len(s[1]))
    how = "touching diff"
    if not hot:
        hot, how = sorted((s for s in same if s[2]), key=lambda s: len(s[1])), "top by real"
    hot = hot[:top]
    tags = [s[0] for s in hot]
    combos = [(a, b) for i, a in enumerate(tags) for b in tags[i + 1:]]
    print("pair candidates %d (%s): %s" % (len(tags), how, " ".join("%s[%s]" % (t, desc(t)) for t in tags)))
    sys.stdout.flush()

    def trial(tagset, name):
        obj = "%s/%s.o" % (out, name)
        run(src, flipset(tagcfgs(BASE, last["call"]) + [cfg_of[t] for t in tagset]), obj)
        bad, err = score(obj, R, size)
        os.remove(obj)
        return None if bad is None else [b for b in bad if b < pool_from]

    def one(pair):
        return pair, trial(pair, "pair_%s+%s" % pair)

    presults = []
    with ThreadPoolExecutor(int(os.environ.get("CF_JOBS", "8"))) as pool:
        for r in pool.map(one, combos):
            presults.append(r)
    ranked = sorted((r for r in presults if r[1] is not None), key=lambda r: len(r[1]))
    for (a, b), real in ranked[:int(os.environ.get("CF_PAIR_SHOW", "15"))]:
        if len(real) >= len(base_real):
            break
        print("%-24s real=%d %s  {%s | %s}" % (a + "+" + b, len(real), ["0x%x" % x for x in real][:12], desc(a), desc(b)))
    with open(out + "/pairs.json", "w") as fh:
        json.dump(presults, fh)
    exact = ["+".join(p) for p, r in presults if r == []]

    sig = {}
    for t in tags:
        c = cfg_of[t]
        if c["kind"] == "choice":
            n = next(x for x in nodes if x[0] == c["idx"])
            sig.setdefault((n[1], n[2], n[3], n[4], n[5]), []).append(t)
    groups = [tuple(g) for g in sig.values() if len(g) > 2]
    gresults = []
    for g in groups:
        real = trial(g, "group")
        if real is None:
            continue
        gresults.append((g, real))
        print("group %-28s real=%d %s" % ("+".join(g), len(real), ["0x%x" % x for x in real][:12]))
        sys.stdout.flush()
        if real == []:
            exact.append("+".join(g))

    pool_ = sorted([((t,), r) for t, r, c in hot] + ranked + gresults, key=lambda r: len(r[1]))
    combo, combo_real = [], base_real
    for flips, real in pool_:
        if exact or len(real) >= len(base_real):
            break
        cand = combo + [t for t in flips if t not in combo]
        if cand == combo:
            continue
        res = trial(cand, "combo")
        if res is not None and len(res) < len(combo_real):
            combo, combo_real = cand, res
            print("combo +%-22s real=%d %s" % ("+".join(flips), len(combo_real), ["0x%x" % x for x in combo_real][:12]))
            sys.stdout.flush()
            if not combo_real:
                exact.append("+".join(combo))
                break
    print("pairs %d; exact: %s" % (len(presults), " ".join(exact) or "none"))


if __name__ == "__main__":
    main()
