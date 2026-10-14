#!/usr/bin/env python3
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
# REAL per-function gate. Worker PASS criterion == integrator's check, so a PASS is guaranteed
# integrable. Usage: python wgate.py <OV> <addr> <src.cpp>
# Prints "MATCH" (exit 0) or the exact failure reason (exit 1): COMPILE / OVERGEN(size) /
# BYTEDIFF@offsets / UNDEF-SYM[...]. Reloc bytes (bl/pool .word) are masked, same as integration.
import subprocess, re, sys, os
from elftools.elf.elffile import ELFFile
import residue, gatelog, buildcfg
from pragmas import refused_asm
REPO = _kp.REPO
# Compiler scratch and proven candidates belong to external state, never the checkout.
SCR=f"{_kp.SP}/handwork/compile"
os.makedirs(SCR, exist_ok=True)
CC=buildcfg.CC

# Per-file compiler override (tools/cc_overrides.txt, same table the build reads).
# The table is EMPTY and must stay that way: an override is evidence the SOURCE is wrong,
# never a way to land a function. Keyed by the SOURCE FILE BASENAME so it works no matter
# which directory the gate is compiling the candidate from.
_CC_OVR = {}
try:
    for _l in open(f"{REPO}/tools/cc_overrides.txt", encoding="utf-8"):
        _l = _l.split("#")[0].split()
        if len(_l) == 2:
            _CC_OVR[_l[0].replace(chr(92), "/").split("/")[-1]] = _l[1]
except IOError:
    pass
def cc_for(path):
    import os as _os
    # MWCC=<ver>/<sub> wins over the table. Without this, gating a scratch COPY of an
    # overridden source silently used the project default and reported a nonsense BYTEDIFF
    # (3853 against the true 144 on 02061c04), because the table is keyed by basename.
    v = _os.environ.get("MWCC") or _CC_OVR.get(_os.path.basename(str(path)))
    return buildcfg.cc_path(v)

FLAGS=list(buildcfg.FLAGS)
# FLAGS ARE A LEVER TOO. The per-file compiler BUILD is overridable (tools/cc_overrides.txt) but the
# flag set never was, so "no C form reaches this" has always been measured on exactly one flag set.
# WGATE_FLAGS replaces a flag for one gate: `WGATE_FLAGS="-inline on"` or `-O3`. A later token wins,
# so a repeated flag overrides the default rather than conflicting with it.
_FLAG_OVR = {}
try:
    for _l in open(f"{REPO}/tools/cc_flag_overrides.txt", encoding="utf-8"):
        _l = _l.split("#")[0].split()
        if len(_l) >= 2:
            _FLAG_OVR[_l[0].replace(chr(92), "/").split("/")[-1]] = _l[1:]
except IOError:
    pass


def flags_for(path):
    """Per-file flags, same table tools/configure.py reads, keyed by basename like cc_for()."""
    return _FLAG_OVR.get(os.path.basename(str(path)), [])


_extra = os.environ.get("WGATE_FLAGS", "").split()
if _extra:
    FLAGS = FLAGS + _extra
os.chdir(REPO)
OV, ADDR, SRC = sys.argv[1], sys.argv[2], sys.argv[3]
# optional 4th arg: the SECTION this func belongs to (.text default, .init for static-initializer
# funcs). A correct .init function has NO .text at all, so measuring '.text' would report SIZE 0x0.
SEC = sys.argv[4] if len(sys.argv) > 4 else None   # None => auto-detect from the address below
_pragmas = [] if (os.environ.get("WGATE_ALLOW_COMMITTED") or os.environ.get("WGATE_ALLOW_PRAGMA")
                  or not os.path.isfile(SRC)) else \
    sorted(set(buildcfg.CODEGEN_PRAGMA.findall(open(SRC, encoding="utf-8", errors="replace").read())))
