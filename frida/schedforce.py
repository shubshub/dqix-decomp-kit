import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.dirname(_kpos.path.abspath(__file__))))
import kitpaths as _kp
import json
import os
import struct
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import capstone
import frida
import yaml
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

sys.path.insert(0, (_kp.KIT + "/frida"))
import forcereal  # noqa: E402

SP = _kp.SP
KIT = _kp.KIT
REPO = _kp.REPO
sys.path.insert(0, KIT)
import buildcfg  # noqa: E402

EDGE_KINDS = {
    0x4ff18c: "raw", 0x4ff1d5: "waw", 0x4ff236: "war",
    0x4ff29d: "mwar", 0x4ff32d: "mraw", 0x4ff33f: "mwaw",
    0x4ff10e: "bar", 0x4ff142: "barl", 0x4ff5b9: "term",
}

JS = r"""
const CFG = %s;
const KINDS = %s;
const BLOCKS = ptr('0x63a828');
const pickOrig = new NativeFunction(ptr('0x4ff700'), 'pointer', ['pointer', 'uint32'], 'mscdecl');
const edgeOrig = new NativeFunction(ptr('0x4ff030'), 'void', ['pointer', 'pointer', 'uint32', 'uint32'], 'mscdecl');
const TRACE = CFG.mode === 'trace';
let active = false, tpass = 0, blk = 0, pos = null, npick = 0, blockPtrs = [], picks = [], edges = [];

function pcOf(node) { return node.add(0xc).readPointer(); }

Interceptor.attach(ptr('0x4ffa30'), {
  onEnter() {
    let name = null;
    try { name = this.context.ebp.readCString(); } catch (e) {}
    if (TRACE) send({ev: 'fn', name: name});
    active = name === CFG.want;
    if (active) { tpass++; blk = 0; blockPtrs = []; }
  },
  onLeave() {
    if (active && TRACE) {
      const layout = [];
      for (let b = BLOCKS.readPointer(); !b.isNull(); b = b.readPointer()) {
        const pcs = [];
        for (let pc = b.add(0x14).readPointer(); !pc.isNull(); pc = pc.readPointer()) pcs.push([pc.toString(), pc.add(0x28).readU16()]);
        layout.push([b.toString(), pcs]);
      }
      send({ev: 'pass', pass: tpass, blocks: blockPtrs, picks: picks, edges: edges, layout: layout});
      picks = []; edges = [];
    }
    active = false;
  }
});

Interceptor.attach(ptr('0x4ff8c0'), {
  onEnter(a) {
    if (!active) return;
    blk++;
    blockPtrs.push(a[0].toString());
    pos = new Map();
    let i = 0;
    for (let pc = a[0].add(0x14).readPointer(); !pc.isNull(); pc = pc.readPointer()) pos.set(pc.toString(), i++);
  },
  onLeave() { pos = null; }
});

const PICKS = new Set(CFG.picks || []), DROPS = new Set(CFG.edges || []);
if (TRACE || PICKS.size) {
  Interceptor.replace(ptr('0x4ff700'), new NativeCallback(function (head, cycle) {
    const best = pickOrig(head, cycle);
    if (!active || pos === null || best.isNull()) return best;
    const n = npick++;
    const flip = PICKS.has(n);
    if (!TRACE && !flip) return best;
    let alt;
    if (best.equals(head)) {
      alt = pickOrig(best.readPointer(), cycle);
    } else {
      const prev = best.add(4).readPointer();
      prev.writePointer(best.readPointer());
      alt = pickOrig(head, cycle);
      prev.writePointer(best);
    }
    if (TRACE && !alt.isNull()) picks.push([n, tpass, blk, cycle, pcOf(best).toString(), pcOf(alt).toString()]);
    return flip && !alt.isNull() ? alt : best;
  }, 'pointer', ['pointer', 'uint32'], 'mscdecl'));
}

if (TRACE || DROPS.size) {
  Interceptor.replace(ptr('0x4ff030'), new NativeCallback(function (f, t, p3, p4) {
    if (!active || pos === null) { edgeOrig(f, t, p3, p4); return; }
    const fpc = pcOf(f), tpc = pcOf(t);
    const key = tpass + ':' + blk + ':' + pos.get(fpc.toString()) + '>' + pos.get(tpc.toString());
    if (!TRACE) { if (!DROPS.has(key)) edgeOrig(f, t, p3, p4); return; }
    let exists = false;
    for (let e = f.add(8).readPointer(); !e.isNull(); e = e.readPointer()) if (e.add(4).readPointer().equals(t)) { exists = true; break; }
    edgeOrig(f, t, p3, p4);
    if (exists) return;
    let lat = -1;
    for (let e = f.add(8).readPointer(); !e.isNull(); e = e.readPointer()) if (e.add(4).readPointer().equals(t)) { lat = e.add(8).readU16(); break; }
    const kind = KINDS[this.returnAddress.toInt32()] || ('?' + this.returnAddress);
    edges.push([key, kind, lat, fpc.toString(), tpc.toString()]);
  }, 'void', ['pointer', 'pointer', 'uint32', 'uint32'], 'mscdecl'));
}
"""


