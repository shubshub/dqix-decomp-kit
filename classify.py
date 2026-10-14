#!/usr/bin/env python
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
# PRE-GATE CLASSIFIER. Compiles every candidate and splits it into:
#   TRUSTED  - byte-exact vs pristine (reloc bytes masked) AND *every* reloc target independently
#              confirmed to resolve to the exact address the pristine binary points at.
#   RISKY    - byte-exact but >=1 reloc could not be confirmed (unresolvable sym, blx, exotic type).
#   BAD      - COMPILE / SIZE / BYTEDIFF / UNDEF / RELOCWRONG. Definitively not a match.
#
# WHY: a whole-wave `ninja check` costs ~6 min. One reloc-false-match (right bytes, WRONG callee — the
# masked per-func compare cannot see it) turns that gate RED, and bisecting N funcs to find it costs
# O(k*log N) gates = hours, so the gate cap fires and the ENTIRE wave defers uncommitted. That is the
# 0-commit loop. Classifying first is ~0.3 s/func of pure local CPU and lets the integrator gate the
# TRUSTED set as ONE green build, quarantining only the genuinely uncertain few for bisection.
import re, os, glob, subprocess, hashlib
from elftools.elf.elffile import ELFFile

import buildcfg
import dataown

REPO = _kp.REPO
SP = _kp.SP
KIT = _kp.KIT
CC = buildcfg.CC

# Per-file compiler override (tools/cc_overrides.txt, same table the build reads).
# The table is EMPTY and must stay that way: an override is evidence the SOURCE is
# wrong, never a way to land a function. Keyed by the SOURCE FILE BASENAME so it
# works no matter which directory the gate is compiling the candidate from.
_CC_OVR = {}
try:
    for _l in open(f"{REPO}/tools/cc_overrides.txt", encoding="utf-8"):
        _l = _l.split("#")[0].split()
        if len(_l) == 2:
            _CC_OVR[_l[0].replace(chr(92), "/").split("/")[-1]] = _l[1]
except IOError:
    pass
_FLAG_OVR = {}
try:
    for _l in open(f"{REPO}/tools/cc_flag_overrides.txt", encoding="utf-8"):
        _l = _l.split("#")[0].split()
        if len(_l) >= 2:
            _FLAG_OVR[_l[0].replace(chr(92), "/").split("/")[-1]] = _l[1:]
except IOError:
    pass


def flags_for(path):
    """Per-file compiler flags. Without this the wave rejects a function the build would compile
    correctly -- the same fault the per-file COMPILER override had until 2026-09-06."""
    import os as _os
    return _FLAG_OVR.get(_os.path.basename(str(path)), [])


def cc_for(path):
    import os as _os
    v = _CC_OVR.get(_os.path.basename(str(path)))
    return buildcfg.cc_path(v)

FLAGS = list(buildcfg.FLAGS)


def _s24(v):
    return v - 0x1000000 if v & 0x800000 else v


_THM_BR = {10, 25, 30, 31}   # R_ARM_THM_PC22 / THM_CALL / THM_JUMP*
_CTX = {}


