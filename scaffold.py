#!/usr/bin/env python
"""ZERO-TOKEN scaffold: resolve everything about a function that is pure lookup, so the worker only
has to write the logic.

Measured: an unmatched overlay function averages 18.3 callee/data references. `relocs.txt` already
records the exact target address of every one, and symbols.txt gives its real name. Workers currently grep that out one reference at a time — and guessing wrong
produces UNDEF-SYM / RELOC-WRONG, the two gate errors that force a full retry.

None of that needs a model. This emits:
  * an `extern "C"` declaration for every call target, under its CORRECT current name
  * an `extern` declaration for every pool data reference
  * the USA tag, the right ARM/THUMB macro, and a stub with the right symbol name
  * the target disassembly inline, with call sites annotated by callee name

Arg counts and types are NOT derivable and are left as TODO — that is the worker's job.

Usage: python scaffold.py <module> <addr> [outfile]
       python scaffold.py --wave <module>          (scaffold every addr in that module's wave files)
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import re, sys, os, glob, subprocess, functools

import buildcfg
import symfix

SP = _kp.SP
KIT = _kp.KIT
REPO = _kp.REPO

SYMS, BOUNDS = {}, {}
for p in glob.glob(f"{REPO}/{buildcfg.config_dir('main')}/**/symbols.txt", recursive=True):
    ov = re.search(r'overlays[/\\]ov(\d+)', p)
    tag = f"overlay({int(ov.group(1))})" if ov else "main"
    table = SYMS.setdefault(tag, {})
    for l in open(p, encoding='utf-8', errors='ignore'):
        m = re.match(r'(\S+)\s+kind:(\w+)[^\n]*?addr:0x([0-9a-fA-F]+)', l)
        if m:
            table[int(m.group(3), 16)] = (m.group(1), m.group(2))
    dl = os.path.join(os.path.dirname(p), "delinks.txt")
    ends = [int(e, 16) for e in re.findall(r'start:0x[0-9a-fA-F]+\s+end:0x([0-9a-fA-F]+)\s+kind:', open(dl).read())] \
        if os.path.exists(dl) else []
    BOUNDS[tag] = sorted(set(table) | set(ends))

def object_size(tag, addr):
    bounds = BOUNDS.get(tag, [])
    later = [b for b in bounds if b > addr]
    return later[0] - addr if later else None


def cfg_of(mod):
    return buildcfg.config_dir(mod)


@functools.lru_cache(maxsize=None)
def module_image(mod):
    blob = open(f"{REPO}/{buildcfg.pristine(mod)}", "rb").read()
    if mod == "main":
        return blob, 0x02000000
    delinks = open(f"{REPO}/{cfg_of(mod)}/delinks.txt", encoding='utf-8', errors='ignore').read()
    return blob, min(int(s, 16) for s in re.findall(r'start:0x([0-9a-fA-F]+)', delinks))


def _signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def rom_calls(mod, a, sz, isa):
    blob, base = module_image(mod)
    code = blob[a - base:a - base + sz]
    calls = []
    if isa == "arm":
        words = [(a + i, int.from_bytes(code[i:i + 4], "little")) for i in range(0, len(code) - 3, 4)]
        pool = {pc + 8 + ((w & 0xfff) if w & 0x800000 else -(w & 0xfff))
                for pc, w in words if (w & 0x0f7f0000) == 0x051f0000}
        for pc, w in words:
            if pc in pool:
                continue
            if (w >> 28) != 0xf and (w & 0x0f000000) == 0x0b000000:
                calls.append((pc, pc + 8 + _signed(w & 0xffffff, 24) * 4))
            elif (w >> 25) == 0x7d:
                calls.append((pc, pc + 8 + _signed(w & 0xffffff, 24) * 4 + ((w >> 24) & 1) * 2))
        return calls
    halves = [(a + i, int.from_bytes(code[i:i + 2], "little")) for i in range(0, len(code) - 1, 2)]
    pool = set()
    for pc, h in halves:
        if (h & 0xf800) == 0x4800:
            p = ((pc + 4) & ~3) + (h & 0xff) * 4
            pool |= {p, p + 2}
    for (pc, hi), (_n, lo) in zip(halves, halves[1:]):
        if pc in pool or (hi & 0xf800) != 0xf000 or (lo & 0xe800) != 0xe800:
            continue
        off = _signed(((hi & 0x7ff) << 12) | ((lo & 0x7ff) << 1), 23)
        calls.append((pc, ((pc + 4) & ~3) + off if (lo & 0xf800) == 0xe800 else pc + 4 + off))
    return calls


def callee_decl(nm):
    if not nm.startswith("_Z"):
        return nm, f'extern "C" void {nm}();'
    m = re.match(r'^_Z(\d+)(.+)$', nm)
    if m:
        n = int(m.group(1))
        try:
            params = symfix.demangle_params(m.group(2)[n:])
        except (ValueError, IndexError):
            params = None
        if params is not None:
            return m.group(2)[:n], f"void {m.group(2)[:n]}({', '.join(params)});"
    return nm, f"// C++ callee {nm}: declare the signature it demangles to, without extern \"C\""


def scaffold(mod, addr):
    cfg = cfg_of(mod)
    sym = open(f"{REPO}/{cfg}/symbols.txt", encoding='utf-8', errors='ignore').read()
    m = re.search(r'^(\S+) kind:function\((arm|thumb),size=0x([0-9a-fA-F]+)\) addr:0x0*%s\b'
                  % addr.lstrip('0'), sym, re.M | re.I)
    if not m:
        return None
    symname, isa, sz = m.group(1), m.group(2), int(m.group(3), 16)
    a = int(addr, 16)

    rp = f"{REPO}/{cfg}/relocs.txt"
    rel = []
    if os.path.exists(rp):
        for l in open(rp, encoding='utf-8', errors='ignore'):
            mm = re.match(r'from:0x([0-9a-fA-F]+) kind:(\w+) to:0x([0-9a-fA-F]+)(?: add:(\S+))? module:(\S+)', l)
            if mm:
                f = int(mm.group(1), 16)
                if a <= f < a + sz:
                    rel.append((f, mm.group(2), int(mm.group(3), 16), mm.group(5), mm.group(4)))

    own = "main" if mod == "main" else f"overlay({int(mod)})"
    covered = {r[0] for r in rel}
    for frm, to in rom_calls(mod, a, sz, isa):
        tag = next((t for t in (own, "main") if SYMS.get(t, {}).get(to, ('', ''))[1] == 'function'), None)
        if tag and frm not in covered:
            rel.append((frm, "arm_call", to, tag, None))
    rel.sort(key=lambda r: r[0])

    calls, data, seen = [], [], set()
    for frm, kind, to, tag, add in rel:
        nm = SYMS.get(tag, {}).get(to, (None, None))[0]
        if not nm or nm in seen:
            continue
        seen.add(nm)
        k = SYMS.get(tag, {}).get(to, ('', ''))[1]
        (calls if ('call' in kind or k == 'function') else data).append(
            (nm, to, frm, (object_size(tag, to), add)))

    L = [f"#include <globaldefs.h>", ""]
    L.append("// AUTO-GENERATED SCAFFOLD (scaffold.py) — every NAME and ADDRESS below is exact, resolved")
    L.append("// from relocs.txt + symbols.txt. Do NOT re-grep them. Argument counts/types are GUESSES")
    L.append("// (not derivable) — correct them as you go. Delete anything you end up not calling.")
    L.append("")
    for nm, to, frm, _size in sorted(calls, key=lambda x: x[2]):
        L.append(f'{callee_decl(nm)[1]}   // called at +0x{frm - a:x}  (0x{to:08x})')
    if data:
        L.append("")
        for nm, to, frm, (size, add) in sorted(data, key=lambda x: x[2]):
            offset = f"{add}" if add and add.startswith("-") else (f"+{add}" if add else "")
            extent = f", 0x{size:x} bytes to the next symbol" if size else ""
            L.append(f"extern int {nm};   // pool ref at +0x{frm - a:x}  (0x{to:08x}{offset}{extent})")
    L.append("")

    # DO NOT embed the full disassembly. It is the bulk of the file — the 8 largest scaffolds came to
    # 11.6k tokens, more than double the worker doc — and the worker already fetches the listing with
    # the documented grep. Emit only the CALL MAP: offset -> the callee's correct current name, which
    # is the part they cannot derive. SCAFFOLD_ASM=1 restores the full listing if ever needed.
    byaddr = {f: (callee_decl(nm)[0], to) for nm, to, f, _size in calls}
    byaddr.update({f: (nm, to) for nm, to, f, _size in data})
    if byaddr:
        L.append("/* CALL MAP (offset -> resolved name; listing via the grep in the worker doc)")
        for f in sorted(byaddr):
            L.append(f"   +0x{f - a:<5x} {byaddr[f][0]}")
        L.append("*/")
        L.append("")
    if os.environ.get("SCAFFOLD_ASM"):
        dz = subprocess.run([sys.executable, f"{SP}/lab/dz.py", "rom", mod, addr],
                            capture_output=True, text=True).stdout.strip().split('\n')
        L.append("/* TARGET DISASSEMBLY")
        for line in dz[1:]:
            mm = re.match(r'^([0-9a-f]{8})', line)
            note = ""
            if mm and int(mm.group(1), 16) in byaddr:
                note = f"      <-- {byaddr[int(mm.group(1), 16)][0]}"
            L.append("   " + line + note)
        L.append("*/")
        L.append("")
    pfx = "func_" if mod == "main" else f"func_ov{mod}_"
    mac = "THUMB" if isa == "thumb" else "ARM"
    # A CURATED ROM NAME IS THE BIGGEST HINT THERE IS, and it was being handed over silently.
    # 146 unmatched functions carry a real symbol (strcmp, AutoloadCallback, WaitForVCountZero,
    # and mangled C++ names that spell out the whole signature). Knowing the name means the body
    # can be written from semantics instead of reconstructed from registers -- that is how twelve
    # C library functions landed in one pass. It is also BINDING: dsd's `check symbols` fails the
    # whole build if the linked binary lacks it, so it must not be renamed.
    if not symname.startswith("func_"):
        L.append(f"// ROM SYMBOL: {symname}")
        L.append("// This function's REAL name, straight from the ROM config. Two consequences:")
        L.append("//   1. You know what it DOES. Write it from that meaning; if it is a C library")
        L.append("//      or SDK routine, write the library implementation rather than reverse-")
        L.append("//      engineering the registers. A mangled _Z name also gives you the exact")
        L.append("//      parameter types -- decode it before writing anything.")
        L.append("//   2. Keep this exact name and the `// KEEP-NAME` marker below. Renaming it to")
        L.append("//      func_<addr> deletes a symbol the build requires and reds the WHOLE wave.")
        L.append("// KEEP-NAME")
    L.append(f"// USA: {pfx}{addr}")
    L.append(f'extern "C" {mac} int {symname if not symname.startswith("func_") else "TODO_Name_" + addr}(/* TODO args */) {{')
    L.append("    /* TODO */")
    L.append("}")
    return '\n'.join(L) + '\n', len(calls) + len(data)


if sys.argv[1] == "--all":
    # EVERY remaining function, not just this wave's. Two reasons:
    #  1. the point is to grow how much is done AUTOMATICALLY — each improvement to infer.py/synth.py
    #     should reach the whole pool, not the 9 addrs that happen to be in flight;
    #  2. a scaffold is cheap and disposable, so regenerating the lot after any improvement (or after
    #     callees get renamed, which is the only thing that goes stale) costs minutes of CPU.
    # Facts (args, fields, control flow, switch) never go stale. Only NAMES do, and a full regen fixes
    # those in one pass.
    out = f"{SP}/scaffold"; os.makedirs(out, exist_ok=True)
    skip = set()
    for f in ("skiplist_ov.txt", "skiplist_main.txt"):
        q = f"{KIT}/{f}"
        if os.path.exists(q):
            skip |= {l.split()[0].lower() for l in open(q) if l.strip()}
    n = 0
    for sy in glob.glob(f"{REPO}/{buildcfg.config_dir('main')}/overlays/ov*/symbols.txt") + [f"{REPO}/{buildcfg.config_dir('main')}/symbols.txt"]:
        mod = "main" if sy.endswith("arm9/symbols.txt") else re.search(r'ov(\d+)', sy).group(1)
        dl = os.path.join(os.path.dirname(sy), "delinks.txt")
        rng = [(int(x, 16), int(y, 16)) for x, y in
               re.findall(r'(?m)^\s*\.(?:text|init) start:0x([0-9a-fA-F]+) end:0x([0-9a-fA-F]+)\s*$', open(dl).read())] if os.path.exists(dl) else []
        for m in re.finditer(r'^\S+ kind:function\((?:arm|thumb),size=0x[0-9a-fA-F]+\) addr:0x([0-9a-fA-F]+)',
                             open(sy, encoding='utf-8', errors='ignore').read(), re.M):
            ad = f"{int(m.group(1), 16):08x}"
            if any(s <= int(ad, 16) < e for s, e in rng) or ad in skip:
                continue
            try:
                r = scaffold(mod, ad)
            except Exception:
                continue
            if r:
                open(f"{out}/{ad}.cpp", 'w', encoding='utf-8').write(r[0]); n += 1
                if n % 250 == 0:
                    print(f"  {n}...", flush=True)
    print(f"scaffolded {n} remaining functions -> {out}/")
elif sys.argv[1] == "--wave":
    mod = sys.argv[2]
    d = f"{SP}/wave_main" if mod == "main" else f"{SP}/wave_ov{mod}"
    out = f"{SP}/scaffold"; os.makedirs(out, exist_ok=True)
    n = 0
    for bf in glob.glob(f"{d}/[bLN]*.txt"):
        for line in open(bf):
            ad = line.split('\t')[0].strip()
            if not re.fullmatch(r'[0-9a-fA-F]{8}', ad):
                continue
            r = scaffold(mod, ad)
            if r:
                open(f"{out}/{ad}.cpp", 'w', encoding='utf-8').write(r[0]); n += 1
    print(f"scaffolded {n} functions -> {out}/")
else:
    mod, addr = sys.argv[1], sys.argv[2]
    r = scaffold(mod, addr)
    if not r:
        sys.exit("no such function")
    if len(sys.argv) > 3:
        open(sys.argv[3], 'w', encoding='utf-8').write(r[0]); print(f"wrote {sys.argv[3]} ({r[1]} refs resolved)")
    else:
        print(r[0])