if _pragmas:
    print("RESIDUE PRAGMA 1 " + " ".join(_pragmas))
    print("PRAGMA: the ROM was built with every pass on, so a codegen #pragma is a diagnosis, never a "
          "match. Delete it and write the source that pass cannot reorder (core.md, PRAGMAS ARE A DIAGNOSIS).")
    sys.exit(1)
# AUTO-DETECT THE SECTION. Passing the wrong section makes a perfectly correct .init function
# report "0 .text total=0x0" and look broken; that cost a full integration pass. The delinks
# header lists every code section, so just look up which one contains this address.
# MODULE PARAMETER, NOT A FORK. `wgate_main.py` was a copy of an older wgate and immediately drifted:
# it never got the thumb fixes, so main's thumb funcs came back NO-SLOT and workers logged
# "gate has no thumb support". One code path, parameterized — same rule as ov_recover.py.
MAIN = (OV == "main")
if MAIN:
    CFG=buildcfg.config_dir("main")
    PRISTINE=open(buildcfg.pristine("main"),"rb").read()
    PFX="func_"
    BASE=0x02000000
else:
    CFG=buildcfg.config_dir(OV)
    PRISTINE=open(buildcfg.pristine(OV),"rb").read()
    PFX=f"func_ov{OV}_"
symtxt=open(f"{CFG}/symbols.txt").read()
delinks=open(f"{CFG}/delinks.txt").read()
if not MAIN:
    STARTS=[int(m,16) for m in re.findall(r'start:0x([0-9a-f]+)', delinks)]
    BASE=min(STARTS)
import glob
SYMSET=set(buildcfg.lcf_symbols())
for _p in glob.glob(f"{buildcfg.config_dir('main')}/**/symbols.txt", recursive=True):
    for _l in open(_p):
        if ' kind:' in _l: SYMSET.add(_l.split()[0])
_SESS = os.environ.get("WGATE_SESSION", "-")


def _gate_record(cls, metric=-1, detail="", hint=""):
    try:
        gatelog.record(OV, ADDR, _SESS, cls, metric, SRC)
        for line in gatelog.banner(gatelog.read(OV, ADDR), _SESS, ADDR, cls, metric):
            print(line)
    except OSError:
        pass


def fail(msg, cls="UNKNOWN", metric=-1, detail="", hint=""):
    _gate_record(cls, metric, detail, hint)
    print(("RESIDUE %s %d %s" % (cls, metric, detail)).rstrip())
    if hint: print("HINT: " + hint)
    print(msg)
    sys.exit(1)
# accept THUMB as well as ARM. Every genwave/classify/integrate regex used to demand `(arm,...)`,
# so the pipeline could not even SEE the 166 thumb functions, let alone gate one.
m=re.search(r'(?m)^\S+ kind:function\((arm|thumb),size=0x([0-9a-fA-F]+)\) addr:0x0*%s\b'
            % ADDR.lstrip('0'), symtxt, re.I)
if not m: fail(f"NO-SLOT: {PFX}{ADDR} not a function in symbols.txt")
ISA, slot = m.group(1), int(m.group(2),16)
if SEC is None:
    _sep = chr(10) + chr(10)
    _head = delinks[:delinks.index(_sep)] if _sep in delinks else delinks
    _av = int(ADDR, 16)
    _hit = next((mm.group(1) for mm in
                 re.finditer(r'\.(\w+)\s+start:0x([0-9a-fA-F]+) end:0x([0-9a-fA-F]+) kind:code', _head)
                 if int(mm.group(2), 16) <= _av < int(mm.group(3), 16)), None)
    SEC = "." + _hit if _hit else ".text"