def _ctx(MOD):
    # Building symaddr means globbing+parsing every symbols.txt in the project (~MBs). classify() is
    # called once per candidate variant (hundreds per wave), so this is cached per module.
    # MOD is an overlay number ("000".."035") or "main" (the arm9 module). ONE code path: every
    # difference is a value below, because forked copies drift apart and silently drop matches.
    if MOD in _CTX: return _CTX[MOD]
    MAIN = (MOD == 'main')
    if MAIN:
        CFG = f"{REPO}/{buildcfg.config_dir(MOD)}"
        pristine = open(f"{REPO}/{buildcfg.pristine(MOD)}", "rb").read()
        HEXC, PRE, ANCH = "[0-9a-fA-F]", "func_", "(?m)^"   # main's configs mix hex case; the ^ anchor
        # stops `func_<8hex>` from matching inside a `func_ovNNN_<8hex>` symbol (it cannot anyway — `o`
        # is not hex — but the anchor makes that structural rather than accidental).
    else:
        CFG = f"{REPO}/{buildcfg.config_dir(MOD)}"
        pristine = open(f"{REPO}/{buildcfg.pristine(MOD)}", "rb").read()
        HEXC, PRE, ANCH = "[0-9a-f]", f"func_ov{MOD}_", ""
    _dl = open(f"{CFG}/delinks.txt").read()
    # Section map (header block) — ALL code sections, not just .text. `.init` holds
    # CodeWarrior's static-initializer functions and IS matchable from readable C++ via
    # `#pragma define_section initcode ".init" RX` + `__declspec(initcode)` (SP/inv/initsec_FINDINGS.md,
    # 6/6 matched). integrate_ov.py already emits the correct `.init` delink. So only NON-code sections
    # (rodata/data/bss) are genuinely unmatchable as functions.
    _head = _dl[:_dl.index('\n\n')] if '\n\n' in _dl else _dl
    textrng = [(m.group(1), int(m.group(2), 16), int(m.group(3), 16)) for m in
               re.finditer(rf'\.(\w+)\s+start:0x({HEXC}+) end:0x({HEXC}+) kind:code', _head)]
    if MAIN:
        # BASE FROM THE SECTION TABLE, never min() over every `start:` in the file: main's per-file
        # delink entries mix hex case, and one lowercase-only parse of `0x0200FE68` yields BASE=512,
        # which silently offsets every byte compare (inv/mainsup_FINDINGS.md §6.2/6.3).
        base = min(int(m, 16) for m in re.findall(rf'start:0x({HEXC}+)', _head))
        # main's .init (0x020e5920, ~4KB of real ARM code holding 42 raw func_ symbols) is code but
        # NOT delinkable — integrate_main.py refuses it. Dropping it here makes those addrs classify
        # as SECTION: parked and never struck, instead of burning a gate to be rejected downstream.
        # main's .init IS delinkable: the linker script emits main_661.o(.init) and an .init
        # delink entry gates green (verified 08-18, 17 functions landed). Keeping .init here lets
        # those addrs classify normally instead of being parked as SECTION forever.
        pass
    else:
        base = min(int(m, 16) for m in re.findall(r'start:0x([0-9a-f]+)', _dl))
    symaddr = buildcfg.lcf_symbols()
    for p in glob.glob(f"{REPO}/{buildcfg.config_dir('main')}/**/symbols.txt", recursive=True):
        for l in open(p):
            m = re.match(r'(\S+)\s+kind:\w+[^\n]*?addr:0x([0-9a-fA-F]+)', l)
            if m:
                symaddr[m.group(1)] = int(m.group(2), 16)
    # keep the ISA: thumb needs a different size tolerance, reloc mask and branch decode below.
    sizes = {m.group(1).lower(): (int(m.group(3), 16), m.group(2)) for m in
             re.finditer(rf'{ANCH}{PRE}({HEXC}{{8}}) kind:function\((arm|thumb),size=0x({HEXC}+)\)',
                         open(f"{CFG}/symbols.txt").read())}
    # ADDRESS-KEYED FALLBACK. The pattern above only sees `func_<addr>` names, so every
    # function carrying a semantic or mangled ROM symbol classified as NOSLOT and was
    # thrown away -- 8 of 15 candidates in one main wave. Index those by address too.
    for _m in re.finditer(rf'^\S+ kind:function\((arm|thumb),size=0x({HEXC}+)\) addr:0x0*({HEXC}+)',
                          open(f"{CFG}/symbols.txt").read(), re.M):
        _a = _m.group(3).lower().rjust(8, '0')
        sizes.setdefault(_a, (int(_m.group(2), 16), _m.group(1)))
    _CTX[MOD] = (pristine, base, symaddr, sizes, textrng)
    return _CTX[MOD]


CACHED = ('SIZE', 'BYTEDIFF')
_CACHE = {}