def compile_argv(src, obj):
    return buildcfg.tool_command(os.path.normpath(buildcfg.cc_path(None))) + list(buildcfg.FLAGS) + ["-c", src, "-o", obj]


def run(src, cfg, obj):
    events = []
    done = threading.Event()
    device = frida.get_local_device()
    pid = device.spawn(compile_argv(src, obj), cwd=REPO)
    session = device.attach(pid)
    script = session.create_script(JS % (json.dumps(cfg), json.dumps({str(k): v for k, v in EDGE_KINDS.items()})))
    script.on("message", lambda m, d: events.append(m["payload"]) if m["type"] == "send" else print(m))
    script.load()
    session.on("detached", lambda *a: done.set())
    device.resume(pid)
    done.wait(600)
    return events


class Func:
    def __init__(self, obj, name=None, addr=None):
        with open(obj, "rb") as fh:
            e = ELFFile(fh)
            funcs = [s for s in e.get_section_by_name(".symtab").iter_symbols() if s["st_info"]["type"] == "STT_FUNC"]
            if name is None:
                hits = [s for s in funcs if addr and ("%08x" % addr) in s.name.lower()]
                sym = hits[0] if hits else max(funcs, key=lambda s: s["st_size"])
            else:
                sym = next(s for s in funcs if s.name == name)
            self.name = sym.name
            shndx, lo, size = sym["st_shndx"], sym["st_value"], sym["st_size"]
            sec = e.get_section(shndx)
            self.data = sec.data()[lo:lo + size]
            self.size = size
            self.relocs = set()
            for rs in e.iter_sections():
                if isinstance(rs, RelocationSection) and rs["sh_info"] == shndx:
                    self.relocs.update(r["r_offset"] - lo for r in rs.iter_relocations() if lo <= r["r_offset"] < lo + size)
            maps = sorted((s["st_value"] - lo, s.name[:2]) for s in e.get_section_by_name(".symtab").iter_symbols()
                          if s["st_shndx"] == shndx and s.name[:2] in ("$a", "$d") and lo <= s["st_value"] < lo + size)
            self.data_ranges = []
            for i, (off, kind) in enumerate(maps):
                if kind == "$d":
                    end = maps[i + 1][0] if i + 1 < len(maps) else size
                    self.data_ranges.append((off, end))

    def words(self):
        return struct.unpack("<%dI" % (len(self.data) // 4), self.data)

    def code_offsets(self):
        return [o for o in range(0, self.size, 4) if not any(a <= o < b for a, b in self.data_ranges)]


def rom(mod, addr, size):
    if mod == "main":
        base = yaml.safe_load(open(REPO + "/extract/usa/arm9/arm9.yaml"))["base_address"]
        blob = open(REPO + "/extract/usa/arm9/arm9.bin", "rb").read()
        return struct.unpack("<%dI" % (size // 4), blob[addr - base:addr - base + size])
    return forcereal.rom(mod, addr, size)


def score(fn, R, size, pool_from):
    if fn.size != size:
        return None
    ours = fn.words()
    return [i * 4 for i, (a, b) in enumerate(zip(ours, R)) if a != b and i * 4 < pool_from
            and i * 4 not in fn.relocs and not (forcereal.relocish(a) and forcereal.relocish(b))]


MD = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)


def dis(word, off):
    ins = list(MD.disasm(struct.pack("<I", word), off))
    return "%s %s" % (ins[0].mnemonic, ins[0].op_str) if ins else ".word 0x%08x" % word


def moved(base, var):
    a, b = base.words(), var.words()
    idx = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
    if not idx:
        return "no change"
    lo, hi = idx[0], idx[-1]
    span = "0x%x-0x%x" % (lo * 4, hi * 4) if hi != lo else "0x%x" % (lo * 4)
    if hi - lo > 3:
        return "changed %s (%d words)" % (span, len(idx))
    return "changed %s: %s" % (span, " ; ".join("%s -> %s" % (dis(a[i], i * 4), dis(b[i], i * 4)) for i in range(lo, hi + 1)))


def main():
    src = os.path.abspath(sys.argv[1]).replace("\\", "/")
    mod, addr, size = _kp.module_label(sys.argv[2]), int(sys.argv[3], 16), int(sys.argv[4], 0)
    pool_from = int(sys.argv[5], 16) if len(sys.argv) > 5 else size
    out = os.path.splitext(src)[0] + ".sf"
    os.makedirs(out, exist_ok=True)
    R = rom(mod, addr, size)

    native_obj = out + "/native.o"
    subprocess.run(compile_argv(src, native_obj), cwd=REPO, check=True)
    native = Func(native_obj, addr=addr)
    ev = run(src, {"mode": "trace", "want": native.name}, out + "/replay.o")
    passes = [e for e in ev if e.get("ev") == "pass"]
    if not passes:
        sys.exit("NO SCHEDULER PASS SEEN for %s; seen %s" % (native.name, sorted({e["name"] for e in ev if e.get("ev") == "fn"})))
    replay = Func(out + "/replay.o", name=native.name)
    if replay.data != native.data:
        sys.exit("REPLAY MISMATCH: the hooked scheduler does not reproduce mwcc for %s" % native.name)
    with open(out + "/trace.json", "w") as fh:
        json.dump(passes, fh)

    base_real = score(native, R, size, pool_from)
    if base_real is None:
        sys.exit("SIZE 0x%x != 0x%x" % (native.size, size))

    final = passes[-1]
    codes = native.code_offsets()
    npc = sum(len(p) for _, p in final["layout"])
    addr_of, block_rng = {}, {}
    if npc == len(codes):
        k = 0
        for bptr, pcs in final["layout"]:
            if pcs:
                block_rng[bptr] = (codes[k], codes[k + len(pcs) - 1])
            for pc, _ in pcs:
                addr_of[pc] = codes[k]
                k += 1
    else:
        print("layout: %d final pcodes vs %d code words; block localisation off" % (npc, len(codes)))
    words = native.words()

    def where(pc):
        o = addr_of.get(pc)
        return "0x%x %s" % (o, dis(words[o // 4], o)) if o is not None else "?"

    localise = bool(block_rng) and not os.environ.get("SF_ALL")

    def hot(p, b):
        if not localise:
            return True
        rng = block_rng.get(p["blocks"][b - 1])
        return rng is not None and any(rng[0] - 4 <= d <= rng[1] + 4 for d in base_real)

    kinds = set(os.environ.get("SF_EDGES", "war,waw,mwar,mraw,mwaw,bar,barl").split(","))
    jobs = []
    for p in passes:
        for n, ps, b, cyc, best, alt in p["picks"]:
            if hot(p, b):
                jobs.append(({"mode": "flip", "picks": [n]}, "pick_%d" % n,
                             "p%d blk%d cyc%d %s -> %s" % (ps, b, cyc, where(best), where(alt))))
        for key, kind, lat, f, t in p["edges"]:
            b = int(key.split(":")[1])
            if kind in kinds and hot(p, b):
                jobs.append(({"mode": "flip", "edges": [key]}, "edge_%s" % key.replace(":", "_").replace(">", "_"),
                             "p%s %s lat%d %s => %s" % (key[0], kind, lat, where(f), where(t))))
    print("replay exact; %s; target passes %d; picks %d, edges %d; baseline real diff words %d %s; variants %d%s"
          % (native.name, len(passes), sum(len(p["picks"]) for p in passes), sum(len(p["edges"]) for p in passes),
             len(base_real), ["0x%x" % b for b in base_real], len(jobs), " (localised)" if localise else ""))
    sys.stdout.flush()
    if os.environ.get("SF_DRY"):
        return

    def one(job):
        cfg, tag, desc = job
        obj = "%s/%s.o" % (out, tag)
        run(src, dict(cfg, want=native.name), obj)
        try:
            fn = Func(obj, name=native.name)
        except Exception as ex:
            return tag, desc, None, "ERR %s" % ex
        real = score(fn, R, size, pool_from)
        info = moved(native, fn)
        os.remove(obj)
        return tag, desc, real, info

    results = []
    with ThreadPoolExecutor(int(os.environ.get("SF_JOBS", "12"))) as pool:
        for r in pool.map(one, jobs):
            results.append(r)
    ranked = sorted((r for r in results if r[2] is not None and r[3] != "no change"), key=lambda r: len(r[2]))
    for tag, desc, real, info in ranked[:int(os.environ.get("SF_TOP", "10"))]:
        print("%-16s real=%d %s\n    %s\n    %s" % (tag, len(real), ["0x%x" % b for b in real][:12], desc, info))
    with open(out + "/results.json", "w") as fh:
        json.dump(results, fh)
    nochange = sum(1 for r in results if r[3] == "no change")
    sized = sum(1 for r in results if r[2] is None)
    print("no-change %d; size-changed/err %d" % (nochange, sized))
    exact = [t for t, d, r, i in results if r == []]
    cfg_of = {tag: cfg for cfg, tag, desc in jobs}
    combo, combo_real = {"picks": [], "edges": []}, base_real
    tags = []
    budget = int(os.environ.get("SF_COMBO", "0")) or len(ranked)
    tried = 0
    for tag, desc, real, info in ranked:
        if exact or tried >= budget:
            break
        tried += 1
        trial = {k: combo[k] + cfg_of[tag].get(k, []) for k in combo}
        res = one(({"mode": "flip", **trial}, "combo", ""))
        if res[2] is not None and len(res[2]) < len(combo_real):
            combo, combo_real = trial, res[2]
            tags.append(tag)
            print("combo +%-14s real=%d %s" % (tag, len(combo_real), ["0x%x" % b for b in combo_real][:12]))
            sys.stdout.flush()
            if not combo_real:
                exact.append("+".join(tags))
                break
    print("combo: tried %d/%d ranked flips on top of the running combo" % (tried, len(ranked)))
    print("flips %d; exact: %s" % (len(results), " ".join(exact) or "none"))


if __name__ == "__main__":
    main()
