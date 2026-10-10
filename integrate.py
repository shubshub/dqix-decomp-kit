#!/usr/bin/env python3
"""Wire gate-passing worker source into the module config. ONE script for every module.

    python integrate.py <main|NNN> [--dry] [--srcdir=DIR]

REPLACES integrate_main.py + integrate_ov.py, which were maintained separately and drifted:
each had fixes the other lacked, so every candidate hit whichever set of bugs its module's copy
still had. This file is the UNION, and the module differences are the CFG table below -- nothing
else in the logic branches on module.

  only in the overlay copy      THUMB isa detection, thumb slot-2 size tolerance, thumb BL/BLX
                                reloc masking + target verify, ABS32-to-thumb bit0 tolerance
  only in the main copy         --dry, duplicate-address guard, CRLF/LF preservation, the
                                inline-table fix, --srcdir, autorepair wiring

WHAT IT DOES, per candidate: locate the definition, force the symbol the config demands, compile,
compare bytes against the pristine ROM with relocations masked, verify every relocation resolves
to the address the ROM actually calls, then rewrite symbols.txt and append a delinks entry.
Anything that fails is moved out of src/ to a stage dir so it cannot poison the build glob.
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import bisect
import glob
import importlib.util as _ilu
import itertools
import os
import re
import shutil
import subprocess
import sys

from elftools.elf.elffile import ELFFile

import buildcfg
import dataown

SP = _kp.SP
KIT = _kp.KIT
REPO = os.environ.get("DQIX_REPO", _kp.REPO)
FLAGS = list(buildcfg.FLAGS)

_spec = _ilu.spec_from_file_location("autorepair", f"{KIT}/autorepair.py")
_autorepair = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_autorepair)

MOD = sys.argv[1]
MAIN = MOD == "main"
DRY = "--dry" in sys.argv
SRCDIR = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--srcdir=")), None)
if SRCDIR:
    DRY = True                      # a non-default srcdir is for inspection only
os.chdir(REPO)

# ---- the ONLY module-dependent values -------------------------------------------------------
if MAIN:
    CFG = "config/usa/arm9"
    PRISTINE = open(f"{REPO}/extract/usa/arm9/arm9.bin", "rb").read()
    PREFIX = "func_"
    # ALL of src/, not the module's own directory. The `// USA: <PREFIX><addr>` tag decides which
    # module a file belongs to, and main functions live outside src/Combat/Main -- src/System/
    # GamecardBusOwnership.cpp is main code. Both originals scanned "src"; narrowing it here would
    # have silently stopped integrating anything filed elsewhere.
    SRCDIR = SRCDIR or "src"
    STAGE = f"{SP}/main_stage"
    WLOG = f"{SP}/wlog/integ_main.txt"
    LBL = "main"
else:
    CFG = f"config/usa/arm9/overlays/ov{MOD}"
    PRISTINE = open(f"{REPO}/extract/usa/arm9_overlays/ov{MOD}.bin", "rb").read()
    PREFIX = f"func_ov{MOD}_"
    SRCDIR = SRCDIR or "src"        # see the note above: the tag, not the directory, selects module
    STAGE = f"{SP}/ov{MOD}_stage"
    WLOG = f"{SP}/wlog/integ_ov{MOD}.txt"
    LBL = f"ov{MOD}"

# Per-file compiler override: a few functions only match on a later mwccarm build.
_CC_OVR = {}
try:
    for _l in open(f"{REPO}/tools/cc_overrides.txt", encoding="utf-8"):
        parts = _l.split("#")[0].split()
        if len(parts) == 2:
            _CC_OVR[parts[0].replace("\\", "/").split("/")[-1]] = parts[1]
except IOError:
    pass                            # the table is optional; absence means "use the default"


def cc_for(path):
    v = _CC_OVR.get(os.path.basename(str(path)))
    # through buildcfg.cc_path, so an override build is run by whatever runs the default one: the
    # .exe itself on Windows, the wrapper around it (wibo/wine) everywhere else.
    return buildcfg.cc_path(v)


def _read_keep_nl(p):
    """Read a config file, remembering its line ending.

    main's symbols.txt is LF while its delinks.txt is CRLF. Rewriting in Python text mode turned
    a one-line change into a whole-file diff on a shared file, so each file is written back with
    the ending it arrived with.
    """
    raw = open(p, "rb").read()
    return raw.decode("utf-8", "replace").replace("\r\n", "\n"), ("\r\n" if b"\r\n" in raw else "\n")


symtxt, SYM_NL = _read_keep_nl(f"{CFG}/symbols.txt")
delinks, DEL_NL = _read_keep_nl(f"{CFG}/delinks.txt")

# INLINE-TABLE FIX (main only, idempotent). dsd emits the 256-byte reciprocal tables inlined in
# _fdiv/_ddiv as sized data symbols nested INSIDE a function's range. mwldarm sums symbol sizes per
# section and hard-errors when the total exceeds the section, which only bites once a delink splits
# that module. Declaring them label(arm) makes dsd emit them size-0. Re-applied every run because
# run_main.sh does `git checkout HEAD -- config/` immediately before calling us.
if MAIN:
    _fixed = []
    for _lbl in (".L_0200c274", ".L_0200d484"):
        if f"{_lbl} kind:data(byte[256])" in symtxt:
            symtxt = symtxt.replace(f"{_lbl} kind:data(byte[256])", f"{_lbl} kind:label(arm)")
            _fixed.append(_lbl)
    if _fixed:
        print(f"[{LBL}] inline-table fix re-applied to {_fixed}")

# Section table. Code sections only; a function outside them cannot be delinked.
CODE_SECTIONS, ALL_SECTIONS = [], []
for _m in re.finditer(r"\.(\w+)\s+start:0x([0-9a-fA-F]+)\s+end:0x([0-9a-fA-F]+)\s+kind:(\w+)", delinks):
    t = (_m.group(1), int(_m.group(2), 16), int(_m.group(3), 16))
    ALL_SECTIONS.append(t)
    if _m.group(4) == "code":
        CODE_SECTIONS.append(t)
# Every address some file ALREADY delinks. The candidate filter below is by PATH, so a second file
# for a function that is already delinked under a different name passes it and delinks the same
# bytes twice: `.text in file 'func_0209ed0c.cpp' (0x209ed0c..0x209ee34) overlaps with previous file
# '0209ed0c.cpp'` reds the build. Path-keyed dedupe cannot see it, and because the first entry is
# committed it recurs on every pass. Per-file entries carry no `kind:`; section-table rows do.
DELINKED = set()
for _m in re.finditer(r"(?m)^\s*\.\w+\s+start:0x([0-9a-fA-F]+)\s+end:0x[0-9a-fA-F]+(.*)$", delinks):
    if "kind:" not in _m.group(2):
        DELINKED.add(_m.group(1).lower().rjust(8, "0"))
_spans = sorted((int(m.group(1), 16), int(m.group(2), 16)) for m in
                re.finditer(r"(?m)^\s*\.\w+\s+start:0x([0-9a-fA-F]+)\s+end:0x([0-9a-fA-F]+)[ \t]*$", delinks))
_starts = [s for s, _e in _spans]
_reach = list(itertools.accumulate((e for _s, e in _spans), max))
for _m in re.finditer(r"(?m)^\S+\s+kind:function\([^)]*\)\s+addr:0x([0-9a-fA-F]+)", symtxt):
    _i = bisect.bisect_right(_starts, int(_m.group(1), 16)) - 1
    if _i >= 0 and int(_m.group(1), 16) < _reach[_i]:
        DELINKED.add(_m.group(1).lower().rjust(8, "0"))

BASE = min(s for _n, s, _e in ALL_SECTIONS)
CODE_NAMES = [n for n, _s, _e in CODE_SECTIONS]


def section_for(addr):
    for nm, s, e in ALL_SECTIONS:
        if s <= addr < e:
            return nm
    return "text"


# Every symbol name in the WHOLE project, so an undefined reference can be told apart from a typo,
# and every symbol's address, so a relocation can be checked against what the ROM really calls.
SYMADDR = buildcfg.lcf_symbols()
SYMSET = set(SYMADDR)
for _p in glob.glob("config/usa/arm9/**/symbols.txt", recursive=True):
    try:
        for _l in open(_p, encoding="utf-8", errors="ignore"):
            if " kind:" in _l:
                SYMSET.add(_l.split()[0])
            _ma = re.match(r"(\S+)\s+kind:\w+[^\n]*?addr:0x([0-9a-fA-F]+)", _l)
            if _ma:
                SYMADDR[_ma.group(1)] = int(_ma.group(2), 16)
    except OSError:
        pass


def _s24(v):
    return v - 0x1000000 if v & 0x800000 else v


_TRACKED = set(subprocess.run(["git", "ls-files"], capture_output=True, text=True)
               .stdout.replace("\\", "/").split("\n"))


def tracked(f):
    return f.replace("\\", "/") in _TRACKED


# ---- candidates -----------------------------------------------------------------------------
# Source under this module carrying `// USA: <PREFIX><addr>` whose address is a function in this
# module's symbols.txt and whose path is not already delinked.
#
# A TRACKED file is skipped only when its address is ALREADY WIRED. Skipping every tracked file
# stranded the ones that were committed but never delinked: invisible to the integrator forever, so
# the address stayed unmatched while its finished source sat in the repo, and a fresh MATCH for it
# could not land either -- staging copies over the tracked path, the glob then reports "0 pending
# files", and the rollback reverts the copy (0205f9cc, 9 such files at the time of the fix).
pending = []
for f in sorted(glob.glob(f"{SRCDIR}/**/*.cpp", recursive=True)
                + glob.glob(f"{SRCDIR}/**/*.c", recursive=True)):
    fn = f.replace("\\", "/")
    txt = open(f, encoding="utf-8", errors="ignore").read()
    if tracked(fn) and all(a.lower() in DELINKED for a in
                           re.findall(rf"// USA: {PREFIX}([0-9a-fA-F]{{8}})\b", txt)):
        continue
    tags = {a.lower() for a in re.findall(rf"// USA: {PREFIX}([0-9a-fA-F]{{8}})\b", txt)}
    for addr in tags:
        # Keyed on the ADDRESS. Keying on the func_ name hid every function whose ROM symbol is
        # curated or mangled: they were placed, gated, then reported as "0 pending files".
        if re.search(rf"kind:function\([^)]*\) addr:0x{addr}\b", symtxt) and (fn not in delinks or len(tags) > 1):
            pending.append((fn, addr))
pending = sorted(set(pending))
TU = {}
for f, addr in pending:
    TU.setdefault(f, []).append(addr)
TU = {f: addrs for f, addrs in TU.items() if len(addrs) > 1}
print(f"[{LBL}] base=0x{BASE:x}  {len(pending)} pending files{'  (DRY RUN)' if DRY else ''}")


def size_of(addr):
    m = re.search(rf"kind:function\((?:arm|thumb),size=0x([0-9a-fA-F]+)\) addr:0x{addr}\b", symtxt)
    return int(m.group(1), 16) if m else None


def isa_of(addr):
    m = re.search(rf"kind:function\((arm|thumb),size=0x[0-9a-fA-F]+\) addr:0x{addr}\b", symtxt)
    return m.group(1) if m else "arm"


def cfg_name(addr):
    """The name this module's config already gives the address, if it is not the raw func_ tag.

    dsd's `check symbols` fails the build when a name in symbols.txt is missing from the linked
    binary, so keep-raw renaming a curated symbol (AutoloadCallback, strcmp, a mangled _Z name)
    deletes a symbol the config still demands and reds the WHOLE wave. The config is binding.
    """
    m = re.search(r"(?m)^(\S+)\s+kind:function\([^\n]*?addr:0x0*%s\b" % addr.lstrip("0"), symtxt)
    if not m:
        return None
    return None if m.group(1) == f"{PREFIX}{addr}" else m.group(1)


_THM_BR = {10, 25, 30, 31}          # R_ARM_THM_PC22 / THM_CALL / THM_JUMP*

def missing_undefs(elf, symtab, f):
    global symtxt
    referenced = {rr["r_info_sym"] for s in elf.iter_sections() if hasattr(s, "iter_relocations")
                  for rr in s.iter_relocations()}
    undef = [s.name for i, s in enumerate(symtab.iter_symbols())
             if s["st_shndx"] == "SHN_UNDEF" and s["st_info"]["bind"] == "STB_GLOBAL" and s.name
             and i in referenced]
    missing = [u for u in undef if u not in SYMSET]
    for u in list(missing):
        am = re.match(r"^(\w+_[0-9a-fA-F]{8})_(dup|arg)$", u)
        bm = am and re.search(r"(?m)^%s kind:(\w+)[^\n]*?addr:0x([0-9a-fA-F]+)[^\n]*$" % re.escape(am.group(1)),
                              symtxt)
        if not bm or bm.group(1) == "function":
            continue
        kind = "bss" if bm.group(1) == "bss" else "data(any)"
        alias = "%s kind:%s addr:0x%s" % (u, kind, bm.group(2))
        symtxt = symtxt[:bm.end()] + "\n" + alias + symtxt[bm.end():]
        SYMSET.add(u)
        SYMADDR[u] = int(bm.group(2), 16)
        missing.remove(u)
        print(f"ALIAS {f}: added {alias}")
    return missing


def reloc_wrong(elf, symtab, text_index, a, slot, orig, mine):
    wrong = []
    for s in elf.iter_sections():
        if not hasattr(s, "iter_relocations") or s["sh_info"] != text_index or not s.name.startswith(".rel"):
            continue
        for rr in s.iter_relocations():
            o, t = rr["r_offset"], rr["r_info_type"]
            if o + 4 > slot:
                continue
            sy = symtab.get_symbol(rr["r_info_sym"]).name
            if not sy:
                continue
            S = SYMADDR.get(sy)
            P = (a + o) & 0xFFFFFFFF
            pb = orig[o:o + 4]
            if len(pb) < 4:
                continue
            pi = int.from_bytes(pb, "little")
            if t in (1, 28, 29):                                  # ARM BL / CALL / JUMP24
                if (pi >> 24) & 0xFE == 0xFA:                     # BLX(imm): interworking, skip
                    continue
                tgt = (P + 8 + _s24(pi & 0xFFFFFF) * 4) & 0xFFFFFFFF
                if S is None or S != tgt:
                    wrong.append((hex(o), sy, f"@0x{tgt:x}"))
            elif t in _THM_BR:                                    # THUMB BL/BLX halfword pair
                hi, lo = pi & 0xFFFF, (pi >> 16) & 0xFFFF
                if (hi & 0xF800) != 0xF000:
                    continue
                off = ((hi & 0x7FF) << 12) | ((lo & 0x7FF) << 1)
                if off & 0x400000:
                    off -= 0x800000
                tgt = (P + 4 + off) & 0xFFFFFFFF
                if (lo & 0xF800) == 0xE800:                       # BLX: target is word-aligned
                    tgt &= ~3
                # a thumb callee's symbol carries bit0 for interworking -- mask before comparing
                if S is None or (S & ~1) != tgt:
                    wrong.append((hex(o), sy, f"@0x{tgt:x}"))
            elif t == 2:                                          # ABS32 pool word
                if S is None:
                    continue
                A = dataown.addend(rr, mine)
                # An ABS32 pointing at a THUMB function legitimately carries bit0, so the ROM word
                # is S+A+1; without this every thumb function reached through a pool word is
                # falsely rejected.
                if pi not in (((S + A) & 0xFFFFFFFF), ((S + A + 1) & 0xFFFFFFFF)):
                    wrong.append((hex(o), sy, f"@0x{pi:x}"))
    return wrong


matched, fails, verdicts = {}, [], []
newdelinks = delinks
RELOCS, RELOCS_TOUCHED = None, {}
os.makedirs(f"{SP}/wlog", exist_ok=True)

ASMPAT = re.compile(r'(?m)^\s*asm\b|\basm\s+(?:void|int|unsigned|char|long|short)\b')
ASM_ALLOW = set()
for _l in open(f"{KIT}/asm_allow.txt", encoding="utf-8").read().splitlines():
    _l = _l.split('#', 1)[0].strip()
    if _l: ASM_ALLOW.add(_l.split()[0].lower())

ORIGINAL = {}
for f, addr in pending:
    if f not in ORIGINAL:
        ORIGINAL[f] = open(f, encoding="utf-8", errors="ignore", newline="").read()
    if f in TU:
        continue
    a = int(addr, 16)
    # Two files claiming one address silently overwrote each other, dropping a byte-exact match
    # with no signal. Reject the later one loudly.
    if addr in matched:
        print(f"DUP-ADDR {f} @0x{addr}: already claimed by {matched[addr][2]} — SKIP")
        fails.append((f, addr))
        continue

    if addr in DELINKED:
        print(f"DUP-DELINK {f} @0x{addr}: another file already delinks this address — SKIP")
        fails.append((f, addr))
        continue

    # SAME ASM POLICY AS ov_recover.place(). This integrator takes its candidates from untracked
    # src/, so anything place() refused could still walk in here from another tool and land as
    # assembly -- which is how eleven secure-area stubs entered a combined build nobody intended
    # to batch. The allowlist is the single control point for both integrators.
    if addr not in ASM_ALLOW and ASMPAT.search(open(f, encoding="utf-8", errors="ignore").read()):
        print(f"ASM-SKIP {f} @0x{addr}: hand-asm and not on the asm allowlist — SKIP")
        fails.append((f, addr))
        continue

    sec = section_for(a)
    if sec not in CODE_NAMES:
        # .init is code but dsd cannot place a file there; rodata/data/bss are not code at all.
        print(f"NON-TEXT {f} @0x{addr}: .{sec} — SKIP")
        fails.append((f, addr))
        continue

    # Fix the mechanical faults (uncommitted callee names, a missing .init pragma) BEFORE judging.
    try:
        fixes = _autorepair.repair(f, MOD, addr)
        if fixes:
            print(f"REPAIR {f} @0x{addr}: " + ", ".join(fixes))
    except Exception as e:
        print(f"REPAIR-FAIL {f} @0x{addr}: {e}")

    raw = f"{PREFIX}{addr}"
    ftxt = open(f, encoding="utf-8", errors="ignore").read()
    # A hand-written C++ member function has no `ARM name(` to rewrite -- its linked symbol IS the
    # mangled name -- so it carries `// KEEP-NAME` and is delinked under its own symbol.
    keepname = "// KEEP-NAME" in ftxt
    cfg = cfg_name(addr)
    want = cfg or raw               # `raw` finds the definition; `want` is the symbol to emit
    if cfg:
        print(f"CFG-NAME {f} @0x{addr}: config demands '{cfg}' — keeping that symbol")

    # `[ \t]*\n?[ \t]*(?:asm[ \t]+)?` is what lets this see an assembly definition, which spells the
    # keyword on its own line: `ARM` / newline / `asm void MTX_Identity33_(...)`. Requiring the name
    # on the ARM|THUMB line made every allowlisted asm function NO-DEF, so it never got renamed to
    # its ROM symbol and the wave reported wired-0. At most one newline, so this still cannot walk
    # into the next function.
    m = re.search(rf"(// USA: {raw}[^\n]*\n)(.*?)(\b(?:ARM|THUMB)\b[ \t]*\n?[ \t]*(?:asm[ \t]+)?[^\n;{{]*?\b)(\w+)(\s*\()", ftxt, re.S)
    if not m and not keepname:
        print(f"NO-DEF {f} @0x{addr}: cannot locate def to rename — SKIP")
        fails.append((f, addr))
        continue
    sem = m.group(4) if m else None
    has_ec = True if m is None else ('extern "C"' in m.group(2)) or ('extern "C"' in m.group(3)) \
        or ('extern "C"' in ftxt[max(0, m.start(3) - 24):m.start(3)])
    cf = f
    if m and not keepname and (sem != want or not has_ec):
        # Force `extern "C" ARM|THUMB <want>(`. A symbol name never affects codegen, so this is
        # byte-neutral; without extern "C" the name is C++-mangled and every caller breaks.
        newhdr = m.group(1) + m.group(2) + ("" if has_ec else 'extern "C" ') + m.group(3) + want + m.group(5)
        ftxt = ftxt[:m.start()] + newhdr + ftxt[m.end():]
        if sem != want:
            ftxt = ftxt.replace(f"// USA: {raw}", f"// USA: {raw}  (semantic: {sem})", 1)
            # A RECURSIVE CALL IS A REFERENCE TO THE NAME WE JUST RETIRED. Renaming only the
            # definition left `UpdateAndRenderDebugList_02029988(...)` inside func_02029988 on
            # main:02029988, so the file never compiled again and every sweep re-reported it.
            # `sem` is this function's own name, so any other occurrence can only be a
            # self-reference -- except in a comment, and the `// USA:` marker is the file's
            # identity, which renaming has been a bug before (caught 08-18).
            body = ftxt.split("\n")
            for k, ln in enumerate(body):
                if not ln.lstrip().startswith("//"):
                    body[k] = re.sub(rf"\b{re.escape(sem)}\b", want, ln)
            ftxt = "\n".join(body)
        if DRY:
            print(f'  [dry] would rename {f}: {sem} -> extern "C" {want}')
            cf = f"{SP}/_int_dry_{os.getpid()}.cpp"
            open(cf, "w", encoding="utf-8", newline="\n").write(ftxt)
        else:
            open(f, "w", encoding="utf-8", newline="\n").write(ftxt)

    obj = f"{SP}/_int_{LBL}_{os.getpid()}.o"
    r = subprocess.run([cc_for(f)] + FLAGS + ["-c", cf, "-o", obj], capture_output=True, text=True)
    if r.returncode != 0:
        verdicts.append(("COMPILE", addr))
        print("COMPILE FAIL", f, (r.stdout + r.stderr)[-300:])
        fails.append((f, addr))
        continue

    elf = ELFFile(open(obj, "rb"))
    secname = "." + sec
    # A .init function emits no .text at all, and its relocations live in .rel.init -- looking only
    # at .text reported total=0x0 and rejected byte-exact files.
    texts = [s for s in elf.iter_sections() if s.name == secname]
    text_index = next((i for i, s in enumerate(elf.iter_sections()) if s.name == secname), None)
    total = sum(s["sh_size"] for s in texts)
    slot = size_of(addr)
    if slot is None:
        print(f"NO-SLOT {f} @0x{addr}: no function size in symbols — SKIP")
        fails.append((f, addr))
        continue
    # THUMB is padded to a 4-byte boundary by the LINKER, not mwcc, so the object may be slot-2.
    ok_sizes = (slot, slot - 2) if isa_of(addr) == "thumb" else (slot,)
    if len(texts) != 1 or total not in ok_sizes:
        verdicts.append(("SIZE", addr))
        print(f"OVERGEN {f}: {len(texts)} {secname} total=0x{total:x} slot=0x{slot:x} — SKIP")
        fails.append((f, addr))
        continue

    # Mask relocated bytes: bl targets and pool words are linker-resolved and meaningless here.
    reloc_off = set()
    for s in elf.iter_sections():
        if s.name in (".rel" + secname, ".rela" + secname) and hasattr(s, "iter_relocations"):
            for rr in s.iter_relocations():
                o = rr["r_offset"]
                # A thumb BL/BLX pair sits at a HALFWORD offset and spans 4 bytes; `&~3` would
                # leave 2 of them unmasked and report a false BYTEDIFF.
                reloc_off.update(range(o, o + 4) if rr["r_info_type"] in _THM_BR
                                 else range(o & ~3, (o & ~3) + 4))

    mine = texts[0].data()[:slot]
    orig = PRISTINE[a - BASE:a - BASE + slot]
    diffs = [i for i in range(min(len(mine), len(orig))) if i not in reloc_off and mine[i] != orig[i]]
    if diffs:
        verdicts.append(("BYTEDIFF", addr))
        print(f"BYTEDIFF {f} @0x{a:x}: {len(diffs)} bytes at {[hex(o) for o in diffs[:6]]} — SKIP")
        fails.append((f, addr))
        continue

    symtab = elf.get_section_by_name(".symtab")
    missing = missing_undefs(elf, symtab, f)
    if missing:
        verdicts.append(("UNDEF", addr))
        print(f"UNDEF-SYM {f} @0x{a:x}: {missing} — SKIP")
        fails.append((f, addr))
        continue

    # RELOC-TARGET VERIFY. The masked byte-compare above cannot see WHICH function a bl calls, so a
    # candidate calling the wrong callee passes it and then fails the ROM checksum. Decode the
    # pristine target and confirm the referenced symbol resolves to the same address. Conservative
    # by design: anything unresolvable or unrecognised is skipped, never falsely rejected.
    wrong = reloc_wrong(elf, symtab, text_index, a, slot, orig, mine)
    if wrong:
        verdicts.append(("RELOCWRONG", addr))
        print(f"RELOC-WRONG {f} @0x{a:x}: {wrong[:4]} — SKIP")
        fails.append((f, addr))
        continue

    names = [s.name for s in symtab.iter_symbols()
             if s["st_info"]["type"] == "STT_FUNC" and s["st_info"]["bind"] == "STB_GLOBAL"
             and s["st_shndx"] != "SHN_UNDEF"]
    if len(names) != 1:
        verdicts.append(("MULTI", addr))
        print("MULTI/NO GLOBAL FUNC", f, names)
        fails.append((f, addr))
        continue

    if RELOCS is None:
        RELOCS = dataown.load_relocs(REPO)
    dplan = dataown.plan(elf, text_index, slot, a, PRISTINE, BASE, MOD, CFG, REPO, f, SYMADDR,
                         newdelinks, symtxt, RELOCS)
    if isinstance(dplan, str):
        verdicts.append((dplan.split()[0], addr))
        print(f"{dplan.split()[0]} {f} @0x{a:x}: {dplan} — SKIP")
        fails.append((f, addr))
        continue
    data_lines = ""
    if dplan:
        symtxt = dplan["symtxt"]
        RELOCS.update(dplan["relocs"])
        RELOCS_TOUCHED.update(dplan["relocs"])
        RELOCS_TOUCHED.update(dplan.get("src_edits", {}))
        data_lines = dplan["delink_lines"]
        print(f"DATA {f} @0x{a:x}: owns {[(n, hex(s), hex(e)) for n, s, e in dplan['ranges']]}, "
              f"{len(dplan['retired'])} symbols retired, {len(dplan['relocs'])} relocs.txt rewritten")

    verdicts.append(("MATCHED", addr))
    matched[addr] = (names[0], slot, f)
    newdelinks += f"\n\n{f}:\n    complete\n    .{sec} start:0x{a:08x} end:0x{a + slot:08x}\n{data_lines}"


def delink_blocks(text):
    head, blocks = [], []
    for ln in text.split("\n"):
        if ln and not ln[0].isspace() and ln.rstrip().endswith(":"):
            blocks.append([ln])
        elif blocks:
            blocks[-1].append(ln)
        else:
            head.append(ln)
    return head, blocks


def tu_fail(f, addrs, code, msg):
    verdicts.extend((code, x) for x in addrs)
    print(f"{code} {f} {addrs}: {msg} — SKIP")
    fails.append((f, addrs[0]))


# A translation unit holding several functions: one compile, one delinks entry spanning them all, and
# every entry it supersedes (its own older, narrower entry, or a file whose function moved into it and
# was deleted) removed.
for f, addrs in sorted(TU.items()):
    addrs = sorted(addrs, key=lambda x: int(x, 16))
    slots = [size_of(x) for x in addrs]
    if None in slots:
        tu_fail(f, addrs, "NO-SLOT", "a function has no size in symbols")
        continue
    starts = [int(x, 16) for x in addrs]
    if any(starts[k] + slots[k] != starts[k + 1] for k in range(len(addrs) - 1)):
        tu_fail(f, addrs, "TU-GAP", "the functions are not contiguous")
        continue
    lo, hi = starts[0], starts[-1] + slots[-1]
    sec = section_for(lo)
    if sec not in CODE_NAMES or section_for(hi - 1) != sec:
        tu_fail(f, addrs, "NON-TEXT", f".{sec}")
        continue
    if any(x in matched for x in addrs):
        tu_fail(f, addrs, "DUP-ADDR", "another file already claimed one of these addresses")
        continue
    if not all(x in ASM_ALLOW for x in addrs) and ASMPAT.search(open(f, encoding="utf-8", errors="ignore").read()):
        tu_fail(f, addrs, "ASM-SKIP", "hand-asm and not on the asm allowlist")
        continue

    head, blocks = delink_blocks(newdelinks)
    keep, dropped, blocked, at = [], [], None, None
    for b in blocks:
        path = b[0].rstrip()[:-1]
        spans = [(int(m.group(1), 16), int(m.group(2), 16)) for m in
                 re.finditer(rf"\.{sec}\s+start:0x([0-9a-fA-F]+)\s+end:0x([0-9a-fA-F]+)", "\n".join(b))]
        if not any(s < hi and lo < e for s, e in spans):
            keep.append(b)
        elif path == f or not os.path.exists(path):
            dropped.append(path)
            at = len(keep) if at is None else at
        else:
            blocked = path
            break
    if blocked:
        tu_fail(f, addrs, "TU-OVERLAP", f"{blocked} still exists and delinks part of 0x{lo:08x}..0x{hi:08x}")
        continue
    trimmed = "\n".join(head + [ln for b in keep for ln in b])
    at = len(keep) if at is None else at

    obj = f"{SP}/_int_{LBL}_{os.getpid()}.o"
    r = subprocess.run([cc_for(f)] + FLAGS + ["-c", f, "-o", obj], capture_output=True, text=True)
    if r.returncode != 0:
        tu_fail(f, addrs, "COMPILE", (r.stdout + r.stderr)[-300:])
        continue
    elf = ELFFile(open(obj, "rb"))
    symtab = elf.get_section_by_name(".symtab")
    sections = list(elf.iter_sections())
    tidx = [i for i, s in enumerate(sections) if s.name == "." + sec]
    if [sections[i]["sh_size"] for i in tidx] != slots:
        tu_fail(f, addrs, "SIZE", f"{[hex(sections[i]['sh_size']) for i in tidx]} want {[hex(x) for x in slots]}")
        continue
    defs = {s["st_shndx"]: s.name for s in symtab.iter_symbols()
            if s["st_info"]["type"] == "STT_FUNC" and s["st_info"]["bind"] == "STB_GLOBAL"
            and s["st_shndx"] != "SHN_UNDEF" and s["st_value"] == 0}
    names = [defs.get(i) for i in tidx]
    wants = [cfg_name(x) or f"{PREFIX}{x}" for x in addrs]
    if names != wants:
        tu_fail(f, addrs, "TU-NAME", f"defines {names}, config demands {wants}")
        continue
    bad = None
    for i, x, a, slot in zip(tidx, addrs, starts, slots):
        mine = sections[i].data()
        orig = PRISTINE[a - BASE:a - BASE + slot]
        masked = set()
        for s in sections:
            if hasattr(s, "iter_relocations") and s["sh_info"] == i and s.name.startswith(".rel"):
                for rr in s.iter_relocations():
                    o = rr["r_offset"]
                    masked.update(range(o, o + 4) if rr["r_info_type"] in _THM_BR else range(o & ~3, (o & ~3) + 4))
        diffs = [k for k in range(slot) if k not in masked and mine[k] != orig[k]]
        if diffs:
            bad = ("BYTEDIFF", f"{x}: {len(diffs)} bytes at {[hex(o) for o in diffs[:6]]}")
            break
        wrong = reloc_wrong(elf, symtab, i, a, slot, orig, mine)
        if wrong:
            bad = ("RELOCWRONG", f"{x}: {wrong[:4]}")
            break
    if bad:
        tu_fail(f, addrs, *bad)
        continue
    missing = missing_undefs(elf, symtab, f)
    if missing:
        tu_fail(f, addrs, "UNDEF", str(missing))
        continue

    if RELOCS is None:
        RELOCS = dataown.load_relocs(REPO)
    dplan = dataown.plan(elf, tidx[0], slots[0], lo, PRISTINE, BASE, MOD, CFG, REPO, f, SYMADDR,
                         trimmed, symtxt, RELOCS, extra_texts=list(zip(tidx[1:], slots[1:], starts[1:])))
    if isinstance(dplan, str):
        tu_fail(f, addrs, dplan.split()[0], dplan)
        continue
    data_lines = ""
    if dplan:
        symtxt = dplan["symtxt"]
        RELOCS.update(dplan["relocs"])
        RELOCS_TOUCHED.update(dplan["relocs"])
        RELOCS_TOUCHED.update(dplan.get("src_edits", {}))
        data_lines = dplan["delink_lines"]
        print(f"DATA {f} TU: owns {[(n, hex(s), hex(e)) for n, s, e in dplan['ranges']]}, "
              f"{len(dplan['retired'])} symbols retired ({dplan['retired'][:4]}), {len(dplan['relocs'])} relocs.txt rewritten")
    if dropped:
        print(f"TU {f}: supersedes the entries of {dropped}")
    for x, n, slot in zip(addrs, names, slots):
        verdicts.append(("MATCHED", x))
        matched[x] = (n, slot, f)
    entry = f"{f}:\n    complete\n    .{sec} start:0x{lo:08x} end:0x{hi:08x}\n{data_lines}".split("\n")
    newdelinks = "\n".join(head + [ln for b in keep[:at] for ln in b] + entry + [ln for b in keep[at:] for ln in b])

# ---- wire it up -----------------------------------------------------------------------------
out = []
for line in symtxt.split("\n"):
    m = re.match(rf"{PREFIX}([0-9a-fA-F]+) (kind:function.*)", line)
    if m and m.group(1).lower() in matched:
        out.append(f"{matched[m.group(1).lower()][0]} {m.group(2)}")
    else:
        out.append(line)

if DRY:
    print("--- [dry] symbols.txt renames ---")
    for addr, (name, sz, f) in matched.items():
        print(f"    {PREFIX}{addr} -> {name}")
    print("--- [dry] delinks.txt additions ---")
    import difflib
    print("".join(difflib.unified_diff(delinks.splitlines(True), newdelinks.splitlines(True), n=0)) or "    (none)")
    for path in RELOCS_TOUCHED:
        print(f"--- [dry] would rewrite {path}")
else:
    open(f"{CFG}/symbols.txt", "w", newline=SYM_NL).write("\n".join(out))
    open(f"{CFG}/delinks.txt", "w", newline=DEL_NL).write(newdelinks)
    for path, (text, nl) in RELOCS_TOUCHED.items():
        open(path, "w", newline=nl).write(text)
    with open(f"{SP}/wlog/integrate_src_rewrites.txt", "w", encoding="utf-8") as fh:
        for path in RELOCS_TOUCHED:
            rel = os.path.relpath(path, REPO).replace("\\", "/")
            if rel.startswith("src/"):
                fh.write(rel + "\n")

print(f"integrated {len(matched)}; {len(fails)} failed")
for a, (n, _s, _f) in matched.items():
    print(f"  {a} {n}")

# Move failed UNTRACKED files out of src/ so they cannot pollute the build glob. A TRACKED file is
# committed source: moving it would delete real work, so it stays put (not in delinks -> not built).
landed_files = {v[2] for v in matched.values()}
for f, text in ORIGINAL.items():
    if (DRY or (tracked(f) and f not in landed_files)) and \
            open(f, encoding="utf-8", errors="ignore", newline="").read() != text:
        open(f, "w", encoding="utf-8", newline="").write(text)
        print(f"  restored {f}")

if not DRY:
    os.makedirs(STAGE, exist_ok=True)
for f, addr in fails:
    if tracked(f):
        continue
    if DRY:
        print(f"  [dry] would stage-out {f} -> {STAGE}/")
        continue
    try:
        shutil.move(f, os.path.join(STAGE, os.path.basename(f)))
    except OSError as e:
        print(f"  stage-move fail {f}: {e}")

if not DRY and verdicts:
    open(WLOG, "a").write("\n".join(f"{c} {a}" for c, a in verdicts) + "\n")
print(f"this-pass verdicts {len(verdicts)}: "
      f"{dict((c, sum(1 for cc, _ in verdicts if cc == c)) for c, _ in verdicts)}")