def _headers():
    if 'headers' not in _CACHE:
        dirty = subprocess.run(["git", "-C", REPO, "status", "--porcelain", "--", "include", "libs"],
                               capture_output=True, text=True)
        index = subprocess.run(["git", "-C", REPO, "ls-files", "-s", "--", "include", "libs"],
                               capture_output=True, text=True)
        ok = dirty.returncode == 0 and index.returncode == 0 and not dirty.stdout.strip()
        state = index.stdout + open(__file__, encoding="utf-8").read()
        _CACHE['headers'] = hashlib.sha1(state.encode()).hexdigest() if ok else None
    return _CACHE['headers']


def _verdicts():
    if 'verdicts' not in _CACHE:
        _CACHE['verdicts'] = {}
        try:
            for line in open(f"{SP}/wlog/classify_cache.tsv", encoding="utf-8"):
                key, _, verdict = line.rstrip("\n").rpartition("\t")
                _CACHE['verdicts'][key] = verdict
        except OSError:
            pass
    return _CACHE['verdicts']


def classify(MOD, cands, workdir=None, names=None):
    """cands: {addr: source_text}. Returns {addr: 'TRUSTED'|'RISKY'|<BAD verdict>}.
    MOD: overlay number ("000".."035") or "main".

    names: {addr: original filename}. cc_for() keys the per-file compiler override on the BASENAME,
    and every candidate is written here as `c.cpp` -- so without this the override never applies and
    a function that only matches on a later mwccarm is rejected by every wave forever. ov000:0215858c
    (4816B, the largest match this project has made) gated MATCH under wgate, which had the real
    path, and BYTEDIFF here on the same text."""
    work = workdir or f"{SP}/clswork_{os.getpid()}"
    os.makedirs(work, exist_ok=True)
    pristine, base, symaddr, sizes, textrng = _ctx(MOD)
    out = {}
    keys = {}
    src = f"{work}/c.cpp"
    obj = f"{work}/c.o"
    for addr, txt in cands.items():
        _si = sizes.get(addr)
        if not _si:
            out[addr] = 'NOSLOT'; continue
        slot, _isa = _si
        # Which CODE section does this addr live in? Measure THAT section, not always .text — a correct
        # .init function emits no .text at all, so measuring .text would wrongly report SIZE 0x0.
        _av = int(addr, 16)
        _want = next((f".{n}" for n, s0, e0 in textrng if s0 <= _av < e0), None)
        if textrng and _want is None:
            # Not in any CODE section (rodata/data/bss) -> not an emittable function. Parked, never struck.
            out[addr] = 'SECTION'; continue
        _want = _want or '.text'
        _cc = cc_for((names or {}).get(addr) or src)
        if _headers():
            _flags = " ".join(FLAGS + flags_for((names or {}).get(addr) or src))
            keys[addr] = "\t".join((MOD, addr, str(slot), _want, _cc, _headers(),
                                    hashlib.sha1((_flags + "\0" + txt).encode()).hexdigest()))
            if keys[addr] in _verdicts():
                out[addr] = _verdicts()[keys[addr]]; continue
        open(src, 'w', encoding='utf-8').write(txt)
        r = subprocess.run(buildcfg.tool_command(_cc) + FLAGS + flags_for((names or {}).get(addr) or src) + ["-c", src, "-o", obj], capture_output=True, text=True,
                           cwd=REPO)
        if r.returncode != 0:
            out[addr] = 'COMPILE'; continue
        buildcfg.postcompile(obj)
        try:
            elf = ELFFile(open(obj, "rb"))
            _texts = buildcfg.sections(elf, _want)
            texts = [s for _, s in _texts]
            # thumb is padded to 4 bytes by the LINKER, not mwcc, so the object may be slot-2.
            _ok = (slot, slot - 2) if _isa == 'thumb' else (slot,)
            if len(texts) != 1 or texts[0]['sh_size'] not in _ok:
                out[addr] = 'SIZE'; continue
            mine = texts[0].data()[:slot]
            orig = pristine[int(addr, 16) - base: int(addr, 16) - base + slot]
            symtab = elf.get_section_by_name('.symtab')
            relocs = []
            masked = set()
            for sec in buildcfg.relocations(elf, _texts[0][0]):
                for rr in sec.iter_relocations():
                    # a thumb BL/BLX pair sits at a HALFWORD offset and spans 4 bytes from
                    # r_offset; rounding down to a word leaves 2 bytes unmasked -> false BYTEDIFF.
                    o = rr['r_offset']
                    masked.update(range(o, o + 4) if rr['r_info_type'] in _THM_BR
                                  else range(o & ~3, (o & ~3) + 4))
                    relocs.append(rr)
            if [i for i in range(min(len(mine), len(orig)))
                    if i not in masked and mine[i] != orig[i]]:
                out[addr] = 'BYTEDIFF'; continue
            referenced = {rr['r_info_sym'] for sec in elf.iter_sections()
                          if hasattr(sec, 'iter_relocations') for rr in sec.iter_relocations()}
            undef = [s.name for i, s in enumerate(symtab.iter_symbols())
                     if s['st_shndx'] == 'SHN_UNDEF' and s['st_info']['bind'] == 'STB_GLOBAL' and s.name
                     and i in referenced]
            for u in undef:
                am = re.match(r"^(\w+_[0-9a-fA-F]{8})_(dup|arg)$", u)
                if u not in symaddr and am and am.group(1) in symaddr:
                    symaddr[u] = symaddr[am.group(1)]
            if [u for u in undef if u not in symaddr]:
                out[addr] = 'UNDEF'; continue
            verdict = 'TRUSTED'
            for rr in relocs:
                off = rr['r_offset']; typ = rr['r_info_type']
                if off + 4 > slot:
                    continue
                sym = symtab.get_symbol(rr['r_info_sym']).name
                S = symaddr.get(sym)
                pi = int.from_bytes(orig[off:off + 4], 'little')
                if typ in (1, 28, 29):                       # ARM branch (bl/b/bCC)
                    if (pi >> 24) & 0xFE == 0xFA:            # blx -> thumb target, can't confirm
                        verdict = 'RISKY'; continue
                    tgt = (int(addr, 16) + off + 8 + _s24(pi & 0xFFFFFF) * 4) & 0xFFFFFFFF
                    if S is None:
                        verdict = 'RISKY'
                    elif S != tgt:
                        verdict = 'RELOCWRONG'; break
                elif typ in _THM_BR:                         # THUMB BL/BLX(imm) halfword pair
                    hi = pi & 0xFFFF; lo = (pi >> 16) & 0xFFFF
                    if (hi & 0xF800) != 0xF000:
                        verdict = 'RISKY'; continue
                    _o23 = ((hi & 0x7FF) << 12) | ((lo & 0x7FF) << 1)
                    if _o23 & 0x400000: _o23 -= 0x800000
                    tgt = (int(addr, 16) + off + 4 + _o23) & 0xFFFFFFFF
                    if (lo & 0xF800) == 0xE800: tgt &= ~3
                    if S is None:
                        verdict = 'RISKY'
                    elif (S & ~1) != tgt:
                        verdict = 'RELOCWRONG'; break
                elif typ == 2:                               # ABS32 (.word data/func pointer)
                    A = dataown.addend(rr, mine)
                    if S is None:
                        verdict = 'RISKY'
                    # ABS32 to a THUMB function has bit0 set by the linker (interworking) -> S+A+1.
                    elif pi not in (((S + A) & 0xFFFFFFFF), ((S + A + 1) & 0xFFFFFFFF)):
                        verdict = 'RELOCWRONG'; break
                else:
                    verdict = 'RISKY'
            out[addr] = verdict
        except Exception:
            out[addr] = 'COMPILE'
            keys.pop(addr, None)
    fresh = [(k, out[a]) for a, k in keys.items() if out.get(a) in CACHED and k not in _verdicts()]
    if fresh:
        os.makedirs(f"{SP}/wlog", exist_ok=True)
        with open(f"{SP}/wlog/classify_cache.tsv", "a", encoding="utf-8") as fh:
            for k, v in fresh:
                fh.write(f"{k}\t{v}\n")
                _verdicts()[k] = v
    return out
