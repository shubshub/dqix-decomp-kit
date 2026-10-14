#!/usr/bin/env python
"""Compact DECODED diff for the iteration loop: only the diverging instructions, side by side.

WHY THIS EXISTS. Cost of matching a large function is roughly (context size) x (iteration count), and
for the large tier both terms are bad: a 2KB function is ~500 instructions, and a worker that dumps
that disassembly once carries ~30k tokens in context for every one of the next 20-40 gate cycles. That
single decision dominates the bill -- measured 08-11, worker context averaged 226k tokens per message
and 83.6% of it was tool output.

wgate says WHICH bytes differ. wdiag prints the raw words. Neither tells you what changed, so the
worker re-disassembles to find out -- and that is the expensive habit. This prints exactly the diverging
instructions, decoded, target vs yours, with one instruction of context either side. A typical
mid-iteration diff comes out at 5-15 lines instead of a 500-line dump.

Usage: python wdiff.py <OV|main> <addr> <file.cpp> [section=.text]
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import subprocess, re, sys, os

from elftools.elf.elffile import ELFFile

import residue
import buildcfg

REPO = _kp.REPO
# PID-isolated compiler scratch belongs to external state.
SCR = f"{_kp.SP}/handwork/compile"
os.makedirs(SCR, exist_ok=True)
CC = buildcfg.cc_path(os.environ.get("MWCC"))
FLAGS = list(buildcfg.FLAGS)
# Per-file flag experiments: a real build can compile one file with different optimisation
# settings, and that changes register allocation without changing the source. Set
# WDIFF_FLAGS to replace or extend the default set when probing such a case.
_extra = os.environ.get("WDIFF_FLAGS", "")
if _extra:
    FLAGS = FLAGS + _extra.split()
os.chdir(REPO)

OV, ADDR, SRC = sys.argv[1], sys.argv[2], sys.argv[3]
# Section: auto-detect from the delinks section table when not given. A function that
# lives in .init emits no .text at all, and a hardcoded ".text" then reports
# "no .text emitted" for a file that is actually byte-exact -- wgate.py had this fix,
# this copy did not, and workers were told correct sources were wrong.
SEC = sys.argv[4] if len(sys.argv) > 4 else None
CTX = int(os.environ.get("WDIFF_CTX", "1"))       # instructions of context around each diverging run
MAXRUNS = int(os.environ.get("WDIFF_MAXRUNS", "12"))


def fail(msg):
    print(msg)
    sys.exit(1)


if OV == "main":
    CFG = buildcfg.config_dir("main")
    PRISTINE = open(buildcfg.pristine("main"), "rb").read()
    PFX = "func_"
    BASE = 0x02000000
else:
    CFG = buildcfg.config_dir(OV)
    PRISTINE = open(buildcfg.pristine(OV), "rb").read()
    PFX = f"func_ov{OV}_"
    BASE = min(int(m, 16) for m in
               re.findall(r'start:0x([0-9a-fA-F]+)', open(f"{CFG}/delinks.txt").read()))

symtxt = open(f"{CFG}/symbols.txt", encoding='utf-8', errors='ignore').read()
m = re.search(rf'{PFX}{ADDR} kind:function\((arm|thumb),size=0x([0-9a-fA-F]+)\)', symtxt, re.I)
if not m:
    # Fall back to an ADDRESS lookup. Once a function matches it is RENAMED, so `func_ov..._<addr>`
    # stops existing -- which is fine for the worker loop (its targets are unmatched by definition)
    # but makes the tool untestable against known-good source. The size is in the symbol either way.
    m = re.search(r'\S+ kind:function\((arm|thumb),size=0x([0-9a-fA-F]+)\) addr:0x0*%s' % ADDR.lstrip('0'),
                  symtxt, re.I)
if not m:
    fail(f"NO-SLOT: nothing at {ADDR} in {CFG}/symbols.txt")
ISA, slot = m.group(1).lower(), int(m.group(2), 16)

if SEC is None:
    # The delinks section table lists every code section and its address range; pick the
    # one containing this function so .init functions are compared against .init.
    _head = open(f"{CFG}/delinks.txt", encoding="utf-8").read().split(chr(10) + chr(10))[0]
    _av = int(ADDR, 16)
    _hit = next((mm.group(1) for mm in
                 re.finditer(r'\.(\w+)\s+start:0x([0-9a-fA-F]+) end:0x([0-9a-fA-F]+) kind:code', _head)
                 if int(mm.group(2), 16) <= _av < int(mm.group(3), 16)), None)
    SEC = '.' + _hit if _hit else ".text"


_OBJ = f"{SCR}/wdiff_{os.getpid()}.o"
r = subprocess.run(buildcfg.tool_command(CC) + FLAGS + ["-c", SRC, "-o", _OBJ], capture_output=True, text=True)
if r.returncode != 0:
    fail("COMPILE-FAIL: " + (r.stdout + r.stderr)[-600:])
buildcfg.postcompile(_OBJ)
try:
    import io
    with open(_OBJ, "rb") as _fh:
        elf = ELFFile(io.BytesIO(_fh.read()))
    _texts = buildcfg.sections(elf, SEC)
    texts = [s for _, s in _texts]
    if not texts:
        fail(f"SIZE/OVERGEN: no {SEC} emitted")
    total = sum(s['sh_size'] for s in texts)

    # Reloc-masked, exactly as the real gate does it: bl targets and pool words are linker-resolved, so
    # their bytes are meaningless here and a diff on them is noise, not a mismatch.
    THM_BR = {10, 25, 30, 31}
    reloc = set()
    for sec in buildcfg.relocations(elf, _texts[0][0]):
        for rr in sec.iter_relocations():
            o = rr['r_offset']
            if rr['r_info_type'] in THM_BR:
                reloc.update(range(o, o + 4))
            else:
                reloc.update(range(o & ~3, (o & ~3) + 4))

    a = int(ADDR, 16)
    mine = texts[0].data()[:slot]
    orig = PRISTINE[a - BASE:a - BASE + slot]
finally:
    try:
        os.remove(_OBJ)
    except OSError:
        pass

if total != slot:
    print(f"SIZE/OVERGEN: emitted {SEC} total=0x{total:x} but slot=0x{slot:x} "
          f"({'too short' if total < slot else 'too long — define ONLY this function'})")

diffs = [i for i in range(min(len(mine), len(orig))) if i not in reloc and mine[i] != orig[i]]
if not diffs:
    print("MATCH")
    sys.exit(0)

step = 2 if ISA == 'thumb' else 4


def decode(buf):
    """offset -> text; disassembly RESUMES after an undecodable word (a mid-body literal pool)."""
    return residue.decode(buf, ISA)


do, dm = decode(orig), decode(mine)

# collapse diverging bytes into instruction-aligned runs so a 6-byte diff is one entry, not six
bad = sorted({(i // step) * step for i in diffs})
runs = []
for o in bad:
    if runs and o - runs[-1][1] <= step * (CTX + 1):
        runs[-1][1] = o
    else:
        runs.append([o, o])

print(f"BYTEDIFF {len(diffs)} bytes in {len(runs)} run(s), slot=0x{slot:x} isa={ISA}")
for s, e in runs[:MAXRUNS]:
    lo, hi = max(0, s - step * CTX), min(len(orig) - step, e + step * CTX)
    for o in range(lo, hi + step, step):
        mark = "*" if o in bad else " "
        t, y = do.get(o, "?"), dm.get(o, "?")
        print(f" {mark}0x{o:04x}  {t:<34}| {y}" if t != y else f" {mark}0x{o:04x}  {t}")
if len(runs) > MAXRUNS:
    print(f" ... {len(runs)-MAXRUNS} more run(s) suppressed (raise WDIFF_MAXRUNS)")
print("legend: '*' = differs, left = TARGET | right = yours (reloc bytes already masked)")

# ---- AUTO-DIAGNOSIS (classification lives in residue.py, shared with wgate and blocker) -------
# The router used to live only in the worker doc, so after every gate a worker had to recall it from
# ~4k of prose and apply it correctly. Measured 08-11: 11 workers read func_ov027_021dc680 as register
# colouring and burned 10-15 variations each on declaration-order levers that were later PROVEN inert,
# because the real difference was a loop-shape change. The tell -- mismatched branch targets -- was in
# every one of those diffs. Decide it here, where the evidence already is.
_cls, _metric, _detail, _hint = residue.classify(orig, mine, ISA, reloc, slot)

print()
print("DIAGNOSIS: %s%s" % (_cls, ("  " + _detail) if _detail else ""))
if _cls == "LOOP-SHAPE":
    print("  BRANCH TARGETS DIFFER -> loop shape, NOT register colouring.")
    print("  No declaration/definition-order lever can fix it. Compare which instructions sit INSIDE")
    print("  the loop. Usual cause: the target hoists a field load OUT of the loop and keeps it in a")
    print("  register, while your source re-reads obj->field each iteration so the load is pulled IN.")
    print("  Fix: T* p = obj->field;  do { ... p++; obj->field = p; } while (...);")
elif _cls == "REGPERM":
    print("  only REGISTER NUMBERS differ, every mnemonic and branch matches -> COLOURABLE.")
    print("  " + _hint)
    print("  Run: python colorsweep.py <mod> <addr> <your.cpp> --apply")
elif _cls == "SCHED":
    print("  the same instructions in a DIFFERENT ORDER -> statement/definition order, not colouring.")
    print("  Move the definition, do not renumber registers. colorsweep r5/r16 generate these moves.")
elif _cls == "SHAPE":
    print("  MNEMONICS differ -> the C construct itself is wrong, not the register assignment.")
    print("  Route by the differing instruction: predication (#3), offset chain (#4), bitfield (#7),")
    print("  bool-return (#8), volatile (#10). Do not permute declarations.")
elif _cls in ("OVERGEN", "UNDERGEN"):
    print("  wrong SIZE -> no colouring rewrite can close it while the length is wrong.")
else:
    print("  operands differ with matching mnemonics -> check immediates/offsets first (#4).")