# REJECT 0x in the function NAME — a `0x<hex>` token in a func name breaks dsd delinking
# (".rodata referenced but has not been written" -> symbol at 0x0 -> link-poison). One such name
# poisons the whole wave's integration. Force clean names at the source.
_txt=open(SRC,encoding='utf-8',errors='ignore').read()
# ALREADY COMMITTED IS NOT A FRESH MATCH. This compiles a file and compares bytes; it never asked
# whether that address is already delinked, so a source for work already in the repo reported MATCH
# exactly like new work. staging/ therefore filled with duplicates that looked like pending wins --
# 19 of 46 in one backlog -- and every wave re-copied and re-classified them. Report it distinctly so
# gate_staging can drop them. WGATE_ALLOW_COMMITTED=1 keeps the old behaviour for pipetest, which
# deliberately gates already-committed functions to prove the gate still works.
# Before compiling: a stale copy of a landed function logged NO-COMPILE for finished work.
if not os.environ.get("WGATE_ALLOW_COMMITTED"):
    _rngs = [(int(a, 16), int(b, 16)) for a, b in
             re.findall(r"(?m)^\s*\.(?:text|init) start:0x([0-9a-fA-F]+) end:0x([0-9a-fA-F]+)\s*$",
                        delinks)]
    if any(s <= int(ADDR, 16) < e for s, e in _rngs):
        _gate_record("ALREADY-COMMITTED", 0)
        print("RESIDUE ALREADY-COMMITTED 0")
        print("ALREADY-COMMITTED: this address is already delinked; the function is in the repo. "
              "Nothing to gate or integrate.")
        sys.exit(2)
    if refused_asm(ADDR, SRC, _txt):
        print("RESIDUE HAND-ASM 1")
        print("HAND-ASM: assembly reaches the bytes by definition, so it is not a match, and the "
              "integrator parks it. Write C, or emit BLOCKED with your best C file "
              "(core.md, NEVER WRITE HAND ASSEMBLY).")
        sys.exit(1)
_defm=re.search(r'\b(?:ARM|THUMB)\b[^\n(;{]*?\b([A-Za-z_]\w*)\s*\(', _txt)
# EXEMPT THE CONFIG'S OWN NAME. Some ROM symbols legitimately contain 0x (AccumulateFlag0x800-
# CombatantValue is committed and delinked; SubmitFlag0x800CombatantDataA0201fca0 is waiting).
# The rule exists to stop a WORKER inventing `func_..0x1234..`, not to forbid the name dsd itself
# demands -- and symbols.txt is binding, so a candidate that spells it correctly must pass.
# `in` rather than `==` because a C++ symbol embeds the identifier inside its mangling.
_cfgm = re.search(r'(?m)^(\S+)\s+kind:function\([^\n]*?addr:0x0*%s\b' % ADDR.lstrip('0'), symtxt)
_cfgnm = _cfgm.group(1) if _cfgm else ""
if _defm and re.search(r'0x[0-9a-fA-F]', _defm.group(1)) and _defm.group(1) not in _cfgnm:
    fail(f"BAD-NAME: function name '{_defm.group(1)}' contains a 0x token — breaks dsd delinking. "
         f"Rename with NO 0x (use decimal or drop the hex; keep the _{ADDR} suffix).")
# PER-PROCESS object path. This used to be a single shared "wg.o": with N workers gating concurrently,
# one worker's compile output could be read by another, giving false verdicts (mostly false BYTEDIFF, so
# workers abandoned functions that had actually matched). Unique per PID = no cross-talk.
_OBJ=f"{SCR}/wg_{os.getpid()}.o"
# DELETE IT ON THE WAY OUT, however we exit. Every gate wrote one and none were ever removed:
# 15,255 stale .o had accumulated (part of a 453MB scratchpad), and every pool glob in the pipeline
# walks that directory. atexit covers the fail() paths too, which all call sys.exit.
# The ELF handle MUST be closed first: Windows refuses to delete an open file, and an atexit that
# raises prints a traceback over the verdict a worker is parsing.
import atexit as _atexit
_ELFH = []                      # holds the open object-file handle so cleanup can close it


