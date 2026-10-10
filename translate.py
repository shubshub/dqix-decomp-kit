#!/usr/bin/env python
"""ZERO-TOKEN literal translator: ARM -> compilable C, one instruction at a time.

This does NOT try to be pretty. It emits a register-machine transliteration with `goto` mirroring the
exact branch graph, because mwcc compiles goto-structured C close to literally. Two outcomes, both
useful and neither costing a model token:

  * it GATES MATCH  -> the function is done, nobody was paid for it
  * it does not     -> the worker gets a compiling DRAFT with the real control flow, field accesses
                       and calls already in place, instead of an empty TODO

Every draft is verified by the real gate before it is staged, so a wrong translation can never enter
the build — it just gets thrown away.

The translator is meant to be improved: each new instruction form or idiom it learns is re-run over the
whole remaining pool, so coverage only grows. `wgate.py` gives an instant, objective score on ~4000
real cases, so improvement is empirical rather than guesswork.

Usage: python translate.py <module> <addr> [--stage]
       python translate.py --sweep [maxinsn] [--nocall]
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import re, sys, os, glob, subprocess
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM

SP = _kp.SP
KIT = _kp.KIT
REPO = _kp.REPO
md = Cs(CS_ARCH_ARM, CS_MODE_ARM)

LD = {'ldr': ('unsigned int', 'u32'), 'ldrh': ('unsigned short', 'u16'), 'ldrb': ('unsigned char', 'u8'),
      'ldrsh': ('short', 's16'), 'ldrsb': ('signed char', 's8')}
ST = {'str': 'unsigned int', 'strh': 'unsigned short', 'strb': 'unsigned char'}
ALU = {'add': '+', 'sub': '-', 'and': '&', 'orr': '|', 'eor': '^', 'mul': '*',
       'lsl': '<<', 'lsr': '>>', 'asr': '>>'}
CC = {'eq': '==', 'ne': '!=', 'lt': '<', 'gt': '>', 'le': '<=', 'ge': '>=',
      'lo': '<', 'hi': '>', 'ls': '<=', 'hs': '>='}
REGS = [f'r{i}' for i in range(13)]


def norm(r):
    return {'sb': 'r9', 'sl': 'r10', 'fp': 'r11', 'ip': 'r12'}.get(r, r)


_SYMS = {}


def symmap():
    """addr -> name over EVERY module, the same map wlist.py builds.

    A `bl` whose callee stays anonymous makes the whole draft unusable: attempt() discards any
    translation still carrying a call marker, so one unresolved call threw away the entire
    transliteration of a 10KB function.
    """
    if _SYMS:
        return _SYMS
    for c in (glob.glob(f"{REPO}/config/usa/arm9/symbols.txt")
              + glob.glob(f"{REPO}/config/usa/arm9/overlays/ov*/symbols.txt")):
        for m in re.finditer(r'^(\S+) kind:function\([^)]*\) addr:0x([0-9a-fA-F]+)',
                             open(c, encoding='utf-8', errors='ignore').read(), re.M):
            _SYMS.setdefault(int(m.group(2), 16), m.group(1))
    return _SYMS


def load(mod, addr):
    if mod == "main":
        cfg, binp, base = "config/usa/arm9", "extract/usa/arm9/arm9.bin", 0x02000000
    else:
        cfg = f"config/usa/arm9/overlays/ov{mod}"
        binp = f"extract/usa/arm9_overlays/ov{mod}.bin"
        d = open(f"{REPO}/{cfg}/delinks.txt").read()
        base = min(int(x, 16) for x in re.findall(r'start:0x([0-9a-fA-F]+)', d))
    sym = open(f"{REPO}/{cfg}/symbols.txt", encoding='utf-8', errors='ignore').read()
    m = re.search(r'^\S+ kind:function\((arm|thumb),size=0x([0-9a-fA-F]+)\) addr:0x0*%s\b'
                  % addr.lstrip('0'), sym, re.M | re.I)
    if not m or m.group(1) != 'arm':
        return None
    sz = int(m.group(2), 16); a = int(addr, 16)
    blob = open(f"{REPO}/{binp}", "rb").read()
    # The blob and its base go back with the instructions: resolving a PC-relative literal needs to
    # read the pool word, and it is the caller that emits the code.
    return list(md.disasm(blob[a - base:a - base + sz], a)), blob, base


def operand(tok):
    """`#0x10` -> 0x10 ; `r3` -> r3 ; `r3, lsl #2` -> (r3 << 2)"""
    tok = tok.strip()
    m = re.match(r'^#(-?(?:0x)?[0-9a-fA-F]+)$', tok)
    if m:
        return hex(int(m.group(1), 0) & 0xFFFFFFFF)
    m = re.match(r'^(\w+), (lsl|lsr|asr) #(\d+)$', tok)
    if m:
        return f"({norm(m.group(1))} {'<<' if m.group(2) == 'lsl' else '>>'} {m.group(3)})"
    # REGISTER-SHIFTED OPERAND: `r5, lsl r4`. Only the immediate form was handled, so a single
    # `orr r2, r2, r5, lsl r4` was the last untranslated instruction in a 2551-instruction function.
    m = re.match(r'^(\w+), (lsl|lsr|asr) (\w+)$', tok)
    if m and norm(m.group(3)) in REGS:
        return f"({norm(m.group(1))} {'<<' if m.group(2) == 'lsl' else '>>'} {norm(m.group(3))})"
    return norm(tok) if norm(tok) in REGS else None


MEMOP = re.compile(r'^(\w+), \[(\w+)(?:, #(-?(?:0x)?[0-9a-fA-F]+))?\]$')
MEMREG = re.compile(r'^(\w+), \[(\w+), (\w+)(?:, (lsl|lsr) #(\d+))?\]$')


def translate(mod, addr):
    loaded = load(mod, addr)
    if not loaded:
        return None
    ins, blob, base = loaded
    if not ins:
        return None
    lo = ins[0].address
    targets = set()
    for i in ins:
        if i.mnemonic.startswith('b') and not i.mnemonic.startswith(('bl', 'bx')):
            m = re.match(r'^#(0x[0-9a-fA-F]+)$', i.op_str)
            if m and lo <= int(m.group(1), 16) <= ins[-1].address:
                targets.add(int(m.group(1), 16))
    body, used, callees = [], set(), {}

    def emit(s, cond=None):
        body.append(("    " * 1) + (f"if ({cond}) {{ {s} }}" if cond else s))

    for i in ins:
        if i.address in targets:
            body.append(f"L{i.address - lo:x}:;")
        mn, op = i.mnemonic, i.op_str
        cond = None
        for suf, cop in CC.items():
            if mn.endswith(suf) and len(mn) > len(suf) and mn[:-len(suf)] in (
                    list(ALU) + ['mov', 'mvn', 'ldr', 'str', 'cmp', 'b', 'ldrh', 'ldrb', 'strh', 'strb', 'pop']):
                cond, mn = f"cc {cop} 0", mn[:-len(suf)]
                break
        if mn in ('push', 'stmdb', 'pop', 'ldm', 'ldmia'):
            if 'pc' in op:
                emit("return r0;", cond)
            continue
        if mn == 'bx':
            emit("return r0;", cond)
            continue
        if mn == 'b':
            m = re.match(r'^#(0x[0-9a-fA-F]+)$', op)
            if m and int(m.group(1), 16) in targets:
                emit(f"goto L{int(m.group(1), 16) - lo:x};", cond)
            continue
        if mn == 'bl':
            m = re.match(r'^#(0x[0-9a-fA-F]+)$', op)
            nm = symmap().get(int(m.group(1), 16)) if m else None
            if nm:
                callees[nm] = True
                used.add('r0')
                emit(f"r0 = {nm}(r0, r1, r2, r3);", cond)
            else:
                emit("/* TODO call */", cond)
            continue
        if mn == 'cmp':
            a1, a2 = [x.strip() for x in op.split(',', 1)]
            l, r = operand(a1), operand(a2)
            if l and r:
                used.add('cc'); emit(f"cc = (int)({l}) - (int)({r});", cond)
            continue
        if mn == 'mov' or mn == 'mvn':
            a1, rest = op.split(',', 1)
            d, s = norm(a1.strip()), operand(rest)
            if d in REGS and s:
                used.add(d); emit(f"{d} = {'~' if mn == 'mvn' else ''}{s};", cond)
            continue
        if mn in LD or mn in ST:
            m = MEMOP.match(op)
            # A PC-RELATIVE LOAD IS A CONSTANT, NOT A MEMORY READ. Emitting it literally produced
            # `*(unsigned int*)((char*)pc + 0x4)` -- `pc` is no variable, so the whole function
            # COMPILE-FAILed. Literal pools appear in nearly every function, so this alone sank the
            # sweep. Resolve it against the ROM: pc = insn + 8, word-aligned.
            if m and norm(m.group(2)) == "pc" and mn in LD:
                pool = ((i.address + 8 + (int(m.group(3), 0) if m.group(3) else 0)) & ~3) - base
                if 0 <= pool + 4 <= len(blob):
                    val = int.from_bytes(blob[pool:pool + 4], "little")
                    rt = norm(m.group(1))
                    used.add(rt)
                    emit("%s = 0x%x;" % (rt, val), cond)
                    continue
            if m:
                rt, rn, off = norm(m.group(1)), norm(m.group(2)), int(m.group(3), 0) if m.group(3) else 0
                ct = LD[mn][0] if mn in LD else ST[mn]
                mem = f"*({ct}*)((char*){rn} + {hex(off)})"
                if mn in LD:
                    used.add(rt); emit(f"{rt} = {mem};", cond)
                else:
                    emit(f"{mem} = ({ct}){rt};", cond)
                continue
            m = MEMREG.match(op)
            if m:
                rt, rn, rm = norm(m.group(1)), norm(m.group(2)), norm(m.group(3))
                sh = f" << {m.group(5)}" if m.group(4) == 'lsl' else (f" >> {m.group(5)}" if m.group(4) else "")
                ct = LD[mn][0] if mn in LD else ST[mn]
                mem = f"*({ct}*)((char*){rn} + ({rm}{sh}))"
                if mn in LD:
                    used.add(rt); emit(f"{rt} = {mem};", cond)
                else:
                    emit(f"{mem} = ({ct}){rt};", cond)
                continue
            continue
        if mn in ALU:
            parts = [x.strip() for x in op.split(',')]
            if len(parts) >= 3 and mn in ('lsl', 'lsr', 'asr'):
                d, s, sh = norm(parts[0]), operand(parts[1]), operand(parts[2])
                if d in REGS and s and sh:
                    used.add(d); emit(f"{d} = {s} {ALU[mn]} {sh};", cond)
                continue
            if len(parts) == 3:
                d, s1 = norm(parts[0]), operand(parts[1])
                s2 = operand(', '.join(parts[2:]))
                if d in REGS and s1 and s2:
                    used.add(d); emit(f"{d} = {s1} {ALU[mn]} {s2};", cond)
                continue
            if len(parts) == 2 and mn in ('lsl', 'lsr', 'asr'):
                d, s = norm(parts[0]), operand(parts[1])
                if d in REGS and s:
                    used.add(d); emit(f"{d} = {d} {ALU[mn]} {s};", cond)
            continue
    if not body:
        return None
    # ARM's named registers (sp, lr, ip, fp, sl) have no digit after the 'r', and `int(r[1:])`
    # raised ValueError on the first function using one -- which is nearly all of them, so the
    # sweep died on its first candidate every time it was run.
    _RNUM = {"sp": 13, "lr": 14, "pc": 15, "ip": 12, "fp": 11, "sl": 10}
    decls = sorted(used - {'cc'},
                   key=lambda r: _RNUM.get(r, int(r[1:]) if r[1:].isdigit() else 99))
    pfx = "func_" if mod == "main" else f"func_ov{mod}_"
    src = ["#include <globaldefs.h>", "", f"// USA: {pfx}{addr}"]
    # every callee at the widest arity: a mangled name stays linkable because extern "C" takes the
    # mangled spelling verbatim as the identifier
    src += [f'extern "C" unsigned int {c}(unsigned int, unsigned int, unsigned int, unsigned int);'
            for c in sorted(callees)]
    src += ["",
           f'extern "C" ARM unsigned int Trans_{addr}(unsigned int r0, unsigned int r1, '
           f'unsigned int r2, unsigned int r3) {{']
    if 'cc' in used:
        src.append("    int cc = 0;")
    extra = [r for r in decls if r not in ('r0', 'r1', 'r2', 'r3', 'sp')]
    if extra:
        src.append("    unsigned int " + ", ".join(f"{r} = 0" for r in extra) + ";")
    if re.search(r'\bsp\b', "\n".join(body)):
        # the whole frame is ONE object: mwcc lays a function's stack out as a block, so a draft
        # that declares each slot separately cannot reproduce the ROM's offsets
        frame = 0
        for i in ins:
            if i.mnemonic == 'sub':
                m = re.match(r'^sp, sp, #(\d+|0x[0-9a-fA-F]+)$', i.op_str)
                if m:
                    frame += int(m.group(1), 0)
        src.append(f"    unsigned char stk[{frame or 0x400}];")
        src.append("    unsigned int sp = (unsigned int)stk;")
    src += body + ["    return r0;", "}"]
    return "\n".join(src) + "\n"


def attempt(mod, addr, stage=False):
    src = translate(mod, addr)
    if not src or "/* TODO call */" in src:
        return None
    p = f"{SP}/lab/trans_{addr}.cpp"
    open(p, 'w', encoding='utf-8').write(src)
    r = subprocess.run([sys.executable, f"{KIT}/wgate.py", mod, addr, p], capture_output=True, text=True)
    o = (r.stdout + r.stderr).strip()
    if o.startswith("MATCH"):
        if stage:
            d = f"{SP}/hold_main" if mod == "main" else f"{SP}/hold_ov{mod}"
            os.makedirs(d, exist_ok=True)
            open(f"{d}/Trans_{addr}.cpp", 'w', encoding='utf-8').write(src)
        return "MATCH"
    m = re.search(r'BYTEDIFF: (\d+) bytes', o)
    return f"diff {m.group(1)}" if m else o.split(':')[0][:24]


if __name__ == "__main__":
    if sys.argv[1] == "--sweep":
        maxi = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 40
        skip = set()
        for f in ("skiplist_ov.txt", "skiplist_main.txt"):
            q = f"{KIT}/{f}"
            if os.path.exists(q):
                skip |= {l.split()[0].lower() for l in open(q) if l.strip()}
        hit = tried = 0
        for sy in glob.glob(f"{REPO}/config/usa/arm9/overlays/ov*/symbols.txt") + [f"{REPO}/config/usa/arm9/symbols.txt"]:
            mod = "main" if sy.endswith("arm9/symbols.txt") else re.search(r'ov(\d+)', sy).group(1)
            dl = os.path.join(os.path.dirname(sy), "delinks.txt")
            rng = [(int(x, 16), int(y, 16)) for x, y in
                   re.findall(r'(?m)^\s*\.(?:text|init) start:0x([0-9a-fA-F]+) end:0x([0-9a-fA-F]+)\s*$', open(dl).read())] if os.path.exists(dl) else []
            for m in re.finditer(r'^\S+ kind:function\(arm,size=0x([0-9a-fA-F]+)\) addr:0x([0-9a-fA-F]+)',
                                 open(sy, encoding='utf-8', errors='ignore').read(), re.M):
                sz = int(m.group(1), 16); ad = f"{int(m.group(2), 16):08x}"
                if sz > maxi * 4 or any(s <= int(ad, 16) < e for s, e in rng) or ad in skip:
                    continue
                r = attempt(mod, ad, stage=True)
                if r is None:
                    continue
                tried += 1
                if r == "MATCH":
                    hit += 1
                    print(f"  MATCH {ad} ov{mod} ({sz}B)", flush=True)
        print(f"translate sweep: {hit} matched / {tried} attempted")
    else:
        print(attempt(sys.argv[1], sys.argv[2], stage="--stage" in sys.argv) or "not translatable")
