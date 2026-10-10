#!/usr/bin/env python
"""ZERO-TOKEN synthesiser for trivial accessor functions. No model involved.

Game code is full of one- and two-instruction getters/setters. They currently consume a full worker
slot each, competing with genuinely hard functions, when their C form is a pure function of the
disassembly:

    str r1,[r0,#0x10c] ; bx lr   ->  *(int*)((char*)p + 0x10c) = v;
    ldr r0,[r0,#0x18c] ; bx lr   ->  return *(int*)((char*)p + 0x18c);
    add r0,r0,#0x118   ; bx lr   ->  return (char*)p + 0x118;
    mov r1,#0 ; str r1,[r0] ; bx lr -> *(int*)p = 0;

Emit the C, run the REAL gate, keep it only on MATCH. A wrong guess costs one compile and is discarded,
so this can never introduce a bad match — wgate is the same check integration uses.

Usage: python synth.py <module> <addr>      -> prints MATCH/NO and writes the .cpp on success
       python synth.py --sweep [maxsize]    -> try every unmatched function up to maxsize (default 32)
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

# width -> (C cast, signedness handled by the load kind)
LOADS = {'ldr': 'int', 'ldrh': 'unsigned short', 'ldrb': 'unsigned char',
         'ldrsh': 'short', 'ldrsb': 'signed char'}
STORES = {'str': 'int', 'strh': 'unsigned short', 'strb': 'unsigned char'}


def insns(mod, addr):
    if mod == "main":
        cfg, binp, base = "config/usa/arm9", "extract/usa/arm9/arm9.bin", 0x02000000
    else:
        cfg = f"config/usa/arm9/overlays/ov{mod}"
        binp = f"extract/usa/arm9_overlays/ov{mod}.bin"
        d = open(f"{REPO}/{cfg}/delinks.txt").read()
        base = min(int(x, 16) for x in re.findall(r'start:0x([0-9a-fA-F]+)', d))
    sym = open(f"{REPO}/{cfg}/symbols.txt").read()
    m = re.search(r'^(\S+) kind:function\((arm|thumb),size=0x([0-9a-fA-F]+)\) addr:0x0*%s\b'
                  % addr.lstrip('0'), sym, re.M | re.I)
    if not m or m.group(2) != 'arm':
        return None, None, None
    sz = int(m.group(3), 16)
    a = int(addr, 16)
    blob = open(f"{REPO}/{binp}", "rb").read()
    code = blob[a - base:a - base + sz]
    return [(i.mnemonic, i.op_str) for i in md.disasm(code, a)], sz, m.group(1)


OFF = re.compile(r'^r0, \[r0(?:, #(-?(?:0x)?[0-9a-fA-F]+))?\]$')
OFFW = re.compile(r'^r(\d), \[r0(?:, #(-?(?:0x)?[0-9a-fA-F]+))?\]$')
IMM = re.compile(r'^r(\d), #(-?(?:0x)?[0-9a-fA-F]+)$')
ADDI = re.compile(r'^r0, r0, #(-?(?:0x)?[0-9a-fA-F]+)$')


def synth(ins, name, tag):
    """Return a C++ body, or None if the shape is not one we can derive."""
    if not ins or ins[-1][0] not in ('bx', 'b') or ins[-1][1] not in ('lr',):
        return None
    b = ins[:-1]
    S = lambda x: int(x, 0) if x else 0

    if len(b) == 0:                                              # bx lr
        return f"ARM void {name}(void) {{\n}}\n"
    if len(b) == 1:
        mn, op = b[0]
        m = OFF.match(op)
        if mn in LOADS and m:                                    # getter
            t = LOADS[mn]; o = S(m.group(1))
            return (f"ARM {t} {name}(void* p) {{\n"
                    f"    return *({t}*)((char*)p + {hex(o)});\n}}\n")
        m = OFFW.match(op)
        if mn in STORES and m and m.group(1) == '1':             # setter
            t = STORES[mn]; o = S(m.group(2))
            return (f"ARM void {name}(void* p, {t} v) {{\n"
                    f"    *({t}*)((char*)p + {hex(o)}) = v;\n}}\n")
        m = ADDI.match(op)
        if mn == 'add' and m:                                    # pointer bump
            return (f"ARM void* {name}(void* p) {{\n"
                    f"    return (char*)p + {hex(S(m.group(1)))};\n}}\n")
        m = IMM.match(op)
        if mn == 'mov' and m and m.group(1) == '0':              # constant return
            return f"ARM int {name}(void) {{\n    return {hex(S(m.group(2)))};\n}}\n"
    if len(b) == 2:
        (n0, o0), (n1, o1) = b
        # chained load: obj->field->field  (`ldr r0,[r0,#A] ; ldr r0,[r0,#B] ; bx lr`)
        a0, a1 = OFF.match(o0), OFF.match(o1)
        if n0 in LOADS and n1 in LOADS and a0 and a1:
            t = LOADS[n1]
            return (f"ARM {t} {name}(void* p) {{\n"
                    f"    return *({t}*)(*(char**)((char*)p + {hex(S(a0.group(1)))}) + {hex(S(a1.group(1)))});\n}}\n")
        # load then bump: returns a pointer into a sub-object
        m1 = ADDI.match(o1)
        if n0 in LOADS and a0 and n1 == 'add' and m1:
            return (f"ARM void* {name}(void* p) {{\n"
                    f"    return *(char**)((char*)p + {hex(S(a0.group(1)))}) + {hex(S(m1.group(1)))};\n}}\n")
        # bump then load: reads a field of an embedded struct
        m0 = ADDI.match(o0)
        if n0 == 'add' and m0 and n1 in LOADS and a1:
            t = LOADS[n1]
            return (f"ARM {t} {name}(void* p) {{\n"
                    f"    return *({t}*)((char*)p + {hex(S(m0.group(1)) + S(a1.group(1)))});\n}}\n")
        # store an immediate: `mov rN,#K ; str rN,[r0,#off]`
        imm, dst = IMM.match(o0), OFFW.match(o1)
        if n0 == 'mov' and imm and n1 in STORES and dst and imm.group(1) == dst.group(1):
            t = STORES[n1]
            return (f"ARM void {name}(void* p) {{\n"
                    f"    *({t}*)((char*)p + {hex(S(dst.group(2)))}) = {hex(S(imm.group(2)))};\n}}\n")
        # two-field setter: `str r1,[r0,#a] ; str r2,[r0,#b]`
        st1, st2 = OFFW.match(o0), OFFW.match(o1)
        if n0 in STORES and n1 in STORES and st1 and st2 \
                and st1.group(1) == '1' and st2.group(1) == '2':
            t0, t1 = STORES[n0], STORES[n1]
            return (f"ARM void {name}(void* p, {t0} a, {t1} b) {{\n"
                    f"    *({t0}*)((char*)p + {hex(S(st1.group(2)))}) = a;\n"
                    f"    *({t1}*)((char*)p + {hex(S(st2.group(2)))}) = b;\n}}\n")
    return None


def attempt(mod, addr, quiet=False):
    ins, sz, sym = insns(mod, addr)
    if ins is None:
        return None
    pfx = "func_" if mod == "main" else f"func_ov{mod}_"
    name = f"Synth_{addr}"
    body = synth(ins, name, addr)
    if not body:
        return None
    src = (f"#include <globaldefs.h>\n\n// USA: {pfx}{addr}  (semantic: {name})\n"
           f'extern "C" {body}')
    p = f"{SP}/lab/synth_{addr}.cpp"
    open(p, 'w', encoding='utf-8').write(src)
    # THE NAME IS NOT OURS TO INVENT. This emitted `extern "C" Synth_<addr>` unconditionally, so any
    # address whose config carries a real symbol -- most of them, including every mangled C++ name --
    # came back WRONG-SYMBOL with ZERO BYTES DIFFERING, and the file was then deleted. The bodies had
    # been right the whole time. autorepair already reconciles a definition against the config symbol
    # and is the same pass integration runs; measured on 020c7b38 and 020c7ca8, both go straight to
    # MATCH after it.
    try:
        sys.path.insert(0, KIT)
        import autorepair
        autorepair.repair(p, mod, addr)
    except Exception:
        pass
    r = subprocess.run([sys.executable, f"{KIT}/wgate.py", mod, addr, p], capture_output=True, text=True)
    if (r.stdout + r.stderr).strip().startswith("MATCH"):
        d = f"{SP}/hold_main" if mod == "main" else f"{SP}/hold_ov{mod}"
        os.makedirs(d, exist_ok=True)
        open(f"{d}/Synth_{addr}.cpp", 'w', encoding='utf-8').write(src)
        return True
    # KEEP A NEAR MISS. This used to `os.remove(p)` anything short of an instant match, throwing
    # away a compilable, correctly-declared body that a colorsweep pass can finish -- the rules
    # cannot run at all on a function with no artifact, so discarding this left cold addresses with
    # nothing for automation to work on and sent every one of them to a paid session. presweep.py
    # picks the file up from here.
    return False


if sys.argv[1] == "--sweep":
    maxsz = int(sys.argv[2]) if len(sys.argv) > 2 else 32
    skip = set()
    for f in ("skiplist_ov.txt", "skiplist_main.txt"):
        q = f"{KIT}/{f}"
        if os.path.exists(q):
            skip |= {l.split()[0].lower() for l in open(q) if l.strip()}
    tried = hit = 0
    for sy in glob.glob(f"{REPO}/config/usa/arm9/overlays/ov*/symbols.txt") + [f"{REPO}/config/usa/arm9/symbols.txt"]:
        mod = "main" if sy.endswith("arm9/symbols.txt") else re.search(r'ov(\d+)', sy).group(1)
        d = os.path.join(os.path.dirname(sy), "delinks.txt")
        # ANCHOR THE RANGE REGEX. Unanchored, it matched COMMENTED-OUT delinks too
        # (`//    .text start:0x020c75b4 end:0x020c7d34`, 1920 bytes marked "complete" and disabled),
        # so every address inside a commented block was treated as already matched and skipped. That
        # is why the sweep reported "0 shapes recognised" while holding two provable matches inside
        # one such block. claim.py anchors for exactly this reason; match it.
        rng = [(int(a, 16), int(b, 16)) for a, b in
               re.findall(r'(?m)^\s*\.(?:text|init) start:0x([0-9a-fA-F]+) end:0x([0-9a-fA-F]+)\s*$',
                          open(d).read())] if os.path.exists(d) else []
        for m in re.finditer(r'^\S+ kind:function\(arm,size=0x([0-9a-fA-F]+)\) addr:0x([0-9a-fA-F]+)',
                             open(sy).read(), re.M):
            sz = int(m.group(1), 16); a = int(m.group(2), 16); ah = f"{a:08x}"
            if sz > maxsz or any(s <= a < e for s, e in rng) or ah in skip:
                continue
            r = attempt(mod, ah)
            if r is None:
                continue
            tried += 1
            if r:
                hit += 1
                print(f"  MATCH {ah} ov{mod} ({sz}B)")
    print(f"synth sweep: {hit} matched / {tried} shapes recognised")
else:
    print("MATCH" if attempt(sys.argv[1], sys.argv[2]) else "no")