def _cleanup_obj():
    for _h in _ELFH:
        try:
            _h.close()
        except OSError:
            pass
    try:
        if os.path.exists(_OBJ):
            os.remove(_OBJ)
    except OSError:
        pass                    # a locked leftover is litter, never a reason to fail a gate


_atexit.register(_cleanup_obj)
# A `.s` GOES TO THE ASSEMBLER. Handing one to mwccarm reports a C syntax error on a line that is
# perfectly good assembly, so the eighteen secure-area stubs and every SDK routine that has no C
# form were ungateable through the one tool that decides what a match is.
if SRC.lower().endswith(".s"):
    _cmd=buildcfg.tool_command(buildcfg.AS)+buildcfg.AS_FLAGS+["-o",_OBJ,SRC]
else:
    _cmd=buildcfg.tool_command(cc_for(SRC))+FLAGS+flags_for(SRC)+["-c",SRC,"-o",_OBJ]
r=subprocess.run(_cmd,capture_output=True,text=True)
if r.returncode!=0:
    _err=(r.stdout+r.stderr).strip().replace("\t"," ")
    _first=next((l.strip() for l in _err.split("\n") if "rror" in l or "undefined" in l), "")[:120]
    fail("COMPILE-FAIL: "+_err[-400:], "NO-COMPILE", -1, _first)
buildcfg.postcompile(_OBJ)
_objfh=open(_OBJ,"rb"); _ELFH.append(_objfh)
elf=ELFFile(_objfh)
_texts=buildcfg.sections(elf,SEC)
texts=[s for _,s in _texts]
total=sum(s['sh_size'] for s in texts)
# a thumb function is padded to a 4-byte boundary by the LINKER, not mwcc, so the object's
# section can legitimately be slot-2 bytes.
ok_sizes = (slot, slot-2) if ISA=="thumb" else (slot,)
if len(texts)!=1 or total not in ok_sizes:
    _cls = "OVERGEN" if total > slot else "UNDERGEN"
    fail(f"SIZE/OVERGEN: {len(texts)} {SEC} total=0x{total:x} slot=0x{slot:x} (define ONLY this func; declare helpers extern; if genuinely shorter your codegen differs)",
         _cls, abs(total - slot), f"0x{total:x} vs slot 0x{slot:x}")
# mask by reloc TYPE. A thumb BL/BLX pair sits at a HALFWORD offset and spans 4 bytes from
# r_offset; the ARM `&~3` rounding leaves 2 of its bytes unmasked -> false BYTEDIFF.
THM_BR={10,25,30,31}          # R_ARM_THM_PC22 / THM_CALL / THM_JUMP*
reloc=set()
_TEXT_RELS=buildcfg.relocations(elf,_texts[0][0])
for sec in _TEXT_RELS:
    for rr in sec.iter_relocations():
        o=rr['r_offset']
        if rr['r_info_type'] in THM_BR: reloc.update(range(o,o+4))
        else: reloc.update(range(o&~3,(o&~3)+4))
a=int(ADDR,16); mine=texts[0].data()[:slot]; orig=PRISTINE[a-BASE:a-BASE+slot]
diffs=[i for i in range(min(len(mine),len(orig))) if i not in reloc and mine[i]!=orig[i]]
if diffs:
    _cls, _metric, _detail, _hint = residue.classify(orig, mine, ISA, reloc, slot)
    fail(f"BYTEDIFF: {len(diffs)} bytes differ at {[hex(o) for o in diffs[:8]]} (NOT byte-exact — keep matching or emit BLOCKED)",
         _cls, _metric, _detail, _hint)
symtab=elf.get_section_by_name('.symtab')
referenced=set()
for sec in elf.iter_sections():
    if hasattr(sec,'iter_relocations'):
        referenced.update(rr['r_info_sym'] for rr in sec.iter_relocations())
undef=[s.name for i,s in enumerate(symtab.iter_symbols()) if s['st_shndx']=='SHN_UNDEF' and s['st_info']['bind']=='STB_GLOBAL' and s.name and i in referenced]
missing=[u for u in undef if u not in SYMSET
         and not (re.match(r"^(\w+_[0-9a-fA-F]{8})_(dup|arg)$",u) and re.sub(r"_(dup|arg)$","",u) in SYMSET)]
if missing: fail(f"UNDEF-SYM: {missing} — these callee symbols don't exist. Use the EXACT committed name (grep symbols.txt by the callee ADDR); for an un-decompiled callee use raw {PFX}<addr> (extern \"C\").",
                     "UNDEF-SYM", len(missing), " ".join(missing[:3]))

# DEFINED-SYMBOL VERIFY: the byte-compare says the CODE is right; it says nothing about the NAME the
# object exports. A curated name in symbols.txt is binding -- dsd fails the build without it -- so a
# file whose bytes are perfect but whose symbol is spelled differently passes this gate and then
# link-fails the whole wave. Two got through on 2026-08-19: `WriteGXFifoZeroBurst(void*)` mangled to
# _Z20WriteGXFifoZeroBurstPv where the config binds _Z20WriteGXFifoZeroBurstPVv (the parameter is
# volatile), and a file defining MTX_Identity44_ at an address whose bound name is
# _Z18InitStruct020c21dcPv. Both were caught by a human-written verify pass, not by this gate.
_m=re.search(r"(?m)^(\S+)\s+kind:function\([^\n]*?addr:0x0*%s\b"%ADDR.lstrip("0"),symtxt)
_want=_m.group(1) if _m else None
# EVERY address, not only curated ones. Skipping `func_<addr>` addresses left the gate promising
# MATCH for an object that exports a name the linker will not find -- pipetest caught exactly that
# at 0x020017a4. The name is repairable there (gate_staging runs autorepair on WRONG-SYMBOL before
# rejecting), but a gate that stays silent about it is making a guarantee it cannot keep.
if _want:
    _defined=[s.name for s in symtab.iter_symbols()
              if s['st_info']['type']=='STT_FUNC' and s['st_info']['bind']=='STB_GLOBAL' and s.name]
    if _defined and _want not in _defined:
        fail(f"WRONG-SYMBOL: object defines {_defined}, but config binds '{_want}' at {ADDR}. "
             f"The bytes match; the exported name does not, so this link-fails. Define exactly that "
             f"name -- for a mangled C++ name match the signature it demangles to (a 'V' before a "
             f"pointer type means the parameter is volatile).",
             "WRONG-SYMBOL", 0, _want)

# RELOC-TARGET VERIFY: the masked byte-compare above IGNORES bl/pool bytes, so a func that calls the
# WRONG callee still "matches" — then FAILS the overlay checksum at link (reloc-false-match). Decode
# what the PRISTINE binary's bl/.word actually target and confirm the worker referenced the SAME address.
# Conservative: fail ONLY on a confirmed mismatch; skip unresolvable symbols / thumb / blx / unknown
# reloc types so a genuine match is never wrongly rejected.
SYMADDR=buildcfg.lcf_symbols()
for _p in glob.glob(f"{buildcfg.config_dir('main')}/**/symbols.txt", recursive=True):
    for _l in open(_p):
        _mm=re.match(r'(\S+)\s+kind:\w+[^\n]*?addr:0x([0-9a-f]+)', _l)
        if _mm: SYMADDR[_mm.group(1)]=int(_mm.group(2),16)
def _sign24(v): return v-0x1000000 if v&0x800000 else v
def _sign23(v): return v-0x800000 if v&0x400000 else v
wrong=[]
for _sec in _TEXT_RELS:
    for rr in _sec.iter_relocations():
        off=rr['r_offset']; typ=rr['r_info_type']
        if off+4>slot: continue
        sym=symtab.get_symbol(rr['r_info_sym']).name
        if not sym: continue
        S=SYMADDR.get(sym); P=(a+off)&0xFFFFFFFF
        pb=orig[off:off+4]
        if len(pb)<4: continue
        pinstr=int.from_bytes(pb,'little')
        if typ in (1,28,29):                             # R_ARM_PC24/CALL/JUMP24 (ARM branch bl/b/bCC)
            if (pinstr>>24)&0xFE==0xFA: continue         # blx (thumb target) -> skip
            tgt=(P+8+_sign24(pinstr&0xFFFFFF)*4)&0xFFFFFFFF
            # bl target is a real func at addr `tgt`; worker's sym must resolve there. mismatch OR
            # unresolvable (invented / wrong-mangled-signature name) = WRONG callee.
            if S is None or S!=tgt: wrong.append((hex(off),sym,f"want callee@0x{tgt:x}"))
        elif typ in THM_BR:                              # THUMB BL/BLX(imm) halfword pair
            # WITHOUT this branch a thumb func calling the WRONG callee passes the gate and then
            # poisons the overlay checksum — thumb calls are type 10, which the ARM cases ignore.
            hi=pinstr&0xFFFF; lo=(pinstr>>16)&0xFFFF
            if (hi&0xF800)!=0xF000: continue
            off23=_sign23(((hi&0x7FF)<<12)|((lo&0x7FF)<<1))
            tgt=(P+4+off23)&0xFFFFFFFF
            if (lo&0xF800)==0xE800: tgt&=~3              # blx -> ARM target, word aligned
            if S is None or (S&~1)!=tgt: wrong.append((hex(off),sym,f"want callee@0x{tgt:x}"))
        elif typ==2:                                     # R_ARM_ABS32 (.word data/func pointer)
            if S is None: continue                       # data ref, unresolvable -> skip (less certain)
            A=rr['r_addend'] if rr.is_RELA() else int.from_bytes(mine[off:off+4],'little')
            # an ABS32 pointing at a THUMB function gets bit0 set by the linker (ARM ELF interworking),
            # so the ROM word is legitimately S+A+1.
            if pinstr not in ((S+A)&0xFFFFFFFF, (S+A+1)&0xFFFFFFFF):
                wrong.append((hex(off),sym,f"want data@0x{pinstr:x}"))
if wrong:
    fail(f"RELOC-WRONG: {len(wrong)} call/data target(s) resolve to a DIFFERENT address than the original "
         f"binary — you referenced the WRONG function/data (the masked byte-compare can't see this; it "
         f"would FAIL the overlay checksum). first {wrong[:4]} = [offset, your_symbol, resolves_to, "
         f"original_target]. Grep symbols.txt by the ORIGINAL target addr for the correct callee, fix, re-gate.",
         "RELOC-WRONG", len(wrong), str(wrong[0]))
# SNAPSHOT THE PROVEN FILE. Measured 08-11: 20 addrs had a PASS verdict but were not in the build,
# and re-gating every candidate showed 13 genuinely did not match. Cause was NOT rename drift
# (zero UNDEF-SYM) -- it is that a worker iterates many files all carrying the same `// USA:` tag
# (15 of them for func_ov031_02226910), so the file left on disk need not be the one that passed.
# A PASS verdict therefore did not identify any particular source. This is the one place in the
# pipeline where a match is PROVEN, so copy the exact bytes that proved it, immediately.
try:
    import shutil as _sh
    _d = f"{_kp.SP}/gated/" + ("main" if OV == "main" else f"ov{OV}")
    os.makedirs(_d, exist_ok=True)
    _tmp = f"{_d}/{ADDR}.{os.getpid()}.tmp"
    _sh.copy2(SRC, _tmp)
    os.replace(_tmp, f"{_d}/{ADDR}.cpp")
except OSError as _e:
    print(f"PRESERVATION-FAILED: {_e}")
    sys.exit(1)
_gate_record("MATCH", 0)
print("MATCH"); sys.exit(0)
